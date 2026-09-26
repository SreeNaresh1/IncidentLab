#!/usr/bin/env bash
# Stage 4: FIX — enable exponential backoff + jitter + bounded retry budget
# + circuit breaker, then replay the EXACT SAME open-loop load against the
# EXACT SAME degraded service-c as break.sh.
#
# Load parameters (identical to BASELINE and BREAK):
#   RATE=100 req/s (constant-arrival-rate, open-loop)
#   DURATION=45s
#   PRE_ALLOC=200 VUs
#   MAX_VUS=1000 VUs
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
TOOLS_LIB="${SCRIPT_DIR}/../../tools/lib"
source "${TOOLS_LIB}/experiment.sh"

banner "4. FIX"

echo "Applying:"
echo "  - exponential backoff + jitter"
echo "  - bounded retries + a shared retry budget"
echo "  - circuit breaker (fails fast once error rate crosses threshold)"
echo "  - in-loop breaker validation (retries abort if breaker opened)"
echo ""

export PROCESSING_MS=90
export MITIGATION_ENABLED=true

# Full teardown -> clean slate for all three application services.
docker compose down -v --remove-orphans
docker compose up -d --no-build service-c service-b service-a prometheus grafana

wait_for_health "http://localhost:8010/health"
wait_for_health "http://localhost:8011/health"
wait_for_health "http://localhost:8012/health"

reset_app_stats "http://localhost:8011"
reset_app_stats "http://localhost:8012"

echo "Replaying identical open-loop load: 100 RPS, 45s against degraded service-c..."
run_load "results/fix_k6.json" 10 45s \
  "-e EXECUTOR=constant-arrival-rate -e RATE=100 -e PRE_ALLOC=200 -e MAX_VUS=1000"

snapshot_stats "http://localhost:8011" "results/fix_stats_b.json"
snapshot_stats "http://localhost:8012" "results/fix_stats_c.json"
python3 -c "
import json
b = json.load(open('results/fix_stats_b.json'))
c = json.load(open('results/fix_stats_c.json'))
json.dump({'b': b, 'c': c}, open('results/fix_stats.json', 'w'))
"

echo ""
python3 "${TOOLS_LIB}/verify.py" summarize results/fix_k6.json results/fix_stats.json

echo ""
echo "Next: ./verify.sh"
