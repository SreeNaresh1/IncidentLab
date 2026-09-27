#!/usr/bin/env bash
# Stage 2 + 3: BREAK + OBSERVE -- sever the peer channel for real (a
# genuine docker network disconnect, not a simulated flag) and let the
# naive rule do exactly what it's designed to do: both nodes lose their
# peer, both conclude "it must be down," both promote themselves.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
TOOLS_LIB="../../tools/lib"
source "${TOOLS_LIB}/experiment.sh"

banner "2. BREAK"

CLUSTER_NET="incidentlab-03-cluster-net"

# Ensure node-2 is connected to cluster-net for the pre-partition check
docker network connect "${CLUSTER_NET}" incidentlab-03-node-2 2>/dev/null || true

docker exec incidentlab-03-node-1 python3 -c "
import urllib.request, json
def _post(url, d):
  req = urllib.request.Request(url, data=json.dumps(d).encode(), headers={'Content-Type': 'application/json'})
  urllib.request.urlopen(req, timeout=3)
_post('http://witness:8000/admin/reset', {})
_post('http://node-1:8000/admin/set-mode', {'mitigation_enabled': False})
_post('http://node-2:8000/admin/set-mode', {'mitigation_enabled': False})
_post('http://node-1:8000/admin/reset', {'keep_data': False})
_post('http://node-2:8000/admin/reset', {'keep_data': False})
"
sleep 2

LEADER_COUNT=$(docker exec incidentlab-03-node-1 python3 -c "
import json, urllib.request
n1 = json.loads(urllib.request.urlopen('http://node-1:8000/stats', timeout=3).read())
n2 = json.loads(urllib.request.urlopen('http://node-2:8000/stats', timeout=3).read())
print(int(n1['is_leader']) + int(n2['is_leader']))
")
if [ "$LEADER_COUNT" != "1" ]; then
  echo "Pre-partition precondition FAILED: expected exactly 1 leader, got ${LEADER_COUNT}." >&2
  exit 1
fi
echo "Pre-partition: exactly 1 leader, as expected. Partitioning now..."

# The actual break: sever node-2's connectivity on the peer channel. Both
# sides lose the ability to reach each other -- disconnecting one side is
# enough, since node-1 was never reachable FROM a network node-2 isn't on.
# Idempotent: break.sh may be rerun without an intervening fix.sh, in
# which case node-2 is already disconnected from a prior run.
docker network disconnect "${CLUSTER_NET}" incidentlab-03-node-2 2>/dev/null \
  || echo "(node-2 was already disconnected from ${CLUSTER_NET})"

# Don't just assume the disconnect worked -- verify it directly, matching
# Pattern 02's lesson about proving the mechanism instead of assuming it.
echo "Confirming the partition actually severed peer communication..."
if docker exec incidentlab-03-node-1 python3 -c "
import urllib.request
urllib.request.urlopen('http://node-2-peer:8000/health', timeout=2)
" 2>/dev/null; then
  echo "Partition NOT confirmed -- node-1 can still reach node-2-peer. Aborting." >&2
  exit 1
fi
echo "Partition confirmed: node-1 cannot reach node-2-peer."
PARTITION_CONFIRMED=1

# Allow heartbeat cycles for both nodes to detect the partition and promote themselves
sleep 1

echo "Injecting failure: 20s of writes against both nodes while partitioned..."
mkdir -p results
docker compose run --no-deps --rm \
  -e NODE1_URL="http://node-1:8000" -e NODE2_URL="http://node-2:8000" \
  -e RATE="15" -e DURATION="20s" \
  k6 run --summary-export="/scripts/results/break_k6.json" /scripts/k6-script.js

banner "3. OBSERVE"
docker exec incidentlab-03-node-1 python3 /app/pattern/collect_stats.py \
  "http://node-1:8000" "http://node-2:8000" "http://witness:8000" \
  /app/pattern/results/break_stats.json --partition-confirmed "${PARTITION_CONFIRMED}"

# Deliberately NOT healing the partition here. Force-recreating node-2 to
# restore cluster-net would wipe its in-memory KV store -- exactly the
# divergent data FIX needs for reconciliation. fix.sh inherits this
# still-partitioned state (mitigated leadership only needs quorum-net,
# which was never touched) and heals at its own end, once reconciliation
# and its own measurements no longer need this data.
echo ""
echo "Left intentionally partitioned -- fix.sh needs this data for reconciliation."
echo "System is split-brained. Watch it live at http://localhost:3002"
echo "Next: ./fix.sh"
