# Golf Papa Tango

## Purpose
This file explains what still needs to change in the local repository so that it is ready for **Checkpoint 3**.

The text uses simple English, but it still names the technical parts that need to be changed.

## Webpages reviewed
I reviewed these course pages:

- `https://courses.cs.ut.ee/2026/ds/spring/Main/Projects`
- `https://courses.cs.ut.ee/2026/ds/spring/Main/Guide9`
- `https://courses.cs.ut.ee/2026/ds/spring/Main/Guide10`
- `https://courses.cs.ut.ee/2026/ds/spring/Main/Guide12`

These pages define the practical work flow, the Checkpoint 3 tasks, the database task, and the distributed commitment task.

## Short answer
The repository is **not ready yet for Checkpoint 3**.

Right now, the repository is still mainly a **Checkpoint 2** solution:

- vector clocks are implemented
- leader election is implemented
- the queue and executor replicas are implemented
- the executor can dequeue an order and log that it is executing it

But Checkpoint 3 needs more than that. The main missing parts are:

- a **replicated books database** service
- a **consistency protocol** for that database
- a **new payment service**
- a **distributed commitment protocol** between the executor, the database module, and the payment service
- updated **order data**, so the system knows which books and how many copies were ordered
- clearer **code-level documentation** in the important new execution-path files
- new **documentation, diagrams, logs, and tests** for Checkpoint 3

## What the course expects in Checkpoint 3
From the course pages, Checkpoint 3 expects these main things:

1. A **books database** gRPC service with at least **3 replicas**.
2. The database must support **Read** and **Write** operations.
3. The replicated database must use a **consistency protocol** or another strong coordination design.
4. The **executor** must use the database when it executes an order.
5. A new dummy gRPC service must be added, for example a **payment service**.
6. The **executor must become the coordinator** of a distributed commitment protocol such as **2PC** or **3PC**.
7. The database module and the payment service must be the **participants** in that commitment protocol.
8. The project must include:
   - logs
   - documentation
   - documented important code paths
   - Docker and Docker Compose demo readiness
   - committed latest repository changes
   - a **consistency protocol diagram**
   - a **distributed commitment protocol diagram**
   - a `checkpoint-3` tag

There are also bonus topics:

- handling concurrent writes to the same book
- recovery from a failing participant
- analysis of coordinator failure

## What the repository currently has
### Services that already exist
The current `docker-compose.yaml` starts these services:

- `frontend`
- `orchestrator`
- `transaction_verification`
- `fraud_detection`
- `suggestions`
- `order_queue`
- `order_executor_1`
- `order_executor_2`
- `order_executor_3`

### Current strong points
The repository already has some useful parts for Checkpoint 3:

- The queue already exists.
- The executor replicas already exist.
- Leader election already exists.
- Mutual exclusion for dequeueing already exists.
- The frontend already sends an `items` array.
- The course allows focusing on the execution path for this checkpoint.

This means the project does **not** need to start from zero.

## Main gaps between the current repo and Checkpoint 3
### Gap 1: there is no books database service
There is no folder or service for a replicated books database.

Missing parts:

- no `books_db` or `books_database` service
- no database gRPC proto
- no replicated database containers in Docker Compose
- no storage of book stock
- no database reads or writes during execution

### Gap 2: there is no consistency protocol yet
The course requires a consistency design for the replicated database.

Missing parts:

- no primary/backup logic
- no chain replication logic
- no quorum logic
- no replica-to-replica database messages
- no design choice written in the docs

### Gap 3: there is no payment service
The course asks for a new dummy gRPC service, for example a payment service.

Missing parts:

- no `payment_service` folder
- no payment proto
- no payment container in Docker Compose
- no payment prepare/commit/abort logic

### Gap 4: there is no distributed commitment protocol
The current executor does not coordinate commit or abort across services.

Right now, `order_executor/src/app.py` only:

- checks if this replica is leader
- dequeues an order
- prints that it is executing the order

It does **not**:

- send `Prepare` to participants
- wait for participant votes
- decide `Commit` or `Abort`
- retry or timeout
- log transaction phases

### Gap 5: the current order data is too small
This is a very important gap.

The frontend already sends:

- item name
- quantity

But the backend drops that information. In `orchestrator/src/app.py`, the order is reduced to:

- user data
- credit card data
- `item_count`
- terms accepted

Also, `utils/pb/order_queue/order_queue.proto` only stores:

- `item_count`

This is not enough for Checkpoint 3 because the executor must know:

- which book was ordered
- how many copies were ordered

Without that information, the database cannot update stock.

### Gap 6: the repo documentation is still Checkpoint 2 documentation
The current `README.md` is still written as a Checkpoint 2 guide.

It does not yet document:

- the books database
- the chosen consistency protocol
- the payment service
- the commitment protocol
- the new Checkpoint 3 demo flow

### Gap 7: the important code paths are not yet documented for Checkpoint 3
The Checkpoint 3 page explicitly asks to document relevant code.

That means the final Checkpoint 3 repo should not only have a README. It should also have short comments or docstrings in the parts of the code that are central to the execution flow.

Good places for this are:

- the executor coordinator flow
- the database participant flow
- the internal database replication flow
- the payment participant flow
- any timeout or recovery logic

### Gap 8: the test script is still a Checkpoint 2 test script
The current `scripts/checkpoint2-checks.ps1` verifies:

- vector clocks
- fraud rejection
- queueing
- leader failover

It does not verify:

- stock reads and writes
- replication correctness
- prepare / commit / abort behavior
- payment failure
- database failure handling

### Gap 9: the final repository handoff steps are not written clearly enough
Checkpoint 3 also expects the latest state to be committed and tagged.

The repo plan should therefore end with these final steps:

- verify Docker Compose startup one last time
- commit the final implementation, tests, docs, and diagrams
- after team lead peer review and merge into `master`, the team lead should create the `checkpoint-3` tag on that final approved commit

## Recommended implementation direction
### Recommendation: use a simple design first
For Checkpoint 3, a simple and clear design is better than a complicated design.

I recommend this design:

- **Consistency protocol:** primary-backup replication
- **Commitment protocol:** 2PC (Two-Phase Commit)

Why this is a good fit:

- it is easier to explain
- it matches the course examples well
- it is enough for the checkpoint
- it keeps the amount of code reasonable

## Detailed changes needed
### 1. Add a replicated books database module
Create a new service folder, for example:

- `books_database/`

or:

- `books_db/`

This service should be replicated at least 3 times in `docker-compose.yaml`.

#### What this service should do
The database should store book stock in a key-value style.

A simple example:

- key = book title
- value = stock count

Example data:

- `"Book A" -> 10`
- `"Book B" -> 6`

#### What code needs to be added
Add a new proto file, for example:

- `utils/pb/books_db/books_db.proto`

At minimum, the database module should support:

- `Read`
- `PrepareWrite`
- `Commit`
- `Abort`

If you use primary-backup replication, you will probably also need internal replica RPCs such as:

- `ReplicatePrepare`
- `ReplicateCommit`
- `ReplicateAbort`

Then generate the Python gRPC files for that proto.

#### What Docker changes are needed
Add 3 database services in `docker-compose.yaml`, for example:

- `books_db_1`
- `books_db_2`
- `books_db_3`

Each replica should know:

- its replica id
- who the primary is
- who the other replicas are

### 2. Choose and implement a consistency protocol
The course asks for a consistency protocol for the replicated database.

For a first working version, use **primary-backup**:

- one replica is primary
- the other replicas are backups
- all writes go through the primary
- the primary forwards the staged update to the backups
- reads can go to the primary only, if you want stronger and simpler behavior

#### What the protocol should guarantee
At minimum, the design should make it clear that:

- writes are ordered
- all replicas receive the same committed stock update
- the client does not need to know which internal replica was changed

#### Current repo impact
This work will be completely new. No existing service currently does this.

### 3. Add a payment service
Create a new folder, for example:

- `payment_service/`

Add a new proto file, for example:

- `utils/pb/payment/payment.proto`

This service does not need real payment logic. Dummy logic is enough.

#### Good minimal behavior
The payment service can:

- accept `Prepare`
- stage a fake payment record
- on `Commit`, mark payment as completed
- on `Abort`, discard the staged payment

It should log every step.

#### Why this service matters
Checkpoint 3 is not only about the database. It is also about making **multiple participants** agree on one transaction result.

### 4. Turn the executor into a 2PC coordinator
This is one of the biggest changes.

Right now, `order_executor/src/app.py` only dequeues and prints.

For Checkpoint 3, after the leader dequeues an order, it should:

1. read the order items
2. contact the database module
3. contact the payment service
4. start **Phase 1** of 2PC: `Prepare`
5. collect the answers
6. if all answer yes, send `Commit`
7. if any answer no, or if timeout happens, send `Abort`
8. log the full decision path

#### Important design note
The **database module** should behave like one 2PC participant from the executor's point of view.

This means:

- the executor should not run 2PC separately with each database replica
- the executor talks to the database module interface
- the database module itself handles internal replication

This is simpler and closer to the course wording.

### 5. Carry the real items through the system
This is required if you want to update stock correctly.

#### Current problem
The frontend sends real items, but the backend drops them and only keeps `item_count`.

#### Files that need changes
- `frontend/src/index.html`
- `orchestrator/src/app.py`
- `utils/pb/order_queue/order_queue.proto`
- generated gRPC files under `utils/pb/order_queue/`

If you choose to keep the old validation services active during Checkpoint 3 development, then you may also need to extend:

- `utils/pb/transaction_verification/transaction_verification.proto`
- `utils/pb/fraud_detection/fraud_detection.proto`
- `utils/pb/suggestions/suggestions.proto`

#### Suggested new structure
Add an order item message, for example:

```proto
message OrderItem {
  string title = 1;
  int32 quantity = 2;
}
```

Then put:

```proto
repeated OrderItem items = ...
```

inside the order data message.

#### Why this is necessary
The executor must know:

- which titles to read in the database
- how much stock to reduce

### 6. Decide how much of Checkpoint 2 stays active during Checkpoint 3 work
The Session 10 instructions say that for development and testing, you may comment out the earlier validation services and focus on valid orders entering the queue.

That means you have two practical options.

#### Option A: simpler path for Checkpoint 3
Temporarily focus only on:

- frontend
- orchestrator
- order queue
- order executors
- books database
- payment service

In this option:

- the orchestrator does basic request parsing
- it creates an order id
- it enqueues only valid-looking orders
- it does not run fraud, suggestions, or transaction verification during Checkpoint 3 development

