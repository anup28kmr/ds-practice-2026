import sys
import os
import grpc
from concurrent import futures

# This set of lines are needed to import the gRPC stubs.
# The path of the stubs is relative to the current file, or absolute inside the container.
# Change these lines only if strictly needed.
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
suggestions_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/suggestions'))
sys.path.insert(0, suggestions_grpc_path)
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc

# In-memory store to cache order data temporarily
# Structure: { order_id: { user_name, item_count, vector_clock } }
order_store = {}

# Index of this service in the vector clock
# TV=0, FD=1, SG=2
SERVICE_INDEX = 2

# Helper function to merge two vector clocks
# Takes MAX of each slot, then increments own slot
def update_clock(local_vc, received_vc):
    merged = [max(local_vc[i], received_vc[i]) for i in range(3)]
    merged[SERVICE_INDEX] += 1
    return merged

# Static list of books to recommend
BOOKS = [
    {"bookId": "101", "title": "Distributed Systems Basics", "author": "A. Author"},
    {"bookId": "102", "title": "Designing Data-Intensive Applications", "author": "Martin Kleppmann"},
    {"bookId": "103", "title": "Clean Code", "author": "Robert C. Martin"},
    {"bookId": "104", "title": "The Pragmatic Programmer", "author": "Andrew Hunt"},
]

# Create a class to define the server functions, derived from
# suggestions_pb2_grpc.SuggestionsServiceServicer
class SuggestionsService(suggestions_grpc.SuggestionsServiceServicer):

    # Create an RPC function to initialize and cache order data
    def InitOrder(self, request, context):
        # Get vector clock from request or initialize fresh
        vc = list(request.vector_clock) if request.vector_clock else [0, 0, 0]
        # Update the vector clock
        vc = update_clock(vc, vc)
        # Cache the order data in memory
        order_store[request.order_id] = {
            "user_name": request.user_name,
            "item_count": request.item_count,
            "vector_clock": vc
        }
        print(f"[SG] InitOrder | order_id={request.order_id} | VC={vc}")
        return suggestions.InitOrderResponse(success=True, vector_clock=vc)

    # Create an RPC function to generate book suggestions (Event f)
    # Can only run after event (e) completes
    def GetSuggestions(self, request, context):
        # Look up the cached order data
        order = order_store.get(request.order_id)
        if not order:
            return suggestions.SuggestionsResponse(
                books=[],
                vector_clock=[0, 0, 0]
            )
        # Merge incoming vector clock with local clock
        vc = update_clock(order["vector_clock"], list(request.vector_clock))
        order["vector_clock"] = vc
        print(f"[SG] Event f - GetSuggestions | order_id={request.order_id} | VC={vc}")
        # Build the response object
        response = suggestions.SuggestionsResponse(vector_clock=vc)
        # Dummy logic: return first 2 books if order has items
        if order["item_count"] > 0:
            for book in BOOKS[:2]:
                b = response.books.add()
                b.bookId = book["bookId"]
                b.title = book["title"]
                b.author = book["author"]
        print(f"[SG] Returning {len(response.books)} suggested books")
        return response

    # Create an RPC function to clear cached order data (Broadcast)
    # Called by orchestrator at the end of the flow
    def ClearOrder(self, request, context):
        vc_final = list(request.vector_clock)
        order = order_store.get(request.order_id)
        if order:
            local_vc = order["vector_clock"]
            # Check local VC <= VCf before clearing
            if all(local_vc[i] <= vc_final[i] for i in range(3)):
                del order_store[request.order_id]
                print(f"[SG] ClearOrder | order_id={request.order_id} | VC check OK")
                return suggestions.ClearOrderResponse(success=True)
            else:
                print(f"[SG] ClearOrder | order_id={request.order_id} | VC check FAILED")
                return suggestions.ClearOrderResponse(success=False)
        return suggestions.ClearOrderResponse(success=True)

def serve():
    # Create a gRPC server
    server = grpc.server(futures.ThreadPoolExecutor())
    # Add SuggestionsService
    suggestions_grpc.add_SuggestionsServiceServicer_to_server(SuggestionsService(), server)
    # Listen on port 50053
    port = "50053"
    server.add_insecure_port("[::]:" + port)
    # Start the server
    server.start()
    print("Suggestions server started. Listening on port 50053.")
    # Keep thread alive
    server.wait_for_termination()

if __name__ == '__main__':
    serve()