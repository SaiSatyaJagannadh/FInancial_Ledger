#!/usr/bin/env bash
# Run the API and the web app together; Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")"

[ -d backend/.venv ] || { echo "run: cd backend && uv venv --python 3.11 .venv && uv pip install -e '.[dev]'"; exit 1; }
[ -d frontend/node_modules ] || { echo "run: cd frontend && npm install"; exit 1; }

trap 'kill 0' EXIT
(cd backend && .venv/bin/python -m uvicorn app.main:app --port 8000 --reload) &
(cd frontend && npm run dev) &
wait
