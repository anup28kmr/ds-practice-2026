import os
import sys
import time
import grpc
import threading
from concurrent import futures

FILE = __file__ if "__file__" in globals() else os.getenv("PYTHONFILE", "")

executor_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/order_executor")
)
queue_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/order_queue")
)
db_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/books_database")
)
payment_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/payment_service")
)

sys.path.insert(0, executor_grpc_path)
sys.path.insert(0, queue_grpc_path)
sys.path.insert(0, db_grpc_path)
sys.path.insert(0, payment_grpc_path)

import order_executor_pb2 as executor_pb2
import order_executor_pb2_grpc as executor_grpc
import order_queue_pb2 as queue_pb2
import order_queue_pb2_grpc as queue_grpc
import books_database_pb2 as db_pb2
import books_database_pb2_grpc as db_grpc
import payment_pb2 as pay_pb2
import payment_pb2_grpc as pay_grpc


EXECUTOR_ID = int(os.getenv("EXECUTOR_ID", "1"))
EXECUTOR_PORT = os.getenv("EXECUTOR_PORT", "50055")
HEARTBEAT_INTERVAL = 2.0
LEADER_TIMEOUT = 5.0

# 2PC participants. The DB primary address is discovered dynamically because
# any of the three replicas can hold primary after a failover.
DB_REPLICA_ADDRS = [
    "books_database_1:50058",
    "books_database_2:50058",
    "books_database_3:50058",
]
PAYMENT_ADDR = "payment_service:50061"

# Price table used only so the Prepare payload has a realistic amount.
# The demo payment service never validates the amount; this is purely for
# the log trail.
BOOK_PRICES = {
    "Book A": 12.99,
    "Book B": 14.99,
    "Book C": 9.99,
    "Distributed Systems Basics": 45.00,
    "Designing Data-Intensive Applications": 52.00,
}
DEFAULT_PRICE = 15.00

state_lock = threading.Lock()
leader_id = None
last_heartbeat = time.time()
is_leader = False
election_in_progress = False


def parse_peers():
    peers = []
    raw = os.getenv("PEERS", "")
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        peer_id, peer_addr = item.split("@", 1)
        peers.append((int(peer_id), peer_addr))
    return peers


PEERS = parse_peers()


def has_fresh_leader_locked():
    if leader_id is None:
        return False
    if is_leader and leader_id == EXECUTOR_ID:
        return True
    return (time.time() - last_heartbeat) <= LEADER_TIMEOUT


def announce_coordinator():
    for pid, addr in PEERS:
        if pid == EXECUTOR_ID:
            continue
        send_rpc(
            addr,
            lambda stub: stub.Coordinator(
                executor_pb2.CoordinatorRequest(leader_id=EXECUTOR_ID),
                timeout=2.0,
            ),
        )


class ControlService(executor_grpc.OrderExecutorControlServicer):
    def Election(self, request, context):
        global election_in_progress

        if EXECUTOR_ID <= request.candidate_id:
            return executor_pb2.ElectionResponse(alive=False)

        print(f"[EXEC-{EXECUTOR_ID}] received election from {request.candidate_id}")

        with state_lock:
            already_leader = is_leader
            election_running = election_in_progress

        # If I am already the leader, just re-announce myself instead of
        # starting a brand new election.
        if already_leader:
            threading.Thread(target=announce_coordinator, daemon=True).start()
        elif not election_running:
            threading.Thread(target=start_election, daemon=True).start()

        return executor_pb2.ElectionResponse(alive=True)

    def Coordinator(self, request, context):
        global leader_id, is_leader, election_in_progress, last_heartbeat
        with state_lock:
            leader_id = request.leader_id
            is_leader = leader_id == EXECUTOR_ID
            election_in_progress = False
            last_heartbeat = time.time()

        print(f"[EXEC-{EXECUTOR_ID}] new leader is {leader_id}")
        return executor_pb2.Ack(ok=True)

    def Heartbeat(self, request, context):
        global leader_id, is_leader, last_heartbeat
        with state_lock:
            leader_id = request.leader_id
            is_leader = leader_id == EXECUTOR_ID
            last_heartbeat = time.time()
        return executor_pb2.Ack(ok=True)


def send_rpc(addr, fn):
    try:
        with grpc.insecure_channel(addr) as channel:
            stub = executor_grpc.OrderExecutorControlStub(channel)
            return fn(stub)
    except Exception:
        return None


