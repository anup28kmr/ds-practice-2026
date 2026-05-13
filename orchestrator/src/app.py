import os
import sys
import threading
import time
import uuid

import grpc
from flask import Flask, g, request
from flask_cors import CORS

from utils.telemetry import init_telemetry

# Import gRPC stubs
FILE = __file__ if "__file__" in globals() else os.getenv("PYTHONFILE", "")

fraud_detection_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/fraud_detection")
)
sys.path.insert(0, fraud_detection_grpc_path)
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc

transaction_verification_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/transaction_verification")
)
sys.path.insert(0, transaction_verification_grpc_path)
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc

suggestions_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/suggestions")
)
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc

order_queue_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/order_queue")
)
sys.path.insert(0, order_queue_grpc_path)
import order_queue_pb2 as order_queue
import order_queue_pb2_grpc as order_queue_grpc


app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# --- OpenTelemetry (Checkpoint 4) ---
# A Flask before/after request pair gives one span per HTTP request plus the
# four mandatory metric kinds. The counter labels the outcome class so the
# dashboard can split 2xx from 4xx/5xx without a second instrument.
_tracer, _meter = init_telemetry("orchestrator")
_checkout_requests = _meter.create_counter(
    "checkout_requests_total",
    description="Number of HTTP requests handled by the orchestrator",
)
_checkout_latency = _meter.create_histogram(
    "checkout_latency_seconds",
    description="End-to-end /checkout latency, including the CP3 2PC fan-out",
    unit="s",
)
_inflight_checkouts = _meter.create_up_down_counter(
    "in_flight_checkouts",
    description="Checkouts currently being processed by the orchestrator",
)


@app.before_request
def _otel_before_request():
    g.otel_start_time = time.time()
    g.otel_span = _tracer.start_span(f"{request.method} {request.path}")
    g.otel_span.set_attribute("http.method", request.method)
    g.otel_span.set_attribute("http.target", request.path)
    _inflight_checkouts.add(1, {"path": request.path})


@app.after_request
def _otel_after_request(response):
    span = getattr(g, "otel_span", None)
    if span is not None:
        elapsed = time.time() - getattr(g, "otel_start_time", time.time())
        status_class = f"{response.status_code // 100}xx"
        attrs = {"path": request.path, "status_class": status_class}
        _checkout_requests.add(1, attrs)
        _checkout_latency.record(elapsed, attrs)
        _inflight_checkouts.add(-1, {"path": request.path})
        span.set_attribute("http.status_code", response.status_code)
        span.end()
    return response


# CP3_EXECUTION_ONLY lets us skip the Checkpoint 2 validation pipeline
# (TV / FD / SUG + vector-clock gating + clear broadcast) and go straight
# from input validation to enqueue. It is a dev-time flag for iterating
# on the Checkpoint 3 2PC path without waiting ~second(s) for the CP2
# pipeline on every checkout. The final demo (§6 Option A in
# Charlie-Lima-Alfa.md) keeps this flag off.
CP3_EXECUTION_ONLY = os.getenv("CP3_EXECUTION_ONLY", "").strip().lower() in (
    "1", "true", "yes", "on"
)
print(
    f"[ORCH] startup cp3_execution_only={CP3_EXECUTION_ONLY} "
    f"(set CP3_EXECUTION_ONLY=true to skip the CP2 validation pipeline)"
)


def mask_fixed(card: str) -> str:
    digits = "".join(c for c in str(card) if c.isdigit())
    masked = "*" * 12 + digits[-4:].rjust(4, "*")
    return " ".join(masked[i:i + 4] for i in range(0, 16, 4))


def merge_vcs(*vectors):
    result = [0, 0, 0]
    for vc in vectors:
        for i in range(3):
            result[i] = max(result[i], vc[i])
    return result


def build_order_kwargs(
    user_name, user_contact, card_number, expiration_date, cvv, item_count, terms_accepted, items
):
    return {
        "user_name": user_name,
        "user_contact": user_contact,
        "card_number": card_number,
        "expiration_date": expiration_date,
        "cvv": cvv,
        "item_count": item_count,
        "terms_accepted": terms_accepted,
        "items": items,
    }


