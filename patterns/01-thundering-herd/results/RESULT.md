# Experiment Result — 01-thundering-herd

Generated: 2026-09-27T12:27:38+00:00

## Baseline

- HTTP requests: 3852
- Request rate: 127.3/s
- P99: 153 ms
- Errors: 0.00%

## Break

- HTTP requests: 76455
- Request rate: 1688.5/s
- P99: 1036 ms
- Errors: 0.00%

## Fix

- HTTP requests: 87571
- Request rate: 1933.5/s
- P99: 111 ms
- Errors: 0.00%

## Failure reproduction

- [PASS] BREAK peak concurrent origin fetches >= 5x BASELINE (thundering herd mechanism) (measured: 14.8750)
- [PASS] BREAK P99 reaches at least 2x BASELINE (latency consequence) (measured: 6.7659)

## Recovery

- [PASS] DB queries during FIX <= 15% of DB queries during BREAK (measured: 0.0061)
- [PASS] Error rate during FIX < 1% (measured: 0.0000)
- [PASS] P99 latency during FIX <= 2x BASELINE P99 (measured: 0.7265)

## Additional signals (informational — not pass/fail)

- break_db_queries: 1152
- fix_db_queries: 7
- fix_cache_hits: 87564
- fix_cache_misses: 1595
- fix_lock_waits: 1588

**VERDICT: PASS**

_Generated automatically by tools/lib/verify.py — do not hand-edit; rerun ./verify.sh instead._