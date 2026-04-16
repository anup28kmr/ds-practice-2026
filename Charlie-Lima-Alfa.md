# Charlie-Lima-Alfa: Changes Needed for Checkpoint 3

This document explains what we still need to do in this repository so it is
ready for **Checkpoint 3** (evaluation date 06.05 / 08.05). It is based on:

- the main Projects page: <https://courses.cs.ut.ee/2026/ds/spring/Main/Projects>
- the Checkpoint 3 guide (Session 13): <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide12>
- the Session 10 guide (database + consistency): <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide9>
- the Session 11 guide (distributed commitment): <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide10>

The language is kept simple (B1 level) on purpose, but the technical details
are precise so that any team member can pick the file up and start working.

---

## 1. Where the repository is today

Today the repository is at **Checkpoint 2** state. The top-level
[README.md](README.md) still says "Checkpoint 2". The running services are:

- [frontend/](frontend/)
- [orchestrator/](orchestrator/)
- [transaction_verification/](transaction_verification/)
- [fraud_detection/](fraud_detection/)
- [suggestions/](suggestions/)
- [order_queue/](order_queue/)
- [order_executor/](order_executor/) (3 replicas: `order_executor_1..3`)

Vector clocks, leader election, and mutual exclusion for the queue are already
done and documented. The protobuf folder [utils/pb/](utils/pb/) only contains
stubs for the services above.

There is **no database service** and **no payment service** yet. There is also
**no consistency protocol diagram** and **no commitment protocol diagram** in
[docs/diagrams/](docs/diagrams/). We also do not have a `checkpoint-3` git tag.

There is one more gap we uncovered during cross-review: the current pipeline
collapses the real `items` list into a single `item_count` field, so the
executor has no way to know which books or how many copies to process. §3
describes how to fix this before any 2PC work begins.

So, for Checkpoint 3 we need to add two new distributed features, two new
services, two new diagrams, fix the order data model, and update
documentation and tooling.

---

## 2. What Checkpoint 3 asks from us

The Checkpoint 3 guide gives us **10 base points + up to 3 bonus points**. The
grading breakdown is:

| Item | Points |
| --- | --- |
| Consistency protocol & database module | 3 |
| Distributed Commitment protocol & new service | 3 |
| Logging implementation | 1 |
| Project organization, documentation, collaboration | 1 |
| Consistency Protocol diagram | 1 |
| Distributed Commitment Protocol diagram | 1 |
| Bonus: Consistency protocol session task | +0.75 |
| Bonus: Commitment protocol session task | +0.75 |
| (Further bonus tasks from the session pages) | up to 3 total |

So we must deliver two protocols, one new database tier, one new payment
service, logs that prove the protocols work, and two diagrams.

---

## 3. Prerequisite: extend the order data model so items flow end to end

Before any of the Checkpoint 3 work can actually update stock, we must fix a
real gap in the current pipeline. Today the frontend sends the real items,
but the backend drops them very early:

- [orchestrator/src/app.py](orchestrator/src/app.py) reduces the order to
  `user data`, `credit card data`, `item_count`, and `terms_accepted`.
- [utils/pb/order_queue/order_queue.proto](utils/pb/order_queue/order_queue.proto)
  only stores `item_count`.

This means the executor that dequeues an order has no idea **which** books
or **how many** of each to decrement. The 2PC Prepare described later in
this document cannot work until this is fixed.

### 3.1 Proto change

Add a shared item message and let the order data carry a list of items:

```proto
message OrderItem {
  string title = 1;
  int32 quantity = 2;
}

// inside the queue order message
repeated OrderItem items = ...;
```

### 3.2 Files that have to change

- [frontend/src/index.html](frontend/src/index.html): confirm the checkout
  payload already sends `items` with real titles and quantities; if not,
  extend it.
- [orchestrator/src/app.py](orchestrator/src/app.py): stop collapsing the
  items to `item_count`; pass the full `items` list through to the queue
  enqueue call.
- [utils/pb/order_queue/order_queue.proto](utils/pb/order_queue/order_queue.proto):
  add `repeated OrderItem items` to the enqueue message, then regenerate
  the Python stubs under [utils/pb/order_queue/](utils/pb/order_queue/).
- [order_queue/src/app.py](order_queue/src/app.py): store and return the
  items on dequeue.
- [order_executor/src/app.py](order_executor/src/app.py): read the items
  field of the dequeued order and pass it to the 2PC coordinator logic.

If we choose Option A in §6 (keep the Checkpoint 2 validation pipeline
active), the same `OrderItem` message should also be added to:

- [utils/pb/transaction_verification/](utils/pb/transaction_verification/)
- [utils/pb/fraud_detection/](utils/pb/fraud_detection/)
- [utils/pb/suggestions/](utils/pb/suggestions/)

so that those services do not trip over the new field.

### 3.3 Why this sits before the main work

Without this change, the entire consistency and commitment plan below is
theoretical: the coordinator cannot send meaningful `Prepare` calls because
it does not know the items. Treat this as a **blocking prerequisite**, not
as optional polish.

---

## 4. New work item A: Replicated Books Database

### 4.1 What to build

A new service called, for example, `books_database`, that stores the **book
inventory** as a key-value store. The key is the book title (string), the
value is the current stock quantity (integer). We may also store the price per
book, which is nice for the final demo and costs almost nothing.

The service has to be a **gRPC service** (not HTTP). The minimum API is:

- `Read(key) -> value`
- `Write(key, value) -> ok`

Both calls must be **atomic** on one replica. The database must run as **at
least 3 instances in parallel**. All instances share the same codebase.
Clients (the order executor) should see a single logical database; the fact
that there are many replicas is hidden behind a consistency protocol.

### 4.2 Which consistency protocol

The guide lets us pick, but we must reach **at least sequential consistency**.
The candidates are:

- **Primary-backup** (easiest). One replica is the primary. All writes go to
  the primary, the primary replicates to the backups, then acknowledges. Reads
  can go to the primary (stronger) or to any replica (weaker, but we must be
  careful that reads still return a value not older than the last write the
  client saw).
- **Chain replication**. Writes enter the head of the chain and propagate to
  the tail. The tail answers reads. This is what the lectures 8 and 9 covered.
- **State machine replication (Raft / Paxos)**. Strongest and most interesting
  but also the most code. Only pick this if we really want the full bonus.

**Recommendation:** primary-backup with synchronous replication is the safest
choice. It is enough for sequential consistency and we already have a pattern
for leader election in [order_executor/src/app.py](order_executor/src/app.py).
We can reuse the same bully-style election to elect the primary database
replica if the primary dies.

### 4.3 Tasks to do in the repo

1. Create a new folder `books_database/` next to the other services, with:
   - `Dockerfile`
   - `requirements.txt`
   - `src/app.py` implementing the gRPC server
2. Add a new folder `utils/pb/books_database/` with a `books_database.proto`
   file that defines the service messages (`ReadRequest`, `ReadResponse`,
   `WriteRequest`, `WriteResponse`, plus internal messages like
   `ReplicateWriteRequest` for primary-to-backup replication).
3. Generate the Python stubs next to the proto file (the other services
   already do this pattern).
4. In [docker-compose.yaml](docker-compose.yaml), add three services
   `books_database_1`, `books_database_2`, `books_database_3`. Give each a
   `REPLICA_ID`, a `PEERS` list, and its own port. Reuse the same pattern that
   already exists for the order executor replicas (lines 85-137).
5. **Pre-seed the stock.** At startup, each replica should load a small,
   fixed list of books with quantities. This is enough for the demo.

### 4.4 Bonus: concurrent writes on the same book

The Session 10 page asks us to handle the case where two orders target the
same book at the same time. The primary-backup approach already gives us a
clear single point of serialization (the primary). We can get the bonus by:

- Taking a per-key lock on the primary during Read-Validate-Write, OR
- Using a small version number per key and rejecting a write whose base
  version is stale (optimistic concurrency control).

Pick one and document it. The README must describe which one we chose.

---

## 5. New work item B: Payment Service + Commitment Protocol

### 5.1 What to build

A new service called, for example, `payment_service`, with a gRPC API that
exposes **Prepare**, **Commit**, and **Abort**. It does not need replication
(one instance is enough) and it does not need to really charge a card. It
just needs to log the call and respond.

We also need a **distributed commitment protocol** that coordinates:

- the **order executor** as the **coordinator**, and
- the **books_database primary** and **payment_service** as the **two
  participants**.

We recommend **Two-Phase Commit (2PC)** because it is the simplest protocol
that makes the demo convincing, and the guide explicitly allows it. 3PC is a
bonus we can discuss verbally if asked about blocking behavior.

### 5.2 How 2PC fits into the existing flow

Today, the leader executor just dequeues an order and logs "executing
order=...". For Checkpoint 3, the leader executor must, for each dequeued
order:

1. **Phase 1 (Prepare):**
   - Send `Prepare(orderId, items)` to the database primary.
     The primary reads stock for each requested book and checks there is
     enough. If yes, it writes the tentative decrement into a **pending
     buffer** (not the committed value yet) and replies `VoteCommit`.
   - Send `Prepare(orderId, amount)` to the payment service. It replies
     `VoteCommit` for the demo.
2. **Decision:** if both participants voted commit, the coordinator logs
   `decision=COMMIT` and sends `Commit(orderId)` to both. Otherwise it sends
   `Abort(orderId)` to both.
3. **Phase 2 (Commit/Abort):**
   - The database replaces stock values from the pending buffer into the
     committed state, and also replicates the commit to the backups through
     the chosen consistency protocol.
   - The payment service logs the commit.

