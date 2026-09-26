"""
IncidentLab — Pattern 01: Thundering Herd

A cache-aside product lookup service with two code paths, switched by the
MITIGATION_ENABLED env var:

  naive     — every concurrent cache miss goes straight to the origin DB.
  mitigated — lease locking + jittered TTL + request coalescing: only one
              request per key fetches from the origin; everyone else waits
              for its result instead of hammering the DB themselves.

Nothing else about the service changes between BREAK and FIX — same code,
same load, one env var.
"""

import asyncio
import os
import random
import time
from contextlib import asynccontextmanager

import asyncpg
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]

MITIGATION_ENABLED = (
    os.environ.get("MITIGATION_ENABLED", "false").lower() == "true"
)

CACHE_TTL_SECONDS = int(
    os.environ.get("CACHE_TTL_SECONDS", "5")
)

CACHE_TTL_JITTER_SECONDS = int(
    os.environ.get("CACHE_TTL_JITTER_SECONDS", "2")
)

DB_QUERY_DELAY_MS = int(
    os.environ.get("DB_QUERY_DELAY_MS", "150")
)

DB_POOL_MAX_SIZE = int(
    os.environ.get("DB_POOL_MAX_SIZE", "10")
)

LOCK_WAIT_TIMEOUT_MS = int(
    os.environ.get("LOCK_WAIT_TIMEOUT_MS", "2000")
)

LOCK_TTL_MS = int(
    os.environ.get("LOCK_TTL_MS", "3000")
)


# ============================================================
# Prometheus metrics
# ============================================================

# HTTP-level metrics
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["endpoint", "status"],
)

HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "Request latency",
    ["endpoint"],
)


# Origin database metrics
DB_QUERIES = Counter(
    "db_queries_total",
    "Total queries successfully sent to the origin database",
)

ORIGIN_REQUESTS_STARTED = Counter(
    "origin_requests_started_total",
    "Total requests entering the origin database fetch path",
)

ORIGIN_REQUESTS_COMPLETED = Counter(
    "origin_requests_completed_total",
    "Total requests completing the origin database fetch path",
)

ORIGIN_REQUESTS_IN_FLIGHT = Gauge(
    "origin_requests_in_flight",
    "Current number of requests inside the origin database fetch path",
)

ORIGIN_REQUESTS_MAX = Gauge(
    "origin_requests_max",
    "Maximum concurrent origin database fetches observed",
)


# Cache metrics
CACHE_HITS = Counter(
    "cache_hits_total",
    "Cache hits",
)

CACHE_MISSES = Counter(
    "cache_misses_total",
    "Cache misses",
)


# Mitigation metrics
LOCK_WAITS = Counter(
    "lock_waits_total",
    "Requests that waited on another in-flight fetch",
)


# ============================================================
# Per-experiment episode statistics
# ============================================================

# Prometheus counters are intentionally cumulative.
# These plain counters are reset between experiment stages by
# POST /admin/reset and are used by run.sh / break.sh / fix.sh.

episode_stats = {
    "requests_total": 0,
    "errors_total": 0,

    "db_queries_total": 0,

    "cache_hits_total": 0,
    "cache_misses_total": 0,

    "lock_waits_total": 0,

    # New mechanism-specific measurements
    "origin_requests_started": 0,
    "origin_requests_completed": 0,
    "origin_requests_max": 0,
}


# ============================================================
# Runtime state
# ============================================================

db_pool: asyncpg.Pool | None = None
redis_client: redis.Redis | None = None

# Number of requests currently inside fetch_from_origin().
origin_in_flight = 0

# Maximum simultaneous origin fetches observed during the
# current application lifetime.
origin_max_in_flight = 0


# ============================================================
# Application lifecycle
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, redis_client

    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=DB_POOL_MAX_SIZE,
    )

    redis_client = redis.from_url(
        REDIS_URL,
        decode_responses=True,
    )

    yield

    await db_pool.close()
    await redis_client.close()


app = FastAPI(lifespan=lifespan)


# ============================================================
# Origin database
# ============================================================

