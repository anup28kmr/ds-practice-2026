import sys
import os
import grpc
from concurrent import futures

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
tv_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/transaction_verification'))
sys.path.insert(0, tv_path)
import transaction_verification_pb2 as tv
import transaction_verification_pb2_grpc as tv_grpc

# In-memory store: { order_id: { data + vector_clock } }
order_store = {}

# Service index in vector clock: TV=0, FD=1, SG=2
SERVICE_INDEX = 0

def update_clock(local_vc, received_vc):
    """Merge clocks: take MAX of each slot, then increment own slot."""
    merged = [max(local_vc[i], received_vc[i]) for i in range(3)]
    merged[SERVICE_INDEX] += 1
    return merged

class TransactionVerificationService(tv_grpc.TransactionVerificationServiceServicer):

    def InitOrder(self, request, context):
        """Cache order data and initialize vector clock."""
        vc = list(request.vector_clock) if request.vector_clock else [0, 0, 0]
        vc = update_clock(vc, vc)

        order_store[request.order_id] = {
            "user_name": request.user_name,
            "user_contact": request.user_contact,
            "card_number": request.card_number,
            "expiration_date": request.expiration_date,
            "cvv": request.cvv,
            "item_count": request.item_count,
            "vector_clock": vc
        }

        print(f"[TV] InitOrder {request.order_id} | VC={vc}")
        return tv.InitOrderResponse(success=True, vector_clock=vc)

    def VerifyItems(self, request, context):
        """Event a: Verify items list is not empty."""
        order = order_store.get(request.order_id)
        if not order:
            return tv.VerifyResponse(is_valid=False, reason="Order not found", vector_clock=[0,0,0])

        vc = update_clock(order["vector_clock"], list(request.vector_clock))
        order["vector_clock"] = vc
        print(f"[TV] Event a - VerifyItems {request.order_id} | VC={vc}")

        if order["item_count"] <= 0:
            return tv.VerifyResponse(is_valid=False, reason="Items list is empty", vector_clock=vc)

        return tv.VerifyResponse(is_valid=True, reason="Items OK", vector_clock=vc)

    def VerifyUserData(self, request, context):
        """Event b: Verify user data is filled in."""
        order = order_store.get(request.order_id)
        if not order:
            return tv.VerifyResponse(is_valid=False, reason="Order not found", vector_clock=[0,0,0])

        vc = update_clock(order["vector_clock"], list(request.vector_clock))
        order["vector_clock"] = vc
        print(f"[TV] Event b - VerifyUserData {request.order_id} | VC={vc}")

        if not order["user_name"] or not order["user_contact"]:
            return tv.VerifyResponse(is_valid=False, reason="Missing user data", vector_clock=vc)

        return tv.VerifyResponse(is_valid=True, reason="User data OK", vector_clock=vc)

    def VerifyCardFormat(self, request, context):
        """Event c: Verify card format."""
        order = order_store.get(request.order_id)
        if not order:
            return tv.VerifyResponse(is_valid=False, reason="Order not found", vector_clock=[0,0,0])

        vc = update_clock(order["vector_clock"], list(request.vector_clock))
        order["vector_clock"] = vc
        print(f"[TV] Event c - VerifyCardFormat {request.order_id} | VC={vc}")

        card = order["card_number"].replace(" ", "")
        if not card.isdigit() or len(card) != 16:
            return tv.VerifyResponse(is_valid=False, reason="Invalid card number", vector_clock=vc)
        if not order["cvv"].isdigit() or len(order["cvv"]) != 3:
            return tv.VerifyResponse(is_valid=False, reason="Invalid CVV", vector_clock=vc)

        return tv.VerifyResponse(is_valid=True, reason="Card format OK", vector_clock=vc)

    def ClearOrder(self, request, context):
        """Clear cached order data."""
        vc_final = list(request.vector_clock)
        order = order_store.get(request.order_id)
        if order:
            local_vc = order["vector_clock"]
            # Check local VC <= VCf
            if all(local_vc[i] <= vc_final[i] for i in range(3)):
                del order_store[request.order_id]
                print(f"[TV] ClearOrder {request.order_id} | VC check OK")
                return tv.ClearOrderResponse(success=True)
            else:
                print(f"[TV] ClearOrder {request.order_id} | VC check FAILED")
                return tv.ClearOrderResponse(success=False)
        return tv.ClearOrderResponse(success=True)

def serve():
    server = grpc.server(futures.ThreadPoolExecutor())
    tv_grpc.add_TransactionVerificationServiceServicer_to_server(
        TransactionVerificationService(), server
    )
    server.add_insecure_port("[::]:" + "50052")
    server.start()
    print("Transaction verification server started. Listening on port 50052.")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()