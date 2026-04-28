#!/usr/bin/env bash
set -euo pipefail

PROJECT_PATH="${1:-/mnt/d/auto-record-live}"
ENABLE_FFMPEG="${ARL_RECORDING_ENABLE_FFMPEG:-1}"

cd "$PROJECT_PATH"

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -e .

export ARL_RECORDING_ENABLE_FFMPEG="$ENABLE_FFMPEG"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

echo "[ARL] orchestrator loop started"
echo "[ARL] project: $PROJECT_PATH"
echo "[ARL] ARL_RECORDING_ENABLE_FFMPEG=$ARL_RECORDING_ENABLE_FFMPEG"

exec .venv/bin/python -m arl.cli orchestrator
