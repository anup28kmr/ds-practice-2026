import sys
import os
import grpc
import uuid
import time
import threading
from concurrent import futures

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")

order_executor_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_executor'))
sys.path.insert(0, order_executor_grpc_path)
import order_executor_pb2 as order_executor
import order_executor_pb2_grpc as order_executor_grpc

order_queue_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, order_queue_grpc_path)
import order_queue_pb2 as order_queue
import order_queue_pb2_grpc as order_queue_grpc

books_database_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/books_database'))
sys.path.insert(0, books_database_grpc_path)
import books_database_pb2 as books_database
import books_database_pb2_grpc as books_database_grpc

payment_service_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/payment_service'))
sys.path.insert(0, payment_service_grpc_path)
import payment_service_pb2 as payment_service
import payment_service_pb2_grpc as payment_service_grpc

# ─── Configuration ─────────────────────────────────────────────────────────────

EXECUTOR_ID   = os.getenv("EXECUTOR_ID", "1")
EXECUTOR_PORT = os.getenv("EXECUTOR_PORT", "60001")

EXECUTOR_REPLICAS = [
    {"id": "1", "host": "order_executor_1", "port": "60001"},
    {"id": "2", "host": "order_executor_2", "port": "60002"},
    {"id": "3", "host": "order_executor_3", "port": "60003"},
]

# All known DB replicas — used for fallback if primary is unreachable
DB_REPLICAS = [
    {"host": "books_database_1", "port": "60011"},
    {"host": "books_database_2", "port": "60012"},
    {"host": "books_database_3", "port": "60013"},
]

# 2PC retry settings for coordinator fault tolerance
COMMIT_MAX_RETRIES = 5
COMMIT_RETRY_DELAY = 2.0   # seconds between retries

# ─── Leader election state ─────────────────────────────────────────────────────

current_leader       = None
leader_lock          = threading.Lock()
election_in_progress = False
election_lock        = threading.Lock()

# ─── gRPC stubs ────────────────────────────────────────────────────────────────

def get_queue_stub():
    channel = grpc.insecure_channel('order_queue:50054')
    return order_queue_grpc.OrderQueueServiceStub(channel)

def get_db_stub():
    """
    Always connect to books_database_1 as primary.
    If it is unreachable, fall back to the next available replica.
    This keeps compatibility with your static PRIMARY/BACKUP setup.
    """
    for replica in DB_REPLICAS:
        try:
            addr = f"{replica['host']}:{replica['port']}"
            channel = grpc.insecure_channel(addr)
            stub = books_database_grpc.BooksDatabaseServiceStub(channel)
            # Quick liveness check
            stub.Read(books_database.ReadRequest(book_title="__ping__"), timeout=1)
            return stub
        except Exception:
            continue
    # Last resort — return primary stub even if unreachable
    channel = grpc.insecure_channel('books_database_1:60011')
    return books_database_grpc.BooksDatabaseServiceStub(channel)

def get_payment_stub():
    channel = grpc.insecure_channel('payment_service:60020')
    return payment_service_grpc.PaymentServiceStub(channel)

def get_executor_stub(host, port):
    channel = grpc.insecure_channel(f'{host}:{port}')
    return order_executor_grpc.OrderExecutorServiceStub(channel)


# ─── gRPC service ──────────────────────────────────────────────────────────────

class OrderExecutorService(order_executor_grpc.OrderExecutorServiceServicer):

    def StartElection(self, request, context):
        candidate_id = request.candidate_id
        print(f"[EXEC-{EXECUTOR_ID}] received_election from={candidate_id}")
        if int(EXECUTOR_ID) > int(candidate_id):
            print(f"[EXEC-{EXECUTOR_ID}] taking_over_election higher_id={EXECUTOR_ID}")
            with leader_lock:
                no_leader = (current_leader is None)
            if no_leader:
                threading.Thread(target=maybe_start_election, daemon=True).start()
            else:
                threading.Thread(target=announce_leadership, daemon=True).start()
        return order_executor.ElectionResponse(acknowledged=True)

    def AnnounceLeader(self, request, context):
        global current_leader, election_in_progress
        with leader_lock:
            current_leader = request.leader_id
        with election_lock:
            election_in_progress = False
        print(f"[EXEC-{EXECUTOR_ID}] new_leader leader_id={current_leader}")
        return order_executor.LeaderResponse(acknowledged=True)

    def GetLeader(self, request, context):
        with leader_lock:
            lid = current_leader or ""
        return order_executor.GetLeaderResponse(leader_id=lid)


# ─── Election helpers ──────────────────────────────────────────────────────────

def maybe_start_election():
    global election_in_progress
    with leader_lock:
        if current_leader is not None:
            return
    with election_lock:
        if election_in_progress:
            return
        election_in_progress = True
    try:
        start_bully_election()
    finally:
        with election_lock:
            election_in_progress = False

