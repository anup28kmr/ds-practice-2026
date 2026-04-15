import os
import sys
import time
import threading
from concurrent import futures

import grpc

FILE = __file__ if "__file__" in globals() else os.getenv("PYTHONFILE", "")

db_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/books_database")
)
sys.path.insert(0, db_grpc_path)

import books_database_pb2 as db_pb2
import books_database_pb2_grpc as db_grpc


REPLICA_ID = int(os.getenv("REPLICA_ID", "1"))
REPLICA_PORT = os.getenv("REPLICA_PORT", "50058")
HEARTBEAT_INTERVAL = 2.0
LEADER_TIMEOUT = 5.0
REPLICATE_TIMEOUT = 2.0

SEED_STOCK = {
    "Book A": 10,
    "Book B": 6,
    "Book C": 20,
    "Distributed Systems Basics": 5,
    "Designing Data-Intensive Applications": 3,
}


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


# --- Bully election state ---

state_lock = threading.Lock()
leader_id = None
last_heartbeat = time.time()
is_leader = False
election_in_progress = False


# --- KV store state ---
#
# We use fine-grained per-key locks so two writes against *different* books
# can run in parallel while two writes against the *same* book are
# serialized. This gives us the concurrent-writes bonus (§4.4) without
# breaking read-validate-write atomicity. kv_state_lock is a short meta-lock
# that only covers lookups in key_locks and kv_store; it is never held while
# we fan out to backups.

kv_state_lock = threading.Lock()
kv_store = dict(SEED_STOCK)
key_locks = {}  # title -> threading.Lock

seq_lock = threading.Lock()
seq_counter = 0

# --- 2PC participant state ---
#
# pending_orders[order_id] = list of (title, quantity) reservations.
# A Prepare inserts the reservation here and we "hold" stock against it when
# evaluating subsequent Prepares. A Commit reads the reservation, applies
# the decrement to kv_store (and replicates to backups), then drops the
# pending entry. An Abort just drops. All three handlers take pending_lock
# so concurrent 2PC ops on the same or different orders serialize cleanly.
pending_lock = threading.Lock()
pending_orders = {}


def get_key_lock(title):
    with kv_state_lock:
        lock = key_locks.get(title)
        if lock is None:
            lock = threading.Lock()
            key_locks[title] = lock
        return lock


def peer_addr_for(pid):
    for p, addr in PEERS:
        if p == pid:
            return addr
    return ""


def has_fresh_leader_locked():
    if leader_id is None:
        return False
    if is_leader and leader_id == REPLICA_ID:
        return True
    return (time.time() - last_heartbeat) <= LEADER_TIMEOUT


def send_rpc(addr, fn):
    try:
        with grpc.insecure_channel(addr) as channel:
            stub = db_grpc.BooksDatabaseServiceStub(channel)
            return fn(stub)
    except Exception:
        return None


def announce_coordinator():
    for pid, addr in PEERS:
        if pid == REPLICA_ID:
            continue
        send_rpc(
            addr,
            lambda stub: stub.Coordinator(
                db_pb2.CoordinatorRequest(leader_id=REPLICA_ID),
                timeout=2.0,
            ),
        )


def start_election():
    global election_in_progress, leader_id

    with state_lock:
        if election_in_progress:
            return
        if has_fresh_leader_locked():
            return
        election_in_progress = True

    print(f"[DB-{REPLICA_ID}] starting election")

    higher_peers = [(pid, addr) for pid, addr in PEERS if pid > REPLICA_ID]
    got_answer = False

    for _pid, addr in higher_peers:
        response = send_rpc(
            addr,
            lambda stub: stub.Election(
                db_pb2.ElectionRequest(candidate_id=REPLICA_ID),
                timeout=2.0,
            ),
        )
        if response and response.alive:
            got_answer = True

    if not got_answer:
        become_leader()
        return

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
        leader_id = REPLICA_ID
        is_leader = True
        election_in_progress = False
        last_heartbeat = time.time()

    print(f"[DB-{REPLICA_ID}] became primary")
    announce_coordinator()


def heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        with state_lock:
            leader_now = is_leader
        if not leader_now:
            continue
        for pid, addr in PEERS:
            if pid == REPLICA_ID:
                continue
            send_rpc(
                addr,
                lambda stub: stub.Heartbeat(
                    db_pb2.HeartbeatRequest(leader_id=REPLICA_ID),
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
            if leader_id is None:
                continue
            expired = (time.time() - last_heartbeat) > LEADER_TIMEOUT
        if expired:
            print(f"[DB-{REPLICA_ID}] primary timeout detected")
            with state_lock:
                leader_id = None
            start_election()


# --- Replication helper (called by the primary on Write) ---

def replicate_to_backups(title, quantity, seq):
    targets = [(pid, addr) for pid, addr in PEERS if pid != REPLICA_ID]
    results = {}

    def do_one(pid, addr):
        resp = send_rpc(
            addr,
            lambda stub: stub.ReplicateWrite(
                db_pb2.ReplicateWriteRequest(
                    title=title,
                    quantity=quantity,
                    seq=seq,
                    from_replica=REPLICA_ID,
                ),
                timeout=REPLICATE_TIMEOUT,
            ),
        )
        results[pid] = resp

    threads = [
        threading.Thread(target=do_one, args=(pid, addr))
        for pid, addr in targets
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=REPLICATE_TIMEOUT + 1.0)

    acked = [pid for pid, r in results.items() if r is not None and r.success]
    missing = [pid for pid, _ in targets if pid not in acked]
    return acked, missing


# --- gRPC service ---

class BooksDatabaseService(db_grpc.BooksDatabaseServiceServicer):

    # Client-facing RPCs (Phase 2: primary-only for strong consistency).

    def Read(self, request, context):
        with state_lock:
            if not is_leader:
                msg = f"not primary; primary={leader_id}"
                print(f"[DB-{REPLICA_ID}] read_rejected title={request.title} reason={msg}")
                return db_pb2.ReadResponse(success=False, quantity=0, message=msg)

        key_lock = get_key_lock(request.title)
        with key_lock:
            with kv_state_lock:
                value = kv_store.get(request.title)

        if value is None:
            print(f"[DB-{REPLICA_ID}] read_miss title=\"{request.title}\"")
            return db_pb2.ReadResponse(
                success=False, quantity=0, message="unknown title"
            )

        print(
            f"[DB-{REPLICA_ID}] read_ok title=\"{request.title}\" value={value}"
        )
        return db_pb2.ReadResponse(success=True, quantity=value, message="ok")

    def ReadLocal(self, request, context):
        """Debug/ops read. Returns whatever this replica currently holds,
        regardless of leader status. Used only by the convergence check."""
        key_lock = get_key_lock(request.title)
        with key_lock:
            with kv_state_lock:
                value = kv_store.get(request.title)

        if value is None:
            return db_pb2.ReadResponse(
                success=False, quantity=0, message="unknown title"
            )
        return db_pb2.ReadResponse(success=True, quantity=value, message="ok")

    def Write(self, request, context):
        global seq_counter

        with state_lock:
            if not is_leader:
                msg = f"not primary; primary={leader_id}"
                print(
                    f"[DB-{REPLICA_ID}] write_rejected "
                    f"title=\"{request.title}\" reason={msg}"
                )
                return db_pb2.WriteResponse(success=False, message=msg)

        # Per-key lock: concurrent writes on the *same* title serialize here
        # while concurrent writes on *different* titles run in parallel.
        key_lock = get_key_lock(request.title)
        with key_lock:
            with kv_state_lock:
                old = kv_store.get(request.title)

            with seq_lock:
                seq_counter += 1
                seq = seq_counter

            acked, missing = replicate_to_backups(
                request.title, request.quantity, seq
            )

            if missing:
                print(
                    f"[DB-{REPLICA_ID}] write_failed "
                    f"title=\"{request.title}\" seq={seq} "
                    f"old={old} new={request.quantity} "
                    f"acked={acked} missing={missing}"
                )
                return db_pb2.WriteResponse(
                    success=False,
                    message=f"replication incomplete; missing backups {missing}",
                )

            with kv_state_lock:
                kv_store[request.title] = request.quantity

        print(
            f"[DB-{REPLICA_ID}] write_committed primary={REPLICA_ID} "
            f"title=\"{request.title}\" seq={seq} "
            f"old={old} new={request.quantity} backups_acked={acked}"
        )
        return db_pb2.WriteResponse(success=True, message="ok")

    # Internal RPCs.

    def ReplicateWrite(self, request, context):
        global seq_counter

        # Per-key lock on the backup too: defensive — the primary already
        # serializes replicates for the same key, but this guards against
        # any future path that might not.
        key_lock = get_key_lock(request.title)
        with key_lock:
            with kv_state_lock:
                old = kv_store.get(request.title)
                kv_store[request.title] = request.quantity
            with seq_lock:
                if request.seq > seq_counter:
                    seq_counter = request.seq

        print(
            f"[DB-{REPLICA_ID}] replicate_applied "
            f"from_primary={request.from_replica} "
            f"title=\"{request.title}\" seq={request.seq} "
            f"old={old} new={request.quantity}"
        )
        return db_pb2.ReplicateWriteResponse(success=True, message="ok")

    def WhoIsPrimary(self, request, context):
        with state_lock:
            current = leader_id if leader_id is not None else 0
        addr = peer_addr_for(current) if current else ""
        return db_pb2.WhoIsPrimaryResponse(leader_id=current, leader_addr=addr)

    # Bully election RPCs.

    def Election(self, request, context):
        global election_in_progress

        if REPLICA_ID <= request.candidate_id:
            return db_pb2.ElectionResponse(alive=False)

        print(f"[DB-{REPLICA_ID}] received election from {request.candidate_id}")

        with state_lock:
            already_leader = is_leader
            election_running = election_in_progress

        if already_leader:
            threading.Thread(target=announce_coordinator, daemon=True).start()
        elif not election_running:
            threading.Thread(target=start_election, daemon=True).start()

        return db_pb2.ElectionResponse(alive=True)

    def Coordinator(self, request, context):
        global leader_id, is_leader, election_in_progress, last_heartbeat
        with state_lock:
            leader_id = request.leader_id
            is_leader = leader_id == REPLICA_ID
            election_in_progress = False
            last_heartbeat = time.time()

        print(f"[DB-{REPLICA_ID}] new primary is {leader_id}")
        return db_pb2.Ack(ok=True)

    def Heartbeat(self, request, context):
        global leader_id, is_leader, last_heartbeat
        with state_lock:
            leader_id = request.leader_id
            is_leader = leader_id == REPLICA_ID
            last_heartbeat = time.time()
        return db_pb2.Ack(ok=True)

    # --- 2PC participant RPCs ---

    def Prepare(self, request, context):
        """Phase 1 of 2PC. Check that each requested item has enough stock
        once existing reservations are subtracted, then stage the order in
        pending_orders and return vote_commit=True. If any item is short,
        return vote_commit=False and stage nothing."""
        with state_lock:
            if not is_leader:
                msg = f"not primary; primary={leader_id}"
                print(
                    f"[DB-{REPLICA_ID}] prepare_rejected "
                    f"order={request.order_id} reason={msg}"
                )
                return db_pb2.PrepareResponse(vote_commit=False, message=msg)

        order_id = request.order_id
        items = [(it.title, it.quantity) for it in request.items]

        with pending_lock:
            if order_id in pending_orders:
                print(
                    f"[DB-{REPLICA_ID}] prepare_idempotent order={order_id} "
                    f"(already prepared)"
                )
                return db_pb2.PrepareResponse(
                    vote_commit=True, message="already prepared"
                )

            with kv_state_lock:
                stock_snapshot = {t: kv_store.get(t) for t, _ in items}

            reserved = {}
            for staged in pending_orders.values():
                for t, q in staged:
                    reserved[t] = reserved.get(t, 0) + q

            insufficient = []
            for title, qty in items:
                current = stock_snapshot.get(title)
                if current is None:
                    insufficient.append(f"{title}(unknown)")
                    continue
                available = current - reserved.get(title, 0)
                if available < qty:
                    insufficient.append(
                        f"{title}(want={qty},avail={available})"
                    )

            if insufficient:
                print(
                    f"[DB-{REPLICA_ID}] prepare_vote_abort "
                    f"order={order_id} reasons={insufficient}"
                )
                return db_pb2.PrepareResponse(
                    vote_commit=False,
                    message=f"insufficient stock: {insufficient}",
                )

            pending_orders[order_id] = items

        items_repr = ",".join(f"{t}x{q}" for t, q in items)
        print(
            f"[DB-{REPLICA_ID}] prepare_vote_commit "
            f"order={order_id} items=[{items_repr}]"
        )
        return db_pb2.PrepareResponse(vote_commit=True, message="ok")

    def Commit(self, request, context):
        """Phase 2 of 2PC. Apply the staged decrements to kv_store, replicate
        each one to the backups synchronously, then drop the pending entry.
        If any backup fails to ack, leave pending in place and report failure
        so the coordinator can retry."""
        global seq_counter

        with state_lock:
            if not is_leader:
                msg = f"not primary; primary={leader_id}"
                print(
                    f"[DB-{REPLICA_ID}] commit_rejected "
                    f"order={request.order_id} reason={msg}"
                )
                return db_pb2.CommitResponse(success=False, message=msg)

        order_id = request.order_id

        with pending_lock:
            items = pending_orders.get(order_id)
            if items is None:
                # Either never prepared or already committed. Treat as no-op.
                print(
                    f"[DB-{REPLICA_ID}] commit_noop order={order_id} "
                    f"reason=no-pending"
                )
                return db_pb2.CommitResponse(
                    success=True, message="no pending (already committed?)"
                )

            applied = []
            for title, qty in items:
                with kv_state_lock:
                    old = kv_store.get(title, 0)
                new_value = old - qty
                with seq_lock:
                    seq_counter += 1
                    seq = seq_counter

                acked, missing = replicate_to_backups(title, new_value, seq)
                if missing:
                    print(
                        f"[DB-{REPLICA_ID}] commit_replicate_failed "
                        f"order={order_id} title=\"{title}\" seq={seq} "
                        f"missing={missing}"
                    )
                    return db_pb2.CommitResponse(
                        success=False,
                        message=f"replication failed; missing {missing}",
                    )

                with kv_state_lock:
                    kv_store[title] = new_value
                applied.append((title, old, new_value, seq, acked))

            del pending_orders[order_id]

        for title, old, new_value, seq, acked in applied:
            print(
                f"[DB-{REPLICA_ID}] commit_applied order={order_id} "
                f"title=\"{title}\" seq={seq} old={old} new={new_value} "
                f"backups_acked={acked}"
            )
        return db_pb2.CommitResponse(success=True, message="ok")

    def Abort(self, request, context):
        """Drop the staged reservation for this order. Idempotent: aborting
        an order that was never prepared (or already committed/aborted) is
        a successful no-op."""
        order_id = request.order_id
        with pending_lock:
            items = pending_orders.pop(order_id, None)

        if items is None:
            print(f"[DB-{REPLICA_ID}] abort_noop order={order_id}")
            return db_pb2.AbortResponse(success=True, message="no pending")

        items_repr = ",".join(f"{t}x{q}" for t, q in items)
        print(
            f"[DB-{REPLICA_ID}] abort_ok order={order_id} "
            f"dropped=[{items_repr}]"
        )
        return db_pb2.AbortResponse(success=True, message="ok")


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    db_grpc.add_BooksDatabaseServiceServicer_to_server(
        BooksDatabaseService(), server
    )
    server.add_insecure_port("[::]:" + REPLICA_PORT)
    server.start()
    print(
        f"[DB-{REPLICA_ID}] listening on port {REPLICA_PORT} "
        f"seeded_titles={list(SEED_STOCK.keys())}"
    )

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=timeout_loop, daemon=True).start()

    time.sleep(1.0)
    with state_lock:
        should_start = (leader_id is None) and (not election_in_progress)
    if should_start:
        start_election()

    server.wait_for_termination()


if __name__ == "__main__":
    serve()