def start_election():
    global election_in_progress, leader_id

    with state_lock:
        if election_in_progress:
            return

        # Do not start a new election if a healthy leader is already known.
        if has_fresh_leader_locked():
            return

        election_in_progress = True

    print(f"[EXEC-{EXECUTOR_ID}] starting election")

    higher_peers = [(pid, addr) for pid, addr in PEERS if pid > EXECUTOR_ID]
    got_answer = False

    for pid, addr in higher_peers:
        response = send_rpc(
            addr,
            lambda stub: stub.Election(
                executor_pb2.ElectionRequest(candidate_id=EXECUTOR_ID),
                timeout=2.0,
            ),
        )
        if response and response.alive:
            got_answer = True

    if not got_answer:
        become_leader()
        return

    # Wait for a higher node to announce a leader.
    time.sleep(LEADER_TIMEOUT)

    with state_lock:
        fresh_leader = has_fresh_leader_locked()
        election_in_progress = False

    if not fresh_leader:
        with state_lock:
            leader_id = None
        start_election()


def become_leader():
    global leader_id, is_leader, election_in_progress, last_heartbeat
    with state_lock:
        leader_id = EXECUTOR_ID
        is_leader = True
        election_in_progress = False
        last_heartbeat = time.time()

    print(f"[EXEC-{EXECUTOR_ID}] became leader")
    announce_coordinator()


def heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)

        with state_lock:
            leader_now = is_leader

        if not leader_now:
            continue

        for pid, addr in PEERS:
            if pid == EXECUTOR_ID:
                continue
            send_rpc(
                addr,
                lambda stub: stub.Heartbeat(
                    executor_pb2.HeartbeatRequest(leader_id=EXECUTOR_ID),
                    timeout=2.0,
                ),
            )


def timeout_loop():
    global leader_id

    while True:
        time.sleep(1.0)

        with state_lock:
            if is_leader or election_in_progress:
                continue

            # During startup, if no leader is known yet, do not immediately
            # treat that as a timeout storm.
            if leader_id is None:
                continue

            expired = (time.time() - last_heartbeat) > LEADER_TIMEOUT

        if expired:
            print(f"[EXEC-{EXECUTOR_ID}] leader timeout detected")
            with state_lock:
                leader_id = None
            start_election()


def find_db_primary_addr():
    """Ask each replica who the current primary is and return the first
    leader_addr the quorum reports. Returns '' if no primary is known."""
    for addr in DB_REPLICA_ADDRS:
        try:
            with grpc.insecure_channel(addr) as ch:
                stub = db_grpc.BooksDatabaseServiceStub(ch)
                r = stub.WhoIsPrimary(
                    db_pb2.WhoIsPrimaryRequest(), timeout=2.0
                )
                if r.leader_id and r.leader_addr:
                    return r.leader_addr
        except Exception:
            continue
    return ""


def compute_amount(items):
    return sum(
        BOOK_PRICES.get(i.title, DEFAULT_PRICE) * i.quantity for i in items
    )


