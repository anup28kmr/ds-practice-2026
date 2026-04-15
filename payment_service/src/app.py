"""Payment service: 2PC participant.

This service never actually charges a card. For the Checkpoint 3 demo it is
a single-instance gRPC server that always votes commit on Prepare, then logs
the subsequent Commit or Abort. Idempotent on retries (the coordinator may
resend Commit/Abort).
"""

import os
import sys
import threading
import time
from concurrent import futures

import grpc

FILE = __file__ if "__file__" in globals() else os.getenv("PYTHONFILE", "")

pay_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/payment_service")
)
sys.path.insert(0, pay_grpc_path)

import payment_pb2 as pay_pb2
import payment_pb2_grpc as pay_grpc


PORT = os.getenv("PAYMENT_PORT", "50061")


# Per-order bookkeeping. `prepared` stores the amount/user for logging on
# Commit. `committed` and `aborted` are sets used purely for idempotent
# retry handling.
state_lock = threading.Lock()
prepared = {}    # order_id -> {"amount": float, "user_name": str}
committed = set()
aborted = set()


class PaymentService(pay_grpc.PaymentServiceServicer):

    def Prepare(self, request, context):
        order_id = request.order_id
        with state_lock:
            if order_id in prepared:
                print(
                    f"[PAYMENT] prepare_idempotent order={order_id} "
                    f"(already prepared)"
                )
                return pay_pb2.PaymentPrepareResponse(
                    vote_commit=True, message="already prepared"
                )
            if order_id in committed:
                return pay_pb2.PaymentPrepareResponse(
                    vote_commit=True, message="already committed"
                )
            if order_id in aborted:
                return pay_pb2.PaymentPrepareResponse(
                    vote_commit=False, message="already aborted"
                )
            prepared[order_id] = {
                "amount": request.amount,
                "user_name": request.user_name,
            }

        print(
            f"[PAYMENT] prepare_vote_commit order={order_id} "
            f"user=\"{request.user_name}\" amount={request.amount:.2f}"
        )
        return pay_pb2.PaymentPrepareResponse(vote_commit=True, message="ok")

    def Commit(self, request, context):
        order_id = request.order_id
        with state_lock:
            if order_id in committed:
                print(f"[PAYMENT] commit_idempotent order={order_id}")
                return pay_pb2.PaymentCommitResponse(
                    success=True, message="already committed"
                )
            info = prepared.pop(order_id, None)
            committed.add(order_id)

        if info is None:
            # Commit without Prepare. Log and accept so the coordinator can
            # make progress; the decision record lives on the coordinator.
            print(
                f"[PAYMENT] commit_without_prepare order={order_id} "
                f"(accepted)"
            )
        else:
            print(
                f"[PAYMENT] commit_applied order={order_id} "
                f"user=\"{info['user_name']}\" amount={info['amount']:.2f}"
            )
        return pay_pb2.PaymentCommitResponse(success=True, message="ok")

    def Abort(self, request, context):
        order_id = request.order_id
        with state_lock:
            if order_id in aborted:
                print(f"[PAYMENT] abort_idempotent order={order_id}")
                return pay_pb2.PaymentAbortResponse(
                    success=True, message="already aborted"
                )
            info = prepared.pop(order_id, None)
            aborted.add(order_id)

        if info is None:
            print(f"[PAYMENT] abort_without_prepare order={order_id}")
        else:
            print(
                f"[PAYMENT] abort_ok order={order_id} "
                f"user=\"{info['user_name']}\" amount={info['amount']:.2f}"
            )
        return pay_pb2.PaymentAbortResponse(success=True, message="ok")


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    pay_grpc.add_PaymentServiceServicer_to_server(PaymentService(), server)
    server.add_insecure_port("[::]:" + PORT)
    server.start()
    print(f"[PAYMENT] listening on port {PORT}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
