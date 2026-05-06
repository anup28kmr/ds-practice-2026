import os
import sys
import threading
from concurrent import futures

FILE = __file__ if "__file__" in globals() else os.getenv("PYTHONFILE", "")
transaction_verification_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/transaction_verification")
)
sys.path.insert(0, transaction_verification_grpc_path)

fraud_detection_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/fraud_detection")
)
sys.path.insert(0, fraud_detection_grpc_path)

suggestions_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/suggestions")
)
sys.path.insert(0, suggestions_grpc_path)

import grpc
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc


SERVICE_INDEX = 0  # [transaction_verification, fraud_detection, suggestions]

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


def mask_fixed(card: str) -> str:
    digits = extract_card_digits(card)
    masked = "*" * 12 + digits[-4:].rjust(4, "*")
    return " ".join(masked[i:i + 4] for i in range(0, 16, 4))


def get_order_state(order_id: str):
    with orders_lock:
        return orders.get(order_id)


def forward_to_fd(order_id, source_event, vc, success, message):
    try:
        with grpc.insecure_channel("fraud_detection:50051") as channel:
            stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
            req = fraud_detection.VCForward(
                order_id=order_id,
                source_event=source_event,
                vc=fraud_detection.VectorClock(values=vc),
                success=success,
                message=message,
            )
            stub.ForwardVC(req, timeout=10.0)
    except Exception as e:
        print(f"[TV] order={order_id} forward_to_fd_error source={source_event} error={e}")


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
        print(f"[TV] order={order_id} forward_to_sug_error source={source_event} error={e}")


def call_fd_check_user_fraud(order_id, vc):
    try:
        with grpc.insecure_channel("fraud_detection:50051") as channel:
            stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
            req = fraud_detection.EventRequest(
                order_id=order_id,
                vc=fraud_detection.VectorClock(values=vc),
            )
            return stub.CheckUserFraud(req, timeout=10.0)
    except Exception as e:
        print(f"[TV] order={order_id} call_fd_check_user_fraud_error={e}")
        return None


def call_sug_precompute(order_id, vc):
    try:
        with grpc.insecure_channel("suggestions:50053") as channel:
            stub = suggestions_grpc.SuggestionsServiceStub(channel)
            req = suggestions.EventRequest(
                order_id=order_id,
                vc=suggestions.VectorClock(values=vc),
            )
            return stub.PrecomputeSuggestions(req, timeout=10.0)
    except Exception as e:
        print(f"[TV] order={order_id} call_sug_precompute_error={e}")
        return None