This is the simpler path.

#### Option B: keep the full Checkpoint 2 path active
This is possible, but it is more work.

In this option:

- the current validation pipeline still runs
- after approval, the order goes to the queue
- the executor then runs the new Checkpoint 3 execution logic

This is more complete, but it means you must keep both paths correct at the same time.

#### Recommendation
For now, use **Option A** during development, but do not delete the old Checkpoint 2 code. Keep it in the repo so it can be reconnected later for Checkpoint 4.

### 7. Update the orchestrator
If you follow the simpler Checkpoint 3 path, `orchestrator/src/app.py` should be simplified during this phase.

#### What it should do in the short term
- accept checkout JSON
- validate basic required fields
- preserve the full items list
- assign an `orderId`
- enqueue the order
- return a clear response

#### What it should not do in the short term
- no vector-clock orchestration for every order during Checkpoint 3 development, if you use the allowed simplified path

A safe approach is:

- keep the current code
- add a development flag or separate code path

For example:

- `CP3_EXECUTION_ONLY=true`

This is cleaner than deleting large parts of the old logic.

### 8. Update the queue and order data model
The queue must carry enough data for the executor.

#### Files that need changes
- `utils/pb/order_queue/order_queue.proto`
- `order_queue/src/app.py`
- `orchestrator/src/app.py`
- `order_executor/src/app.py`

#### New queue behavior needed
The queue must store:

- order id
- user data if needed
- items with title and quantity

It may also store:

- total amount
- payment metadata

But these extra fields are optional for a minimal Checkpoint 3 solution.

### 9. Add stock logic to the executor + database interaction
The executor must stop being a print-only worker.

#### New execution steps
For each dequeued order:

1. Read stock for every ordered title.
2. Check if stock is enough.
3. If stock is not enough, abort the transaction.
4. If stock is enough, prepare the new stock values.
5. Run 2PC with:
   - books database
   - payment service
6. Only after `Commit`, apply the final changes.

#### Important rule
No participant should make permanent changes during `Prepare`.

During `Prepare`, a participant should only:

- check if it can do the work
- stage the change
- answer yes or no

Real changes should happen only on `Commit`.

### 10. Add logs for Checkpoint 3
Checkpoint 3 also gives points for logging.

The current repo already logs Checkpoint 2 behavior well. You now need similar logs for Checkpoint 3.

#### Add logs in the executor
Log:

- order dequeued
- transaction id
- phase 1 started
- participant prepare response
- commit decision
- abort decision
- timeout or error

#### Add logs in the database module
Log:

- read request
- prepare write
- commit
- abort
- replication message sent
- replication message received
- stock before and after commit

#### Add logs in the payment service
Log:

- payment prepare
- payment commit
- payment abort
- reason for failure if it rejects

### 11. Create new tests and a new check script
The current repo only has Checkpoint 2 tests.

You need a new script, for example:

- `scripts/checkpoint3-checks.ps1`

#### Good minimum test cases
1. **Successful order**
   - order enters queue
   - leader dequeues it
   - payment prepares and commits
   - database prepares and commits
   - stock decreases on all replicas

2. **Insufficient stock**
   - database prepare says no
   - executor aborts
   - payment aborts
   - stock does not change

3. **Payment failure**
   - payment prepare says no
   - executor aborts
   - database aborts staged write

4. **Leader failover still works**
   - after one leader stops, another leader can still dequeue and run the transaction flow

5. **Read consistency check**
   - after a commit, all replicas show the same final stock

#### New test files likely needed
Examples:

- `test_checkout_cp3_success.json`
- `test_checkout_cp3_low_stock.json`
- `test_checkout_cp3_payment_fail.json`

### 12. Update documentation and diagrams
Checkpoint 3 requires new diagrams and documentation.

#### README changes needed
The root `README.md` should be updated so the first section becomes a Checkpoint 3 demo guide.

It should explain:

- how to start the services
- how to show one successful order
- how to show one aborted order
- how to show stock changes
- how to show commit and abort logs

#### New diagrams needed
1. **Consistency protocol diagram**
   - show the books database replicas
   - show the primary and backups, or your chosen design
   - show read and write flow

2. **Distributed commitment protocol diagram**
   - show executor as coordinator
   - show database module and payment service as participants
   - show `Prepare`, `Commit`, and `Abort`

#### Other documentation
Add a short system model section that explains:

- chosen consistency design
- chosen commitment protocol
- main trade-offs
- what happens on failure

#### Code-level documentation
Add short comments or docstrings in the most important new logic paths.

Good target areas:

- coordinator decision logic in `order_executor/src/app.py`
- participant staging and commit logic in the books database module
- participant staging and commit logic in the payment service
- timeout or recovery handling

### 13. Add the Checkpoint 3 tag
When the implementation is ready, create:

- `checkpoint-3`

This is explicitly required by the Checkpoint 3 page.

### 14. Commit the final Checkpoint 3 state
Before the evaluation, make sure the repository is not only working, but also committed in Git.

That means:

- review the changed files
- commit the latest implementation, tests, diagrams, and docs
- then create the `checkpoint-3` tag on that final commit

## Suggested implementation order
This order should reduce confusion and rework.

