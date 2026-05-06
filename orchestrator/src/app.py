import sys
import os
import uuid
import grpc
from concurrent import futures
from flask import Flask, request
from flask_cors import CORS

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")

fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc

transaction_verification_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/transaction_verification'))
sys.path.insert(0, transaction_verification_grpc_path)
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc

suggestions_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/suggestions'))
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc

order_queue_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/order_queue'))
sys.path.insert(0, order_queue_grpc_path)
import order_queue_pb2 as order_queue
import order_queue_pb2_grpc as order_queue_grpc

app = Flask(__name__)
CORS(app, resources={r'/*': {'origins': '*'}})

def merge_clocks(*clocks):
    result = [0, 0, 0]
    for vc in clocks:
        for i in range(3):
            result[i] = max(result[i], vc[i])
    return result

def get_tv_stub():
    channel = grpc.insecure_channel('transaction_verification:50052')
    return transaction_verification_grpc.TransactionVerificationServiceStub(channel)

def get_fd_stub():
    channel = grpc.insecure_channel('fraud_detection:50051')
    return fraud_detection_grpc.FraudDetectionServiceStub(channel)

def get_sg_stub():
    channel = grpc.insecure_channel('suggestions:50053')
    return suggestions_grpc.SuggestionsServiceStub(channel)

def get_queue_stub():
    channel = grpc.insecure_channel('order_queue:50054')
    return order_queue_grpc.OrderQueueServiceStub(channel)

def init_all_services(order_id, user_name, user_contact,
                      card_number, expiration, cvv, item_count, vc):
    results = {}
    errors  = []

    def init_tv():
        try:
            resp = get_tv_stub().InitOrder(
                transaction_verification.InitOrderRequest(
                    order_id=order_id, user_name=user_name,
                    user_contact=user_contact, card_number=card_number,
                    expiration_date=expiration, cvv=cvv,
                    item_count=item_count, vector_clock=vc
                ), timeout=5.0)
            results["tv"] = list(resp.vector_clock)
            if not resp.success:
                errors.append("TV InitOrder failed")
        except Exception as e:
            errors.append(f"TV InitOrder error: {e}")
            results["tv"] = list(vc)

    def init_fd():
        try:
            resp = get_fd_stub().InitOrder(
                fraud_detection.InitOrderRequest(
                    order_id=order_id, user_name=user_name,
                    user_contact=user_contact, card_number=card_number,
                    vector_clock=vc
                ), timeout=5.0)
            results["fd"] = list(resp.vector_clock)
            if not resp.success:
                errors.append("FD InitOrder failed")
        except Exception as e:
            errors.append(f"FD InitOrder error: {e}")
            results["fd"] = list(vc)

    def init_sg():
        try:
            resp = get_sg_stub().InitOrder(
                suggestions.InitOrderRequest(
                    order_id=order_id, user_name=user_name,
                    item_count=item_count, vector_clock=vc
                ), timeout=5.0)
            results["sg"] = list(resp.vector_clock)
            if not resp.success:
                errors.append("SG InitOrder failed")
        except Exception as e:
            errors.append(f"SG InitOrder error: {e}")
            results["sg"] = list(vc)

    with futures.ThreadPoolExecutor(max_workers=3) as executor:
        f1 = executor.submit(init_tv)
        f2 = executor.submit(init_fd)
        f3 = executor.submit(init_sg)
        f1.result(); f2.result(); f3.result()

    tv_vc = results.get("tv", list(vc))
    fd_vc = results.get("fd", list(vc))
    sg_vc = results.get("sg", list(vc))
    merged = merge_clocks(tv_vc, fd_vc, sg_vc)
    return tv_vc, fd_vc, sg_vc, merged, errors

def broadcast_clear(order_id, vc_final):
    def clear_tv():
        try:
            get_tv_stub().ClearOrder(
                transaction_verification.ClearOrderRequest(
                    order_id=order_id, vector_clock=vc_final
                ), timeout=3.0)
        except Exception as e:
            print(f"[ORCH] clear_tv_failed order={order_id} err={e}")

    def clear_fd():
        try:
            get_fd_stub().ClearOrder(
                fraud_detection.ClearOrderRequest(
                    order_id=order_id, vector_clock=vc_final
                ), timeout=3.0)
        except Exception as e:
            print(f"[ORCH] clear_fd_failed order={order_id} err={e}")

    def clear_sg():
        try:
            get_sg_stub().ClearOrder(
                suggestions.ClearOrderRequest(
                    order_id=order_id, vector_clock=vc_final
                ), timeout=3.0)
        except Exception as e:
            print(f"[ORCH] clear_sg_failed order={order_id} err={e}")

    with futures.ThreadPoolExecutor(max_workers=3) as executor:
        executor.submit(clear_tv)
        executor.submit(clear_fd)
        executor.submit(clear_sg)
    print(f"[ORCH] clear_broadcast_sent order={order_id} vc_final={vc_final}")

