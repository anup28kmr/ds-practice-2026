"""Phase 5 end-to-end test.

Submits a checkout, waits for the pipeline to execute, then confirms that
the DB primary and all backups hold the expected decremented stock and
that the coordinator wrote a decision record.

Run from host (Docker stack must be up):
    python order_executor/tests/test_2pc_end_to_end.py
"""

import json
import os
import subprocess
import sys
import time

import grpc
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../utils/pb/books_database")))

import books_database_pb2 as db_pb2
import books_database_pb2_grpc as db_grpc

DB_HOSTS = [
    ("127.0.0.1:51258", 1),
    ("127.0.0.1:51259", 2),
    ("127.0.0.1:51260", 3),
]
ORCH = "http://localhost:8081"


def find_primary():
    id_to_host = {rid: addr for addr, rid in DB_HOSTS}
    for addr, _ in DB_HOSTS:
        try:
            with grpc.insecure_channel(addr) as ch:
                stub = db_grpc.BooksDatabaseServiceStub(ch)
                r = stub.WhoIsPrimary(db_pb2.WhoIsPrimaryRequest(), timeout=2.0)
                if r.leader_id:
                    return id_to_host[r.leader_id], r.leader_id
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
        return stub.ReadLocal(
            db_pb2.ReadRequest(title=title), timeout=3.0
        ).quantity


def post_checkout(items):
    payload = {
        "user": {
            "name": "Alice",
            "contact": "alice@example.com",
            "creditCard": {
                "number": "4111111111111111",
                "expirationDate": "12/30",
                "cvv": "123",
            },
            "billingAddress": {
                "street": "1 Main St",
                "city": "Tartu",
                "state": "Tartumaa",
                "zip": "51000",
                "country": "EE",
            },
        },
        "items": items,
        "termsAndConditionsAccepted": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{ORCH}/checkout",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def wait_for_2pc_decision(order_id, timeout=30):
    """Scrape executor logs until we see a 2pc_decision for this order."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(
            [
                "docker",
                "compose",
                "logs",
                "order_executor_1",
                "order_executor_2",
                "order_executor_3",
                "--since",
                "2m",
            ],
            capture_output=True,
            text=True,
        )
        for line in (out.stdout or "").splitlines():
            if f"2pc_decision order={order_id}" in line:
                return line
        time.sleep(1.0)
    return None


def main():
    primary_addr, primary_id = find_primary()
    print(f"DB primary = DB-{primary_id} @ {primary_addr}")

    # Reset to known baseline via raw Write (skips 2PC participant path).
    baseline = {"Book A": 10, "Book B": 6}
    for title, qty in baseline.items():
        raw_write(primary_addr, title, qty)
    print(f"baseline stock = {baseline}")

    # Happy-path checkout.
    checkout_items = [{"title": "Book A", "quantity": 2}, {"title": "Book B", "quantity": 1}]
    print(f"POST /checkout items={checkout_items}")
    resp = post_checkout(checkout_items)
    order_id = resp.get("orderId")
    print(f"orchestrator response = orderId={order_id} status={resp.get('status')!r}")

    # Wait for executor to report decision.
    line = wait_for_2pc_decision(order_id, timeout=45)
    print(f"decision log: {line!r}")
    assert line is not None, "no 2pc_decision log line observed"
    assert "decision=COMMIT" in line, f"expected COMMIT, got: {line}"

    # Give commit a moment to finish replicating.
    time.sleep(2.0)

    for addr, rid in DB_HOSTS:
        a = read_local(addr, "Book A")
        b = read_local(addr, "Book B")
        print(f"  DB-{rid}: Book A={a} Book B={b}")
        assert a == 8, f"DB-{rid} Book A expected 8 got {a}"
        assert b == 5, f"DB-{rid} Book B expected 5 got {b}"

    print("\nPHASE 5 E2E HAPPY PATH: PASSED")

    # ABORT path: request more than available stock.
    print("\n-- ABORT path --")
    resp = post_checkout([{"title": "Book A", "quantity": 1000}])
    abort_order_id = resp.get("orderId")
    print(f"ORCH resp = {resp.get('status')!r}")

    line = wait_for_2pc_decision(abort_order_id, timeout=45)
    print(f"decision log: {line!r}")
    assert line is not None, "no 2pc_decision log line observed"
    assert "decision=ABORT" in line, f"expected ABORT, got: {line}"

    time.sleep(1.0)

    for addr, rid in DB_HOSTS:
        a = read_local(addr, "Book A")
        print(f"  DB-{rid}: Book A={a}")
        assert a == 8, f"DB-{rid} Book A stayed 8, got {a}"

    print("\nPHASE 5 E2E ABORT PATH: PASSED")


if __name__ == "__main__":
    main()
