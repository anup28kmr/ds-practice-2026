"""Phase 4 smoke tests: payment_service + books_database participant RPCs.

Does NOT drive a 2PC coordinator - that is Phase 5. Here we just prove
each participant RPC is wired end-to-end and behaves as specified.

Run from the host:
    python payment_service/tests/smoke_test.py
"""

import os
import sys
import time

import grpc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../utils/pb/payment_service")))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../utils/pb/books_database")))

import payment_pb2 as pay_pb2
import payment_pb2_grpc as pay_grpc
import books_database_pb2 as db_pb2
import books_database_pb2_grpc as db_grpc

DB_HOSTS = [("127.0.0.1:50058", 1), ("127.0.0.1:50059", 2), ("127.0.0.1:50060", 3)]
PAY_HOST = "127.0.0.1:50061"


def find_db_primary():
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
    raise RuntimeError("no DB primary found")


def read(addr, title):
    """Read from the primary only (client-facing)."""
    with grpc.insecure_channel(addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.Read(db_pb2.ReadRequest(title=title), timeout=3.0)


def read_local(addr, title):
    """Read from any replica's local copy (debug/ops)."""
    with grpc.insecure_channel(addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.ReadLocal(db_pb2.ReadRequest(title=title), timeout=3.0)


def db_prepare(addr, order_id, items):
    with grpc.insecure_channel(addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        item_msgs = [db_pb2.PrepareItem(title=t, quantity=q) for t, q in items]
        return stub.Prepare(
            db_pb2.PrepareRequest(order_id=order_id, items=item_msgs),
            timeout=5.0,
        )


def db_commit(addr, order_id):
    with grpc.insecure_channel(addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.Commit(db_pb2.CommitRequest(order_id=order_id), timeout=5.0)


def db_abort(addr, order_id):
    with grpc.insecure_channel(addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.Abort(db_pb2.AbortRequest(order_id=order_id), timeout=5.0)


def pay_prepare(order_id, amount, user):
    with grpc.insecure_channel(PAY_HOST) as ch:
        stub = pay_grpc.PaymentServiceStub(ch)
        return stub.Prepare(
            pay_pb2.PaymentPrepareRequest(
                order_id=order_id, amount=amount, user_name=user
            ),
            timeout=3.0,
        )


def pay_commit(order_id):
    with grpc.insecure_channel(PAY_HOST) as ch:
        stub = pay_grpc.PaymentServiceStub(ch)
        return stub.Commit(
            pay_pb2.PaymentCommitRequest(order_id=order_id), timeout=3.0
        )


def pay_abort(order_id):
    with grpc.insecure_channel(PAY_HOST) as ch:
        stub = pay_grpc.PaymentServiceStub(ch)
        return stub.Abort(
            pay_pb2.PaymentAbortRequest(order_id=order_id), timeout=3.0
        )


def main():
    db_addr, db_id = find_db_primary()
    print(f"DB primary: DB-{db_id} @ {db_addr}")

    # Per-run prefix so the participant's idempotency caches don't blur runs.
    rp = f"smoke-{int(time.time())}"

    # --- Payment service round-trip ---
    r = pay_prepare(f"{rp}-p1", 42.50, "Alice")
    print(f"payment.Prepare -> vote_commit={r.vote_commit} msg={r.message!r}")
    assert r.vote_commit

    r = pay_commit(f"{rp}-p1")
    print(f"payment.Commit -> success={r.success} msg={r.message!r}")
    assert r.success

    # Idempotent commit
    r = pay_commit(f"{rp}-p1")
    print(f"payment.Commit (retry) -> success={r.success} msg={r.message!r}")
    assert r.success

    # Prepare + Abort
    r = pay_prepare(f"{rp}-p2", 9.99, "Bob")
    assert r.vote_commit
    r = pay_abort(f"{rp}-p2")
    print(f"payment.Abort -> success={r.success} msg={r.message!r}")
    assert r.success

    # --- DB participant: reset Book A to a known value first ---
    with grpc.insecure_channel(db_addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        stub.Write(db_pb2.WriteRequest(title="Book A", quantity=10), timeout=3.0)
        stub.Write(db_pb2.WriteRequest(title="Book B", quantity=6), timeout=3.0)
    print(f"DB reset: Book A=10 Book B=6")

    # Test 1: Prepare + Commit applies decrement everywhere.
    r = db_prepare(db_addr, f"{rp}-o1", [("Book A", 2), ("Book B", 1)])
    print(f"db.Prepare(o1) -> vote={r.vote_commit} msg={r.message!r}")
    assert r.vote_commit

    r = db_commit(db_addr, f"{rp}-o1")
    print(f"db.Commit(o1) -> ok={r.success} msg={r.message!r}")
    assert r.success

    # Convergence check across all replicas (via ReadLocal).
    for addr, rid in DB_HOSTS:
        a = read_local(addr, "Book A").quantity
        b = read_local(addr, "Book B").quantity
        print(f"  DB-{rid}: Book A={a} Book B={b}")
        assert a == 8 and b == 5

    # Test 2: Prepare + Abort leaves stock unchanged.
    r = db_prepare(db_addr, f"{rp}-o2", [("Book A", 3)])
    assert r.vote_commit
    r = db_abort(db_addr, f"{rp}-o2")
    print(f"db.Abort(o2) -> ok={r.success} msg={r.message!r}")
    assert read(db_addr, "Book A").quantity == 8

    # Test 3: Prepare more than stock -> vote_commit=False, no staging.
    r = db_prepare(db_addr, f"{rp}-o3", [("Book A", 1000)])
    print(f"db.Prepare(o3, huge) -> vote={r.vote_commit} msg={r.message!r}")
    assert not r.vote_commit
    assert read(db_addr, "Book A").quantity == 8

    # Test 4: Two overlapping Prepares that together exceed stock.
    # Book A is 8. We stage an order for 6. Then an order for 5 should fail
    # because the first order has 6 reserved.
    r = db_prepare(db_addr, f"{rp}-o4a", [("Book A", 6)])
    assert r.vote_commit
    r = db_prepare(db_addr, f"{rp}-o4b", [("Book A", 5)])
    print(f"db.Prepare(o4b, would oversell) -> vote={r.vote_commit} msg={r.message!r}")
    assert not r.vote_commit

    # Clean up: abort o4a, confirm stock back.
    db_abort(db_addr, f"{rp}-o4a")
    assert read(db_addr, "Book A").quantity == 8

    # Test 5: Prepare idempotence.
    r1 = db_prepare(db_addr, f"{rp}-o5", [("Book A", 1)])
    r2 = db_prepare(db_addr, f"{rp}-o5", [("Book A", 1)])
    print(f"db.Prepare(o5) x2 -> first={r1.vote_commit} second={r2.vote_commit}")
    assert r1.vote_commit and r2.vote_commit
    db_abort(db_addr, f"{rp}-o5")

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
