import os
import sys
import threading
from concurrent import futures

FILE = __file__ if "__file__" in globals() else os.getenv("PYTHONFILE", "")
fraud_detection_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/fraud_detection")
)
sys.path.insert(0, fraud_detection_grpc_path)

suggestions_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/suggestions")
)
sys.path.insert(0, suggestions_grpc_path)

import grpc
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc


SERVICE_INDEX = 1  # [transaction_verification, fraud_detection, suggestions]

orders = {}
orders_lock = threading.Lock()


def merge_vc(local_vc, incoming_vc):
    return [max(a, b) for a, b in zip(local_vc, incoming_vc)]


def tick(vc, idx):
    vc = list(vc)
    vc[idx] += 1
    return vc


def extract_card_digits(card: str) -> str:
    return "".join(c for c in str(card) if c.isdigit())


def get_order_state(order_id: str):
    with orders_lock:
        return orders.get(order_id)


def forward_to_sug(order_id, source_event, vc, success, message):
    try:
        with grpc.insecure_channel("suggestions:50053") as channel:
            stub = suggestions_grpc.SuggestionsServiceStub(channel)
            req = suggestions.VCForward(
                order_id=order_id,
                source_event=source_event,
                vc=suggestions.VectorClock(values=vc),
                success=success,
                message=message,
            )
            stub.ForwardVC(req, timeout=10.0)
    except Exception as e:
        print(f"[FD] order={order_id} forward_to_sug_error source={source_event} error={e}")


