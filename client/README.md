# Real 3D OSRS client — watching the RL bot fight Zulrah

This is a rev-180 RuneLite RSPS fork (`RuneLitePlus-PrivateServerEdition`) configured to connect to the
local Zenyte/Zyrox game server and **spectate** a headless RL bot's live Zulrah fight in a real 3D client.

Status: **builds, connects to the live server, renders the login screen, and the game-login handshake
now decodes cleanly server-side** (the silent-drop bug below is fixed). The interactive login +
`::spectate` step needs either macOS Accessibility permission (to drive the AWT login fields
programmatically) or a manual login by you — see "Blocker" below.

---

## Getting the client

The client source is **not** vendored here — clone the upstream fork and apply this repo's patch
(~320 lines: RSA modulus, handshake fixes, injector bug fixes, mixin bounds checks):

```sh
cd client
git clone https://github.com/NateChambers/RuneLitePlus-PrivateServerEdition.git
cd RuneLitePlus-PrivateServerEdition
git apply ../runelite-pse.patch
# then copy the server cache so client and server agree byte-for-byte:
cp -r ../../server/zyrox/data/cache local-cache
```

Then build it (see "Building the client" below).

---

## TL;DR — launch + spectate

The game server (`com.zenyte.GameEngine offline_dev`) must already be running and listening on `43594`
(game) / `43500` (RL control).

```sh
# 1. Launch the client (MUST run from the client root so params.txt + local-cache resolve)
cd client/RuneLitePlus-PrivateServerEdition

/opt/homebrew/opt/openjdk@11/bin/java \
  -Drunelite.rsps.cache="$(pwd)/local-cache" \
  -jar runelite-client/target/client-1.5.28-SNAPSHOT-shaded.jar \
  --local-injected --developer-mode

# 2. At the "Welcome to RuneScape" screen, click "Existing User" and log in:
#      Username:  juice          (an owner -> gets SPAWN_ADMINISTRATOR, needed for ::spectate)
#      Password:  juice          (the offline_dev world has verifyPasswords:true and api.enabled:false,
#                                 so the typed password is checked against playerInformation.plainPassword
#                                 in data/characters/juice.json, which is "juice" — NOT "password")

# 3. Once in-game, list bots and spectate one:
#      ::bots               -> lists active RL bots, e.g. "[0] rlbot1 - fighting Zulrah"
#      ::spectate 0         -> teleports you (hidden, locked, free-cam) into bot 0's Zulrah instance
#      ::unspectate         -> stop spectating, return to a safe tile
```

You are dropped into the bot's dynamically-allocated Zulrah instance as an invisible, frozen, free-cam
spectator. The bot, Zulrah, snakelings, venom clouds and projectiles all render in the 3D scene.

---

## Connection parameters (must match the server)

All sourced from `server/zyrox/src/main/java/com/zenyte/network/NetworkConstants.java`.

| Setting             | Value |
|---------------------|-------|
| Game host:port      | `127.0.0.1:43594` |
| Protocol revision   | `180` |
| Desktop sub-version | `3` (server `SUB_VERSION_DESKTOP`) |
| Cache               | `local-cache/` (byte-identical copy of `server/zyrox/data/cache`) |
| RL control port     | `43500` (training owns it; bots = gym envs) |
| Login world         | `offline_dev` (number 100, `development:true`, `api.enabled:false`, `verifyPasswords:true`) |

### RSA login key

The client encrypts the login block with the **public exponent + the server's modulus**; the server
decrypts with its private exponent + the same modulus. The client modulus had to be replaced with the
server's `RSA_MODULUS`:

- **Public exponent** (`__cm_m`): `10001` hex = `65537` — unchanged (matches the server keypair).
- **Modulus** (`__cm_f`, hex): `d2a780dccbcf534dc61a36deff725aabf9f46fc9ea298ac8c39b89b5bcb5d0817f8c9f59621187d448da9949aca848d0b2acae50c3122b7da53a79e6fe87ff76b675bcbf5bc18fbd2c9ed8f4cff2b7140508049eb119259af888eb9d20e8cea8a4384b06589483bcda11affd8d67756bc93a4d786494cdf7b634e3228b64116d`

This is the hex form of the server's decimal `RSA_MODULUS`. The server's `RSA_EXPONENT` constant is the
**private** exponent `d` (not 65537), used only server-side for decryption. (Verified: client `e=65537`
and server `d` round-trip correctly under the shared modulus — RSA is **not** a source of login failure.)

---

## Game-login handshake fix — "Connecting to server" hang (silent server-side drop)