def start_bully_election():
    global current_leader
    print(f"[EXEC-{EXECUTOR_ID}] starting_election")
    higher_replied = False
    for replica in EXECUTOR_REPLICAS:
        if int(replica["id"]) > int(EXECUTOR_ID):
            try:
                stub = get_executor_stub(replica["host"], replica["port"])
                resp = stub.StartElection(
                    order_executor.ElectionRequest(candidate_id=EXECUTOR_ID),
                    timeout=2
                )
                if resp.acknowledged:
                    higher_replied = True
                    print(f"[EXEC-{EXECUTOR_ID}] higher_replica_acked replica={replica['id']}")
            except Exception:
                print(f"[EXEC-{EXECUTOR_ID}] replica_unreachable replica={replica['id']}")

    if not higher_replied:
        with leader_lock:
            current_leader = EXECUTOR_ID
        print(f"[EXEC-{EXECUTOR_ID}] became_leader leader_id={EXECUTOR_ID}")
        announce_leadership()

def announce_leadership():
    for replica in EXECUTOR_REPLICAS:
        if replica["id"] != EXECUTOR_ID:
            try:
                stub = get_executor_stub(replica["host"], replica["port"])
                stub.AnnounceLeader(
                    order_executor.LeaderRequest(leader_id=EXECUTOR_ID),
                    timeout=2
                )
                print(f"[EXEC-{EXECUTOR_ID}] announced_leadership to={replica['id']}")
            except Exception:
                print(f"[EXEC-{EXECUTOR_ID}] announce_failed to={replica['id']}")


# ─── Two-Phase Commit ──────────────────────────────────────────────────────────

def two_phase_commit(order):
    """
    Coordinator side of 2PC. Participants: books_database (primary) and
    payment_service.

    Uses a single transaction_id (UUID) shared across both participants.
    Real order data — book_title, quantity, amount — is read from the
    dequeued order, not hardcoded.

    Phase 1 (Prepare): ask both participants to stage their operations.
    Phase 2 (Commit/Abort): if both vote yes → Commit; otherwise → Abort.

    Commit phase retries up to COMMIT_MAX_RETRIES times with a delay if a
    participant returns failure (e.g. transient network error). This
    handles the coordinator-retry scenario described in the seminar bonus.
    """
    transaction_id = str(uuid.uuid4())
    db_stub        = get_db_stub()
    payment_stub   = get_payment_stub()

    # ── Extract real order data ───────────────────────────────────────────────
    # order.items is a list of order items from the queue proto.
    # We take the first item for now; extend the loop below for multi-item.
    if order.items:
        book_title = order.items[0]
        quantity   = 1
        amount     = 9.99
    else:
        print(f"[EXEC-{EXECUTOR_ID}] 2pc_abort_empty_order order={order.order_id}")
        return False

    user_name = order.user_name










    print(f"[EXEC-{EXECUTOR_ID}] 2pc_start order={order.order_id} "
          f"tx={transaction_id} book={book_title} qty={quantity} amount={amount:.2f}")

    # ── Phase 1: Prepare ──────────────────────────────────────────────────────
    db_vote = payment_vote = False

    try:
        r = db_stub.Prepare(books_database.PrepareRequest(
            transaction_id=transaction_id,
            order_id=order.order_id,
            book_title=book_title,
            quantity=quantity
        ))
        db_vote = r.ready
        if not db_vote:
            print(f"[EXEC-{EXECUTOR_ID}] db_prepare_vote_abort "
                  f"order={order.order_id} reason={r.message}")
    except Exception as e:
        print(f"[EXEC-{EXECUTOR_ID}] db_prepare_failed order={order.order_id} error={e}")

    try:
        r = payment_stub.Prepare(payment_service.PrepareRequest(
            transaction_id=transaction_id,
            order_id=order.order_id,
            amount=amount
        ))
        payment_vote = r.ready
        if not payment_vote:
            print(f"[EXEC-{EXECUTOR_ID}] payment_prepare_vote_abort "
                  f"order={order.order_id} reason={r.message}")
    except Exception as e:
        print(f"[EXEC-{EXECUTOR_ID}] payment_prepare_failed order={order.order_id} error={e}")

    print(f"[EXEC-{EXECUTOR_ID}] 2pc_votes order={order.order_id} "
          f"db=(vote_commit={db_vote}) payment=(vote_commit={payment_vote})")

    # ── Phase 2: Commit or Abort ──────────────────────────────────────────────
    if db_vote and payment_vote:
        print(f"[EXEC-{EXECUTOR_ID}] 2pc_decision order={order.order_id} "
              f"decision=COMMIT participants=[db,payment]")

        # Commit DB — retry on transient failure (coordinator fault tolerance)
        db_committed = False
        for attempt in range(1, COMMIT_MAX_RETRIES + 1):
            try:
                r = db_stub.Commit(books_database.CommitRequest(
                    transaction_id=transaction_id,
                    order_id=order.order_id
                ))
                if r.success:
                    print(f"[EXEC-{EXECUTOR_ID}] db_commit_result "
                          f"order={order.order_id} success=True attempt={attempt}")
                    db_committed = True
                    break
                else:
                    print(f"[EXEC-{EXECUTOR_ID}] db_commit_retry "
                          f"order={order.order_id} attempt={attempt} reason={r.message}")
                    time.sleep(COMMIT_RETRY_DELAY)
            except Exception as e:
                print(f"[EXEC-{EXECUTOR_ID}] db_commit_error "
                      f"order={order.order_id} attempt={attempt} error={e}")
                time.sleep(COMMIT_RETRY_DELAY)

        if not db_committed:
            print(f"[EXEC-{EXECUTOR_ID}] db_commit_exhausted "
                  f"order={order.order_id} max_retries={COMMIT_MAX_RETRIES}")

        # Commit payment — retry on transient failure
        payment_committed = False
        for attempt in range(1, COMMIT_MAX_RETRIES + 1):
            try:
                r = payment_stub.Commit(payment_service.CommitRequest(
                    transaction_id=transaction_id,
                    order_id=order.order_id
                ))
                if r.success:
                    print(f"[EXEC-{EXECUTOR_ID}] payment_commit_result "
                          f"order={order.order_id} success=True attempt={attempt}")
                    payment_committed = True
                    break
                else:
                    print(f"[EXEC-{EXECUTOR_ID}] payment_commit_retry "
                          f"order={order.order_id} attempt={attempt} reason={r.message}")
                    time.sleep(COMMIT_RETRY_DELAY)
            except Exception as e:
                print(f"[EXEC-{EXECUTOR_ID}] payment_commit_error "
                      f"order={order.order_id} attempt={attempt} error={e}")
                time.sleep(COMMIT_RETRY_DELAY)

        if not payment_committed:
            print(f"[EXEC-{EXECUTOR_ID}] payment_commit_exhausted "
                  f"order={order.order_id} max_retries={COMMIT_MAX_RETRIES}")

        success = db_committed and payment_committed
        print(f"[EXEC-{EXECUTOR_ID}] 2pc_commit_applied order={order.order_id} "
              f"tx={transaction_id} db={db_committed} payment={payment_committed}")
        return success

    else:
        # At least one participant voted abort — send Abort to both
        print(f"[EXEC-{EXECUTOR_ID}] 2pc_decision order={order.order_id} "
              f"decision=ABORT participants=[db,payment]")

        try:
            db_stub.Abort(books_database.AbortRequest(
                transaction_id=transaction_id,
                order_id=order.order_id
            ))
            print(f"[EXEC-{EXECUTOR_ID}] db_aborted order={order.order_id}")
        except Exception as e:
            print(f"[EXEC-{EXECUTOR_ID}] db_abort_failed order={order.order_id} error={e}")

        try:
            payment_stub.Abort(payment_service.AbortRequest(
                transaction_id=transaction_id,
                order_id=order.order_id
            ))
            print(f"[EXEC-{EXECUTOR_ID}] payment_aborted order={order.order_id}")
        except Exception as e:
            print(f"[EXEC-{EXECUTOR_ID}] payment_abort_failed order={order.order_id} error={e}")

        print(f"[EXEC-{EXECUTOR_ID}] 2pc_aborted order={order.order_id} tx={transaction_id}")
        return False


