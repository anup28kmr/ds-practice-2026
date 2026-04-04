import sys
import os
import grpc
from concurrent import futures

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
order_queue_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, order_queue_grpc_path)
import order_queue_pb2 as order_queue
import order_queue_pb2_grpc as order_queue_grpc

from collections import deque
import threading

# In-memory order queue
# Protected by a lock for thread safety
queue = deque()
queue_lock = threading.Lock()

# Create a class to define the server functions, derived from
# order_queue_pb2_grpc.OrderQueueServiceServicer
class OrderQueueService(order_queue_grpc.OrderQueueServiceServicer):

    # Create an RPC function to enqueue an order
    def Enqueue(self, request, context):
        # Acquire lock before modifying the queue
        with queue_lock:
            queue.append(request.order)
            print(f"[QUEUE] Enqueued order | order_id={request.order.order_id} | queue_size={len(queue)}")
        return order_queue.EnqueueResponse(
            success=True,
            message=f"Order {request.order.order_id} enqueued successfully"
        )

    # Create an RPC function to dequeue an order
    def Dequeue(self, request, context):
        # Acquire lock before modifying the queue
        with queue_lock:
            if len(queue) == 0:
                print(f"[QUEUE] Dequeue attempted by {request.executor_id} | queue is empty")
                return order_queue.DequeueResponse(
                    success=False,
                    message="Queue is empty"
                )
            # Pop the first order from the queue
            order = queue.popleft()
            print(f"[QUEUE] Dequeued order | order_id={order.order_id} | executor={request.executor_id} | queue_size={len(queue)}")
        return order_queue.DequeueResponse(
            success=True,
            order=order,
            message=f"Order {order.order_id} dequeued successfully"
        )

def serve():
    # Create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor())
    # Add OrderQueueService
    order_queue_grpc.add_OrderQueueServiceServicer_to_server(OrderQueueService(), server)
    # Listen on port 50054
    port = "50054"
    server.add_insecure_port("[::]:" + port)
    # Start the server
    server.start()
    print("Order queue server started. Listening on port 50054.")
    # Keep thread alive
    server.wait_for_termination()

if __name__ == '__main__':
    serve()