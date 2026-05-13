"""Helper for scripts/checkpoint3-checks.ps1.

Subcommands:
  read-stock <title>        print one line per replica: "DB-<id>=<qty>"
  find-primary              print "primary_id=<N>" and "primary_addr=<host>:<port>"
  all-reachable             exit 0 if all 3 DB replicas answer WhoIsPrimary

All subcommands exit non-zero on failure with a short reason on stderr.

Used by the PowerShell verification script so it can assert on the
actual gRPC state of the replicated books_database rather than going
through the orchestrator path.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../utils/pb/books_database")))

import grpc  # noqa: E402
import books_database_pb2 as db_pb2  # noqa: E402
import books_database_pb2_grpc as db_grpc  # noqa: E402

REPLICAS = [
    (1, "127.0.0.1:51258"),
    (2, "127.0.0.1:51259"),
    (3, "127.0.0.1:51260"),
]


def _stub(addr):
    return db_grpc.BooksDatabaseServiceStub(grpc.insecure_channel(addr))


def cmd_read_stock(title, tolerate_missing=False):
    out_lines = []
    errors = []
    for rid, addr in REPLICAS:
        try:
            with grpc.insecure_channel(addr) as ch:
                stub = db_grpc.BooksDatabaseServiceStub(ch)
                r = stub.ReadLocal(
                    db_pb2.ReadRequest(title=title), timeout=3.0
                )
                out_lines.append(f"DB-{rid}={r.quantity}")
        except Exception as exc:
            if tolerate_missing:
                out_lines.append(f"DB-{rid}=UNREACHABLE")
            else:
                errors.append(f"DB-{rid}: {exc!r}")
    if errors:
        sys.stderr.write("read-stock errors: " + "; ".join(errors) + "\n")
        return 1
    print("\n".join(out_lines))
    return 0


def cmd_find_primary():
    id_to_addr = {
        1: "127.0.0.1:51258",
        2: "127.0.0.1:51259",
        3: "127.0.0.1:51260",
    }
    for _, addr in REPLICAS:
        try:
            with grpc.insecure_channel(addr) as ch:
                stub = db_grpc.BooksDatabaseServiceStub(ch)
                r = stub.WhoIsPrimary(
                    db_pb2.WhoIsPrimaryRequest(), timeout=2.0
                )
                if r.leader_id:
                    print(f"primary_id={r.leader_id}")
                    print(f"primary_addr={id_to_addr[r.leader_id]}")
                    return 0
        except Exception:
            continue
    sys.stderr.write("no DB primary reachable\n")
    return 2


def cmd_all_reachable():
    for rid, addr in REPLICAS:
        try:
            with grpc.insecure_channel(addr) as ch:
                stub = db_grpc.BooksDatabaseServiceStub(ch)
                stub.WhoIsPrimary(db_pb2.WhoIsPrimaryRequest(), timeout=2.0)
        except Exception as exc:
            sys.stderr.write(f"DB-{rid} unreachable: {exc!r}\n")
            return 3
    print("all_reachable=1")
    return 0


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: _cp3_db_probe.py <subcommand> [args...]\n")
        return 64
    sub = sys.argv[1]
    if sub == "read-stock":
        if len(sys.argv) < 3:
            sys.stderr.write("read-stock requires <title>\n")
            return 64
        tolerate = "--tolerate-missing" in sys.argv[3:]
        title = sys.argv[2]
        return cmd_read_stock(title, tolerate_missing=tolerate)
    if sub == "find-primary":
        return cmd_find_primary()
    if sub == "all-reachable":
        return cmd_all_reachable()
    sys.stderr.write(f"unknown subcommand: {sub}\n")
    return 64


if __name__ == "__main__":
    sys.exit(main())
