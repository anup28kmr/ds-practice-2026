"""Phase 3 verification: per-key locks allow concurrent writes on different
keys to proceed in parallel, while concurrent writes on the same key
serialize cleanly.

Run from host:
    python books_database/tests/test_concurrent_writes.py
"""

import os
import sys
import time
import threading

import grpc

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../utils/pb/books_database")))

import books_database_pb2 as db_pb2
import books_database_pb2_grpc as db_grpc

# Host ports for the three replicas (see docker-compose.yaml).
PRIMARY_CANDIDATES = [
    ("127.0.0.1:50058", 1),
    ("127.0.0.1:50059", 2),
    ("127.0.0.1:50060", 3),
]


def find_primary():
    """Ask any replica who the primary is and return its host-side address."""
    # Map replica_id -> host_addr
    id_to_host = {rid: addr for addr, rid in PRIMARY_CANDIDATES}
    for addr, _ in PRIMARY_CANDIDATES:
        try:
            with grpc.insecure_channel(addr) as ch:
                stub = db_grpc.BooksDatabaseServiceStub(ch)
                r = stub.WhoIsPrimary(db_pb2.WhoIsPrimaryRequest(), timeout=2.0)
                if r.leader_id:
                    return id_to_host[r.leader_id], r.leader_id
        except Exception:
            continue
    raise RuntimeError("no primary found")


def write_one(addr, title, quantity, results, idx, barrier):
    barrier.wait()
    t0 = time.time()
    try:
        with grpc.insecure_channel(addr) as ch:
            stub = db_grpc.BooksDatabaseServiceStub(ch)
            r = stub.Write(
                db_pb2.WriteRequest(title=title, quantity=quantity),
                timeout=10.0,
            )
            ok, msg = r.success, r.message
    except Exception as exc:
        ok, msg = False, f"rpc_error={exc}"
    results[idx] = (title, quantity, ok, msg, t0, time.time())


def read_one(addr, title):
    with grpc.insecure_channel(addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.Read(db_pb2.ReadRequest(title=title), timeout=3.0)


def run_concurrent(addr, plan, label):
    """plan = list of (title, quantity) tuples, all fired at once."""
    n = len(plan)
    results = [None] * n
    barrier = threading.Barrier(n)
    threads = [
        threading.Thread(
            target=write_one,
            args=(addr, title, qty, results, i, barrier),
        )
        for i, (title, qty) in enumerate(plan)
    ]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30.0)
    elapsed = time.time() - start

    all_ok = all(r is not None and r[2] for r in results)
    print(f"\n== {label}: {n} concurrent writes -> elapsed={elapsed:.2f}s all_ok={all_ok}")
    for r in results:
        title, qty, ok, msg, t0, t1 = r
        print(f"  title=\"{title}\" qty={qty} ok={ok} latency={(t1-t0):.2f}s msg={msg!r}")
    return elapsed, all_ok


def main():
    addr, pid = find_primary()
    print(f"primary = DB-{pid} @ {addr}")

    # Test B: 5 concurrent writes on 5 DIFFERENT keys. With per-key locks
    # these should fan out to backups in parallel — wall-clock well under
    # 5 * (single-write latency).
    different_plan = [
        ("Book A", 201),
        ("Book B", 202),
        ("Book C", 203),
        ("Distributed Systems Basics", 204),
        ("Designing Data-Intensive Applications", 205),
    ]
    elapsed_diff, ok_diff = run_concurrent(addr, different_plan, "TEST B (different keys)")

    # Re-confirm per-key serialization baseline: 5 concurrent writes on SAME key.
    same_plan = [("Book A", 300 + i) for i in range(5)]
    elapsed_same, ok_same = run_concurrent(addr, same_plan, "TEST A' (same key, smaller)")

    # Verify final value is one of the attempted values (no torn state).
    r_a = read_one(addr, "Book A")
    consistent_a = r_a.success and (r_a.quantity in {300, 301, 302, 303, 304})
    print(f"\nfinal Book A = {r_a.quantity} consistent={consistent_a}")

    for title, qty in different_plan:
        r = read_one(addr, title)
        print(f"  {title} = {r.quantity} (expected {qty}) match={r.quantity == qty}")

    print()
    print(f"elapsed(different keys) = {elapsed_diff:.2f}s")
    print(f"elapsed(same key)       = {elapsed_same:.2f}s")
    print("  -> different-keys should be roughly same as one write's latency,")
    print("     same-key should be ~N * single-write latency (serialized).")


if __name__ == "__main__":
    main()
