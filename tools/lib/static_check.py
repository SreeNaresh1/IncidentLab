#!/usr/bin/env python3
"""
Sanity check with no Docker dependency: for every pattern under patterns/,
confirms every metric name referenced in its experiment.json (in
failure_criteria, recovery_criteria, or informational_metrics) is actually
returned by verify.py's registered metrics computer for that pattern.
Catches a typo'd or renamed metric before it fails at runtime.

Run: python3 tools/lib/static_check.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS_LIB = ROOT / "tools" / "lib"
sys.path.insert(0, str(TOOLS_LIB))
import verify as v  # noqa: E402

DUMMY_K6 = {"rps": 100.0, "requests": 3000.0, "p99_ms": 100.0, "error_rate": 0.0}

# Dummy stats shaped like each pattern's real /stats snapshot(s), keyed by
# pattern id. Adding a pattern means adding one entry here.
DUMMY_STATS = {
    "01-thundering-herd": {
        "db_queries_total": 100,
        "cache_hits_total": 900,
        "cache_misses_total": 100,
        "lock_waits_total": 10,
        "origin_requests_max": 10,
    },
    "02-cascading-retry-storm": {
        "b": {
            "requests_total": 1000,
            "retry_attempts_total": 50,
            "circuit_breaker_opens_total": 2,
            "rejected_by_breaker_total": 20,
        },
        "c": {
            "requests_total": 1000,
            "c_requests_outstanding_max": 20,
            "c_processing_concurrency_max": 8,
        },
    },
    "03-split-brain": {
        "node1": {"writes_accepted_total": 5, "writes_rejected_total": 2, "is_leader": True, "current_epoch": 3},
        "node2": {"writes_accepted_total": 3, "writes_rejected_total": 1, "is_leader": False, "current_epoch": 0},
        "witness": {"claims_granted_total": 2, "claims_rejected_total": 1, "current_epoch": 3, "current_leader": "node-1"},
        "computed": {
            "leader_count": 1,
            "divergent_keys": 0,
            "partition_confirmed": 1,
            "reconciled_pushed": 2,
            "reconciled_discarded": 1,
        },
    },
}


def check_pattern(experiment_path):
    exp = json.loads(experiment_path.read_text())
    pattern_id = exp["id"]

    referenced = set()
    for group in ("failure_criteria", "recovery_criteria"):
        for c in exp.get(group, []):
            referenced.add(c["metric"])
    referenced |= set(exp.get("informational_metrics", []))

    compute_fn = v.METRIC_COMPUTERS.get(pattern_id)
    if compute_fn is None:
        print(f"FAIL {pattern_id}: no entry in verify.py's METRIC_COMPUTERS")
        return False

    dummy_stats = DUMMY_STATS.get(pattern_id)
    if dummy_stats is None:
        print(f"FAIL {pattern_id}: no dummy stats shape in static_check.py's DUMMY_STATS")
        return False

    available = set(compute_fn(DUMMY_K6, dummy_stats, DUMMY_K6, dummy_stats, DUMMY_K6, dummy_stats).keys())
    missing = referenced - available

    if missing:
        print(f"FAIL {pattern_id}: referenced but not computed: {sorted(missing)}")
        return False

    print(f"PASS {pattern_id}: {len(referenced)} referenced metrics all present")
    return True


def main():
    pattern_dirs = sorted((ROOT / "patterns").glob("*/experiment.json"))
    if not pattern_dirs:
        print("No patterns/*/experiment.json found", file=sys.stderr)
        sys.exit(2)

    all_ok = True
    for experiment_path in pattern_dirs:
        all_ok = check_pattern(experiment_path) and all_ok

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
