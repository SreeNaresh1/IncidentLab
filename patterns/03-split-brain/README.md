# 03 — Split-Brain

```
Partition severs node-1 <-> node-2
              |
              v
Naive: "I can't reach my peer -> it must be down -> I'm leader now"
              |
              v
   BOTH nodes reach that conclusion independently
              |
              v
   BOTH accept writes -> divergent data
```

## Architecture

```
        k6
       /  \
      v    v
  node-1  node-2   <-- cluster-net: peer channel, THIS is what gets severed
      \    /
       \  /
        v v
     witness        <-- quorum-net: both nodes always reachable here,
                         partitioned or not
```

Two real Docker networks, not one flag:
- **`cluster-net`** — node-1 and node-2 only, each with a network-scoped
  alias (`node-1-peer` / `node-2-peer`) that doesn't exist on the other
  network. This is the *only* network `break.sh` disconnects. The alias
  matters: if node-2 were reachable by any other path once cluster-net is
  severed, the partition wouldn't actually be a partition.
- **`quorum-net`** — both nodes, the witness, k6, Prometheus. Never
  touched by the partition. This is what makes the witness a genuine third
  party rather than a second vote from the same two sides that are
  disagreeing.

## Schema

```yaml
id: 03-split-brain
version: 1.0.0
pattern: split-brain
category: consensus
difficulty: advanced

mechanism: >
  Two nodes decide leadership independently. The naive rule infers
  leadership from direct peer reachability: if I can reach my peer, the
  lower node_id is leader (a healthy pair converges to exactly one,
  deterministically); if I can't reach my peer, I promote myself --
  unconditionally, regardless of whether the peer is actually down. Under
  a real partition this inference is wrong on BOTH sides simultaneously:
  neither node can reach the other, so both conclude the other is dead and
  both promote themselves. The mitigated rule never consults the peer at
  all -- it only claims leadership by winning an atomic lease from a third
  party (the witness) that stays reachable throughout the partition.
trigger: >
  A real network partition on the peer channel (cluster-net), while the
  witness channel (quorum-net) stays intact.

baseline:
  load: "15 writes/s split randomly across both nodes, 20s, full connectivity"
  expected: "Exactly one leader (deterministic tie-break), zero divergence -- checked as an explicit precondition before BREAK runs, not assumed"

expected_failure:
  load: "Same 15 writes/s, 20s, but node-2 disconnected from cluster-net"
  symptoms:
    - "Both nodes report is_leader = true simultaneously"
    - "Divergent keys > 0 -- proof both leaders actually accepted conflicting writes, not just that both believed they could"
    - "The partition is independently confirmed (a direct reachability check from inside node-1), not inferred from its downstream effects"

detection:
  metrics:
    - "leader_count computed by directly querying both nodes' /stats and summing is_leader -- not inferred from write behavior"
    - "divergent_keys computed by diffing both nodes' actual KV dumps -- a key counts as divergent if it's missing on one side or holds different values on both"
    - "A production system would also watch for a node's local leadership belief persisting longer than its last confirmed quorum contact -- the fencing property this pattern's mitigated path enforces on every write, not just at claim time"

mitigation:
  techniques:
    - "Atomic lease claim at the witness -- a check-and-set with real admission control (grant only if no current holder, the caller already holds it, or the current lease expired), not merely 'increment and return,' which two independent callers could both do without ever conflicting"
    - "Per-write fencing -- a node must hold a CURRENTLY valid, witness-confirmed epoch to accept a write, re-checked on every write, not cached from whenever it last successfully claimed"
    - "Fencing tokens on every write (epoch), so downstream conflict resolution has something principled to compare, not just arrival order"
  tradeoffs:
    - "The witness is a new single point of coordination. It is not a single point of failure for CORRECTNESS (if it's unreachable, both nodes correctly refuse to accept writes rather than guessing), but it is a single point of failure for AVAILABILITY -- see fix_availability_rate below."
    - "Lease TTL (2s) trades failover speed against false-positive risk: too short and a slow-but-alive leader gets evicted; too long and a genuinely dead leader's slot stays wasted longer than necessary."

verification: >
  Enforced criteria live in experiment.json, split into distinct checks
  for each causal step rather than one before/after comparison -- BASELINE
  precondition (one leader, zero divergence), BREAK mechanism (partition
  actually confirmed, both nodes actually claim leadership, divergence
  actually occurs), and FIX recovery (one leader under partition, zero
  NEW divergence). A run that skips straight to "did BREAK look bad and
  FIX look good" without checking each link in that chain could pass for
  the wrong reason -- this doesn't.

real_world_examples:
  - "Any leader-election implementation that infers peer death from peer unreachability instead of from losing quorum"
  - "The general shape of most split-brain postmortems: two sides, no fencing token, a network event that looks like death from either side but isn't"

requires:
  - docker
  - docker-compose

architecture:
  services: [witness, node-1, node-2, prometheus, grafana, k6]

experiment:
  duration: "~2 minutes end to end (baseline + break + fix + verify)"
  load_tool: k6
```

## The reconciliation problem, and why FIX doesn't start from a clean slate

