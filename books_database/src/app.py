import json
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

# Phase 6: participant-failure bonus. Staged transactions are persisted to
# STATE_DIR at the moment the participant votes commit so that, if the
# container restarts between Prepare and Commit, the participant can
# recover the staged state from disk and the coordinator's Commit retry
# can still succeed.
STATE_DIR = os.getenv("STATE_DIR", "/app/state")

# Soft fail injection: make the next N Commit RPCs return UNAVAILABLE so
# we can demonstrate the coordinator's retry loop without having to crash
# the container (which would trigger a leader failover and lose the
# primary-only pending buffer). Gets decremented on each injected failure.
_fail_next_commit_counter = [int(os.getenv("FAIL_NEXT_COMMIT", "0"))]

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
kv_store = {}  # populated in serve() from disk or SEED_STOCK
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
#
# Phase 6: pending_orders is also persisted to STATE_DIR on vote_commit.
# Startup recovery re-loads the map so a container restart between
# Prepare and Commit does not lose the reservation.
pending_lock = threading.Lock()
pending_orders = {}

# committed_orders lets Commit distinguish three cases during a retry:
#   (a) order_id is still pending -> apply the decrement
#   (b) order_id is in committed_orders -> idempotent success (safe no-op)
#   (c) order_id is in neither -> uncertain; refuse with success=False so
#       the coordinator keeps retrying until the right replica (the one
#       that ran Prepare) becomes reachable again.
# Case (c) is what protects us during a brief failover window: a freshly
# elected primary that never saw Prepare must NOT pretend it committed
# the order. committed_orders is a set of order_ids (bounded by the
# number of orders processed this container lifetime -- fine for a demo).
committed_orders = set()
aborted_orders = set()


def _txn_file(order_id):
    safe = order_id.replace("/", "_").replace("\\", "_")
    return os.path.join(STATE_DIR, f"txn_{safe}.json")


def persist_pending(order_id, items):
    """Atomically persist a staged transaction before voting commit.

    Write-then-rename: write the full JSON payload to `<file>.tmp`, then
    `os.replace` it onto the final path. POSIX guarantees replace is
    atomic, so a crash between the two steps leaves either the old file
    (no change) or the new file (complete), never a truncated half-file
    that `load_persisted_all` could misread on recovery.
    """
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _txn_file(order_id)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"items": [[t, q] for t, q in items]}, f)
    os.replace(tmp, path)


def remove_persisted(order_id):
    """Drop the on-disk staged-transaction file for `order_id`. Called
    once Commit (apply + replicate) or Abort has succeeded, because the
    in-memory state is now authoritative. Missing file is a no-op so
    recovery and steady-state paths can both call this unconditionally."""
    try:
        os.remove(_txn_file(order_id))
    except FileNotFoundError:
        pass


