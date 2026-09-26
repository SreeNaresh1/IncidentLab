"""
IncidentLab — Pattern 02: Cascading Retry Storm
service-b ("checkout-service") — where naive vs mitigated differs. Same
split as Pattern 01's get_product_naive/get_product_mitigated: everything
else in the system (service-a, service-c, the load profile) stays fixed.

call_c_naive     — retries immediately on failure, fixed count, no backoff,
                    no jitter, no coordination across concurrent requests.
                    Under overload this is a feedback loop: timeouts trigger
                    retries, retries add load to service-c, more load causes
                    more timeouts.
call_c_mitigated — exponential backoff + jitter, a hard retry cap, a retry
                    BUDGET shared across all concurrent requests (not just
                    per-request), and a circuit breaker that opens on a
                    rolling error rate and fails fast for a cooldown period.

The circuit breaker here is intentionally simple: time-based open/closed,
no distinct half-open probe state. After the cooldown it just goes back to
closed and the next real outcome decides whether it reopens. A fuller
implementation would test recovery with a single trial request before
resuming full traffic — documented as a known simplification, not hidden.
"""
import asyncio
import os
import random
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

C_URL = os.environ["C_URL"]
MITIGATION_ENABLED = os.environ.get("MITIGATION_ENABLED", "false").lower() == "true"

C_TIMEOUT_MS = int(os.environ.get("C_TIMEOUT_MS", "100"))
NAIVE_MAX_RETRIES = int(os.environ.get("NAIVE_MAX_RETRIES", "3"))
MITIGATED_MAX_RETRIES = int(os.environ.get("MITIGATED_MAX_RETRIES", "1"))
BACKOFF_BASE_MS = int(os.environ.get("BACKOFF_BASE_MS", "10"))
BACKOFF_JITTER_MS = int(os.environ.get("BACKOFF_JITTER_MS", "10"))
RETRY_BUDGET_PER_WINDOW = int(os.environ.get("RETRY_BUDGET_PER_WINDOW", "2"))
RETRY_BUDGET_WINDOW_MS = int(os.environ.get("RETRY_BUDGET_WINDOW_MS", "2000"))
BREAKER_ERROR_THRESHOLD = float(os.environ.get("BREAKER_ERROR_THRESHOLD", "0.5"))
BREAKER_WINDOW_SIZE = int(os.environ.get("BREAKER_WINDOW_SIZE", "5"))
BREAKER_COOLDOWN_MS = int(os.environ.get("BREAKER_COOLDOWN_MS", "1000"))

B_REQUESTS = Counter("b_requests_total", "Requests service-b received")
B_RETRIES = Counter("b_retry_attempts_total", "Retry attempts service-b made to service-c")
B_BREAKER_OPENS = Counter("b_circuit_breaker_opens_total", "Times the circuit breaker opened")

episode_stats = {
    "requests_total": 0,
    "c_attempts_total": 0,
    "retry_attempts_total": 0,
    "success_total": 0,
    "failed_total": 0,
    "circuit_breaker_opens_total": 0,
    "rejected_by_breaker_total": 0,
}

# Retry budget: attempts allowed in the current rolling window, shared
# across ALL concurrent requests -- this is what actually caps amplification,
# not the per-request retry count alone.
_budget_window_start = time.monotonic()
_budget_used = 0

# Circuit breaker: rolling window of outcomes (True=success, False=failure).
_breaker_outcomes = []
_breaker_open_until = 0.0  # monotonic time; now < this means open

http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient()
    yield
    await http_client.aclose()


app = FastAPI(lifespan=lifespan)


def _breaker_is_open():
    return time.monotonic() < _breaker_open_until


def _breaker_record(success: bool):
    global _breaker_open_until
    _breaker_outcomes.append(success)
    if len(_breaker_outcomes) > BREAKER_WINDOW_SIZE:
        _breaker_outcomes.pop(0)
    if len(_breaker_outcomes) >= BREAKER_WINDOW_SIZE:
        error_rate = 1 - (sum(_breaker_outcomes) / len(_breaker_outcomes))
        if error_rate >= BREAKER_ERROR_THRESHOLD:
            _breaker_open_until = time.monotonic() + (BREAKER_COOLDOWN_MS / 1000)
            B_BREAKER_OPENS.inc()
            episode_stats["circuit_breaker_opens_total"] += 1
            _breaker_outcomes.clear()


def _take_retry_budget():
    """True if a retry is allowed under the shared budget right now."""
    global _budget_window_start, _budget_used
    now = time.monotonic()
    if (now - _budget_window_start) * 1000 > RETRY_BUDGET_WINDOW_MS:
        _budget_window_start = now
        _budget_used = 0
    if _budget_used >= RETRY_BUDGET_PER_WINDOW:
        return False
    _budget_used += 1
    return True


async def _call_c_once():
    episode_stats["c_attempts_total"] += 1
    resp = await http_client.get(C_URL, timeout=C_TIMEOUT_MS / 1000)
    resp.raise_for_status()
    return resp.json()


async def call_c_naive():
    last_exc = None
    for attempt in range(NAIVE_MAX_RETRIES + 1):
        if attempt > 0:
            B_RETRIES.inc()
            episode_stats["retry_attempts_total"] += 1
        try:
            return await _call_c_once()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            last_exc = exc
            continue  # immediate retry: no backoff, no jitter, no budget check
    raise last_exc


async def call_c_mitigated():
    if _breaker_is_open():
        episode_stats["rejected_by_breaker_total"] += 1
        raise RuntimeError("circuit breaker open")

    last_exc = None
    for attempt in range(MITIGATED_MAX_RETRIES + 1):
        if attempt > 0:
            # If the breaker opened while we were waiting (e.g. concurrent failures),
            # fail fast immediately rather than firing retries into an open breaker.
            if _breaker_is_open():
                episode_stats["rejected_by_breaker_total"] += 1
                break
            if not _take_retry_budget():
                break  # shared budget exhausted -- stop retrying, fail fast
            B_RETRIES.inc()
            episode_stats["retry_attempts_total"] += 1
            delay = (BACKOFF_BASE_MS * (2 ** (attempt - 1)) + random.randint(0, BACKOFF_JITTER_MS)) / 1000
            await asyncio.sleep(delay)
            # Check breaker again after backoff sleep
            if _breaker_is_open():
                episode_stats["rejected_by_breaker_total"] += 1
                break
        try:
            result = await _call_c_once()
            _breaker_record(True)
            return result
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            last_exc = exc
            continue
    _breaker_record(False)
    raise last_exc if last_exc else RuntimeError("retry budget exhausted")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats")
async def stats():
    return {**episode_stats, "mitigation_enabled": MITIGATION_ENABLED}


@app.post("/admin/reset")
async def admin_reset():
    global _breaker_outcomes, _breaker_open_until, _budget_used, _budget_window_start
    for k in episode_stats:
        episode_stats[k] = 0
    _breaker_outcomes = []
    _breaker_open_until = 0.0
    _budget_used = 0
    _budget_window_start = time.monotonic()
    return {"reset": True}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/process")
async def process():
    B_REQUESTS.inc()
    episode_stats["requests_total"] += 1
    try:
        if MITIGATION_ENABLED:
            result = await call_c_mitigated()
        else:
            result = await call_c_naive()
        episode_stats["success_total"] += 1
        return result
    except Exception:
        episode_stats["failed_total"] += 1
        raise HTTPException(status_code=503, detail="upstream unavailable")
