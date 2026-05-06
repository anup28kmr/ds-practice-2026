import os
import sys
import threading
from concurrent import futures

FILE = __file__ if "__file__" in globals() else os.getenv("PYTHONFILE", "")
suggestions_grpc_path = os.path.abspath(
    os.path.join(FILE, "../../../utils/pb/suggestions")
)
sys.path.insert(0, suggestions_grpc_path)

import grpc
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc


SERVICE_INDEX = 2  # [transaction_verification, fraud_detection, suggestions]

orders = {}
orders_lock = threading.Lock()

STATIC_BOOKS = [
    {
        "bookId": "101",
        "title": "Distributed Systems Basics",
        "author": "A. Author",
    },
    {
        "bookId": "102",
        "title": "Designing Data-Intensive Applications",
        "author": "Martin Kleppmann",
    },
    {
        "bookId": "103",
        "title": "Clean Code",
        "author": "Robert C. Martin",
    },
    {
        "bookId": "104",
        "title": "The Pragmatic Programmer",
        "author": "Andrew Hunt",
    },
]


def merge_vc(local_vc, incoming_vc):
    return [max(a, b) for a, b in zip(local_vc, incoming_vc)]


def tick(vc, idx):
    vc = list(vc)
    vc[idx] += 1
    return vc


def get_order_state(order_id: str):
    with orders_lock:
        return orders.get(order_id)


