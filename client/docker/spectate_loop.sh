#!/usr/bin/env bash
# Once logged in: runs `::bots` then `::spectate <idx>` via the chatbox, then
# continuously screenshots the live 3D view to $OUT_DIR/live.png (cropped to the
# game canvas) which the web dashboard's "3D View" panel polls.
set -u

OUT_DIR="${OUT_DIR:-/out}"
SPECTATE_BOT="${SPECTATE_BOT:-0}"
LIVE_PERIOD="${LIVE_PERIOD:-1.5}"
export DISPLAY="${DISPLAY:-:99}"

log() { echo "[spectate] $*"; }

# Locate the game canvas (the 765x503 sun-awt XCanvasPeer) and report its root
# geometry so we can crop the live screenshot to just the 3D scene + HUD.
find_canvas_geom() {
    local best=""
    for w in $(xdotool search --name "sun-awt-X11-XCanvasPeer" 2>/dev/null); do
        local g; g=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep Geometry | grep -oE '[0-9]+x[0-9]+')
        [ "$g" = "765x503" ] && best="$w"
    done
    [ -z "$best" ] && return 1
    eval "$(xdotool getwindowgeometry --shell "$best" 2>/dev/null)"
    echo "${WIDTH}x${HEIGHT}+${X}+${Y}"
}

WIN=$(xdotool search --name "^RuneLite$" 2>/dev/null | head -1)
[ -n "$WIN" ] && xdotool windowactivate "$WIN" 2>/dev/null && xdotool windowfocus "$WIN" 2>/dev/null

# Give the world a moment to load after login before issuing chat commands.
sleep 8

send_cmd() {
    # OSRS chat: typing text then Enter sends it; commands start with ::
    xdotool type --clearmodifiers --delay 60 "$1"
    sleep 0.4
    xdotool key --clearmodifiers Return
    sleep 1.5
}

log "issuing ::bots"
send_cmd "::bots"
import -window root "$OUT_DIR/07_bots.png" 2>/dev/null

log "issuing ::spectate $SPECTATE_BOT"
send_cmd "::spectate $SPECTATE_BOT"
sleep 2
import -window root "$OUT_DIR/08_spectate.png" 2>/dev/null

CROP=$(find_canvas_geom || true)
if [ -n "$CROP" ]; then
    log "live loop: cropping canvas $CROP -> $OUT_DIR/live.png every ${LIVE_PERIOD}s"
else
    log "live loop: canvas geom unknown, capturing full root -> $OUT_DIR/live.png every ${LIVE_PERIOD}s"
fi

# Write to an explicit png: target (a ".png.tmp" name makes ImageMagick
# misdetect the format and hit the policy-blocked PS coder), then atomically
# rename so the dashboard never reads a half-written file.
while true; do
    if [ -n "$CROP" ]; then
        import -window root -crop "$CROP" +repage "png:$OUT_DIR/.live.tmp.png" 2>/dev/null \
            && mv -f "$OUT_DIR/.live.tmp.png" "$OUT_DIR/live.png"
    else
        import -window root "png:$OUT_DIR/.live.tmp.png" 2>/dev/null \
            && mv -f "$OUT_DIR/.live.tmp.png" "$OUT_DIR/live.png"
    fi
    sleep "$LIVE_PERIOD"
done
