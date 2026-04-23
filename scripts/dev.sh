#!/usr/bin/env bash
# Start the FastAPI dev server with hot-reload scoped to the app/ source tree.
# Watching the whole repo causes spurious restarts from venv/, .codacy/,
# .pytest_cache/, and __pycache__/ writes.

set -euo pipefail

cd "$(dirname "$0")/.."

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

exec venv/bin/uvicorn app.main:app \
    --host "$HOST" \
    --port "$PORT" \
    --reload \
    --reload-dir app \
    --reload-exclude 'venv/*' \
    --reload-exclude '.codacy/*' \
    --reload-exclude '.pytest_cache/*' \
    --reload-exclude '**/__pycache__/*' \
    --reload-exclude '**/*.pyc' \
    "$@"
