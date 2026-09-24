#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

if [ ! -f "$PROJECT_DIR/.env.local" ]; then
  echo "Missing $PROJECT_DIR/.env.local"
  exit 1
fi

set -a
. "$PROJECT_DIR/.env.local"
set +a

mkdir -p "$PROJECT_DIR/.runtime/uploads"

VENV_SITE=""
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  VENV_SITE=$(
    "$PROJECT_DIR/.venv/bin/python" -c 'import site; print(site.getsitepackages()[0])'
  )
fi
export PYTHONPATH="$PROJECT_DIR/backend${VENV_SITE:+:$VENV_SITE}"

cd "$PROJECT_DIR/backend"
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8001 &
BACKEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$PROJECT_DIR/frontend"
BACKEND_URL=http://127.0.0.1:8001 npm run dev
