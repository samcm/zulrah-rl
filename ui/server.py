"""Dashboard backend for the Zulrah RL project.

A FastAPI app that runs ONE read-only "watcher" bot against the live RL control socket
(127.0.0.1:43500), reusing the existing env code (env/protocol.py, env/state.py,
env/zulrah_env.py) and the checkpoint-loading + episode-stepping logic from eval/watch.py.

The blocking env loop runs in a background thread; per-tick state is pushed to all connected
WebSocket clients via an asyncio queue. Metrics endpoints serve the trainer's sidecar JSON and
the cross-version CSV history.

Launch:
    .venv/bin/python -m uvicorn ui.server:app --host 127.0.0.1 --port 8200
"""
import asyncio
import csv
import glob
import json
import math
import os
import threading
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from env import ZulrahEnv
from env.actions import ACTION_NAMES

# --- paths / config (must match eval/watch.py + env/state.py) ---------------------------------
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(REPO_ROOT, "ui", "static")
INDEX_HTML = os.path.join(STATIC_DIR, "index.html")
MODEL_GLOB = os.path.join(REPO_ROOT, "models", "*.zip")
TRAIN_METRICS_PATH = os.path.join(REPO_ROOT, "runs", "train_metrics.json")
TRAINING_HISTORY_CSV = os.path.join(REPO_ROOT, "metrics", "training_history.csv")
# Latest 3D screenshot from the headless spectator client (client/docker writes here).
CLIENT_FRAME_PATH = os.environ.get("RL_CLIENT_FRAME", "/tmp/zulrah_client/live.png")

HOST = os.environ.get("RL_CONTROL_HOST", "127.0.0.1")
PORT = int(os.environ.get("RL_CONTROL_PORT", "43500"))
MAX_STEPS = int(os.environ.get("RL_WATCH_MAX_STEPS", "800"))
# Wall-clock pacing between watcher steps so the browser sees a watchable fight and we do not
# hammer the (shared) control socket. The training run owns the socket too; keep this gentle.
STEP_PERIOD_S = float(os.environ.get("RL_WATCH_STEP_S", "0.10"))


