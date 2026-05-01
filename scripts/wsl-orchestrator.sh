#!/usr/bin/env bash
set -euo pipefail

PROJECT_PATH="${1:-/mnt/d/auto-record-live}"
ENABLE_FFMPEG="${ARL_RECORDING_ENABLE_FFMPEG:-1}"
VENV_DIR="${ARL_WSL_VENV_DIR:-.venv-wsl}"

cd "$PROJECT_PATH"

VENV_PYTHON="$VENV_DIR/bin/python"

if [ ! -x "$VENV_PYTHON" ] || [ ! -s "$VENV_PYTHON" ] || ! "$VENV_PYTHON" -V >/dev/null 2>&1; then
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
  "$VENV_PYTHON" -m ensurepip --upgrade
fi

"$VENV_PYTHON" -m pip install -e .

export ARL_RECORDING_ENABLE_FFMPEG="$ENABLE_FFMPEG"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
# Keep CLI/env override authoritative even after sourcing .env defaults.
export ARL_RECORDING_ENABLE_FFMPEG="$ENABLE_FFMPEG"

echo "[ARL] orchestrator loop started"
echo "[ARL] project: $PROJECT_PATH"
echo "[ARL] ARL_RECORDING_ENABLE_FFMPEG=$ARL_RECORDING_ENABLE_FFMPEG"

exec "$VENV_PYTHON" -m arl.cli orchestrator
