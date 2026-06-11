#!/usr/bin/env bash
# Builds the headless-client Docker image.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-zulrah-client:latest}"
echo "[build] building $IMAGE from $SCRIPT_DIR"
docker build -t "$IMAGE" "$SCRIPT_DIR"
echo "[build] done: $IMAGE"