The coordinator must also write a log line **before** sending the decision,
so we have a "decision record" visible in `docker compose logs`. This is what
makes 2PC recoverable in theory and what the TA wants to see.

### 5.3 Tasks to do in the repo

1. Create `payment_service/` next to the other services with the same layout
   as the others.
2. Create `utils/pb/payment_service/payment.proto` with `Prepare`, `Commit`,
   `Abort` RPCs.
3. Extend `utils/pb/books_database/books_database.proto` with `Prepare`,
   `Commit`, `Abort` (participant side).
4. Rewrite the "execute order" block in
   [order_executor/src/app.py](order_executor/src/app.py) so that after a
   successful `Dequeue`, the leader runs the 2PC coordinator logic above.
5. Add `payment_service` to [docker-compose.yaml](docker-compose.yaml) with
   its own port. The order executor must `depends_on` it.
6. Keep the existing leader election for the executor. Only the leader runs
   the coordinator role. This is already guaranteed by the mutual-exclusion
   design we use for `Dequeue`.

### 5.4 Bonus: failing participants and coordinator failure

Two separate bonus points are offered:

- **Participant failure (implementation).** Inject a sleep or a forced error
  inside the database participant so it fails after voting commit but before
  applying the commit. The coordinator must retry the `Commit` RPC until the
  participant comes back.

  For the recovery to actually work, the participant must **persist the
  staged transaction to a local file** (for example
  `/app/state/txn_<orderId>.json` or a small on-disk write-ahead log) **at
  the moment it votes commit**. On restart, the participant reloads the
  file and either waits for the coordinator's next `Commit` / `Abort`, or
  asks the coordinator for the outcome. Without on-disk persistence, the
  participant forgets the staged update when its container dies, and the
  coordinator's retries can never succeed. Document this as an experiment
  we can run on demo day.

- **Coordinator failure (analysis only).** In the README or a short section
  of [docs/](docs/), explain what happens when the coordinator dies after
  sending the decision to only one participant. Explain why this is the
  classic "blocking" problem of 2PC, and mention 3PC or a highest-ID
  replacement coordinator as mitigations. No code is required for full
  points, but a few paragraphs are needed.

---

## 6. Wiring the new services into the existing flow

The Session 10 guide says we **may comment out** the validation services
(transaction_verification, fraud_detection, suggestions) in
[docker-compose.yaml](docker-compose.yaml) to keep the demo focused on the
new components. We have three realistic options:

- **Option A (recommended for the final demo).** Keep the three validation
  services **on** but keep their role unchanged. They still run the
  vector-clock pipeline from Checkpoint 2. Only the work **after** approval
  changes: the order executor now runs the 2PC protocol against the database
  and the payment service. This option preserves all the Checkpoint 2
  deliverables and gives the TA the whole picture.
- **Option B.** Comment out the three validation services, as the guide
  allows. This is simpler to demo but means Checkpoint 2 features are no
  longer visible. Only do this if something breaks in the pipeline that we
  cannot fix in time.
- **Option C (dev-time flag).** Keep all the code in place but add an
  environment variable such as `CP3_EXECUTION_ONLY=true` to
  [orchestrator/src/app.py](orchestrator/src/app.py). When the flag is on,
  the orchestrator skips the vector-clock pipeline and enqueues the order
  directly after basic input validation. When the flag is off, the full
  Checkpoint 2 pipeline runs. This avoids deleting code, lets us iterate
  on the 2PC path fast, and keeps the demo flexible — we can flip the flag
  mid-demo to show either mode.

We recommend **Option A for the final demo** and **Option C during
development**, so the team can iterate on 2PC without waiting for the full
pipeline on every run. The main code change is then very local to
[order_executor/src/app.py](order_executor/src/app.py) plus the two new
services, plus the prerequisite from §3.

---

## 7. Logging

The Checkpoint 3 guide awards 1 point for **logging of component
interactions**. We already log vector clocks, queue actions, and leader
election in Checkpoint 2. For Checkpoint 3 we must add at least:

- **Database replication:** log every write on the primary, every
  `ReplicateWrite` call to a backup, and every apply on a backup, with the
  key, the old value, and the new value.
- **Consistency protocol:** log "primary=X" on startup and on failover, plus
  every Read/Write the executor does.
- **Payment service:** one log line per Prepare/Commit/Abort.
- **2PC coordinator:** one log line per phase, including `decision=COMMIT`
  or `decision=ABORT`, and the list of participants and their votes.
- **2PC participants:** one log line per Prepare/Commit/Abort they receive,
  including their vote.

All logs should follow the existing key=value style used in
[orchestrator/src/app.py](orchestrator/src/app.py) so the patterns we already
grep for in [scripts/checkpoint2-checks.ps1](scripts/checkpoint2-checks.ps1)
stay consistent.

---

## 8. Diagrams

Two new diagrams are required. Both go into [docs/diagrams/](docs/diagrams/).

1. **Consistency protocol diagram** — shows:
   - the three database replicas with clear labels (primary vs backup, or
     head / middle / tail if we pick chain replication),
   - the executor client,
   - Read and Write arrows with the direction of replication.
   File name suggestion: `docs/diagrams/consistency-protocol.svg`.
2. **Distributed commitment protocol diagram** — shows:
   - the executor as coordinator,
   - the database primary and the payment service as participants,
   - the two phases (Prepare / Vote, then Commit or Abort) with arrows,
   - one success path and, ideally, one failure path.
   The guide explicitly allows 2-3 pictures in a sequence. File name
   suggestion: `docs/diagrams/commitment-protocol.svg`.

Both diagrams should be referenced from the updated README in the same way
the existing diagrams are referenced (see lines 115 and 153 in the current
[README.md](README.md)).

---

## 9. Documentation updates

- **[README.md](README.md):** rename the top header to "Checkpoint 3", and
  add two new top-level sections: "Replicated database and consistency
  protocol" and "Distributed commitment protocol (2PC)". Each section should
  explain:
  - what the section delivers and why it satisfies the rubric,
  - how to run and observe it in the demo,
  - which log lines prove it works.
- **[docs/](docs/):** add a short design note, for example
  `docs/consistency-redesign.md` and `docs/commitment-protocol.md`, similar
  in style to the existing [docs/vector-clock-redesign.md](docs/vector-clock-redesign.md).
  Keep them short: one page each is enough.
- **In-code comments and docstrings.** The Checkpoint 3 guide explicitly
  asks us to document the crucial code sections. Short comments or
  docstrings are required in, at a minimum:
  - the 2PC coordinator block in
    [order_executor/src/app.py](order_executor/src/app.py) (one block
    comment explaining the phase sequence and what happens on timeout),
  - the `Prepare` / `Commit` / `Abort` handlers in the new
    `books_database` service (one docstring each, explaining how the
    pending buffer works and when replication happens),
  - the `Prepare` / `Commit` / `Abort` handlers in the new
    `payment_service`,
  - any retry or recovery helper used by the coordinator or participants.

  These comments are small but they are a named deliverable of the 1-point
  "project organization, documentation, collaboration" rubric item,
  alongside the README and the design notes.

---

## 10. Verification and demo support

The Checkpoint 2 demo script is
[scripts/checkpoint2-checks.ps1](scripts/checkpoint2-checks.ps1). We should
create `scripts/checkpoint3-checks.ps1` that:

1. runs `docker compose up --build -d` and confirms all containers are up
   (now including 3 database replicas and 1 payment service),
2. submits a valid checkout and confirms the order is committed in the
   database (stock decremented on all 3 replicas),
3. submits a checkout that would oversell a book and confirms the protocol
   aborts cleanly (stock unchanged on all replicas, payment service logged
   Abort),
4. kills the database primary and confirms a new primary takes over and
   the system still commits a new order,
5. **read-consistency convergence check** — after a successful commit,
   reads issued directly against each of the 3 database replicas return
   the **same** final stock value. This is the single most direct proof
   that the consistency protocol actually works and that all replicas
   converge,
6. (bonus) forces the database participant to fail after voting commit and
   checks that the coordinator retries until the participant recovers.

New test payload files should live next to the Checkpoint 2 files:
- `test_checkout_oversold.json` for the abort case,
- `test_checkout_concurrent_a.json` and `test_checkout_concurrent_b.json`
  for the concurrent-writes bonus.

---

## 11. Release steps (the day before the demo)

1. Merge all work into `master`.
2. Run `scripts/checkpoint3-checks.ps1` end to end. All checks must pass.
3. Commit the final state and create the git tag:
   ```bash
   git tag checkpoint-3
   git push origin checkpoint-3
   ```
4. Book the evaluation slot in the Google Sheet linked from
   <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide12>.
5. Prepare one laptop with:
   - Docker Desktop running,
   - the repo at tag `checkpoint-3`,
   - both diagrams open as images,
   - the README open at the "How to demonstrate" section.

---

## 12. Checklist (to track progress)

