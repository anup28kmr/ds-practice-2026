# Checkpoint 3 test plan — commit `95f9e74` (branch `individual-sten-qy-li`)

## 0. What we're validating

Per [Guide12](https://courses.cs.ut.ee/2026/ds/spring/Main/Guide12), CP3 is graded as **10 base + up to 3 bonus** points across:

| # | Rubric item | Points | Where it lives in this repo |
|---|---|---|---|
| R1 | Consistency protocol + database module | 3 | [books_database/](books_database/), [docs/consistency-redesign.md](docs/consistency-redesign.md) |
| R2 | Distributed commitment protocol + new service | 3 | [order_executor/src/app.py](order_executor/src/app.py) `run_2pc`, [payment_service/](payment_service/), [docs/commitment-protocol.md](docs/commitment-protocol.md) |
| R3 | Logging | 1 | All services — `[PAYMENT]`, `[DB-X]`, `[EXEC-Y]`, key=value lines |
| R4 | Project organization, documentation, collaboration | 1 | [README.md](README.md), [docs/](docs/) |
| R5 | Consistency protocol diagram | 1 | [docs/diagrams/consistency-protocol.svg](docs/diagrams/consistency-protocol.svg) |
| R6 | Commitment protocol diagram | 1 | [docs/diagrams/commitment-protocol.svg](docs/diagrams/commitment-protocol.svg) |
| B1 | Concurrent-write handling | up to 3 (shared) | [books_database/tests/test_concurrent_writes.py](books_database/tests/test_concurrent_writes.py) |
| B2 | Failing-participant recovery | " | [order_executor/tests/test_2pc_fail_injection.py](order_executor/tests/test_2pc_fail_injection.py), [order_executor/tests/test_2pc_crash_recovery.py](order_executor/tests/test_2pc_crash_recovery.py) |
| B3 | Coordinator-failure analysis | " | [docs/commitment-protocol.md](docs/commitment-protocol.md) §§3–5 |

Plus the **handoff items**: latest changes committed and a `checkpoint-3` Git tag. The branch is committed; the tag is the last thing left after team-lead merge to `master`.

## 1. Pre-flight (≈5 min)

Run these once before the demo. They surface environmental issues early.

| Step | Command | Pass criterion |
|---|---|---|
| 1.1 Checkout the audited commit | `git fetch && git checkout 95f9e74` | HEAD is `95f9e74` |
| 1.2 Docker Desktop is running | `docker version` | Server section reports a version |
| 1.3 Compose file parses | `docker compose config -q` | Exit 0, no errors |
| 1.4 Required ports free | `Test-NetConnection -Port 8080`, `8081`, `50251–50261` | All free or owned by an old `docker compose` stack we can `down`. **Note:** the gRPC services bind to host ports `50251–50261` (not `50051–50061`) because the team lead's Windows host has Hyper-V port exclusions covering `50000–50159`. Verify with `netsh interface ipv4 show excludedportrange protocol=tcp` — `50251–50261` must not fall inside any excluded range. |
| 1.5 Python 3 available on host | `python --version` | ≥ 3.10 (used by the verifier and bonus tests) |
| 1.6 No host process holds `books_database/state/{1,2,3}` | inspect | empty or only `kv_store.json` / `txn_*.json` from prior runs (the verifier will `down -v` anyway) |

## 2. Smoke build & start (≈5 min) — covers R1, R2 plumbing

| Step | Command | Pass criterion |
|---|---|---|
| 2.1 Cold tear-down | `docker compose down -v` | Exit 0; volumes cleared so `SEED_STOCK` is the starting state |
| 2.2 Build & start the full stack | `docker compose up --build -d` | Exit 0 |
| 2.3 Confirm 13 services are up | `docker compose ps` | Status `running` for: `frontend`, `orchestrator`, `transaction_verification`, `fraud_detection`, `suggestions`, `order_queue`, `order_executor_{1,2,3}`, `books_database_{1,2,3}`, `payment_service` |
| 2.4 Orchestrator is reachable | `curl http://127.0.0.1:8081/` | HTTP 200 |
| 2.5 DB primary elected | `python scripts/_cp3_db_probe.py find-primary` | Exit 0, prints `primary_id=N` for some N∈{1,2,3} |
| 2.6 All DB replicas reachable | `python scripts/_cp3_db_probe.py all-reachable` | Exit 0 |

## 3. Reusable verifier — the main reviewable artifact (≈8 min)

This is the script the README points the TAs at. It is the single most important pass on the day.

```powershell
.\scripts\checkpoint3-checks.ps1 -SkipBuild
```

Pass criterion: prints `Passed: 19  Failed: 0` and exits 0. The 19 checks and which rubric line they cover:

| # | Check | Covers |
|---|---|---|
| 1 | `docker` availability | env |
| 2 | `docker-compose` availability | env |
| 3 | `compose-config` | env |
| 4 | `compose-down` | env |
| 5 | `compose-up` | env |
| 6 | `orchestrator-ready` | env |
| 7 | `db-all-reachable` | R1 |
| 8 | `db-primary-elected` | R1 |
| 9 | `compose-ps` | env |
| 10–13 | `py-compile:*` (4 services) | R4 |
| 14 | `2pc:valid-commit` — happy-path checkout, asserts every coordinator/participant log line, asserts all 3 replicas decremented by 1 | R1, R2, R3 |
| 15 | `2pc:oversold-abort` — `quantity=999`, asserts DB votes abort, EXEC decides ABORT, payment aborts, no DB commit, stock unchanged on all replicas | R2, R3 |
| 16 | `convergence:read-all-replicas` — direct `ReadLocal` on all 3 returns the same value | R1 |
| 17 | `db-failover` — stops current DB primary, asserts new primary is elected, restarts old primary, asserts a new commit still works and replicas converge | R1, B2-adjacent |
| 18 | `bonus:participant-failure-recovery` — runs `test_2pc_fail_injection.py`; coordinator absorbs 2 injected commit failures and the third lands | B2 |
| 19 | `bonus:concurrent-writes` — runs `test_concurrent_writes.py`; per-key locks serialize same-key writes, parallelize different-key writes, all replicas converge | B1 |

If any check fails, **stop here and triage**; the rest of the plan assumes the verifier is green.

## 4. Manual demo flow for the meeting (≈10 min) — covers R3, R4

The verifier proves the system; this section shows the markers a TA wants to *see* on screen.

### 4.1 Happy-path checkout via the frontend (R2, R3)

1. Open `http://127.0.0.1:8080` in the browser, submit one order for "Book A" × 1.
2. Expect HTTP 200, `status: "Order Approved"`, an `orderId`.
3. Tail the logs filtered to the order id and point out, in order:

```powershell
$id = "<orderId from response>"
docker compose logs --no-color --tail 400 orchestrator order_queue order_executor_1 order_executor_2 order_executor_3 books_database_1 books_database_2 books_database_3 payment_service `
  | Select-String $id
```

Markers to point at (from the README §"How to observe it in the demo"):
- `[PAYMENT] prepare_vote_commit order=<id> amount=...`
- `[DB-X] prepare_vote_commit order=<id> persisted=yes`  ← the `persisted=yes` is the participant-recovery hook
- `[EXEC-Y] 2pc_votes order=<id> db=(vote_commit=True,...) payment=(vote_commit=True,...)`
- `[EXEC-Y] 2pc_decision order=<id> decision=COMMIT participants=[db,payment]`
- `[DB-X] commit_applied order=<id> seq=N old=A new=B backups_acked=[...]`
- `[DB-W] replicate_applied from_primary=X seq=N` on each backup
- `[PAYMENT] commit_applied order=<id>`
- `[EXEC-Y] 2pc_commit_applied order=<id>`

### 4.2 Oversell abort via the API (R2, R3)

```powershell
$body = Get-Content .\test_checkout_oversold.json -Raw
$resp = Invoke-WebRequest -Uri http://127.0.0.1:8081/checkout -Method POST -ContentType application/json -Body $body
$id = ($resp.Content | ConvertFrom-Json).orderId
docker compose logs --no-color --since 60s order_executor_1 order_executor_2 order_executor_3 books_database_1 books_database_2 books_database_3 payment_service `
  | Select-String $id
```

Expect:
- `[DB-X] prepare_vote_abort order=<id> reasons=[insufficient_stock ...]`
- `[EXEC-Y] 2pc_decision order=<id> decision=ABORT`
- `[PAYMENT] abort_ok|abort_without_prepare order=<id>`
- No `commit_applied` for the order.
- Direct read on each replica still shows the pre-abort stock value.

### 4.3 CP2 rejection paths still work (R4, evidence the CP2 work didn't regress)

For each, expect HTTP 200 with `status: "Order Rejected"` and *no* enqueue / 2PC log lines for the synthetic order:

```powershell
foreach ($f in "test_checkout_empty_items.json", "test_checkout_terms_false.json", "test_checkout_fraud.json") {
  Invoke-WebRequest -Uri http://127.0.0.1:8081/checkout -Method POST -ContentType application/json -Body (Get-Content $f -Raw)
}
```

### 4.4 Executor leader failover (CP2 carry-over, R4)

```powershell
docker compose stop order_executor_3
Invoke-WebRequest -Uri http://127.0.0.1:8081/checkout -Method POST -ContentType application/json -Body (Get-Content .\test_checkout.json -Raw)
docker compose logs --no-color --since 30s order_queue order_executor_1 order_executor_2 order_executor_3
docker compose up -d order_executor_3
```

Expect the surviving executors to elect a new leader (`became leader` in one of `_1`/`_2`) and the order to be dequeued and 2PC-committed exactly once.

## 5. Targeted bonus drill-down (≈10 min) — covers B1, B2, B3

The verifier already runs B1 and B2, but the team lead will likely want to see them in isolation.

### 5.1 B1 concurrent writes — standalone

```powershell
python books_database/tests/test_concurrent_writes.py
```

Pass criterion: exit 0 and stdout contains `CONCURRENT WRITES TEST: PASSED`. Watch for:
- 5 same-key writes are serialized (no torn `old → new` chains).
- 5 different-key writes proceed in parallel (start times overlap).
- All 3 replicas read the same final value for every key.

### 5.2 B2 fail-injection — standalone

```powershell
python order_executor/tests/test_2pc_fail_injection.py
```

Pass criterion: exit 0, prints `PHASE 6 FAIL-INJECTION E2E: PASSED`, all 3 replicas show `Book A=9` at the end. The interesting log lines (the test script also prints them) are:
- `[DB-3] commit_fail_injected order=<id>` × 2
- `[EXEC-Y] 2pc_commit_retry order=<id>` × 2 then `2pc_commit_retry_succeeded`
- `[DB-3] commit_applied order=<id>` (the third try lands)

### 5.3 B2 crash-recovery — standalone

```powershell
python order_executor/tests/test_2pc_crash_recovery.py
```

This is the strictest participant-recovery test: it `docker kill`s `books_database_3` *between* Prepare and Commit, restarts it without the fail-inject override, and verifies the staged `txn_<id>.json` is reloaded (`recovered_pending order=<id>`) and the retry commit lands. Pass criterion: exit 0; all 3 replicas converge to stock-1; `books_database/state/3/` contains no leftover `txn_*.json`.

### 5.4 B3 coordinator-failure analysis — review only

Open [docs/commitment-protocol.md](docs/commitment-protocol.md) and read §§3–5 with the team lead. There is no executable test; this bonus is graded on the written analysis. Specifically check that the document is internally consistent — the audits in [Golf-Papa-Tango.md](Golf-Papa-Tango.md) flagged earlier inconsistencies about queue redelivery; verify the current text matches what the queue actually does (`Enqueue`/`Dequeue`, no ack, no requeue).

### 5.5 CP3-only fast-path override (optional, supports R4)

This isn't a rubric item but is the cleanest way to demonstrate the 2PC path without CP2 noise:

```powershell
docker compose -f docker-compose.yaml -f docker-compose.cp3-only.yaml up -d --no-deps --force-recreate orchestrator
python orchestrator/tests/test_cp3_execution_only.py
docker compose up -d --no-deps --force-recreate orchestrator   # restore CP2 path
```

## 6. Documentation & diagram review (≈5 min) — covers R4, R5, R6

Open in this order; verify each renders and matches what was demonstrated:

1. [README.md](README.md) §"How to demonstrate that this repository works" — the script call and expected check count (19) match what we just ran.
2. [docs/diagrams/consistency-protocol.svg](docs/diagrams/consistency-protocol.svg) — confirm it shows the 3 replicas, primary/backup roles, `Write` flowing to all backups, `Read` served by primary.
3. [docs/consistency-redesign.md](docs/consistency-redesign.md) — chosen protocol (synchronous primary-backup) and trade-offs are stated.
4. [docs/diagrams/commitment-protocol.svg](docs/diagrams/commitment-protocol.svg) — confirm both COMMIT and ABORT sequences are drawn with the executor as coordinator and DB+payment as participants.
5. [docs/commitment-protocol.md](docs/commitment-protocol.md) — protocol description, decision-record line, and the coordinator-failure analysis (B3).
6. [Golf-Papa-Tango.md](Golf-Papa-Tango.md) — the audit log itself; useful so the team lead can see *why* certain choices were made.

## 7. Known fragile spots — spend extra time here

These come straight out of the audit history in `Golf-Papa-Tango.md`. Each was patched, but each is the most likely thing to wobble during a live demo:

1. **Post-failover leader-stabilization race.** After the `db-failover` check restores the old primary, both `test_concurrent_writes.py` and `test_2pc_fail_injection.py` use a stable-primary gate (2/3 majority + a successful primary-only `Read` + N consecutive stable observations) before driving load. If the verifier hangs at `bonus:concurrent-writes` or `bonus:participant-failure-recovery`, run the standalone test alone — the standalone runs are usually green because the system has settled by then.
2. **DB restart persistence.** The phase-14 fix flushes committed stock to `kv_store.json` via `write-then-rename` before responding to the coordinator's Commit. Verify by: stopping a replica after a few commits, removing the container, `docker compose up -d books_database_X`, and reading the per-replica state files — `cat books_database/state/X/kv_store.json` should show the post-commit values, and the startup log should say `loaded kv_store from disk`.
3. **Queue redelivery is not implemented.** If the team lead asks "what happens if the executor leader dies between `Dequeue` and `2pc_decision`?", the *honest* answer (matching the code) is that the order is lost; B3 in `docs/commitment-protocol.md` discusses this. Don't claim queue redelivery — the audit explicitly flagged that as a stale claim and it has been removed.
4. **`checkpoint-3` Git tag.** Required by Guide12 and not yet present on the remote. Do not tag commit `95f9e74` directly — the team plan is "tag the merge commit on `master` after team-lead review." Track this as the final blocker for submission.

## 8. Tear-down

```powershell
docker compose down -v
```

## 9. Outcome rubric you can mark in real time

| Step | Pass = |
|---|---|
| §2 smoke build | All 13 services running; HTTP 200 from `/`; primary elected |
| §3 verifier | `Passed: 19  Failed: 0` |
| §4.1 happy path | All 8 log markers visible for the order id; replicas converge |
| §4.2 oversell | DB votes abort; no `commit_applied`; stock unchanged |
| §4.3 CP2 rejections | Three `Order Rejected` responses; no enqueue |
| §4.4 leader failover | New executor leader elected; order committed exactly once |
| §5.1 concurrent writes | `CONCURRENT WRITES TEST: PASSED` |
| §5.2 fail-injection | `PHASE 6 FAIL-INJECTION E2E: PASSED` |
| §5.3 crash-recovery | Test exits 0; `recovered_pending` log line present |
| §6 docs | Both diagrams render; protocol docs match observed behavior |

If everything in §9 is green, commit `95f9e74` is **content-ready** for Checkpoint 3. The remaining work is the **`checkpoint-3` Git tag** after merge to `master` and the evaluation slot booking — neither is something this test plan covers.
