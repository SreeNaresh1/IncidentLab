#!/usr/bin/env bash
# Shared helpers sourced by every pattern's run.sh / break.sh / fix.sh / verify.sh.
# Keep this file free of anything pattern-specific — patterns should only
# need to set variables and call these functions.

export MSYS_NO_PATHCONV=1
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

# Use relative path so Python invocation works across Linux, macOS, and Windows/MSYS
TOOLS_LIB="../../tools/lib"
VERIFY_PY="${TOOLS_LIB}/verify.py"

BOLD=$(tput bold 2>/dev/null || echo "")
RESET=$(tput sgr0 2>/dev/null || echo "")
RED=$(tput setaf 1 2>/dev/null || echo "")

banner() {
  echo ""
  echo "${BOLD}── $1 ────────────────────────────────────────${RESET}"
  echo ""
}

# wait_for_health <url> [timeout_seconds]
wait_for_health() {
  local url="$1"
  local timeout="${2:-60}"
  local waited=0
  echo -n "Waiting for ${url} "
  until curl -sf "${url}" > /dev/null 2>&1; do
    sleep 1
    waited=$((waited + 1))
    echo -n "."
    if [ "${waited}" -ge "${timeout}" ]; then
      echo ""
      echo "${RED}Timed out waiting for ${url} after ${timeout}s${RESET}" >&2
      echo "Check 'docker compose logs' in this pattern's directory." >&2
      exit 1
    fi
  done
  echo " ready"
}

# reset_app_stats <app_base_url>
reset_app_stats() {
  curl -sf -X POST "$1/admin/reset" > /dev/null
}

# flush_cache <redis_service_name>
# Every stage must start from a known-empty cache — otherwise BREAK can
# inherit a still-live entry from BASELINE and the first portion of the run
# isn't a clean experiment. -T disables the pseudo-TTY so this works
# non-interactively (CI, scripts).
flush_cache() {
  local redis_service="${1:-redis}"
  docker compose exec -T "${redis_service}" redis-cli FLUSHDB > /dev/null
}

# warm_key <app_base_url> <product_id>
# Populates the cache for one key with no timing coordination. For
# BASELINE, which wants a normally warm cache representing healthy
# steady-state behavior — not a synchronized expiry, which is what
# warm_and_sync below is for.
warm_key() {
  curl -sf "$1/product/$2" > /dev/null
}

# warm_and_sync <app_base_url> <product_id> <ttl_seconds>
# Deterministically triggers the FIRST herd event instead of leaving it to
# chance: warm the key, then sleep until just before that entry's TTL
# expires. This positions the first cache expiry immediately around the
# beginning of the load window — not mathematically synchronized to it,
# since Compose/k6 startup between the sleep and the first actual request
# still adds some slack, but reproducible run to run in a way "let it
# happen whenever traffic timing causes it" wasn't. Subsequent TTL cycles
# during the run are naturally less synchronized (closer to production),
# which is fine — the decisive first burst is what's measured. Use
# warm_key alone (not this) for BASELINE — see run.sh.
warm_and_sync() {
  local app_url="$1"
  local product_id="$2"
  local ttl_seconds="$3"
  warm_key "${app_url}" "${product_id}"
  local sync_wait=$((ttl_seconds - 1))
  if [ "${sync_wait}" -gt 0 ]; then
    sleep "${sync_wait}"
  fi
}

# snapshot_stats <app_base_url> <output_file>
snapshot_stats() {
  mkdir -p "$(dirname "$2")"
  curl -sf "$1/stats" -o "$2"
}

# run_load <results_file> <vus> <duration> [extra_env_args]
# Runs k6 as a one-off Compose service against the app, on the same Docker
# network, and writes its summary JSON to ./results/.
#
# extra_env_args (optional 4th argument) is a string of additional
# "-e KEY=VALUE" pairs, e.g. "-e EXECUTOR=constant-arrival-rate -e RATE=100".
# Pattern 01 does not pass this argument; Pattern 02 uses it to configure
# open-loop constant-arrival-rate across all experiment stages.
run_load() {
  local results_file="$1"
  local vus="$2"
  local duration="$3"
  local extra_env_args="${4:-}"  # optional; empty string is safe
  mkdir -p results
  # shellcheck disable=SC2086  # word-split on extra_env_args is intentional
  docker compose run --rm \
    -e VUS="${vus}" \
    -e DURATION="${duration}" \
    ${extra_env_args} \
    k6 run --summary-export="/scripts/results/$(basename "${results_file}")" /scripts/k6-script.js
}