- [x] Order data model carries `items` (title + quantity) end to end
- [x] Orchestrator stops collapsing the order to `item_count`
- [x] Queue proto extended with `repeated OrderItem items` and stubs regenerated
- [x] `books_database` service with gRPC Read/Write
- [x] 3 database replicas in `docker-compose.yaml`
- [x] Consistency protocol implemented (primary-backup recommended)
- [x] Primary failover reuses bully election pattern
- [x] `payment_service` with Prepare/Commit/Abort
- [x] 2PC coordinator logic inside `order_executor`
- [x] Database participant supports Prepare/Commit/Abort
- [x] `CP3_EXECUTION_ONLY` dev-time flag in orchestrator
- [x] Logs updated for replication, consistency, payment, 2PC
- [x] In-code docstrings added to 2PC coordinator, DB participant, payment participant, recovery helpers
- [x] `docs/diagrams/consistency-protocol.svg` added
- [x] `docs/diagrams/commitment-protocol.svg` added
- [x] README updated to Checkpoint 3
- [x] `docs/consistency-redesign.md` and `docs/commitment-protocol.md` added
- [x] `scripts/checkpoint3-checks.ps1` passes end to end (including replica-convergence check)
- [x] New test payload files added
- [ ] Git tag `checkpoint-3` created and pushed
- [x] Bonus: concurrent-writes strategy documented
- [x] Bonus: participant persists staged transaction to disk before voting commit
- [x] Bonus: participant-failure recovery demonstrated
- [x] Bonus: coordinator-failure analysis written
- [ ] Evaluation slot booked in the Google Sheet

---

## 13. Team plan for Checkpoint 3 (who does what)

The work in this document will be delivered in three steps by two roles:
this branch's author (me) and the team lead (another team member).

### Step 1 — Implementation on this branch (me)

On the current feature branch `individual-sten-qy-li`, implement every
Checkpoint 3 requirement described in §§3–10 of this document, **except**
creating the `checkpoint-3` git tag. Concretely, Step 1 covers:

- §3 order data model prerequisite,
- §4 replicated `books_database` service and consistency protocol,
- §5 `payment_service` and 2PC coordinator logic,
- §6 wiring options (Option A for the demo, Option C dev flag during work),
- §7 logging additions,
- §8 diagrams,
- §9 documentation and in-code docstrings,
- §10 verification script,
- the three bonus items from §4.4 and §5.4.

### Step 2 — Push to GitHub (me)

