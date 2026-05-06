import sys
import os
import grpc
from concurrent import futures

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
payment_service_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/payment_service'))
sys.path.insert(0, payment_service_grpc_path)
import payment_service_pb2 as payment_service
import payment_service_pb2_grpc as payment_service_grpc

# Pending transactions store for 2PC
# { transaction_id: { order_id, amount } }
pending_transactions = {}

# Create a class to define the server functions, derived from
# payment_service_pb2_grpc.PaymentServiceServicer
class PaymentService(payment_service_grpc.PaymentServiceServicer):

    # Create an RPC function for 2PC Phase 1 - Prepare
    # Stage the payment without executing
    def Prepare(self, request, context):
        pending_transactions[request.transaction_id] = {
            "order_id": request.order_id,
            "amount": request.amount
        }
        print(f"[PAYMENT] prepare_vote_commit order={request.order_id} amount={request.amount} tx={request.transaction_id}")
        return payment_service.PrepareResponse(
            ready=True,
            message="Payment ready to commit"
        )

    # Create an RPC function for 2PC Phase 2 - Commit
    # Execute the staged payment
    def Commit(self, request, context):
        tx = pending_transactions.pop(request.transaction_id, None)
        if not tx:
            return payment_service.CommitResponse(success=False, message="Transaction not found")

        # Dummy payment execution
        print(f"[PAYMENT] commit_applied order={tx['order_id']} amount={tx['amount']} tx={request.transaction_id}")
        return payment_service.CommitResponse(
            success=True,
            message="Payment committed successfully"
        )

    # Create an RPC function for 2PC Phase 2 - Abort
    # Discard the staged payment
    def Abort(self, request, context):
        tx = pending_transactions.pop(request.transaction_id, None)
        if tx:
            print(f"[PAYMENT] abort_ok order={tx['order_id']} tx={request.transaction_id}")
        else:
            print(f"[PAYMENT] abort_without_prepare tx={request.transaction_id}")
        return payment_service.AbortResponse(success=True)

def serve():
    # Create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor())
    # Add PaymentService
    payment_service_grpc.add_PaymentServiceServicer_to_server(PaymentService(), server)
    # Listen on port 60020
    port = "60020"
    server.add_insecure_port("[::]:" + port)
    # Start the server
    server.start()
    print("Payment service started. Listening on port 60020.")
    # Keep thread alive
    server.wait_for_termination()

if __name__ == '__main__':
    serve()