#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
PY="${ASR_PYTHON:-.venv/bin/python}"
if [ ! -x "$PY" ]; then PY=python3; fi
if [ -z "${ASR_MODEL_ROOT:-}" ] && [ -f models/sense-voice/model.int8.onnx ]; then
  export ASR_MODEL_ROOT="$PWD/models"
fi
if [ "${1:-}" = "--cli" ]; then
  shift
  exec "$PY" src/realtime_asr.py "$@"
fi
exec "$PY" src/app.py "$@"
