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

- [ ] Order data model carries `items` (title + quantity) end to end
- [ ] Orchestrator stops collapsing the order to `item_count`
- [ ] Queue proto extended with `repeated OrderItem items` and stubs regenerated
- [ ] `books_database` service with gRPC Read/Write
- [ ] 3 database replicas in `docker-compose.yaml`
- [ ] Consistency protocol implemented (primary-backup recommended)
- [ ] Primary failover reuses bully election pattern
- [ ] `payment_service` with Prepare/Commit/Abort
- [ ] 2PC coordinator logic inside `order_executor`
- [ ] Database participant supports Prepare/Commit/Abort
- [ ] `CP3_EXECUTION_ONLY` dev-time flag in orchestrator
- [ ] Logs updated for replication, consistency, payment, 2PC
- [ ] In-code docstrings added to 2PC coordinator, DB participant, payment participant, recovery helpers
- [ ] `docs/diagrams/consistency-protocol.svg` added
- [ ] `docs/diagrams/commitment-protocol.svg` added
- [ ] README updated to Checkpoint 3
- [ ] `docs/consistency-redesign.md` and `docs/commitment-protocol.md` added
- [ ] `scripts/checkpoint3-checks.ps1` passes end to end (including replica-convergence check)
- [ ] New test payload files added
- [ ] Git tag `checkpoint-3` created and pushed
- [ ] Bonus: concurrent-writes strategy documented
- [ ] Bonus: participant persists staged transaction to disk before voting commit
- [ ] Bonus: participant-failure recovery demonstrated
- [ ] Bonus: coordinator-failure analysis written
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
- [ ] Phase 5 — 2PC coordinator in executor
- [ ] Phase 6 — participant-failure bonus (on-disk staged txn)
- [ ] Phase 7 — coordinator-failure analysis
- [ ] Phase 8 — wiring + `CP3_EXECUTION_ONLY` dev flag
- [ ] Phase 9 — logging sweep
- [ ] Phase 10 — diagrams
- [ ] Phase 11 — documentation
- [ ] Phase 12 — verification script
- [ ] Phase 13 — final commit + push

### Risk notes

- Phases 5 and 6 are the riskiest (new distributed logic). Expect iteration.
- Phases 1 and 4 involve regenerating Python gRPC stubs. The local
  environment must have `grpcio-tools` installed, or the regeneration must
  be done inside a container that already has it.
- Phase 12 needs a running Docker Desktop to really pass.