1. Extend the order data model so items and quantities are preserved.
2. Add the books database proto and service folders.
3. Add the payment proto and service folder.
4. Add the new services to Docker Compose.
5. Implement a simple primary-backup database path.
6. Implement 2PC in the executor.
7. Add logs.
8. Add new tests and the new PowerShell script.
9. Update README, diagrams, and code comments/docstrings.
10. Verify Docker Compose startup and the final demo flow.
11. Commit the final Checkpoint 3 state.
12. Create the `checkpoint-3` tag.

## File-level change map
Below is a practical map of the local repo files that most likely need to change.

### Existing files to modify
- `docker-compose.yaml`
- `frontend/src/index.html`
- `orchestrator/src/app.py`
- `order_executor/src/app.py`
- `order_queue/src/app.py`
- `utils/pb/order_queue/order_queue.proto`
- `README.md`

### New folders or files to add
- `books_db/` or `books_database/`
- `payment_service/`
- `utils/pb/books_db/books_db.proto`
- `utils/pb/payment/payment.proto`
- `scripts/checkpoint3-checks.ps1`
- new Checkpoint 3 test JSON files
- new Checkpoint 3 diagrams under `docs/diagrams/`

### Generated files that will also change
- generated gRPC Python files under `utils/pb/order_queue/`
- generated gRPC Python files for the new database proto
- generated gRPC Python files for the new payment proto

## Minimum definition of "ready for Checkpoint 3"
The repo is ready for Checkpoint 3 when all of these are true:

- a 3-replica books database exists
- the database has a clear consistency design
- a payment service exists
- the executor runs a real commitment protocol
- the order data includes real items and quantities
- a successful order updates stock
- a failed transaction aborts cleanly
- logs clearly show prepare / commit / abort behavior
- the important execution-path code is documented clearly enough for evaluation
- Docker Compose can bring up the Checkpoint 3 stack cleanly
- the README explains how to demo the system
- the 2 required diagrams exist
- the latest changes are committed
- the repo has a `checkpoint-3` tag

## Bonus readiness ideas
### Bonus 1: concurrent writes
To handle two orders updating the same book at the same time, the easiest solution is:

- serialize writes at the primary
- use per-book locks

This is much easier than trying to solve it later with a complex global lock.

### Bonus 2: failing participant recovery
A simple recovery idea:

- when a participant receives `Prepare`, save the staged transaction to a local file
- on restart, reload that file
- decide whether the transaction should still wait, abort, or ask for coordinator status

Even a small recovery mechanism can already help for bonus credit.

### Bonus 3: coordinator failure analysis
If you use 2PC, explain clearly that:

- 2PC can block
- participants may stay in an uncertain state if the coordinator dies after prepare

For the course, even a good written analysis of this issue can help.

## Final note
The good news is that the current repo already has the queue and the leader election layer. That is a strong base for Checkpoint 3.

The main work now is to move from:

- "the leader prints that it executed an order"

to:

- "the leader coordinates a real distributed transaction that updates replicated stock and payment state correctly"

## Audit update for commit `1ed359b`
This section is a later audit update for commit `1ed359b` (`1ed359b4320f82bb032417e7bfe8d0f771468351`).

The earlier sections in this file describe an older gap-analysis view of the repository. This new section records what a later audit found when the repository had already moved much closer to Checkpoint 3.

### Audit conclusion
Commit `1ed359b` looks **close to Checkpoint 3 base readiness**, but it does **not yet safely satisfy the full Checkpoint 3 brief including all bonus credit requirements**.

What is already present in commit `1ed359b`:

- a replicated `books_database` service with 3 replicas
- a `payment_service`
- a 2PC coordinator in `order_executor/src/app.py`
- Checkpoint 3 diagrams and protocol write-ups
- a Checkpoint 3 PowerShell check script
- item data now carried through the queue and executor path

So the repo is no longer just a Checkpoint 2 system. However, the audit found some important remaining issues.

### Main audit findings
#### 1. Restarted database replicas can come back with stale stock
This is the most serious technical issue found in the audit.

The `books_database` service starts from `SEED_STOCK`, and on restart it reloads only **pending** staged transactions from disk. It does **not** reload the latest committed stock values. Because of that:

- a restarted replica can come back with old stock values
- that replica can later win bully election again
- the system may temporarily treat stale state as the new primary state

The repository's own `scripts/checkpoint3-checks.ps1` already knows about this and works around it by forcing another checkout after restore so that replication brings all replicas back into sync.

Why this matters:

- it weakens the claim that the 3 replicas always behave like one logical database
- it makes the recovery story less trustworthy during a live evaluation

#### 2. The coordinator-failure write-up is stronger than the current queue implementation
The protocol documentation says that after coordinator failure, the queue can re-deliver an un-acked order so a new leader can continue the work.

But the current queue service only has:

- `Enqueue`
- `Dequeue`

and `Dequeue` removes the order from the queue immediately.

The queue does **not** currently show:

- an ack step
- a requeue step
- an "in-flight but not yet finished" order state

Why this matters:

- the written coordinator-failure story is partly based on behavior that is not fully implemented in the queue code
- this makes the coordinator-failure bonus weaker than the document suggests

#### 3. The required `checkpoint-3` tag is still missing
The Checkpoint 3 brief explicitly asks for a `checkpoint-3` Git tag.

At the time of the audit, that tag was not present on the remote repository state that matched commit `1ed359b`.

Why this matters:

- even if the implementation is mostly ready, the submission is not fully compliant yet

