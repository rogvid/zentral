#!/usr/bin/env bash
# Browser end-to-end test: builds the web client, starts a throw-away
# development instance with sample data, runs web/e2e/walkthrough.mjs, cleans up.
#
#   scripts/e2e.sh [screenshot-dir]
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d)"
port="${E2E_PORT:-8765}"
pids=()
cleanup() { for pid in "${pids[@]:-}"; do kill "$pid" 2>/dev/null || true; done; rm -rf "$work"; }
trap cleanup EXIT

cd "$root"
(cd web && npm run build >/dev/null)

# Never inherit a real installation's settings.
for name in $(env | grep -o '^ZENTRAL_[A-Z_]*' || true); do unset "$name"; done
uv run zentral init --auth dev --output "$work/.env" >/dev/null
set -a; . "$work/.env"; set +a
export ZENTRAL_DATABASE_URL="sqlite+aiosqlite:///$work/e2e.db"
export ZENTRAL_STATIC_DIR="$root/web/dist"
export ZENTRAL_PUBLIC_BASE_URL="http://127.0.0.1:$port"
export ZENTRAL_LOG_LEVEL=WARNING
export ZENTRAL_PORTAL_ENABLED=true
export ZENTRAL_PORTAL_MAIL_CHANNEL="Monitoring & forms"

uv run zentral db upgrade >/dev/null
uv run zentral serve --port "$port" >"$work/serve.log" 2>&1 & pids+=($!)
for _ in $(seq 1 50); do curl -fsS "http://127.0.0.1:$port/readyz" >/dev/null 2>&1 && break; sleep 0.2; done
uv run python scripts/seed_demo.py "http://127.0.0.1:$port" >/dev/null
uv run python scripts/seed_history.py >/dev/null
uv run zentral worker >"$work/worker.log" 2>&1 & pids+=($!)
sleep 4   # let the worker classify the seeded tickets

export PORTAL_LINK="$(uv run python scripts/portal_link.py tom.nielsen@example.com 2>/dev/null | tail -n 1)"
[ $# -ge 1 ] && mkdir -p "$1"
BASE_URL="http://127.0.0.1:$port" node "${E2E_SCRIPT:-web/e2e/walkthrough.mjs}" "$@" || { echo "--- server log"; tail -20 "$work/serve.log"; exit 1; }
