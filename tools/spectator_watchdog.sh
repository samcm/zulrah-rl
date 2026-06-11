#!/usr/bin/env bash
# Keeps the dashboard's 3D spectator alive. The headless RuneLite client (player "juice") occasionally drops to the
# login screen on server churn and the container does not self-recover, so the 3D View freezes on the login screen.
# This watches the game server log (the authoritative signal) and, whenever juice's most recent event is a logout,
# re-drives the container's login + a single spectate/crop loop. Idempotent: while juice stays logged in it does nothing.
set -u

SERVER_LOG="${SERVER_LOG:-/tmp/zulrah_server_v8.log}"
CONTAINER="${CONTAINER:-zulrah-client}"

log() { echo "[watchdog $(date +%H:%M:%S)] $*"; }

relogin() {
  log "juice logged out -> re-driving login + spectate"
  docker exec "$CONTAINER" sh -c 'pkill -f spectate_loop.sh 2>/dev/null' 2>/dev/null || true
  sleep 1
  docker exec -d "$CONTAINER" sh -c '/usr/local/bin/drive_login.sh >> /out/wd_login.log 2>&1; sleep 3; exec /usr/local/bin/spectate_loop.sh >> /out/wd_spectate.log 2>&1'
  sleep 35   # let the login flow + spectate settle before re-checking
}

ensure_one_loop() {
  # if juice is logged in but no crop loop is running (e.g. it died), restart exactly one
  local n
  n=$(docker exec "$CONTAINER" sh -c 'ps -o args 2>/dev/null | grep -c "[s]pectate_loop"' 2>/dev/null || echo 0)
  if [ "${n:-0}" = "0" ]; then
    log "no crop loop running -> starting one"
    docker exec -d "$CONTAINER" /usr/local/bin/spectate_loop.sh
    sleep 5
  fi
}

log "starting; watching $SERVER_LOG for '$CONTAINER'"
while true; do
  if ! docker ps --filter "name=$CONTAINER" --filter status=running -q | grep -q .; then
    sleep 15; continue
  fi
  last=$(grep -aE "'Juice' has logged (in|out)" "$SERVER_LOG" 2>/dev/null | tail -1)
  # re-login if juice's last event was a logout OR there's no login at all yet (e.g. just after a server restart)
  if [ -z "$last" ] || echo "$last" | grep -q "logged out"; then
    relogin
  else
    ensure_one_loop
  fi
  sleep 8
done
