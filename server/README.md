# Server setup

Training runs against a self-hosted, Zenyte-based RSPS ("Zyrox"). The server
source is **not** vendored here — clone the upstream and apply this repo's
additions on top:

```bash
cd server
git clone https://github.com/Rims-Naps/Zyrox-Server.git zyrox
cd zyrox

# 1. Apply the RL hook points (~125 lines across 12 files: headless login,
#    per-tick dispatch, hit attribution, instant Zulrah spawn, debug logging)
git apply ../zyrox.patch

# 2. Drop in the control bridge (new package, com.zenyte.rl)
cp -r ../rl-bridge/com src/main/java/

# 3. Run headless (game port 43594, control port 43500; boots in ~4s)
JAVA_HOME=/opt/homebrew/opt/openjdk@17 ./gradlew runOfflineDev
```

## What the bridge does

- `ControlServer` — TCP server on port **43500**, newline-delimited JSON
  commands (`ping`, `reset [hp]`, `step <action_id>`, `state`, `map`).
  One socket connection = one headless bot = one gym environment.
- `BotChannel` / `Bot` — a Netty `EmbeddedChannel` standing in for a real
  game client, so a `Player` can be injected with no client attached.
- `ZulrahControl` — per-tick world task: dispatches the chosen action
  (attack / equip / pray / eat / move / …) and snapshots the observation
  JSON each game tick.
- `SpectateZulrah` — `::spectate` / `::bots` in-game commands so a normal
  client can watch a bot's fight live.

The wire protocol and observation schema are documented in
[CONTROL_PROTOCOL.md](CONTROL_PROTOCOL.md). Zulrah mechanics (all four
rotations, form NPC ids, attack types, counter-prayers, tile coordinates)
are specced in [ZULRAH_SPEC.md](ZULRAH_SPEC.md).

## Why Zenyte/Zyrox?

The first candidate server (Elvarg) turned out not to implement Zulrah at
all. Zyrox ships a faithful, all-4-rotation Zulrah — the environment is the
hard part, and a wrong environment teaches the policy the wrong boss.
