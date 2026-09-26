# IncidentLab

**Break production patterns safely. Fix them. Prove the recovery.**

```
BREAK IT
   ↓
WATCH IT FAIL
   ↓
UNDERSTAND WHY
   ↓
APPLY THE FIX
   ↓
WATCH IT RECOVER
```

IncidentLab is a set of locally runnable labs that reproduce canonical
distributed-systems failure patterns — not descriptions of them. Each lab is
a small, real system running entirely in Docker Compose, using only the
components needed to reproduce that specific failure — Pattern 01 needs
Postgres and Redis, Pattern 02 doesn't need either. What's constant across
every pattern is the protocol, not the stack. You can genuinely break each
one, observe it under load, fix it, and verify the recovery.

This is different from:
- **Distributed-systems teaching labs** (e.g. DSLabs) — those focus on
  implementing protocols and finding correctness bugs. IncidentLab focuses on
  operational failure patterns and the mitigations you'd actually ship.
- **Chaos-engineering platforms** — those inject faults generically.
  IncidentLab is a curated catalog of named incidents, each with a
  standardized `baseline → break → observe → fix → verify` protocol and a
  measurable pass/fail result.
- **Postmortem link collections** — those are static reading lists. Every
  lab here is something you run, not just read.

## The protocol

Every pattern follows the same five stages, driven by scripts inside the
pattern's own directory:

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  1. BASELINE │ → │   2. BREAK   │ → │  3. OBSERVE  │ → │   4. FIX     │ → │  5. VERIFY   │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
   ./run.sh            ./break.sh       (metrics/Grafana)     ./fix.sh          ./verify.sh
```

Every lab must produce measurable before/after evidence — latency, error
rate, throughput, and system-specific signals — checked against explicit
`failure_criteria` (did BREAK actually reproduce the incident?) and
`recovery_criteria` (did FIX actually recover it?). Not just logs. A lab
that "passes" means the numbers actually recovered, and that there was a
real incident to recover from in the first place.

## Patterns

| # | Pattern | Status |
|---|---------|--------|
| 01 | [Thundering Herd](patterns/01-thundering-herd/README.md) | ✅ validated (real Docker run on record) |
| 02 | [Cascading Retry Storm](patterns/02-cascading-retry-storm/README.md) | ✅ validated (real Docker run on record) |
| 03 | [Split-Brain](patterns/03-split-brain/README.md) | built, not yet run for real |

Deliberately starting with three, built well, before expanding. See
[`schema/pattern.schema.yaml`](schema/pattern.schema.yaml) for the schema
every pattern's README follows, which is what will eventually make patterns
comparable across the catalog.

## Stack

Docker Compose for orchestration, Prometheus + Grafana for live metrics, k6
for reproducible load generation — constant across every pattern. App
services are Python (FastAPI + asyncio) throughout, but which
infrastructure a pattern runs (a database, a cache, N microservices) is
whatever that failure actually needs, not a fixed template copied from
Pattern 01.

## Requirements

The stage scripts run on the host, not just in containers — they need:

- Linux, macOS, or Windows via WSL2 (the scripts are bash; they won't run
  in PowerShell or cmd.exe directly)
- Docker and Docker Compose v2 (`docker compose version`)
- `bash`, `curl`, and `python3` (3.10+, stdlib only — no pip installs
  required) on the host
- ~2GB free RAM per running lab's stack
- Ports vary per pattern (each pattern's README lists its own) so multiple
  labs can run side by side; all services bind to `127.0.0.1` only, never
  all interfaces

## Quick start

```bash
cd patterns/01-thundering-herd   # or any other pattern directory
./run.sh      # 1. BASELINE — bring the stack up, record normal behavior
./break.sh    # 2. BREAK + 3. OBSERVE — inject the failure, watch it degrade
./fix.sh      # 4. FIX — apply the mitigation, replay the same load
./verify.sh   # 5. VERIFY — check failure_criteria and recovery_criteria
```

Each pattern's own README has its Grafana port and any pattern-specific
notes.