def load_persisted_all():
    """Recovery scan: read every `txn_*.json` in STATE_DIR and return the
    staged items keyed by order_id. Called once at process start before
    `serve()` accepts RPCs so the replica can rebuild `pending_orders`
    exactly as the previous instance left it, letting a retrying
    coordinator's Commit or Abort finish the transaction."""
    if not os.path.isdir(STATE_DIR):
        return {}
    out = {}
    for fname in os.listdir(STATE_DIR):
        if not (fname.startswith("txn_") and fname.endswith(".json")):
            continue
        path = os.path.join(STATE_DIR, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            order_id = fname[len("txn_"):-len(".json")]
            out[order_id] = [(t, int(q)) for t, q in data["items"]]
        except Exception as exc:
            print(f"[DB-{REPLICA_ID}] recovery_skip file={fname} err={exc!r}")
    return out


def _kv_store_path():
    return os.path.join(STATE_DIR, "kv_store.json")


def persist_kv_store():
    """Atomically flush the current kv_store to disk so a restarted
    replica comes back with post-commit stock, not the hard-coded
    SEED_STOCK. Same write-then-rename pattern as persist_pending.

    The temp file includes the thread id so that concurrent callers
    (e.g. parallel ReplicateWrite handlers for different keys) each
    write to their own temp file and never race on os.replace."""
    os.makedirs(STATE_DIR, exist_ok=True)
    path = _kv_store_path()
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with kv_state_lock:
        snapshot = dict(kv_store)
    with open(tmp, "w") as f:
        json.dump(snapshot, f)
    os.replace(tmp, path)


def load_kv_store():
    """Load kv_store from disk if a previous instance persisted it,
    otherwise fall back to SEED_STOCK for a fresh container."""
    path = _kv_store_path()
    if os.path.isfile(path):
        try:
            with open(path) as f:
                data = json.load(f)
            return {k: int(v) for k, v in data.items()}
        except Exception as exc:
            print(f"[DB-{REPLICA_ID}] kv_store_load_failed err={exc!r} falling_back_to=SEED_STOCK")
    return dict(SEED_STOCK)


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


def start_election(force=False):
    global election_in_progress, leader_id

    with state_lock:
        if election_in_progress:
            return
        # Normal path: if we already have a fresh leader, do nothing.
        # Forced path: a recovering higher-ID replica is allowed to
        # challenge a lower-ID active leader, matching the bully rule
        # that the highest alive replica should eventually win.
        if (not force) and has_fresh_leader_locked():
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
#
# Persistent gRPC channels for backup replication. Creating a fresh
# channel per call works under low concurrency but causes a connection
# storm when several writes fan out to backups simultaneously (each
# write opens 2 new TCP connections). Caching one channel per peer lets
# gRPC multiplex all RPCs over a single HTTP/2 connection.

_replication_channels = {}
_replication_channels_lock = threading.Lock()


def _get_replication_channel(addr):
    with _replication_channels_lock:
        ch = _replication_channels.get(addr)
        if ch is None:
            ch = grpc.insecure_channel(addr)
            _replication_channels[addr] = ch
        return ch


def replicate_to_backups(title, quantity, seq):
    targets = [(pid, addr) for pid, addr in PEERS if pid != REPLICA_ID]
    results = {}

    def do_one(pid, addr):
        try:
            ch = _get_replication_channel(addr)
            stub = db_grpc.BooksDatabaseServiceStub(ch)
            resp = stub.ReplicateWrite(
                db_pb2.ReplicateWriteRequest(
                    title=title,
                    quantity=quantity,
                    seq=seq,
                    from_replica=REPLICA_ID,
                ),
                timeout=REPLICATE_TIMEOUT,
            )
            results[pid] = resp
        except Exception as exc:
            print(
                f"[DB-{REPLICA_ID}] replicate_rpc_error "
                f"peer={pid} title=\"{title}\" seq={seq} err={exc!r}"
            )
            results[pid] = None

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
            persist_kv_store()

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
            persist_kv_store()

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

            # Persist first, then mark pending. If persist fails (disk error)
            # the pending buffer stays empty and we vote abort.
            try:
                persist_pending(order_id, items)
            except Exception as exc:
                print(
                    f"[DB-{REPLICA_ID}] prepare_persist_failed "
                    f"order={order_id} err={exc!r}"
                )
                return db_pb2.PrepareResponse(
                    vote_commit=False,
                    message=f"persist failed: {exc!r}",
                )
            pending_orders[order_id] = items

        items_repr = ",".join(f"{t}x{q}" for t, q in items)
        print(
            f"[DB-{REPLICA_ID}] prepare_vote_commit "
            f"order={order_id} items=[{items_repr}] persisted=yes"
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

        # Phase 6 fail injection. If the env var asked us to fail the next N
        # Commits, do so without touching kv_store or the pending entry so
        # the coordinator's retry (after the counter reaches zero) still
        # finds the reservation and completes the transaction.
        if _fail_next_commit_counter[0] > 0:
            _fail_next_commit_counter[0] -= 1
            remaining = _fail_next_commit_counter[0]
            print(
                f"[DB-{REPLICA_ID}] commit_fail_injected "
                f"order={order_id} remaining_failures={remaining}"
            )
            return db_pb2.CommitResponse(
                success=False,
                message=f"injected failure; retry (remaining={remaining})",
            )

        with pending_lock:
            items = pending_orders.get(order_id)
            if items is None:
                # No pending reservation. Distinguish "already committed"
                # (safe, idempotent success) from "never heard of this
                # order" (uncertain; refuse so the coordinator retries
                # against the replica that did see Prepare).
                if order_id in committed_orders:
                    print(
                        f"[DB-{REPLICA_ID}] commit_idempotent "
                        f"order={order_id} reason=already-committed"
                    )
                    return db_pb2.CommitResponse(
                        success=True, message="already committed"
                    )
                print(
                    f"[DB-{REPLICA_ID}] commit_unknown "
                    f"order={order_id} reason=no-pending-no-record"
                )
                return db_pb2.CommitResponse(
                    success=False,
                    message="unknown order; never prepared on this replica",
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
            committed_orders.add(order_id)
            remove_persisted(order_id)
            persist_kv_store()

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
            aborted_orders.add(order_id)
            remove_persisted(order_id)

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
    global kv_store
    # Load committed stock from disk (survives restarts) or fall back to
    # the hard-coded SEED_STOCK for a fresh container.
    kv_store = load_kv_store()
    loaded_from = "disk" if os.path.isfile(_kv_store_path()) else "SEED_STOCK"
    print(
        f"[DB-{REPLICA_ID}] kv_store_loaded from={loaded_from} "
        f"titles={list(kv_store.keys())}"
    )

    # Phase 6 recovery: reload any staged transactions the previous instance
    # persisted before it died. From this point on pending_orders is
    # authoritative again and the coordinator's next Commit or Abort will
    # finish the transaction.
    recovered = load_persisted_all()
    if recovered:
        with pending_lock:
            pending_orders.update(recovered)
        for oid, items in recovered.items():
            items_repr = ",".join(f"{t}x{q}" for t, q in items)
            print(
                f"[DB-{REPLICA_ID}] recovered_pending "
                f"order={oid} items=[{items_repr}]"
            )

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    db_grpc.add_BooksDatabaseServiceServicer_to_server(
        BooksDatabaseService(), server
    )
    server.add_insecure_port("[::]:" + REPLICA_PORT)
    server.start()
    print(
        f"[DB-{REPLICA_ID}] listening on port {REPLICA_PORT} "
        f"seeded_titles={list(SEED_STOCK.keys())} "
        f"state_dir={STATE_DIR} "
        f"recovered_pending={len(recovered)} "
        f"fail_next_commit={_fail_next_commit_counter[0]}"
    )

    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=timeout_loop, daemon=True).start()

    time.sleep(1.0)
    with state_lock:
        current_leader = leader_id
        should_start = (
            (not election_in_progress)
            and (
                current_leader is None
                or current_leader < REPLICA_ID
            )
        )
    if should_start:
        # If a lower-ID leader is already active when this replica comes
        # back, proactively challenge it so the highest live replica can
        # reclaim primary as expected by the tests and bully semantics.
        start_election(force=(current_leader is not None and current_leader < REPLICA_ID))

    server.wait_for_termination()


if __name__ == "__main__":
    serve()
