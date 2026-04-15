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
- create the `checkpoint-3` tag on that final commit

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