Once all checklist items from §12 are ticked (except "Git tag `checkpoint-3`
created and pushed", which is owned by Step 3), commit the final state on
`individual-sten-qy-li` and push the branch to the GitHub remote:

```bash
git push origin individual-sten-qy-li
```

No `master` merge and no tag are created in this step. The branch is left
in a "ready for review" state.

### Step 3 — Merge and tag (team lead, not me)

The team lead will:

1. merge `individual-sten-qy-li` into `master` (typically via a reviewed
   pull request),
2. create the `checkpoint-3` git tag on **the merge commit on `master`**,
   not on any commit that lives only on the feature branch,
3. push the tag:

   ```bash
   git tag checkpoint-3 <merge-commit-sha>
   git push origin checkpoint-3
   ```

This is the hand-off point. After Step 3, the repository is officially at
Checkpoint 3 state.

### How this supersedes §11

§11 step 3 (run `git tag checkpoint-3` and push) is intentionally **not**
done on this branch. For our team, that step belongs to the team lead on
`master` after the merge. The rest of §11 (final local checks, booking the
evaluation slot, preparing the demo laptop) is still valid and is shared
between me and the team lead as applicable.

---

## 14. Execution phases (implementation order for Step 1 of §13)

§§3–12 describe **what** to deliver. §13 Step 1 is the big "do all of it"
bucket. This section breaks Step 1 into **13 ordered phases** so progress
is trackable across sessions and the team lead can see at a glance where
we are. Each phase is chosen so the repository ends the phase in a
runnable state (the stack still starts and the previous features still
work), which keeps the blast radius of any single mistake small.

### Phase table

| # | Phase | Covers | Ends when |
| --- | --- | --- | --- |
| 1 | Order data model prerequisite | §3 | `docker compose up` still works; a checkout carries real `items` (title + quantity) end to end, visible in both orchestrator and executor logs |
| 2 | `books_database` service (core) | §4.1–4.3 | 3 replicas running; direct gRPC `Read`/`Write` against the primary works; pre-seeded stock is visible |
| 3 | Concurrent-writes bonus | §4.4 | Two simultaneous writes on the same book serialize cleanly (per-key lock on primary, or versioned OCC) |
| 4 | Payment service + DB participant RPCs | §5.1, §5.3 steps 1–3 | `payment_service` container up; both payment and DB answer direct `Prepare` / `Commit` / `Abort` calls |
| 5 | 2PC coordinator in executor | §5.2, §5.3 steps 4–6 | End-to-end successful order decrements stock on all 3 database replicas; `decision=COMMIT` log line present |
| 6 | Participant-failure bonus (on-disk staged txn) | §5.4 item 1 | Forced DB-participant crash after vote recovers on restart; coordinator retries succeed |
| 7 | Coordinator-failure analysis | §5.4 item 2 | Short analysis in `docs/commitment-protocol.md` covering 2PC blocking + mitigations |
| 8 | Wiring + `CP3_EXECUTION_ONLY` dev flag | §6 | Both Option A (full CP2+CP3 pipeline) and Option C (flag on) run end to end |
| 9 | Logging sweep | §7 | `docker compose logs` shows the full 2PC trace using the existing key=value style |
| 10 | Diagrams | §8 | `docs/diagrams/consistency-protocol.svg` and `docs/diagrams/commitment-protocol.svg` present, referenced from README |
| 11 | Documentation | §9 | README renamed to CP3; `docs/consistency-redesign.md`, `docs/commitment-protocol.md`, and the in-code docstrings added |
| 12 | Verification script | §10 | `scripts/checkpoint3-checks.ps1` passes end to end, including the replica-convergence check |
| 13 | Final commit + push | §13 Step 2 | Clean commit on `individual-sten-qy-li` pushed to `origin`; no tag created (that is §13 Step 3, team lead's) |

### Phase progress log

This log is updated at the end of each phase. Keep entries short so the
section stays scannable.

- [x] Phase 1 completed. End-to-end proof: orchestrator log
  `received_checkout ... item_count=2 items=[Book Ax3,Book Bx1]` and
  executor log `executing order=... items=[Book Ax3,Book Bx1]`. Four
  protos extended with `OrderItem` + `repeated OrderItem items = 9;`,
  stubs regenerated, orchestrator propagates items, executor logs them,
  frontend updated to send `title`. CP2 vector-clock pipeline still
  reaches `final_status=APPROVED`.
- [x] Phase 2 completed. Three replicas up with bully election (DB-3 primary;
  DB-1 and DB-2 acknowledge). Client-facing `Read` and `Write` on the
  primary work; pre-seeded stock visible. `Write(Book A, 7)` committed with
  `seq=1`, replicated to DB-1 and DB-2 (both logged `replicate_applied`
  old=10 new=7). Non-primary `Write` correctly rejected with
  `not primary; primary=3`. New proto `books_database.proto`, new
  `books_database/` service folder, three replicas added to
  `docker-compose.yaml` on host ports 50058–50060.
- [x] Phase 3 completed. Per-key locks in `books_database/src/app.py`
  (`kv_state_lock` meta-lock + `key_locks` dict + `get_key_lock(title)` +
  dedicated `seq_lock` for the monotonic sequence). Test A (10 concurrent
  writes on `Book A`, quantities 100..109): final=104 (one of the attempted
  values), 10 distinct seqs 1..10 on primary with each `old` matching the
  previous `new`, both DB-1 and DB-2 applied 1..10 in order. Test B
  (5 concurrent writes on 5 different titles): wall-clock 0.04s vs 0.07s
  for 5 same-key writes — different keys fan out to backups in parallel
  while same-key serializes. No torn state.
- [x] Phase 4 completed. New `payment_service/` runtime (single-instance gRPC
  server on port 50061, always votes commit, idempotent on retries). New
  `utils/pb/payment_service/payment.proto` with `Prepare` / `Commit` /
  `Abort`. `books_database.proto` extended with the same three RPCs plus a
  `ReadLocal` debug RPC for the replica-convergence check. Primary-side
  `Prepare` stages reservations in `pending_orders` and rejects votes when
  stock (minus existing reservations) is insufficient; `Commit` applies each
  decrement through the existing synchronous-replication path; `Abort`
  drops the reservation. `docker-compose.yaml` now starts `payment_service`
  and the executor `depends_on` it and the three DB replicas. Smoke tests
  pass: payment round-trip, DB prepare+commit with cross-replica
  convergence (Book A 10→8 and Book B 6→5 on DB-1/2/3), abort leaves stock
  unchanged, huge request vote-aborts with reason, overlapping prepares
  observe prior reservations, prepare is idempotent.
- [x] Phase 5 completed. `run_2pc(order)` added to
  [order_executor/src/app.py](order_executor/src/app.py): discovers the DB
  primary via `WhoIsPrimary`, fans out Prepare to DB + payment in parallel,
  logs `2pc_decision=COMMIT|ABORT` *before* phase 2, then sends Commit
  (or Abort) to both participants. Wired into `consume_loop` after
  `Dequeue`. End-to-end test passes both paths:
  - COMMIT: `POST /checkout` with Book Ax2 + Book Bx1 → coordinator logs
    `2pc_start ... amount=40.97`, `2pc_votes db=True payment=True`,
    `2pc_decision=COMMIT`, `2pc_commit_applied`; all three DB replicas
    converge to Book A=8 Book B=5.
  - ABORT: `POST /checkout` with Book Ax1000 → DB votes abort
    (`insufficient stock`), `2pc_decision=ABORT`, both participants get
    Abort, stock unchanged across all replicas.
- [x] Phase 6 completed. DB participant persists staged txn to
  `/app/state/txn_<order>.json` (write-temp-then-rename) at the moment
  it votes commit. On startup the server reloads any `txn_*.json` it
  finds (logs `recovered_pending order=... items=[...]`) before serving
  traffic. A `FAIL_NEXT_COMMIT` env counter makes the next N `Commit`
  RPCs return `UNAVAILABLE` with a retry hint so we can exercise the
  coordinator loop without crashing the container. The coordinator
  (`run_2pc` in `order_executor/src/app.py`) now retries `Commit` up to
  12 times (~40s total, [0.5,1,2,4,4,4,4,4,4,4,4]s backoffs), re-
  discovering the DB primary between attempts, and logs
  `2pc_commit_retry` / `2pc_commit_retry_succeeded`. DB participant
  distinguishes `commit_idempotent` (already-committed set) from
  `commit_unknown` (no pending + no record) so a freshly-elected primary
  that never saw Prepare refuses the commit instead of silently
  succeeding. `utils/other/hotreload.py` skips `/app/state/` so txn
  persistence no longer triggers dev-mode restarts. Two passing tests:
  `order_executor/tests/test_2pc_fail_injection.py` (2 injected
  failures, 3rd attempt wins, convergence Book A 10→9 on DB-1/2/3) and
  `order_executor/tests/test_2pc_crash_recovery.py` (Prepare + persist,
  `docker kill books_database_3` mid-retry, restart without override,
  `recovered_pending` logged, DB-3 re-wins bully, coordinator retry
  commits, convergence Book A 10→9, state dir cleaned).
- [x] Phase 7 completed. Added
  [docs/commitment-protocol.md](docs/commitment-protocol.md), a
  self-contained analysis of coordinator failure in our 2PC setup.
  Walks through the four crash windows (W1–W4), explains why a prepared
  participant is forced to block, and documents what our repo already
  provides (bully re-election of the leader executor, queue redelivery,
  idempotent `Prepare`/`Commit`/`Abort` on both participants,
  `committed_orders`/`commit_unknown` distinction) versus what it does
  not (the coordinator's `2pc_decision` line is stdout-only, not
  durable). Covers four literature mitigations — 3PC (non-blocking
  under crash failures, extra RPC round), highest-ID replacement
  coordinator with a `/app/executor_state/decision_*.json` log (the
  pragmatic fit for our topology, reusing the existing executor bully
  election), cooperative termination (participant-to-participant
  recovery), and Paxos Commit (consensus-based, out of scope). References
  the Phase 6 bonus tests and concludes with the concrete follow-up
  (durable decision record on the coordinator) that would close the
  W3/W4 gap.
- [x] Phase 8 completed. `CP3_EXECUTION_ONLY` env flag added to
  [orchestrator/src/app.py](orchestrator/src/app.py): when truthy
  (`1`/`true`/`yes`/`on`, case-insensitive) the `/checkout` handler
  skips Init + root events + `AwaitPipelineResult` + clear broadcast
  and goes straight to `enqueue_order` after basic input validation,
  returning `Order Approved` with empty `suggestedBooks`. Default is
  off so the full CP2+CP3 pipeline runs (Option A for the demo).
  Startup log line `[ORCH] startup cp3_execution_only=True|False`
  records the mode. New
  [docker-compose.cp3-only.yaml](docker-compose.cp3-only.yaml) override
  flips the flag without mutating the main compose file. Both modes
  verified end-to-end on the running stack:
  - Option A (default): `test_2pc_end_to_end.py` happy + abort paths
    pass; orchestrator logs show
    `initialization_complete → starting_root_events → clear_broadcast_sent
    final_vc=[3,2,2] → final_status=APPROVED`, 2PC commits, DB-1/2/3
    converge.
  - Option C (flag on): new
    [orchestrator/tests/test_cp3_execution_only.py](orchestrator/tests/test_cp3_execution_only.py)
    confirms orchestrator logs
    `cp3_execution_only=true skipping CP2 pipeline` for the order and
    asserts the absence of `initialization_complete`,
    `starting_root_events`, and `clear_broadcast_sent` for that order,
    response has `suggestedBooks=[]`, 2PC still commits via the
    executor, and stock decrements on DB-1/2/3.
- [x] Phase 9 — logging sweep. Audited §7 coverage across all CP3
  components; the key=value style used in orchestrator/src/app.py is
  already applied consistently. Verified the full 2PC trace via a live
  `/checkout` on the stack (order=f2a70cca…):
  - `payment_service`: `prepare_vote_commit … amount=12.99` →
    `commit_applied`.
  - `order_executor_3` (leader): `2pc_start`, `2pc_votes db=(vote_commit=True,msg='ok')
    payment=(vote_commit=True,msg='ok')`, `2pc_decision decision=COMMIT
    participants=[db,payment]`, `2pc_commit_applied`, `order_done status=committed`.
  - `books_database_3` (primary): `prepare_vote_commit items=[Book Ax1] persisted=yes`,
    `commit_applied title="Book A" seq=8 old=9 new=8 backups_acked=[1,2]`.
  - `books_database_1/2` (backups): `replicate_applied from_primary=3 title="Book A"
    seq=8 old=9 new=8` on each.
  One gap closed: the 2PC retry loop was silent on DB primary re-
  discovery. Added `2pc_primary_changed` and `2pc_primary_unknown` log
  lines in
  [order_executor/src/app.py](order_executor/src/app.py) so bully
  failover during a coordinator retry is visible in the trace. All
  other §7 items (DB replication, consistency protocol primary= lines,
  Payment/2PC Prepare/Commit/Abort, decision record with participants
  and votes) were already present from earlier phases.
- [x] Phase 10 — diagrams. Authored two new SVGs in the same lane-and-
  box style as
  [docs/diagrams/vector-clocks.svg](docs/diagrams/vector-clocks.svg) and
  [docs/diagrams/leader-election.svg](docs/diagrams/leader-election.svg):
  - [docs/diagrams/consistency-protocol.svg](docs/diagrams/consistency-protocol.svg)
    — four lanes (executor client, primary `books_database_3`, the two
    backups `_1` and `_2`). Shows the post-election coordinator
    announcement (`primary=3`), a full Write flow with the primary
    fanning `ReplicateWrite(seq=42)` to both backups and blocking until
    both `replicate_applied` acks land before logging
    `write_committed backups_acked=[1,2]`, and a Read that is served
    only by the primary. Side notes cover why replication is
    synchronous and how bully failover transparently re-targets the
    client.
  - [docs/diagrams/commitment-protocol.svg](docs/diagrams/commitment-protocol.svg)
    — three lanes (executor coordinator, `books_database` primary,
    `payment_service`). Case A shows the happy path: parallel
    `Prepare` fan-out, `vote_commit=true` from both, the
    `2pc_decision=COMMIT participants=[db,payment]` decision record
    written before phase 2, then `Commit` to both and
    `2pc_commit_applied`. Case B shows payment voting abort (e.g.
    amount over `MAX_AMOUNT`) and the coordinator sending `Abort` to
    both, producing `2pc_decision=ABORT`. Side notes call out the
    stdout decision record and the Phase-6 participant-recovery hook.
  Both files parse cleanly as XML
  (`consistency-protocol.svg` viewBox 1200×980, 82 nodes;
  `commitment-protocol.svg` viewBox 1200×1200, 98 nodes). README
  wiring lands in Phase 11.
- [x] Phase 11 — documentation. Three deliverables landed:
  - **README rewrite.** Top header renamed to Checkpoint 3; added two
    new top-level sections ([README.md](README.md)):
    "Replicated database and consistency protocol" and "Distributed
    commitment protocol (2PC)". Each section states what it delivers,
    embeds its diagram, lists the log lines that prove it, and links
    the design note. Service count updated from 9 to 13.
  - **Design notes in [docs/](docs/).** New
    [docs/consistency-redesign.md](docs/consistency-redesign.md)
    explains the primary-backup + synchronous-replication choice,
    summarises the protocol, walks through failover, shows how 2PC
    sits on top, lists the expected log lines, and calls out known
    limitations. Existing
    [docs/commitment-protocol.md](docs/commitment-protocol.md) was
    re-framed in its opening paragraph so readers see it is both the
    2PC primer and the §5.4 coordinator-failure analysis in one file.
  - **In-code docstrings.** Added method docstrings to the three
    payment_service 2PC handlers
    (`Prepare`/`Commit`/`Abort` in
    [payment_service/src/app.py](payment_service/src/app.py)); expanded
    the `persist_pending` docstring and added new docstrings to
    `remove_persisted` and `load_persisted_all` in
    [books_database/src/app.py](books_database/src/app.py) covering
    the write-then-rename invariant and the startup recovery scan.
    The 2PC coordinator (`run_2pc` in
    [order_executor/src/app.py](order_executor/src/app.py)) and the
    books_database Prepare/Commit/Abort handlers already had their
    docstrings from Phases 5–6.
  Also fixed a minor inaccuracy in the commitment-protocol diagram's
  abort case: the current code has DB (not payment) voting abort on
  insufficient stock, so the Case-B arrows were relabelled to match.
- [x] Phase 12 — verification script
  Delivered three new files:
  - [scripts/checkpoint3-checks.ps1](scripts/checkpoint3-checks.ps1) — the
    end-to-end verifier. Flags: `-SkipBuild` (reuse images for fast
    iteration), `-SkipFailover`, `-SkipBonus`. On every run it first does
    `docker compose down -v` so state starts from seed, then brings the
    stack up, waits for orchestrator and DB primary, and runs six
    assertion groups: 2PC happy path, 2PC oversold → abort, replica
    convergence, DB primary failover, and the participant-failure-recovery
    bonus. Each check writes a `[PASS]`/`[FAIL]` line and feeds a final
    summary with a non-zero exit code on any failure.
  - [scripts/_cp3_db_probe.py](scripts/_cp3_db_probe.py) — a small gRPC
    helper called from PowerShell. Subcommands `read-stock <title>`
    (with `--tolerate-missing` for post-failover reads), `find-primary`,
    `all-reachable`. The PS script parses `DB-<id>=<qty>` lines back.
  - [test_checkout_oversold.json](test_checkout_oversold.json) — payload
    asking for 999 copies of Book A so the DB participant votes abort,
    which exercises the §5.4 ABORT path end to end; it sits next to the
    existing [test_checkout.json](test_checkout.json) used for the
    happy-path commit.
  Along the way, a few hygiene fixes fell out of running under
  `Set-StrictMode -Version Latest`: `Run-Compose` now wraps docker compose
  calls so stderr doesn't trip `$ErrorActionPreference='Stop'`; HTTP calls
  go through a short inline Python block via `urllib.request` because
  `Invoke-WebRequest` intermittently NREs against our orchestrator;
  `$Matches` is avoided in favour of `[regex]::Match` for reliable capture;
  `Sort-Object -Unique` results are array-wrapped before `.Count`; and the
  failover test re-drives a write after restoring the old primary, because
  only staged transactions are persisted — committed state resets to seed
  on restart, so the first post-restore write is what re-synchronises the
  backups through ReplicateWrite.
  Verification run (clean state, `-SkipBuild`): all 18 checks pass —
  docker/compose version, compose config, compose down/up, orchestrator
  ready, DB all-reachable, DB primary elected, compose ps, four
  `py-compile` checks, 2pc:valid-commit, 2pc:oversold-abort,
  convergence:read-all-replicas, db-failover, and
  bonus:participant-failure-recovery. Sample line:
  `[PASS] db-failover - DB primary 3 stopped, replica 2 elected new
  primary, writes resumed after replica restore.`
- [x] Phase 13 — final commit + push
  Commit `054d9a8` "Implement Checkpoint 3 phases 7-12" pushed to
  `origin/individual-sten-qy-li` (15 files, +1855/-20). Local Claude
  config under `.claude/` was deliberately excluded. The `checkpoint-3`
  git tag is Step 2's responsibility per §13 and is not created here.
- [x] Phase 14 — gap closure (post-audit).
  Closed the three code/documentation gaps identified in §15.4:
  1. **Stale stock after restart.** Added `persist_kv_store()` /
     `load_kv_store()` to [books_database/src/app.py](books_database/src/app.py).
     Every mutation site (Write handler, ReplicateWrite handler, Commit
     handler) now flushes `kv_store` to `STATE_DIR/kv_store.json` via
     write-then-rename with a per-thread temp file to avoid races under
     concurrent replication. `serve()` loads from disk on startup (log
     line `kv_store_loaded from=disk|SEED_STOCK`). Verified: checkout
     reduced Book A from 10→8, restart of all 3 replicas loaded
     `from=disk`, `ReadLocal` showed 8 on all 3 replicas.
  2. **Concurrent-writes documentation.** Added a "Concurrent writes
     (bonus)" subsection to [README.md](README.md) and §7 to
     [docs/consistency-redesign.md](docs/consistency-redesign.md)
     explaining per-key locking, why per-key over global, and the test
     reference. §12 checklist item ticked.
  3. **Concurrent-writes test strengthened.** Rewrote
     [books_database/tests/test_concurrent_writes.py](books_database/tests/test_concurrent_writes.py)
     with hard pass/fail assertions and convergence checks across all 3
     replicas. Test A (5 same-key writes) and Test B (5 different-key
     writes) both pass. Fixed a race condition: concurrent
     `persist_kv_store()` calls on backups shared the same `.tmp` file
     name, causing `os.replace` to fail under parallel
     `ReplicateWrite`. Fix: include `threading.get_ident()` in the temp
     file name. Also switched replication from per-call channels to
     persistent cached channels (`_replication_channels` dict) so
     concurrent fan-outs multiplex over a single HTTP/2 connection per
     backup.
  4. **Queue redelivery honesty note.** Added a blockquote to
     [docs/commitment-protocol.md](docs/commitment-protocol.md) §4.1
     explicitly stating that `Dequeue` is a destructive `popleft()` with
     no ack/nack/visibility-timeout and that queue redelivery is
     described-but-not-implemented.
  Remaining §15.4 items (git tag, evaluation slot) are release-day
  steps per §13.

### Risk notes

- Phases 5 and 6 are the riskiest (new distributed logic). Expect iteration.
- Phases 1 and 4 involve regenerating Python gRPC stubs. The local
  environment must have `grpcio-tools` installed, or the regeneration must
  be done inside a container that already has it.
- Phase 12 needs a running Docker Desktop to really pass.

---

## 15. Rubric audit at commit `1ed359b`

This section records an audit of the repository at commit `1ed359b`
("Mark Phase 13 complete in Charlie-Lima-Alfa.md", 2026-04-15) against
the Checkpoint 3 grading rubric published on the course website. The
rubric sources are:

- main Projects page:
  <https://courses.cs.ut.ee/2026/ds/spring/Main/Projects>
- Checkpoint 3 guide (Session 13):
  <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide12>
- Session 10 guide (database + consistency):
  <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide9>
- Session 11 guide (distributed commitment):
  <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide10>

### 15.1 Base points (10 pts)

| # | Requirement | Pts | Status | Evidence |
|---|---|---|---|---|
| 1 | **Consistency protocol & database module** — 3+ replicas, gRPC interface, chosen consistency protocol | 3 | Covered | 3 `books_database` replicas with bully election, synchronous primary-backup replication, gRPC `Read`/`Write`/`ReplicateWrite`/`WhoIsPrimary`, per-key locks for concurrent writes. Source: [books_database/src/app.py](books_database/src/app.py), [docker-compose.yaml](docker-compose.yaml). |
| 2 | **Distributed commitment protocol & new service** — coordinator/participant roles, payment service with Prepare/Commit/Abort | 3 | Covered | `payment_service` with Prepare/Commit/Abort; `order_executor` leader runs `run_2pc` as coordinator; `books_database` primary and `payment_service` are participants. Source: [order_executor/src/app.py](order_executor/src/app.py), [payment_service/src/app.py](payment_service/src/app.py). |
| 3 | **Logging** — system logs offering insight into functioning | 1 | Covered | Full key=value trace: `2pc_start`, `2pc_votes`, `2pc_decision`, `prepare_vote_commit`/`abort`, `commit_applied`, `replicate_applied`, `abort_ok`, etc., across all CP3 services. Phase 9 verified the end-to-end trace on a live stack. |
| 4 | **Project organization & documentation** — code docs, collaboration, overall organization | 1 | Covered | [README.md](README.md) updated to Checkpoint 3 with two new top-level sections; [docs/consistency-redesign.md](docs/consistency-redesign.md) and [docs/commitment-protocol.md](docs/commitment-protocol.md) added; docstrings on all 2PC handlers, coordinator, and recovery helpers. |
| 5 | **Consistency protocol diagram** — replicas, executor, operations | 1 | Covered | [docs/diagrams/consistency-protocol.svg](docs/diagrams/consistency-protocol.svg) — 4 lanes (executor, DB-3 primary, DB-1 backup, DB-2 backup), Write fan-out with synchronous replication, Read from primary only. |
| 6 | **Commitment protocol diagram** — sequence diagrams (2–3 pictures) illustrating protocol messages | 1 | Covered | [docs/diagrams/commitment-protocol.svg](docs/diagrams/commitment-protocol.svg) — Case A (happy path, decision=COMMIT) and Case B (DB votes abort, decision=ABORT) in one SVG with clear phase separators and side notes on decision record and participant recovery. |

### 15.2 Bonus points (up to 3 pts, 0.75 each)

| # | Bonus | Pts | Status | Evidence |
|---|---|---|---|---|
| B1 | **Concurrent write handling** (consistency session) | 0.75 | **Covered** | Per-key locks in [books_database/src/app.py](books_database/src/app.py) (`get_key_lock`), verified by [books_database/tests/test_concurrent_writes.py](books_database/tests/test_concurrent_writes.py) with hard pass/fail assertions and cross-replica convergence checks. Strategy documented in [README.md](README.md) and [docs/consistency-redesign.md](docs/consistency-redesign.md) §7. *(Gaps from original audit closed in Phase 14.)* |
| B2 | **Failing participant recovery** (commitment session) | 0.75 | Covered | On-disk `txn_<order>.json` persistence via write-then-rename; `load_persisted_all` recovery on startup; coordinator retry with exponential backoff and primary re-discovery; `committed_orders`/`aborted_orders` idempotency tracking. Demonstrated by [order_executor/tests/test_2pc_fail_injection.py](order_executor/tests/test_2pc_fail_injection.py) and [order_executor/tests/test_2pc_crash_recovery.py](order_executor/tests/test_2pc_crash_recovery.py). |
| B3 | **Coordinator failure analysis** (commitment session) | 0.75 | **Covered** | [docs/commitment-protocol.md](docs/commitment-protocol.md) §§3–5: W1–W4 crash windows, the blocking problem, what our repo handles vs. gaps, four literature mitigations. §4.1 now includes an explicit honesty note that `Dequeue` is a destructive `popleft()` with no ack/requeue, so the queue-redelivery claim is clearly flagged as described-but-not-implemented. *(Caveat from original audit addressed in Phase 14.)* |
| B4 | *(4th bonus — if one exists per the main rubric's "Maximum 4 bonus tasks … 3 points total")* | 0.75 | Unknown | The main rubric page says 4 tasks are available. The session guides (Guide9, Guide10) enumerate only 3 (B1–B3 above). There may be a 4th task on a child page not fully extracted during this audit. |

### 15.3 Other submission requirements

| Requirement | Status | Note |
|---|---|---|
| `checkpoint-3` git tag | **Not yet created** | Only `checkpoint-1` and `checkpoint-2` tags exist. Per §13 of this plan, the tag is a release-day step (Step 2's responsibility). |
| Docker Compose spins up seamlessly | Yes | [docker-compose.yaml](docker-compose.yaml) starts all 13 services. |
| Connects with provided frontend | Yes | [frontend/src/index.html](frontend/src/index.html) targets the orchestrator. |
| Verification script | Yes | [scripts/checkpoint3-checks.ps1](scripts/checkpoint3-checks.ps1) + [scripts/_cp3_db_probe.py](scripts/_cp3_db_probe.py) + [test_checkout_oversold.json](test_checkout_oversold.json). |
| Evaluation slot booked | Not yet | Scheduling link in the Session 13 guide. |

### 15.4 Gaps to close before the demo

1. ~~**Stale stock after DB replica restart.**~~ **Closed in Phase 14.**
   `persist_kv_store()` / `load_kv_store()` added; every mutation site
   flushes `kv_store` to disk; startup loads from disk or falls back to
   `SEED_STOCK`.
2. ~~**Document the concurrent-writes strategy (B1).**~~ **Closed in
   Phase 14.** README and `docs/consistency-redesign.md` now explain
   per-key locking; test rewritten with hard pass/fail assertions and
   convergence checks; §12 checklist item ticked.
3. ~~**Queue lacks ack/requeue (weakens B3 narrative).**~~ **Closed in
   Phase 14.** Honesty note added to
   [docs/commitment-protocol.md](docs/commitment-protocol.md) §4.1
   explicitly stating `Dequeue` is destructive with no ack mechanism.
4. **Create and push the `checkpoint-3` git tag.** Required by the
   rubric; deferred to release day.
5. **Book the evaluation slot.** Not a code deliverable.

### 15.5 Internal documentation files

The following files are **not required** by the course or any
checkpoint. They exist purely for internal team planning, progress
tracking, and/or recording TA feedback:

| File | Location | Purpose |
|---|---|---|
| `Charlie-Lima-Alfa.md` | repo root | Checkpoint 3 implementation plan — phases, checklist, risk notes, team roles, this audit |
| `Golf-Papa-Tango.md` | repo root | Earlier CP3 planning/analysis document (reviewed course pages, listed what needed to change) |
| `vc_investigation_results.txt` | repo root | TA feedback on Checkpoint 2 vector clocks — grade + feedback quote that prompted the VC redesign |

---

## 16. Rubric audit at commit `6f00e05`

This section records a second audit, run against commit `6f00e05`
("Close Checkpoint 3 audit gaps (phase 14)", 2026-04-16, branch
`individual-sten-qy-li`). It supersedes §15 as the current "where do
we stand" snapshot. Findings from the parallel audit in
[Golf-Papa-Tango.md](Golf-Papa-Tango.md) "Audit update for commit
`6f00e05`" that were not in this section's first draft have been
folded in; those additions are flagged with *(via GPT)* where they
appear. The rubric sources are the same as in §15:

- main Projects page:
  <https://courses.cs.ut.ee/2026/ds/spring/Main/Projects>
- Checkpoint 3 guide (Session 13 / Guide12):
  <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide12>
- Session 10 guide (Guide9 — database + consistency):
  <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide9>
- Session 11 guide (Guide10 — distributed commitment):
  <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide10>

### 16.0 Short answer *(via GPT)*

Commit `6f00e05` is **much closer** to full Checkpoint 3 readiness
than `1ed359b`. The earlier stale-replica-state problem is closed,
the concurrent-writes test has real assertions and cross-replica
convergence checks, and the coordinator-failure write-up is now
honest about the queue having no ack/requeue mechanism. No large
Checkpoint 3 implementation block is missing. What remains is
mostly **release/tag completeness** (§16.4 Category A), **README
and documentation consistency** (§16.4 Category B — three new
items folded in from the GPT audit) and **optional bonus-evidence
polish** (§16.4 Category C). Predicted rubric outcome if the
release-day items get done: **12.25 / 13.0** (see §16.5).

### 16.1 What changed between `1ed359b` and `6f00e05`

`6f00e05` is a single gap-closure commit that sits directly on top of
`1ed359b`. It closes the three code/documentation gaps the §15 audit
identified, plus a test-correctness follow-up:

- **Stale stock after DB replica restart** → fixed. `kv_store` is now
  persisted to `STATE_DIR/kv_store.json` via write-then-rename with a
  per-thread temp file name (`threading.get_ident()` suffix) to avoid
  `os.replace` races under concurrent `ReplicateWrite`. Startup loads
  from disk or falls back to `SEED_STOCK` (`kv_store_loaded from=...`
  log line). See
  [books_database/src/app.py](books_database/src/app.py)
  `persist_kv_store` / `load_kv_store`.
- **Concurrent-writes strategy documentation** → fixed. Per-key
  locking is now explained in both [README.md](README.md) (a new
  "Concurrent writes (bonus)" subsection) and in
  [docs/consistency-redesign.md](docs/consistency-redesign.md) §7.
- **Concurrent-writes test quality** → fixed. Rewrote
  [books_database/tests/test_concurrent_writes.py](books_database/tests/test_concurrent_writes.py)
  with hard pass/fail assertions, cross-replica convergence checks on
  every key, and a non-zero exit code on any failure. Both Test A
  (same-key writes) and Test B (different-key writes) pass.
- **Queue redelivery honesty note** → added. A blockquote in
  [docs/commitment-protocol.md](docs/commitment-protocol.md) §4.1 now
  states explicitly that `Dequeue` is a destructive `popleft()` with
  no ack/nack/visibility-timeout, so the queue-redelivery claim is
  unambiguously flagged as described-but-not-implemented.

One incidental fix fell out of the test-strengthening work:
replication channels to backups are now cached in
`_replication_channels` (one `grpc.insecure_channel` per peer, reused
across calls) instead of being created fresh on every replication
call. Under the new concurrent-writes test this keeps 5 parallel
fan-outs from opening 10 simultaneous TCP connections and tripping
over each other.

### 16.2 Base points status at `6f00e05`

All six base items from the §15.1 table are still covered. Nothing in
Phase 14 regressed any base deliverable; the stale-stock fix is an
improvement to base #1, and the documentation/test work strengthens
base #3 (logging — the new `kv_store_loaded` line) and base #4
(project organization — the honesty note and the expanded phase log).

| # | Requirement | Pts | `6f00e05` status |
|---|---|---|---|
| 1 | Consistency protocol & database module | 3 | Covered (improved: committed stock now survives replica restart) |
| 2 | Distributed commitment protocol & new service | 3 | Covered (unchanged from `1ed359b`) |
| 3 | Logging | 1 | Covered (one new log line added in Phase 14) |
| 4 | Project organization & documentation | 1 | Covered (strengthened in Phase 14) |
| 5 | Consistency Protocol diagram | 1 | Covered (unchanged) |
| 6 | Commitment Protocol diagram | 1 | Covered (unchanged) |

**Base subtotal: 10 / 10.**

### 16.3 Bonus points status at `6f00e05`

The Guide12 rubric awards **0.75 points per bonus task completed**,
with a total bonus cap of **3.0 points**. The Guide9 and Guide10
session pages explicitly enumerate three bonus tasks:

| # | Bonus | Source | Pts | `6f00e05` status |
|---|---|---|---|---|
| B1 | Concurrent write handling | Guide9 | 0.75 | **Fully covered.** Per-key locks + docs + assertive test with convergence check. |
| B2 | Failing-participant recovery | Guide10 | 0.75 | **Fully covered.** On-disk staged-txn persistence, startup recovery, coordinator retry, idempotency tracking, two passing tests. |
| B3 | Coordinator-failure analysis | Guide10 | 0.75 | **Fully covered.** [docs/commitment-protocol.md](docs/commitment-protocol.md) §§3–5 — crash windows, blocking problem, four literature mitigations, honest labelling of the queue-redelivery gap. |

**Bonus subtotal: 2.25 / 3.0.**

The remaining 0.75 pt gap between 2.25 achieved and the 3.0 cap is
addressed in §16.4 item 5 below. In short: we could not identify an
explicit fourth bonus task in the Guide9 / Guide10 content the
WebFetch audit extracted. Either (a) no fourth task exists and 2.25
is the maximum any team can score, or (b) a fourth task exists on a
child page the audit did not pull. The §15.2 B4 row kept this as
"Unknown" and the audit at `6f00e05` could not conclude otherwise.

### 16.4 Remaining gaps at `6f00e05`

#### Category A — Must-do before demo day (hard requirements)

**Gap 16.4.1 — `checkpoint-3` git tag not created.**
- **Rubric citation:** Guide12 lists "Git tag: `checkpoint-3`" under
  the "Code & Repository" deliverables.
- **Current state:** `git tag -l` on `6f00e05` shows
  `checkpoint-1`, `checkpoint-2`, `seminar-5`, `stable-v1`,
  `sten-seminar-7-leader-election`. The `checkpoint-3` tag is **not**
  present either locally or on `origin`.
- **Owner / when:** Per §13 of this plan, the tag is Step 3
  (team-lead's responsibility, placed on the **merge commit on
  `master`**, not on any commit that lives only on
  `individual-sten-qy-li`).
- **Severity:** High. If this tag is missing on demo day, the TA who
  checks out `checkpoint-3` gets nothing. This is the single most
  important remaining hard gap.

**Gap 16.4.2 — Branch not yet merged into `master`.**
- **Rubric citation:** the rubric does not demand `master` specifically,
  but §13 of this plan (our internal process) requires the
  `checkpoint-3` tag to live on the merge commit on `master`. Without
  the merge there is nothing to tag.
- **Current state:** `master` is still at its pre-CP3 state; all CP3
  work (Phases 1–14) is on `individual-sten-qy-li`.
- **Owner / when:** Team lead, release day, before tagging.
- **Severity:** High. Blocks Gap 16.4.1.

**Gap 16.4.3 — Evaluation slot not booked.**
- **Rubric citation:** Guide12 — "Team must schedule evaluation slot
  in provided spreadsheet. Only attend officially registered seminar
  group time slot."
- **Current state:** Not yet booked. The spreadsheet link lives in
  the Session 13 guide.
- **Owner / when:** Team, before evaluation dates 2026-05-06 /
  2026-05-08.
- **Severity:** High. Missing the slot means no evaluation and no
  points, regardless of code quality.

#### Category B — Demo-day logistics (administrative, not code)

**Gap 16.4.4 — Demo laptop not prepared.**
- **Rubric citation:** Guide12 — "At least one laptop with functional
  system", "Codebase and diagrams available for display", "System
  logs demonstrated showing component interactions".
- **Current state:** N/A (one-off preparation step).
- **Needed:** Docker Desktop running; repo checked out at tag
  `checkpoint-3`; both SVG diagrams open as images; README open at
  the "How to demonstrate" section; a terminal ready to run
  `scripts/checkpoint3-checks.ps1` and `docker compose logs`.
- **Severity:** Medium. Easy to forget; doesn't cost rubric points
  directly but the 10–15 minute demo window is tight enough that
  setup friction matters.

**Gap 16.4.5 — 10–15 minute presentation script not rehearsed.**
- **Rubric citation:** Guide12 — "10–15 minute presentation per
  group".
- **Current state:** The README's "How to demonstrate" section plus
  this plan's §§1–11 give most of the material, but no explicit
  script is written.
- **Severity:** Low-to-medium. A bad demo can cost soft points on
  base #4 ("Project organization, documentation, collaboration").

**Gap 16.4.5a — README demo section still points to the Checkpoint 2
verification script.** *(via GPT, closed in this commit.)*
- **Rubric citation:** Guide12 — base #4 "Project organization,
  documentation, collaboration"; demo requirement "Codebase and
  diagrams available for display" and "System logs demonstrated
  showing component interactions".
- **Current state:** [README.md](README.md) is labelled as a
  Checkpoint 3 document, but four separate places (lines 25, 31,
  169, 298 as of `6f00e05`) still instruct the reader to run
  `scripts/checkpoint2-checks.ps1` rather than
  `scripts/checkpoint3-checks.ps1`. A TA who follows the README
  literally exercises the CP2 pipeline and never runs the CP3
  verification flow.
- **Fix sketch:** `replace_all` on the README for
  `checkpoint2-checks.ps1` → `checkpoint3-checks.ps1`, then re-read
  each hit in context to make sure the surrounding prose still
  matches (some hits may talk about CP2 historically and should be
  left alone — a grep-and-replace without review will over-rewrite).
- **Severity:** Medium. Directly weakens the demo-readiness part of
  base #4. Quick to fix.

**Gap 16.4.5b — `docs/commitment-protocol.md` summary still claims
queue redelivery exists.** *(via GPT, closed in this commit.)*
- **Rubric citation:** Guide10 bonus — coordinator-failure analysis
  quality.
- **Current state:** Phase 14 added an honesty note to §4.1
  explicitly stating `Dequeue` is destructive with no ack/requeue.
  But §6 "Summary" at line 252 still lists
  `(b) redelivery from \`order_queue\`` as one of the three
  mitigations our repo partially provides. §4.1 and §6 are now
  internally inconsistent.
- **Fix sketch:** edit line 252 to replace `(b) redelivery from
  \`order_queue\`` with a phrasing consistent with the §4.1 honesty
  note — e.g. `(b) idempotent replay if the original coordinator is
  restarted, since the queue itself does not redeliver` — or simply
  drop the `(b)` clause and renumber. Fifteen-minute edit.
- **Severity:** Medium. B3 is a documentation-quality bonus; an
  internally contradictory document weakens exactly the axis this
  bonus is scored on. Quick to fix.

**Gap 16.4.5c — `scripts/checkpoint3-checks.ps1` still carries stale
comments describing the pre-Phase-14 restart problem.** *(via GPT, closed in this commit.)*
- **Rubric citation:** Guide12 — base #4 "Project organization,
  documentation, collaboration" (code documentation).
- **Current state:** Lines 385–388 of the script still say:
  > Bully tie-breaker (higher replica id wins). The current design
  > loads only staged/prepared state from disk on restart, so the
  > recovered replica can bring stale seed values back with it — a
  > documented consistency-vs-availability tradeoff …
  > The first write through the restored primary re-synchronises the
  > [backups]
  This was accurate against `1ed359b` but is no longer accurate
  against `6f00e05`: the replica now loads committed `kv_store.json`
  on startup, so it does not bring stale seed values back with it,
  and the post-restore write is no longer needed as a
  re-synchronisation workaround.
- **Fix sketch:** rewrite the block to either (a) remove the
  workaround rationale and just keep the bully tie-breaker
  explanation, or (b) briefly note that Phase 14 made the workaround
  unnecessary while the follow-up write is retained as an
  end-to-end sanity check. Ten-minute edit.
- **Severity:** Low. Does not affect test behaviour or rubric points
  directly; only confuses a reader of the script.

#### Category C — Optional polish (not required for full rubric marks)

**Gap 16.4.6 — Verification script does not invoke the
concurrent-writes test.**
- **Current state:** [scripts/checkpoint3-checks.ps1](scripts/checkpoint3-checks.ps1)
  runs 18 assertions but does **not** call
  [books_database/tests/test_concurrent_writes.py](books_database/tests/test_concurrent_writes.py).
  Grep for `test_concurrent_writes` in the script returns nothing.
- **Why this is a gap:** the §15.2 B1 entry flagged three weaknesses.
  Phase 14 fixed two of the three (documentation + test assertions)
  but not the third (make the test runnable from the main verifier so
  a TA who just runs the script sees concurrent-write evidence too).
  The test exists and passes, so a TA who reads the code and runs
  the test manually still gets full B1 credit; this gap only matters
  for the "run one script and watch everything pass" narrative.
- **Fix sketch:** add a `2. [PASS] consistency:concurrent-writes`
  style check to the script that shells out to
  `python books_database/tests/test_concurrent_writes.py` from the
  host, expects exit-code 0, and asserts the output contains
  `CONCURRENT WRITES TEST: PASSED`. Ten minutes of work.
- **Severity:** Low. Does not affect rubric points directly as long
  as the TA is willing to run the test manually.

**Gap 16.4.7 — No explicit fourth bonus task implemented.**
- **Rubric citation:** Guide12 — "0.75 points each for completing
  bonus tasks in Consistency Protocols sessions" and "0.75 points
  each for completing bonus tasks in Commitment Protocols sessions".
  Total bonus cap is 3.0 pts, implying space for four 0.75-pt tasks.
- **Current state:** B1–B3 covered for 2.25 pts. We could not
  identify a fourth bonus task in the Guide9 / Guide10 content
  extracted by the audit.
- **Possible fourth-bonus candidates** (if the 0.75 pt is worth
  chasing):
  - **Implement 3PC** (Guide10 alternative protocol). Adds a
    `PreCommit` phase and `pre_committed_orders` state on each
    participant. Roughly a day of work and requires updating both
    diagrams and the commitment-protocol doc.
  - **Durable decision log on the coordinator** (closes the W3/W4 gap
    analysed in [docs/commitment-protocol.md](docs/commitment-protocol.md)
    §5.2). Mirror the Phase-6 participant persistence pattern onto
    the executor: write `/app/executor_state/decision_<order>.json`
    before phase 2, scan on leader promotion, resume phase 2 from the
    record. Roughly half a day of work, reuses infra we already have.
  - **Queue ack / visibility-timeout** (closes the B3 caveat and
    makes the coordinator-failure story actually work end to end).
    Add `AckOrder(order_id)` to
    [order_queue/src/app.py](order_queue/src/app.py) and track
    in-flight orders with a visibility timeout so a crashed leader's
    order is re-offered to a new leader. Half a day of work.
- **Severity:** Low. 2.25 / 3.0 bonus is already a strong result.
  Only pursue a fourth bonus if schedule allows and if the team can
  confirm with the TA that a fourth task actually exists in the
  rubric.

**Gap 16.4.8 — `kv_store.json` persisted files are not covered by
the Phase-6 recovery tests.**
- **Current state:** Phase 6 tests
  ([order_executor/tests/test_2pc_fail_injection.py](order_executor/tests/test_2pc_fail_injection.py),
  [order_executor/tests/test_2pc_crash_recovery.py](order_executor/tests/test_2pc_crash_recovery.py))
  exercise staged-txn recovery via `txn_<order>.json`, but the new
  `kv_store.json` persistence from Phase 14 does not have its own
  dedicated assertion in the verification script or the test suite.
  The concurrent-writes test indirectly exercises it by forcing a
  write-then-restart cycle in the stack, but there is no explicit
  "restart replica, read back post-commit value" assertion.
- **Fix sketch:** add a small test (or a step in the checkpoint3
  script) that writes a value via the primary, restarts all three
  replicas, then asserts `ReadLocal` on each still returns the
  post-commit value and the startup log shows `kv_store_loaded
  from=disk`.
- **Severity:** Low. The manual verification in Phase 14's
  commit message confirmed the fix works end to end; automating it
  would be defensive but is not strictly required by the rubric.

### 16.5 Verdict for commit `6f00e05`

**Code-space completion: ~100% of what the rubric scores.** All ten
base points and all three enumerated bonus tasks (B1–B3) are covered.
The only remaining code-space items are polish (16.4.6, 16.4.8) and a
speculative fourth bonus (16.4.7) whose existence we could not confirm.

**Non-code-space completion: blocked on release-day work.** The
`checkpoint-3` git tag does not exist (16.4.1), the branch is not
merged into `master` (16.4.2), and the evaluation slot is not booked
(16.4.3). These are §13 Step-3 items, owned by the team lead.

**Predicted rubric score, assuming 16.4.1–16.4.3 get done on release
day:**
- Base: 10 / 10
- Bonus: 2.25 / 3.0 (unless a fourth bonus is implemented)
- **Total: 12.25 / 13.0**

**Predicted rubric score, assuming 16.4.1–16.4.3 are NOT done:**
- Demo fails (no `checkpoint-3` tag to check out; no booked
  evaluation slot) → evaluation does not happen → score is effectively
  zero regardless of code quality.

The risk concentration is therefore entirely on release-day
administrative work, not on anything in the code.

### 16.6 Fix-before-submission checklist *(via GPT)*

A consolidated, ordered to-do list covering every gap in §16.4. Items
1–3 are hard blockers; items 4–6 are documentation-consistency fixes
that cost very little time and tighten base #4 plus the B3 narrative;
items 7–9 are optional polish.

1. Create the `checkpoint-3` Git tag on the final approved
   submission commit on `master` after §13 Step 3 (blocker —
   §16.4.1).
2. Merge `individual-sten-qy-li` into `master` so there is a merge
   commit to tag (blocker — §16.4.2).
3. Book the evaluation slot in the Google Sheet linked from the
   Session 13 guide (blocker — §16.4.3).
4. ~~Update [README.md](README.md) so every reference to
   `scripts/checkpoint2-checks.ps1` in CP3 demo instructions points
   to `scripts/checkpoint3-checks.ps1` instead (§16.4.5a).~~ **Done in this commit.**
5. ~~Fix the line-252 summary in
   [docs/commitment-protocol.md](docs/commitment-protocol.md) so it
   is consistent with the §4.1 honesty note about queue redelivery
   (§16.4.5b).~~ **Done in this commit.**
6. ~~Rewrite the lines-385–388 stale-seed comment block in
   [scripts/checkpoint3-checks.ps1](scripts/checkpoint3-checks.ps1)
   to reflect the Phase-14 `kv_store.json` persistence (§16.4.5c).~~ **Done in this commit.**
7. *(Optional)* Add a `consistency:concurrent-writes` check to
   [scripts/checkpoint3-checks.ps1](scripts/checkpoint3-checks.ps1)
   that shells out to
   [books_database/tests/test_concurrent_writes.py](books_database/tests/test_concurrent_writes.py)
   and asserts the output contains `CONCURRENT WRITES TEST: PASSED`
   (§16.4.6).
8. *(Optional)* Add a direct `kv_store.json` restart-persistence test
   (write → restart all three replicas → assert `ReadLocal` returns
   the post-commit value on each + startup log shows
   `kv_store_loaded from=disk`) (§16.4.8).
9. *(Optional, stretch)* Implement a fourth bonus task (candidates:
   durable coordinator decision log, queue ack/visibility-timeout,
   or 3PC) if the team wants to chase the last 0.75 pt and can
   confirm with the TA that a fourth bonus actually exists
   (§16.4.7).

Items 1–6 together are ~90 minutes of work. Items 7–8 are another
60–90 minutes. Item 9 is a half-day to full-day commitment depending
on which candidate is picked.

## 17. Audit of commit `2c42158`

This section records the review of three peer-review findings raised
against commit `2c42158` (the "audit + doc-consistency" commit that
closed §16.4.5a/b/c). Each finding was checked directly against the
files in the tree at `2c42158`; conclusions below are independent of
the reporter's framing.

### 17.1 Finding 1 — commitment-protocol.md still contradicts itself (Medium)

**Reporter's claim.** The §4.1 honesty note says the queue does **not**
redeliver in-flight orders (because `Dequeue` is a destructive
`popleft()` with no ack/nack), but §4.2 and the §4.2 closing paragraph
still describe the replacement leader as if queue redelivery did work.

**Verification.** Confirmed, and worse than reported. Two separate
contradictions remain in §4.2 of
[docs/commitment-protocol.md](docs/commitment-protocol.md):

- **Line 152:** "Will re-dequeue the order (because the previous leader
  never sent `OrderDone` to the queue) and re-run `run_2pc` from
  scratch." This directly contradicts the honesty note, which says the
  replacement leader never sees the order again unless the original
  leader restarts or the user resubmits.
- **Line 178:** The "honest summary" paragraph credits correct
  convergence to *"participant idempotency + bully re-election + the
  queue's redelivery semantics"*. The queue has no redelivery
  semantics, so this bullet is false.

Commit `2c42158`'s fix for §16.4.5b only patched the top-level §6
Summary (the one-sentence version). The §4.2 body paragraphs — which
are what an evaluator reading the coordinator-failure analysis end-to-
end will actually rely on — were missed.

**Conclusion.** Agree. The `6f00e05` audit under-scoped gap 16.4.5b:
the fix should have covered every paragraph that implied queue
redelivery, not only the final summary. This is now tracked as **Gap
17.1** and will be closed in the same commit that archives this
section.

**Severity.** Medium. The coordinator-failure analysis bonus (Guide10
§5.4) is a documentation-quality bonus; an internally inconsistent
document is exactly what this bonus penalises.

### 17.2 Finding 2 — "no persistent database yet" in CP2 known-limitations (Low)

**Reporter's claim.** [README.md](README.md) line 291 ("there is no
persistent database yet") is no longer true after the Phase-14
`kv_store.json` work.

**Verification.** The statement sits under the "Checkpoint 2
deliverables in this repo" header (line 162) and in-context is
historically accurate for the CP2 snapshot. However, the root
`README.md` is now a combined CP2+CP3 document, and the "Known
limitations" heading is generic enough that a TA skim-reading the
document in evaluation mode would reasonably take it as current state.

**Conclusion.** Partially agree. The claim is technically scoped to
the CP2 section, but the boundary is invisible to a casual reader. A
one-line clarifier (e.g. "the books database persists committed stock
to `kv_store.json` as of CP3; the CP2 caches and queue remain
process-local memory only") would close the ambiguity without
rewriting the CP2 section wholesale.

**Severity.** Low. Polish item. Does not affect any rubric bullet
directly.

### 17.3 Finding 3 — checkpoint3-checks.ps1 doesn't exercise concurrent-writes bonus (Low)

**Reporter's claim.** The main CP3 verification script only wires in
the participant-failure recovery bonus
([scripts/checkpoint3-checks.ps1:491-494](scripts/checkpoint3-checks.ps1#L491-L494));
the concurrent-writes bonus is covered only by the standalone
[books_database/tests/test_concurrent_writes.py](books_database/tests/test_concurrent_writes.py).

**Verification.** Confirmed. A full-file search of
`scripts/checkpoint3-checks.ps1` returns **zero** hits for
`concurrent` or `test_concurrent_writes`. Only
`Test-ParticipantFailureBonus` is invoked from the bonus section.

**Conclusion.** Agree, and already captured as §16.4.6 in the earlier
audit. The concurrent-writes bonus is not a missing-implementation
gap — the code, per-key locks, and dedicated assertive test all
exist. What is missing is the "one-script TA demo" path that rolls
the dedicated test into the same verification flow as the other CP3
bonus evidence.

**Severity.** Low. Optional polish. Closing it strengthens the demo
story; not closing it does not weaken any rubric bullet.

### 17.4 Updated action list

Items carried over from §16.6 plus the new Finding 1 item:

1. ~~**Gap 17.1 (Medium).** Rewrite
   [docs/commitment-protocol.md](docs/commitment-protocol.md) §4.2 so
   line 152 and line 178 no longer imply queue redelivery. The
   replacement leader cannot "re-dequeue" the order; correct wording
   is that redelivery depends on the original leader restarting or
   the user resubmitting.~~ **Done in the same commit that added
   this §17.** §4.1 honesty note, §4.2 body, §4.2 closing paragraph,
   and §6 Summary now tell one consistent story.
2. ~~*(Low.)* Add the CP3 clarifier to the CP2 known-limitations list
   in [README.md](README.md) (Finding 17.2).~~ **Done in the same
   commit as item 1.** The CP2 "Known limitations" list now opens
   with an explicit "this list describes the state of the repo at
   the Checkpoint 2 snapshot" clarifier and points the reader to the
   earlier CP3 section, with a follow-up bullet noting that
   `kv_store.json` persistence was added in CP3.
3. ~~*(Low.)* Add a `bonus:concurrent-writes` check to
   [scripts/checkpoint3-checks.ps1](scripts/checkpoint3-checks.ps1)
   that shells out to
   [books_database/tests/test_concurrent_writes.py](books_database/tests/test_concurrent_writes.py)
   (Finding 17.3, identical to §16.4.6).~~ **Done in the same commit
   as item 1.** New `Test-ConcurrentWritesBonus` helper added
   alongside `Test-ParticipantFailureBonus`, invoked in the
   `-SkipBonus` guarded section, reports as check name
   `bonus:concurrent-writes`. Since this also closes §16.4.6,
   §16.6 item 7 is now Done as well.
4. All three §16.4 Category A blockers (`checkpoint-3` tag, merge to
   `master`, book the evaluation slot) remain unchanged.

