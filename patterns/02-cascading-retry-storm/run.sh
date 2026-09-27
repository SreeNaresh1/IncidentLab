#!/usr/bin/env bash
# Stage 1: BASELINE — bring the stack up clean and record healthy-state
# behavior under open-loop load (100 RPS).
#
# Healthy state: service-c is healthy (PROCESSING_MS=50, capacity 160 req/s),
# so 100 RPS is comfortably within capacity.
#
# Load parameters:
#   RATE=100 req/s (constant-arrival-rate, open-loop)
#   DURATION=30s
#   PRE_ALLOC=200 VUs
#   MAX_VUS=1000 VUs
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
TOOLS_LIB="${SCRIPT_DIR}/../../tools/lib"
source "${TOOLS_LIB}/experiment.sh"

banner "1. BASELINE"

export PROCESSING_MS=50
export MITIGATION_ENABLED=false

# Full teardown -> clean slate for all three application services.
docker compose down -v --remove-orphans
docker compose up -d --build service-c service-b service-a prometheus grafana

wait_for_health "http://localhost:8010/health"
wait_for_health "http://localhost:8011/health"
wait_for_health "http://localhost:8012/health"

reset_app_stats "http://localhost:8011"
reset_app_stats "http://localhost:8012"

echo "Running healthy baseline load: 100 RPS open-loop against a 160 req/s capacity chain for 30s..."
run_load "results/baseline_k6.json" 10 30s \
  "-e EXECUTOR=constant-arrival-rate -e RATE=100 -e PRE_ALLOC=200 -e MAX_VUS=1000"

snapshot_stats "http://localhost:8011" "results/baseline_stats_b.json"
snapshot_stats "http://localhost:8012" "results/baseline_stats_c.json"
python3 -c "
import json
b = json.load(open('results/baseline_stats_b.json'))
c = json.load(open('results/baseline_stats_c.json'))
json.dump({'b': b, 'c': c}, open('results/baseline_stats.json', 'w'))
"

echo ""
python3 "${TOOLS_LIB}/verify.py" summarize results/baseline_k6.json results/baseline_stats.json

echo ""
echo "Baseline captured. Grafana: http://localhost:3001  (anonymous access enabled)"
echo "Next: ./break.sh"
