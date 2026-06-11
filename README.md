# Teaching a Small Network to Kill Zulrah with PPO

A minimal, **observable** reinforcement-learning project: train a small MLP policy (PPO via
stable-baselines3) to fight the OSRS boss **Zulrah** on a self-hosted RuneScape private server.
Each milestone makes one RL concept concrete and watchable. No LLM is in the control loop.

**→ [LEARNINGS.md](LEARNINGS.md) — the full story: nine reward versions, one wall, one breakthrough.**

## Status

| Milestone | Concept | State |
|---|---|---|
| **M0** | the environment is the hard part | ✅ headless server + control socket; bot spawns at Zulrah, live state vector, actions take effect |
| **M1** | the RL loop's shape | ✅ `gymnasium.Env` + random agent baseline; TensorBoard + per-episode traces |
| **M2** | escaping the dead band | ✅ PPO beats the random baseline; healthy-internals reading (entropy, EV, KL) established |
| **M3** | reward shaping & Goodhart | ✅ five reward/optimizer variants all plateau (penalties reward passivity; on-policy RL can't learn from unsampled kills) → reverse-HP curriculum: **reliable kills from ~340/500 HP**, frontier 395 |
| **M4** | throughput is the budget | ✅ 8→32 parallel bots, server tick 600ms→25ms: ~0.6 → ~175 steps/s (and the speedup itself broke game semantics once — see LEARNINGS) |
| M5 | overfitting | rotations randomize every spawn; held-out-rotation eval never run — the real generalization lesson became a privileged-info leak (see LEARNINGS) |

## The stack

- **Server:** a Zenyte-based RSPS ("Zyrox") — chosen because it ships a faithful, all-4-rotation
  Zulrah. Runs headless via `gradlew runOfflineDev`. Not vendored: clone the upstream and apply
  [`server/zyrox.patch`](server/zyrox.patch) + the [`server/rl-bridge/`](server/rl-bridge/)
  package — see [server/README.md](server/README.md).
- **Bridge:** a headless "bot" `Player` injected with no game client, driven server-side each tick
  over a TCP control socket (Java package `com.zenyte.rl`). One socket = one bot = one gym env.
  Protocol: [server/CONTROL_PROTOCOL.md](server/CONTROL_PROTOCOL.md).
- **Env:** `env/` — `ZulrahEnv` (`gymnasium.Env`), `state.py` (35-dim Markov observation),
  `actions.py` (13 discrete actions), `reward.py` (two positive terms, components logged),
  `trace.py` (per-episode traces for the ghost renderer).
- **RL:** stable-baselines3 PPO, `MlpPolicy` `[128, 128]`, `SubprocVecEnv` parallelism, reverse-HP
  curriculum + entropy annealing (`train/callbacks.py`).
- **Observability:** TensorBoard, a live pygame watcher (`eval/watch.py`), a web dashboard (`ui/`),
  and an optional real 3D spectator client ([client/README.md](client/README.md)).

## Quickstart

```bash
# 0. One-time: set up the server (clone upstream + apply patch — see server/README.md)

# 1. Run the server (headless, ~4s boot; game port 43594, control port 43500)
cd server/zyrox && JAVA_HOME=/opt/homebrew/opt/openjdk@17 ./gradlew runOfflineDev

# 2. Python env
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 3. (M0) prove the bridge works
.venv/bin/python tools/m0_smoke.py

# 4. (M1) random agent end-to-end + TensorBoard + traces
.venv/bin/python train/random_rollout.py --episodes 50

# 5. Train PPO with the curriculum (resumes from checkpoint with --resume)
.venv/bin/python train/train_ppo.py

# 6. Watch it live while it trains
.venv/bin/python eval/watch.py        # pygame 2D top-down viewer, hot-reloads newest checkpoint
tensorboard --logdir runs/
```

Pre-trained checkpoints ship in `models/` (`ppo_v7.zip`, `ppo_v8.zip`, `ppo_v9.zip` — see
[LEARNINGS.md](LEARNINGS.md) for what each version represents). Full training logs are in
`logs/`, and the cross-version comparison lives in `metrics/`.

## Observability: metric → concept

| Metric (TensorBoard) | What it teaches |
|---|---|
| episode reward (mean) | is it learning at all? |
| kill rate | the real objective, independent of reward shaping |
| episode length | surviving vs. dying fast |
| reward component breakdown (kill / dmg-dealt) | *which* incentive drives behavior — reward-hacking early-warning |
| `curriculum/zulrah_start_hp` | the difficulty ratchet |
| policy entropy | entropy-collapse detector |
| value-function explained variance | critic health |
| KL divergence / clip fraction | PPO's "stay proximal" leash |
| steps/sec | throughput is the budget |
| episode traces → ghost replays | qualitatively *watch* the policy (prayer switching, dodging) |

## Repo layout

```
LEARNINGS.md   the training journey — read this first
env/           ZulrahEnv + state/actions/reward/trace/protocol
train/         train_ppo.py + callbacks (metrics, curriculum, entropy anneal), random_rollout.py
eval/          watch.py — live pygame viewer, hot-reloads the newest checkpoint
ui/            FastAPI web dashboard (metrics + live 3D client frame)
tools/         m0_smoke.py, env_check.py, log parsers, metrics sidecar
server/        zyrox.patch + rl-bridge/ (apply to upstream Zyrox-Server), protocol + Zulrah spec docs
client/        runelite-pse.patch + docker/ (headless 3D spectator), client/README.md
models/        final checkpoints per reward version (v7/v8/v9)
logs/          full training logs, v2 → v9
metrics/       training_history.csv + cross-version comparison chart
traces/        sample per-episode traces (M1)
```