async def fetch_from_origin(product_id: int) -> dict:
    """
    Fetch a product from the origin database.

    This function deliberately tracks concurrency because the
    thundering-herd failure is about many requests entering this
    path simultaneously after a cache miss.

    The DB pool is limited to DB_POOL_MAX_SIZE, so a large number
    of concurrent callers can create pool contention even though
    only DB_POOL_MAX_SIZE queries can execute against PostgreSQL
    simultaneously.
    """

    global origin_in_flight
    global origin_max_in_flight

    # --------------------------------------------------------
    # Enter origin fetch path
    # --------------------------------------------------------

    origin_in_flight += 1

    origin_max_in_flight = max(
        origin_max_in_flight,
        origin_in_flight,
    )

    ORIGIN_REQUESTS_STARTED.inc()

    ORIGIN_REQUESTS_IN_FLIGHT.set(
        origin_in_flight
    )

    ORIGIN_REQUESTS_MAX.set(
        origin_max_in_flight
    )

    episode_stats["origin_requests_started"] += 1

    episode_stats["origin_requests_max"] = max(
        episode_stats["origin_requests_max"],
        origin_in_flight,
    )

    try:
        # ----------------------------------------------------
        # Acquire a DB connection
        # ----------------------------------------------------

        async with db_pool.acquire() as conn:

            # Simulated expensive DB operation
            await conn.execute(
                "SELECT pg_sleep($1)",
                DB_QUERY_DELAY_MS / 1000,
            )

            row = await conn.fetchrow(
                """
                SELECT id, name, price
                FROM products
                WHERE id = $1
                """,
                product_id,
            )

        # ----------------------------------------------------
        # Product not found
        # ----------------------------------------------------

        if row is None:
            raise HTTPException(
                status_code=404,
                detail="product not found",
            )

        # ----------------------------------------------------
        # Successful DB query
        # ----------------------------------------------------

        DB_QUERIES.inc()

        episode_stats["db_queries_total"] += 1

        return {
            "id": row["id"],
            "name": row["name"],
            "price": float(row["price"]),
        }

    finally:
        # ----------------------------------------------------
        # Always leave origin fetch path
        #
        # This executes even if the DB operation raises.
        # ----------------------------------------------------

        origin_in_flight -= 1

        ORIGIN_REQUESTS_COMPLETED.inc()

        ORIGIN_REQUESTS_IN_FLIGHT.set(
            origin_in_flight
        )

        episode_stats["origin_requests_completed"] += 1


# ============================================================
# Naive implementation
# ============================================================

async def get_product_naive(product_id: int) -> dict:
    """
    No protection.

    Every concurrent cache miss independently calls the
    origin database.

    This is the intentionally vulnerable implementation.
    """

    key = f"product:{product_id}"

    # --------------------------------------------------------
    # Try cache
    # --------------------------------------------------------

    cached = await redis_client.get(key)

    if cached is not None:
        CACHE_HITS.inc()

        episode_stats["cache_hits_total"] += 1

        return _deserialize(cached)

    # --------------------------------------------------------
    # Cache miss
    # --------------------------------------------------------

    CACHE_MISSES.inc()

    episode_stats["cache_misses_total"] += 1

    # --------------------------------------------------------
    # Every concurrent miss independently reaches DB
    # --------------------------------------------------------

    product = await fetch_from_origin(product_id)

    # --------------------------------------------------------
    # Populate cache
    # --------------------------------------------------------

    await redis_client.set(
        key,
        _serialize(product),
        ex=CACHE_TTL_SECONDS,
    )

    return product


# ============================================================
# Mitigated implementation
# ============================================================