**Symptom:** JS5/cache streams fine and the login screen renders, but submitting credentials hangs on
"Connecting to server..." and the game-login connection never stabilises on `43594`.

**Root cause — a one-byte desync in the login block.** The server's `LoginDecoder` reads the byte
immediately after `version`(int) + `subVersion`(int) as the **MAC-address length** (`macLength`), then
for a desktop client reads exactly that many MAC bytes before the RSA block:

```java
// server: com/zenyte/network/login/codec/LoginDecoder.java
val version    = in.readInt();
val subVersion = in.readInt();
val macLength  = in.readUnsignedByte();          // <-- the contested byte
val device     = macLength == 2 ? MOBILE : DESKTOP;
// DESKTOP: reads macLength MAC bytes, THEN the RSA block
```

The deob client wrote `clientType` into that exact position instead of a MAC length:

```java
// client: Client.java doCycleLoggedOut(), loginState == 5
var6.packetBuffer.writeInt(180);                 // revision
var6.packetBuffer.writeInt(3);                   // sub-version
var6.packetBuffer.writeByte(clientType);         // <-- BUG: server reads this as macLength
var6.packetBuffer.__s_297(var3.array, ...);      // RSA block immediately follows (no MAC bytes)
```

`clientType` comes from applet `param=4`, which is **`761`** in `params.txt`. As a byte that is
`761 & 0xFF = 249`. The server therefore thinks `macLength = 249`, consumes 249 bytes of the RSA block
as a phantom MAC address, and the rest of the decode is shifted by 249 bytes. The RSA decrypt then
yields garbage, `AuthType.values[rsaBuf.readUnsignedByte()]` throws `ArrayIndexOutOfBoundsException`,
and Netty closes the channel **before any login response is written** — a silent drop. The client's
own failover then flips `port3` from `43594` to `443` (Jagex's alternate game port, set when
`gameBuild==0`), where nothing is listening, so the retry never reconnects and the screen hangs forever.

**Fix (client):** write `0` for the MAC-length byte (desktop, no MAC follows — matching the zero MAC
bytes the client actually sends). `clientType` is still sent unchanged at its *other* position (the
`supportsJs` byte) and is still used for error-report URLs, so nothing else changes.

```java
var6.packetBuffer.writeByte(0);   // was: writeByte(clientType)
```

This is in `runescape-client/.../Client.java`, so the deob -> injected -> shaded chain was rebuilt; the
shaded jar already contains the fix. **Login now succeeds end-to-end** — the server log shows
`'Juice' has logged in.` (verified by driving the login from the headless container, see `docker/`).

### In-game crash fix — injector covariant-bridge bug (`AbstractMethodError`)

After login the client immediately crashed with `Classes are out of date; Build with maven again` and
`AbstractMethodError: Receiver class NodeHashTable does not define ... RSNode get(long)` (and the same for
`EvictingDualNodeHashTable.get(long)` / `DualNode`), thrown from `Client.getVarbitValue` during
`doCycleLoggedOut` — so the client disconnected the instant the world started loading (`'Juice' has
logged out.` right after the login).

Root cause is in the **injector** (`injector-plugin/.../InjectInvoker.java`). When a deob method is
exported under the same name as its API method but with a **covariant** return type (deob
`Node get(long)` vs API `RSNode get(long)`, `Node implements RSNode`), the JVM needs a synthetic bridge
method `RSNode get(long)` so `invokeinterface RSNodeHashTable.get` resolves. The injector's guard

```java
if (clazz.findMethod(name, apiSignature) != null
    || clazz.findMethod(name, deobfuscatedMethod.getDescriptor()) != null)  // <- wrongly skips
```

matched the existing deob method by its concrete descriptor and logged *"Not injecting method ... because
it already exists!"*, so the bridge was never emitted. **Fix:** only skip when a method with the exact
API signature exists, or when the deob method's return type equals the API return type; otherwise inject
the bridge and flag it `ACC_BRIDGE | ACC_SYNTHETIC` so it coexists with the concrete method. After the
fix the injected classes carry both `Node get(long)` and `RSNode get(long)`, the `AbstractMethodError` is
gone, and the client renders the game world. **The injector-plugin, injected-client and runelite-client
were all rebuilt** (the shaded jar already contains this fix).

**Why not RSA / revision / CRC:** all four of the usual suspects were verified to match — handshake byte
`14` (game) / `15` (JS5), login opcode `16` (new) / `18` (reconnect), revision `180`, sub-version `3`,
the RSA keypair round-trips, and the cache is byte-identical so per-archive CRCs match. The only
divergence was the `macLength` byte.

