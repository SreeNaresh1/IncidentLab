#!/usr/bin/env bash
# Stage 5: VERIFY — check that BREAK actually reproduced the incident AND
# that FIX actually recovered it (experiment.json's failure_criteria and
# recovery_criteria). Error rate is deliberately informational only for
# this pattern -- a circuit breaker is supposed to fail fast, not fail
# never, so a non-zero FIX error rate isn't itself a defect. See this
# pattern's README for why "reject everything" still can't pass unnoticed.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
TOOLS_LIB="${SCRIPT_DIR}/../../tools/lib"
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
