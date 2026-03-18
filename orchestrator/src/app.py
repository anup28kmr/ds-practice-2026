import sys
import os
import uuid
import grpc
from concurrent import futures
from flask import Flask, request
from flask_cors import CORS

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")

# Import fraud detection stubs
fraud_detection_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/fraud_detection'))
sys.path.insert(0, fraud_detection_grpc_path)
import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc

# Import transaction verification stubs
transaction_verification_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/transaction_verification'))
sys.path.insert(0, transaction_verification_grpc_path)
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc

# Import suggestions stubs
suggestions_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/suggestions'))
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc

# Helper function to get transaction verification stub
def get_tv_stub():
    channel = grpc.insecure_channel('transaction_verification:50052')
    return transaction_verification_grpc.TransactionVerificationServiceStub(channel)

# Helper function to get fraud detection stub
def get_fd_stub():
    channel = grpc.insecure_channel('fraud_detection:50051')
    return fraud_detection_grpc.FraudDetectionServiceStub(channel)

# Helper function to get suggestions stub
def get_sg_stub():
    channel = grpc.insecure_channel('suggestions:50053')
    return suggestions_grpc.SuggestionsServiceStub(channel)

# Helper function to merge two vector clocks by taking MAX of each slot
def merge_clocks(vc1, vc2):
    return [max(vc1[i], vc2[i]) for i in range(3)]

def greet(name='you'):
    # Establish a connection with the fraud-detection gRPC service.
    with grpc.insecure_channel('fraud_detection:50051') as channel:
        # Create a stub object.
        stub = fraud_detection_grpc.HelloServiceStub(channel)
        # Call the service through the stub object.
        response = stub.SayHello(fraud_detection.HelloRequest(name=name))
    return response.greeting

# Initialize all 3 services in parallel with order data
# Services cache the data and initialize their vector clocks
def init_all_services(order_id, user_name, user_contact, card_number, expiration, cvv, item_count, initial_vc):
    def init_tv():
        return get_tv_stub().InitOrder(transaction_verification.InitOrderRequest(
            order_id=order_id,
            user_name=user_name,
            user_contact=user_contact,
            card_number=card_number,
            expiration_date=expiration,
            cvv=cvv,
            item_count=item_count,
            vector_clock=initial_vc
        ))

    def init_fd():
        return get_fd_stub().InitOrder(fraud_detection.InitOrderRequest(
            order_id=order_id,
            user_name=user_name,
            user_contact=user_contact,
            card_number=card_number,
            vector_clock=initial_vc
        ))

    def init_sg():
        return get_sg_stub().InitOrder(suggestions.InitOrderRequest(
            order_id=order_id,
            user_name=user_name,
            item_count=item_count,
            vector_clock=initial_vc
        ))

    # Run all 3 initializations in parallel
    with futures.ThreadPoolExecutor(max_workers=3) as executor:
        tv_future = executor.submit(init_tv)
        fd_future = executor.submit(init_fd)
        sg_future = executor.submit(init_sg)
        tv_vc = list(tv_future.result().vector_clock)
        fd_vc = list(fd_future.result().vector_clock)
        sg_vc = list(sg_future.result().vector_clock)

    return tv_vc, fd_vc, sg_vc

# Broadcast clear order to all 3 services in parallel
# Sends final vector clock so services can verify and clear cached data
def broadcast_clear(order_id, vc_final):
    def clear_tv():
        get_tv_stub().ClearOrder(transaction_verification.ClearOrderRequest(
            order_id=order_id,
            vector_clock=vc_final
        ))

    def clear_fd():
        get_fd_stub().ClearOrder(fraud_detection.ClearOrderRequest(
            order_id=order_id,
            vector_clock=vc_final
        ))

    def clear_sg():
        get_sg_stub().ClearOrder(suggestions.ClearOrderRequest(
            order_id=order_id,
            vector_clock=vc_final
        ))

    # Broadcast clear to all 3 services in parallel
    with futures.ThreadPoolExecutor(max_workers=3) as executor:
        executor.submit(clear_tv)
        executor.submit(clear_fd)
        executor.submit(clear_sg)

# Import Flask.
# Flask is a web framework for Python.
# It allows you to build a web application quickly.
# For more information, see https://flask.palletsprojects.com/en/latest/

# Create a simple Flask app.
app = Flask(__name__)
# Enable CORS for the app.
CORS(app, resources={r'/*': {'origins': '*'}})

# Define a GET endpoint.
@app.route('/', methods=['GET'])
def index():
    """
    Responds with 'Hello, [name]' when a GET request is made to '/' endpoint.
    """
    # Test the fraud-detection gRPC service.
    response = greet(name='orchestrator')
    # Return the response.
    return response

@app.route('/checkout', methods=['POST'])
def checkout():
    """
    Responds with a JSON object containing the order ID, status, and suggested books.
    """
    # Get request object data to json
    request_data = request.get_json(force=True, silent=True) or {}
    # Print request object data
    print("Request Data:", request_data.get('items'))

    # Extract order data from request
    user_info = request_data.get("user", {})
    card_info = request_data.get("creditCard", {})
    items = request_data.get("items", [])

    user_name = user_info.get("name", "")
    user_contact = user_info.get("contact", "")
    card_number = card_info.get("number", "")
    expiration = card_info.get("expirationDate", "")
    cvv = card_info.get("cvv", "")
    item_count = len(items)

    # Generate a unique OrderID for this order
    order_id = str(uuid.uuid4())
    print(f"[ORCH] New order | order_id={order_id}")

    # Initialize vector clocks and result
    initial_vc = [0, 0, 0]
    tv_vc = [0, 0, 0]
    fd_vc = [0, 0, 0]
    sg_vc = [0, 0, 0]
    vc_final = [0, 0, 0]
    result = {"success": False, "books": [], "reason": ""}

    try:
        # Phase 1: Initialize all 3 services in parallel
        print(f"[ORCH] Phase 1 - Initializing all services | VC={initial_vc}")
        tv_vc, fd_vc, sg_vc = init_all_services(
            order_id, user_name, user_contact,
            card_number, expiration, cvv,
            item_count, initial_vc
        )
        print(f"[ORCH] Phase 1 done | TV_VC={tv_vc} FD_VC={fd_vc} SG_VC={sg_vc}")

        # Phase 2: Run event (a) and event (b) in parallel
        # (a) TV verifies items list is not empty
        # (b) TV verifies user data is filled in
        print(f"[ORCH] Phase 2 - Events a and b in parallel")

        with futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_a = executor.submit(
                lambda: get_tv_stub().VerifyItems(
                    transaction_verification.VerifyRequest(order_id=order_id, vector_clock=tv_vc)
                )
            )
            future_b = executor.submit(
                lambda: get_tv_stub().VerifyUserData(