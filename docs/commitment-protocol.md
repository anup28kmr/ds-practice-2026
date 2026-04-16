# Distributed Commitment Protocol: 2PC and Coordinator-Failure Analysis

This note is the design write-up for the two-phase commit (2PC)
protocol used in Checkpoint 3. §§1–2 are the short "what the protocol
does" primer; §§3–5 are the §5.4 coordinator-failure bonus analysis.
Participant-failure is not analysed here because it is handled in code
and demonstrated by
[order_executor/tests/test_2pc_fail_injection.py](../order_executor/tests/test_2pc_fail_injection.py)
and
[order_executor/tests/test_2pc_crash_recovery.py](../order_executor/tests/test_2pc_crash_recovery.py).
See also the sequence diagram at
[docs/diagrams/commitment-protocol.svg](diagrams/commitment-protocol.svg).

## 1. Roles in this repository

| Role | Service | Source |
|---|---|---|
| Coordinator | Leader `order_executor` (only the bully-elected leader runs `run_2pc`) | [order_executor/src/app.py](../order_executor/src/app.py) |
| Participant 1 | `books_database` primary replica | [books_database/src/app.py](../books_database/src/app.py) |
| Participant 2 | `payment_service` | [payment_service/src/app.py](../payment_service/src/app.py) |

The executor leader dequeues an order, runs `run_2pc(order)`, and the
protocol touches both participants. The other two executors are hot
standbys for the dequeue role but **today they do not take over an
in-flight 2PC round** — §4.2 below explains the gap.

## 2. Protocol trace (happy path)

```
  executor (coordinator)                  books_database primary        payment_service
  ----------------------                  ----------------------        ---------------
  log 2pc_start
  Prepare(order, items) ------------------>
                                          persist /app/state/txn_*.json
                                          pending_orders[order]=items
                                          vote_commit <-----------------
  Prepare(order, amount) ------------------------------------------------->
                                                                        prepared[order]=amt
                                                                        <-- vote_commit
  log 2pc_decision=COMMIT                  (decision record in stdout only)
  Commit(order) --------------------------->
                                          apply + replicate to backups
                                          committed_orders.add(order)
                                          remove /app/state/txn_*.json
                                          <-- success
  Commit(order) ------------------------------------------------------->
                                                                        committed.add(order)
                                                                        <-- success
  log 2pc_commit_applied
```

Phase 1 decides. Phase 2 enacts. The decision log line (`2pc_decision=...`)
is written **before** phase 2 so every 2PC round leaves a human-readable
audit point. That line is the "decision record" in the 2PC literature
but, as §4 explains, writing it only to stdout is insufficient for full
coordinator-failure recovery.

## 3. What can go wrong with the coordinator

Call the four timing windows `W1..W4`:

| Window | When | State of participants |
|---|---|---|
| W1 | Coordinator crashes **before** any Prepare is sent | Nothing staged. No blocking. |
| W2 | Coordinator crashes **after sending some Prepares, before writing the decision** | Some participants are in `prepared`. They are waiting and hold reservations. |
| W3 | Coordinator crashes **after writing the decision to stdout, before sending any phase-2 RPC** | Participants are still in `prepared`. The decision exists only in memory/log buffers of a dead process. |
| W4 | Coordinator crashes **after sending the phase-2 RPC to *one* participant but not the other** | One participant committed (or aborted); the other is still `prepared`. Their views diverge. |

W1 is harmless. W2, W3, and W4 are all variants of the classic "2PC
blocking" problem described below.

### 3.1 The blocking problem (why participants must wait)

A participant in `prepared` state knows it voted commit and the coordinator
has the authority to either commit or abort. It does **not** know which
one the coordinator decided. It cannot safely do either of these:

- **Unilaterally commit.** If the coordinator actually decided abort, a
  unilateral commit violates atomicity: `books_database` would decrement
  stock that the payment side never billed.
- **Unilaterally abort.** If the coordinator actually decided commit and
  the other participant already committed, a unilateral abort also
  violates atomicity: `payment_service` has a committed payment for an
  order whose stock reservation got dropped.

The only safe action is **wait**. That is the "blocking": while the
coordinator is unreachable, the prepared participant holds its
reservation (a row/pending entry) and blocks anyone else who needs the
same resource. In our system a blocked `books_database` Prepare keeps the
book in `pending_orders`, which reduces the effective stock for every
subsequent Prepare until the blocked transaction is resolved (see the
`reserved` calculation in `BooksDatabaseService.Prepare`,
[books_database/src/app.py](../books_database/src/app.py)).

