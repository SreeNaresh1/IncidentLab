#!/usr/bin/env bash
# Stage 1: BASELINE -- bring the stack up fully connected, confirm exactly
# one leader and zero divergence BEFORE anything is broken. Runs in naive
# mode deliberately: naive's peer-reachability rule only goes wrong under
# a partition, so a healthy baseline looks identical either way. That's
# the actual lesson -- the bug is invisible until the specific failure
# condition triggers it.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
TOOLS_LIB="../../tools/lib"
source "${TOOLS_LIB}/experiment.sh"

banner "1. BASELINE"

docker compose down -v --remove-orphans
export MITIGATION_ENABLED=false
docker compose up -d --build witness node-1 node-2 prometheus grafana

wait_for_health "http://localhost:8022/health"
wait_for_health "http://localhost:8020/health"
wait_for_health "http://localhost:8021/health"

# Full reset: fresh data, fresh leadership contest.
docker exec incidentlab-03-node-1 python3 -c "
import urllib.request, json
def _post(url, d):
  req = urllib.request.Request(url, data=json.dumps(d).encode(), headers={'Content-Type': 'application/json'})
  urllib.request.urlopen(req, timeout=3)
_post('http://witness:8000/admin/reset', {})
_post('http://node-1:8000/admin/reset', {'keep_data': False})
_post('http://node-2:8000/admin/reset', {'keep_data': False})
"

# A few heartbeat cycles (300ms interval) to let a leader converge before
# trusting the precondition check.
sleep 2

echo "Checking precondition: exactly one leader, zero divergence..."
mkdir -p results
python3 collect_stats.py \
  "http://localhost:8020" "http://localhost:8021" "http://localhost:8022" \
  results/baseline_stats.json 2>/dev/null || \
docker exec incidentlab-03-node-1 python3 /app/pattern/collect_stats.py \
  "http://node-1:8000" "http://node-2:8000" "http://witness:8000" \
  /app/pattern/results/baseline_stats.json
LEADER_COUNT=$(python3 -c "import json; print(json.load(open('results/baseline_stats.json'))['computed']['leader_count'])")
if [ "$LEADER_COUNT" != "1" ]; then
  echo "Precondition FAILED: expected exactly 1 leader, got ${LEADER_COUNT}." >&2
  echo "This means the naive tie-break (lower node_id wins when both reachable)" >&2
  echo "didn't converge -- rerun, and if this persists, HEARTBEAT_INTERVAL_MS" >&2
  echo "or the 2s settle time above may need adjusting." >&2
  exit 1
fi
echo "Precondition OK: exactly 1 leader."

echo "Running baseline load (RATE=15/s, 20s)..."
docker compose run --no-deps --rm \
  -e NODE1_URL="http://node-1:8000" -e NODE2_URL="http://node-2:8000" \
  -e RATE="15" -e DURATION="20s" \
  k6 run --summary-export="/scripts/results/baseline_k6.json" /scripts/k6-script.js

echo ""
echo "Baseline captured. Grafana: http://localhost:3002  (anonymous access enabled)"
echo "Next: ./break.sh"