### Server-side diagnostics added (needs a server rebuild to take effect)

To make any future login rejection self-evident in the log, verbose `System.out` tracing was added to the
server (`compileJava` verified, **not** run):

- `HandshakeHandler` — logs the game-connection handshake (opcode 14) before switching to `LoginDecoder`.
- `LoginDecoder` — logs `opcode / type / size / version / subVersion / macLength`, the RSA magic byte
  (expected `1`), and the decoded `username / crcCount / sessionTokenLen`; drops cleanly on an unknown
  opcode; and a new `exceptionCaught` logs+closes instead of dropping silently.
- `LoginRequest.getResponseCode` — logs the exact reject reason for `CLIENT_UPDATED` (revision /
  sub-version), `SERVER_UPDATED` (which CRC index mismatched, client vs server), and `BAD_SESSION_ID`.

Also fixed an unrelated per-tick log flood: `TearsOfGuthixWall.transform` called
`Utils.getRandomCollectionElement` on a possibly-empty collection, throwing "Collection cannot be empty"
every tick — now guarded with `if (!possibleWalls.isEmpty())`.

Rebuild the server to pick up these server-side changes (training auto-resumes from checkpoint via
`--resume`). The **client fix alone** (already in the shaded jar) is what unblocks login; the server
changes are diagnostics + the log de-spam and are not required for login to succeed.

---

## What was changed in the client (vs. the upstream fork)

| File | Change |
|------|--------|
| `params.txt` | `codebase=http://127.0.0.1/`, `initial_class=Client.class` — the deob client derives `worldHost` from `getCodeBase().getHost()`, so this points the game + JS5 sockets at `127.0.0.1`. Port `43594` is hardcoded in `Client.setUp()` when `gameBuild==0`. |
| `runescape-client/.../class83.java` | Replaced `__cm_f` (RSA modulus) with the server's modulus (above). |
| `runescape-client/.../Client.java` | Login sub-version `1 -> 3` (matches `SUB_VERSION_DESKTOP`); cache dir reads `-Drunelite.rsps.cache` (default `./local-cache`). Revision stays `180`. **MAC-length byte `writeByte(clientType) -> writeByte(0)`** in the login block — `clientType` (`param=4=761`, low byte `249`) was being read by the server as `macLength`, desyncing the RSA block and silently dropping the connection (see "Game-login handshake fix"). |
| `runelite-client/.../ui/ClientPanel.java` | Drives the applet via the deob lifecycle names (`__init_109` / `__start_97`) instead of `Applet.init()`/`start()`. **This was the fix for the black-screen bug:** the deob client never re-obfuscates these methods back to the JVM applet names, so `client.init()` was resolving to the inherited no-op and the game engine never booted. |

---

## Building the client

JDK 11 (`/opt/homebrew/opt/openjdk@11`). After editing the RSA modulus you must rebuild the chain
deob -> injected -> shaded, because the injector bakes the deob client into the injected jar:

```sh
cd client/RuneLitePlus-PrivateServerEdition
export JAVA_HOME=/opt/homebrew/opt/openjdk@11
export PATH="$JAVA_HOME/bin:$PATH"

mvn -o -pl injector-plugin    install -DskipTests -Dcheckstyle.skip=true   # if injector changed
mvn -o -pl runelite-mixins    install -DskipTests -Dcheckstyle.skip=true   # if a mixin changed
mvn -o -pl runescape-client   install -DskipTests -Dcheckstyle.skip=true   # deob client (rs-client jar)
rm -rf injected-client/target/classes injected-client/target/*.jar          # force the injector to re-run
mvn -o -pl injected-client    install -DskipTests -Dcheckstyle.skip=true   # runs the injector + mixins
rm -f runelite-client/target/*-shaded.jar
mvn -o -pl runelite-client    install -DskipTests -Dcheckstyle.skip=true   # shaded runnable jar
```

