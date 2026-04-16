# Distributed Systems Practice Project - Checkpoint 3

This checkpoint extends the Checkpoint 2 system with two new distributed features:

- a **replicated books database** (3 replicas, primary-backup, synchronous replication) — see [Replicated database and consistency protocol](#replicated-database-and-consistency-protocol)
- a **distributed commitment protocol (2PC)** across the books database primary and a new payment service — see [Distributed commitment protocol (2PC)](#distributed-commitment-protocol-2pc)

The Checkpoint 2 features (vector clocks, leader election, mutual exclusion) are retained; their documentation has moved further down in this file.

## How to demonstrate that this repository works
This section is intentionally first so it can be used as a short live-demo checklist.

1. Start Docker Desktop, then start the full stack from the repository root.

```powershell
docker compose up --build -d
docker compose ps
```

Expected result: all 13 services are up — the 9 services from Checkpoint 2 (`frontend`, `orchestrator`, 3 backend services, `order_queue`, 3 executor replicas) **plus** the 3 `books_database` replicas and `payment_service`.

2. Run the reusable Checkpoint 3 verification script.

```powershell
.\scripts\checkpoint3-checks.ps1
```

After the first full build, the quicker rerun is:

```powershell
.\scripts\checkpoint3-checks.ps1 -SkipBuild
```

Expected result: all 19 checks pass — docker/compose availability, compose down/up, orchestrator ready, DB all-reachable, DB primary elected, `py-compile` for all new services, 2PC valid-commit, 2PC oversold-abort, cross-replica read convergence, DB primary failover, the participant-failure recovery bonus, and the concurrent-writes bonus. The Checkpoint 2 features (vector clocks, leader election, mutual exclusion) are exercised as part of the CP3 `/checkout` flow; they also still have a dedicated verifier at [scripts/checkpoint2-checks.ps1](scripts/checkpoint2-checks.ps1) if the TA wants to run it separately.

3. If the teaching assistants want a manual happy-path demo, open the frontend at `http://127.0.0.1:8080` and submit a normal order. The REST API is also available at `http://127.0.0.1:8081`.

4. If they want manual API testing, use the prepared payload files in the repo:

```powershell
Invoke-WebRequest `
  -Uri http://127.0.0.1:8081/checkout `
  -Method POST `
  -ContentType "application/json" `
  -Body (Get-Content .\test_checkout.json -Raw)
```

Swap in `test_checkout_fraud.json`, `test_checkout_empty_items.json`, and `test_checkout_terms_false.json` to show rejection paths.

5. Show the logs that prove the distributed behavior:

```powershell
docker compose logs --no-color --tail 200 orchestrator transaction_verification fraud_detection suggestions
docker compose logs --no-color --tail 200 order_queue order_executor_1 order_executor_2 order_executor_3
```

Point out:
- `vc=[...]` in the 3 backend services
- `event=ForwardVC source=...` lines showing services forwarding vector clocks to each other
- `clear_broadcast_sent final_vc=[...]` in the orchestrator
- `action=enqueue` and `action=dequeue` in the queue
- `executing order=` in exactly one executor replica

6. Show the leader-election bonus by either rerunning the script or doing a quick manual failover:

```powershell
docker compose stop order_executor_3
Invoke-WebRequest `
  -Uri http://127.0.0.1:8081/checkout `
  -Method POST `
  -ContentType "application/json" `
  -Body (Get-Content .\test_checkout.json -Raw)
docker compose logs --no-color --since 30s order_queue order_executor_1 order_executor_2 order_executor_3
docker compose up -d order_executor_3
```

Expected result: another executor becomes leader after timeout, dequeues the next approved order, and execution still happens exactly once.

7. Stop the stack when the demo is over.

```powershell
docker compose down
```

## Replicated database and consistency protocol
This section covers the first of the two new Checkpoint 3 features: a replicated key-value store for book stock with a documented consistency protocol. The design note is at [docs/consistency-redesign.md](./docs/consistency-redesign.md).

### What it delivers
- three `books_database` replicas (`books_database_1..3`) running the same image with distinct `REPLICA_ID`s
- bully-style primary election (reusing the pattern from the executor tier)
- **synchronous primary-backup replication** — on every `Write` the primary blocks until every live backup has applied the update, so a `Read` from any replica after a successful write returns the same value
- gRPC interface with `Write`, `Read`, `ReplicateWrite`, `WhoIsPrimary`, the bully RPCs, and the 2PC participant RPCs (`Prepare`/`Commit`/`Abort`); see [utils/pb/books_database/books_database.proto](./utils/pb/books_database/books_database.proto)
- per-replica state volume (`./books_database/state/{1,2,3}` → `/app/state`) for the on-disk staged-transaction log used by the 2PC participant-recovery bonus

### Diagram
![Consistency protocol diagram](./docs/diagrams/consistency-protocol.svg)

### How to observe it in the demo
Convergence check (what the verification script calls): after a successful checkout, `ReadLocal` against each replica must return the same stock value.

```powershell
docker compose logs --no-color --tail 200 books_database_1 books_database_2 books_database_3
```

Point out:
- `became primary` / `new primary is X` — election landed
- `write_committed primary=X title="Book A" seq=N old=A new=B backups_acked=[...]` — primary synchronously replicated before acking
- `replicate_applied from_primary=X title="Book A" seq=N old=A new=B` — each backup applied the same `new` value
- `read_ok title="Book A" value=B` — Read served from the primary

### Concurrent writes (bonus)
Two orders updating the same book at the same time are serialized by **per-key locks** on the primary. Each title has its own `threading.Lock` (created on first access via `get_key_lock(title)`). The lock is held for the full read-validate-write-replicate span of a `Write` or 2PC `Commit`, so two concurrent decrements on "Book A" never observe the same `old` value. Writes on *different* titles proceed in parallel because they acquire different locks. This is the simplest correct strategy: the primary is already the single serialization point, and the per-key granularity avoids a global bottleneck. Verified by [books_database/tests/test_concurrent_writes.py](./books_database/tests/test_concurrent_writes.py).

### Why this satisfies the rubric
- separate database service: ✓ three replicas with a real gRPC interface, not an in-memory mock in the executor
- consistency protocol chosen and documented: ✓ synchronous primary-backup, explained in [docs/consistency-redesign.md](./docs/consistency-redesign.md)
- convergence on all replicas: ✓ shown live by `ReadLocal` against each replica returning the same value after a commit
- concurrent write handling (bonus): ✓ per-key locks on the primary, verified by a dedicated test

## Distributed commitment protocol (2PC)
This section covers the second new Checkpoint 3 feature: a two-phase commit coordinator in the leader `order_executor` that atomically reserves book stock in `books_database` **and** the payment in `payment_service`. The design note is at [docs/commitment-protocol.md](./docs/commitment-protocol.md).

### What it delivers
- 2PC coordinator in [order_executor/src/app.py](./order_executor/src/app.py) (`run_2pc`), running only on the elected executor leader
- two participants — `books_database` primary (stock reservation) and `payment_service` (payment authorization) — with idempotent `Prepare`/`Commit`/`Abort` handlers on each
- a decision record: the coordinator logs `2pc_decision=COMMIT|ABORT participants=[db,payment]` **before** sending phase-2 RPCs, so every round leaves a grep-friendly audit point
- coordinator retry: `Commit` retries with exponential backoff and re-discovers the DB primary between attempts, so a participant that crashed mid-Commit can rejoin and finish the round
- **Phase-6 bonus (participant persistence + recovery).** The DB participant `write-then-rename`s a `txn_<order>.json` before voting commit, reloads it on startup (`recovered_pending`), and refuses to commit an order it has no record of — so a freshly elected replacement primary during the retry window cannot silently mis-commit. Demonstrated end-to-end by [order_executor/tests/test_2pc_crash_recovery.py](./order_executor/tests/test_2pc_crash_recovery.py) and [order_executor/tests/test_2pc_fail_injection.py](./order_executor/tests/test_2pc_fail_injection.py).
- **Option C dev flag.** Setting `CP3_EXECUTION_ONLY=true` on the orchestrator (via [docker-compose.cp3-only.yaml](./docker-compose.cp3-only.yaml)) bypasses the CP2 validation pipeline and sends the order straight to 2PC. Useful for iterating on 2PC in isolation; verified by [orchestrator/tests/test_cp3_execution_only.py](./orchestrator/tests/test_cp3_execution_only.py).

### Diagram
![Commitment protocol diagram](./docs/diagrams/commitment-protocol.svg)

The diagram shows both a COMMIT path (both participants vote commit) and an ABORT path (DB votes abort on insufficient stock).

### How to observe it in the demo
```powershell
docker compose logs --no-color --tail 200 order_executor_1 order_executor_2 order_executor_3 books_database_1 books_database_2 books_database_3 payment_service
```

For a single checkout you should see (on the leader executor and the current DB primary):
- `[PAYMENT] prepare_vote_commit order=<id> user="..." amount=N.NN`
- `[DB-X] prepare_vote_commit order=<id> items=[Book Ax1] persisted=yes`
- `[EXEC-Y] 2pc_votes order=<id> db=(vote_commit=True,msg='ok') payment=(vote_commit=True,msg='ok')`
- `[EXEC-Y] 2pc_decision order=<id> decision=COMMIT participants=[db,payment]`
- `[DB-X] commit_applied order=<id> title="Book A" seq=N old=A new=B backups_acked=[...]`
- `[DB-W] replicate_applied from_primary=X title="Book A" seq=N old=A new=B` (on each backup)
- `[PAYMENT] commit_applied order=<id> user="..." amount=N.NN`
- `[EXEC-Y] 2pc_commit_applied order=<id>`

For a deliberate oversell (requesting more stock than exists), the DB votes abort and the log shows:
- `[DB-X] prepare_vote_abort order=<id> reasons=[...]`
- `[EXEC-Y] 2pc_decision order=<id> decision=ABORT participants=[db,payment]`
- `[DB-X] abort_ok|abort_noop order=<id>` and `[PAYMENT] abort_ok|abort_without_prepare order=<id>`

### Why this satisfies the rubric
- distributed commitment protocol chosen and documented: ✓ 2PC, written up in [docs/commitment-protocol.md](./docs/commitment-protocol.md)
- full trace visible in logs: ✓ key=value lines across coordinator, DB primary, both DB backups, payment
- coordinator-failure analysis (bonus): ✓ §§3–5 of [docs/commitment-protocol.md](./docs/commitment-protocol.md)
- participant persistence + recovery (bonus): ✓ `txn_<order>.json` + `recovered_pending` recovery scan + coordinator retry loop; live demo in the two tests referenced above

## Checkpoint 2 deliverables in this repo
This repository contains the implementation and documentation required for Checkpoint 2:

- vector clocks across `transaction_verification`, `fraud_detection`, and `suggestions`, driven by the services themselves rather than by the orchestrator
- order queuing plus 3 replicated order executors
- leader election and mutual exclusion for queue consumption
- logs that expose vector-clock values, inter-service `ForwardVC` events, queue actions, and executor leadership
- a reusable verification script at `scripts/checkpoint2-checks.ps1`
- the required vector-clocks diagram, leader-election diagram, and system-model write-up

## Vector clocks
The vector clock has 3 positions in the fixed service order `[TV, FD, SUG]`.

### Service-driven event ordering
Event ordering is handled by the microservices, not by the orchestrator. The orchestrator only kicks off the two root events on `transaction_verification` and then blocks on `suggestions` for the final pipeline result. All other events, and all causal dependencies between them, are driven by the backend services through inter-service gRPC calls and vector-clock gating.

Each backend service:
- stores per-order state after `InitOrder`, including a `threading.Lock()` that makes the per-order vector-clock update atomic
- on every event, merges the incoming vector clock with its local vector clock, then increments its own component before logging and replying
- forwards its updated vector clock to the next service over a `ForwardVC` RPC when that event causally precedes work on another service
- gates events that depend on multiple causal predecessors until every predecessor's vector clock has arrived
- clears the order only if `local_vc <= final_vc`

### Event-to-service mapping and causal dependencies
| Event | Service | Depends on | Trigger |
| --- | --- | --- | --- |
| `ValidateUserData` | TV | (root) | orchestrator |
| `ValidateItems` | TV | (root) | orchestrator |
| `CheckUserFraud` | FD | `ValidateUserData` | TV calls FD after `ValidateUserData` |
| `PrecomputeSuggestions` | SUG | `ValidateItems` | TV calls SUG after `ValidateItems` |
| `ValidateCardFormat` | TV | `ValidateItems` | TV chains internally after `ValidateItems`, then `ForwardVC` to FD |
| `CheckCardFraud` | FD | `CheckUserFraud` AND `ValidateCardFormat` | FD fires internally when both predecessor VCs are present, then `ForwardVC` to SUG |
| `FinalizeSuggestions` | SUG | `PrecomputeSuggestions` AND `CheckCardFraud` | SUG fires internally when both predecessor VCs are present |

FD uses `_try_run_e()` to gate `CheckCardFraud` on the presence of both `d_done` (local `CheckUserFraud`) and `c_received` (forwarded VC from TV). SUG uses `_try_run_g()` to gate `FinalizeSuggestions` on both `f_done` (local `PrecomputeSuggestions`) and `e_received` (forwarded VC from FD). These gates are where the vector clocks actually make a decision instead of being inert metadata.

### Diagram
![Vector clocks diagram](./docs/diagrams/vector-clocks.svg)

The orchestrator starts `ValidateItems` and `ValidateUserData` together, so their relative order may swap between runs. The diagram and the table below document one valid run captured from the logs.

### Observed successful event sequence
| Step | Service | Event | Vector clock |
| --- | --- | --- | --- |
| 1 | Transaction verification | `ValidateUserData` | `[1, 0, 0]` |
| 2 | Transaction verification | `ValidateItems` | `[2, 0, 0]` |
| 3 | Fraud detection | `CheckUserFraud` | `[1, 1, 0]` |
| 4 | Suggestions | `PrecomputeSuggestions` | `[2, 0, 1]` |
| 5 | Transaction verification | `ValidateCardFormat` | `[3, 0, 0]` |
| 6 | Fraud detection | `CheckCardFraud` | `[3, 2, 0]` |
| 7 | Suggestions | `FinalizeSuggestions` | `[3, 2, 2]` |

### Failure propagation
When an event fails or a prerequisite cannot complete, the responsible service forwards a failure marker (`ForwardVC` with `success=False`) to every downstream service that would have waited for it. FD and SUG record the failed prerequisite and short-circuit any gated event they would otherwise have run. The orchestrator learns about the failure through `SUG.AwaitPipelineResult()` and returns `"status": "Order Rejected"` with a human-readable reason.

### Final clear
- each service tracks the maximum vector clock it has observed locally
- the orchestrator merges every completed event clock into one `final_vc`
- the orchestrator broadcasts `ClearOrder(final_vc)` to all 3 services
- each service clears only when its local vector clock is not ahead of the final one

### Deep-dive documentation
A complete write-up of the redesign, including the before/after architecture, the inter-service call flow, and the files that were changed, is available at [docs/vector-clock-redesign.md](./docs/vector-clock-redesign.md).

## Leader election and mutual exclusion
The order execution tier uses 3 replicas: `order_executor_1`, `order_executor_2`, and `order_executor_3`.

The implementation follows a bully-style pattern:
- a replica starts an election only if no healthy leader is known
- a replica contacts only higher-numbered peers during election
- the highest live executor becomes leader and announces itself
- the leader sends heartbeats
- followers start a new election if the leader times out
- only the current leader dequeues from `order_queue`

![Leader election diagram](./docs/diagrams/leader-election.svg)

Why this satisfies the checkpoint requirements:
- leader election is visible in logs through `starting election`, `became leader`, and `new leader is ...`
- mutual exclusion is enforced because only the leader calls `Dequeue`
- the failover path is demonstrable with 3 replicas by stopping the current leader and submitting another valid order

## System model
### Architecture
The system is a small distributed online-bookstore workflow:

![Architecture diagram](./docs/diagrams/architecture-diagram.jpg)

- `frontend` serves the browser UI
- `orchestrator` accepts checkout requests over HTTP, initializes the backend services, kicks off the two root events, and blocks on the pipeline result
- `transaction_verification`, `fraud_detection`, and `suggestions` are gRPC services that drive the vector-clock event flow among themselves
- `order_queue` stores approved orders in FIFO order
- `order_executor_1..3` form a replicated execution tier that elects a leader and consumes approved orders

### System flow
The following diagram shows the end-to-end flow of an order through the system:

![System flow diagram](./docs/diagrams/system-flow-diagram.jpg)

### Communication model
- the browser communicates with the orchestrator over HTTP
- the orchestrator communicates with backend services over synchronous gRPC calls, but only for init, two root events, the pipeline result, and the final clear broadcast
- the 3 backend services communicate with each other over gRPC through `ForwardVC` and direct event RPCs; this is where the vector clocks actually flow
- executor replicas communicate with each other over gRPC for election, coordinator announcements, and heartbeats
- the order queue is a separate gRPC service used by the orchestrator and the current leader
- all services run in Docker Compose on one virtual network, but they still behave as separate processes with separate local state

### Concurrency and ordering
- the orchestrator starts the two root validation events in parallel
- there is no global clock
- ordering is captured by vector clocks rather than wall-clock timestamps, and causal delivery is enforced by the services themselves through `ForwardVC` gating
- per-order `threading.Lock()` in every backend service protects the vector-clock read-modify-write against concurrent gRPC threads
- approval requires the full causal dependency chain to complete successfully
- queue consumption is serialized by leadership: only one replica is allowed to dequeue at a time

### Failure assumptions
- the executor layer assumes crash-stop failures, not Byzantine behavior
- a failed leader is detected through missing heartbeats
- after timeout, surviving replicas re-run election and the highest live replica becomes leader
- backend service state for vector clocks is kept in memory per order, so restarting a container loses that in-memory state
- the queue is also in-memory, so queued orders are not durable across queue restarts

### Safety properties
- every order gets a unique `orderId` from the orchestrator
- vector-clock logs expose causal relationships between backend events, including inter-service `ForwardVC` hops
- approved orders are enqueued once by the orchestrator
- only the elected leader dequeues and executes an approved order
- the clear broadcast uses the merged final vector clock so services do not clear too early

### Known limitations
- this list describes the state of the repo **at the Checkpoint 2 snapshot**; the CP3 work below introduces a replicated, partially persistent books database — see "Checkpoint 3 deliverables" earlier in this document
- at the CP2 snapshot there was no persistent database; CP3 adds `kv_store.json` write-then-rename persistence for committed stock on each `books_database` replica
- the queue and vector-clock service caches remain process-local memory only (both in CP2 and in CP3)
- the frontend and orchestrator are single-instance services
- retries and network partitions are not handled beyond the simple crash-stop assumptions needed for this checkpoint

## Logs and verification
The reusable verification script is `scripts/checkpoint2-checks.ps1`.

It checks:
- Docker and Docker Compose availability
- Compose startup
- Python syntax for all backend services
- one valid checkout
- three rejection scenarios
- vector-clock log presence
- queue enqueue and dequeue behavior
- leader failover and executor recovery

Prepared input files:
- `test_checkout.json`
- `test_checkout_fraud.json`
- `test_checkout_empty_items.json`
- `test_checkout_terms_false.json`

The required documentation assets are also available in `docs/`:
- `docs/diagrams/vector-clocks.svg`
- `docs/diagrams/leader-election.svg`
- `docs/diagrams/consistency-protocol.svg` (Checkpoint 3)
- `docs/diagrams/commitment-protocol.svg` (Checkpoint 3)
- `docs/vector-clock-redesign.md`
- `docs/consistency-redesign.md` (Checkpoint 3)
- `docs/commitment-protocol.md` (Checkpoint 3)
- `docs/README.md`
