#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

KEY_FILE=$("$ROOT_DIR/.venv/bin/python" -c "from backend.config import discover_service_account_json; p = discover_service_account_json(); print(p if p else '')")

if [[ -n "$KEY_FILE" && ! -f "$ROOT_DIR/.env" ]]; then
  echo "==> Detected GCP JSON key ($KEY_FILE) — bootstrapping GCP resources..."
  "$ROOT_DIR/.venv/bin/python" "$ROOT_DIR/scripts/bootstrap_gcp.py" --key-file "$KEY_FILE"
fi

echo "==> Starting FastAPI ADK Backend on http://127.0.0.1:8000 ..."
"$ROOT_DIR/.venv/bin/uvicorn" backend.main:app --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> Starting React + Vite Stage Dashboard on http://localhost:5173 ..."
cd "$ROOT_DIR/frontend"
npm run dev
