"""
IncidentLab — Pattern 03: Split-Brain
witness — the third party both nodes can reach even when they can't reach
each other. Provides a lease-based mutual-exclusion claim, not just a
yes/no reachability answer: only one node can hold a valid lease at a
time, and holding it requires periodic renewal.

This is what the naive path skips entirely (it decides leadership from
direct peer reachability) and what the mitigated path uses instead.

Single-process asyncio, no real concurrency inside a single request
handler -- the check-and-set in claim() is atomic with respect to other
requests because there's no `await` between reading and mutating state.
"""
import json
import os
import time

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel

LEASE_TTL_SECONDS = float(os.environ.get("LEASE_TTL_SECONDS", "2.0"))

CLAIMS_GRANTED = Counter("witness_claims_granted_total", "Claims granted", ["node_id"])
CLAIMS_REJECTED = Counter("witness_claims_rejected_total", "Claims rejected", ["node_id"])

state = {"epoch": 0, "leader_id": None, "lease_expires_at": 0.0}
episode_stats = {"claims_granted_total": 0, "claims_rejected_total": 0}

app = FastAPI()


class ClaimRequest(BaseModel):
    node_id: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/stats")
async def stats():
    return {
        **episode_stats,
        "current_epoch": state["epoch"],
        "current_leader": state["leader_id"],
    }


@app.post("/admin/reset")
async def admin_reset():
    state["epoch"] = 0
    state["leader_id"] = None
    state["lease_expires_at"] = 0.0
    for k in episode_stats:
        episode_stats[k] = 0
    return {"reset": True}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/claim")
async def claim(req: ClaimRequest):
    now = time.monotonic()
    no_current_leader = state["leader_id"] is None or now >= state["lease_expires_at"]
    is_renewal = state["leader_id"] == req.node_id and now < state["lease_expires_at"]

    if no_current_leader or is_renewal:
        if no_current_leader:
            state["epoch"] += 1  # a genuinely new claim gets a new epoch; renewal does not
        state["leader_id"] = req.node_id
        state["lease_expires_at"] = now + LEASE_TTL_SECONDS
        CLAIMS_GRANTED.labels(node_id=req.node_id).inc()
        episode_stats["claims_granted_total"] += 1
        return {
            "granted": True,
            "epoch": state["epoch"],
            "lease_expires_in_ms": int(LEASE_TTL_SECONDS * 1000),
        }

    # Someone else holds a valid, unexpired lease.
    CLAIMS_REJECTED.labels(node_id=req.node_id).inc()
    episode_stats["claims_rejected_total"] += 1
    return Response(
        content=json.dumps({
            "granted": False,
            "epoch": state["epoch"],
            "current_leader": state["leader_id"],
        }),
        status_code=409,
        media_type="application/json",
    )
