# Sizing and load testing

How much to give Zentral, based on measurements, and how to measure your own
installation.

## Recommendations

| Desk size (agents working at once) | API | Worker | PostgreSQL | Notes |
|---|---|---|---|---|
| Up to ~100 | 1 process, 1 vCPU, 512 MiB | 1 | 2 vCPU, 4 GiB | A single Compose host with 4 vCPU and 8 GiB runs everything |
| ~100–300 | 2 processes / replicas (also for availability) | 1 | 2–4 vCPU, 8 GiB | The Kubernetes base (2 API replicas) |
| More | Add API replicas or `serve --workers`, ~1 per vCPU | 1–2 | Size for connections: ~10 per API process | Measure first (below) |

**Always run at least two API replicas in production** so a restart or a node
drain does not take the desk down; capacity is the second reason.

Disk: plan about **1 KB per ticket** plus **1 KB per message** plus
**0.5 KB per audit event**, and **1.4 × the size of each attachment** (stored
encrypted in the database). Attachments dominate: in the test below they were
three quarters of the database. Keep the same again for one backup.

## What was measured

`scripts/loadtest.py` against PostgreSQL 16 on one machine with **4 vCPUs**
that also ran the database, the worker and the load generator (so all
figures are conservative). The database held 4,800 tickets at the start and
13,200 at the end. Each virtual user loops over a realistic mix with a 0–1 s
pause: queue lists 25 %, webhook ingestion 25 %, opening a ticket 20 %, notes
and updates 15 %, search 5 %, the requester portal 5 %, Insights 3 %,
attachment uploads (200 kB images) 2 %.

A virtual user makes about one request per second — roughly **ten times** a
busy human agent (the web client itself polls once every 30 to 60 seconds).

### One API process

| Virtual users | Requests/s | p50 | p95 | p99 | Errors |
|---:|---:|---:|---:|---:|---:|
| 25 | 47 | 16–37 ms (Insights 364 ms) | 0.2–0.8 s | 0.5–1.2 s | 0 |
| 50 | 64 | 0.1–0.6 s | 0.9–1.5 s | 1.3–1.9 s | 0 |
| 100 | 63 | ~1 s | ~2.2 s | ~2.8 s | 0 |

One process is CPU-bound at about **60 requests/s**. Past that, throughput
stays flat and latency grows — nothing fails. Comfortable operation is up to
about **40 requests/s per process** (p95 under a second), which corresponds to
a few hundred concurrently active agents.

### Three processes on the same 4 vCPUs

| Virtual users | Requests/s | p50 | p95 | Errors |
|---:|---:|---:|---:|---:|
| 100 | 85 | 0.5–0.9 s | 1.1–1.8 s | 0 |
| 200 | 74 | ~2.2 s | ~3.5 s | 0 |

The gain is limited here because the load generator, PostgreSQL and the
worker competed for the same four cores; on separate hosts, expect API
throughput to grow with the number of processes until the database is the
limit. **Every query on the hot paths ran in under 1 ms on its index** (queue
list, audit-chain tail, job queue), and the audit-chain lock — one
transaction-scoped PostgreSQL advisory lock taken only around the commit —
showed no contention: 68,600 audit events written by three processes
concurrently, and `zentral audit verify` confirmed every one.

The only errors in any run were *429 Too Many Requests* on "portal: new
ticket": the portal's per-requester throttle doing its job (the test reuses a
few requester sessions from one address).

### Worker

With the `rules` classifier, one worker processed about **70 jobs/s**
(classification and attachment scans): 1,056 queued jobs in 15 seconds. The
queue only grew during runs that ingested **12–17 new tickets per second**
for minutes on end while the worker shared CPU with everything else —
50,000 tickets an hour, far beyond any service desk. With an on-prem language
model, classification speed is the model server's: measure it with the
model you choose.

### What the load test found and fixed

- Two messages arriving at the same moment from a requester Zentral had not
  seen before could fail one of them (a race on creating the contact). Fixed,
  with a regression test.
- Insights and search decrypted content they did not use (~628,000
  decryptions for 700 requests in a profile). Fixed: Insights reads metadata
  only; search decrypts subjects only, then loads the page it returns. Single
  process throughput rose by about a third.

## Measure your own installation

Load testing needs development sign-in to mint tokens, so run it against a
**development-mode copy with production sizing** (same CPU, memory and
PostgreSQL), never against production:

```bash
zentral serve --port 8800                   # or several, on several ports
zentral worker
python scripts/loadtest.py http://127.0.0.1:8800 --users 25 --duration 120 --seed 5000
# several processes: http://127.0.0.1:8800,http://127.0.0.1:8801
```

`--seed` creates tickets first so lists and search run against realistic
volume. The report gives requests/s and p50/p95/p99 per operation; watch
`jobs` with `status = 'pending'` to see whether the worker keeps up.
`ZENTRAL_RATE_LIMIT_PER_MINUTE` and `ZENTRAL_HOOK_RATE_LIMIT_PER_MINUTE` must
be raised on the test instance, or the limits are what you measure.

## Limits that do not depend on hardware

- **Search** scans the 1,000 most recent tickets visible to you (#14).
- **Insights** reads up to 50,000 tickets of the selected period.
- **Rate limits** are per process and in memory: with *N* processes, a client
  can make *N* times the configured rate before being throttled.