### 3.2 W4 specifically: divergent participant views

W4 is the worst case. Assume the coordinator sent `Commit` to
`books_database` and died before sending `Commit` to `payment_service`:

- `books_database` committed: stock is decremented and the pending entry
  is cleared. `committed_orders` contains the order id.
- `payment_service` is still in `prepared`. It is waiting for the
  coordinator and has no way to know a commit already happened
  elsewhere.

A replacement process cannot tell these two apart from the outside
without reading at least one participant's state or the coordinator's
decision log. This is exactly the scenario 3PC and similar protocols
are designed to survive.

## 4. What our repo does and does not handle

### 4.1 What works today

- **Participant side is fully recoverable.** `books_database` persists
  its staged transaction to
  `/app/state/txn_<order>.json` before voting commit, reloads the file
  on startup (`recovered_pending` log line), and refuses to commit an
  order it has no record of (`commit_unknown`) so a freshly elected
  primary during a retry window cannot silently mis-commit. See
  [order_executor/tests/test_2pc_crash_recovery.py](../order_executor/tests/test_2pc_crash_recovery.py)
  for the full demo.
- **Coordinator-side retry for participant transients.** `run_2pc` has a
  12-attempt / ~40-second retry budget on `Commit` with primary re-
  discovery between attempts. This covers the common case of a flaky
  or briefly-restarted participant.
- **Hot standby coordinators exist structurally.** The three executors
  run the same bully-election pattern as the databases. If the leader
  dies, one of the other two will be elected within `LEADER_TIMEOUT`
  (5s).

> **Honesty note on queue redelivery.** The text above describes the
> executor failover that *is* implemented. What is **not** implemented
> is automatic queue redelivery: `Dequeue` is a destructive `popleft()`
> in [order_queue/src/app.py](../order_queue/src/app.py) with no ack /
> nack / visibility-timeout mechanism. If the coordinator dies after
> dequeuing an order but before completing 2PC, the order is lost from
> the queue and will not be re-offered to a replacement leader. Our
> coordinator-failure recovery therefore depends on either (a) the
> original leader being restarted quickly enough to finish its retry
> loop, or (b) the user re-submitting the order. A production system
> would add an explicit ack (e.g. `AckOrder(order_id)`) so the queue
> only removes the message once the coordinator confirms completion.

### 4.2 What is still a gap

The executor leader holds the 2PC decision only in **stdout** (the
`2pc_decision=COMMIT|ABORT` line is a print, not a durable write). If
the leader dies in W3 or W4, the replacement leader elected by bully:

1. Does not read any decision record, because none is persisted.
2. Does **not** automatically pick up the in-flight order, because
   `Dequeue` already removed it from the queue and there is no ack /
   requeue mechanism (see the §4.1 honesty note). The order is
   therefore either (a) re-offered when the original leader restarts
   within its retry window, or (b) resubmitted by the user. Either way,
   the replacement path re-enters `run_2pc` from scratch for that
   order.
3. On the re-run, `Prepare` to `books_database` is **idempotent** (the
   order_id is already in `pending_orders` or in `committed_orders`),
   and `Prepare` to `payment_service` is idempotent (`prepared[order]`
   returns "already prepared"). So vote gathering still works.
4. On the re-run, `Commit` is safe: `books_database` returns
   `commit_idempotent` if the order is already in `committed_orders`,
   and `payment_service` returns `already committed` on retry. So the
   final outcome converges.

So in practice, **the replacement coordinator in our system lands on the
correct outcome by retrying Phase 1 and relying on participant
idempotency**, *as long as the original coordinator got at least one
participant to commit and the order re-enters `run_2pc` via a leader
restart or a user resubmit*. The blocking case our code does not
resolve is W4 where `books_database` committed but `payment_service`
is still prepared and the original decision was COMMIT: once the order
re-enters `run_2pc`, the new leader will re-Prepare,
`payment_service` will re-vote commit, the new leader will re-decide,
and eventually Commit will reach `payment_service`. That is correct,
but it only works because the demo participants are
idempotent-by-design and the payment side has no real money at stake.
A participant that cannot safely accept a second Prepare after its first
Commit would not be saved by this scheme.

