import sys
import os
import grpc
import time
import threading
from concurrent import futures

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
order_executor_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_executor'))
sys.path.insert(0, order_executor_grpc_path)
import order_executor_pb2 as order_executor
import order_executor_pb2_grpc as order_executor_grpc

order_queue_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, order_queue_grpc_path)
import order_queue_pb2 as order_queue
import order_queue_pb2_grpc as order_queue_grpc

# Executor ID and port from environment variables
# Each replica has a unique ID and port
EXECUTOR_ID = os.getenv("EXECUTOR_ID", "1")
EXECUTOR_PORT = os.getenv("EXECUTOR_PORT", "60001")

# All executor replicas in the system
# Add more replicas here if needed
EXECUTOR_REPLICAS = [
    {"id": "1", "host": "order_executor_1", "port": "60001"},
    {"id": "2", "host": "order_executor_2", "port": "60002"},
]

# Current leader ID - None means no leader elected yet
current_leader = None
leader_lock = threading.Lock()

# Get stub for order queue service
def get_queue_stub():
    channel = grpc.insecure_channel('order_queue:50054')
    return order_queue_grpc.OrderQueueServiceStub(channel)

# Get stub for another executor replica
def get_executor_stub(host, port):
    channel = grpc.insecure_channel(f'{host}:{port}')
    return order_executor_grpc.OrderExecutorServiceStub(channel)

# Create a class to define the server functions, derived from
# order_executor_pb2_grpc.OrderExecutorServiceServicer
class OrderExecutorService(order_executor_grpc.OrderExecutorServiceServicer):

    # Create an RPC function to handle election messages (Bully Algorithm)
    # Called by lower ID executors to start an election
    def StartElection(self, request, context):
        global current_leader
        candidate_id = request.candidate_id
        print(f"[EXEC-{EXECUTOR_ID}] Received election message from {candidate_id}")
        # If my ID is higher I take over the election
        if int(EXECUTOR_ID) > int(candidate_id):
            print(f"[EXEC-{EXECUTOR_ID}] I have higher ID, taking over election")
            threading.Thread(target=start_bully_election).start()
        return order_executor.ElectionResponse(acknowledged=True)

    # Create an RPC function to receive leader announcement
    # Called by the winner to announce itself as leader
    def AnnounceLeader(self, request, context):
        global current_leader
        with leader_lock:
            current_leader = request.leader_id
        print(f"[EXEC-{EXECUTOR_ID}] New leader announced: {current_leader}")
        return order_executor.LeaderResponse(acknowledged=True)

    # Create an RPC function to get current leader
    def GetLeader(self, request, context):
        return order_executor.GetLeaderResponse(leader_id=current_leader or "")

# Bully Election Algorithm
# Higher ID always wins the election
def start_bully_election():
    global current_leader
    print(f"[EXEC-{EXECUTOR_ID}] Starting bully election")

    # Send election message to all higher ID replicas
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
                    print(f"[EXEC-{EXECUTOR_ID}] Higher replica {replica['id']} acknowledged")
            except Exception:
                # Replica is down, skip it
                print(f"[EXEC-{EXECUTOR_ID}] Replica {replica['id']} is unreachable")

    # If no higher replica replied I am the leader
    if not higher_replied:
        with leader_lock:
            current_leader = EXECUTOR_ID
        print(f"[EXEC-{EXECUTOR_ID}] I am the new leader!")
        # Announce leadership to all other replicas
        announce_leadership()

# Announce leadership to all other replicas
def announce_leadership():
    for replica in EXECUTOR_REPLICAS:
        if replica["id"] != EXECUTOR_ID:
            try:
                stub = get_executor_stub(replica["host"], replica["port"])
                stub.AnnounceLeader(
                    order_executor.LeaderRequest(leader_id=EXECUTOR_ID),
                    timeout=2
                )
                print(f"[EXEC-{EXECUTOR_ID}] Announced leadership to replica {replica['id']}")
            except Exception:
                # Replica is down, skip it
                print(f"[EXEC-{EXECUTOR_ID}] Could not announce to replica {replica['id']}")

# Main execution loop
# Only the leader dequeues and executes orders
def execution_loop():
    global current_leader
    print(f"[EXEC-{EXECUTOR_ID}] Starting execution loop")

    while True:
        try:
            # Start election if no leader exists
            if current_leader is None:
                start_bully_election()
                time.sleep(2)
                continue

            # Only the leader dequeues orders
            if current_leader == EXECUTOR_ID:
                stub = get_queue_stub()
                resp = stub.Dequeue(order_queue.DequeueRequest(executor_id=EXECUTOR_ID))

                if resp.success:
                    # Execute the order
                    print(f"[EXEC-{EXECUTOR_ID}] Order is being executed... order_id={resp.order.order_id}")
                    print(f"[EXEC-{EXECUTOR_ID}] User: {resp.order.user_name} | Items: {list(resp.order.items)}")
                    # Simulate execution time
                    time.sleep(1)
                else:
                    # Queue is empty wait before retrying
                    time.sleep(2)
            else:
                # Not the leader just wait
                time.sleep(2)

        except Exception as e:
            # Leader might have crashed start new election
            print(f"[EXEC-{EXECUTOR_ID}] Error in execution loop: {e}")
            with leader_lock:
                current_leader = None
            time.sleep(2)

def serve():
    # Create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor())
    # Add OrderExecutorService
    order_executor_grpc.add_OrderExecutorServiceServicer_to_server(
        OrderExecutorService(), server
    )
    # Listen on executor port from environment variable
    server.add_insecure_port("[::]:" + EXECUTOR_PORT)
    # Start the server
    server.start()
    print(f"Order executor {EXECUTOR_ID} started. Listening on port {EXECUTOR_PORT}.")

    # Start execution loop in background thread
    threading.Thread(target=execution_loop, daemon=True).start()

    # Wait for election to settle then start
    time.sleep(3)
    start_bully_election()

    # Keep thread alive
    server.wait_for_termination()

if __name__ == '__main__':
    serve()