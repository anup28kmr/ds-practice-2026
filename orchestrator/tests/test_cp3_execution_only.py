"""Phase 8 verification: CP3_EXECUTION_ONLY fast-path.

When the orchestrator is started with CP3_EXECUTION_ONLY=true, /checkout
must:
  - log the `cp3_execution_only=true skipping CP2 pipeline` line,
  - NOT emit initialization_complete / starting_root_events /
    clear_broadcast_sent,
  - still enqueue the order so the 2PC path downstream runs,
  - return Order Approved with empty suggestedBooks.

Run from host (stack must be up with the cp3-only override applied):
    docker compose -f docker-compose.yaml \
        -f docker-compose.cp3-only.yaml \
        up -d --no-deps --force-recreate orchestrator
    python orchestrator/tests/test_cp3_execution_only.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

import grpc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../utils/pb/books_database")))
import books_database_pb2 as db_pb2
import books_database_pb2_grpc as db_grpc

DB_HOSTS = [
    ("127.0.0.1:50058", 1),
    ("127.0.0.1:50059", 2),
    ("127.0.0.1:50060", 3),
]
ORCH = "http://localhost:8081"
REPO_ROOT = os.path.abspath(os.path.join(HERE, "../.."))
COMPOSE = ["docker", "compose", "-f", "docker-compose.yaml"]


def _run(cmd, timeout=60):
    return subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout
    )


def find_primary():
    id_to_host = {rid: addr for addr, rid in DB_HOSTS}
    for addr, _ in DB_HOSTS:
        try:
            with grpc.insecure_channel(addr) as ch:
                stub = db_grpc.BooksDatabaseServiceStub(ch)
                r = stub.WhoIsPrimary(db_pb2.WhoIsPrimaryRequest(), timeout=2.0)
                if r.leader_id:
                    return id_to_host[r.leader_id]
        except Exception:
            continue
    raise RuntimeError("no DB primary")


def raw_write(addr, title, qty):
    with grpc.insecure_channel(addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.Write(
            db_pb2.WriteRequest(title=title, quantity=qty), timeout=5.0
        )


def read_local(addr, title):
    with grpc.insecure_channel(addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.ReadLocal(db_pb2.ReadRequest(title=title), timeout=3.0).quantity


def post_checkout(items):
    payload = {
        "user": {
            "name": "Dave",
            "contact": "dave@example.com",
            "creditCard": {
                "number": "4111111111111111",
                "expirationDate": "12/30",
                "cvv": "123",
            },
            "billingAddress": {
                "street": "1 Main St", "city": "Tartu", "state": "Tartumaa",
                "zip": "51000", "country": "EE",
            },
        },
        "items": items,
        "termsAndConditionsAccepted": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{ORCH}/checkout", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def main():
    # Baseline.
    primary = find_primary()
    raw_write(primary, "Book A", 10)
    print(f"primary={primary} baseline Book A = 10")

    # Confirm orchestrator is running in CP3_EXECUTION_ONLY=true mode.
    # Wide window because the container may have been restarted earlier.
    out = _run(COMPOSE + ["logs", "--since", "30m", "orchestrator"])
    assert "cp3_execution_only=True" in (out.stdout or ""), (
        "orchestrator not in CP3_EXECUTION_ONLY mode. Start it with:\n"
        "  docker compose -f docker-compose.yaml -f docker-compose.cp3-only.yaml "
        "up -d --no-deps --force-recreate orchestrator"
    )
    print("orchestrator mode = CP3_EXECUTION_ONLY (flag=True)")

    # Submit checkout.
    resp = post_checkout([{"title": "Book A", "quantity": 1}])
    order_id = resp.get("orderId")
    status = resp.get("status")
    books = resp.get("suggestedBooks") or []
    print(f"/checkout -> orderId={order_id} status={status!r} "
          f"suggestedBooks={len(books)}")
    assert status == "Order Approved", f"expected Approved, got {status!r}"
    assert books == [], "CP3_EXECUTION_ONLY must return empty suggestedBooks"

    # Wait for 2PC commit.
    deadline = time.time() + 30
    committed = False
    while time.time() < deadline:
        out = _run(COMPOSE + [
            "logs", "--since", "1m",
            "order_executor_1", "order_executor_2", "order_executor_3",
        ])
        if f"2pc_commit_applied order={order_id}" in (out.stdout or ""):
            committed = True
            break
        time.sleep(1.0)
    assert committed, "2PC never committed the order"
    print("2PC commit observed in executor logs")

    # Verify orchestrator did NOT run the CP2 pipeline for this order.
    out = _run(COMPOSE + ["logs", "--since", "2m", "orchestrator"])
    orch = out.stdout or ""
    skip_line = f"order={order_id} cp3_execution_only=true"
    assert skip_line in orch, f"expected orch skip line for {order_id}"
    # None of these CP2-pipeline log points should exist for this order.
    for forbidden in (
        f"order={order_id} initialization_complete",
        f"order={order_id} starting_root_events",
        f"order={order_id} clear_broadcast_sent",
    ):
        assert forbidden not in orch, f"CP2 pipeline ran: saw {forbidden!r}"
    print("orchestrator skipped CP2 pipeline (init/root/clear all absent)")

    # Verify stock converged.
    time.sleep(1.5)
    for addr, rid in DB_HOSTS:
        q = read_local(addr, "Book A")
        print(f"  DB-{rid}: Book A={q}")
        assert q == 9, f"DB-{rid} expected 9 got {q}"

    print("\nPHASE 8 OPTION C (CP3_EXECUTION_ONLY) E2E: PASSED")


if __name__ == "__main__":
    main()