def _db_prepare(primary_addr, order_id, items):
    with grpc.insecure_channel(primary_addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        item_msgs = [
            db_pb2.PrepareItem(title=i.title, quantity=i.quantity)
            for i in items
        ]
        return stub.Prepare(
            db_pb2.PrepareRequest(order_id=order_id, items=item_msgs),
            timeout=5.0,
        )


def _db_commit(primary_addr, order_id):
    with grpc.insecure_channel(primary_addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.Commit(
            db_pb2.CommitRequest(order_id=order_id), timeout=10.0
        )


def _db_abort(primary_addr, order_id):
    with grpc.insecure_channel(primary_addr) as ch:
        stub = db_grpc.BooksDatabaseServiceStub(ch)
        return stub.Abort(
            db_pb2.AbortRequest(order_id=order_id), timeout=5.0
        )


def _pay_prepare(order_id, amount, user_name):
    with grpc.insecure_channel(PAYMENT_ADDR) as ch:
        stub = pay_grpc.PaymentServiceStub(ch)
        return stub.Prepare(
            pay_pb2.PaymentPrepareRequest(
                order_id=order_id, amount=amount, user_name=user_name
            ),
            timeout=5.0,
        )


def _pay_commit(order_id):
    with grpc.insecure_channel(PAYMENT_ADDR) as ch:
        stub = pay_grpc.PaymentServiceStub(ch)
        return stub.Commit(
            pay_pb2.PaymentCommitRequest(order_id=order_id), timeout=5.0
        )


def _pay_abort(order_id):
    with grpc.insecure_channel(PAYMENT_ADDR) as ch:
        stub = pay_grpc.PaymentServiceStub(ch)
        return stub.Abort(
            pay_pb2.PaymentAbortRequest(order_id=order_id), timeout=5.0
        )


def run_2pc(order):
    """Two-Phase Commit coordinator for a single order.

    Phase 1: fan out Prepare to the DB primary and the payment service in
    parallel, collect votes.
    Decision record: log decision=COMMIT or decision=ABORT *before* sending
    phase 2 RPCs so recovery (Phase 6) has a log point to resume from.
    Phase 2: send Commit to both participants if both voted commit, else
    send Abort to both (idempotent on either side).
    """
    order_id = order.order_id
    items = list(order.items)
    user_name = order.user_name or "unknown"
    amount = compute_amount(items)

    items_repr = ",".join(f"{i.title}x{i.quantity}" for i in items)
    print(
        f"[EXEC-{EXECUTOR_ID}] 2pc_start order={order_id} "
        f"user=\"{user_name}\" items=[{items_repr}] amount={amount:.2f}"
    )

    primary_addr = find_db_primary_addr()
    if not primary_addr:
        print(
            f"[EXEC-{EXECUTOR_ID}] 2pc_decision order={order_id} "
            f"decision=ABORT reason=no-db-primary"
        )
        try:
            _pay_abort(order_id)
        except Exception:
            pass
        return False

    # Phase 1: Prepare fan-out in parallel.
    results = {}

    def _call_db():
        try:
            results["db"] = _db_prepare(primary_addr, order_id, items)
        except Exception as e:
            results["db"] = None
            results["db_err"] = str(e)

    def _call_pay():
        try:
            results["pay"] = _pay_prepare(order_id, amount, user_name)
        except Exception as e:
            results["pay"] = None
            results["pay_err"] = str(e)

    t_db = threading.Thread(target=_call_db)
    t_pay = threading.Thread(target=_call_pay)
    t_db.start()
    t_pay.start()
    t_db.join(timeout=10.0)
    t_pay.join(timeout=10.0)

    db_resp = results.get("db")
    pay_resp = results.get("pay")
    db_vote = bool(db_resp and db_resp.vote_commit)
    pay_vote = bool(pay_resp and pay_resp.vote_commit)
    db_msg = db_resp.message if db_resp else results.get("db_err", "no-response")
    pay_msg = pay_resp.message if pay_resp else results.get(
        "pay_err", "no-response"
    )

    print(
        f"[EXEC-{EXECUTOR_ID}] 2pc_votes order={order_id} "
        f"db=(vote_commit={db_vote},msg={db_msg!r}) "
        f"payment=(vote_commit={pay_vote},msg={pay_msg!r})"
    )

    decision = "COMMIT" if (db_vote and pay_vote) else "ABORT"

    # Decision record. Written BEFORE phase 2 so a crashed coordinator can
    # be re-derived from the log (Phase 6/7 material).
    print(
        f"[EXEC-{EXECUTOR_ID}] 2pc_decision order={order_id} "
        f"decision={decision} participants=[db,payment]"
    )

    # Phase 2.
    if decision == "COMMIT":
        # Retry loop for Phase 6 recovery: if the DB participant returns a
        # transient failure (injected or real), back off and retry. The
        # participant persists the staged transaction on vote_commit so
        # its state survives a restart.
        # Retry budget ~40s of wall-clock: long enough to outlast a DB
        # participant that is killed mid-retry, rebooted from its on-disk
        # pending file, and then has to re-win the bully election to
        # become primary again (commit lands on the original participant).
        commit_max_attempts = 12
        commit_backoffs = [0.5, 1.0, 2.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0]
        db_ok = False
        db_msg = ""
        for attempt in range(1, commit_max_attempts + 1):
            try:
                db_c = _db_commit(primary_addr, order_id)
                db_ok = bool(db_c and db_c.success)
                db_msg = db_c.message if db_c else "no-response"
            except Exception as e:
                db_c = None
                db_ok = False
                db_msg = f"rpc_error={e!r}"

            if db_ok:
                if attempt > 1:
                    print(
                        f"[EXEC-{EXECUTOR_ID}] 2pc_commit_retry_succeeded "
                        f"order={order_id} attempt={attempt}"
                    )
                break

            print(
                f"[EXEC-{EXECUTOR_ID}] 2pc_commit_retry "
                f"order={order_id} attempt={attempt} db_msg={db_msg!r}"
            )
            if attempt == commit_max_attempts:
                break
            time.sleep(commit_backoffs[min(attempt - 1, len(commit_backoffs) - 1)])
            # Re-discover primary in case of failover.
            new_primary = find_db_primary_addr()
            if new_primary and new_primary != primary_addr:
                print(
                    f"[EXEC-{EXECUTOR_ID}] 2pc_primary_changed "
                    f"order={order_id} old_primary={primary_addr} "
                    f"new_primary={new_primary}"
                )
                primary_addr = new_primary
            elif not new_primary:
                print(
                    f"[EXEC-{EXECUTOR_ID}] 2pc_primary_unknown "
                    f"order={order_id} attempt={attempt}"
                )

        try:
            pay_c = _pay_commit(order_id)
        except Exception as e:
            pay_c = None
            print(
                f"[EXEC-{EXECUTOR_ID}] 2pc_commit_pay_rpc_error "
                f"order={order_id} err={e!r}"
            )
        pay_ok = bool(pay_c and pay_c.success)

        if db_ok and pay_ok:
            print(f"[EXEC-{EXECUTOR_ID}] 2pc_commit_applied order={order_id}")
            return True
        print(
            f"[EXEC-{EXECUTOR_ID}] 2pc_commit_partial order={order_id} "
            f"db_ok={db_ok} db_msg={db_msg!r} pay_ok={pay_ok}"
        )
        return False

    # ABORT path: Abort is idempotent on both sides; send to both
    # regardless of which (if any) voted commit.
    try:
        _db_abort(primary_addr, order_id)
    except Exception as e:
        print(
            f"[EXEC-{EXECUTOR_ID}] 2pc_abort_db_rpc_error "
            f"order={order_id} err={e!r}"
        )
    try:
        _pay_abort(order_id)
    except Exception as e:
        print(
            f"[EXEC-{EXECUTOR_ID}] 2pc_abort_pay_rpc_error "
            f"order={order_id} err={e!r}"
        )
    print(f"[EXEC-{EXECUTOR_ID}] 2pc_abort_applied order={order_id}")
    return False


def consume_loop():
    while True:
        time.sleep(1.0)

        with state_lock:
            if not is_leader:
                continue

        try:
            with grpc.insecure_channel("order_queue:50054") as channel:
                stub = queue_grpc.OrderQueueServiceStub(channel)
                response = stub.Dequeue(
                    queue_pb2.DequeueRequest(executor_id=str(EXECUTOR_ID)),
                    timeout=2.0,
                )
        except Exception as e:
            print(f"[EXEC-{EXECUTOR_ID}] queue error: {e}")
            continue

        if not response.success:
            continue

        items_repr = ",".join(
            f"{i.title}x{i.quantity}" for i in response.order.items
        )
        print(
            f"[EXEC-{EXECUTOR_ID}] leader={EXECUTOR_ID} "
            f"executing order={response.order.order_id} "
            f'user="{response.order.user_name}" '
            f"item_count={response.order.item_count} items=[{items_repr}]"
        )

        committed = run_2pc(response.order)
        status = "committed" if committed else "aborted"
        print(
            f"[EXEC-{EXECUTOR_ID}] order_done "
            f"order={response.order.order_id} status={status}"
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    executor_grpc.add_OrderExecutorControlServicer_to_server(
        ControlService(), server
    )
    server.add_insecure_port("[::]:" + EXECUTOR_PORT)
    server.start()
    print(f"[EXEC-{EXECUTOR_ID}] listening on port {EXECUTOR_PORT}")

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=timeout_loop, daemon=True).start()
    threading.Thread(target=consume_loop, daemon=True).start()

    # Give peers a brief moment to come up, then start election only if
    # no leader is already known.
    time.sleep(1.0)
    with state_lock:
        should_start = (leader_id is None) and (not election_in_progress)

    if should_start:
        start_election()

    server.wait_for_termination()


if __name__ == "__main__":
    serve()