def parse_items(raw_items):
    # Accept both new "title" key and legacy "name" key so old frontends still work.
    parsed = []
    for it in raw_items or []:
        if not isinstance(it, dict):
            continue
        title = (it.get("title") or it.get("name") or "").strip()
        try:
            qty = int(it.get("quantity", 0))
        except (TypeError, ValueError):
            qty = 0
        if not title or qty <= 0:
            continue
        parsed.append({"title": title, "quantity": qty})
    return parsed


def _make_order_data(pb_module, order_id, order_kwargs):
    items_raw = order_kwargs.get("items", [])
    scalar_kwargs = {k: v for k, v in order_kwargs.items() if k != "items"}
    item_protos = [
        pb_module.OrderItem(title=i["title"], quantity=i["quantity"])
        for i in items_raw
    ]
    return pb_module.OrderData(order_id=order_id, items=item_protos, **scalar_kwargs)


# --- Service init calls (unchanged) ---

def init_fraud_service(order_id, order_kwargs):
    with grpc.insecure_channel("fraud_detection:50051") as channel:
        stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
        request = fraud_detection.InitOrderRequest(
            order=_make_order_data(fraud_detection, order_id, order_kwargs)
        )
        return stub.InitOrder(request, timeout=5.0)


def init_transaction_service(order_id, order_kwargs):
    with grpc.insecure_channel("transaction_verification:50052") as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
        request = transaction_verification.InitOrderRequest(
            order=_make_order_data(transaction_verification, order_id, order_kwargs)
        )
        return stub.InitOrder(request, timeout=5.0)


def init_suggestions_service(order_id, order_kwargs):
    with grpc.insecure_channel("suggestions:50053") as channel:
        stub = suggestions_grpc.SuggestionsServiceStub(channel)
        request = suggestions.InitOrderRequest(
            order=_make_order_data(suggestions, order_id, order_kwargs)
        )
        return stub.InitOrder(request, timeout=5.0)


# --- Root event calls: only a and b ---

def tv_validate_items(order_id):
    with grpc.insecure_channel("transaction_verification:50052") as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
        req = transaction_verification.EventRequest(
            order_id=order_id,
            vc=transaction_verification.VectorClock(values=[0, 0, 0]),
        )
        return stub.ValidateItems(req, timeout=15.0)


def tv_validate_user_data(order_id):
    with grpc.insecure_channel("transaction_verification:50052") as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
        req = transaction_verification.EventRequest(
            order_id=order_id,
            vc=transaction_verification.VectorClock(values=[0, 0, 0]),
        )
        return stub.ValidateUserData(req, timeout=15.0)


# --- Pipeline result collection ---

def await_pipeline_result(order_id):
    with grpc.insecure_channel("suggestions:50053") as channel:
        stub = suggestions_grpc.SuggestionsServiceStub(channel)
        req = suggestions.PipelineResultRequest(order_id=order_id)
        return stub.AwaitPipelineResult(req, timeout=30.0)


# --- Enqueue and clear (unchanged) ---

def enqueue_order(order_id, order_kwargs):
    with grpc.insecure_channel("order_queue:50054") as channel:
        stub = order_queue_grpc.OrderQueueServiceStub(channel)
        request = order_queue.EnqueueRequest(
            order=_make_order_data(order_queue, order_id, order_kwargs)
        )
        return stub.Enqueue(request, timeout=5.0)


def clear_fraud_service(order_id, final_vc):
    with grpc.insecure_channel("fraud_detection:50051") as channel:
        stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
        request = fraud_detection.ClearOrderRequest(
            order_id=order_id,
            final_vc=fraud_detection.VectorClock(values=final_vc),
        )
        return stub.ClearOrder(request, timeout=5.0)


def clear_transaction_service(order_id, final_vc):
    with grpc.insecure_channel("transaction_verification:50052") as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
        request = transaction_verification.ClearOrderRequest(
            order_id=order_id,
            final_vc=transaction_verification.VectorClock(values=final_vc),
        )
        return stub.ClearOrder(request, timeout=5.0)


