# Experiment Result — 03-split-brain

Generated: 2026-10-02T09:42:13+00:00

## Baseline

- HTTP requests: 300
- Request rate: 15.0/s
- P99: 2 ms
- Errors: 0.00%

## Break

- HTTP requests: 301
- Request rate: 15.0/s
- P99: 1 ms
- Errors: 0.00%

## Fix

- HTTP requests: 301
- Request rate: 15.0/s
- P99: 2 ms
- Errors: 0.00%

## Failure reproduction

- [PASS] BASELINE precondition: exactly one leader before anything is broken (measured: 1.0000)
- [PASS] BASELINE precondition: zero divergence before anything is broken (measured: 0.0000)
- [PASS] BREAK: the partition actually severed peer communication (measured: 1.0000)
- [PASS] BREAK: both nodes claim leadership (the actual split-brain condition) (measured: 2.0000)
- [PASS] BREAK: divergent keys > 0 (both leaders actually accepted conflicting writes) (measured: 20.0000)

## Recovery

- [PASS] FIX: exactly one leader while partitioned (witness-mediated quorum) (measured: 1.0000)
- [PASS] FIX: zero new divergence created during the partition (measured: 0.0000)

## Additional signals (informational — not pass/fail)

- fix_availability_accepted_writes: 159
- fix_availability_total_writes: 301
- fix_availability_rate: 52.82%
- fix_follower_writes_rejected: 142
- reconciled_keys_pushed: 0
- reconciled_keys_discarded: 20

**VERDICT: PASS**

_Generated automatically by tools/lib/verify.py — do not hand-edit; rerun ./verify.sh instead._