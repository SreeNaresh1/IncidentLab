"""
IncidentLab — Pattern 03: Split-Brain
node — identical code for node-1 and node-2, parameterized by NODE_ID /
PEER_ID / PEER_URL / WITNESS_URL. The naive-vs-mitigated split lives in
exactly one place: how `is_leader` gets decided. Everything downstream of
that (the write-acceptance check) is identical code for both modes --
same discipline as Pattern 01's naive/mitigated pair and Pattern 02's
call_c_naive/call_c_mitigated.

naive rule (leader_check_naive):
    Can I reach my peer directly? If no, I assume it's dead and promote
    myself -- regardless of whether that assumption is true. If yes, the
    lower node_id is leader (a simple deterministic tie-break so a healthy
    pair has exactly one leader). This is the actual real-world mistake:
    conflating "I can't reach my peer" with "my peer is down."

mitigated rule (leader_check_mitigated):
    Only claim leadership by successfully claiming a lease from the
    witness. Never consult the peer directly. If the witness is
    unreachable or the claim is rejected, this node is NOT leader --
    full stop, regardless of what it believed a moment ago. This is the
    fencing property: leadership must be reconfirmed continuously, not
    assumed to persist because it held once.
"""
import asyncio
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

NODE_ID = os.environ["NODE_ID"]
PEER_ID = os.environ["PEER_ID"]
PEER_URL = os.environ["PEER_URL"]  # e.g. http://node-2-peer:8000 (cluster-net only)
WITNESS_URL = os.environ["WITNESS_URL"]

HEARTBEAT_INTERVAL_MS = int(os.environ.get("HEARTBEAT_INTERVAL_MS", "300"))
PEER_CHECK_TIMEOUT_MS = int(os.environ.get("PEER_CHECK_TIMEOUT_MS", "500"))
WITNESS_TIMEOUT_MS = int(os.environ.get("WITNESS_TIMEOUT_MS", "500"))

WRITES_ACCEPTED = Counter("node_writes_accepted_total", "Writes accepted", ["node_id"])
WRITES_REJECTED = Counter("node_writes_rejected_total", "Writes rejected (not leader)", ["node_id"])
IS_LEADER_GAUGE = Gauge("node_is_leader", "1 if this node currently believes it is leader", ["node_id"])

kv_store = {}  # key -> {"value": ..., "epoch": int}

state = {
    "is_leader": False,
    "current_epoch": 0,
    "mitigation_enabled": os.environ.get("MITIGATION_ENABLED", "false").lower() == "true",
}
episode_stats = {"writes_accepted_total": 0, "writes_rejected_total": 0}

http_client: httpx.AsyncClient | None = None
_heartbeat_task = None


import socket
from urllib.parse import urlparse, urlunparse

_witness_ip_url = None
_peer_check_lock = asyncio.Lock()


def get_witness_url():
    global _witness_ip_url
    if _witness_ip_url:
        return _witness_ip_url
    try:
        parsed = urlparse(WITNESS_URL)
        ip = socket.gethostbyname(parsed.hostname)
        netloc = f"{ip}:{parsed.port}" if parsed.port else ip
        _witness_ip_url = urlunparse(parsed._replace(netloc=netloc))
        return _witness_ip_url
    except Exception:
        return WITNESS_URL


async def leader_check_naive():
    if _peer_check_lock.locked():
        state["is_leader"] = True
        return

    async with _peer_check_lock:
        try:
            resp = await asyncio.wait_for(
                http_client.get(f"{PEER_URL}/health"),
                timeout=PEER_CHECK_TIMEOUT_MS / 1000.0,
            )
            peer_reachable = resp.status_code == 200
        except Exception:
            peer_reachable = False

        if not peer_reachable:
            # "I can't reach my peer, so it must be down, so I'm leader now."
            # The actual bug: this is also true from the peer's point of view.
            state["is_leader"] = True
        else:
            # Healthy pair: deterministic tie-break, lower id is leader.
            state["is_leader"] = NODE_ID < PEER_ID
        state["current_epoch"] = 0  # naive never obtains a real fencing epoch


