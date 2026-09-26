# 02 — Cascading Retry Storm

```
Downstream (Service C) slows / degrades
      ↓
Service B times out
      ↓
Service B retries (immediately, no backoff, no limit)
      ↓
Service C gets MORE traffic & queue explodes
      ↓
Service C gets even slower
      ↓
more timeouts, more retries
      ↓
system collapses
```

## Architecture

```
   k6 (open-loop: constant-arrival-rate)
    │
    ▼
service-a (gateway)
    │  /checkout
    ▼
service-b (checkout-service)  ◄── naive vs mitigated lives entirely here
    │  /process
    ▼
service-c (payment-processor)
    (capacity-limited: 8 concurrent slots)
    - Healthy: 50ms processing → 160 req/s capacity (comfortably handles 100 RPS)
    - Degraded: 90ms processing → 88.9 req/s capacity (saturated by 100 RPS)
```

No Postgres or Redis this time — this pattern uses only the components its
specific failure mode needs.

## Schema

```yaml
id: 02-cascading-retry-storm
version: 1.0.0
pattern: cascading-retry-storm
category: retries
difficulty: intermediate

mechanism: >
  service-b calls service-c on every request. service-c has fixed
  capacity (an asyncio.Semaphore of 8 slots). In baseline, service-c processes
  work in 50ms (giving 160 req/s capacity, healthy for 100 RPS load).
  In BREAK and FIX, service-c experiences downstream degradation (processing
  slows to 90ms, dropping throughput ceiling to 88.9 req/s), causing incoming
  100 RPS traffic to queue. In the naive path, when service-b's call to
  service-c times out (at 100ms), it retries immediately up to 3 times with
  no backoff, no jitter, and no coordination across concurrent requests.
  This creates an explosive feedback loop: timeouts trigger retries, retries
  quadruple traffic to service-c, and deeper queueing causes more timeouts.
trigger: >
  Downstream service-c degrades (processing slows from 50ms to 90ms), reducing
  sustainable throughput (88.9 req/s) below incoming 100 RPS open-loop load,
  causing service-b's client-side timeout (100ms) to fire.

baseline:
  load: "100 RPS, 30s, constant-arrival-rate (open-loop)"
  expected: "Healthy service-c (50ms); request rate comfortably under capacity; low P99 (~57ms); 0 retries; 100% success"

expected_failure:
  load: "100 RPS, 45s, constant-arrival-rate (open-loop)"
  symptoms:
    - "Outstanding requests at service-c explode (1760x baseline) as naive retries flood the queue"
    - "P99 at service-a degrades drastically (9.8x baseline) as requests timeout and retry repeatedly"
    - "Thousands of retry attempts generated (13,479 retries) while success rate collapses to <1%"

detection:
  metrics:
    - "Peak outstanding requests (c_requests_outstanding_max) at origin service-c — captures queue amplification before the semaphore"
    - "Processing concurrency (c_processing_concurrency_max) at service-c — verifies semaphore enforcement (never exceeds CAPACITY=8)"
    - "Retry attempts total (b_retry_attempts_total) climbing rapidly without corresponding successful completions"
    - "User-facing P99 at service-a degrading drastically"

mitigation:
  techniques:
    - "Exponential backoff (base 10ms) + random jitter (10ms) — the codebase implements full exponential progression; with the experiment's one-retry cap, the recorded run exercises the first backoff interval"
    - "Bounded retry count per request (max 1 retry)"
    - "A shared retry BUDGET across all concurrent requests (2 tokens per 2000ms window) — strictly caps global retry amplification"
    - "Circuit breaker — opens on a rolling error rate (50% over window of 5) and fails fast (1000ms cooldown), protecting service-c"
    - "In-loop breaker validation — retrying requests check breaker state before and after backoff sleep, preventing retries from punching into open breakers"
  tradeoffs:
    - "The circuit breaker is intentionally simple: time-based open/closed, without a distinct half-open probe state."
    - "Stability vs Throughput Recovery: During continuous downstream capacity degradation, the mitigation achieves recovery by shedding excess load (90.47% direct fast 503 breaker rejections) to preserve latency and origin queue stability, rather than attempting to force 100 RPS through an 88.9 RPS origin."

verification: >
  Enforced criteria in experiment.json:
  1. Failure reproduction requires BREAK peak C outstanding requests >= 5x BASELINE
     and BREAK P99 at A >= 2x BASELINE under identical 100 RPS open-loop load.
  2. Recovery evaluates whether FIX bounds C outstanding requests (<= 2x BASELINE) and
     user-facing P99 (<= 2x BASELINE) under the exact same 100 RPS load and same degraded C.
  3. Success rate, error rate, retry counts, and breaker trips are tracked as
     informational metrics to give full visibility into load-shedding vs throughput recovery.

real_world_examples:
  - "Any outage writeup where a downstream slowdown became a full outage because upstream retries amplified load onto the struggling service"
  - "Classic retry storm incidents (AWS DynamoDB / RDS throttling, payment gateway cascade outages) without retry budgets and circuit breakers"

requires:
  - docker
  - docker-compose

architecture:
  services: [service-a, service-b, service-c, prometheus, grafana, k6]

experiment:
  duration: "~2.5 minutes end to end (baseline + break + fix + verify)"
  load_tool: k6
```

## Load Model & Scientific Comparability

Pattern 02 enforces complete **scientific comparability** across all three stages:

