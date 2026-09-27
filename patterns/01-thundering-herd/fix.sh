#!/usr/bin/env bash
# Stage 4: FIX — enable lease locking + jittered TTL + request coalescing,
# then replay the exact same load profile as break.sh so the comparison is
# apples-to-apples.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS_LIB="${SCRIPT_DIR}/../../tools/lib"
VERIFY_PY="${TOOLS_LIB}/verify.py"
cd "${SCRIPT_DIR}"
source "${TOOLS_LIB}/experiment.sh"

banner "4. FIX"

echo "Applying:"
echo "  - request coalescing"
echo "  - jittered cache expiration"
echo "  - lease locking"
echo ""

export MITIGATION_ENABLED=true
docker compose up -d --build --force-recreate app
wait_for_health "http://localhost:8000/health"

# Same reset + sync sequence as break.sh — the only variable that should
# differ between BREAK and FIX is the mitigation itself.
flush_cache
warm_and_sync "http://localhost:8000" 42 5
reset_app_stats "http://localhost:8000"

echo "Replaying identical load: 300 VUs, 45s..."
run_load "results/fix_k6.json" 300 45s
snapshot_stats "http://localhost:8000" "results/fix_stats.json"

echo ""
python3 "${VERIFY_PY}" summarize results/fix_k6.json results/fix_stats.json

echo ""
echo "Next: ./verify.sh"
