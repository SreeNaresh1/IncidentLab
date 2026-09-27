#!/usr/bin/env bash
# Stage 1: BASELINE — bring the stack up and record normal-load behavior.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS_LIB="${SCRIPT_DIR}/../../tools/lib"
VERIFY_PY="${TOOLS_LIB}/verify.py"
cd "${SCRIPT_DIR}"
source "${TOOLS_LIB}/experiment.sh"

banner "1. BASELINE"

export MITIGATION_ENABLED=false
docker compose up -d --build postgres redis app prometheus grafana

wait_for_health "http://localhost:8000/health"
flush_cache

# Warm the hot key first — BASELINE should represent healthy, warm-cache
# behavior, not a cold-start miss burst. (warm_key only, deliberately not
# warm_and_sync: BASELINE shouldn't synchronize an expiry the way BREAK/FIX
# do.) The 5s TTL will still lapse a few times over this 30s run — that's
# accepted as ordinary steady-state miss behavior, not a confound: at 20
# unsynchronized VUs each cycle produces a handful of misses, not a herd.
warm_key "http://localhost:8000" 42
reset_app_stats "http://localhost:8000"

echo "Running light baseline load (20 VUs, 30s)..."
run_load "results/baseline_k6.json" 20 30s
snapshot_stats "http://localhost:8000" "results/baseline_stats.json"

echo ""
python3 "${VERIFY_PY}" summarize results/baseline_k6.json results/baseline_stats.json

echo ""
echo "Baseline captured. Grafana: http://localhost:3000  (anonymous access enabled)"
echo "Next: ./break.sh"