async def get_product_mitigated(product_id: int) -> dict:
    """
    Lease locking + jittered TTL + request coalescing.

    Only one request per key fetches from the origin.

    Other requests wait for the lock holder to populate the
    cache instead of independently hammering the database.
    """

    key = f"product:{product_id}"

    lock_key = f"lock:product:{product_id}"

    # --------------------------------------------------------
    # Try cache
    # --------------------------------------------------------

    cached = await redis_client.get(key)

    if cached is not None:
        CACHE_HITS.inc()

        episode_stats["cache_hits_total"] += 1

        return _deserialize(cached)

    # --------------------------------------------------------
    # Cache miss
    # --------------------------------------------------------

    CACHE_MISSES.inc()

    episode_stats["cache_misses_total"] += 1

    # --------------------------------------------------------
    # Try to become the lock holder
    # --------------------------------------------------------

    acquired = await redis_client.set(
        lock_key,
        "1",
        nx=True,
        px=LOCK_TTL_MS,
    )

    # ========================================================
    # LOCK HOLDER
    # ========================================================

    if acquired:

        try:
            # Only the lock holder reaches the origin.
            product = await fetch_from_origin(product_id)

            # Add jitter to avoid synchronized future expiry.
            ttl = (
                CACHE_TTL_SECONDS
                + random.randint(
                    0,
                    CACHE_TTL_JITTER_SECONDS,
                )
            )

            await redis_client.set(
                key,
                _serialize(product),
                ex=ttl,
            )

            return product

        finally:
            # Always release the lock.
            await redis_client.delete(lock_key)

    # ========================================================
    # WAITER
    # ========================================================

    LOCK_WAITS.inc()

    episode_stats["lock_waits_total"] += 1

    deadline = (
        time.monotonic()
        + (LOCK_WAIT_TIMEOUT_MS / 1000)
    )

    while time.monotonic() < deadline:

        await asyncio.sleep(0.05)

        cached = await redis_client.get(key)

        if cached is not None:

            CACHE_HITS.inc()

            episode_stats["cache_hits_total"] += 1

            return _deserialize(cached)

    # ========================================================
    # FALLBACK
    # ========================================================

    # If the lock holder failed or took longer than the
    # configured timeout, fetch directly rather than hanging
    # forever.

    return await fetch_from_origin(product_id)


# ============================================================
# Serialization helpers
# ============================================================

def _serialize(product: dict) -> str:
    return (
        f"{product['id']}"
        f"|{product['name']}"
        f"|{product['price']}"
    )


def _deserialize(raw: str) -> dict:
    id_str, name, price = raw.split("|")

    return {
        "id": int(id_str),
        "name": name,
        "price": float(price),
    }


# ============================================================
# Health endpoint
# ============================================================

@app.get("/health")
async def health():
    return {
        "status": "ok"
    }


# ============================================================
# Experiment statistics
# ============================================================

@app.get("/stats")
async def stats():
    """
    Returns the current experiment-stage statistics.

    These values are reset by POST /admin/reset.
    """

    return {
        **episode_stats,

        # Useful for debugging and experiment reports
        "origin_requests_in_flight": origin_in_flight,

        "mitigation_enabled": MITIGATION_ENABLED,
    }


# ============================================================
# Experiment reset
# ============================================================

@app.post("/admin/reset")
async def admin_reset():
    """
    Reset per-stage experiment counters.

    Prometheus counters are intentionally NOT reset.
    """

    global origin_in_flight
    global origin_max_in_flight

    for key in episode_stats:
        episode_stats[key] = 0

    # Reset local concurrency state.
    origin_in_flight = 0
    origin_max_in_flight = 0

    # Reset live Prometheus gauges.
    ORIGIN_REQUESTS_IN_FLIGHT.set(0)
    ORIGIN_REQUESTS_MAX.set(0)

    return {
        "reset": True
    }


# ============================================================
# Prometheus endpoint
# ============================================================

@app.get("/metrics")
async def metrics():
    return Response(
        generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


# ============================================================
# Product endpoint
# ============================================================

@app.get("/product/{product_id}")
async def get_product(product_id: int):

    start = time.perf_counter()

    status = "200"

    try:

        if MITIGATION_ENABLED:
            result = await get_product_mitigated(
                product_id
            )
        else:
            result = await get_product_naive(
                product_id
            )

        episode_stats["requests_total"] += 1

        return result

    except HTTPException as exc:

        status = str(exc.status_code)

        episode_stats["requests_total"] += 1

        episode_stats["errors_total"] += 1

        raise

    except Exception:

        status = "500"

        episode_stats["requests_total"] += 1

        episode_stats["errors_total"] += 1

        raise

    finally:

        HTTP_REQUESTS.labels(
            endpoint="/product",
            status=status,
        ).inc()

        HTTP_LATENCY.labels(
            endpoint="/product"
        ).observe(
            time.perf_counter() - start
        )