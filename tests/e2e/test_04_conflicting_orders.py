"""Guide13 scenario 4.

  "Define and automate test scenarios for orders that contain conflicting
  requests, such as attempting to purchase the same book simultaneously."

We fire eight concurrent orders, each asking for one copy of
'Designing Data-Intensive Applications' (seed stock 3). The B1
concurrent-writes mechanism in books_database/src/app.py guards the
read-validate-write window with a per-title lock, so the books database
votes commit on at most `current_stock` Prepares and aborts the rest
with 'insufficient stock'.

Important asymmetry vs scenarios 1-3: the orchestrator returns
'Order Approved' the instant the order is enqueued. The actual 2PC
COMMIT/ABORT happens asynchronously on the elected order_executor
leader. So this test ignores the HTTP status field and instead reads
the leader's `2pc_decision` log line for each order_id, which is the
authoritative outcome.

The test reads the *live* stock at the start rather than assuming the
seed value, because the CP3 verifier's `bonus:concurrent-writes` test
overwrites unrelated keys including this one and would otherwise leave
the stack with stock != seed. The safety invariant we actually want to
prove is "commits never exceed available stock", which is correct under
any starting stock.
"""

from tests.e2e._common import (
    build_payload,
    classify,
    collect_2pc_decisions,
    post_concurrent,
    read_stock_quorum,
)


CONFLICT_TITLE = "Designing Data-Intensive Applications"
N_ORDERS = 8


def test_conflicting_orders_match_initial_stock():
    pre_stock = read_stock_quorum(CONFLICT_TITLE)
    expected_commits = min(pre_stock, N_ORDERS)
    expected_aborts = N_ORDERS - expected_commits

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

    # Safety invariant: commits never exceed the stock that was available
    # when the orders raced. This is the property the per-title lock
    # guarantees.
    assert len(commits) <= pre_stock, (
        f"committed more orders ({len(commits)}) than the pre-test stock "
        f"({pre_stock}); the per-title lock failed. decisions={decisions}"
    )

    # Liveness: with eight orders racing on a deterministic primary, every
    # available unit should turn into a commit and the remainder into a
    # clean abort. Both are visible to the demo audience.
    assert len(commits) == expected_commits, (
        f"expected {expected_commits} commits given pre_stock={pre_stock}, "
        f"got {len(commits)}. decisions={decisions}"
    )
    assert len(aborts) == expected_aborts, (
        f"expected {expected_aborts} aborts given pre_stock={pre_stock}, "
        f"got {len(aborts)}. decisions={decisions}"
    )
