#!/usr/bin/env bash
# Drives the OSRS login screen via xdotool: dismisses the welcome splash, opens
# the "Existing User" form, types the credentials into the username/password
# fields, and submits. Screenshots every step into $OUT_DIR.
#
# The OSRS login screen is a software-rendered canvas (no native widgets), so we
# drive it with absolute clicks + typed keys + Enter, exactly like a human. The
# hotspots sit at fixed fractions of the 765x503 logical game canvas, so all
# coordinates are derived from the canvas window geometry (NOT the outer frame).
set -u

OUT_DIR="${OUT_DIR:-/out}"
RL_USER="${RL_USER:-juice}"
RL_PASS="${RL_PASS:-juice}"
export DISPLAY="${DISPLAY:-:99}"

log() { echo "[drive_login] $*"; }
shot() { import -window root "$OUT_DIR/$1" 2>/dev/null && log "screenshot $1"; }

# Locate the actual game canvas (sun-awt-X11-XCanvasPeer, 765x503) and report
# its ROOT-window position so clicks land correctly. The canvas peer (where the
# 3D scene + login UI are actually drawn) sits a few px above/left of the panel
# peer, so we must use the canvas peer's own origin.
find_canvas() {
    local best="" bx="" by=""
    for w in $(xdotool search --name "sun-awt-X11-XCanvasPeer" 2>/dev/null); do
        local g; g=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep Geometry | grep -oE '[0-9]+x[0-9]+')
        if [ "$g" = "765x503" ]; then best="$w"; fi
    done
    if [ -z "$best" ]; then
        for w in $(xdotool search --name "" 2>/dev/null); do
            local g; g=$(xdotool getwindowgeometry "$w" 2>/dev/null | grep Geometry | grep -oE '[0-9]+x[0-9]+')
            if [ "$g" = "765x503" ]; then best="$w"; fi
        done
    fi
    [ -z "$best" ] && best=$(xdotool search --name "^RuneLite$" 2>/dev/null | head -1)
    echo "$best"
}

WIN=$(find_canvas)
log "canvas window id=$WIN"

CANVAS_W=765
CANVAS_H=503
CX_OFF=0
CY_OFF=0
if [ -n "$WIN" ]; then
    eval "$(xdotool getwindowgeometry --shell "$WIN" 2>/dev/null)"
    CANVAS_W="${WIDTH:-765}"
    CANVAS_H="${HEIGHT:-503}"
    # X/Y are the canvas position in root coordinates.
    CX_OFF="${X:-0}"
    CY_OFF="${Y:-0}"
fi
log "canvas geometry ${CANVAS_W}x${CANVAS_H} at +${CX_OFF}+${CY_OFF}"

# All login hotspots are expressed as fractions of the logical 765x503 canvas
# and translated into root-window pixel coordinates.
abs_x() { echo $(( CX_OFF + ($1 * CANVAS_W / 765) )); }
abs_y() { echo $(( CY_OFF + ($1 * CANVAS_H / 503) )); }

click_at() { # logical_x logical_y
    local x y; x=$(abs_x "$1"); y=$(abs_y "$2")
    xdotool mousemove "$x" "$y"; sleep 0.3; xdotool click 1; sleep 0.7
}
type_str() { xdotool type --clearmodifiers --delay 90 "$1"; sleep 0.4; }

# Make sure keystrokes go to the canvas.
focus() {
    [ -n "$WIN" ] && xdotool windowactivate "$WIN" 2>/dev/null
    [ -n "$WIN" ] && xdotool windowfocus "$WIN" 2>/dev/null
    sleep 0.3
}

enter_credentials() {
    # Click the form, focus + clear username, type it, advance to password,
    # clear + type it, submit. Returns after the submit Enter.
    log "click login form (focus username field)"
    click_at 380 247
    sleep 0.4
    for _ in $(seq 1 25); do xdotool key --clearmodifiers BackSpace; done
    sleep 0.2
    log "type username=$RL_USER"
    type_str "$RL_USER"
    shot "03_after_username.png"

    # Enter advances username -> password field on the OSRS login screen.
    xdotool key --clearmodifiers Return; sleep 0.5
    for _ in $(seq 1 25); do xdotool key --clearmodifiers BackSpace; done
    sleep 0.2
    log "type password"
    type_str "$RL_PASS"
    shot "04_after_password.png"

    xdotool key --clearmodifiers Return; sleep 0.8
    log "submitted login (Enter)"
}

focus
shot "00b_login_initial.png"

# Step 1: dismiss the "click here to play" splash if present. Harmless on the
# New/Existing screen (lands between the two buttons) and on the form.
log "click splash centre"
click_at 382 250
shot "01_after_splash_click.png"

# Step 2: open the existing-user form. On the New/Existing screen the buttons sit
# just below centre, Existing User on the right. Click it; if we were already on
# the form, this click lands harmlessly on the form body.
log "click 'Existing User'"
click_at 460 290
shot "02_after_existing_user.png"

# Step 3: enter credentials + submit. A correct juice/juice submit logs straight
# in, so we do NOT retry automatically — a second submit while already in-world
# is treated as ALREADY_ONLINE by the server and disconnects us.
enter_credentials
sleep 6
shot "05_after_login_submit.png"
sleep 5
shot "06_login_result.png"

log "login flow done"
