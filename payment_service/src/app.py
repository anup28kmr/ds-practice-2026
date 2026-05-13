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

from utils.telemetry import init_telemetry


PORT = os.getenv("PAYMENT_PORT", "50061")


# --- OpenTelemetry (Checkpoint 4) ---
_tracer, _meter = init_telemetry("payment_service")
_payment_total = _meter.create_counter(
    "payment_total",
    description="2PC payment outcomes (label phase=prepare|commit|abort outcome=vote_commit|committed|aborted)",
)
_payment_latency = _meter.create_histogram(
    "payment_latency_seconds",
    description="Wall-clock latency of payment RPCs",
    unit="s",
)


# Per-order bookkeeping. `prepared` stores the amount/user for logging on
# Commit. `committed` and `aborted` are sets used purely for idempotent
# retry handling.
state_lock = threading.Lock()
prepared = {}    # order_id -> {"amount": float, "user_name": str}
committed = set()
aborted = set()


class PaymentService(pay_grpc.PaymentServiceServicer):

    def Prepare(self, request, context):
        """Phase 1 of 2PC on the payment side. Stages the order amount in
        the in-memory `prepared` map and always votes commit (the demo
        does not simulate card-network rejection). Idempotent: replaying
        Prepare after a committed/aborted state returns the recorded
        outcome instead of re-staging."""
        order_id = request.order_id
        _start = time.time()
        with _tracer.start_as_current_span("payment_prepare") as _span:
            _span.set_attribute("order.id", order_id)
            _span.set_attribute("payment.amount", request.amount)
            with state_lock:
                if order_id in prepared:
                    print(
                        f"[PAYMENT] prepare_idempotent order={order_id} "
                        f"(already prepared)"
                    )
                    _payment_total.add(1, {"phase": "prepare", "outcome": "idempotent"})
                    _payment_latency.record(time.time() - _start, {"phase": "prepare"})
                    return pay_pb2.PaymentPrepareResponse(
                        vote_commit=True, message="already prepared"
                    )
                if order_id in committed:
                    _payment_total.add(1, {"phase": "prepare", "outcome": "already_committed"})
                    _payment_latency.record(time.time() - _start, {"phase": "prepare"})
                    return pay_pb2.PaymentPrepareResponse(
                        vote_commit=True, message="already committed"
                    )
                if order_id in aborted:
                    _payment_total.add(1, {"phase": "prepare", "outcome": "already_aborted"})
                    _payment_latency.record(time.time() - _start, {"phase": "prepare"})
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
            _payment_total.add(1, {"phase": "prepare", "outcome": "vote_commit"})
            _payment_latency.record(time.time() - _start, {"phase": "prepare"})
            return pay_pb2.PaymentPrepareResponse(vote_commit=True, message="ok")

    def Commit(self, request, context):
        """Phase 2 commit. Moves the order from `prepared` to `committed`
        and logs the settled amount. Idempotent on retry (second call
        returns `commit_idempotent`). A Commit that arrives without a
        matching Prepare is still accepted so a retrying coordinator can
        make progress; the authoritative decision record lives on the
        coordinator, not here."""
        order_id = request.order_id
        _start = time.time()
        with _tracer.start_as_current_span("payment_commit") as _span:
            _span.set_attribute("order.id", order_id)
            with state_lock:
                if order_id in committed:
                    print(f"[PAYMENT] commit_idempotent order={order_id}")
                    _payment_total.add(1, {"phase": "commit", "outcome": "idempotent"})
                    _payment_latency.record(time.time() - _start, {"phase": "commit"})
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
                _payment_total.add(1, {"phase": "commit", "outcome": "without_prepare"})
            else:
                print(
                    f"[PAYMENT] commit_applied order={order_id} "
                    f"user=\"{info['user_name']}\" amount={info['amount']:.2f}"
                )
                _payment_total.add(1, {"phase": "commit", "outcome": "applied"})
            _payment_latency.record(time.time() - _start, {"phase": "commit"})
            return pay_pb2.PaymentCommitResponse(success=True, message="ok")

    def Abort(self, request, context):
        """Phase 2 abort. Drops any `prepared` reservation for the order
        and records it in `aborted`. Idempotent; also tolerates an Abort
        that arrives before Prepare (logged as `abort_without_prepare`
        and treated as a success)."""
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
