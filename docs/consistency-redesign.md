# Replicated Books Database: Primary-Backup Consistency

This note documents the consistency protocol introduced in Checkpoint 3
to satisfy the rubric's "distributed database with a consistency
protocol" and "convergence across replicas" requirements.

## 1. Goal and requirement

The Checkpoint 3 guide asks for a replicated database for book stock
with a documented consistency protocol. The read-modify-decrement a 2PC
Commit performs on each approved order must land on **every** replica,
so a Read issued directly against any of the three replicas after a
successful commit returns the same value. This is the convergence check
the verification script runs.

## 2. Choice: synchronous primary-backup

We chose **synchronous primary-backup replication** over chain
replication or quorum reads/writes. The reasoning:

- The order executor already needs a single coordinator for 2PC. Giving
  the database a single primary keeps the system simple: Write and 2PC
  Prepare/Commit/Abort all talk to the same replica.
- Primary-backup lets us reuse the bully election we already built for
  `order_executor`. The three `books_database` replicas run the same
  pattern, so a single mental model covers both tiers.
- Synchronous replication (primary blocks until every live backup has
  applied the write) trades availability for simplicity of reasoning.
  There is never an observable divergence window, so the convergence
  check is a straight equality assertion rather than a bounded-staleness
  one.

Chain replication and quorum systems are richer but neither is
necessary for a 3-replica demo, and both introduce terminology the TA
does not need from a project of this size.

## 3. Protocol summary

| Operation | What the primary does |
|---|---|
| `Write(title, qty)` | Call `ReplicateWrite` on every backup in parallel. If every live backup acks, update `kv_store` locally and log `write_committed backups_acked=[...]`. If any backup is missing, log `write_failed` and return failure without updating `kv_store`. |
| `Read(title)` | Serve from `kv_store` on the primary only. Reads from a backup return `"not primary; primary=X"`. |
| `ReplicateWrite(title, qty, seq)` | On the backup: update `kv_store`, bump local `seq_counter` so ordering is observable, log `replicate_applied`. |

See the sequence diagram at
[docs/diagrams/consistency-protocol.svg](diagrams/consistency-protocol.svg)
for the full happy-path trace.

## 4. Leader election and failover

The primary is chosen by bully election on replica id: the highest live
replica becomes primary and announces itself via `Coordinator(pid)` to
every peer, which each record as their new `leader_id` (log line
`new primary is X`). Heartbeats fire every `HEARTBEAT_INTERVAL` seconds;
a backup that misses `LEADER_TIMEOUT` worth of heartbeats declares the
primary dead (`primary timeout detected`), clears its cached leader,
and starts a new election.

If the primary dies mid-Write, the Write fails on the coordinator side
(the primary's `replicate_to_backups` helper sees the missing ack and
returns `success=False`). The caller (order executor) re-discovers the
primary via `WhoIsPrimary` on any replica and retries. In-flight 2PC
Prepares that were persisted on the old primary are replayed when that
replica is restarted; see
[docs/commitment-protocol.md](commitment-protocol.md) for how
participant recovery interacts with this.

## 5. How 2PC sits on top

2PC Prepare/Commit/Abort RPCs are primary-only, same as Write. The
primary stages items in `pending_orders` during Prepare (and persists
the stage to disk per Phase 6); on Commit it applies the decrement and
immediately synchronously replicates the new value to the backups
before acking the coordinator. So the commit of a 2PC transaction and
the replication of its effect happen inside the same critical section
on the primary: a Read from any replica after `2pc_commit_applied`
observes the post-commit value.

This is why the convergence check is the single strongest proof that
the consistency protocol works: it is an end-to-end assertion that
"whatever 2PC committed is visible on every replica".

## 6. Log lines that prove it

```
[DB-3] became primary
[DB-1] new primary is 3
[DB-2] new primary is 3
[DB-3] write_committed primary=3 title="Book A" seq=42 old=9 new=10 backups_acked=[1, 2]
[DB-1] replicate_applied from_primary=3 title="Book A" seq=42 old=9 new=10
[DB-2] replicate_applied from_primary=3 title="Book A" seq=42 old=9 new=10
[DB-3] read_ok title="Book A" value=10
```

Compare `new` on the primary's `write_committed` line to `new` on each
`replicate_applied` line from the two backups: the three values must be
equal, and the convergence check in
[scripts/checkpoint3-checks.ps1](../scripts/checkpoint3-checks.ps1)
verifies that from the outside by calling `ReadLocal` directly on each
replica.

## 7. Files involved

| File | Role |
|---|---|
| [books_database/src/app.py](../books_database/src/app.py) | `BooksDatabaseService` — primary election, Read/Write/ReplicateWrite, plus the 2PC Prepare/Commit/Abort handlers |
| [utils/pb/books_database/books_database.proto](../utils/pb/books_database/books_database.proto) | gRPC schema for the service |
| [docker-compose.yaml](../docker-compose.yaml) | Three replicas (`books_database_1..3`) with their own state volumes |
| [docs/diagrams/consistency-protocol.svg](diagrams/consistency-protocol.svg) | Sequence diagram (elected primary, Write fan-out, Read path) |

## 8. Known limitations

- **Availability degrades if any backup is down.** Because replication
  is synchronous to every live backup, a slow or dead backup will slow
  down (and eventually fail) Writes on the primary. This is the
  expected cost of strong consistency on a small demo cluster.
- **Split-brain is not prevented.** If the cluster partitions, both
  sides may run elections. Our bully election is not fenced by a
  quorum, so under a partition both halves could briefly believe they
  are primary. The project scope does not ask us to solve this; see
  §9 of the main checkpoint plan ("Known limitations") for the full
  list.
- **`committed_orders` / `aborted_orders` grow unboundedly.** They are
  in-memory sets that exist to make 2PC retry semantics safe (see
  `Commit` handler in
  [books_database/src/app.py](../books_database/src/app.py)). In a
  production system they would be compacted or backed by a real log.