def clear_suggestions_service(order_id, final_vc):
    with grpc.insecure_channel("suggestions:50053") as channel:
        stub = suggestions_grpc.SuggestionsServiceStub(channel)
        request = suggestions.ClearOrderRequest(
            order_id=order_id,
            final_vc=suggestions.VectorClock(values=final_vc),
        )
        return stub.ClearOrder(request, timeout=5.0)


def broadcast_clear(order_id, final_vc):
    try:
        clear_results = [
            ("transaction_verification", clear_transaction_service(order_id, final_vc)),
            ("fraud_detection", clear_fraud_service(order_id, final_vc)),
            ("suggestions", clear_suggestions_service(order_id, final_vc)),
        ]
        failed_services = [
            f"{service}: {response.message}"
            for service, response in clear_results
            if not response.success
        ]

        if failed_services:
            print(
                f"[ORCH] order={order_id} clear_broadcast_warning="
                f"{'; '.join(failed_services)} final_vc={final_vc}"
            )
            return False

        print(f"[ORCH] order={order_id} clear_broadcast_sent final_vc={final_vc}")
        return True
    except Exception as e:
        print(f"[ORCH] order={order_id} clear_broadcast_warning={e}")
        return False


@app.route("/", methods=["GET"])
def index():
    return {"message": "Orchestrator is running."}, 200


