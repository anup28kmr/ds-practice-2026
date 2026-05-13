"""Guide13 scenario 1.

  "Demonstrate a test scenario where a single non-fraudulent order is
  created from the frontend and verified for correctness."
"""

from tests.e2e._common import build_payload, classify, post_checkout


def test_single_clean_order_is_approved():
    payload = build_payload(
        user_name="Clean Carol",
        items=[{"name": "Book A", "quantity": 1}],
    )

    status, body = post_checkout(payload)

    assert status == 200, f"unexpected HTTP status: {status} body={body}"
    assert classify(body) == "approved", (
        f"expected Order Approved, got {body!r}"
    )
    assert body.get("orderId"), f"missing orderId in {body!r}"
