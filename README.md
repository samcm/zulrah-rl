# Teaching a Small Network to Kill Zulrah with PPO

A minimal, **observable** reinforcement-learning project: train a small MLP policy (PPO via
stable-baselines3) to fight the OSRS boss **Zulrah** on a self-hosted RuneScape private server.
Each milestone makes one RL concept concrete and watchable. No LLM is in the control loop.

How it actually went, version by version: [LEARNINGS.md](LEARNINGS.md).

## Status

| Milestone | Concept | State |
|---|---|---|
| **M0** | the environment is the hard part | ✅ headless server + control socket; bot spawns at Zulrah, actions take effect |
| **M1** | the RL loop's shape | ✅ `gymnasium.Env` + random agent; TensorBoard + per-episode traces |
| **M2** | escaping the dead band | ✅ PPO beats the random baseline |
| **M3** | reward shaping & Goodhart | ✅ five reward variants plateaued near 0 kills; reverse-HP curriculum reached reliable kills from ~340/500 starting HP |
| **M4** | throughput is the budget | ✅ 8→32 parallel bots, 600ms→25ms ticks: ~0.6 → ~175 steps/s |
| M5 | overfitting | rotations randomize per spawn; held-out eval never run |

## The stack

- **Server:** a Zenyte-based RSPS, chosen because it ships a faithful all-4-rotation Zulrah
  (Elvarg does not implement Zulrah at all). Not vendored: clone the upstream and apply
  `server/zyrox.patch` + the `server/rl-bridge/` package — see [server/README.md](server/README.md).
- **Bridge:** a headless "bot" `Player` injected with no game client, driven server-side each
  tick over a TCP control socket. One socket = one bot = one gym env.
  See [server/CONTROL_PROTOCOL.md](server/CONTROL_PROTOCOL.md).
- **Env:** `env/` — `ZulrahEnv` (`gymnasium.Env`), 35-dim Markov state, 13 discrete actions,
  a two-term reward with logged components, per-episode traces for replays.
- **RL:** stable-baselines3 PPO + MlpPolicy `[128, 128]`, `SubprocVecEnv`, reverse-HP
  curriculum and entropy annealing (`train/callbacks.py`).
- **Observability:** TensorBoard, a live pygame watcher (`eval/watch.py`), a web dashboard
  (`ui/`), and an optional real 3D spectator client ([client/README.md](client/README.md)).

## Quickstart

```bash
# 0. One-time: set up the server (clone upstream + apply patch — see server/README.md)

# 1. Run the server (headless, ~4s boot; game port 43594, control port 43500)
cd server/zyrox && JAVA_HOME=/opt/homebrew/opt/openjdk@17 ./gradlew runOfflineDev

# 2. Python env
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 3. (M0) prove the bridge works
.venv/bin/python tools/m0_smoke.py

# 4. (M1) random agent end-to-end
.venv/bin/python train/random_rollout.py --episodes 50

# 5. Train PPO with the curriculum (resumes from checkpoint with --resume)
.venv/bin/python train/train_ppo.py

# 6. Watch it live while it trains
.venv/bin/python eval/watch.py
tensorboard --logdir runs/
```

Final checkpoints for v7/v8/v9 are in `models/`, full training logs in `logs/`, and the
cross-version comparison in `metrics/`.

## Observability: metric → concept

| Metric (TensorBoard) | What it teaches |
|---|---|
| episode reward (mean) | is it learning at all? |
| kill rate | the real objective, independent of reward shaping |
| episode length | surviving vs. dying fast |
| reward component breakdown | *which* incentive drives behavior — reward-hacking early-warning |
| `curriculum/zulrah_start_hp` | the difficulty ratchet |
| policy entropy | entropy-collapse detector |
| value-function explained variance | critic health |
| KL divergence / clip fraction | PPO's "stay proximal" leash |
| steps/sec | throughput is the budget |
| episode traces → ghost replays | qualitatively *watch* the policy (prayer switching, dodging) |

## Repo layout

```
LEARNINGS.md   the training journey
env/           ZulrahEnv + state/actions/reward/trace/protocol
train/         train_ppo.py + callbacks (metrics, curriculum, entropy anneal), random_rollout.py
eval/          watch.py — live pygame viewer, hot-reloads the newest checkpoint
ui/            FastAPI web dashboard
tools/         smoke tests, log parsers, metrics sidecar
server/        zyrox.patch + rl-bridge/ (apply to upstream), protocol + Zulrah spec docs
client/        runelite-pse.patch + docker/ (headless 3D spectator)
models/        final checkpoints (v7/v8/v9)
logs/          full training logs, v2 → v9
metrics/       training_history.csv + comparison chart
traces/        sample per-episode traces (M1)
```
