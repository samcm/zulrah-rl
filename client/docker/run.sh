#!/usr/bin/env bash
# Runs the headless OSRS client container: mounts the shaded jar, the RSPS cache
# and params.txt, plus a host output dir for screenshots, and points the client
# at the host game server via host.docker.internal.
#
# The host game server (43594 game / 43500 RL control) must already be running.
#
# Usage:
#   ./run.sh                 # detached, auto login + spectate, screenshots to OUT_HOST
#   OUT_HOST=/tmp/zulrah_client ./run.sh
#   AUTO_SPECTATE=0 ./run.sh # login only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)/RuneLitePlus-PrivateServerEdition"
IMAGE="${IMAGE:-zulrah-client:latest}"
NAME="${NAME:-zulrah-client}"
OUT_HOST="${OUT_HOST:-/tmp/zulrah_client}"
RSPS_HOST="${RSPS_HOST:-host.docker.internal}"
RL_USER="${RL_USER:-juice}"
RL_PASS="${RL_PASS:-juice}"
SPECTATE_BOT="${SPECTATE_BOT:-0}"
AUTO_LOGIN="${AUTO_LOGIN:-1}"
AUTO_SPECTATE="${AUTO_SPECTATE:-1}"

JAR="$CLIENT_ROOT/runelite-client/target/client-1.5.28-SNAPSHOT-shaded.jar"
CACHE="$CLIENT_ROOT/local-cache"
PARAMS="$CLIENT_ROOT/params.txt"

[ -f "$JAR" ]    || { echo "[run] missing client jar: $JAR" >&2; exit 1; }
[ -d "$CACHE" ]  || { echo "[run] missing cache dir: $CACHE" >&2; exit 1; }
[ -f "$PARAMS" ] || { echo "[run] missing params.txt: $PARAMS" >&2; exit 1; }

mkdir -p "$OUT_HOST"

# Build the image on first use so a cold checkout works with a single command.
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "[run] image $IMAGE not found — building it"
    IMAGE="$IMAGE" "$SCRIPT_DIR/build.sh"
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true

echo "[run] starting $NAME (host=$RSPS_HOST, out=$OUT_HOST)"
# The driver scripts are also mounted (not just baked into the image) so edits to
# drive_login.sh / spectate_loop.sh / entrypoint.sh take effect on the next run
# without rebuilding the image.
exec docker run -d --name "$NAME" \
    --add-host host.docker.internal:host-gateway \
    -e RSPS_HOST="$RSPS_HOST" \
    -e RL_USER="$RL_USER" \
    -e RL_PASS="$RL_PASS" \
    -e SPECTATE_BOT="$SPECTATE_BOT" \
    -e AUTO_LOGIN="$AUTO_LOGIN" \
    -e AUTO_SPECTATE="$AUTO_SPECTATE" \
    -e OUT_DIR=/out \
    -v "$JAR":/client/client.jar:ro \
    -v "$CACHE":/client/cache-ro:ro \
    -v "$PARAMS":/client/params.txt:ro \
    -v "$OUT_HOST":/out \
    -v "$SCRIPT_DIR/entrypoint.sh":/usr/local/bin/entrypoint.sh:ro \
    -v "$SCRIPT_DIR/drive_login.sh":/usr/local/bin/drive_login.sh:ro \
    -v "$SCRIPT_DIR/spectate_loop.sh":/usr/local/bin/spectate_loop.sh:ro \
    "$IMAGE"
