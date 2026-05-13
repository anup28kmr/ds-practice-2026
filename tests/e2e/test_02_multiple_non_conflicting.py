"""Guide13 scenario 2.

  "Create automated tests to handle scenarios involving multiple
  simultaneous non-fraudulent orders that do not conflict with each
  other."

We fire 5 concurrent orders, each for a different book title, so the
per-key locks in the books database never contend. All 5 must commit.
"""

from tests.e2e._common import build_payload, classify, post_concurrent


# Pick titles that are seeded (see books_database/src/app.py SEED_STOCK) and
# distinct so the 2PC Prepares run in parallel rather than serializing on the
# per-title lock. 'Designing Data-Intensive Applications' is intentionally
# excluded -- scenario 4 uses it as its conflict subject, and we don't want
# scenario 2 to consume the small seed stock first.
NON_CONFLICTING_TITLES = [
    "Book A",
    "Book B",
    "Book C",
    "Distributed Systems Basics",
]


def test_multiple_non_conflicting_orders_all_commit():
    payloads = [
        build_payload(
            user_name=f"User {i}",
            items=[{"name": title, "quantity": 1}],
        )
        for i, title in enumerate(NON_CONFLICTING_TITLES)
    ]

    results = post_concurrent(payloads)

    approved = sum(1 for _s, b in results if classify(b) == "approved")
    assert approved == len(payloads), (
        f"expected all {len(payloads)} non-conflicting orders to commit, "
        f"got approved={approved}; raw responses: {results!r}"
    )
    order_ids = {b.get("orderId") for _s, b in results}
    assert len(order_ids) == len(payloads), (
        f"expected distinct order_ids, got: {order_ids}"
    )
