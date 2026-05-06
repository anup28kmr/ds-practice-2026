import sys
import os
import grpc
from concurrent import futures

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc

# ── In-memory order store ──────────────────────────────────────────────────────
# { order_id: { user_name, user_contact, card_number, vector_clock } }
order_store = {}

# Vector clock index: TV=0, FD=1, SG=2
SERVICE_INDEX = 1

def update_clock(local_vc, received_vc):
    """
    Merge two vector clocks element-wise then increment own slot.
    This is the standard Lamport vector clock update rule:
      1. merged[i] = max(local[i], received[i]) for all i
      2. merged[SERVICE_INDEX] += 1  (record that this service processed an event)
    """
    merged = [max(local_vc[i], received_vc[i]) for i in range(3)]
    merged[SERVICE_INDEX] += 1
    return merged

def extract_digits(card: str) -> str:
    """Strip all non-digit characters from a card number string."""
    return "".join(c for c in str(card) if c.isdigit())


class FraudDetectionService(fraud_detection_grpc.FraudDetectionServiceServicer):

    def InitOrder(self, request, context):
        """
        Phase 1 — Called by orchestrator in parallel with TV and SG.
        Cache the order data (user name, contact, card number) for
        subsequent fraud check calls in this order's lifecycle.
        Increments FD slot in the vector clock (slot 1).
        """
        vc = list(request.vector_clock) if request.vector_clock else [0, 0, 0]
        vc = update_clock(vc, vc)

        order_store[request.order_id] = {
            "user_name":    request.user_name,
            "user_contact": request.user_contact,
            "card_number":  request.card_number,
            "vector_clock": vc,
        }

        print(f"[FD] InitOrder order={request.order_id} "
              f"user={request.user_name} vc={vc}")
        return fraud_detection.InitOrderResponse(success=True, vector_clock=vc)

    def CheckUserDataForFraud(self, request, context):
        """
        Event d — Check user name and contact for suspicious patterns.
        Called by orchestrator after event b (VerifyUserData) completes.
        Runs in parallel with event c (VerifyCardFormat).

        Vector clock: receives vc_after_b, merges with local vc,
        increments FD slot, returns updated vc.
        """
        order = order_store.get(request.order_id)
        if not order:
            print(f"[FD] CheckUserDataForFraud order_not_found order={request.order_id}")
            return fraud_detection.FraudCheckResponse(
                is_fraud=True,
                reason="Order not found in fraud service",
                vector_clock=[0, 0, 0]
            )

        # Merge incoming VC (from orchestrator) with our local VC
        # then increment our slot to record this event
        vc = update_clock(order["vector_clock"], list(request.vector_clock))
        order["vector_clock"] = vc

        user_name    = order["user_name"]
        user_contact = order["user_contact"]

        is_fraud = False
        reason   = "User data looks legitimate"

        if not user_name or len(user_name.strip()) < 2:
            is_fraud = True
            reason   = "User name too short or missing"
        elif "fraud" in user_name.lower():
            is_fraud = True
            reason   = "Suspicious user name detected"
        elif not user_contact or "@" not in user_contact:
            is_fraud = True
            reason   = "Invalid or missing contact email"

        print(f"[FD] CheckUserDataForFraud order={request.order_id} "
              f"is_fraud={is_fraud} reason={reason} vc={vc}")
        return fraud_detection.FraudCheckResponse(
            is_fraud=is_fraud, reason=reason, vector_clock=vc
        )

    def CheckCreditCardForFraud(self, request, context):
        """
        Event e — Check the credit card number for fraud patterns.
        Called by orchestrator after BOTH event c (VerifyCardFormat)
        and event d (CheckUserDataForFraud) complete.

        The orchestrator merges vc_after_c and vc_after_d before calling
        this, so the incoming VC already encodes the causal dependency
        on both c and d.

        Vector clock: receives merge(vc_after_c, vc_after_d),
        merges with local vc, increments FD slot, returns updated vc.
        """
        order = order_store.get(request.order_id)
        if not order:
            print(f"[FD] CheckCreditCardForFraud order_not_found order={request.order_id}")
            return fraud_detection.FraudCheckResponse(
                is_fraud=True,
                reason="Order not found in fraud service",
                vector_clock=[0, 0, 0]
            )

        # Merge incoming VC with local VC and increment FD slot
        vc = update_clock(order["vector_clock"], list(request.vector_clock))
        order["vector_clock"] = vc

        card_number = order["card_number"]
        digits      = extract_digits(card_number)
        masked      = f"****{digits[-4:]}" if len(digits) >= 4 else "****"

        is_fraud = False
        reason   = "Credit card looks legitimate"

        # Rule 1: must be exactly 16 digits
        if len(digits) != 16:
            is_fraud = True
            reason   = f"Invalid card length: {len(digits)} digits (expected 16)"
        # Rule 2: suspicious prefix
        elif digits.startswith("9999"):
            is_fraud = True
            reason   = "Card number flagged as high-risk (9999 prefix)"
        # Rule 3: all zeros
        elif digits == "0" * 16:
            is_fraud = True
            reason   = "Card number is all zeros"
        # Rule 4: all same digit (e.g. 1111111111111111)
        elif len(set(digits)) == 1:
            is_fraud = True
            reason   = "Card number is all the same digit"

        print(f"[FD] CheckCreditCardForFraud order={request.order_id} "
              f"card={masked} is_fraud={is_fraud} vc={vc}")
        return fraud_detection.FraudCheckResponse(
            is_fraud=is_fraud, reason=reason, vector_clock=vc
        )

    def ClearOrder(self, request, context):
        """
        Broadcast clear — called by orchestrator at the end of the flow
        with the final vector clock (vc_final = max of all returned VCs).

        Before clearing, we check that our local VC <= vc_final.
        This guarantees we only clear AFTER all causal events are done.
        If the check fails it means we processed an event the orchestrator
        hasn't accounted for yet — this should not happen in normal flow.
        """
        vc_final = list(request.vector_clock)
        order    = order_store.get(request.order_id)

        if not order:
            # Already cleared or never existed — treat as success
            return fraud_detection.ClearOrderResponse(success=True)

        local_vc = order["vector_clock"]

        # Check: local_vc[i] <= vc_final[i] for all i
        if all(local_vc[i] <= vc_final[i] for i in range(3)):
            del order_store[request.order_id]
            print(f"[FD] ClearOrder order={request.order_id} "
                  f"local_vc={local_vc} final_vc={vc_final} cleared=True")
            return fraud_detection.ClearOrderResponse(success=True)
        else:
            print(f"[FD] ClearOrder order={request.order_id} "
                  f"local_vc={local_vc} final_vc={vc_final} "
                  f"cleared=False (local VC ahead of final)")
            return fraud_detection.ClearOrderResponse(success=False)


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


if __name__ == '__main__':
    serve()