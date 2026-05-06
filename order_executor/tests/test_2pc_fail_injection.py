"""Phase 6 end-to-end test: participant-failure recovery via 2PC commit retry.

Procedure:
  1. Baseline stock via raw Write against the current DB primary.
  2. Recreate books_database_3 with FAIL_NEXT_COMMIT=2 (compose override).
     DB-3 has the highest ID, so bully election hands primary back to it.
  3. POST /checkout for a single Book A. Coordinator sends Prepare -> DB
     votes commit -> coordinator sends Commit. First two Commits are
     rejected (commit_fail_injected). Third succeeds.
  4. Scrape executor logs for 2pc_commit_retry (x2) +
     2pc_commit_retry_succeeded, and DB logs for commit_fail_injected (x2)
     + commit_applied. All three replicas converge to stock-1.
  5. Restart books_database_3 without the override to return to baseline.

Run from host (Docker stack must be up):
    python order_executor/tests/test_2pc_fail_injection.py
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
    ("127.0.0.1:50258", 1),
    ("127.0.0.1:50259", 2),
    ("127.0.0.1:50260", 3),
]
ORCH = "http://localhost:8081"
REPO_ROOT = os.path.abspath(os.path.join(HERE, "../.."))
COMPOSE_BASE = ["docker", "compose", "-f", "docker-compose.yaml"]
COMPOSE_OVERRIDE = COMPOSE_BASE + ["-f", "docker-compose.fail-inject.yaml"]


def _run(cmd, timeout=120):
    return subprocess.run(
        cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=timeout
    )


def wait_for_primary(expected_id=None, timeout=45, stable_checks=3):
    """Return (addr, leader_id) once a primary is stably usable.

    The naive "first replica that reports any leader_id wins" probe is
    too optimistic. Around restarts and failovers, one replica can
    advertise a leader_id before the named primary is actually ready to
    serve primary-only Read/Write RPCs. We therefore require:

    (a) a 2-of-3 majority via WhoIsPrimary,
    (b) a primary-only Read against the named leader succeeds, and
    (c) the same leader_id holds for `stable_checks` consecutive probes.
    """
    id_to_host = {rid: addr for addr, rid in DB_HOSTS}
    deadline = time.time() + timeout
    last_answer = None
    streak = 0

    while time.time() < deadline:
        votes = {}
        for addr, _ in DB_HOSTS:
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
            candidate_id, candidate_votes = sorted(
                votes.items(), key=lambda kv: (kv[1], kv[0]), reverse=True
            )[0]
            if candidate_votes < 2:
                candidate_id = None

        if expected_id is not None and candidate_id != expected_id:
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

            if streak >= stable_checks:
                return id_to_host[candidate_id], candidate_id
        else:
            last_answer = None
            streak = 0

        time.sleep(1.0)

    if expected_id is None:
        raise RuntimeError(
            f"timed out waiting for any stable DB primary "
            f"(last_answer={last_answer}, streak={streak})"
        )
    raise RuntimeError(
        f"timed out waiting for DB-{expected_id} to reclaim stable primary "
        f"(last_answer={last_answer}, streak={streak})"
    )


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
            "name": "Bob",
            "contact": "bob@example.com",
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


def scrape_logs(services, since, patterns, timeout=60):
    """Block until every pattern in `patterns` appears at least the
    expected number of times in the merged logs of `services` since
    `since`. patterns is a dict {pattern_string: min_count}."""
    if isinstance(services, str):
        services = [services]
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = _run(
            COMPOSE_BASE + ["logs", *services, "--since", since],
            timeout=30,
        )
        text = (out.stdout or "") + (out.stderr or "")
        missing = {}
        for p, need in patterns.items():
            got = text.count(p)
            if got < need:
                missing[p] = (got, need)
        if not missing:
            return text
        time.sleep(1.0)
    raise AssertionError(
        f"timed out scraping {services} logs; still missing: {missing}"
    )


def main():
    # --- Baseline ---
    addr, pid = wait_for_primary(timeout=45)
    print(f"initial primary = DB-{pid} @ {addr}")
    raw_write(addr, "Book A", 10)
    print("baseline stock: Book A = 10")

    # --- Arm fail injection on DB-3 and wait for it to reclaim primary ---
    print("\n-- arming FAIL_NEXT_COMMIT=2 on DB-3 (recreate with override) --")
    r = _run(
        COMPOSE_OVERRIDE
        + [
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "books_database_3",
        ],
        timeout=120,
    )
    if r.returncode != 0:
        print("compose up stdout:", r.stdout)
        print("compose up stderr:", r.stderr)
        raise RuntimeError("failed to recreate books_database_3 with override")

    primary_addr, primary_id = wait_for_primary(expected_id=3, timeout=60)
    print(f"DB-{primary_id} reclaimed primary @ {primary_addr}")
    # Let the post-election state settle (heartbeats, etc.)
    time.sleep(2.0)

    # --- Submit the checkout ---
    t_submit = time.time()
    since_ts = "30s"  # log scrape window
    print("\n-- POST /checkout Book A x1 --")
    resp = post_checkout([{"title": "Book A", "quantity": 1}])
    order_id = resp.get("orderId")
    print(f"orchestrator resp: orderId={order_id} status={resp.get('status')!r}")

    # --- DB-3 should log 2 injected failures + a real commit_applied ---
    print("\n-- scraping DB-3 logs --")
    db_text = scrape_logs(
        "books_database_3",
        since=since_ts,
        patterns={
            f"commit_fail_injected order={order_id}": 2,
            f"commit_applied order={order_id}": 1,
        },
        timeout=90,
    )
    print("DB-3 log snippet:")
    for line in db_text.splitlines():
        if order_id and order_id in line:
            print(f"  {line}")

    # --- Executor should log 2 retry lines + 1 retry_succeeded ---
    print("\n-- scraping executor logs (all 3 merged; leader is unknown) --")
    exec_services = ["order_executor_1", "order_executor_2", "order_executor_3"]
    exec_text = scrape_logs(
        exec_services,
        since=since_ts,
        patterns={
            f"2pc_commit_retry order={order_id}": 2,
            f"2pc_commit_retry_succeeded order={order_id}": 1,
            f"2pc_commit_applied order={order_id}": 1,
        },
        timeout=90,
    )
    for line in exec_text.splitlines():
        if order_id and order_id in line and "2pc_commit" in line:
            print(f"  {line}")

    # --- Convergence: all 3 replicas must show Book A = 9 ---
    time.sleep(2.0)
    print("\n-- convergence check --")
    for addr, rid in DB_HOSTS:
        q = read_local(addr, "Book A")
        print(f"  DB-{rid}: Book A={q}")
        assert q == 9, f"DB-{rid} expected 9 got {q}"

    print("\nPHASE 6 FAIL-INJECTION E2E: PASSED")

    # --- Cleanup: recreate DB-3 without override so fail_next_commit=0 again ---
    print("\n-- cleanup: recreate DB-3 without override --")
    r = _run(
        COMPOSE_BASE
        + [
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "books_database_3",
        ],
        timeout=120,
    )
    if r.returncode != 0:
        print("cleanup stdout:", r.stdout)
        print("cleanup stderr:", r.stderr)
    # Wait for DB-3 to reclaim primary with the clean env.
    wait_for_primary(expected_id=3, timeout=60)
    print("DB-3 back to normal, fail_next_commit=0")


if __name__ == "__main__":
    main()
