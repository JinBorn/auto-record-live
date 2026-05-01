#!/usr/bin/env bash
set -euo pipefail

PROJECT_PATH="${1:-/www/auto-record-live}"
INTERVAL_SECONDS="${2:-${ARL_RECORDER_INTERVAL_SECONDS:-5}}"
ENABLE_FFMPEG="${ARL_RECORDING_ENABLE_FFMPEG:-1}"
VENV_DIR="${ARL_WSL_VENV_DIR:-.venv-wsl}"
INSTALL_MODE="${ARL_WSL_INSTALL_MODE:-if-missing}"

if [ ! -d "$PROJECT_PATH" ]; then
  echo "[ARL][error] project path not found: $PROJECT_PATH" >&2
  echo "[ARL][hint] pass path explicitly: bash scripts/wsl-recorder-loop.sh /www/auto-record-live 5" >&2
  exit 1
fi

cd "$PROJECT_PATH"

VENV_PYTHON="$VENV_DIR/bin/python"

if [ ! -x "$VENV_PYTHON" ] || [ ! -s "$VENV_PYTHON" ] || ! "$VENV_PYTHON" -V >/dev/null 2>&1; then
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

if ! "$VENV_PYTHON" -m pip --version >/dev/null 2>&1; then
  "$VENV_PYTHON" -m ensurepip --upgrade
fi

if [ "$INSTALL_MODE" = "always" ] || [ ! -f "$VENV_DIR/.deps-ready" ]; then
  "$VENV_PYTHON" -m pip install -e .
  touch "$VENV_DIR/.deps-ready"
fi

export ARL_RECORDING_ENABLE_FFMPEG="$ENABLE_FFMPEG"

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi
# Keep CLI/env override authoritative even after sourcing .env defaults.
export ARL_RECORDING_ENABLE_FFMPEG="$ENABLE_FFMPEG"

echo "[ARL] recorder loop started"
echo "[ARL] project: $PROJECT_PATH"
echo "[ARL] interval: ${INTERVAL_SECONDS}s"
echo "[ARL] install mode: $INSTALL_MODE"
echo "[ARL] venv dir: $VENV_DIR"
echo "[ARL] ARL_RECORDING_ENABLE_FFMPEG=$ARL_RECORDING_ENABLE_FFMPEG"

while true; do
  if ! "$VENV_PYTHON" -m arl.cli recorder; then
    echo "[ARL][warn] recorder run failed; continue after sleep" >&2
  fi
  sleep "$INTERVAL_SECONDS"
done