#### 4. The bonus-credit evidence is uneven
The repository has bonus-related code and tests, but the evidence is not equally strong for all bonus items.

What looks strongest:

- participant-failure recovery

What looks weaker:

- concurrent-write bonus proof
- coordinator-failure bonus proof

Why:

- the concurrent-writes test mostly prints timing observations instead of giving strong pass/fail assertions
- the main Checkpoint 3 verification script does not run the concurrent-writes test
- the coordinator-failure analysis is thoughtful, but part of its repo-specific recovery story depends on queue behavior that is not fully there

### What the audit says about Checkpoint 3 readiness
#### Base Checkpoint 3
For the **base** Checkpoint 3 goals, commit `1ed359b` looks **mostly ready**:

- consistency protocol exists
- distributed commitment protocol exists
- new service exists
- logs exist
- diagrams exist
- documentation exists
- Docker Compose setup exists

So if the question is only "is there a serious Checkpoint 3 implementation here?", the answer is **yes**.

#### Full Checkpoint 3 including all bonus credit
For the stronger question "does commit `1ed359b` sufficiently satisfy the Checkpoint 3 brief, including all bonus credit requirements?", the audit answer is **no, not confidently**.

The main reasons are:

- stale-state risk after DB replica restart
- queue/recovery mismatch in the coordinator-failure story
- missing `checkpoint-3` tag
- weaker proof for some bonus items than for others

### Very short fix-before-submission checklist
If the team wants commit `1ed359b` to become a stronger final Checkpoint 3 submission, these are the most important remaining tasks:

1. Fix database recovery so a restarted replica reloads committed stock state, not only pending staged transactions.
2. Either implement real queue redelivery / in-flight order recovery, or reduce the coordinator-failure write-up so it matches the current code honestly.
3. Strengthen the concurrent-writes bonus proof with a clearer pass/fail test and add it to the main Checkpoint 3 verification flow.
4. Re-check the README demo instructions so they point to the Checkpoint 3 verification path, not only the older Checkpoint 2 script.
5. Create the required `checkpoint-3` Git tag on the final approved submission commit.

### Practical team note
This audit section should be treated as the more up-to-date status note for commit `1ed359b`.

The older sections above are still useful because they explain the original design direction, but they no longer describe the repository's later Checkpoint 3 implementation state exactly.

## Audit update for commit `6f00e05`
This section is a later audit update for commit `6f00e05` (`6f00e05100d7b6919a80070893f374206235fe5f`) on branch `individual-sten-qy-li`.

This audit was done after the fixes from the earlier `1ed359b` audit had already been merged. So this section focuses only on the **remaining** gaps between commit `6f00e05` and a fully clean Checkpoint 3 submission, including bonus-credit readiness.

### Short answer
Commit `6f00e05` is **much closer** to full Checkpoint 3 readiness than `1ed359b`.

The strongest improvements compared with the earlier audit are:

- the books database now persists committed `kv_store` state to disk, so restarted replicas no longer come back only with `SEED_STOCK`
- the concurrent-writes test now has real pass/fail assertions plus replica-convergence checks
- the commitment-protocol document now contains an honesty note that the queue has no ack / requeue mechanism
- backup replication now uses cached gRPC channels, which makes the concurrent-writes path more stable under load

So the earlier stale-replica-state problem is now closed, and the repository is no longer missing any large Checkpoint 3 implementation block.

### What changed between `1ed359b` and `6f00e05`
The new commit is mainly a gap-closure pass on top of the earlier Checkpoint 3 implementation.

The most important changes are:

1. **Database restart state fixed**
   - committed stock is now flushed to `kv_store.json`
   - startup loads from disk or falls back to `SEED_STOCK`
   - this closes the earlier problem where a restarted replica could come back with stale stock

2. **Concurrent-writes bonus evidence strengthened**
   - the code still uses per-key locking
   - the dedicated concurrent-writes test is now assertive instead of descriptive
   - the test now checks cross-replica convergence too

3. **Coordinator-failure write-up made more honest**
   - the document now explicitly says that `Dequeue` is destructive
   - it also says there is no ack / nack / visibility-timeout mechanism in the queue

4. **Replication path made more robust**
   - backup replication channels are now reused instead of created fresh on every call
   - this is especially useful when several writes happen in parallel

### Rubric-style status at `6f00e05`
#### Base Checkpoint 3 items
For the six main rubric items, commit `6f00e05` looks covered:

- consistency protocol and database module: covered
- distributed commitment protocol and new service: covered
- logging: covered
- project organization and documentation: covered
- consistency protocol diagram: covered
- distributed commitment protocol diagram: covered

So, in rubric terms, the code/documentation side looks like:

- **base subtotal: 10 / 10**

#### Bonus items
The three clearly identifiable bonus tasks from the Session 10 and Session 11 pages look like this:

- concurrent write handling: covered
- failing participant recovery: covered
- coordinator-failure analysis: covered

That gives a likely bonus picture of:

- **bonus subtotal: 2.25 / 3.0**

One more bonus detail is still unclear:

- the main Checkpoint 3 rubric page allows up to 3 bonus points
- but the source pages we reviewed clearly spell out only 3 concrete bonus tasks
- so there may be no real 4th task, or it may exist on a child page we did not extract clearly

So the practical reading is:

- the repo looks strong on the 3 clear bonus tasks
- the last 0.75 points remain uncertain because the existence of a 4th concrete task is not clear from the reviewed pages

