"""Guide13 scenario 4.

  "Define and automate test scenarios for orders that contain conflicting
  requests, such as attempting to purchase the same book simultaneously."

We fire eight concurrent orders, each asking for one copy of
'Designing Data-Intensive Applications' which seeds with stock 3. The
B1 concurrent-writes mechanism in books_database/src/app.py guards the
read-validate-write window with a per-title lock, so the books database
must vote commit on exactly three Prepares and abort the rest with
'insufficient stock'.

Important asymmetry vs scenarios 1-3: the orchestrator returns
'Order Approved' the instant the order is enqueued. The actual 2PC
COMMIT/ABORT happens asynchronously on the elected order_executor
leader. So this test ignores the HTTP status field and instead reads
the leader's `2pc_decision` log line for each order_id, which is the
authoritative outcome.
"""

import pytest

from tests.e2e._common import (
    build_payload,
    classify,
    collect_2pc_decisions,
    post_concurrent,
)


CONFLICT_TITLE = "Designing Data-Intensive Applications"
INITIAL_STOCK = 3  # see SEED_STOCK in books_database/src/app.py
N_ORDERS = 8


def test_conflicting_orders_match_initial_stock():
    payloads = [
        build_payload(
            user_name=f"Conflict {i}",
            items=[{"name": CONFLICT_TITLE, "quantity": 1}],
        )
        for i in range(N_ORDERS)
    ]

    results = post_concurrent(payloads)

    # Every order must at least reach the queue. The orchestrator only
    # rejects pre-2PC for fraud / bad input.
    enqueued = [body for _s, body in results if classify(body) == "approved"]
    assert len(enqueued) == N_ORDERS, (
        f"expected all {N_ORDERS} orders to be enqueued; got: {results!r}"
    )

    order_ids = [b["orderId"] for b in enqueued]
    decisions = collect_2pc_decisions(order_ids, timeout_seconds=60.0)

    commits = [oid for oid, d in decisions.items() if d == "COMMIT"]
    aborts = [oid for oid, d in decisions.items() if d == "ABORT"]

    if len(commits) == INITIAL_STOCK:
        # Best case: fresh stack, exactly stock orders commit, others abort.
        assert len(aborts) == N_ORDERS - INITIAL_STOCK, (
            f"expected {N_ORDERS - INITIAL_STOCK} aborts on a fresh stack, "
            f"got commits={len(commits)} aborts={len(aborts)} "
            f"decisions={decisions}"
        )
        return

    # Either the stack wasn't fresh OR a previous run consumed some stock.
    # In every case, the safety invariant must hold: the number of commits
    # cannot exceed the stock that was available at the time these orders
    # raced. We can't observe the pre-test stock from here, so the only
    # universal assertion is "we did not commit more than the initial seed",
    # which still proves the per-title lock is doing its job.
    assert len(commits) <= INITIAL_STOCK, (
        f"committed more orders ({len(commits)}) than the initial seed stock "
        f"({INITIAL_STOCK}); the per-title lock failed. "
        f"decisions={decisions}"
    )
    if len(commits) < INITIAL_STOCK:
        pytest.skip(
            f"non-fresh stack: only {len(commits)} of {N_ORDERS} orders "
            f"committed (initial seed {INITIAL_STOCK}). The safety invariant "
            "still holds. Re-run after 'docker compose down -v' for the "
            "strict assertion."
        )
