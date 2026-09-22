#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$ROOT_DIR/venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing $PYTHON. Create the venv and install requirements first." >&2
  exit 1
fi
if [[ ! -d "$ROOT_DIR/front/node_modules" ]]; then
  echo "Missing frontend dependencies. Run: (cd front && npm install)" >&2
  exit 1
fi

# server.constants loads .env; explicit shell values still take precedence.
# Large multipart manga imports temporarily use one file descriptor per page.
ulimit -n 4096 2>/dev/null || true

"$PYTHON" "$ROOT_DIR/server/main.py" --host 127.0.0.1 --port 8000 --no-gpu --workers 1 &
BACKEND_PID=$!
trap 'kill "$BACKEND_PID" 2>/dev/null || true' EXIT INT TERM

backend_ready=false
for _ in {1..60}; do
  if curl -fsS http://127.0.0.1:8000/docs >/dev/null 2>&1; then
    backend_ready=true
    break
  fi
  sleep 1
done

if [[ "$backend_ready" != true ]]; then
  echo "The backend did not start on http://127.0.0.1:8000." >&2
  exit 1
fi

cd "$ROOT_DIR/front"
npm run dev