### Remaining gaps in commit `6f00e05`
#### Category A: hard requirements to finish before the evaluation
##### 1. The required `checkpoint-3` Git tag is still missing
This is the clearest remaining Checkpoint 3 requirement gap.

The Checkpoint 3 brief explicitly says to create a `checkpoint-3` tag on the repository. At the time of this audit, the remote branch state matched commit `6f00e05`, but the remote repository still did **not** have a `checkpoint-3` tag.

Why this matters:

- this is part of the formal Checkpoint 3 handoff
- even if the implementation is strong, the submission is still not fully complete without the required tag

##### 2. The evaluation slot still needs to be booked
This is not a code gap, but it is still part of the course-side Checkpoint 3 process.

Why this matters:

- the course page explicitly says teams must choose their evaluation slot
- missing this does not weaken the code, but it weakens actual checkpoint readiness

#### Category B: documentation and demo-flow polish
##### 3. The top README demo guide still points to the Checkpoint 2 script instead of the Checkpoint 3 script
The root `README.md` is labelled as a Checkpoint 3 document, but the very first demo section still tells the reader to run:

- `scripts/checkpoint2-checks.ps1`

instead of the Checkpoint 3 verification script:

- `scripts/checkpoint3-checks.ps1`

Why this matters:

- the first section of the README is supposed to help the teaching assistants see the Checkpoint 3 functionality quickly
- the current wording still points them toward a Checkpoint 2 verification path
- this weakens the demo-readiness and documentation-quality part of the checkpoint

##### 4. The commitment-protocol document is much better now, but one stale sentence still remains
The document `docs/commitment-protocol.md` now correctly includes an honesty note that the queue has no ack / nack / visibility-timeout mechanism and that `Dequeue` is destructive.

That is a clear improvement.

But later in the same document, the summary still says the system is partially mitigated by:

- `redelivery from order_queue`

This sentence is still not true for the current implementation.

Why this matters:

- the coordinator-failure analysis bonus is mostly a documentation-and-reasoning task
- the document is now strong overall, but it is still not perfectly internally consistent
- cleaning up this one sentence would make the analysis more trustworthy

##### 5. The Checkpoint 3 script still contains stale comments from the old restart-state issue
The code in `books_database/src/app.py` now persists committed stock state properly.

However, `scripts/checkpoint3-checks.ps1` still contains comments that describe the old problem where a restored primary could come back with stale seed values.

Why this matters:

- this is now a documentation/tooling consistency issue, not a code correctness issue
- it can still confuse the team or the evaluators during final review

#### Category C: optional polish that would make the bonus story stronger
##### 6. The main Checkpoint 3 verification script still does not cover the concurrent-writes bonus
The concurrent-writes test is now much stronger than before, which is a real improvement.

But `scripts/checkpoint3-checks.ps1` still does **not** run that concurrent-writes test as part of the main reusable verification flow.

So the repository currently has:

- bonus implementation evidence in a dedicated Python test

but it does not yet have:

- one main Checkpoint 3 verification flow that also proves the concurrent-writes bonus in the same reusable demo script

Why this matters:

- this does not necessarily break the implementation itself
- but it makes the bonus-credit demo and testing story less complete than it could be
- for evaluation, it is cleaner if the main script can show the main bonus claim too

##### 7. There is still no explicit automated test just for `kv_store.json` restart persistence
The code fix for committed stock persistence looks real and important.

But the repository still does not have one small direct test that says:

- write stock
- restart replica(s)
- read the post-commit value back
- confirm startup log shows load-from-disk behavior

Why this matters:

- this is not strictly required by the rubric
- but it would make the phase-14 fix more directly demonstrable

##### 8. Coordinator failure is analysed well, but not solved operationally
This is a nuance, not a contradiction.

The course page for the coordinator-failure bonus says:

- analyse the consequences of coordinator failure
- think of a solution
- no implementation is needed

So commit `6f00e05` may already be **good enough** to earn this bonus if the teaching assistants accept the analysis quality.

But if the team wants to say that the repository not only analyses coordinator failure, but also handles it robustly in practice, there is still a real gap:

- there is no durable coordinator-side decision log
- there is no replacement-leader recovery of unfinished decisions
- there is still no queue ack / redelivery layer

Why this matters:

- for the formal course bonus, this may already be acceptable
- for a stricter engineering reading, it is still not a fully implemented recovery solution

### What this means for Checkpoint 3 readiness
#### Base Checkpoint 3
For the **base** Checkpoint 3 requirements, commit `6f00e05` now looks **complete in implementation terms**.

The remaining base-like gaps are mostly submission and presentation items:

- missing `checkpoint-3` tag
- evaluation slot not yet booked
- README demo section still points to the wrong script

#### Bonus-credit readiness
For the **bonus-credit** side, the picture is stronger than before:

- concurrent-writes bonus: implementation plus dedicated assertive test exist
- participant-failure bonus: looks strong
- coordinator-failure bonus: the analysis is strong and much more honest than before

So the remaining bonus gaps are mostly:

- packaging the evidence better
- removing one documentation contradiction
- deciding whether it is worth chasing any possible 4th bonus task

### Predicted rubric picture
If the remaining release-day and documentation items are cleaned up, the likely outcome looks like:

- base: **10 / 10**
- bonus: **2.25 / 3.0**
- likely total: **12.25 / 13.0**

That estimate assumes:

- the `checkpoint-3` tag is created
- the evaluation slot is booked
- the teaching assistants accept the three clear bonus tasks as completed

If a real 4th bonus task exists and the team wants to pursue it, then the most realistic directions would be:

- durable coordinator decision logging
- queue ack / redelivery
- another clearly identifiable advanced protocol extension

### Very short fix-before-submission checklist for commit `6f00e05`
If the team wants to close the remaining gaps from commit `6f00e05`, the shortest useful checklist is:

1. Create the required `checkpoint-3` tag on the final approved submission commit.
2. Book the evaluation slot.
3. Update the first section of `README.md` so it points to `scripts/checkpoint3-checks.ps1` for the Checkpoint 3 demo flow.
4. Clean up `docs/commitment-protocol.md` so every section consistently says the queue has no redelivery mechanism.
5. Remove or update the stale comments in `scripts/checkpoint3-checks.ps1` so the script text matches the current fixed database-restart behavior.
6. Optionally add the concurrent-writes test to the main Checkpoint 3 verification flow.
7. Optionally add a small direct restart-persistence test for `kv_store.json`.

### Practical conclusion
Compared with the earlier `1ed359b` audit, commit `6f00e05` appears to have closed the biggest technical gap and strengthened the bonus evidence noticeably.

What remains now is mostly:

- release/tag completeness
- README/demo polish
- documentation consistency
- optional bonus-verification polish

So this is no longer a "big missing implementation" situation. It is now mainly a "finish the release-day details and tighten the presentation" situation.

## Commit `daf4812` audit results
This section records the later audit of commit `daf4812` (`daf4812467734c411c8bbf460b65998adf2f7a7b`) on branch `individual-sten-qy-li`.

The team plans to handle the final `checkpoint-3` tag only after team lead peer review and merge into `master`, so this section focuses only on the two remaining **repository-side** findings that still matter before final submission quality is reached.

### Short answer
Commit `daf4812` is strong enough to be sent to the team lead for human peer review.

The earlier `2c42158` documentation gaps were closed in this commit:

- the coordinator-failure write-up is now internally consistent
- the CP2 README limitation text is now properly scoped
- the main Checkpoint 3 script now includes the concurrent-writes bonus test

However, two smaller gaps still remain:

- the concurrent-writes bonus is implemented, but the **full reusable CP3 verifier** can still fail it after the DB failover / restore path
- the top README demo text is now slightly out of date, because it still describes the older check count and bonus list

### Finding 2: concurrent-writes bonus can still fail inside the full CP3 verifier
This is the more important of the two remaining repo-side findings.

The dedicated concurrent-writes test itself is real and useful:

- it passes when run by itself
- it checks same-key serialization
- it checks different-key parallel writes
- it checks replica convergence too

So the per-key-lock design is not the main problem.

The remaining problem is that the **main Checkpoint 3 script** now runs that test immediately after the DB primary failover / restore flow. In that sequence, the restored DB can briefly be in an unstable election state.

What the audit observed:

- the script first completed the DB failover step successfully
- then the concurrent-writes step found `DB-3` as the primary address
- but the same replica still replied to `Read` / `Write` with `not primary; primary=None`

That means the most likely remaining issue is a short **leader-stabilization race** after restore, not a broken concurrent-writes algorithm.

### Most reliable way to reproduce Finding 2
The most reliable way is **not** to run the standalone Python bonus test by itself.

That standalone test often passes, because by then the DB election has already settled.

The most reliable reproduction path is to run the **full reusable Checkpoint 3 verifier**, because it includes the failover / restore sequence that exposes the race:

```powershell
.\scripts\checkpoint3-checks.ps1 -SkipBuild
```

Why this is the best reproducer:

- it tears the stack down and starts it again
- it runs the DB failover test
- it restores the old primary
- it then continues into the concurrent-writes bonus inside the same end-to-end flow

If the race appears, the clearest failure signature is:

- the script prints `bonus:concurrent-writes`
- the test prints `primary = DB-3 @ 127.0.0.1:50060`
- then multiple `Read` / `Write` operations fail with `not primary; primary=None`

This reproduction path is better than:

- `python books_database/tests/test_concurrent_writes.py`

because that standalone command usually runs after the system is already stable again.

### What this means technically
At commit `daf4812`, the concurrent-writes bonus is in a mixed state:

- **algorithm side:** looks good
- **standalone proof:** looks good
- **main reusable demo flow:** still not fully reliable

So this is now more of a **demo-stability and orchestration** gap than a core implementation gap.

### Finding 3: the top README demo text is now slightly outdated
The root `README.md` is much better than before, but one small mismatch remains.

The first demo section still says:

- the script should produce **18 checks**
- and it names the participant-failure bonus only

But the script now includes the concurrent-writes bonus too, so the first-section wording no longer matches the current verification flow exactly.

Why this matters:

- this is the first section the teaching assistants are likely to read
- it is supposed to be the fastest memory-jogger for the live demo
- if the wording does not match the actual script, the team may lose time explaining the mismatch during evaluation

This is a small documentation gap, not a code-quality gap.

### Practical conclusion for `daf4812`
For peer review, commit `daf4812` looks ready.

For final TA-facing submission quality, the remaining repository-side work is now very small:

