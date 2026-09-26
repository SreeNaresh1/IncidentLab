"""
IncidentLab — Pattern 02: Cascading Retry Storm
service-a ("gateway") — the entry point k6 hits. Deliberately dumb: calls
service-b once and returns whatever it gets. No retry logic here on
purpose, so the naive-vs-mitigated variable stays isolated to service-b —
same "one thing changes" discipline as Pattern 01.
"""
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

B_URL = os.environ["B_URL"]
A_TIMEOUT_MS = int(os.environ.get("A_TIMEOUT_MS", "10000"))

A_REQUESTS = Counter("a_requests_total", "Requests service-a received", ["status"])

http_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient()
    yield
    await http_client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/checkout")
async def checkout():
    try:
        resp = await http_client.get(B_URL, timeout=A_TIMEOUT_MS / 1000)
        resp.raise_for_status()
        A_REQUESTS.labels(status="200").inc()
        return resp.json()
    except Exception:
        A_REQUESTS.labels(status="503").inc()
        raise HTTPException(status_code=503, detail="checkout unavailable")