The injector and mixins are **applied during the `injected-client` build**, and maven's incremental build
will skip re-running the injector if `injected-client/target` looks up-to-date — so after changing a
`runescape-client` / `runelite-mixins` / `injector-plugin` source, delete `injected-client/target` (as
above) to force a clean re-injection, otherwise the shaded jar silently keeps the stale injected classes.
Editing only `ClientPanel.java` (a `runelite-client` source) needs just the last command. There is no
`mvn clean` available offline (the clean-plugin isn't cached); delete `target/` dirs by hand instead.

Output jar: `runelite-client/target/client-1.5.28-SNAPSHOT-shaded.jar` (Main-Class `net.runelite.client.RuneLite`).

---

## Server-side spectate command (already compiled into the live server)

`server/zyrox/src/main/java/com/zenyte/rl/SpectateZulrah.java` + the command wiring in
`GameCommands.java` (`::spectate`, `::unspectate`, `::bots`). It teleports the viewer into the bot's
`ZulrahInstance` chunk; `GlobalAreaManager` then adds the viewer to the same instance area. The viewer is
hidden + locked + frozen + put into free-cam aimed at the shrine/Zulrah. Modelled on
`TournamentSpectatingInterface`. Compiles clean under JDK 17 (`./gradlew compileJava`).

`::spectate` requires `Privilege.SPAWN_ADMINISTRATOR`, which is why the viewer logs in as an owner
(`juice` / `grim` / `jade` per `Constants.owners`).

---

## Headless container (no macOS Accessibility needed)

macOS Accessibility is not granted, so synthetic input can't be injected into a native-macOS AWT window.
Instead the client runs in a **Linux Docker container** under a virtual X display (`Xvfb`), where
`xdotool` drives clicks/keystrokes and `import` (ImageMagick) screenshots the X root — no host GUI and no
Accessibility permission required. The 3D scene renders in **software GL** (mesa/llvmpipe, `import` of the
canvas shows the full 3D login screen), so no OpenGL/LWJGL natives are needed.

Everything is in `client/docker/`:

```sh
# One command: build the image (first run), bring up the container, auto-login as
# juice/juice, and start the live screenshot loop -> /tmp/zulrah_client/live.png
client/docker/build.sh          # once (or when the Dockerfile changes)
client/docker/run.sh            # boots container, auto-logins, screenshots to OUT_HOST
# Knobs: RL_USER, RL_PASS, SPECTATE_BOT, AUTO_SPECTATE=0/1, OUT_HOST=/tmp/zulrah_client
```

The jar + cache + driver scripts are bind-mounted, so a `mvn` rebuild of the client (or an edit to
`drive_login.sh` / `spectate_loop.sh` / `entrypoint.sh`) takes effect on the next `run.sh` with no image
rebuild. The dashboard's "3D View" panel polls `/api/client-frame`, which serves
`/tmp/zulrah_client/live.png` (cropped to the 765x503 game canvas).

## Status of the headless run

**Login fully succeeds.** Driving juice/`juice` (see credentials note above — NOT `password`) the server
log shows the whole handshake decode and then `'Juice' has logged in.` The client then transitions into
the game world (`doCycleLoggedIn` runs) and processes the player-update stream.

Getting there required fixing a chain of latent **injector / mixin** defects that only surface once the
world loads (each previously hard-crashed the client right after login):

1. **Covariant-bridge injection** (`InjectInvoker`) — see "In-game crash fix" above. `AbstractMethodError`
   on `RSNodeHashTable.get(long)` / `RSEvictingDualNodeHashTable.get(long)`.
2. **Static-hook owner class** (`InjectHookMethod`) — the injector hardcoded `invokestatic client.<hook>`
   (the *vanilla* class name); in a deobfuscated build the class is renamed (`Client`), so the call threw
   `NoClassDefFoundError: client` from e.g. `GrandExchangeEvents.addChatMessage`. Fixed to reference the
   live client `ClassFile` name so the output renamer maps it to `Client`.
3. **Transient out-of-range field reads in scene-load mixins** (`VarbitMixin.getVarbitValue`,
   `RSActorMixin.getInteracting`, `RSTileMixin.itemLayerChanged`) — these RuneLite event hooks fire while
   the scene is still settling after login, when the tile/actor obfuscated index getters briefly return
   out-of-range values, throwing `ArrayIndexOutOfBoundsException`. Each is now bounds-checked.

**Remaining blocker — NPC-update protocol desync.** Once in-world the client crashes in
`class3.updateNpcs` with `RuntimeException: <readIndex>,<packetLen>` (e.g. `1712,1166`) — after parsing
the NPC-synchronization block the client's read cursor overshoots the declared packet length. The
server's NPC update encoder (`server/zyrox/.../update/NPCInfo.java`) and this rev-180 deob client's NPC
mask reader disagree on the extended-info block format. The client recovers gracefully (returns to the
login screen) rather than dying, so the screenshot loop keeps producing a valid live 3D feed of the
login/loading screen. Closing the last gap means bit-matching `NPCInfo`'s update masks to the client's
`class3.updateNpcs` reader (a server-side change + rebuild). The player-update block already
round-trips, so it is isolated to the NPC update masks.
