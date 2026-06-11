# Zulrah RL control bridge (M0)

The RL agent does **not** speak the RuneScape protocol. A headless "bot" `Player` is injected into
the running Zenyte server and driven server-side via a small TCP control socket. One socket
connection == one bot == one gym env.

## Run the server
```
cd server/zyrox
JAVA_HOME=/opt/homebrew/opt/openjdk@17 ./gradlew runOfflineDev
```
Boots headless in offline-dev mode (no DB/login API, ~4s). Game port 43594; **RL control port 43500**.

## Smoke test (M0 acceptance)
```
python3 tools/m0_smoke.py
```
Resets (spawns a bot into a fresh Zulrah instance), prints the live state vector, issues moves and
a protection prayer, and asserts the move took effect.

## Protocol (newline-delimited; one JSON line per reply)
| Command        | Effect |
|----------------|--------|
| `ping`         | `{"pong":true}` |
| `reset`        | create/relaunch a headless bot into a fresh Zulrah instance; returns the first observation once Zulrah spawns |
| `step <id>`    | apply action `<id>`, advance exactly one game tick, return the resulting observation |
| `state`        | return the current observation without stepping |

### Action ids (project brief)
```
0 attack mage    1 attack range    2 protect magic   3 protect missiles
4 eat            5 antivenom       6 restore prayer
7 move N         8 move E          9 move S          10 move W      (others: idle)
```
M0 implements movement (7–10) and protection prayers (2,3) + full state. Combat (0,1) needs gear and
consumables (4,5,6) need supplies — wired in M2/M3 alongside the max-gear loadout.

### Observation (one JSON line)
```json
{
  "ready": true, "tick": 7,
  "player":   {"x","y","z","hp","maxHp","prayer","overhead":"none|magic|missiles|melee",
               "venomed","poisoned","running"},
  "zulrah":   {"present", "id", "form":"range|melee|mage", "x","y","hp","maxHp",
               "rotation","sequence","phase"},
  "snakelings": [[x,y], ...],
  "clouds":     [[x,y], ...]   // venom clouds — TODO M1 (scan world objects id 11700)
}
```
This is also the basis for the per-episode trace used by the ghost/montage renderer.

## How it works (Java, package `com.zenyte.rl`)
- `BotChannel` — Netty `EmbeddedChannel` with a real `InetSocketAddress` (buffers outbound packets).
- `ZulrahControl` — `@Subscribe onServerLaunch` starts the server + schedules a per-tick `WorldTask`.
  Creates headless bots (login bypass), applies actions and snapshots state on the world thread.
- `ControlServer` — TCP server; hands commands to `ZulrahControl` and blocks on the reply.
- `ZulrahInstance.launchHeadless` (added) — enters the Zulrah instance without the fade/camera/dialogue
  cinematic so a clientless bot spawns Zulrah immediately.

### Headless-bot gotchas (all handled)
- Set a UI pane (`setPane(RESIZABLE)`) before `onLogin`, else interface sends NPE.
- `setRunning(true)` (lifecycle flag — the per-tick loop iterates `USED_PIDS` and skips `!isRunning()`),
  NOT `setRun` (the walk/run toggle).
- Refresh `lastReceivedPacket` each tick (else auto-logout after 25 ticks) and drain the channel.