1. make the concurrent-writes bonus reliable inside the full `scripts/checkpoint3-checks.ps1` flow, especially after DB primary restore
2. update the first README demo section so it matches the current CP3 script output and bonus coverage

So the repository is now at the stage where the remaining work is mostly:

- race-proofing the final demo flow
- tightening one README summary

That is a much better situation than having a large missing protocol or service.

### Suggested plan to address Findings 2 and 3
The most practical next step is to treat Finding 2 as a **stabilization problem** and Finding 3 as a **README cleanup problem**.

#### Suggested plan for Finding 2
I would suggest addressing Finding 2 in this order:

1. **First, harden the reusable CP3 script.**
   Add a short readiness gate after the DB restore path and before the concurrent-writes bonus starts.

2. **Use a stronger readiness condition than only `WhoIsPrimary`.**
   Right now, the race appears because one replica can already report `leader_id=3`, while the restored DB-3 still has not fully entered leader mode.

3. **Recommended readiness rule:**
   only start the concurrent-writes bonus after the chosen primary:
   - reports itself as primary
   - successfully serves a normal primary-only `Read` or `Write`
   - keeps the same primary identity for a short stability window, for example 2 or 3 consecutive checks

4. **If that is still not enough, then strengthen the DB-side election behavior.**
   The script-side gate is the simpler first fix. If the race still survives that, then the next step should be to tighten the `books_database` leader-transition logic itself so a restored node does not appear externally ready before `is_leader` is fully true and usable.

Why this is my preferred plan:

- it keeps the first fix small
- it targets the exact observed failure mode
- it improves the live demo flow directly
- it does not force a larger redesign unless that is truly needed

#### Suggested plan for Finding 3
For the README mismatch, I would suggest a small but precise edit to the very first demo section:

1. update the expected check count so it matches the current script output
2. explicitly mention both bonus checks now covered by the script
3. keep the wording short, so the first section remains a quick teaching-assistant memory jogger

The best outcome would be a first demo section that tells the reader, in one glance:

- which script to run
- what kind of checks it covers
- what successful output roughly looks like

#### Suggested implementation order
If the team decides to make these fixes, I would suggest this order:

1. fix Finding 2 first, because it affects demo reliability
2. rerun the full CP3 script until the concurrent-writes step is stable
3. then fix Finding 3 so the README matches the now-stable script behavior

This order is better because the README wording should describe the final verified behavior, not a temporary intermediate state.

## Commit `bc2a8cd` audit results
This section records the later audit of commit `bc2a8cd` (`bc2a8cd31c1c0bfabba805c945a86f8629f2a9e5`) on branch `individual-sten-qy-li`.

Compared with commit `daf4812`, this newer commit appears to close the two repo-side issues recorded above:

- the concurrent-writes test now uses a stronger stable-primary gate
- the root README first demo section now matches the newer Checkpoint 3 script wording better

However, the later audit found one new remaining repository-side issue.

### Finding 2: participant-failure bonus path is still brittle in the full CP3 verifier
The old concurrent-writes problem looks much better at `bc2a8cd`, but the **full reusable Checkpoint 3 verifier** still does not pass end-to-end reliably because the participant-failure bonus path is still fragile.

What the audit observed:

- the full `scripts/checkpoint3-checks.ps1 -SkipBuild` flow could still stop in `bonus:participant-failure-recovery`
- the standalone `order_executor/tests/test_2pc_fail_injection.py` path could also fail
- the failure was not mainly about the 2PC retry logic itself
- instead, the fragile part was the assumption that recreated `books_database_3` would quickly become the DB primary again

The most likely explanation is:

- the participant-failure test probes DB leadership too optimistically
- and the DB bully-election behavior does not strongly guarantee that a restarted higher-ID replica will immediately reclaim leadership from a lower-ID replica that is already active

So this finding should be read as:

- **the earlier `daf4812` Findings 2 and 3 look closed**
- **but a different demo-reliability issue still remains at `bc2a8cd`**

### Why this matters
This is important because the participant-failure recovery path is one of the main bonus-credit proof points.

If the team wants the repository to look strong not only in code, but also in repeatable teaching-assistant demo conditions, then this path should be made stable enough that:

- the standalone fail-injection test can be rerun cleanly
- the main Checkpoint 3 PowerShell verifier can also finish cleanly

### Suggested next step from this audit
The most useful follow-up is:

1. harden the participant-failure test with the same kind of stable-primary gate used for the concurrent-writes test
2. tighten the DB bully-election behavior so a restarted higher-ID DB replica can reclaim leadership more reliably
3. rerun the full Checkpoint 3 verifier after those two fixes

### Follow-up after the local fix
The current local working tree now appears to close this archived `bc2a8cd` Finding 2.

What was changed locally:

- the DB bully-election path was tightened so a restarted higher-ID replica can challenge a lower active leader
- the participant-failure test now waits for a **stable usable primary**, not just the first reported leader id

What was rechecked locally after the fix:

- `python order_executor/tests/test_2pc_fail_injection.py`
- `.\scripts\checkpoint3-checks.ps1 -SkipBuild`

Result of the local recheck:

- the standalone participant-failure bonus test passed
- the full Checkpoint 3 verifier passed all **19 / 19** checks

So, at least in the current local repository state, the earlier `bc2a8cd` participant-failure demo-stability problem looks fixed.
