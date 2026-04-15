"""Phase 6 end-to-end test: DB-primary crash between Prepare and Commit.

Procedure:
  1. Baseline stock via raw Write against the current DB primary.
  2. Recreate books_database_3 with FAIL_NEXT_COMMIT=99 so every Commit
     retry will be rejected as injected failure while DB-3 is up.
  3. POST /checkout. The coordinator sends Prepare (DB-3 persists the
     staged txn to /app/state/txn_<id>.json and votes commit), then starts
     retrying Commit, which keeps failing.
  4. After seeing at least one commit_fail_injected, `docker kill` DB-3.
     Wait a moment so the coordinator's next attempts return transport
     errors (UNAVAILABLE).
  5. Restart DB-3 WITHOUT the override (FAIL_NEXT_COMMIT=0). On boot it
     logs `recovered_pending order=<id>` after reading the txn file.
  6. DB-3 reclaims primary via bully. The coordinator's next retry now
     reaches a clean DB-3 which finds the recovered reservation and
     applies the commit.
  7. All three replicas converge to stock-1; /app/state/ is empty again.

Run from host (Docker stack must be up):
    python order_executor/tests/test_2pc_crash_recovery.py
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
COMPOSE_BASE = ["docker", "compose", "-f", "docker-compose.yaml"]
COMPOSE_OVERRIDE = COMPOSE_BASE + ["-f", "docker-compose.fail-inject.yaml"]
STATE_DIR_HOST = os.path.join(REPO_ROOT, "books_database", "state", "3")


def _run(cmd, timeout=120):
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
                    return id_to_host[r.leader_id], r.leader_id
        except Exception:
            continue
    return None, None


def wait_for_primary(expected_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, pid = find_primary()
        if pid == expected_id:
            return
        time.sleep(1.0)
    raise RuntimeError(f"timed out waiting for DB-{expected_id} to reclaim primary")


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


def post_checkout_async(items):
    payload = {
        "user": {
            "name": "Carol",
            "contact": "carol@example.com",
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
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read())


def scrape_logs(services, since, patterns, timeout=60):
    if isinstance(services, str):
        services = [services]
    deadline = time.time() + timeout
    last_text = ""
    while time.time() < deadline:
        out = _run(
            COMPOSE_BASE + ["logs", *services, "--since", since], timeout=30
        )
        text = (out.stdout or "") + (out.stderr or "")
        last_text = text
        missing = {}
        for p, need in patterns.items():
            if text.count(p) < need:
                missing[p] = (text.count(p), need)
        if not missing:
            return text
        time.sleep(1.0)
    raise AssertionError(
        f"timed out scraping {services}; last missing: {missing}"
    )


def main():
    # Baseline.
    addr, pid = find_primary()
    assert pid, "no DB primary at test start"
    print(f"initial primary = DB-{pid} @ {addr}")
    raw_write(addr, "Book A", 10)
    print("baseline: Book A = 10")

    # Arm FAIL_NEXT_COMMIT=99 on DB-3 so Commit keeps failing until we kill it.
    print("\n-- arming FAIL_NEXT_COMMIT=99 on DB-3 --")
    # Temporarily bump the override value.
    override_path = os.path.join(REPO_ROOT, "docker-compose.fail-inject.yaml")
    original = open(override_path).read()
    open(override_path, "w").write(
        "services:\n"
        "  books_database_3:\n"
        "    environment:\n"
        "      - FAIL_NEXT_COMMIT=99\n"
    )
    try:
        r = _run(
            COMPOSE_OVERRIDE
            + ["up", "-d", "--no-deps", "--force-recreate", "books_database_3"],
            timeout=120,
        )
        if r.returncode != 0:
            print(r.stdout, r.stderr)
            raise RuntimeError("recreate DB-3 with high fail count failed")

        wait_for_primary(3, timeout=60)
        print("DB-3 primary (fail_next_commit=99)")
        time.sleep(2.0)

        # Fire checkout in a background thread so we can kill DB-3 mid-retry
        # without blocking on the orchestrator's response.
        import threading

        resp_box = {}

        def do_checkout():
            try:
                resp_box["resp"] = post_checkout_async(
                    [{"title": "Book A", "quantity": 1}]
                )
            except Exception as e:
                resp_box["err"] = repr(e)

        t = threading.Thread(target=do_checkout)
        t.start()

        # Wait until we see at least one commit_fail_injected on DB-3.
        print("\n-- waiting for first commit_fail_injected on DB-3 --")
        scrape_logs(
            "books_database_3",
            since="1m",
            patterns={"commit_fail_injected": 1},
            timeout=60,
        )
        print("saw commit_fail_injected; staged txn is persisted on disk")

        # Confirm the txn file exists on the host-mounted state dir.
        staged = [
            f
            for f in os.listdir(STATE_DIR_HOST)
            if f.startswith("txn_") and f.endswith(".json")
        ]
        print(f"host state dir = {STATE_DIR_HOST} contents={staged}")
        assert staged, "no txn_*.json in DB-3 state dir; persistence broken"

        # Hard-kill DB-3.
        print("\n-- docker kill books_database_3 --")
        r = _run(COMPOSE_BASE + ["kill", "books_database_3"], timeout=30)
        if r.returncode != 0:
            print(r.stdout, r.stderr)
            raise RuntimeError("kill books_database_3 failed")
        time.sleep(3.0)

    finally:
        # Restore override file to the benign FAIL_NEXT_COMMIT=2 value
        # before we restart DB-3, so the recovered instance runs clean.
        open(override_path, "w").write(original)

    # Restart DB-3 WITHOUT any override — fail_next_commit=0.
    print("\n-- restarting DB-3 (clean, FAIL_NEXT_COMMIT=0) --")
    r = _run(
        COMPOSE_BASE
        + ["up", "-d", "--no-deps", "--force-recreate", "books_database_3"],
        timeout=120,
    )
    if r.returncode != 0:
        print(r.stdout, r.stderr)
        raise RuntimeError("restart DB-3 failed")

    # Must see recovered_pending in logs.
    print("\n-- waiting for recovered_pending --")
    scrape_logs(
        "books_database_3",
        since="1m",
        patterns={"recovered_pending": 1},
        timeout=60,
    )
    print("DB-3 recovered pending reservation from disk")

    wait_for_primary(3, timeout=60)
    print("DB-3 reclaimed primary")

    # Coordinator retry should now finally apply the commit.
    print("\n-- waiting for 2pc_commit_applied --")
    exec_services = ["order_executor_1", "order_executor_2", "order_executor_3"]
    scrape_logs(
        exec_services,
        since="2m",
        patterns={"2pc_commit_applied": 1, "2pc_commit_retry_succeeded": 1},
        timeout=120,
    )
    print("coordinator committed after DB-3 recovered")

    # Wait for orchestrator response thread.
    t.join(timeout=60)
    if "resp" in resp_box:
        print(f"checkout resp = {resp_box['resp']}")
    else:
        print(f"checkout err = {resp_box.get('err')}")

    time.sleep(2.0)
    print("\n-- convergence check --")
    for addr, rid in DB_HOSTS:
        q = read_local(addr, "Book A")
        print(f"  DB-{rid}: Book A={q}")
        assert q == 9, f"DB-{rid} expected 9 got {q}"

    remaining = [
        f
        for f in os.listdir(STATE_DIR_HOST)
        if f.startswith("txn_") and f.endswith(".json")
    ]
    assert not remaining, f"state dir should be empty, still has {remaining}"
    print("DB-3 state dir cleaned after commit")

    print("\nPHASE 6 CRASH-RECOVERY E2E: PASSED")


if __name__ == "__main__":
    main()