# --- JSON hygiene: no NaN/inf reaches the websocket ----------------------------------------------
def clean(obj):
    """Recursively replace NaN/inf floats with None so the emitted JSON is strictly valid."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in obj]
    return obj


def latest_model(pattern):
    cands = glob.glob(pattern)
    return max(cands, key=os.path.getmtime) if cands else None


def _action_name(action):
    if action is None:
        return None
    if 0 <= action < len(ACTION_NAMES):
        return ACTION_NAMES[action]
    return None


# --- the broadcast hub: bridges the (sync, threaded) env loop to (async) websocket clients --------
class LiveHub:
    """Holds the set of connected websocket queues and the latest snapshot for late joiners."""

    def __init__(self):
        self._clients = set()           # set[asyncio.Queue]
        self._lock = threading.Lock()
        self._loop = None               # the uvicorn/asyncio event loop, captured at startup
        self.latest = None              # last full message dict (for immediate replay on connect)
        self.arena = None               # arena template (sent once, then omitted by the loop)

    def set_loop(self, loop):
        self._loop = loop

    def register(self):
        q = asyncio.Queue(maxsize=8)
        with self._lock:
            self._clients.add(q)
        return q

    def unregister(self, q):
        with self._lock:
            self._clients.discard(q)

    def has_clients(self):
        with self._lock:
            return len(self._clients) > 0

    def publish(self, message):
        """Called from the env thread. Marshals onto the event loop and fans out to all clients."""
        self.latest = message
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._fanout, message)

    def _fanout(self, message):
        with self._lock:
            queues = list(self._clients)
        for q in queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # Slow/stalled client: drop the oldest frame to keep live data flowing.
                try:
                    q.get_nowait()
                    q.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass


hub = LiveHub()


# --- the watcher bot: one env, reload newest checkpoint each episode, push per-tick state ----------
class WatcherThread(threading.Thread):
    """Reuses ZulrahEnv + eval/watch.py checkpoint logic. Runs forever; survives socket drops and
    missing checkpoints (idle, action 0)."""

    def __init__(self):
        super().__init__(name="zulrah-watcher", daemon=True)
        self._stop = threading.Event()
        self.env = None
        self.model = None
        self.model_name = None
        self.episode = 0
        self.kills = 0

    def stop(self):
        self._stop.set()

    # checkpoint loading, mirroring eval/watch.py (newest models/*.zip, cpu, guard obs-shape drift)
    def _maybe_reload_model(self):
        path = latest_model(MODEL_GLOB)
        if not path or path == self.model_name:
            return
        try:
            from stable_baselines3 import PPO
            m = PPO.load(path, device="cpu")
            if m.observation_space.shape == self.env.observation_space.shape:
                self.model, self.model_name = m, path
        except Exception:
            # Half-written zip mid-save or transient load error: keep the previous model.
            pass

    def _predict(self, obs):
        if self.model is None:
            return 0
        try:
            action, _ = self.model.predict(obs, deterministic=True)
            return int(action)
        except Exception:
            # Obs/model shape drift mid-run: fall back to idle; next reload picks up a fresh model.
            return 0

    def _ensure_env(self):
        if self.env is None:
            self.env = ZulrahEnv(host=HOST, port=PORT, max_steps=MAX_STEPS)

    def _refresh_arena(self):
        try:
            arena = self.env._client.map()
            if arena and arena.get("blocked"):
                hub.arena = arena
        except Exception:
            pass

    def run(self):
        while not self._stop.is_set():
            try:
                self._run_episode()
            except (ConnectionError, OSError):
                # Control socket momentarily unavailable: drop the env and reconnect on next reset.
                self._teardown_env()
                self._idle_wait(1.0)
            except Exception:
                self._teardown_env()
                self._idle_wait(1.0)
        self._teardown_env()

    def _run_episode(self):
        self._ensure_env()
        self._maybe_reload_model()

        obs, info = self.env.reset()
        self.episode += 1
        self._refresh_arena()

        # Send the arena template once at episode start; the per-tick loop omits it to save bandwidth.
        arena_for_episode = hub.arena
        action, reward, outcome = None, 0.0, "ongoing"
        done = trunc = False

        while not (done or trunc) and not self._stop.is_set():
            t0 = time.monotonic()
            action = self._predict(obs)
            obs, reward, done, trunc, info = self.env.step(action)
            raw = info.get("raw", {})
            outcome = info.get("outcome", "ongoing")

            self._publish_tick(raw, action, reward, outcome, arena_for_episode)
            arena_for_episode = None  # only the first tick of the episode carries the arena grid

            dt = time.monotonic() - t0
            if STEP_PERIOD_S > dt:
                self._idle_wait(STEP_PERIOD_S - dt)

        if outcome == "kill":
            self.kills += 1

    def _publish_tick(self, raw, action, reward, outcome, arena):
        player = raw.get("player", {}) or {}
        zulrah = raw.get("zulrah", {}) or {}
        ox, oy = self.env._offset

        message = {
            "tick": raw.get("tick"),
            "episode": self.episode,
            "kills": self.kills,
            "action": action,
            "action_name": _action_name(action),
            "reward": float(reward),
            "outcome": outcome,
            "model_name": os.path.basename(self.model_name) if self.model_name else None,
            "offset": [ox, oy],
            "player": {
                "x": player.get("x"),
                "y": player.get("y"),
                "hp": player.get("hp"),
                "maxHp": player.get("maxHp"),
                "prayer": player.get("prayer"),
                "overhead": player.get("overhead"),
                "attack_style": player.get("attack_style"),
                "venomed": bool(player.get("venomed")),
                "poisoned": bool(player.get("poisoned")),
                "last_atk": player.get("last_atk"),
                "last_atk_ago": player.get("last_atk_ago"),
                "pool": player.get("pool"),
            },
            "zulrah": {
                "present": bool(zulrah.get("present")),
                "form": zulrah.get("form"),
                "x": zulrah.get("x"),
                "y": zulrah.get("y"),
                "hp": zulrah.get("hp"),
                "maxHp": zulrah.get("maxHp"),
                "rotation": zulrah.get("rotation"),
                "phase": zulrah.get("phase"),
                "sequence": zulrah.get("sequence"),
            },
            "clouds": raw.get("clouds", []) or [],
            "snakelings": raw.get("snakelings", []) or [],
            "inv": raw.get("inv", []) or [],          # [[item_id, amount], ...] for the inventory panel
            "supplies": raw.get("supplies", {}) or {},
            "weapon": raw.get("weapon"),
        }
        if arena is not None:
            message["arena"] = arena
        hub.publish(clean(message))

    def _idle_wait(self, seconds):
        # Interruptible sleep so stop() takes effect promptly.
        self._stop.wait(timeout=seconds)

    def _teardown_env(self):
        if self.env is not None:
            try:
                self.env.close()
            except Exception:
                pass
            self.env = None


watcher = WatcherThread()


# --- metrics readers ------------------------------------------------------------------------------
def read_live_metrics():
    try:
        with open(TRAIN_METRICS_PATH) as f:
            return clean(json.load(f))
    except Exception:
        return {}


def read_history():
    rows = []
    try:
        with open(TRAINING_HISTORY_CSV, newline="") as f:
            for row in csv.DictReader(f):
                parsed = {}
                for k, v in row.items():
                    if v is None or v == "":
                        parsed[k] = None
                        continue
                    try:
                        fv = float(v)
                        parsed[k] = fv if math.isfinite(fv) else None
                    except (TypeError, ValueError):
                        parsed[k] = v  # non-numeric column (e.g. run name) stays a string
                rows.append(parsed)
    except FileNotFoundError:
        return []
    except Exception:
        return rows
    return rows


# --- FastAPI app ----------------------------------------------------------------------------------
app = FastAPI(title="Zulrah RL Dashboard")

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def _startup():
    hub.set_loop(asyncio.get_running_loop())
    if not watcher.is_alive():
        watcher.start()


@app.on_event("shutdown")
async def _shutdown():
    watcher.stop()


@app.get("/")
async def index():
    if os.path.isfile(INDEX_HTML):
        return FileResponse(INDEX_HTML)
    return JSONResponse(
        {"error": "ui/static/index.html not found (owned by the frontend agent)"},
        status_code=404,
    )


@app.get("/api/metrics/live")
async def metrics_live():
    return JSONResponse(read_live_metrics())


@app.get("/api/metrics/history")
async def metrics_history():
    return JSONResponse(read_history())


@app.get("/api/client-frame")
async def client_frame():
    """Serve the latest 3D screenshot from the headless spectator client.

    Returns 204 (No Content) when no frame exists yet so the dashboard can render
    a graceful "offline" state instead of a broken image.
    """
    try:
        with open(CLIENT_FRAME_PATH, "rb") as f:
            data = f.read()
    except (FileNotFoundError, OSError):
        return Response(status_code=204)
    if not data:
        return Response(status_code=204)
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    q = hub.register()
    try:
        # Replay arena + latest frame immediately so a fresh client renders without waiting a tick.
        if hub.arena is not None:
            await ws.send_text(json.dumps(clean({"arena": hub.arena})))
        if hub.latest is not None:
            await ws.send_text(json.dumps(hub.latest))
        while True:
            message = await q.get()
            await ws.send_text(json.dumps(message))
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.unregister(q)
