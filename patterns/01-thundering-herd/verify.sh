#!/usr/bin/env bash
# Stage 5: VERIFY — check that BREAK actually reproduced the incident AND
# that FIX actually recovered it (experiment.json's failure_criteria and
# recovery_criteria). Exits non-zero unless both hold, so this is
# CI-friendly.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOOLS_LIB="${SCRIPT_DIR}/../../tools/lib"
VERIFY_PY="${TOOLS_LIB}/verify.py"
cd "${SCRIPT_DIR}"
source "${TOOLS_LIB}/experiment.sh"

banner "5. VERIFY"

python3 "${VERIFY_PY}" check \
  --baseline-k6 results/baseline_k6.json \
  --baseline-stats results/baseline_stats.json \
  --break-k6 results/break_k6.json \
  --break-stats results/break_stats.json \
  --fix-k6 results/fix_k6.json \
  --fix-stats results/fix_stats.json \
  --experiment experiment.json \
  --result-md results/RESULT.md