class FraudDetectionService(fraud_detection_grpc.FraudDetectionServiceServicer):
    def InitOrder(self, request, context):
        order = request.order

        with orders_lock:
            orders[order.order_id] = {
                "order": order,
                "vc": [0, 0, 0],
                "lock": threading.Lock(),
                # Causal gating state for event e (CheckCardFraud).
                # e needs BOTH d (CheckUserFraud, local) AND c (ValidateCardFormat, from TV).
                "d_done": False,
                "d_vc": None,
                "d_success": True,
                "d_message": "",
                "c_received": False,
                "c_vc": None,
                "c_success": True,
                "c_message": "",
                "e_triggered": False,
            }

        print(f"[FD] order={order.order_id} event=InitOrder vc={[0, 0, 0]} success=True")

        return fraud_detection.EventResponse(
            success=True,
            message="Fraud service initialized order.",
            vc=fraud_detection.VectorClock(values=[0, 0, 0]),
        )

    def _try_run_e(self, order_id, state):
        """Check if both prerequisites for event e are met. If so, run CheckCardFraud."""
        with state["lock"]:
            if state["e_triggered"]:
                return
            if not (state["d_done"] and state["c_received"]):
                return
            state["e_triggered"] = True

            d_vc = state["d_vc"]
            d_success = state["d_success"]
            d_message = state["d_message"]
            c_vc = state["c_vc"]
            c_success = state["c_success"]
            c_message = state["c_message"]

        # If either prerequisite failed, propagate failure without running e.
        if not d_success:
            print(f"[FD] order={order_id} event=CheckCardFraud skipped (d failed: {d_message})")
            forward_to_sug(order_id, "e", d_vc, False, d_message)
            return
        if not c_success:
            print(f"[FD] order={order_id} event=CheckCardFraud skipped (c failed: {c_message})")
            forward_to_sug(order_id, "e", c_vc, False, c_message)
            return

        # Both d and c succeeded: merge their VCs and run e.
        merged = merge_vc(d_vc, c_vc)

        with state["lock"]:
            local_vc = state["vc"]
            vc = merge_vc(local_vc, merged)
            vc = tick(vc, SERVICE_INDEX)
            state["vc"] = vc

        # Perform the card-fraud check.
        card_digits = extract_card_digits(state["order"].card_number)

        success = True
        message = "Card fraud check passed."

        if len(card_digits) != 16:
            success = False
            message = "Invalid card number."
        elif card_digits.startswith("0000") or card_digits.endswith("0000"):
            success = False
            message = "Suspicious card number pattern."

        print(
            f"[FD] order={order_id} event=CheckCardFraud "
            f"vc={vc} success={success}"
        )

        # Forward e's result to SUG (SUG needs e's VC to gate event g).
        forward_to_sug(order_id, "e", vc, success, message)

    def CheckUserFraud(self, request, context):
        """Event d: called by TV after event b. After processing, checks if c's VC
        has arrived so that event e can run."""
        order_id = request.order_id
        state = get_order_state(order_id)
        if state is None:
            return fraud_detection.EventResponse(
                success=False,
                message="Order not found in fraud service.",
                vc=fraud_detection.VectorClock(values=[0, 0, 0]),
            )

        incoming_vc = list(request.vc.values)

        with state["lock"]:
            local_vc = state["vc"]
            vc = merge_vc(local_vc, incoming_vc)
            vc = tick(vc, SERVICE_INDEX)
            state["vc"] = vc

        user_name = state["order"].user_name
        success = "fraud" not in user_name.lower()
        message = "User fraud check passed." if success else "Suspicious user name."

        print(
            f"[FD] order={order_id} event=CheckUserFraud "
            f"vc={vc} success={success}"
        )

        # Record d's result and attempt to trigger e.
        with state["lock"]:
            state["d_done"] = True
            state["d_vc"] = vc
            state["d_success"] = success
            state["d_message"] = message

        self._try_run_e(order_id, state)

        return fraud_detection.EventResponse(
            success=success,
            message=message,
            vc=fraud_detection.VectorClock(values=vc),
        )

    def ForwardVC(self, request, context):
        """Receive a forwarded VC from another microservice (TV forwards c's VC here)."""
        order_id = request.order_id
        source_event = request.source_event
        incoming_vc = list(request.vc.values)
        success = request.success
        message = request.message

        state = get_order_state(order_id)
        if state is None:
            return fraud_detection.EventResponse(
                success=False,
                message="Order not found in fraud service.",
                vc=fraud_detection.VectorClock(values=[0, 0, 0]),
            )

        print(
            f"[FD] order={order_id} event=ForwardVC source={source_event} "
            f"vc={incoming_vc} success={success}"
        )

        if source_event == "c":
            with state["lock"]:
                state["c_received"] = True
                state["c_vc"] = incoming_vc
                state["c_success"] = success
                state["c_message"] = message

            self._try_run_e(order_id, state)
        elif source_event == "a":
            # a failed: no c will ever come, so we treat c as failed
            with state["lock"]:
                state["c_received"] = True
                state["c_vc"] = incoming_vc
                state["c_success"] = False
                state["c_message"] = message

            self._try_run_e(order_id, state)
        elif source_event == "d":
            # b failed: TV will not call CheckUserFraud, so d is done+failed
            with state["lock"]:
                state["d_done"] = True
                state["d_vc"] = incoming_vc
                state["d_success"] = success
                state["d_message"] = message

            self._try_run_e(order_id, state)

        return fraud_detection.EventResponse(
            success=True,
            message="VC forwarded.",
            vc=fraud_detection.VectorClock(values=incoming_vc),
        )

    def CheckCardFraud(self, request, context):
        """Event e: kept as an RPC for backward compat, but now triggered internally."""
        order_id = request.order_id
        state = get_order_state(order_id)
        if state is None:
            return fraud_detection.EventResponse(
                success=False,
                message="Order not found in fraud service.",
                vc=fraud_detection.VectorClock(values=[0, 0, 0]),
            )

        incoming_vc = list(request.vc.values)

        with state["lock"]:
            local_vc = state["vc"]
            vc = merge_vc(local_vc, incoming_vc)
            vc = tick(vc, SERVICE_INDEX)
            state["vc"] = vc

        card_digits = extract_card_digits(state["order"].card_number)

        success = True
        message = "Card fraud check passed."

        if len(card_digits) != 16:
            success = False
            message = "Invalid card number."
        elif card_digits.startswith("0000") or card_digits.endswith("0000"):
            success = False
            message = "Suspicious card number pattern."

        print(
            f"[FD] order={order_id} event=CheckCardFraud "
            f"vc={vc} success={success}"
        )

        return fraud_detection.EventResponse(
            success=success,
            message=message,
            vc=fraud_detection.VectorClock(values=vc),
        )

    def ClearOrder(self, request, context):
        order_id = request.order_id
        final_vc = list(request.final_vc.values)

        with orders_lock:
            state = orders.get(order_id)

            if state is None:
                return fraud_detection.EventResponse(
                    success=False,
                    message="Order not found in fraud service.",
                    vc=fraud_detection.VectorClock(values=[0, 0, 0]),
                )

            with state["lock"]:
                local_vc = state["vc"]
                can_clear = all(a <= b for a, b in zip(local_vc, final_vc))

            if can_clear:
                del orders[order_id]

        success = can_clear
        message = (
            "Order cleared from fraud service."
            if success
            else "Cannot clear order: local VC is ahead of final VC."
        )

        print(
            f"[FD] order={order_id} event=ClearOrder "
            f"local_vc={local_vc} final_vc={final_vc} success={success}"
        )

        return fraud_detection.EventResponse(
            success=success,
            message=message,
            vc=fraud_detection.VectorClock(values=final_vc),
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    fraud_detection_grpc.add_FraudDetectionServiceServicer_to_server(
        FraudDetectionService(), server
    )

    port = "50051"
    server.add_insecure_port("[::]:" + port)
    server.start()
    print(f"Fraud detection server started. Listening on port {port}.")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
