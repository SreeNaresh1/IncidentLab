#!/usr/bin/env bash
# Stage 5: VERIFY -- checks each causal step separately (precondition,
# partition confirmed, both-claim-leader, divergence occurred for BREAK;
# single-leader, zero-new-divergence for FIX), not just an overall
# before/after comparison. See experiment.json.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
TOOLS_LIB="../../tools/lib"
source "${TOOLS_LIB}/experiment.sh"

banner "5. VERIFY"

python3 "${TOOLS_LIB}/verify.py" check \
  --baseline-k6 results/baseline_k6.json \
  --baseline-stats results/baseline_stats.json \
  --break-k6 results/break_k6.json \
  --break-stats results/break_stats.json \
  --fix-k6 results/fix_k6.json \
  --fix-stats results/fix_stats.json \
  --experiment experiment.json \
  --result-md results/RESULT.md
