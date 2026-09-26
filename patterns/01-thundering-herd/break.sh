#!/usr/bin/env bash
# Stage 2 + 3: BREAK + OBSERVE — remove the mitigation and put the system
# under the load pattern that triggers a thundering herd: many concurrent
# requests sharing one hot key whose cache entry keeps expiring.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS_LIB="${SCRIPT_DIR}/../../tools/lib"
VERIFY_PY="${TOOLS_LIB}/verify.py"
cd "${SCRIPT_DIR}"
source "${TOOLS_LIB}/experiment.sh"

banner "2. BREAK"

export MITIGATION_ENABLED=false
docker compose up -d --build --force-recreate app
wait_for_health "http://localhost:8000/health"

# Start every stage from a known-empty cache — otherwise this run can
# inherit a still-live cache entry left over from BASELINE.
flush_cache

# Populate the hot key, then wait until just before its TTL expires so the
# load below lands its first burst exactly on the expiry — the decisive
# herd event is now reproducible instead of depending on request timing.
warm_and_sync "http://localhost:8000" 42 5

reset_app_stats "http://localhost:8000"

echo "Injecting failure: 300 VUs hammering one hot key for 45s..."
run_load "results/break_k6.json" 300 45s

banner "3. OBSERVE"
snapshot_stats "http://localhost:8000" "results/break_stats.json"
python3 "${VERIFY_PY}" summarize results/break_k6.json results/break_stats.json

echo ""
echo "System is degrading. Watch it live at http://localhost:3000"
echo "Next: ./fix.sh"