def enqueue_order(order_id, user_name, user_contact, item_names):
    try:
        resp = get_queue_stub().Enqueue(
            order_queue.EnqueueRequest(
                order=order_queue.Order(
                    order_id=order_id, user_name=user_name,
                    user_contact=user_contact, items=item_names
                )
            ), timeout=5.0)
        print(f"[ORCH] enqueue order={order_id} success={resp.success}")
        return resp.success
    except Exception as e:
        print(f"[ORCH] enqueue_failed order={order_id} err={e}")
        return False

@app.route('/', methods=['GET'])
def index():
    return {"message": "Orchestrator is running."}, 200

@app.route('/checkout', methods=['POST'])
def checkout():
    request_data = request.get_json(force=True, silent=True) or {}

    user_info  = request_data.get("user", {}) or {}
    card_info  = request_data.get("creditCard", {}) or {}
    items      = request_data.get("items", []) or []
    terms      = bool(request_data.get("termsAndConditionsAccepted", False))

    user_name    = (user_info.get("name") or "").strip()
    user_contact = (user_info.get("contact") or "").strip()
    card_number  = (card_info.get("number") or "").strip()
    expiration   = (card_info.get("expirationDate") or "").strip()
    cvv          = (card_info.get("cvv") or "").strip()
    item_count   = len(items)
    item_names   = [
        it.get("name", it.get("title", "")).strip()
        for it in items if isinstance(it, dict)
    ]

    order_id = str(uuid.uuid4())
    print(f"[ORCH] new_order order={order_id} user={user_name} items={item_names}")

    result   = {"success": False, "books": [], "reason": ""}
    vc_final = [0, 0, 0]
    tv_vc = fd_vc = sg_vc = [0, 0, 0]

    if not user_name:
        return {"orderId": order_id, "status": "Order Rejected",
                "suggestedBooks": [], "reason": "User name is required."}, 200
    if not user_contact:
        return {"orderId": order_id, "status": "Order Rejected",
                "suggestedBooks": [], "reason": "User contact is required."}, 200
    if not item_names:
        return {"orderId": order_id, "status": "Order Rejected",
                "suggestedBooks": [], "reason": "Order must contain at least one item."}, 200
    if not terms:
        return {"orderId": order_id, "status": "Order Rejected",
                "suggestedBooks": [], "reason": "Terms and conditions must be accepted."}, 200

    try:
        # Phase 1: Init TV, FD, SG in parallel
        # TV increments slot 0, FD increments slot 1, SG increments slot 2
        print(f"[ORCH] phase=1 init_services order={order_id} vc=[0,0,0]")
        tv_vc, fd_vc, sg_vc, merged_init_vc, init_errors = init_all_services(
            order_id, user_name, user_contact,
            card_number, expiration, cvv, item_count, [0, 0, 0]
        )
        if init_errors:
            raise Exception(f"Init failed: {init_errors}")
        vc_final = merged_init_vc
        print(f"[ORCH] phase=1 done tv_vc={tv_vc} fd_vc={fd_vc} sg_vc={sg_vc} merged={merged_init_vc}")

        # Phase 2: Event a (VerifyItems) and b (VerifyUserData) in parallel
        # Both sent tv_vc — TV increments slot 0 for each
        print(f"[ORCH] phase=2 verify_items+user_data order={order_id} sending_vc={tv_vc}")
        resp_a = resp_b = None

        def run_a():
            nonlocal resp_a
            resp_a = get_tv_stub().VerifyItems(
                transaction_verification.VerifyRequest(
                    order_id=order_id, vector_clock=tv_vc
                ), timeout=5.0)

        def run_b():
            nonlocal resp_b
            resp_b = get_tv_stub().VerifyUserData(
                transaction_verification.VerifyRequest(
                    order_id=order_id, vector_clock=tv_vc
                ), timeout=5.0)

        with futures.ThreadPoolExecutor(max_workers=2) as executor:
            fa = executor.submit(run_a)
            fb = executor.submit(run_b)
            fa.result(); fb.result()

        vc_after_a = list(resp_a.vector_clock)
        vc_after_b = list(resp_b.vector_clock)
        vc_final = merge_clocks(vc_final, vc_after_a, vc_after_b)

        print(f"[ORCH] event=a VerifyItems     is_valid={resp_a.is_valid} vc={vc_after_a}")
        print(f"[ORCH] event=b VerifyUserData  is_valid={resp_b.is_valid} vc={vc_after_b}")

        if not resp_a.is_valid:
            result["reason"] = resp_a.reason
            raise Exception(resp_a.reason)
        if not resp_b.is_valid:
            result["reason"] = resp_b.reason
            raise Exception(resp_b.reason)

        # Phase 3: Event c (VerifyCardFormat) and d (CheckUserDataForFraud) in parallel
        # c depends on a → sent vc_after_a
        # d depends on b → sent vc_after_b
        print(f"[ORCH] phase=3 verify_card+check_user_fraud order={order_id} "
              f"c_vc={vc_after_a} d_vc={vc_after_b}")
        resp_c = resp_d = None

        def run_c():
            nonlocal resp_c
            resp_c = get_tv_stub().VerifyCardFormat(
                transaction_verification.VerifyRequest(
                    order_id=order_id, vector_clock=vc_after_a
                ), timeout=5.0)

        def run_d():
            nonlocal resp_d
            resp_d = get_fd_stub().CheckUserDataForFraud(
                fraud_detection.FraudCheckRequest(
                    order_id=order_id, vector_clock=vc_after_b
                ), timeout=5.0)

        with futures.ThreadPoolExecutor(max_workers=2) as executor:
            fc = executor.submit(run_c)
            fd_fut = executor.submit(run_d)
            fc.result(); fd_fut.result()

        vc_after_c = list(resp_c.vector_clock)
        vc_after_d = list(resp_d.vector_clock)
        vc_final = merge_clocks(vc_final, vc_after_c, vc_after_d)

        print(f"[ORCH] event=c VerifyCardFormat       is_valid={resp_c.is_valid} vc={vc_after_c}")
        print(f"[ORCH] event=d CheckUserDataForFraud  is_fraud={resp_d.is_fraud} vc={vc_after_d}")

        if not resp_c.is_valid:
            result["reason"] = resp_c.reason
            raise Exception(resp_c.reason)
        if resp_d.is_fraud:
            result["reason"] = resp_d.reason
            raise Exception(resp_d.reason)

        # Phase 4: Event e (CheckCreditCardForFraud)
        # Depends on BOTH c and d → merge their VCs
        vc_for_e = merge_clocks(vc_after_c, vc_after_d)
        print(f"[ORCH] phase=4 check_credit_card_fraud order={order_id} vc_for_e={vc_for_e}")

        resp_e = get_fd_stub().CheckCreditCardForFraud(
            fraud_detection.FraudCheckRequest(
                order_id=order_id, vector_clock=vc_for_e
            ), timeout=5.0)

        vc_after_e = list(resp_e.vector_clock)
        vc_final = merge_clocks(vc_final, vc_after_e)

        print(f"[ORCH] event=e CheckCreditCardForFraud is_fraud={resp_e.is_fraud} vc={vc_after_e}")

        if resp_e.is_fraud:
            result["reason"] = resp_e.reason
            raise Exception(resp_e.reason)

        # Phase 5: Event f (GetSuggestions)
        # Depends on e → merge vc_after_e with sg_vc
        vc_for_f = merge_clocks(vc_after_e, sg_vc)
        print(f"[ORCH] phase=5 get_suggestions order={order_id} vc_for_f={vc_for_f}")

        resp_f = get_sg_stub().GetSuggestions(
            suggestions.SuggestionsRequest(
                order_id=order_id, vector_clock=vc_for_f
            ), timeout=5.0)

        vc_after_f = list(resp_f.vector_clock)
        vc_final = merge_clocks(vc_final, vc_after_f)

        print(f"[ORCH] event=f GetSuggestions books={len(resp_f.books)} vc={vc_after_f}")

        result["success"] = True
        result["books"] = [
            {"bookId": b.bookId, "title": b.title, "author": b.author}
            for b in resp_f.books
        ]

        # Phase 6: Enqueue
        print(f"[ORCH] phase=6 enqueue order={order_id}")
        queued = enqueue_order(order_id, user_name, user_contact, item_names)
        if not queued:
            result["success"] = False
            result["reason"] = "Order could not be queued."
            raise Exception("Enqueue failed")

        print(f"[ORCH] order_approved order={order_id} vc_final={vc_final}")

    except Exception as e:
        print(f"[ORCH] order_rejected order={order_id} reason={e}")

    finally:
        print(f"[ORCH] broadcasting_clear order={order_id} vc_final={vc_final}")
        broadcast_clear(order_id, vc_final)

    if result["success"]:
        return {"orderId": order_id, "status": "Order Approved",
                "suggestedBooks": result["books"]}, 200
    else:
        return {"orderId": order_id, "status": "Order Rejected",
                "suggestedBooks": [], "reason": result["reason"]}, 200

if __name__ == '__main__':
    app.run(host='0.0.0.0')