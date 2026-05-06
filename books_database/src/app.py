import sys
import os
import grpc
import json
import threading
from concurrent import futures

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
books_database_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/books_database'))
sys.path.insert(0, books_database_grpc_path)
import books_database_pb2 as books_database
import books_database_pb2_grpc as books_database_grpc

# ─── Configuration ─────────────────────────────────────────────────────────────

DB_ROLE = os.getenv("DB_ROLE", "PRIMARY")
DB_PORT = os.getenv("DB_PORT", "60011")
DB_ID   = os.getenv("DB_ID", "1")

BACKUP_REPLICAS = [
    {"host": "books_database_2", "port": "60012"},
    {"host": "books_database_3", "port": "60013"},
]

# State directory — persists across container restarts
STATE_DIR      = f"/app/books_database/state/{DB_ID}"
KV_STORE_PATH  = f"{STATE_DIR}/kv_store.json"
PENDING_DIR    = f"{STATE_DIR}/pending"
os.makedirs(STATE_DIR,  exist_ok=True)
os.makedirs(PENDING_DIR, exist_ok=True)

DEFAULT_STOCK = {
    "Clean Code": 10,
    "The Pragmatic Programmer": 8,
    "Designing Data-Intensive Applications": 5,
    "Distributed Systems Basics": 12,
    "The Hobbit": 15,
    "Book A": 20,
    "Book B": 20,
    "Book C": 20,
}

# ─── Persistence helpers ────────────────────────────────────────────────────────

def load_kv_store():
    """Load committed stock from disk, or seed with defaults on first run."""
    if os.path.exists(KV_STORE_PATH):
        try:
            with open(KV_STORE_PATH, "r") as f:
                store = json.load(f)
            print(f"[DB-{DB_ID}] kv_store_loaded from=disk keys={list(store.keys())}")
            return {k: int(v) for k, v in store.items()}
        except Exception as e:
            print(f"[DB-{DB_ID}] kv_store_load_failed err={e} falling_back=DEFAULT_STOCK")
    return dict(DEFAULT_STOCK)

def save_kv_store(store):
    """Atomically write kv_store to disk (write-then-rename)."""
    tmp = KV_STORE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(store, f)
    os.replace(tmp, KV_STORE_PATH)

def _pending_path(transaction_id):
    safe = transaction_id.replace("/", "_").replace("\\", "_")
    return os.path.join(PENDING_DIR, f"txn_{safe}.json")

def persist_pending(transaction_id, order_id, book_title, old_stock, new_stock):
    """
    Write staged transaction to disk BEFORE voting commit.
    If the container crashes between Prepare and Commit the coordinator's
    retry will find the reservation on restart (crash-safe bonus requirement).
    Uses write-then-rename so a mid-write crash never leaves a corrupt file.
    """
    path = _pending_path(transaction_id)
    tmp  = path + ".tmp"
    payload = {
        "order_id":   order_id,
        "book_title": book_title,
        "old_stock":  old_stock,
        "new_stock":  new_stock,
    }
    with open(tmp, "w") as f:
        json.dump(payload, f)
    os.replace(tmp, path)

def remove_pending(transaction_id):
    """Drop on-disk staged txn. Missing file is a no-op (idempotent)."""
    try:
        os.remove(_pending_path(transaction_id))
    except FileNotFoundError:
        pass

def load_all_pending():
    """
    Recovery scan at startup: reload every staged transaction the previous
    instance persisted so a retrying coordinator's Commit can still succeed.
    """
    recovered = {}
    for fname in os.listdir(PENDING_DIR):
        if not (fname.startswith("txn_") and fname.endswith(".json")):
            continue
        path = os.path.join(PENDING_DIR, fname)
        try:
            with open(path) as f:
                data = json.load(f)
            tx_id = fname[len("txn_"):-len(".json")]
            recovered[tx_id] = data
        except Exception as e:
            print(f"[DB-{DB_ID}] recovery_skip file={fname} err={e}")
    return recovered

# ─── In-memory state ────────────────────────────────────────────────────────────

data_store  = load_kv_store()
data_lock   = threading.Lock()

# Per-key locks: two writes on different books run in parallel;
# two writes on the same book are serialized.
key_locks      = {}
key_locks_lock = threading.Lock()

def get_key_lock(key):
    with key_locks_lock:
        if key not in key_locks:
            key_locks[key] = threading.Lock()
        return key_locks[key]

# Sequence counter for write ordering across replicas
seq_counter = 0
seq_lock    = threading.Lock()

def next_seq():
    global seq_counter
    with seq_lock:
        seq_counter += 1
        return seq_counter

# 2PC participant state
# pending_transactions[tx_id] = { order_id, book_title, old_stock, new_stock }
pending_transactions = {}
pending_lock         = threading.Lock()

# Idempotency tracking — lets a coordinator safely retry Commit after a
# network blip without double-deducting stock.
committed_orders = set()   # order_ids that have been fully committed
aborted_orders   = set()   # order_ids that have been aborted