class SuggestionsService(suggestions_grpc.SuggestionsServiceServicer):
    def InitOrder(self, request, context):
        order = request.order

        with orders_lock:
            orders[order.order_id] = {
                "order": order,
                "vc": [0, 0, 0],
                "lock": threading.Lock(),
                "books": [],
                # Causal gating state for event g (FinalizeSuggestions).
                # g needs BOTH f (PrecomputeSuggestions, local) AND e (CheckCardFraud, from FD).
                "f_done": False,
                "f_vc": None,
                "f_success": True,
                "f_message": "",
                "e_received": False,
                "e_vc": None,
                "e_success": True,
                "e_message": "",
                "g_triggered": False,
                # Pipeline result: set when g completes or a failure is final.
                "pipeline_done": threading.Event(),
                "pipeline_success": False,
                "pipeline_message": "",
                "pipeline_vc": [0, 0, 0],
                "pipeline_books": [],
            }

        print(f"[SUG] order={order.order_id} event=InitOrder vc={[0, 0, 0]} success=True")

        return suggestions.EventResponse(
            success=True,
            message="Suggestions service initialized order.",
            vc=suggestions.VectorClock(values=[0, 0, 0]),
        )

    def _complete_pipeline(self, state, success, message, vc, books):
        state["pipeline_success"] = success
        state["pipeline_message"] = message
        state["pipeline_vc"] = vc
        state["pipeline_books"] = books
        state["pipeline_done"].set()

    def _try_run_g(self, order_id, state):
        """Check if both prerequisites for event g are met. If so, run FinalizeSuggestions."""
        with state["lock"]:
            if state["g_triggered"]:
                return
            if not (state["f_done"] and state["e_received"]):
                return
            state["g_triggered"] = True

            f_vc = state["f_vc"]
            f_success = state["f_success"]
            f_message = state["f_message"]
            e_vc = state["e_vc"]
            e_success = state["e_success"]
            e_message = state["e_message"]

        # If either prerequisite failed, propagate failure.
        if not f_success:
            print(f"[SUG] order={order_id} event=FinalizeSuggestions skipped (f failed: {f_message})")
            self._complete_pipeline(state, False, f_message, f_vc, [])
            return
        if not e_success:
            print(f"[SUG] order={order_id} event=FinalizeSuggestions skipped (e failed: {e_message})")
            self._complete_pipeline(state, False, e_message, e_vc, [])
            return

        # Both f and e succeeded: merge their VCs and run g.
        merged = merge_vc(f_vc, e_vc)

        with state["lock"]:
            local_vc = state["vc"]
            vc = merge_vc(local_vc, merged)
            vc = tick(vc, SERVICE_INDEX)
            state["vc"] = vc

        prepared_books = state["books"]
        success = len(prepared_books) > 0
        message = (
            "Suggestions finalized."
            if success
            else "No prepared suggestions available."
        )

        print(
            f"[SUG] order={order_id} event=FinalizeSuggestions "
            f"vc={vc} success={success} returned_books={len(prepared_books)}"
        )

        self._complete_pipeline(state, success, message, vc, prepared_books)

    def PrecomputeSuggestions(self, request, context):
        """Event f: called by TV after event a. After processing, checks if e's VC
        has arrived so that event g can run."""
        order_id = request.order_id
        state = get_order_state(order_id)
        if state is None:
            return suggestions.EventResponse(
                success=False,
                message="Order not found in suggestions service.",
                vc=suggestions.VectorClock(values=[0, 0, 0]),
            )

        incoming_vc = list(request.vc.values)

        with state["lock"]:
            local_vc = state["vc"]
            vc = merge_vc(local_vc, incoming_vc)
            vc = tick(vc, SERVICE_INDEX)
            state["vc"] = vc

        item_count = state["order"].item_count

        if item_count > 0:
            state["books"] = STATIC_BOOKS[:2]
            success = True
            message = "Suggestions prepared."
        else:
            state["books"] = []
            success = False
            message = "Cannot prepare suggestions for empty order."

        print(
            f"[SUG] order={order_id} event=PrecomputeSuggestions "
            f"vc={vc} success={success} prepared_books={len(state['books'])}"
        )

        # Record f's result and attempt to trigger g.
        with state["lock"]:
            state["f_done"] = True
            state["f_vc"] = vc
            state["f_success"] = success
            state["f_message"] = message

        self._try_run_g(order_id, state)

        return suggestions.EventResponse(
            success=success,
            message=message,
            vc=suggestions.VectorClock(values=vc),
        )

    def ForwardVC(self, request, context):
        """Receive a forwarded VC from another microservice."""
        order_id = request.order_id
        source_event = request.source_event
        incoming_vc = list(request.vc.values)
        success = request.success
        message = request.message

        state = get_order_state(order_id)
        if state is None:
            return suggestions.EventResponse(
                success=False,
                message="Order not found in suggestions service.",
                vc=suggestions.VectorClock(values=[0, 0, 0]),
            )

        print(
            f"[SUG] order={order_id} event=ForwardVC source={source_event} "
            f"vc={incoming_vc} success={success}"
        )

        if source_event == "e":
            with state["lock"]:
                state["e_received"] = True
                state["e_vc"] = incoming_vc
                state["e_success"] = success
                state["e_message"] = message

            self._try_run_g(order_id, state)
        elif source_event == "a":
            # a failed: no f will ever come (since TV won't call PrecomputeSuggestions).
            # Also no c→e chain, so mark both as failed.
            with state["lock"]:
                if not state["f_done"]:
                    state["f_done"] = True
                    state["f_vc"] = incoming_vc
                    state["f_success"] = False
                    state["f_message"] = message
                if not state["e_received"]:
                    state["e_received"] = True
                    state["e_vc"] = incoming_vc
                    state["e_success"] = False
                    state["e_message"] = message

            self._try_run_g(order_id, state)
        elif source_event == "f":
            # f call itself failed (exception in TV calling SUG)
            with state["lock"]:
                if not state["f_done"]:
                    state["f_done"] = True
                    state["f_vc"] = incoming_vc
                    state["f_success"] = False
                    state["f_message"] = message

            self._try_run_g(order_id, state)
        elif source_event == "d":
            # d call failed (exception in TV calling FD), so e will never complete.
            with state["lock"]:
                if not state["e_received"]:
                    state["e_received"] = True
                    state["e_vc"] = incoming_vc
                    state["e_success"] = False
                    state["e_message"] = message

            self._try_run_g(order_id, state)

        return suggestions.EventResponse(
            success=True,
            message="VC forwarded.",
            vc=suggestions.VectorClock(values=incoming_vc),
        )

    def AwaitPipelineResult(self, request, context):
        """Block until the full event pipeline completes for this order."""
        order_id = request.order_id
        state = get_order_state(order_id)
        if state is None:
            return suggestions.PipelineResultResponse(
                success=False,
                message="Order not found in suggestions service.",
                vc=suggestions.VectorClock(values=[0, 0, 0]),
            )

        # Wait for pipeline completion (event g or a propagated failure).
        state["pipeline_done"].wait(timeout=30.0)

        if not state["pipeline_done"].is_set():
            return suggestions.PipelineResultResponse(
                success=False,
                message="Pipeline timed out.",
                vc=suggestions.VectorClock(values=state["vc"]),
            )

        response = suggestions.PipelineResultResponse(
            success=state["pipeline_success"],
            message=state["pipeline_message"],
            vc=suggestions.VectorClock(values=state["pipeline_vc"]),
        )

        for book in state["pipeline_books"]:
            b = response.books.add()
            b.bookId = book["bookId"]
            b.title = book["title"]
            b.author = book["author"]

        print(
            f"[SUG] order={order_id} event=AwaitPipelineResult "
            f"success={state['pipeline_success']} vc={state['pipeline_vc']}"
        )

        return response

    def FinalizeSuggestions(self, request, context):
        """Event g: kept as an RPC for backward compat, but now triggered internally."""
        order_id = request.order_id
        state = get_order_state(order_id)
        if state is None:
            return suggestions.SuggestionsEventResponse(
                success=False,
                message="Order not found in suggestions service.",
                vc=suggestions.VectorClock(values=[0, 0, 0]),
                books=[],
            )

        incoming_vc = list(request.vc.values)

        with state["lock"]:
            local_vc = state["vc"]
            vc = merge_vc(local_vc, incoming_vc)
            vc = tick(vc, SERVICE_INDEX)
            state["vc"] = vc

        prepared_books = state["books"]
        success = len(prepared_books) > 0
        message = (
            "Suggestions finalized."
            if success
            else "No prepared suggestions available."
        )

        response = suggestions.SuggestionsEventResponse(
            success=success,
            message=message,
            vc=suggestions.VectorClock(values=vc),
        )

        for book in prepared_books:
            b = response.books.add()
            b.bookId = book["bookId"]
            b.title = book["title"]
            b.author = book["author"]

        print(
            f"[SUG] order={order_id} event=FinalizeSuggestions "
            f"vc={vc} success={success} returned_books={len(prepared_books)}"
        )

        return response

    def ClearOrder(self, request, context):
        order_id = request.order_id
        final_vc = list(request.final_vc.values)

        with orders_lock:
            state = orders.get(order_id)

            if state is None:
                return suggestions.EventResponse(
                    success=False,
                    message="Order not found in suggestions service.",
                    vc=suggestions.VectorClock(values=[0, 0, 0]),
                )

            with state["lock"]:
                local_vc = state["vc"]
                can_clear = all(a <= b for a, b in zip(local_vc, final_vc))

            if can_clear:
                del orders[order_id]

        success = can_clear
        message = (
            "Order cleared from suggestions service."
            if success
            else "Cannot clear order: local VC is ahead of final VC."
        )

        print(
            f"[SUG] order={order_id} event=ClearOrder "
            f"local_vc={local_vc} final_vc={final_vc} success={success}"
        )

        return suggestions.EventResponse(
            success=success,
            message=message,
            vc=suggestions.VectorClock(values=final_vc),
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    suggestions_grpc.add_SuggestionsServiceServicer_to_server(
        SuggestionsService(), server
    )

    port = "50053"
    server.add_insecure_port("[::]:" + port)
    server.start()
    print(f"Suggestions server started. Listening on port {port}.")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
