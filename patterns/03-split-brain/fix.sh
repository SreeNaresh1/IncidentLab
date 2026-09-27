#!/usr/bin/env bash
# Stage 4: FIX -- switch to witness-mediated leadership, reconcile the
# real divergent data BREAK left behind, then prove no NEW divergence
# occurs under a fresh partition.
#
# Deliberately does NOT recreate node-1/node-2 containers at the start --
# that would wipe the in-memory KV store, and this stage specifically
# needs BREAK's leftover divergent data to still be there. Mitigation
# mode is switched at runtime instead (POST /admin/set-mode), which is
# why that endpoint exists. This is a one-time, deliberate exception to
# the "full reset between stages" discipline, not a lapse in it -- see
# this pattern's README for the reasoning.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
TOOLS_LIB="${SCRIPT_DIR}/../../tools/lib"
source "${TOOLS_LIB}/experiment.sh"

banner "4. FIX"

CLUSTER_NET="incidentlab-03-cluster-net"

echo "Applying:"
echo "  - witness-mediated leader election (atomic lease claim, not peer reachability)"
echo "  - per-write fencing (a node must hold a currently-valid epoch to accept writes)"
echo ""

docker exec incidentlab-03-node-1 python3 -c "
import urllib.request, json
def _post(url, d):
  req = urllib.request.Request(url, data=json.dumps(d).encode(), headers={'Content-Type': 'application/json'})
  urllib.request.urlopen(req)
_post('http://witness:8000/admin/reset', {})
_post('http://node-1:8000/admin/set-mode', {'mitigation_enabled': True})
_post('http://node-2:8000/admin/set-mode', {'mitigation_enabled': True})
_post('http://node-1:8000/admin/reset', {'keep_data': True})
_post('http://node-2:8000/admin/reset', {'keep_data': True})
"

# The witness is reachable over quorum-net regardless of cluster-net's
# state, so this converges even while still partitioned (inherited from
# break.sh). Wait up to 5s for election to settle.
LEADER_COUNT="0"
for i in {1..10}; do
  sleep 0.5
  LEADER_COUNT=$(docker exec incidentlab-03-node-1 python3 -c "
import json, urllib.request
n1 = json.loads(urllib.request.urlopen('http://node-1:8000/stats', timeout=3).read())
n2 = json.loads(urllib.request.urlopen('http://node-2:8000/stats', timeout=3).read())
print(int(n1['is_leader']) + int(n2['is_leader']))
")
  if [ "$LEADER_COUNT" == "1" ]; then
    break
  fi
done

if [ "$LEADER_COUNT" != "1" ]; then
  echo "Precondition FAILED: expected exactly 1 leader under mitigation, got ${LEADER_COUNT}." >&2
  exit 1
fi
echo "Mitigated election settled: exactly 1 leader (witness-mediated)."

banner "RECONCILE"
echo "Reconciling BREAK's leftover divergent data (epoch-based rule)..."
mkdir -p results
python3 reconcile.py "http://localhost:8020" "http://localhost:8021" --apply --output results/reconcile_report.json 2>/dev/null || \
docker exec incidentlab-03-node-1 python3 /app/pattern/reconcile.py "http://node-1:8000" "http://node-2:8000" --apply --output /app/pattern/results/reconcile_report.json
echo ""

banner "FIX load test"
# Confirm (not assume) the partition inherited from break.sh is still in
# effect; if not, create it fresh. Either way, this is FIX's own
# partition window, being measured for NEW divergence.
if docker exec incidentlab-03-node-1 python3 -c "
import urllib.request
urllib.request.urlopen('http://node-2-peer:8000/health', timeout=2)
" 2>/dev/null; then
  echo "Partition had healed -- creating it fresh for this stage."
  docker network disconnect "${CLUSTER_NET}" incidentlab-03-node-2
else
  echo "Partition still in effect (inherited from break.sh)."
fi

echo "Replaying load: 20s of writes against both nodes while partitioned..."
docker compose run --no-deps --rm \
  -e NODE1_URL="http://node-1:8000" -e NODE2_URL="http://node-2:8000" \
  -e RATE="15" -e DURATION="20s" \
  k6 run --summary-export="/scripts/results/fix_k6.json" /scripts/k6-script.js

docker exec incidentlab-03-node-1 python3 /app/pattern/collect_stats.py \
  "http://node-1:8000" "http://node-2:8000" "http://witness:8000" \
  /app/pattern/results/fix_stats.json --partition-confirmed 1

# Fold the reconciliation counts into fix_stats.json's "computed" section
# so verify.py can expose them as informational metrics without needing
# to read a second file.
python3 -c "
import json
stats = json.load(open('results/fix_stats.json'))
report = json.load(open('results/reconcile_report.json'))
stats['computed']['reconciled_pushed'] = len(report['pushed'])
stats['computed']['reconciled_discarded'] = len(report['discarded'])
json.dump(stats, open('results/fix_stats.json', 'w'), indent=2)
"

echo ""
python3 "${TOOLS_LIB}/verify.py" summarize results/fix_k6.json results/fix_stats.json 2>/dev/null || true

# Heal now -- reconciliation and both load tests are done with this data.
# --force-recreate: see break.sh for why a plain `up -d` isn't enough
# after a raw `docker network disconnect`.
echo ""
echo "Healing the partition and resetting for the next full run..."
docker compose up -d --no-build --force-recreate node-2
wait_for_health "http://localhost:8021/health"

echo ""
echo "Next: ./verify.sh"