@app.route("/checkout", methods=["POST"])
def checkout():
    request_data = request.get_json(silent=True)
    if request_data is None:
        return {
            "error": {
                "code": "BAD_REQUEST",
                "message": "Request body must be valid JSON.",
            }
        }, 400

    user = request_data.get("user", {}) or {}
    items = request_data.get("items", []) or []
    terms_accepted = bool(request_data.get("termsAndConditionsAccepted", False))

    user_name = (user.get("name") or "").strip()
    user_contact = (user.get("contact") or "").strip()

    credit_card = (user.get("creditCard") or {})
    card_number = (credit_card.get("number") or "").strip()
    expiration_date = (credit_card.get("expirationDate") or "").strip()
    cvv = (credit_card.get("cvv") or "").strip()

    if not user_name:
        return {
            "error": {
                "code": "BAD_REQUEST",
                "message": "User name is required.",
            }
        }, 400

    if not user_contact:
        return {
            "error": {
                "code": "BAD_REQUEST",
                "message": "User contact is required.",
            }
        }, 400

    parsed_items = parse_items(items)
    item_count = len(parsed_items)
    order_id = str(uuid.uuid4())

    items_repr = ",".join(f"{i['title']}x{i['quantity']}" for i in parsed_items)
    print(
        f"[ORCH] order={order_id} received_checkout "
        f"user={user_name} card={mask_fixed(card_number)} "
        f"item_count={item_count} items=[{items_repr}]"
    )

    order_kwargs = build_order_kwargs(
        user_name=user_name,
        user_contact=user_contact,
        card_number=card_number,
        expiration_date=expiration_date,
        cvv=cvv,
        item_count=item_count,
        terms_accepted=terms_accepted,
        items=parsed_items,
    )

    # --- Option C fast-path: skip the CP2 pipeline entirely ---
    if CP3_EXECUTION_ONLY:
        print(
            f"[ORCH] order={order_id} cp3_execution_only=true "
            f"skipping CP2 pipeline (init/root-events/await/clear)"
        )
        try:
            enqueue_response = enqueue_order(order_id, order_kwargs)
        except Exception as e:
            print(f"[ORCH] order={order_id} enqueue_error={e}")
            return {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Order could not be queued.",
                }
            }, 500

        if not enqueue_response.success:
            print(
                f"[ORCH] order={order_id} enqueue_failed "
                f"message={enqueue_response.message}"
            )
            return {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": enqueue_response.message,
                }
            }, 500

        print(
            f"[ORCH] order={order_id} enqueue_success "
            f"final_status=APPROVED path=cp3_execution_only"
        )
        return {
            "orderId": order_id,
            "status": "Order Approved",
            "suggestedBooks": [],
        }, 200

    # --- Phase 1: Initialize all backend services ---
    try:
        init_tv = init_transaction_service(order_id, order_kwargs)
        init_fd = init_fraud_service(order_id, order_kwargs)
        init_sug = init_suggestions_service(order_id, order_kwargs)
    except Exception as e:
        print(f"[ORCH] order={order_id} initialization_error={e}")
        return {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Failed to initialize backend services.",
            }
        }, 500

    for name, response in [
        ("InitTransactionVerification", init_tv),
        ("InitFraudDetection", init_fd),
        ("InitSuggestions", init_sug),
    ]:
        if not response.success:
            print(
                f"[ORCH] order={order_id} step={name} success=False message={response.message}"
            )
            return {
                "orderId": order_id,
                "status": "Order Rejected",
                "suggestedBooks": [],
                "reason": response.message,
            }, 200

    print(f"[ORCH] order={order_id} initialization_complete")

    # --- Phase 2: Kick off root events on TV ---
    # The orchestrator only triggers the two root events (a and b).
    # TV handles all downstream chaining: c internally, then forwards to FD and SUG.
    # FD gates event e on both d and c's VC.
    # SUG gates event g on both f and e's VC.
    # The orchestrator does NOT manage any dependency graph.
    root_results = {}
    root_errors = {}

    def run_root(name, rpc_fn):
        try:
            root_results[name] = rpc_fn(order_id)
        except Exception as e:
            root_errors[name] = str(e)

    print(f"[ORCH] order={order_id} starting_root_events")

    threads = [
        threading.Thread(target=run_root, args=("a", tv_validate_items)),
        threading.Thread(target=run_root, args=("b", tv_validate_user_data)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if root_errors:
        print(f"[ORCH] order={order_id} root_event_errors={root_errors}")

    # --- Phase 3: Wait for the full pipeline to complete ---
    # SUG.AwaitPipelineResult blocks until event g finishes (or a failure propagates).
    try:
        pipeline_result = await_pipeline_result(order_id)
    except Exception as e:
        print(f"[ORCH] order={order_id} pipeline_await_error={e}")
        broadcast_clear(order_id, [0, 0, 0])
        return {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Failed to await pipeline result.",
            }
        }, 500

    final_vc = list(pipeline_result.vc.values)

    # Also merge root event VCs into final_vc for completeness.
    for name in ("a", "b"):
        if name in root_results:
            final_vc = merge_vcs(final_vc, list(root_results[name].vc.values))

    if not pipeline_result.success:
        print(
            f"[ORCH] order={order_id} pipeline_rejected "
            f"message={pipeline_result.message} final_vc={final_vc}"
        )
        broadcast_clear(order_id, final_vc)
        return {
            "orderId": order_id,
            "status": "Order Rejected",
            "suggestedBooks": [],
            "reason": pipeline_result.message,
        }, 200

    # --- Phase 4: Enqueue approved order ---
    try:
        enqueue_response = enqueue_order(order_id, order_kwargs)
    except Exception as e:
        print(f"[ORCH] order={order_id} enqueue_error={e}")
        return {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Order was approved but could not be queued.",
            }
        }, 500

    if not enqueue_response.success:
        print(f"[ORCH] order={order_id} enqueue_failed message={enqueue_response.message}")
        return {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": enqueue_response.message,
            }
        }, 500

    print(f"[ORCH] order={order_id} enqueue_success")
    broadcast_clear(order_id, final_vc)
    print(f"[ORCH] order={order_id} final_status=APPROVED final_vc={final_vc}")

    books = []
    for book in pipeline_result.books:
        books.append(
            {
                "bookId": book.bookId,
                "title": book.title,
                "author": book.author,
            }
        )

    return {
        "orderId": order_id,
        "status": "Order Approved",
        "suggestedBooks": books,
    }, 200


if __name__ == "__main__":
    app.run(host="0.0.0.0")
