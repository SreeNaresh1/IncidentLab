# IncidentLab — 2-Minute Technical Demo Script

A crisp, repeatable walkthrough designed for live presentations, video recordings, technical interviews, and team demos.

---

## Demo Overview

| Section | Time | What You Show | Key Talking Point |
|---|---|---|---|
| **1. The Problem** | 0:00–0:30 | Terminal & Catalog | "Most engineers encounter distributed systems failures during high-stress 2 AM outages. IncidentLab makes them reproducible locally in Docker." |
| **2. The Baseline** | 0:30–1:00 | `./run.sh` & Grafana | "We establish a live, healthy baseline under 15 writes/sec with exactly one leader." |
| **3. The Break** | 1:00–1:40 | `./break.sh` & Split-Brain | "We sever the peer channel. Both nodes falsely assume the other is dead and claim leadership simultaneously." |
| **4. The Fix** | 1:40–2:20 | `./fix.sh` & Reconciliation | "We introduce a witness-mediated quorum lease and per-write fencing epoch. Unfenced data is discarded, divergence drops to zero." |
| **5. Verification & CI** | 2:20–3:00 | `./verify.sh` & GitHub Actions | "Every run is proven by an automated criteria engine (7/7 checks PASS) and validated across 3 patterns in CI." |

---

## Pre-Flight Checklist

Before presenting:
1. Docker Desktop is running (`docker info`).
2. Terminal opened to `patterns/03-split-brain/` (or run from root).
3. Browser tab open to Grafana: `http://localhost:3002` (anonymous access, no login needed).
4. Browser tab open to GitHub Actions: `https://github.com/SreeNaresh1/ai-developer-toolkit/actions`.

```bash
cd patterns/03-split-brain
docker compose down -v --remove-orphans
```

---

## Step-by-Step Walkthrough

### 1. Introduction (0:00 – 0:30)

> **Speaker:**
> *"Distributed systems failure patterns like split-brain, cascading retry storms, and cache thundering herds are hard to study because you rarely get to observe them safely under real load. IncidentLab provides containerized, deterministic failure laboratories where you break real services, observe the degradation in Grafana, apply architectural mitigations, and verify recovery against strict invariant criteria."*

---

### 2. Baseline — Normal Operation (0:30 – 1:00)

```bash
./run.sh
```

> **Speaker:**
> *"We start with Stage 1: BASELINE. Docker builds and boots a 2-node key-value cluster, a third-party witness, Prometheus, and Grafana. It applies a 20-second open-loop load via k6 at 15 writes per second."*

**Highlight in Terminal:**
```json
{
  "leader_count": 1,
  "divergent_keys": 0,
  "partition_confirmed": 0
}
```
> **Speaker:**
> *"Notice: Node 1 is the deterministic leader. Node 2 rejects writes as a follower. Exactly one leader, zero divergence. Our healthy baseline is locked."*

---

### 3. Break — Injecting Network Partition (1:00 – 1:40)

```bash
./break.sh
```

> **Speaker:**
> *"Now we execute Stage 2: BREAK. This doesn't flip a software toggle; it severs Docker's `incidentlab-03-cluster-net` network using `docker network disconnect`. Node 1 and Node 2 can no longer communicate over their peer channel."*

**Show Grafana (`http://localhost:3002`):**
- Point to panel: **Leader status** showing both `node-1` and `node-2` at `1`.
- Point to panel: **Writes accepted / sec, by node** showing both nodes accepting traffic simultaneously.

**Highlight in Terminal:**
```json
{
  "leader_count": 2,
  "divergent_keys": 20,
  "partition_confirmed": 1
}
```

> **Speaker:**
> *"Here is the bug: under the naive peer-reachability rule, each node asks 'Can I reach my peer?' When the heartbeat times out, both nodes conclude 'my peer must be dead, I will promote myself.' Now both accept writes independently. 20 keys are conflicting and divergent."*

---

### 4. Fix & Reconcile — Witness Quorum & Fencing (1:40 – 2:20)

```bash
./fix.sh
```

> **Speaker:**
> *"In Stage 4: FIX, we apply two mitigations:
> 1. **Witness-mediated atomic leases:** Nodes never infer leadership from peer reachability; they must acquire an atomic lease from an independent witness over a separate network.
> 2. **Per-write fencing tokens:** Every write must validate against the active witness epoch.
>
> Watch what happens to BREAK's divergent data: our reconciliation engine inspects the keys. Because BREAK's writes were unfenced (`epoch: 0`), they cannot be safely trusted and are discarded."*

**Highlight in Terminal:**
```json
{
  "leader_count": 1,
  "divergent_keys": 0,
  "partition_confirmed": 1
}
```

> **Speaker:**
> *"Under the exact same network partition and load, the cluster converges to exactly one leader. Zero new divergence occurs."*

---

### 5. Automated Verification (2:20 – 3:00)

```bash
./verify.sh
```

**Highlight Output:**
```text
Failure reproduction
────────────────────
[PASS] BASELINE precondition: exactly one leader before anything is broken
[PASS] BASELINE precondition: zero divergence before anything is broken
[PASS] BREAK: the partition actually severed peer communication
[PASS] BREAK: both nodes claim leadership (the actual split-brain condition)
[PASS] BREAK: divergent keys > 0 (both leaders actually accepted conflicting writes)

Recovery
────────
[PASS] FIX: exactly one leader while partitioned (witness-mediated quorum)
[PASS] FIX: zero new divergence created during the partition

VERDICT: PASS
```

> **Speaker:**
> *"Finally, Stage 5: VERIFY. `verify.py` evaluates all 7 invariant checks defined in `experiment.json`. It guarantees we didn't just guess recovery — we mathematically verified that the fault occurred and was mitigated.
>
> All 3 patterns in IncidentLab — Thundering Herd, Cascading Retry Storm, and Split-Brain — follow this identical protocol and are fully validated in GitHub Actions CI with 3 out of 3 green builds."*
