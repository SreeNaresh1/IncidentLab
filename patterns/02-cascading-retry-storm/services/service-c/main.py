"""
IncidentLab — Pattern 02: Cascading Retry Storm
service-c ("payment-processor") — the origin that gets overloaded.

CHANGE 4: Two clearly separate concurrency metrics.

A. OUTSTANDING REQUESTS  (c_requests_outstanding / c_requests_outstanding_max)
   Counts every HTTP request whose handler is alive — including requests that
   are blocked waiting for semaphore capacity.  This reflects real back-pressure
   visible to the client (service-b): a request is outstanding from the moment
   service-c's /charge handler starts until it returns.

B. PROCESSING CONCURRENCY  (c_processing_concurrency / c_processing_concurrency_max)
   Counts only requests that have acquired the semaphore and are actively
   executing the simulated origin work.  This value is hard-bounded by CAPACITY
   and must never exceed it.

Conceptual model:

    incoming HTTP request
          |
          v
    _outstanding_count += 1          ← A measured here
          |
          v
    await asyncio.Semaphore(CAPACITY)   ← queue forms here when overloaded
          |
          v
    _processing_count += 1           ← B measured here
          |
          v
    await asyncio.sleep(PROCESSING_MS)  ← simulated work
          |
          v
    _processing_count -= 1
          |
          v
    return response
          |
          v
    _outstanding_count -= 1          ← A decremented here

Both current values and historical maxima are tracked in episode_stats (reset
via /admin/reset between stages) and exported as Prometheus gauges.
"""
import asyncio
import os
import time

from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
)

CAPACITY      = int(os.environ.get("CAPACITY",     "10"))
PROCESSING_MS = int(os.environ.get("PROCESSING_MS", "80"))

# Prometheus metrics
REQUESTS_TOTAL = Counter(
    "c_requests_total",
    "Total HTTP requests received by service-c",
)
OUTSTANDING_GAUGE = Gauge(
    "c_requests_outstanding",
    "HTTP requests currently alive in service-c (waiting + processing)",
)
PROCESSING_GAUGE = Gauge(
    "c_processing_concurrency",
    "Requests actively executing inside the semaphore in service-c",
)
LATENCY = Histogram(
    "c_request_duration_seconds",
    "service-c end-to-end processing latency",
)

# Episode-scoped (reset via /admin/reset between stages).
# These track current live values AND historical maxima for the verification
# report — current values are needed for the Prometheus gauges; maxima are
# what verify.py reads from /stats.
episode_stats = {
    "requests_total":              0,
    # A — outstanding (waiting + processing)
    "c_requests_outstanding":      0,
    "c_requests_outstanding_max":  0,
    # B — processing (inside semaphore only; hard-bounded by CAPACITY)
    "c_processing_concurrency":    0,
    "c_processing_concurrency_max": 0,
}

_capacity_semaphore = asyncio.Semaphore(CAPACITY)

app = FastAPI()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats")
async def stats():
    return episode_stats


@app.post("/admin/reset")
async def admin_reset():
    for k in episode_stats:
        episode_stats[k] = 0
    return {"reset": True}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/charge")
async def charge():
    REQUESTS_TOTAL.inc()
    episode_stats["requests_total"] += 1

    # --- A: OUTSTANDING -------------------------------------------------------
    # Increment as soon as the handler starts — before any semaphore wait.
    episode_stats["c_requests_outstanding"] += 1
    if episode_stats["c_requests_outstanding"] > episode_stats["c_requests_outstanding_max"]:
        episode_stats["c_requests_outstanding_max"] = episode_stats["c_requests_outstanding"]
    OUTSTANDING_GAUGE.set(episode_stats["c_requests_outstanding"])

    start = time.perf_counter()
    try:
        async with _capacity_semaphore:
            # --- B: PROCESSING ------------------------------------------------
            # Only incremented after acquiring the semaphore.
            episode_stats["c_processing_concurrency"] += 1
            if episode_stats["c_processing_concurrency"] > episode_stats["c_processing_concurrency_max"]:
                episode_stats["c_processing_concurrency_max"] = episode_stats["c_processing_concurrency"]
            PROCESSING_GAUGE.set(episode_stats["c_processing_concurrency"])

            try:
                await asyncio.sleep(PROCESSING_MS / 1000)
                return {"status": "charged"}
            finally:
                episode_stats["c_processing_concurrency"] -= 1
                PROCESSING_GAUGE.set(episode_stats["c_processing_concurrency"])
    finally:
        LATENCY.observe(time.perf_counter() - start)
        # --- A: decrement outstanding after handler fully completes -----------
        episode_stats["c_requests_outstanding"] -= 1
        OUTSTANDING_GAUGE.set(episode_stats["c_requests_outstanding"])
