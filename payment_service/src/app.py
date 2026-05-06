import sys
import os
import grpc
import threading
from concurrent import futures

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
payment_service_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/payment_service'))
sys.path.insert(0, payment_service_grpc_path)
import payment_service_pb2 as payment_service
import payment_service_pb2_grpc as payment_service_grpc

# Thread lock — protects all shared state from concurrent RPC handlers
state_lock = threading.Lock()

# Pending transactions: { transaction_id: { order_id, amount } }
pending_transactions = {}

# Idempotency tracking sets
committed_orders = set()   # order_ids fully committed
aborted_orders   = set()   # order_ids aborted


class PaymentService(payment_service_grpc.PaymentServiceServicer):

    def Prepare(self, request, context):
        """
        Phase 1 of 2PC. Stage the payment amount and vote commit.
        Always votes commit (dummy service — no real card processing).
        Idempotent: replaying Prepare for an already-staged transaction
        returns vote_commit=True without re-staging.
        """
        order_id       = request.order_id
        transaction_id = request.transaction_id
        amount         = request.amount

        with state_lock:
            # Already staged — idempotent re-prepare
            if transaction_id in pending_transactions:
                print(f"[PAYMENT] prepare_idempotent order={order_id} tx={transaction_id}")
                return payment_service.PrepareResponse(
                    ready=True, message="already prepared"
                )
            # Already committed — coordinator may be replaying after crash
            if order_id in committed_orders:
                print(f"[PAYMENT] prepare_already_committed order={order_id}")
                return payment_service.PrepareResponse(
                    ready=True, message="already committed"
                )
            # Already aborted
            if order_id in aborted_orders:
                print(f"[PAYMENT] prepare_already_aborted order={order_id}")
                return payment_service.PrepareResponse(
                    ready=False, message="already aborted"
                )

            pending_transactions[transaction_id] = {
                "order_id": order_id,
                "amount":   amount,
            }

        print(f"[PAYMENT] prepare_vote_commit order={order_id} "
              f"amount={amount:.2f} tx={transaction_id}")
        return payment_service.PrepareResponse(
            ready=True, message="Payment ready to commit"
        )

    def Commit(self, request, context):
        """
        Phase 2 commit. Execute the dummy payment and record as committed.
        Idempotent: a second Commit for the same order returns success
        immediately without re-processing. A Commit that arrives without
        a matching Prepare is still accepted — the decision record lives
        on the coordinator, not here.
        """
        order_id       = request.order_id
        transaction_id = request.transaction_id

        with state_lock:
            # Already committed — safe no-op for coordinator retries
            if order_id in committed_orders:
                print(f"[PAYMENT] commit_idempotent order={order_id} tx={transaction_id}")
                return payment_service.CommitResponse(
                    success=True, message="already committed"
                )

            tx = pending_transactions.pop(transaction_id, None)
            committed_orders.add(order_id)

        if tx is None:
            # Commit without a matching Prepare (coordinator retry after failover)
            # Accept it — coordinator is authoritative on the decision
            print(f"[PAYMENT] commit_without_prepare order={order_id} tx={transaction_id} (accepted)")
        else:
            print(f"[PAYMENT] commit_applied order={order_id} "
                  f"amount={tx['amount']:.2f} tx={transaction_id}")

        return payment_service.CommitResponse(
            success=True, message="Payment committed successfully"
        )

    def Abort(self, request, context):
        """
        Phase 2 abort. Discard the staged payment. Fully idempotent —
        aborting an unknown or already-aborted order is a silent no-op.
        """
        order_id       = request.order_id
        transaction_id = request.transaction_id

        with state_lock:
            # Already aborted — idempotent
            if order_id in aborted_orders:
                print(f"[PAYMENT] abort_idempotent order={order_id} tx={transaction_id}")
                return payment_service.AbortResponse(success=True)

            tx = pending_transactions.pop(transaction_id, None)
            aborted_orders.add(order_id)

        if tx is None:
            print(f"[PAYMENT] abort_without_prepare order={order_id} tx={transaction_id}")
        else:
            print(f"[PAYMENT] abort_ok order={order_id} "
                  f"amount={tx['amount']:.2f} tx={transaction_id}")

        return payment_service.AbortResponse(success=True)


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    payment_service_grpc.add_PaymentServiceServicer_to_server(
        PaymentService(), server
    )
    port = "60020"
    server.add_insecure_port("[::]:" + port)
    server.start()
    print(f"Payment service started. Listening on port {port}.")
    server.wait_for_termination()


if __name__ == '__main__':
    serve()