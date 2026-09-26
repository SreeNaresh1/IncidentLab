# 01 — Thundering Herd

```
Cache expires
      ↓
N concurrent requests miss simultaneously
      ↓
N DB queries compete for a small connection pool
      ↓
requests queue
      ↓
P99 explodes
```

## Architecture

```
                k6
                 │
                 ▼
              FastAPI (app)
             /            \
            ▼              ▼
         Redis          PostgreSQL
            │
            │ /metrics
            ▼
        Prometheus
            │
            ▼
          Grafana
```

## What BREAK actually does, mechanically

```
CACHE MISS (key just expired)
    │
    ├──── request 1 ────► DB
    ├──── request 2 ────► DB
    ├──── request 3 ────► DB
    ├──── request 4 ────► DB
    └──── ... (up to 300 concurrent) ────► DB
```

`get_product_mitigated` collapses that fan-out to one DB request; everyone
else waits on the first request's result instead of issuing their own.

## Schema

```yaml
id: 01-thundering-herd
version: 1.0.0
pattern: thundering-herd
category: cache
difficulty: beginner

mechanism: >
  A cache-aside read pattern with no protection against concurrent misses.
  When a hot key's cache entry expires, every in-flight request for that key
  independently treats it as a cache miss and queries the origin database,
  even though they're all asking for the same thing at the same time.
trigger: >
  A cache entry's TTL expires while many concurrent requests are reading
  that same key.

baseline:
  load: "20 VUs, 30s, constant"
  expected: "Low, steady DB query rate; P99 latency dominated by cache hits"

expected_failure:
  load: "300 VUs, 45s, constant, single hot key"
  symptoms:
    - "DB query rate spikes toward the incoming request rate despite stable application traffic (each request generates at most one origin query, but during a herd, nearly every request does)"
    - "P99 latency explodes as requests queue for a DB connection"
    - "The spike repeats on every TTL cycle, not just once"

detection:
  metrics:
    - "Peak concurrent origin fetches spiking far above baseline — the direct signature. More reliable than aggregate DB query rate, which gets diluted by the much larger volume of cache-hit traffic outside the burst window and can understate or even miss the effect entirely"
    - "DB query rate spiking relative to normal, though this can be a weaker/noisier signal than peak concurrency for the reason above"
    - "Periodic latency spikes matching the cache TTL interval"
    - "Connection pool at or near max_size during misses"

mitigation:
  techniques:
    - "Lease locking — only the first miss for a key fetches from the DB"
    - "Request coalescing — everyone else waits for that fetch's result"
    - "Jittered TTL — spreads re-expiry across time instead of one instant"
  tradeoffs:
    - "Slightly higher latency for requests that arrive during the lock window"
    - "Small added complexity: lock acquisition, timeout, and fallback path"
    - "A crashed lock holder still resolves via the lock's PX expiry (LOCK_TTL_MS), adding up to that much extra wait for waiters"
    - "If the lock holder is itself stuck queued behind a saturated DB pool for longer than LOCK_WAIT_TIMEOUT_MS, waiters give up and fetch directly — which can recreate a smaller stampede. This is a real, demonstrated limitation of lease locking under sustained contention, not a bug: it's why LOCK_WAIT_TIMEOUT_MS and DB_POOL_MAX_SIZE both need tuning together in a real system, not just the cache layer in isolation."

verification: >
  Enforced criteria live in experiment.json in this directory, not here —
  not duplicated in this schema block to avoid the two drifting apart.
  It checks two things, not just one: that BREAK actually reproduced the
  incident (failure_criteria), and that FIX actually recovered it
  (recovery_criteria). A run where BREAK looks basically like BASELINE
  fails verification even if FIX's numbers look great, because it hasn't
  proven anything about the mitigation.

real_world_examples:
  - "Facebook's 2010 memcache stampede writeup (concurrent-miss amplification)"
  - "Any 'cache warming' incident triggered by a deploy that flushes the cache"

requires:
  - docker
  - docker-compose

architecture:
  services: [postgres, redis, app, prometheus, grafana, k6]

experiment:
  duration: "~3 minutes end to end (baseline + break + fix + verify)"
  load_tool: k6
```

## Run it

```bash
cd patterns/01-thundering-herd
./run.sh      # 1. BASELINE
./break.sh    # 2. BREAK + 3. OBSERVE
./fix.sh      # 4. FIX
./verify.sh   # 5. VERIFY
```