Every other stage transition in this repo does a full reset. This one
doesn't, on purpose: `fix.sh` inherits BREAK's actual leftover divergent
KV data instead of wiping it, because the mitigated path is *designed to
prevent* divergence -- there's no organic way to generate real
reconciliation-worthy data from code whose entire job is making sure that
never happens. So `MITIGATION_ENABLED` became a runtime-toggleable flag
(`POST /admin/set-mode`) rather than a container-restart-only setting,
specifically so node data survives the naive-to-mitigated transition.
`/admin/reset` grew a `keep_data` flag for the same reason. This is a
one-time, deliberate exception to the "reset everything between stages"
discipline established in Patterns 01 and 02 -- not a lapse in it.

The reconciliation rule itself (`reconcile.py`) is stricter than "higher
epoch wins": a key only survives if *some* side has a genuinely fenced
(epoch > 0) copy. Two nodes tied at epoch 0 -- which is what every
BREAK-era naive write looks like -- get that key discarded on both sides,
not arbitrarily picked. The absence of a competing value doesn't make an
unfenced value any more trustworthy: if only one side ever wrote a key
under naive rules, that copy is just as unverified as if both sides had
written conflicting ones. Untrusted data can't be reconciled into
something trustworthy; it can only be recognized as loss and removed, so
a client can re-submit it properly under the new regime. Tested directly
against five cases (higher-epoch-wins, tied-at-zero-both-sides,
tied-at-zero-one-side, already-consistent, and the pathological
same-real-epoch-different-value anomaly, which gets flagged rather than
silently resolved) before this ever touched Docker.

## Run it

```bash
cd patterns/03-split-brain
./run.sh      # 1. BASELINE
./break.sh    # 2. BREAK + 3. OBSERVE
./fix.sh      # 4. FIX (reconciliation, then its own partition test)
./verify.sh   # 5. VERIFY
```

Watch `http://localhost:3002` (Grafana, anonymous viewer access) --
leader status per node, write accept/reject rate, and witness claims
granted vs rejected, live. Nodes are on `127.0.0.1:8020` (node-1) /
`:8021` (node-2), witness on `:8022`.

`fix.sh` prints the reconciliation report before running its own load
test -- which keys got pushed, which got discarded, and why. The full
report is also saved to `results/reconcile_report.json`.

`./verify.sh` checks the full causal chain (see the `verification` field
in the schema block above), same `PASS` / `FAIL` / `INVALID (incident not
reproduced)` model as the other two patterns. `fix_availability_rate` --
the fraction of write attempts that succeeded while partitioned -- is
informational only, never a pass/fail gate: a healthy leader under
mitigation should keep succeeding normally, but this pattern's job is
proving correctness (no split-brain), not throughput, and conflating the
two was exactly the mistake Pattern 02 had to walk back after its first
real run.

## Where to look in the code

- `services/node/main.py` -- `leader_check_naive` vs
  `leader_check_mitigated` is the entire lesson, same shape as the other
  two patterns' naive/mitigated pairs. Both write down `state["is_leader"]`;
  the write-acceptance check downstream (`PUT /kv/{key}`) is identical
  code for both modes and never knows which rule decided the answer.
- `services/witness/main.py` -- the atomic lease. `claim()` has no
  `await` between reading and mutating state, which is what makes the
  check-and-set atomic in a single-process asyncio app.
- `reconcile.py` -- the reconciliation rule, stdlib-only, runs on the
  host like the other stage scripts.
- `collect_stats.py` -- snapshots both nodes + witness and computes
  `leader_count` / `divergent_keys` by actually diffing both nodes' KV
  dumps, not by reading a single pre-aggregated field.
- `tools/lib/verify.py` -- `_compute_metrics_split_brain`, registered in
  `METRIC_COMPUTERS` alongside the other two patterns.

## Validated results

Real Docker run completed. All 7 criteria pass. `RESULT.md` has the full
numbers; brief summary:

| Stage | HTTP requests | Rate | P99 | Errors |
|-------|--------------|------|-----|--------|
| Baseline | 301 | 15.0/s | 2 ms | 0.00% |
| Break | 301 | 15.0/s | 2 ms | 0.00% |
| Fix | 301 | 15.0/s | 2 ms | 0.00% |

Failure reproduction — all PASS:
- BASELINE: exactly one leader, zero divergence
- BREAK: partition confirmed, both nodes claiming leadership (`leader_count` = 2), divergent keys = 20

Recovery — all PASS:
- FIX: exactly one leader under partition (witness-mediated), zero new divergence

Informational: `fix_availability_rate` = 51.83% (145/301 writes rejected
by the follower, as expected — correctness, not throughput, is the goal).
`reconcile_keys_discarded` = 20: all BREAK-era naive writes (epoch = 0)
were correctly discarded rather than trusted.

## Known limitations

- **The witness is a real availability dependency**, not just a
  correctness one — see the tradeoffs entry in the schema block above.
  At 51.83% availability under partition, the cost is real. In a
  production system you'd run the witness itself as a replicated service;
  here it's a single container intentionally, to keep the lesson focused
  on the naive-vs-mitigated leader-election decision.
- **Lease TTL (2s) and heartbeat interval (300ms) are tunable.** If a
  rerun shows leadership churning or converging too slowly, those are the
  first knobs. The values held fine in the validated run.
