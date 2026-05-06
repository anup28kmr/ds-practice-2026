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

EXECUTOR_ID   = os.getenv("EXECUTOR_ID", "1")
EXECUTOR_PORT = os.getenv("EXECUTOR_PORT", "60001")

EXECUTOR_REPLICAS = [
    {"id": "1", "host": "order_executor_1", "port": "60001"},
    {"id": "2", "host": "order_executor_2", "port": "60002"},
    {"id": "3", "host": "order_executor_3", "port": "60003"},
]

current_leader       = None
leader_lock          = threading.Lock()

election_in_progress = False
election_lock        = threading.Lock()


# ─── gRPC stubs ────────────────────────────────────────────────────────────────

def get_queue_stub():
    channel = grpc.insecure_channel('order_queue:50054')
    return order_queue_grpc.OrderQueueServiceStub(channel)

def get_db_stub():
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
                # Leader already known — re-announce so the caller stops
                threading.Thread(target=announce_leadership, daemon=True).start()

        return order_executor.ElectionResponse(acknowledged=True)

    def AnnounceLeader(self, request, context):
        global current_leader, election_in_progress
        new_leader = request.leader_id
        with leader_lock:
            current_leader = new_leader
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

    # Skip if a leader is already known
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
    transaction_id = str(uuid.uuid4())
    db_stub        = get_db_stub()
    payment_stub   = get_payment_stub()

    book_title = list(order.items)[0] if order.items else "Book A"
    quantity   = 1
    amount     = 50.0

    print(f"[EXEC-{EXECUTOR_ID}] 2pc_start order={order.order_id} tx={transaction_id} book={book_title}")

    # Phase 1 – Prepare
    db_vote = payment_vote = False

    try:
        r = db_stub.Prepare(books_database.PrepareRequest(
            transaction_id=transaction_id, order_id=order.order_id,
            book_title=book_title, quantity=quantity))
        db_vote = r.ready
    except Exception as e:
        print(f"[EXEC-{EXECUTOR_ID}] db_prepare_failed order={order.order_id} error={e}")

    try:
        r = payment_stub.Prepare(payment_service.PrepareRequest(
            transaction_id=transaction_id, order_id=order.order_id, amount=amount))
        payment_vote = r.ready
    except Exception as e:
        print(f"[EXEC-{EXECUTOR_ID}] payment_prepare_failed order={order.order_id} error={e}")

    print(f"[EXEC-{EXECUTOR_ID}] 2pc_votes order={order.order_id} "
          f"db=(vote_commit={db_vote}) payment=(vote_commit={payment_vote})")

    # Phase 2 – Commit or Abort
    if db_vote and payment_vote:
        print(f"[EXEC-{EXECUTOR_ID}] 2pc_decision order={order.order_id} decision=COMMIT participants=[db,payment]")
        try:
            r = db_stub.Commit(books_database.CommitRequest(
                transaction_id=transaction_id, order_id=order.order_id))
            print(f"[EXEC-{EXECUTOR_ID}] db_commit_result order={order.order_id} success={r.success}")
        except Exception as e:
            print(f"[EXEC-{EXECUTOR_ID}] db_commit_failed order={order.order_id} error={e}")
        try:
            r = payment_stub.Commit(payment_service.CommitRequest(
                transaction_id=transaction_id, order_id=order.order_id))
            print(f"[EXEC-{EXECUTOR_ID}] payment_commit_result order={order.order_id} success={r.success}")
        except Exception as e:
            print(f"[EXEC-{EXECUTOR_ID}] payment_commit_failed order={order.order_id} error={e}")
        print(f"[EXEC-{EXECUTOR_ID}] 2pc_commit_applied order={order.order_id} tx={transaction_id}")
        return True

    else:
        print(f"[EXEC-{EXECUTOR_ID}] 2pc_decision order={order.order_id} decision=ABORT participants=[db,payment]")
        try:
            db_stub.Abort(books_database.AbortRequest(
                transaction_id=transaction_id, order_id=order.order_id))
            print(f"[EXEC-{EXECUTOR_ID}] db_aborted order={order.order_id}")
        except Exception as e:
            print(f"[EXEC-{EXECUTOR_ID}] db_abort_failed order={order.order_id} error={e}")
        try:
            payment_stub.Abort(payment_service.AbortRequest(
                transaction_id=transaction_id, order_id=order.order_id))
            print(f"[EXEC-{EXECUTOR_ID}] payment_aborted order={order.order_id}")
        except Exception as e:
            print(f"[EXEC-{EXECUTOR_ID}] payment_abort_failed order={order.order_id} error={e}")
        print(f"[EXEC-{EXECUTOR_ID}] 2pc_aborted order={order.order_id} tx={transaction_id}")
        return False


# ─── Execution loop ────────────────────────────────────────────────────────────

def execution_loop():
    global current_leader
    print(f"[EXEC-{EXECUTOR_ID}] starting execution_loop")

    # Higher ID fires election sooner:
    # EXEC-3 → 2.0 s,  EXEC-2 → 2.5 s,  EXEC-1 → 3.0 s
    # By the time EXEC-2 and EXEC-1 wake up, EXEC-3 has already won
    # and sent AnnounceLeader, so their maybe_start_election() is a no-op.
    sleep_before_election = 2.0 + (3 - int(EXECUTOR_ID)) * 0.5
    time.sleep(sleep_before_election)

    maybe_start_election()   # no-op if leader already announced

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
    server = grpc.server(futures.ThreadPoolExecutor())
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