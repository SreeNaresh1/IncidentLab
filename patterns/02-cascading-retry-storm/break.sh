#!/usr/bin/env bash
# Stage 2 + 3: BREAK + OBSERVE
#
# Degraded state: service-c experiences a slowdown (PROCESSING_MS=90, capacity 88.9 req/s),
# making it unable to sustain the 100 RPS incoming load.
# Under naive retries (MITIGATION_ENABLED=false), timeouts trigger immediate retries,
# creating a cascading retry storm.
#
# Load parameters (identical open-loop load to BASELINE and FIX):
#   RATE=100 req/s (constant-arrival-rate, open-loop)
#   DURATION=45s
#   PRE_ALLOC=200 VUs
#   MAX_VUS=1000 VUs
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
TOOLS_LIB="${SCRIPT_DIR}/../../tools/lib"
source "${TOOLS_LIB}/experiment.sh"

banner "2. BREAK"

export PROCESSING_MS=90
export MITIGATION_ENABLED=false

# Full teardown -> clean slate for all three application services.
docker compose down -v --remove-orphans
docker compose up -d --no-build service-c service-b service-a prometheus grafana

wait_for_health "http://localhost:8010/health"
wait_for_health "http://localhost:8011/health"
wait_for_health "http://localhost:8012/health"

reset_app_stats "http://localhost:8011"
reset_app_stats "http://localhost:8012"

echo "Injecting failure: 100 RPS open-loop against degraded service-c (88.9 req/s capacity) for 45s..."
run_load "results/break_k6.json" 10 45s \
  "-e EXECUTOR=constant-arrival-rate -e RATE=100 -e PRE_ALLOC=200 -e MAX_VUS=1000"

banner "3. OBSERVE"
snapshot_stats "http://localhost:8011" "results/break_stats_b.json"
snapshot_stats "http://localhost:8012" "results/break_stats_c.json"
python3 -c "
import json
b = json.load(open('results/break_stats_b.json'))
c = json.load(open('results/break_stats_c.json'))
json.dump({'b': b, 'c': c}, open('results/break_stats.json', 'w'))
"
python3 "${TOOLS_LIB}/verify.py" summarize results/break_k6.json results/break_stats.json

echo ""
echo "System is degrading. Watch it live at http://localhost:3001"
echo "Next: ./fix.sh"