class TransactionVerificationService(
    transaction_verification_grpc.TransactionVerificationServiceServicer
):
    def InitOrder(self, request, context):
        order = request.order

        with orders_lock:
            orders[order.order_id] = {
                "order": order,
                "vc": [0, 0, 0],
                "lock": threading.Lock(),
            }

        print(f"[TV] order={order.order_id} event=InitOrder vc={[0, 0, 0]} success=True")

        return transaction_verification.EventResponse(
            success=True,
            message="Transaction verification service initialized order.",
            vc=transaction_verification.VectorClock(values=[0, 0, 0]),
        )

    def _process_event(self, order_id, incoming_vc, event_name, check_fn):
        state = get_order_state(order_id)
        if state is None:
            return None, False, "Order not found in transaction verification service.", [0, 0, 0]

        with state["lock"]:
            local_vc = state["vc"]
            vc = merge_vc(local_vc, incoming_vc)
            vc = tick(vc, SERVICE_INDEX)
            state["vc"] = vc

        success, message = check_fn(state)

        print(
            f"[TV] order={order_id} event={event_name} "
            f"vc={vc} success={success}"
        )

        return vc, success, message, vc

    def ValidateItems(self, request, context):
        """Event a: root event. After processing, chains to c (ValidateCardFormat)
        and forwards VC to SUG (PrecomputeSuggestions) and eventually to FD."""
        order_id = request.order_id
        incoming_vc = list(request.vc.values)

        def check(state):
            item_count = state["order"].item_count
            success = item_count > 0
            message = "Items check passed." if success else "No items in order."
            return success, message

        vc, success, message, _ = self._process_event(order_id, incoming_vc, "ValidateItems", check)
        if vc is None:
            return transaction_verification.EventResponse(
                success=False, message=message,
                vc=transaction_verification.VectorClock(values=[0, 0, 0]),
            )

        # After event a completes, trigger downstream events in background threads.
        # TV itself handles event c (ValidateCardFormat) and forwards to FD and SUG.
        def chain_after_a():
            if not success:
                # a failed: propagate failure to FD and SUG
                forward_to_fd(order_id, "a", vc, False, message)
                forward_to_sug(order_id, "a", vc, False, message)
                return

            # Call SUG.PrecomputeSuggestions (event f) — TV forwards a's VC to SUG
            sug_resp = call_sug_precompute(order_id, vc)
            if sug_resp is None:
                forward_to_sug(order_id, "f", vc, False, "PrecomputeSuggestions call failed.")

            # Process event c (ValidateCardFormat) internally on TV
            c_vc, c_success, c_message, _ = self._process_event(
                order_id, vc, "ValidateCardFormat", self._card_format_check
            )

            if c_vc is None:
                forward_to_fd(order_id, "c", vc, False, c_message)
                return

            # Forward c's result to FD (FD needs c's VC to gate event e)
            forward_to_fd(order_id, "c", c_vc, c_success, c_message)

        threading.Thread(target=chain_after_a, daemon=True).start()

        return transaction_verification.EventResponse(
            success=success, message=message,
            vc=transaction_verification.VectorClock(values=vc),
        )

    def _card_format_check(self, state):
        order = state["order"]
        card_digits = extract_card_digits(order.card_number)

        success = True
        message = "Card format check passed."

        if not order.card_number or not order.expiration_date or not order.cvv:
            success = False
            message = "Missing credit card information."
        elif len(card_digits) != 16:
            success = False
            message = "Invalid card number."
        return success, message

    def ValidateUserData(self, request, context):
        """Event b: root event. After processing, forwards VC to FD (CheckUserFraud)."""
        order_id = request.order_id
        incoming_vc = list(request.vc.values)

        def check(state):
            order = state["order"]
            success = True
            message = "User data check passed."

            if not order.user_name:
                success = False
                message = "Missing user name."
            elif not order.user_contact:
                success = False
                message = "Missing user contact."
            elif not order.terms_accepted:
                success = False
                message = "Terms and conditions not accepted."
            return success, message

        vc, success, message, _ = self._process_event(order_id, incoming_vc, "ValidateUserData", check)
        if vc is None:
            return transaction_verification.EventResponse(
                success=False, message=message,
                vc=transaction_verification.VectorClock(values=[0, 0, 0]),
            )

        # After event b completes, forward to FD in a background thread.
        def chain_after_b():
            if not success:
                # b failed: propagate failure downstream (d will never run).
                forward_to_fd(order_id, "d", vc, False, message)
                return

            fd_resp = call_fd_check_user_fraud(order_id, vc)
            if fd_resp is None:
                # If the FD call itself failed, propagate failure to SUG
                forward_to_sug(order_id, "d", vc, False, "CheckUserFraud call failed.")

        threading.Thread(target=chain_after_b, daemon=True).start()

        return transaction_verification.EventResponse(
            success=success, message=message,
            vc=transaction_verification.VectorClock(values=vc),
        )

    def ValidateCardFormat(self, request, context):
        """Event c: kept as an RPC for backward compat, but now called internally by TV."""
        order_id = request.order_id
        incoming_vc = list(request.vc.values)

        vc, success, message, _ = self._process_event(
            order_id, incoming_vc, "ValidateCardFormat", self._card_format_check
        )
        if vc is None:
            return transaction_verification.EventResponse(
                success=False, message=message,
                vc=transaction_verification.VectorClock(values=[0, 0, 0]),
            )

        return transaction_verification.EventResponse(
            success=success, message=message,
            vc=transaction_verification.VectorClock(values=vc),
        )

    def ClearOrder(self, request, context):
        order_id = request.order_id
        final_vc = list(request.final_vc.values)

        with orders_lock:
            state = orders.get(order_id)

            if state is None:
                return transaction_verification.EventResponse(
                    success=False,
                    message="Order not found in transaction verification service.",
                    vc=transaction_verification.VectorClock(values=[0, 0, 0]),
                )

            with state["lock"]:
                local_vc = state["vc"]
                can_clear = all(a <= b for a, b in zip(local_vc, final_vc))

            if can_clear:
                del orders[order_id]

        success = can_clear
        message = (
            "Order cleared from transaction verification service."
            if success
            else "Cannot clear order: local VC is ahead of final VC."
        )

        print(
            f"[TV] order={order_id} event=ClearOrder "
            f"local_vc={local_vc} final_vc={final_vc} success={success}"
        )

        return transaction_verification.EventResponse(
            success=success,
            message=message,
            vc=transaction_verification.VectorClock(values=final_vc),
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    transaction_verification_grpc.add_TransactionVerificationServiceServicer_to_server(
        TransactionVerificationService(), server
    )

    port = "50052"
    server.add_insecure_port("[::]:" + port)
    server.start()
    print(f"Transaction verification server started. Listening on port {port}.")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
