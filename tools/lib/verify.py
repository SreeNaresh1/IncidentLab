#!/usr/bin/env python3
"""
Shared verification / reporting helper for IncidentLab experiments.

Reads k6 --summary-export JSON and each stage's /stats snapshot(s), prints
failure-reproduction / recovery PASS-FAIL tables plus any informational
signals, and writes a human-readable RESULT.md.

Two things are genuinely pattern-agnostic and fully shared:
  - the k6 summary parsing (_k6_row)
  - the criteria engine: <metric> <operator> <threshold>, the three-state
    verdict (INVALID / FAIL / PASS), and RESULT.md rendering

One thing is unavoidably pattern-specific: turning a stage's raw captured
stats into a named metric dict. Pattern 01 captures one app's cache/DB
counters; Pattern 02 captures three services' concurrency/retry/breaker
counters. Rather than branch on pattern id inline (which is exactly the
"if pattern == ..." architecture Pattern 01's own review warned against for
*criteria thresholds*), each pattern gets one explicit compute function,
registered in METRIC_COMPUTERS below. Metrics not referenced by any
criterion can still be exposed — a pattern's experiment.json declares them
in "informational_metrics" and they're rendered automatically, without any
pattern-specific rendering code.
"""
import argparse
import json
import operator
import sys
from datetime import datetime, timezone

OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
}


def _load(path):
    with open(path) as f:
        return json.load(f)


def _k6_row(k6_summary):
    m = k6_summary["metrics"]
    rps = m.get("http_reqs", {}).get("rate", 0.0)
    requests = m.get("http_reqs", {}).get("count", 0.0)
    p99_ms = m.get("http_req_duration", {}).get("p(99)", 0.0)
    checks = m.get("checks", {}).get("value", 1.0)
    error_rate = 1.0 - checks
    return {"rps": rps, "requests": requests, "p99_ms": p99_ms, "error_rate": error_rate}


def _format_metric(name, value):
    """Display-only formatting. Criteria evaluation always uses the raw float."""
    if name.endswith("_rate"):
        return f"{value * 100:.2f}%"
    if "ratio" in name:
        return f"{value:.2f}x"
    if float(value).is_integer():
        return f"{int(value)}"
    return f"{value:.4f}"


def cmd_summarize(args):
    """Quick terminal glance during a running stage. Deliberately generic —
    the detailed, pattern-aware report is RESULT.md via `check`, not this."""
    k6 = _k6_row(_load(args.k6_file))
    stats = _load(args.stats_file)
    print(f"{'Requests:':<16}{k6['requests']:.0f}")
    print(f"{'Request rate:':<16}{k6['rps']:.1f}/s")
    print(f"{'P99:':<16}{k6['p99_ms']:.0f} ms")
    print(f"{'Errors:':<16}{k6['error_rate'] * 100:.2f}%")
    print("Stats snapshot:")
    print(json.dumps(stats, indent=2))