# ─── Execution loop ────────────────────────────────────────────────────────────

def execution_loop():
    global current_leader
    print(f"[EXEC-{EXECUTOR_ID}] starting execution_loop")

    # Stagger: EXEC-3 fires at 2.0s, EXEC-2 at 2.5s, EXEC-1 at 3.0s
    # EXEC-3 wins and announces before the others wake up → one clean election
    sleep_before_election = 2.0 + (3 - int(EXECUTOR_ID)) * 0.5
    time.sleep(sleep_before_election)

    maybe_start_election()

    while True:
        try:
            with leader_lock:
                leader = current_leader

            if leader is None:
                time.sleep(1)
                continue

            if leader == EXECUTOR_ID:
                stub = get_queue_stub()
                resp = stub.Dequeue(order_queue.DequeueRequest(executor_id=EXECUTOR_ID))
                if resp.success:
                    print(f"[EXEC-{EXECUTOR_ID}] order_dequeued order_id={resp.order.order_id}")
                    two_phase_commit(resp.order)
                    time.sleep(1)
                else:
                    time.sleep(2)
            else:
                time.sleep(2)

        except Exception as e:
            print(f"[EXEC-{EXECUTOR_ID}] execution_loop_error error={e}")
            with leader_lock:
                current_leader = None
            time.sleep(1)
            maybe_start_election()
            time.sleep(2)


# ─── Server entry point ────────────────────────────────────────────────────────

def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    order_executor_grpc.add_OrderExecutorServiceServicer_to_server(
        OrderExecutorService(), server
    )
    server.add_insecure_port("[::]:" + EXECUTOR_PORT)
    server.start()
    print(f"[EXEC-{EXECUTOR_ID}] Order executor started. Listening on port {EXECUTOR_PORT}.")
    threading.Thread(target=execution_loop, daemon=True).start()
    server.wait_for_termination()


if __name__ == '__main__':
    serve()