# ─── Persistent gRPC channels for backup replication ───────────────────────────
# One channel per backup, reused across calls (HTTP/2 multiplexing).

_backup_channels      = {}
_backup_channels_lock = threading.Lock()

def get_backup_stub(host, port):
    addr = f"{host}:{port}"
    with _backup_channels_lock:
        if addr not in _backup_channels:
            _backup_channels[addr] = grpc.insecure_channel(addr)
        return books_database_grpc.BooksDatabaseServiceStub(_backup_channels[addr])

# ─── Replication helper ────────────────────────────────────────────────────────

def replicate_to_backups(book_title, stock, seq):
    """
    Fan out a Write to all backups in parallel.
    Returns (acked_hosts, failed_hosts).
    """
    results = {}

    def do_one(replica):
        try:
            stub = get_backup_stub(replica["host"], replica["port"])
            resp = stub.Write(books_database.WriteRequest(
                book_title=book_title,
                stock=stock,
                seq=seq
            ), timeout=2.0)
            results[replica["host"]] = resp.success
        except Exception as e:
            print(f"[DB-{DB_ID}] replication_failed backup={replica['host']} err={e}")
            results[replica["host"]] = False

    threads = [threading.Thread(target=do_one, args=(r,)) for r in BACKUP_REPLICAS]
    for t in threads: t.start()
    for t in threads: t.join(timeout=3.0)

    acked  = [h for h, ok in results.items() if ok]
    failed = [h for h, ok in results.items() if not ok]
    return acked, failed

# ─── gRPC service ──────────────────────────────────────────────────────────────

