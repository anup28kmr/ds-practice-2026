import sys
import os
import grpc
from concurrent import futures

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc

# In-memory store to cache order data temporarily
# Structure: { order_id: { user_name, card_number, vector_clock } }
order_store = {}

# Index of this service in the vector clock
# TV=0, FD=1, SG=2
SERVICE_INDEX = 1

# Helper function to merge two vector clocks
# Takes MAX of each slot, then increments own slot
def update_clock(local_vc, received_vc):
    merged = [max(local_vc[i], received_vc[i]) for i in range(3)]
    merged[SERVICE_INDEX] += 1
    return merged

# Create a class to define the server functions, derived from
# fraud_detection_pb2_grpc.FraudDetectionServiceServicer
class FraudDetectionService(fraud_detection_grpc.FraudDetectionServiceServicer):

    # Create an RPC function to initialize and cache order data
    def InitOrder(self, request, context):
        # Get vector clock from request or initialize fresh
        vc = list(request.vector_clock) if request.vector_clock else [0, 0, 0]
        # Update the vector clock
        vc = update_clock(vc, vc)
        # Cache the order data in memory
        order_store[request.order_id] = {
            "user_name": request.user_name,
            "user_contact": request.user_contact,
            "card_number": request.card_number,
            "vector_clock": vc
        }
        print(f"[FD] InitOrder | order_id={request.order_id} | VC={vc}")
        return fraud_detection.InitOrderResponse(success=True, vector_clock=vc)

    # Create an RPC function to check user data for fraud (Event d)
    # Can only run after event (b) completes, can overlap with event (c)
    def CheckUserDataForFraud(self, request, context):
        # Look up the cached order data
        order = order_store.get(request.order_id)
        if not order:
            return fraud_detection.FraudCheckResponse(
                is_fraud=True,
                reason="Order not found",
                vector_clock=[0, 0, 0]
            )
        # Merge incoming vector clock with local clock
        vc = update_clock(order["vector_clock"], list(request.vector_clock))
        order["vector_clock"] = vc
        print(f"[FD] Event d - CheckUserDataForFraud | order_id={request.order_id} | VC={vc}")
        # Dummy logic: flag user name containing "fraud"
        if "fraud" in order["user_name"].lower():
            return fraud_detection.FraudCheckResponse(
                is_fraud=True,
                reason="Suspicious user name detected",
                vector_clock=vc
            )
        return fraud_detection.FraudCheckResponse(
            is_fraud=False,
            reason="User data looks legitimate",
            vector_clock=vc
        )

    # Create an RPC function to check credit card for fraud (Event e)
    # Can only run after both event (c) and event (d) complete
    def CheckCreditCardForFraud(self, request, context):
        # Look up the cached order data
        order = order_store.get(request.order_id)
        if not order:
            return fraud_detection.FraudCheckResponse(
                is_fraud=True,
                reason="Order not found",
                vector_clock=[0, 0, 0]
            )
        # Merge incoming vector clock with local clock
        vc = update_clock(order["vector_clock"], list(request.vector_clock))
        order["vector_clock"] = vc
        print(f"[FD] Event e - CheckCreditCardForFraud | order_id={request.order_id} | VC={vc}")
        # Dummy logic: card starts with 999 is fraudulent
        if order["card_number"].startswith("999"):
            return fraud_detection.FraudCheckResponse(
                is_fraud=True,
                reason="Card number flagged as fraudulent",
                vector_clock=vc
            )
        return fraud_detection.FraudCheckResponse(
            is_fraud=False,
            reason="Credit card looks legitimate",
            vector_clock=vc
        )

    # Create an RPC function to clear cached order data (Broadcast)
    # Called by orchestrator at the end of the flow
    def ClearOrder(self, request, context):
        vc_final = list(request.vector_clock)
        order = order_store.get(request.order_id)
        if order:
            local_vc = order["vector_clock"]
            # Check local VC <= VCf before clearing
            if all(local_vc[i] <= vc_final[i] for i in range(3)):
                del order_store[request.order_id]
                print(f"[FD] ClearOrder | order_id={request.order_id} | VC check OK")
                return fraud_detection.ClearOrderResponse(success=True)
            else:
                print(f"[FD] ClearOrder | order_id={request.order_id} | VC check FAILED")
                return fraud_detection.ClearOrderResponse(success=False)
        return fraud_detection.ClearOrderResponse(success=True)

def serve():
    # Create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor())
    # Add FraudDetectionService
    fraud_detection_grpc.add_FraudDetectionServiceServicer_to_server(FraudDetectionService(), server)
    # Listen on port 50051
    port = "50051"
    server.add_insecure_port("[::]:" + port)
    # Start the server
    server.start()
    print("Fraud detection server started. Listening on port 50051.")
    # Keep thread alive
    server.wait_for_termination()

if __name__ == '__main__':
    serve()