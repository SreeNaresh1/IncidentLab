# Experiment Result — 02-cascading-retry-storm

Generated: 2026-09-27T12:31:46+00:00

## Baseline

- HTTP requests: 3001
- Request rate: 99.9/s
- P99: 56 ms
- Errors: 0.00%

## Break

- HTTP requests: 4501
- Request rate: 99.1/s
- P99: 515 ms
- Errors: 99.80%

## Fix

- HTTP requests: 4500
- Request rate: 100.0/s
- P99: 106 ms
- Errors: 93.13%

## Failure reproduction

- [PASS] BREAK peak C outstanding requests reaches at least 5x BASELINE (measured: 1758.3750)
- [PASS] BREAK P99 at A reaches at least 2x BASELINE (measured: 9.1439)

## Recovery

- [PASS] FIX peak C outstanding requests returns to <= 2x BASELINE (measured: 1.6250)
- [PASS] FIX P99 at A returns to <= 2x BASELINE (measured: 1.8802)

## Additional signals (informational — not pass/fail)

- baseline_success_rate: 100.00%
- baseline_c_outstanding_max: 8
- baseline_c_processing_max: 8
- break_success_rate: 0.20%
- break_c_outstanding_max: 14067
- break_c_processing_max: 8
- break_retry_attempts_total: 13476
- fix_success_rate: 6.87%
- fix_c_outstanding_max: 13
- fix_c_processing_max: 8
- fix_retry_attempts_total: 34
- fix_circuit_breaker_opens_total: 99
- fix_rejected_by_breaker_total: 4058

**VERDICT: PASS**

_Generated automatically by tools/lib/verify.py — do not hand-edit; rerun ./verify.sh instead._