"""Guide13 scenario 3.

  "Automate test scenarios that involve a mixture of fraudulent and
  non-fraudulent orders, ensuring proper handling."

Three clean orders interleaved with three fraud orders. Clean ones must
all commit (Order Approved). Fraud ones are rejected by the fraud
detection service before the 2PC even fires; the orchestrator returns a
200 with status "Order Rejected".
"""

from tests.e2e._common import (
    CLEAN_CARD,
    FRAUD_CARD,
    build_payload,
    classify,
    post_concurrent,
)


def test_mixed_fraud_and_clean():
    clean_payloads = [
        build_payload(
            user_name=f"Clean {i}",
            card=CLEAN_CARD,
            items=[{"name": "Book C", "quantity": 1}],  # plenty of stock
        )
        for i in range(3)
    ]
    fraud_payloads = [
        build_payload(
            user_name=f"Fraud {i}",
            card=FRAUD_CARD,
            items=[{"name": "Book C", "quantity": 1}],
        )
        for i in range(3)
    ]

    # Interleave so order of arrival doesn't favour one group.
    payloads = [p for pair in zip(clean_payloads, fraud_payloads) for p in pair]
    results = post_concurrent(payloads)

    # The first half of pairs are clean, second half are fraud.
    clean_results = [results[i] for i in range(0, len(results), 2)]
    fraud_results = [results[i] for i in range(1, len(results), 2)]

    assert all(classify(b) == "approved" for _s, b in clean_results), (
        f"all clean orders should be approved, got: {clean_results!r}"
    )
    assert all(classify(b) == "rejected" for _s, b in fraud_results), (
        f"all fraud orders should be rejected, got: {fraud_results!r}"
    )
