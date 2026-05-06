import sys
import os
import grpc
import json
import threading
from concurrent import futures

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
books_database_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/books_database'))
sys.path.insert(0, books_database_grpc_path)
import books_database_pb2 as books_database
import books_database_pb2_grpc as books_database_grpc

# Database role and port from environment variables
# PRIMARY handles writes and replicates to backups
# BACKUP  handles reads and receives updates from primary
DB_ROLE = os.getenv("DB_ROLE", "PRIMARY")
DB_PORT = os.getenv("DB_PORT", "60011")
DB_ID   = os.getenv("DB_ID", "1")

# Backup replica addresses used by primary for replication
BACKUP_REPLICAS = [
    {"host": "books_database_2", "port": "60012"},
    {"host": "books_database_3", "port": "60013"},
]

# State directory for persistence
STATE_DIR = f"/app/books_database/state/{DB_ID}"
KV_STORE_PATH = f"{STATE_DIR}/kv_store.json"
os.makedirs(STATE_DIR, exist_ok=True)

# In-memory key-value store: { book_title: stock }
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

# Load from disk if exists otherwise use defaults
def load_kv_store():
    if os.path.exists(KV_STORE_PATH):
        with open(KV_STORE_PATH, "r") as f:
            store = json.load(f)
        print(f"[DB-{DB_ID}] loaded kv_store from disk | keys={list(store.keys())}")
        return store
    return dict(DEFAULT_STOCK)

# Save kv store to disk atomically using write-then-rename
def save_kv_store(store):
    tmp_path = KV_STORE_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(store, f)
    os.replace(tmp_path, KV_STORE_PATH)

data_store = load_kv_store()
data_lock = threading.Lock()

# Per-key locks for concurrent write serialization
key_locks = {}
key_locks_lock = threading.Lock()

def get_key_lock(key):
    with key_locks_lock:
        if key not in key_locks:
            key_locks[key] = threading.Lock()
        return key_locks[key]

# Sequence number for write ordering
seq_counter = 0
seq_lock = threading.Lock()

def next_seq():
    global seq_counter
    with seq_lock:
        seq_counter += 1
        return seq_counter

# Pending transactions store for 2PC
# { transaction_id: { order_id, book_title, new_stock } }
pending_transactions = {}
pending_lock = threading.Lock()

# Helper function to get backup stub
def get_backup_stub(host, port):
    channel = grpc.insecure_channel(f'{host}:{port}')
    return books_database_grpc.BooksDatabaseServiceStub(channel)

# Create a class to define the server functions, derived from
# books_database_pb2_grpc.BooksDatabaseServiceServicer
class BooksDatabaseService(books_database_grpc.BooksDatabaseServiceServicer):

    # Create an RPC function to read a book stock
    # Both primary and backup can serve reads
    def Read(self, request, context):
        with data_lock:
            stock = data_store.get(request.book_title, -1)
            found = stock != -1
        print(f"[DB-{DB_ID}] read book={request.book_title} stock={stock} found={found}")
        return books_database.ReadResponse(
            book_title=request.book_title,
            stock=stock,
            found=found
        )

    # Create an RPC function to write a book stock directly
    # Used for replication from primary to backups
    def Write(self, request, context):
        key_lock = get_key_lock(request.book_title)
        with key_lock:
            with data_lock:
                old_stock = data_store.get(request.book_title, -1)
                data_store[request.book_title] = request.stock
                save_kv_store(data_store)
        print(f"[DB-{DB_ID}] replicate_applied from_primary={DB_ID} seq={request.seq} book={request.book_title} old={old_stock} new={request.stock}")
        return books_database.WriteResponse(success=True, message="Write successful")

    # Create an RPC function for 2PC Phase 1 - Prepare
    # Check stock availability and stage the transaction
    def Prepare(self, request, context):
        with data_lock:
            current_stock = data_store.get(request.book_title, 0)

        # Check if enough stock is available
        if current_stock < request.quantity:
            print(f"[DB-{DB_ID}] prepare_vote_abort order={request.order_id} reasons=[insufficient_stock current={current_stock} requested={request.quantity}]")
            return books_database.PrepareResponse(
                ready=False,
                message=f"insufficient_stock current={current_stock} requested={request.quantity}",
                new_stock=current_stock
            )

        # Stage the transaction
        new_stock = current_stock - request.quantity
        with pending_lock:
            pending_transactions[request.transaction_id] = {
                "order_id": request.order_id,
                "book_title": request.book_title,
                "new_stock": new_stock,
                "old_stock": current_stock
            }

        print(f"[DB-{DB_ID}] prepare_vote_commit order={request.order_id} persisted=yes book={request.book_title} old={current_stock} new={new_stock}")
        return books_database.PrepareResponse(
            ready=True,
            message="Database ready to commit",
            new_stock=new_stock
        )

    # Create an RPC function for 2PC Phase 2 - Commit
    # Apply the staged transaction and replicate to backups
    def Commit(self, request, context):
        with pending_lock:
            tx = pending_transactions.pop(request.transaction_id, None)

        if not tx:
            return books_database.CommitResponse(success=False, message="Transaction not found")

        seq = next_seq()
        key_lock = get_key_lock(tx["book_title"])

        with key_lock:
            with data_lock:
                data_store[tx["book_title"]] = tx["new_stock"]
                save_kv_store(data_store)

        backups_acked = []

        # If primary replicate commit to all backups
        if DB_ROLE == "PRIMARY":
            for replica in BACKUP_REPLICAS:
                try:
                    stub = get_backup_stub(replica["host"], replica["port"])
                    stub.Write(books_database.WriteRequest(
                        book_title=tx["book_title"],
                        stock=tx["new_stock"],
                        transaction_id=request.transaction_id,
                        seq=seq
                    ))
                    backups_acked.append(replica["host"])
                    print(f"[DB-{DB_ID}] replicated_to backup={replica['host']} seq={seq}")
                except Exception as e:
                    print(f"[DB-{DB_ID}] replication_failed backup={replica['host']} error={e}")

        print(f"[DB-{DB_ID}] commit_applied order={request.order_id} seq={seq} old={tx['old_stock']} new={tx['new_stock']} backups_acked={backups_acked}")
        return books_database.CommitResponse(success=True, message="Committed successfully")

    # Create an RPC function for 2PC Phase 2 - Abort
    # Discard the staged transaction
    def Abort(self, request, context):
        with pending_lock:
            tx = pending_transactions.pop(request.transaction_id, None)
        print(f"[DB-{DB_ID}] abort_ok order={request.order_id} tx={request.transaction_id}")
        return books_database.AbortResponse(success=True)

def serve():
    # Create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor())
    # Add BooksDatabaseService
    books_database_grpc.add_BooksDatabaseServiceServicer_to_server(
        BooksDatabaseService(), server
    )
    # Listen on database port from environment variable
    server.add_insecure_port("[::]:" + DB_PORT)
    # Start the server
    server.start()
    print(f"[DB-{DB_ID}] Books database ({DB_ROLE}) started. Listening on port {DB_PORT}.")
    # Keep thread alive
    server.wait_for_termination()

if __name__ == '__main__':
    serve()