class BooksDatabaseService(books_database_grpc.BooksDatabaseServiceServicer):

    # ── Read ───────────────────────────────────────────────────────────────────

    def Read(self, request, context):
        """Both PRIMARY and BACKUP can serve reads."""
        with data_lock:
            stock = data_store.get(request.book_title, -1)
        found = stock != -1
        print(f"[DB-{DB_ID}] read book={request.book_title} stock={stock} found={found}")
        return books_database.ReadResponse(
            book_title=request.book_title,
            stock=stock,
            found=found
        )

    # ── Write (replication RPC, called by primary on backups) ─────────────────

    def Write(self, request, context):
        """Called by the primary to replicate a committed value to this backup."""
        key_lock = get_key_lock(request.book_title)
        with key_lock:
            with data_lock:
                old = data_store.get(request.book_title, -1)
                data_store[request.book_title] = request.stock
                save_kv_store(data_store)
        print(f"[DB-{DB_ID}] replicate_applied seq={request.seq} "
              f"book={request.book_title} old={old} new={request.stock}")
        return books_database.WriteResponse(success=True, message="replicated")

    # ── 2PC Phase 1: Prepare ───────────────────────────────────────────────────

    def Prepare(self, request, context):
        """
        Check stock availability accounting for ALL existing reservations
        (not just raw stock). This prevents two concurrent orders from both
        passing Prepare when only one copy remains.

        If sufficient stock: stage the txn in memory AND on disk, vote commit.
        If insufficient:     vote abort immediately, stage nothing.
        """
        order_id       = request.order_id
        transaction_id = request.transaction_id
        book_title     = request.book_title
        quantity       = request.quantity

        with pending_lock:
            # Idempotent: already prepared for this transaction
            if transaction_id in pending_transactions:
                print(f"[DB-{DB_ID}] prepare_idempotent tx={transaction_id} order={order_id}")
                return books_database.PrepareResponse(
                    ready=True,
                    message="already prepared",
                    new_stock=pending_transactions[transaction_id]["new_stock"]
                )

            # Already committed — coordinator may be replaying after a crash
            if order_id in committed_orders:
                print(f"[DB-{DB_ID}] prepare_already_committed order={order_id}")
                return books_database.PrepareResponse(
                    ready=True, message="already committed", new_stock=0
                )

            with data_lock:
                current_stock = data_store.get(book_title, 0)

            # Subtract ALL existing reservations for this book across all
            # pending transactions so concurrent orders don't over-commit.
            already_reserved = sum(
                tx["old_stock"] - tx["new_stock"]
                for tx in pending_transactions.values()
                if tx["book_title"] == book_title
            )
            available = current_stock - already_reserved

            if available < quantity:
                print(f"[DB-{DB_ID}] prepare_vote_abort order={order_id} "
                      f"book={book_title} current={current_stock} "
                      f"reserved={already_reserved} available={available} requested={quantity}")
                return books_database.PrepareResponse(
                    ready=False,
                    message=f"insufficient stock: available={available} requested={quantity}",
                    new_stock=current_stock
                )

            new_stock = current_stock - quantity

            # Persist to disk BEFORE voting commit (crash-safe)
            try:
                persist_pending(transaction_id, order_id, book_title, current_stock, new_stock)
            except Exception as e:
                print(f"[DB-{DB_ID}] prepare_persist_failed order={order_id} err={e}")
                return books_database.PrepareResponse(
                    ready=False, message=f"persist failed: {e}", new_stock=current_stock
                )

            # Stage in memory
            pending_transactions[transaction_id] = {
                "order_id":   order_id,
                "book_title": book_title,
                "old_stock":  current_stock,
                "new_stock":  new_stock,
            }

        print(f"[DB-{DB_ID}] prepare_vote_commit order={order_id} tx={transaction_id} "
              f"book={book_title} old={current_stock} new={new_stock} persisted=yes")
        return books_database.PrepareResponse(
            ready=True, message="ready to commit", new_stock=new_stock
        )

    # ── 2PC Phase 2a: Commit ───────────────────────────────────────────────────

    def Commit(self, request, context):
        """
        Apply the staged stock decrement, replicate to all backups, then
        drop the pending entry and record the order as committed.

        Idempotent: if the coordinator retries after a successful commit
        (e.g. the ack was lost in transit) we return success immediately
        without touching stock again.
        """
        order_id       = request.order_id
        transaction_id = request.transaction_id

        with pending_lock:
            # Case 1: already committed — safe no-op for coordinator retries
            if order_id in committed_orders:
                print(f"[DB-{DB_ID}] commit_idempotent order={order_id} tx={transaction_id}")
                return books_database.CommitResponse(
                    success=True, message="already committed"
                )

            tx = pending_transactions.get(transaction_id)

            # Case 2: transaction unknown — never prepared on this replica
            # (can happen if coordinator talks to wrong replica after failover)
            if tx is None:
                print(f"[DB-{DB_ID}] commit_unknown order={order_id} tx={transaction_id}")
                return books_database.CommitResponse(
                    success=False, message="unknown transaction; never prepared"
                )

            book_title = tx["book_title"]
            new_stock  = tx["new_stock"]
            old_stock  = tx["old_stock"]

        seq      = next_seq()
        key_lock = get_key_lock(book_title)

        with key_lock:
            # Replicate BEFORE writing locally so backups are always at least
            # as up-to-date as the primary. If replication fails, leave the
            # pending entry in place so the coordinator can retry.
            if DB_ROLE == "PRIMARY":
                acked, failed = replicate_to_backups(book_title, new_stock, seq)
                if failed:
                    print(f"[DB-{DB_ID}] commit_replicate_failed order={order_id} "
                          f"book={book_title} missing={failed}")
                    return books_database.CommitResponse(
                        success=False,
                        message=f"replication incomplete: missing {failed}"
                    )
            else:
                acked = []

            # All backups acked — now apply locally
            with data_lock:
                data_store[book_title] = new_stock
                save_kv_store(data_store)

        # Clean up staging state
        with pending_lock:
            pending_transactions.pop(transaction_id, None)
            committed_orders.add(order_id)
        remove_pending(transaction_id)

        print(f"[DB-{DB_ID}] commit_applied order={order_id} tx={transaction_id} "
              f"seq={seq} book={book_title} old={old_stock} new={new_stock} "
              f"backups_acked={acked}")
        return books_database.CommitResponse(success=True, message="committed")

    # ── 2PC Phase 2b: Abort ────────────────────────────────────────────────────

    def Abort(self, request, context):
        """
        Discard the staged reservation. Fully idempotent — aborting an order
        that was never prepared or was already aborted is a silent no-op.
        """
        order_id       = request.order_id
        transaction_id = request.transaction_id

        with pending_lock:
            tx = pending_transactions.pop(transaction_id, None)
            aborted_orders.add(order_id)
        remove_pending(transaction_id)

        if tx is None:
            print(f"[DB-{DB_ID}] abort_noop order={order_id} tx={transaction_id}")
        else:
            print(f"[DB-{DB_ID}] abort_ok order={order_id} tx={transaction_id} "
                  f"book={tx['book_title']} reservation_released")
        return books_database.AbortResponse(success=True)


# ─── Server entry point ────────────────────────────────────────────────────────

def serve():
    global data_store

    # Load committed stock from disk (or seeds on first run)
    data_store = load_kv_store()

    # Crash recovery: reload any staged transactions the previous instance
    # persisted before dying. The coordinator's next Commit/Abort will finish
    # the transaction cleanly — this is the seminar bonus requirement.
    recovered = load_all_pending()
    if recovered:
        with pending_lock:
            for tx_id, payload in recovered.items():
                pending_transactions[tx_id] = payload
        for tx_id, payload in recovered.items():
            print(f"[DB-{DB_ID}] recovered_pending tx={tx_id} "
                  f"order={payload['order_id']} book={payload['book_title']} "
                  f"old={payload['old_stock']} new={payload['new_stock']}")
    else:
        print(f"[DB-{DB_ID}] no pending transactions to recover")

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    books_database_grpc.add_BooksDatabaseServiceServicer_to_server(
        BooksDatabaseService(), server
    )
    server.add_insecure_port("[::]:" + DB_PORT)
    server.start()
    print(f"[DB-{DB_ID}] Books database ({DB_ROLE}) started on port {DB_PORT} | "
          f"stock={list(data_store.keys())} | recovered_pending={len(recovered)}")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()