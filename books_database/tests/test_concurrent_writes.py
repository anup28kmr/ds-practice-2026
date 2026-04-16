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
    """Return (host_addr, leader_id) for the current primary.

    Hardened against the brief leader-stabilization window that can
    appear right after a failover/restore cycle. The naive "first peer
    that reports any leader_id wins" approach can point Writes at a
    replica whose own is_leader has already flipped back to False,
    yielding `not primary; primary=None` rejections. To avoid that we
    require all three of the following, for three consecutive
    iterations spaced ~1s apart, within a 30s deadline:

      (a) at least 2 of 3 replicas agree on the same leader_id via
          WhoIsPrimary,
      (b) a primary-only Read RPC against the named leader succeeds
          (this is the only check that actually exercises the
          `if not is_leader: reject` branch on the named node), and
      (c) the named leader_id is the same as the one returned by the
          previous iteration.
    """
    id_to_host = {rid: addr for addr, rid in PRIMARY_CANDIDATES}
    required_stable = 3
    deadline = time.time() + 30.0
    last_answer = None
    streak = 0

    while time.time() < deadline:
        votes = {}
        for addr, _ in PRIMARY_CANDIDATES:
            try:
                with grpc.insecure_channel(addr) as ch:
                    stub = db_grpc.BooksDatabaseServiceStub(ch)
                    r = stub.WhoIsPrimary(
                        db_pb2.WhoIsPrimaryRequest(), timeout=2.0
                    )
                    if r.leader_id:
                        votes[r.leader_id] = votes.get(r.leader_id, 0) + 1
            except Exception:
                continue

        candidate_id = None
        if votes:
            # Pick the candidate with the most votes; tie-break on the
            # higher leader_id to match the bully protocol's rule.
            candidate_id, candidate_votes = sorted(
                votes.items(), key=lambda kv: (kv[1], kv[0]), reverse=True
            )[0]
            if candidate_votes < 2:
                candidate_id = None

        probe_ok = False
        if candidate_id is not None:
            addr = id_to_host[candidate_id]
            try:
                with grpc.insecure_channel(addr) as ch:
                    stub = db_grpc.BooksDatabaseServiceStub(ch)
                    probe = stub.Read(
                        db_pb2.ReadRequest(title="Book A"), timeout=2.0
                    )
                probe_ok = bool(probe.success)
            except Exception:
                probe_ok = False

        if candidate_id is not None and probe_ok:
            if candidate_id == last_answer:
                streak += 1
            else:
                last_answer = candidate_id
                streak = 1

            if streak >= required_stable:
                print(
                    f"find_primary stable: leader_id={candidate_id} "
                    f"votes={votes} streak={streak}"
                )
                return id_to_host[candidate_id], candidate_id
        else:
            last_answer = None
            streak = 0

        time.sleep(1.0)

    raise RuntimeError(
        f"no stable DB primary within 30s "
        f"(last_answer={last_answer}, streak={streak})"
    )


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


def read_local(addr, title):
    """ReadLocal bypasses the primary-only guard."""
    with grpc.insecure_channel(addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.ReadLocal(db_pb2.ReadRequest(title=title), timeout=3.0)


def main():
    addr, pid = find_primary()
    print(f"primary = DB-{pid} @ {addr}")
    failures = []

    # ------------------------------------------------------------------
    # Test A: 5 concurrent writes on the SAME key. Per-key lock
    # serializes them, so the final value must be one of the attempted
    # values (no torn state), and all 5 writes must succeed.
    # ------------------------------------------------------------------
    same_plan = [("Book A", 300 + i) for i in range(5)]
    elapsed_same, ok_same = run_concurrent(addr, same_plan, "TEST A (same key)")
    if not ok_same:
        failures.append("TEST A: not all same-key writes succeeded")

    r_a = read_one(addr, "Book A")
    attempted_values = {300 + i for i in range(5)}
    if not r_a.success:
        failures.append(f"TEST A: Read(Book A) failed: {r_a.message}")
    elif r_a.quantity not in attempted_values:
        failures.append(
            f"TEST A: final Book A = {r_a.quantity}, expected one of {attempted_values}"
        )
    print(f"  final Book A = {r_a.quantity} (in {attempted_values}? "
          f"{'YES' if r_a.quantity in attempted_values else 'NO'})")

    # Verify convergence: all 3 replicas must show the same value.
    id_to_host = {rid: host for host, rid in PRIMARY_CANDIDATES}
    values = {}
    for rid, host in id_to_host.items():
        rl = read_local(host, "Book A")
        values[rid] = rl.quantity
    print(f"  convergence: {values}")
    if len(set(values.values())) != 1:
        failures.append(f"TEST A: replicas diverged after same-key writes: {values}")

    # ------------------------------------------------------------------
    # Test B: 5 concurrent writes on 5 DIFFERENT keys. With per-key
    # locks these should fan out in parallel. Each key gets a unique
    # value, so the final read on each key must match exactly.
    # ------------------------------------------------------------------
    different_plan = [
        ("Book A", 201),
        ("Book B", 202),
        ("Book C", 203),
        ("Distributed Systems Basics", 204),
        ("Designing Data-Intensive Applications", 205),
    ]
    elapsed_diff, ok_diff = run_concurrent(addr, different_plan, "TEST B (different keys)")
    if not ok_diff:
        failures.append("TEST B: not all different-key writes succeeded")

    for title, expected in different_plan:
        r = read_one(addr, title)
        match = r.success and r.quantity == expected
        print(f"  {title} = {r.quantity} (expected {expected}) {'OK' if match else 'FAIL'}")
        if not match:
            failures.append(f"TEST B: {title} = {r.quantity}, expected {expected}")

    # Verify convergence on all replicas for every key.
    for title, expected in different_plan:
        for rid, host in id_to_host.items():
            rl = read_local(host, title)
            if rl.quantity != expected:
                failures.append(
                    f"TEST B convergence: DB-{rid} {title} = {rl.quantity}, "
                    f"expected {expected}"
                )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print(f"elapsed(same key)       = {elapsed_same:.2f}s")
    print(f"elapsed(different keys) = {elapsed_diff:.2f}s")
    if failures:
        print(f"\nFAILED ({len(failures)} assertion(s)):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("\nCONCURRENT WRITES TEST: PASSED")


if __name__ == "__main__":
    main()
