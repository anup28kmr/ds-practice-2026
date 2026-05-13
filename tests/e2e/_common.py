"""Shared helpers for the four Guide13 end-to-end scenarios.

Every scenario hits the orchestrator's HTTP endpoint at 127.0.0.1:8081 and
inspects the JSON response. We deliberately stay on stdlib (urllib + json +
concurrent.futures) so the only thing the operator installs is pytest.
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Tuple

ORCH_URL = os.getenv("CP4_ORCHESTRATOR_URL", "http://127.0.0.1:8081/checkout")

CLEAN_CARD = "4111111111111111"
FRAUD_CARD = "4111111111110000"  # FraudDetection rejects cards ending in 0000.


def build_payload(
    user_name: str = "Test User",
    contact: str = "test@example.com",
    card: str = CLEAN_CARD,
    items: List[Dict[str, Any]] = None,
    terms: bool = True,
) -> Dict[str, Any]:
    return {
        "user": {
            "name": user_name,
            "contact": contact,
            "creditCard": {
                "number": card,
                "expirationDate": "12/30",
                "cvv": "123",
            },
        },
        "items": items or [{"name": "Book A", "quantity": 1}],
        "shippingMethod": "Standard",
        "termsAndConditionsAccepted": terms,
    }


def post_checkout(payload: Dict[str, Any], timeout: float = 30.0) -> Tuple[int, Dict[str, Any]]:
    """POST one checkout and return (status_code, body_json)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ORCH_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        return e.code, json.loads(body) if body else {}


def post_concurrent(
    payloads: List[Dict[str, Any]], max_workers: int = 16
) -> List[Tuple[int, Dict[str, Any]]]:
    """Fire all payloads concurrently from a thread pool, preserve order."""
    results: List[Tuple[int, Dict[str, Any]]] = [None] * len(payloads)  # type: ignore[list-item]
    ready_barrier = threading.Barrier(len(payloads))

    def _run(i: int, p: Dict[str, Any]):
        # Park every worker on the barrier so requests start within the same
        # millisecond instead of staggered by the pool's submit cadence. This
        # is what makes "concurrent" actually concurrent for the conflicting
        # and non-conflicting scenarios.
        ready_barrier.wait()
        results[i] = post_checkout(p)

    with ThreadPoolExecutor(max_workers=max(max_workers, len(payloads))) as ex:
        futures = [ex.submit(_run, i, p) for i, p in enumerate(payloads)]
        for f in as_completed(futures):
            f.result()
    return results


def wait_for_orchestrator(timeout_seconds: float = 60.0) -> None:
    """Block until the orchestrator's GET / responds 200. Useful when the
    test runner has just brought the stack up via `docker compose up -d`."""
    base = ORCH_URL.rsplit("/", 1)[0] + "/"
    deadline = time.time() + timeout_seconds
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base, timeout=2.0) as resp:
                if resp.status == 200:
                    return
        except Exception as e:  # connection refused, timeouts
            last_err = e
        time.sleep(1.0)
    raise RuntimeError(
        f"orchestrator at {base} did not become ready within "
        f"{timeout_seconds:.0f}s (last err: {last_err!r})"
    )


def classify(body: Dict[str, Any]) -> str:
    """Map the orchestrator's response shape to a single bucket label."""
    status = (body.get("status") or "").lower()
    if "approved" in status:
        return "approved"
    if "rejected" in status:
        return "rejected"
    if body.get("error"):
        return "error"
    return "unknown"


def collect_2pc_decisions(
    order_ids: List[str],
    timeout_seconds: float = 30.0,
    poll_interval: float = 1.0,
) -> Dict[str, str]:
    """Poll `docker compose logs` for the executors until every given order_id
    has a `2pc_decision order=<id> decision=COMMIT|ABORT` line, then return
    {order_id: 'COMMIT'|'ABORT'}.

    The orchestrator returns 'Order Approved' optimistically after the
    enqueue succeeds; the actual 2PC commit/abort happens asynchronously
    on the elected order_executor leader. To assert real outcomes (e.g.
    'only 3 of 8 orders committed'), the test must wait for the
    coordinator's decision line for each order_id.
    """
    import re
    import subprocess

    pending = set(order_ids)
    out: Dict[str, str] = {}
    deadline = time.time() + timeout_seconds
    pattern = re.compile(
        r"2pc_decision order=([0-9a-f-]+) decision=(COMMIT|ABORT)"
    )

    while pending and time.time() < deadline:
        result = subprocess.run(
            [
                "docker", "compose", "logs", "--no-color", "--tail=2000",
                "order_executor_1", "order_executor_2", "order_executor_3",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        for m in pattern.finditer(result.stdout):
            oid, decision = m.group(1), m.group(2)
            if oid in pending:
                out[oid] = decision
                pending.discard(oid)
        if pending:
            time.sleep(poll_interval)

    if pending:
        raise AssertionError(
            f"timed out waiting for 2PC decisions; missing: {pending} "
            f"(have {len(out)} of {len(order_ids)})"
        )
    return out