The honest summary: our system handles coordinator failure *in the
demo-happy cases* through participant idempotency + bully re-election
+ leader-restart-or-user-resubmit as the redelivery trigger (the
queue itself does **not** redeliver, per §4.1), not through a true
decision-record recovery protocol.

## 5. Mitigations from the literature (what a hardened system would add)

### 5.1 Three-phase commit (3PC)

3PC inserts a **PreCommit** phase between `Prepare` and `Commit`. After
voting commit, a participant moves to `prepared`; once it receives
`PreCommit`, it moves to `pre-committed`. A participant in
`pre-committed` state is guaranteed that every live participant also
voted commit, so if the coordinator dies, the remaining participants
can elect a new coordinator and **safely commit** on their own. If
nobody reached `pre-committed`, they can safely abort. 3PC is non-
blocking under pure crash failures, at the cost of one extra RPC round
per transaction. It is *not* non-blocking under network partitions —
partitioned participants cannot distinguish a partition from a crash.

For this repo, adding 3PC would mean one extra RPC between Prepare and
Commit in `run_2pc` and an extra state (`pre_committed_orders`) on each
participant. The decision record would still have to be durable, but
any participant in `pre-committed` could replay that decision for a
replacement coordinator.

### 5.2 Replacement coordinator via bully + durable decision log

A cheaper, more pragmatic mitigation is to keep 2PC but make the
coordinator side crash-recoverable:

1. **Durable decision log on the coordinator.** Before sending any
   phase-2 RPC, the leader executor writes a record like
   `/app/executor_state/decision_<order>.json` containing
   `{"order_id": ..., "decision": "COMMIT", "participants": [...]}`.
   This mirrors the participant-side persistence from Phase 6.
2. **Bully re-election already exists.** We reuse the existing executor
   bully election (the 3 order_executor replicas elect a new leader on
   a `LEADER_TIMEOUT` miss).
3. **Recovery on promotion.** When an executor becomes leader, it scans
   `/app/executor_state/` for any `decision_*.json` whose corresponding
   order has not been marked as complete, and resumes phase 2 by calling
   `Commit` or `Abort` on both participants. Participant idempotency
   (which we already implement) makes this safe.

This is the "highest-ID replacement coordinator" mitigation the Session
11 lecture mentioned, instantiated for our specific topology. It is
still blocking while no replacement leader has been elected (~5s under
our current timeouts), but it does not block forever and does not
require 3PC's extra round on the happy path.

### 5.3 Cooperative termination (peer-to-peer recovery)

Participants can also resolve W4-style uncertainty by talking to each
other. If `payment_service` is in `prepared` and loses contact with the
coordinator, it can ask `books_database` "did you commit order X?" If
`books_database` says yes (`order X in committed_orders`),
`payment_service` can safely commit. If it says "I aborted" or "I never
saw it", the other can abort. Cooperative termination only resolves
cases where at least one participant already knows the decision, so it
complements (does not replace) durable decision logging.

### 5.4 Consensus-based commit (Paxos Commit)

The strongest mitigation replaces the single coordinator with a
replicated state machine (Paxos/Raft). The decision itself becomes a
consensus value, so there is no single point of failure. This is
significantly more code than our current executor tier and is out of
scope for Checkpoint 3; we mention it only for completeness.

## 6. Summary

- Coordinator failure in 2PC is the classic blocking problem: a
  participant in `prepared` cannot unilaterally commit or abort, so it
  waits on the coordinator.
- Our repo partially mitigates this via (a) bully re-election of a
  new leader executor and (b) idempotent Prepare/Commit/Abort on both
  participants. As flagged in the §4.1 honesty note, the `order_queue`
  itself does **not** redeliver an in-flight order to a new leader —
  `Dequeue` is destructive with no ack/nack — so recovery in practice
  depends on either the original leader being restarted quickly enough
  to finish its retry loop, or the user resubmitting the order. Given
  that, the demo converges to a correct outcome after a coordinator
  crash without code changes *as long as participants tolerate a
  second Prepare*, which ours do.
- A fully hardened system would additionally (a) persist the decision
  record on the coordinator before phase 2, (b) have the replacement
  leader scan for unfinished decisions and replay phase 2, and/or (c)
  move to 3PC or Paxos Commit for true non-blocking termination.
- Phase 6 already demonstrates participant-side recovery. Coordinator-
  side decision-record persistence is a natural next step and is
  flagged here as the concrete follow-up work needed to close the W3/W4
  gap.
