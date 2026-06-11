#!/usr/bin/env bash
# Boots a virtual X display, launches the headless OSRS client pointed at the
# host game server, then drives the login + spectate flow and screenshots each
# step into the mounted output dir.
#
# Mounts expected at runtime:
#   /client/client.jar        the shaded RuneLite jar
#   /client/local-cache/      the RSPS cache (read-only is fine)
#   /client/params.txt        applet params
#   /out/                     screenshot output (host-mounted)
#
# Env knobs:
#   RSPS_HOST       game server host as seen from the container (default host.docker.internal)
#   RL_USER         login username (default juice)
#   RL_PASS         login password (default password)
#   SCREEN_W/H/D    Xvfb geometry (default 1280x960x24)
#   AUTO_LOGIN      1 to run drive_login.sh automatically (default 1)
#   AUTO_SPECTATE   1 to run spectate_loop.sh after login (default 1)
#   SPECTATE_BOT    bot index for ::spectate (default 0)
set -u

OUT_DIR="${OUT_DIR:-/out}"
RSPS_HOST="${RSPS_HOST:-host.docker.internal}"
SCREEN_W="${SCREEN_W:-1280}"
SCREEN_H="${SCREEN_H:-960}"
SCREEN_D="${SCREEN_D:-24}"
DISPLAY_NUM="${DISPLAY_NUM:-:99}"
export DISPLAY="$DISPLAY_NUM"
AUTO_LOGIN="${AUTO_LOGIN:-1}"
AUTO_SPECTATE="${AUTO_SPECTATE:-1}"

mkdir -p "$OUT_DIR"

log() { echo "[entrypoint] $*"; }

log "starting Xvfb on $DISPLAY (${SCREEN_W}x${SCREEN_H}x${SCREEN_D})"
Xvfb "$DISPLAY" -screen 0 "${SCREEN_W}x${SCREEN_H}x${SCREEN_D}" -ac +extension RANDR >/tmp/xvfb.log 2>&1 &
XVFB_PID=$!

# Wait for the X display to accept connections.
for i in $(seq 1 50); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        log "Xvfb is up"
        break
    fi
    sleep 0.2
done

log "resolving game host '$RSPS_HOST'"
getent hosts "$RSPS_HOST" || log "WARN: could not resolve $RSPS_HOST"

# The deob client opens the cache in "rw" mode, so it must be writable. The host
# cache is mounted read-only at /client/cache-ro; stage a writable copy so the
# byte-identical host/server cache is never mutated.
if [ -d /client/cache-ro ]; then
    log "staging writable cache copy from /client/cache-ro"
    rm -rf /client/local-cache
    cp -a /client/cache-ro /client/local-cache
fi

# The build-time injector left Player.isClanMember()/isFriend() package-private, so any
# plugin that calls them on a PlayerSpawned event (e.g. Clan Chat) throws IllegalAccessError
# and kills the game thread back to the login screen. A spectator needs none of those
# plugins, so disable the offenders via RuneLite config before the client reads it.
RUNELITE_DIR="${HOME:-/root}/.runelite"
mkdir -p "$RUNELITE_DIR"
cat > "$RUNELITE_DIR/runeliteplus.properties" <<'EOF'
runelite.clanchatplugin=false
runelite.grounditemsplugin=false
runelite.musiclistplugin=false
runelite.wikiplugin=false
runelite.animationsmoothingplugin=false
runelite.shortcutplugin=false
runelite.discordplugin=false
animationSmoothing.smoothPlayerAnimations=false
animationSmoothing.smoothNpcAnimations=false
animationSmoothing.smoothObjectAnimations=false
EOF
cp "$RUNELITE_DIR/runeliteplus.properties" "$RUNELITE_DIR/settings.properties"
log "seeded RuneLite config: disabled spectator-crashing plugins"

# The client reads params.txt relative to the CWD.
cd /client

log "launching client (host=$RSPS_HOST)"
java \
    -Drunelite.rsps.host="$RSPS_HOST" \
    -Drunelite.rsps.cache="/client/local-cache" \
    -Djava.awt.headless=false \
    -Dsun.java2d.opengl=false \
    -jar /client/client.jar \
    --local-injected --developer-mode \
    >"$OUT_DIR/client.log" 2>&1 &
CLIENT_PID=$!
log "client pid=$CLIENT_PID, logs at $OUT_DIR/client.log"

# Give the JVM time to boot the applet + render the login screen.
sleep 18
import -window root "$OUT_DIR/00_boot.png" 2>/dev/null && log "screenshot 00_boot.png"

if [ "$AUTO_LOGIN" = "1" ]; then
    /usr/local/bin/drive_login.sh
fi

if [ "$AUTO_SPECTATE" = "1" ]; then
    # spectate_loop issues ::bots / ::spectate (best effort) and then enters the
    # continuous live screenshot loop writing $OUT_DIR/live.png.
    /usr/local/bin/spectate_loop.sh &
    SPEC_PID=$!
    log "spectate loop pid=$SPEC_PID"
else
    # Even without spectating, keep $OUT_DIR/live.png fresh so the dashboard's
    # "3D View" panel always shows the current client (login / loading / world).
    log "live screenshot loop -> $OUT_DIR/live.png (cropped to game canvas) every ${LIVE_PERIOD:-1.5}s"
    (
        export DISPLAY="$DISPLAY"
        while true; do
            crop=""
            for w in $(xdotool search --name "sun-awt-X11-XCanvasPeer" 2>/dev/null); do
                g=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep -oE '[0-9]+x[0-9]+' | head -1)
                if [ "$g" = "765x503" ]; then
                    eval "$(xdotool getwindowgeometry --shell "$w" 2>/dev/null)"
                    crop="${WIDTH}x${HEIGHT}+${X}+${Y}"
                fi
            done
            if [ -n "$crop" ]; then
                import -window root -crop "$crop" +repage "png:$OUT_DIR/.live.tmp.png" 2>/dev/null \
                    && mv -f "$OUT_DIR/.live.tmp.png" "$OUT_DIR/live.png"
            else
                import -window root "png:$OUT_DIR/.live.tmp.png" 2>/dev/null \
                    && mv -f "$OUT_DIR/.live.tmp.png" "$OUT_DIR/live.png"
            fi
            sleep "${LIVE_PERIOD:-1.5}"
        done
    ) &
    log "live loop pid=$!"
fi

log "entrypoint idle; client running. tailing client.log"
# Keep PID 1 alive and surface client output.
wait "$CLIENT_PID"
log "client exited"