async def leader_check_mitigated():
    url = get_witness_url()
    try:
        resp = await asyncio.wait_for(
            http_client.post(
                f"{url}/claim",
                json={"node_id": NODE_ID},
                headers={"Host": urlparse(WITNESS_URL).netloc},
            ),
            timeout=WITNESS_TIMEOUT_MS / 1000.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            state["is_leader"] = True
            state["current_epoch"] = data["epoch"]
        else:
            state["is_leader"] = False
    except Exception:
        # Can't reach the witness -> cannot safely claim or renew leadership.
        # This is the fencing property: no witness contact, no leadership,
        # regardless of what this node believed a moment ago.
        state["is_leader"] = False


async def heartbeat_loop():
    while True:
        try:
            if state["mitigation_enabled"]:
                await leader_check_mitigated()
            else:
                await leader_check_naive()
        except asyncio.CancelledError:
            break
        except Exception:
            state["is_leader"] = False

        try:
            IS_LEADER_GAUGE.labels(node_id=NODE_ID).set(1 if state["is_leader"] else 0)
        except Exception:
            pass

        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL_MS / 1000)
        except asyncio.CancelledError:
            break


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, _heartbeat_task
    http_client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(retries=0))
    get_witness_url()
    _heartbeat_task = asyncio.create_task(heartbeat_loop())
    yield
    if _heartbeat_task:
        _heartbeat_task.cancel()
    await http_client.aclose()


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "node_id": NODE_ID}


@app.get("/stats")
async def stats():
    return {
        **episode_stats,
        "is_leader": state["is_leader"],
        "current_epoch": state["current_epoch"],
        "mitigation_enabled": state["mitigation_enabled"],
        "kv_key_count": len(kv_store),
    }


@app.post("/admin/reset")
async def admin_reset(body: dict = None):
    keep_data = bool((body or {}).get("keep_data", False))
    if not keep_data:
        kv_store.clear()
        state["is_leader"] = False
        state["current_epoch"] = 0
    episode_stats["writes_accepted_total"] = 0
    episode_stats["writes_rejected_total"] = 0
    return {"reset": True, "keep_data": keep_data}


@app.post("/admin/set-mode")
async def set_mode(body: dict):
    state["mitigation_enabled"] = bool(body["mitigation_enabled"])
    return {"mitigation_enabled": state["mitigation_enabled"]}


@app.post("/admin/force-write")
async def force_write(body: dict):
    """
    Bypasses the normal leader check. This is NOT part of the client-write
    path being tested -- it's the repair/reconciliation primitive, used
    only by reconcile.py to push a resolved value after an epoch
    comparison. Clearly separate endpoint, clearly scoped, not a hidden
    backdoor in the mechanism under test.
    """
    key = body["key"]
    if body.get("delete"):
        kv_store.pop(key, None)
        return {"deleted": key}
    kv_store[key] = {"value": body["value"], "epoch": body["epoch"]}
    return {"forced": key, "epoch": body["epoch"]}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/kv/_dump")
async def dump():
    """Full store contents, for divergence measurement and reconciliation."""
    return kv_store


@app.get("/kv/{key}")
async def get_kv(key: str):
    entry = kv_store.get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail="key not found")
    return entry


@app.put("/kv/{key}")
async def put_kv(key: str, body: dict):
    if not state["is_leader"]:
        episode_stats["writes_rejected_total"] += 1
        WRITES_REJECTED.labels(node_id=NODE_ID).inc()
        raise HTTPException(status_code=503, detail="not leader")

    epoch = state["current_epoch"] if state["mitigation_enabled"] else 0
    kv_store[key] = {"value": body["value"], "epoch": epoch}
    episode_stats["writes_accepted_total"] += 1
    WRITES_ACCEPTED.labels(node_id=NODE_ID).inc()
    return {"accepted": True, "epoch": epoch}