# ---------------------------------------------------------------------------
# Pattern 01: Thundering Herd — one app, flat stats dict.
# ---------------------------------------------------------------------------
def _compute_metrics_thundering_herd(baseline_k6, baseline_stats, break_k6, break_stats, fix_k6, fix_stats):
    baseline_db = baseline_stats.get("db_queries_total", 0)
    break_db = break_stats.get("db_queries_total", 0)
    fix_db = fix_stats.get("db_queries_total", 0)
    baseline_origin_max = baseline_stats.get("origin_requests_max", 0)
    break_origin_max = break_stats.get("origin_requests_max", 0)
    fix_origin_max = fix_stats.get("origin_requests_max", 0)

    return {
        "baseline_rps": baseline_k6["rps"],
        "baseline_p99_ms": baseline_k6["p99_ms"],
        "baseline_error_rate": baseline_k6["error_rate"],
        "baseline_db_queries": float(baseline_db),
        "break_rps": break_k6["rps"],
        "break_p99_ms": break_k6["p99_ms"],
        "break_error_rate": break_k6["error_rate"],
        "break_db_queries": float(break_db),
        "fix_rps": fix_k6["rps"],
        "fix_p99_ms": fix_k6["p99_ms"],
        "fix_error_rate": fix_k6["error_rate"],
        "fix_db_queries": float(fix_db),
        "fix_cache_hits": float(fix_stats.get("cache_hits_total", 0)),
        "fix_cache_misses": float(fix_stats.get("cache_misses_total", 0)),
        "fix_lock_waits": float(fix_stats.get("lock_waits_total", 0)),
        # Recovery: FIX vs BREAK, same load profile both sides — raw totals valid here.
        "db_ratio": (fix_db / break_db) if break_db > 0 else 0.0,
        "p99_ratio": (fix_k6["p99_ms"] / baseline_k6["p99_ms"]) if baseline_k6["p99_ms"] > 0 else 0.0,
        # Failure reproduction: BREAK vs BASELINE, different workloads. Peak
        # concurrent origin fetches, not a per-request average — a real run
        # showed the per-request DB rate can be diluted flat (or worse,
        # inverted) by cache-hit volume outside the burst window, while
        # peak concurrency captures the burst directly.
        "break_origin_concurrency_ratio_to_baseline": (
            break_origin_max / baseline_origin_max
        ) if baseline_origin_max > 0 else 0.0,
        "break_p99_ratio_to_baseline": (break_k6["p99_ms"] / baseline_k6["p99_ms"]) if baseline_k6["p99_ms"] > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Pattern 02: Cascading Retry Storm — three services, nested stats dict
# ({"b": {...}, "c": {...}}, merged by break.sh/fix.sh/run.sh before this
# ever sees it — see tools/lib/experiment.sh).
#
# CHANGE 4: service-c now reports two distinct metrics:
#   c_requests_outstanding_max   — all alive handlers (waiting + processing)
#   c_processing_concurrency_max — inside semaphore only; must be <= CAPACITY
#
# The failure criterion uses c_requests_outstanding_max because that is what
# amplifies under a retry storm: retries add to the outstanding queue even
# while the semaphore is already full.  Processing concurrency is bounded by
# CAPACITY regardless of retry behaviour; it cannot show storm amplification.
# ---------------------------------------------------------------------------
def _compute_metrics_retry_storm(baseline_k6, baseline_stats, break_k6, break_stats, fix_k6, fix_stats):
    baseline_c = baseline_stats.get("c", {})
    break_b    = break_stats.get("b", {})
    break_c    = break_stats.get("c", {})
    fix_b      = fix_stats.get("b", {})
    fix_c      = fix_stats.get("c", {})

    # Outstanding: waiting + processing (shows queue amplification)
    baseline_out_max = float(baseline_c.get("c_requests_outstanding_max", 0))
    break_out_max    = float(break_c.get("c_requests_outstanding_max", 0))
    fix_out_max      = float(fix_c.get("c_requests_outstanding_max", 0))

    # Processing: inside semaphore only (bounded by CAPACITY, sanity check)
    baseline_proc_max = float(baseline_c.get("c_processing_concurrency_max", 0))
    break_proc_max    = float(break_c.get("c_processing_concurrency_max", 0))
    fix_proc_max      = float(fix_c.get("c_processing_concurrency_max", 0))

    return {
        # --- per-stage k6 summary ---
        "baseline_rps":          baseline_k6["rps"],
        "baseline_p99_ms":       baseline_k6["p99_ms"],
        "baseline_error_rate":   baseline_k6["error_rate"],
        "baseline_success_rate": 1.0 - baseline_k6["error_rate"],
        "baseline_c_outstanding_max":  baseline_out_max,
        "baseline_c_processing_max":   baseline_proc_max,

        "break_rps":             break_k6["rps"],
        "break_p99_ms":          break_k6["p99_ms"],
        "break_error_rate":      break_k6["error_rate"],
        "break_success_rate":    1.0 - break_k6["error_rate"],
        "break_c_outstanding_max":  break_out_max,
        "break_c_processing_max":   break_proc_max,
        "break_retry_attempts_total": float(break_b.get("retry_attempts_total", 0)),

        "fix_rps":               fix_k6["rps"],
        "fix_p99_ms":            fix_k6["p99_ms"],
        "fix_error_rate":        fix_k6["error_rate"],
        "fix_success_rate":      1.0 - fix_k6["error_rate"],
        "fix_c_outstanding_max":    fix_out_max,
        "fix_c_processing_max":     fix_proc_max,
        "fix_retry_attempts_total":          float(fix_b.get("retry_attempts_total", 0)),
        "fix_circuit_breaker_opens_total":   float(fix_b.get("circuit_breaker_opens_total", 0)),
        "fix_rejected_by_breaker_total":     float(fix_b.get("rejected_by_breaker_total", 0)),

        # --- criteria ratios ---
        # Failure: BREAK vs BASELINE outstanding — captures queue amplification
        "break_c_outstanding_ratio":    (break_out_max / baseline_out_max) if baseline_out_max > 0 else 0.0,
        "break_p99_ratio_to_baseline":  (break_k6["p99_ms"] / baseline_k6["p99_ms"]) if baseline_k6["p99_ms"] > 0 else 0.0,
        # Recovery: FIX vs BASELINE outstanding
        "fix_c_outstanding_ratio":      (fix_out_max / baseline_out_max) if baseline_out_max > 0 else 0.0,
        "fix_p99_ratio_to_baseline":    (fix_k6["p99_ms"] / baseline_k6["p99_ms"]) if baseline_k6["p99_ms"] > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Pattern 03: Split-Brain — witness + two nodes. Stats nested as
# {"node1": {...}, "node2": {...}, "witness": {...}, "computed": {...}},
# produced by collect_stats.py. leader_count and divergent_keys require
# comparing both nodes' actual data, not just reading a single field, so
# that comparison happens once in collect_stats.py rather than being
# duplicated here.
# ---------------------------------------------------------------------------
def _compute_metrics_split_brain(baseline_k6, baseline_stats, break_k6, break_stats, fix_k6, fix_stats):
    baseline_computed = baseline_stats.get("computed", {})
    break_computed = break_stats.get("computed", {})
    fix_computed = fix_stats.get("computed", {})

    fix_node1 = fix_stats.get("node1", {})
    fix_node2 = fix_stats.get("node2", {})
    fix_accepted = fix_node1.get("writes_accepted_total", 0) + fix_node2.get("writes_accepted_total", 0)
    fix_rejected = fix_node1.get("writes_rejected_total", 0) + fix_node2.get("writes_rejected_total", 0)
    fix_total = fix_accepted + fix_rejected

    return {
        "baseline_leader_count": float(baseline_computed.get("leader_count", 0)),
        "baseline_divergent_keys": float(baseline_computed.get("divergent_keys", 0)),
        "break_partition_confirmed": float(break_computed.get("partition_confirmed", 0)),
        "break_leader_count": float(break_computed.get("leader_count", 0)),
        "break_divergent_keys": float(break_computed.get("divergent_keys", 0)),
        "fix_leader_count": float(fix_computed.get("leader_count", 0)),
        "fix_divergent_keys": float(fix_computed.get("divergent_keys", 0)),
        "fix_availability_accepted_writes": float(fix_accepted),
        "fix_availability_total_writes": float(fix_total),
        "fix_availability_rate": (fix_accepted / fix_total) if fix_total > 0 else 0.0,
        "fix_follower_writes_rejected": float(fix_rejected),
        "reconciled_keys_pushed": float(fix_computed.get("reconciled_pushed", 0)),
        "reconciled_keys_discarded": float(fix_computed.get("reconciled_discarded", 0)),
    }


METRIC_COMPUTERS = {
    "01-thundering-herd": _compute_metrics_thundering_herd,
    "02-cascading-retry-storm": _compute_metrics_retry_storm,
    "03-split-brain": _compute_metrics_split_brain,
}


def _evaluate(criteria, metrics):
    """Returns (results, all_passed). results is [(name, passed, value), ...]."""
    results = []
    for criterion in criteria:
        metric_name = criterion["metric"]
        if metric_name not in metrics:
            print(f"Unknown metric '{metric_name}' in experiment.json", file=sys.stderr)
            sys.exit(2)
        value = metrics[metric_name]
        passed = OPERATORS[criterion["operator"]](value, criterion["threshold"])
        results.append((criterion["name"], passed, value))
    return results, all(r[1] for r in results)


def _print_section(title, results):
    print(title)
    print("─" * len(title))
    for name, passed, value in results:
        mark = "PASS" if passed else "FAIL"
        print(f"[{mark}] {name}")
        print(f"       measured: {value:.4f}")
    print("")


def _md_section(title, results):
    lines = [f"## {title}", ""]
    for name, passed, value in results:
        mark = "PASS" if passed else "FAIL"
        lines.append(f"- [{mark}] {name} (measured: {value:.4f})")
    lines.append("")
    return lines


def _informational_rows(experiment, metrics):
    return [(name, metrics[name]) for name in experiment.get("informational_metrics", []) if name in metrics]


def _print_informational(experiment, metrics):
    rows = _informational_rows(experiment, metrics)
    if not rows:
        return
    title = "Additional signals (informational — not pass/fail)"
    print(title)
    print("─" * len(title))
    for name, value in rows:
        print(f"  {name}: {_format_metric(name, value)}")
    print("")


def _md_informational(experiment, metrics):
    rows = _informational_rows(experiment, metrics)
    if not rows:
        return []
    lines = ["## Additional signals (informational — not pass/fail)", ""]
    for name, value in rows:
        lines.append(f"- {name}: {_format_metric(name, value)}")
    lines.append("")
    return lines


def _stage_section(title, k6_row):
    lines = [f"## {title}", ""]
    lines.append(f"- HTTP requests: {k6_row['requests']:.0f}")
    lines.append(f"- Request rate: {k6_row['rps']:.1f}/s")
    lines.append(f"- P99: {k6_row['p99_ms']:.0f} ms")
    lines.append(f"- Errors: {k6_row['error_rate'] * 100:.2f}%")
    lines.append("")
    return lines


def _write_result_md(path, pattern_id, baseline_k6, break_k6, fix_k6,
                      failure_results, recovery_results, experiment, metrics, verdict):
    lines = [f"# Experiment Result — {pattern_id}", ""]
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("")
    lines += _stage_section("Baseline", baseline_k6)
    lines += _stage_section("Break", break_k6)
    lines += _stage_section("Fix", fix_k6)
    lines += _md_section("Failure reproduction", failure_results)
    lines += _md_section("Recovery", recovery_results)
    lines += _md_informational(experiment, metrics)
    lines.append(f"**VERDICT: {verdict}**")
    lines.append("")
    lines.append("_Generated automatically by tools/lib/verify.py — do not hand-edit; rerun ./verify.sh instead._")
    with open(path, "w") as f:
        f.write("\n".join(lines))


def cmd_check(args):
    with open(args.experiment) as f:
        experiment = json.load(f)

    pattern_id = experiment["id"]
    compute_fn = METRIC_COMPUTERS.get(pattern_id)
    if compute_fn is None:
        print(f"No metrics computer registered for pattern '{pattern_id}' — add one to "
              f"METRIC_COMPUTERS in tools/lib/verify.py", file=sys.stderr)
        sys.exit(2)

    baseline_k6 = _k6_row(_load(args.baseline_k6))
    baseline_stats = _load(args.baseline_stats)
    break_k6 = _k6_row(_load(args.break_k6))
    break_stats = _load(args.break_stats)
    fix_k6 = _k6_row(_load(args.fix_k6))
    fix_stats = _load(args.fix_stats)

    metrics = compute_fn(baseline_k6, baseline_stats, break_k6, break_stats, fix_k6, fix_stats)

    failure_results, failure_ok = _evaluate(experiment["failure_criteria"], metrics)
    recovery_results, recovery_ok = _evaluate(experiment["recovery_criteria"], metrics)

    print("")
    _print_section("Failure reproduction", failure_results)
    _print_section("Recovery", recovery_results)
    _print_informational(experiment, metrics)

    if not failure_ok:
        verdict = "INVALID (incident not reproduced)"
        print(f"VERDICT: {verdict}")
        print("The mitigation numbers above don't mean anything until BREAK")
        print("actually demonstrates the failure — rerun, and if this")
        print("persists, the load profile or timing needs adjusting.")
    elif not recovery_ok:
        verdict = "FAIL"
        print(f"VERDICT: {verdict}")
    else:
        verdict = "PASS"
        print(f"VERDICT: {verdict}")

    if args.result_md:
        _write_result_md(
            args.result_md, pattern_id, baseline_k6, break_k6, fix_k6,
            failure_results, recovery_results, experiment, metrics, verdict,
        )
        print(f"\nWrote {args.result_md}")

    sys.exit(0 if (failure_ok and recovery_ok) else 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_summarize = sub.add_parser("summarize", help="Print one stage's metrics")
    p_summarize.add_argument("k6_file")
    p_summarize.add_argument("stats_file")
    p_summarize.set_defaults(func=cmd_summarize)

    p_check = sub.add_parser("check", help="Check a full run against experiment.json and write RESULT.md")
    p_check.add_argument("--baseline-k6", required=True)
    p_check.add_argument("--baseline-stats", required=True)
    p_check.add_argument("--break-k6", required=True)
    p_check.add_argument("--break-stats", required=True)
    p_check.add_argument("--fix-k6", required=True)
    p_check.add_argument("--fix-stats", required=True)
    p_check.add_argument("--experiment", required=True)
    p_check.add_argument("--result-md", default=None)
    p_check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
