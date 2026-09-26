# Experiment Result — 01-thundering-herd

Generated: 2026-08-22T17:45:41+00:00

## Baseline

- HTTP requests: 3837
- Request rate: 126.8/s
- P99: 153 ms
- Errors: 0.00%
- DB queries (total): 75
- DB queries per request: 0.0195
- Origin max concurrent: 17
- Cache hits: 3762
- Cache misses: 75
- Lock waits: 0

## Break

- HTTP requests: 75755
- Request rate: 1672.8/s
- P99: 1176 ms
- Errors: 0.00%
- DB queries (total): 1164
- DB queries per request: 0.0154
- Origin max concurrent: 232
- Cache hits: 74591
- Cache misses: 1164
- Lock waits: 0

## Fix

- HTTP requests: 87156
- Request rate: 1923.9/s
- P99: 151 ms
- Errors: 0.00%
- DB queries (total): 7
- DB queries per request: 0.0001
- Origin max concurrent: 1
- Cache hits: 87149
- Cache misses: 1657
- Lock waits: 1650

## Failure reproduction

- [PASS] BREAK peak concurrent origin fetches >= 5x BASELINE (thundering herd mechanism) (measured: 13.6471)
- [PASS] BREAK P99 reaches at least 2x BASELINE (latency consequence) (measured: 7.6722)

## Recovery

- [PASS] DB queries during FIX <= 15% of DB queries during BREAK (measured: 0.0060)
- [PASS] Error rate during FIX < 1% (measured: 0.0000)
- [PASS] P99 latency during FIX <= 2x BASELINE P99 (measured: 0.9879)

**VERDICT: PASS**

_Generated automatically by tools/lib/verify.py — do not hand-edit; rerun ./verify.sh instead._