1. **Identical Offered Load**: BASELINE, BREAK, and FIX all run with k6's `constant-arrival-rate` executor configured for the exact same **100.0 requests/second** open-loop load.
2. **VU Pool Sizing**: The experiment provisions sufficient worker VUs (`preAllocatedVUs=200`, `maxVUs=1000`) so that even when requests experience timeout delays, k6 sustains the arrival schedule without dropping iterations. The load generator completed 3,001 iterations in baseline and 4,501 iterations in break and fix.
3. **Single-Variable Control**:
   - **BASELINE $\to$ BREAK**: Exactly one variable changes: downstream `service-c` degrades (processing slows from 50ms to 90ms).
   - **BREAK $\to$ FIX**: Exactly one variable changes: `service-b` enables mitigation (`MITIGATION_ENABLED: false -> true`).

## Stability Recovery vs. Throughput Recovery

It is critical to distinguish what this mitigation accomplishes:

- **What the Mitigation Solves (Stability Recovery)**: Under naive retries, uncoordinated retries flood the degraded downstream origin with 13,479 extra requests, exploding the queue to 14,081 in-flight handlers and degrading P99 latency to 559ms. The mitigation **stabilizes the system**: origin queue depth drops from 14,081 down to 13 (within 2× baseline), and user-facing P99 latency recovers from 559ms down to 106ms (within 2× baseline).
- **Why Success Rate Remains Low (Load Shedding)**: Downstream `service-c` has a physical throughput ceiling of 88.9 req/s during degradation (8 concurrent slots / 90ms). When 100 RPS is offered, no client-side retry policy can force 100 RPS through an 88.9 RPS bottleneck without queueing. The circuit breaker actively **sheds the excess load** (4,072 direct breaker rejections at ~2ms, accounting for 90.47% of total traffic; overall FIX error rate is 93.53%) to prevent catastrophic collapse. Success rate and error rate are therefore reported as informational metrics rather than hard pass/fail criteria.

## Concurrency Model in service-c

`service-c` tracks two separate, explicit metrics:

1. **`c_requests_outstanding_max` (Outstanding Requests):** All HTTP request handlers currently alive in `service-c`, including requests waiting in queue for the semaphore. This is the primary signal of queue explosion during a retry storm.
2. **`c_processing_concurrency_max` (Processing Concurrency):** Only requests that have acquired `asyncio.Semaphore(CAPACITY)` and are actively executing simulated work. This is hard-bounded by `CAPACITY` (8) at all times.

```
    incoming requests (100 RPS)
           │
           ▼
    c_requests_outstanding (+1)  ◄── captures queue explosion (e.g. 14,081 in BREAK)
           │
           ▼
     [ Semaphore: 8 ]            ◄── queue forms here under overload
           │
           ▼
    c_processing_concurrency (+1) ◄── hard-bounded <= CAPACITY (8)
           │
           ▼
     processing work (50ms healthy / 90ms degraded)
           │
           ▼
        Response
```

## Run it

```bash
cd patterns/02-cascading-retry-storm
./run.sh      # 1. BASELINE (100 RPS open-loop, healthy C)
./break.sh    # 2. BREAK + 3. OBSERVE (100 RPS open-loop, degraded C, naive retries)
./fix.sh      # 4. FIX (100 RPS open-loop, degraded C, mitigated with breaker + retry budget)
./verify.sh   # 5. VERIFY (evaluates failure reproduction & recovery)
```

Watch `http://localhost:3001` (Grafana, anonymous viewer access) during the run.

## Real Experimental Evidence

The validated open-loop experiment yields the following measured results:

| Metric | BASELINE (Healthy C) | BREAK (Degraded C + Naive) | FIX (Degraded C + Mitigated) |
|---|---|---|---|
| **Configured Arrival Rate** | 100.0 req/s | 100.0 req/s | 100.0 req/s |
| **Completed Request Rate** | 99.9 req/s | 99.0 req/s | 100.0 req/s |
| **Completed HTTP Requests** | 3,001 | 4,501 | 4,501 |
| **Success Rate** | 100.00% | 0.18% | 6.47% |
| **Total Error Rate** | 0.00% | 99.82% | **93.53%** |
| **A Gateway P99 Latency** | **57 ms** | **559 ms** (9.83× baseline) | **106 ms** (1.87× baseline) |
| **C Outstanding Max** | **8** | **14,081** (1760.1× baseline) | **13** (1.63× baseline) |
| **C Processing Max** | **8** | **8** (at capacity) | **8** (at capacity) |
| **Retry Attempts Total** | **0** | **13,479** | **36** (99.73% reduction) |
| **Circuit Breaker Opens** | 0 | 0 | **102** |
| **Direct Breaker Rejections** | 0 | 0 | **4,072** (90.47% of total requests) |

### Active Mitigation Exercised in FIX
- **Exponential Backoff & Jitter**: The codebase implements full exponential progression; with the experiment's 1-retry cap, the recorded run exercises the first backoff interval ($10\text{ms} + \text{jitter}$) for all 36 retries.
- **Bounded Retry Cap**: Hard-capped to a maximum of 1 retry per request.
- **Shared Global Retry Budget**: Allowed 36 retries globally across the 45-second run (2 tokens per 2000ms window), preventing retry amplification.
- **Circuit Breaker**: Tripped 102 times on rolling 50% error rate over a 5-request window.
- **In-Loop Validation**: Re-checked breaker status before and after backoff sleep, preventing retries from hitting open breakers.