Watch `http://localhost:3000` (Grafana, anonymous **viewer** access — no
login needed to watch, but dashboards can't be modified anonymously) while
`break.sh` and `fix.sh` run — request rate, P99 latency, DB queries/sec, and
cache hit ratio update live.

Every stage also prints a text summary, and `./verify.sh` writes the full
picture to `results/RESULT.md`. This pattern has a validated real run on
record in this directory — baseline P99 153ms → break P99 1176ms (7.7x) →
fix P99 151ms (back to baseline), peak concurrent origin fetches 17 → 232
(13.6x) → 1, verdict `PASS`. Rerunning will overwrite it with your
machine's own numbers; the shape should be similar, the exact figures
won't be identical.

`./verify.sh` is the pass/fail gate — it checks two separate things from
`experiment.json`: whether BREAK actually reproduced the incident
(`failure_criteria`) and whether FIX actually recovered it
(`recovery_criteria`). The verdict is one of `PASS`, `FAIL` (reproduced but
didn't recover), or `INVALID (incident not reproduced)` — a BREAK run that
barely differs from BASELINE doesn't validate the mitigation no matter how
good FIX's numbers look, so it's called out separately rather than folded
into a single pass/fail. Exits non-zero unless both criteria groups pass
(usable as a CI check), and writes `results/RESULT.md` — a shareable report
with the baseline/break/fix tables and the verdict.

Each stage also starts from a known-empty cache (`FLUSHDB`) and positions
the first cache-expiry event immediately around the start of load — not
mathematically synchronized to it (Compose/k6 startup still adds some
slack), but reproducible run to run rather than depending on exactly when a
TTL happens to lapse relative to traffic.

## Cleanup

```bash
docker compose down -v
```

## Where to look in the code

- `app/main.py` — `get_product_naive` vs `get_product_mitigated` is the
  entire lesson. Same function signature, same cache, one has a lock.
  `fetch_from_origin` also tracks `origin_requests_max` — the peak number
  of simultaneous in-flight origin fetches during a stage. This turned out
  to be the metric that actually matters for detecting the herd (see
  `tools/lib/verify.py`), not the DB-queries-per-request average, which a
  real run showed can be diluted flat or even inverted by the much larger
  volume of cache-hit traffic outside the burst window.
  `DB_QUERIES` / `db_queries_total` counts *successful* origin queries
  (incremented after the query returns) — a query that errors mid-flight
  isn't counted. Worth knowing if a number looks lower than expected.
- `tools/lib/experiment.sh` — the five-stage protocol as shared shell
  functions (including cache reset and herd synchronization), reused by
  every pattern.
- `tools/lib/verify.py` — turns k6's summary JSON + the app's `/stats`
  snapshot into the failure-reproduction / recovery PASS-FAIL tables and
  `RESULT.md`, driven generically by `experiment.json`'s criteria rather
  than hardcoded thresholds.
- `tools/lib/static_check.py` — a cheap sanity check with no Docker
  dependency: confirms every metric name referenced in `experiment.json`
  is actually returned by `verify.py`'s `_compute_metrics`. Catches a
  typo'd or renamed metric before it fails at runtime.
- `experiment.json` — this pattern's actual enforced criteria, split into
  `failure_criteria` (did BREAK reproduce the incident?) and
  `recovery_criteria` (did FIX recover it?).

## Known limitations

- **No CPU/memory capture yet.** `experiment.json`'s criteria cover
  latency, error rate, and DB load — not container resource usage, which
  would need `docker stats` polling or cAdvisor wired into Prometheus. The
  four metrics here (request rate, P99, DB queries/sec, cache hit ratio)
  are enough signal for this pattern; not adding resource metrics until a
  later pattern actually needs them.
- **The `Schema` block above isn't a validated contract yet** — it's a
  fenced YAML block a human reads, not frontmatter a tool parses. Real
  schema validation (parse README → validate against
  `schema/pattern.schema.yaml` → CI) is worth building once there's a
  second or third pattern to validate against; building a validator
  against a single example risks shaping it around this one pattern's
  quirks rather than the catalog's actual needs.
- **`.github/workflows/e2e.yml` is written but not yet confirmed on
  GitHub Actions.** The experiment itself is now validated — a real local
  Docker run produced the `PASS` on record in `results/RESULT.md` — but
  that's a different environment from the GitHub-hosted runner the
  workflow runs on. Treat its first real CI run as a test of the workflow
  and that environment, not of the lab itself.
