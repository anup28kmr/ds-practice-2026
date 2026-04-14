# Vector Clock Redesign: From Centralized to Service-Driven Ordering

## TA feedback that prompted this change

> Grade: 7.00 / 11.5
>
> Feedback: "+ 0.5 bonus point. Vector clocks are not meaningfully implemented.
> Event ordering should be handled by the microservices not centrally through the orchestrator."

## What was wrong (before this change)

The original implementation had a star topology where the orchestrator was the sole coordinator of all event ordering. The three backend services (transaction_verification, fraud_detection, suggestions) were passive RPC endpoints that never communicated with each other.

### Centralized ordering in the orchestrator

The orchestrator encoded the full partial order as a list of Python threads with explicit `threading.Event` waits:

```python
# OLD orchestrator code (removed)
workers = [
    threading.Thread(target=run_event, args=("a", [], [], tv_validate_items)),
    threading.Thread(target=run_event, args=("b", [], [], tv_validate_user_data)),
    threading.Thread(target=run_event, args=("c", ["a"], ["a"], tv_validate_card_format)),
    threading.Thread(target=run_event, args=("d", ["b"], ["b"], fd_check_user_fraud)),
    threading.Thread(target=run_event, args=("e", ["c", "d"], ["c", "d"], fd_check_card_fraud)),
    threading.Thread(target=run_event, args=("f", ["a"], ["a"], sug_precompute)),
    threading.Thread(target=run_finalize),
]
```

The dependency graph (`"c" after ["a"]`, `"e" after ["c", "d"]`, etc.) lived entirely in the orchestrator. The orchestrator also computed every downstream event's input VC via `merged_from(*input_steps)`. If you deleted the VC plumbing entirely, the system would still produce events in the exact same order because `threading.Event` waits enforced it.

### VCs were inert metadata

No service ever inspected an incoming VC to make a decision. Each handler blindly merged, ticked, logged, and returned. The VC was documentation, not a mechanism.

### Race condition

Events `a` (ValidateItems) and `b` (ValidateUserData) both ran on transaction_verification in parallel, but the read-modify-write of the per-order local clock (`state["vc"]`) had no lock. Under concurrent gRPC threads, both events could read `[0,0,0]`, tick to `[1,0,0]`, and write back the same value, violating the vector-clock invariant that two events on the same process must be totally ordered.

## What changed (this commit)

### Architecture overview

The orchestrator's role shrank from "workflow engine" to "pipeline launcher":

```
BEFORE:  Orchestrator dispatches all 7 events, enforces ordering via threading

AFTER:   Orchestrator only does:
         1. Init all 3 services
         2. Kick off root events a, b on TV
         3. Block on SUG.AwaitPipelineResult()
         4. Enqueue approved orders
         5. Broadcast ClearOrder
```

The microservices now drive the event flow themselves by making inter-service gRPC calls and using vector clocks for causal gating.

### Inter-service call flow (successful order)

```
Orchestrator
  |
  |--- TV.ValidateItems(vc=[0,0,0])         --> event a
  |--- TV.ValidateUserData(vc=[0,0,0])      --> event b
  |
  |    TV (internally, after a):
  |      |--- TV.ValidateCardFormat          --> event c (called internally, same process)
  |      |--- SUG.PrecomputeSuggestions(vc)  --> event f (TV calls SUG directly)
  |      |--- FD.ForwardVC(source="c", vc)  --> forwards c's VC to FD
  |
  |    TV (internally, after b):
  |      |--- FD.CheckUserFraud(vc)         --> event d (TV calls FD directly)
  |
  |    FD (causal gating):
  |      Waits for BOTH d (local) AND c (forwarded from TV)
  |      |--- event e: CheckCardFraud       --> runs when both prerequisites met
  |      |--- SUG.ForwardVC(source="e", vc) --> forwards e's VC to SUG
  |
  |    SUG (causal gating):
  |      Waits for BOTH f (local) AND e (forwarded from FD)
  |      |--- event g: FinalizeSuggestions  --> runs when both prerequisites met
  |      |--- signals pipeline_done
  |
  |<-- SUG.AwaitPipelineResult()            --> orchestrator collects result
```

### Where VCs are now meaningful

In the old code, VCs were never used for decisions. Now they are:

1. **FD gates event e on causal readiness.** FD stores `d_done`/`d_vc` and `c_received`/`c_vc`. The method `_try_run_e()` only fires when both are present. It merges the two VCs before processing, which is a real vector-clock merge across two causal predecessors from different services.

2. **SUG gates event g on causal readiness.** SUG stores `f_done`/`f_vc` and `e_received`/`e_vc`. The method `_try_run_g()` only fires when both are present. Same merge pattern.

3. **Failure propagation uses VCs.** When an event fails (e.g., `b` rejects for terms not accepted), TV forwards the failure VC to FD via `ForwardVC(source="d", success=False)`. FD records it as a failed prerequisite and propagates to SUG. The VC still tracks what happened up to the failure point.

### Per-order locking (race condition fix)

Every service now stores a `threading.Lock()` per order in the state dict. All VC read-modify-write operations are wrapped:

```python
with state["lock"]:
    local_vc = state["vc"]
    vc = merge_vc(local_vc, incoming_vc)
    vc = tick(vc, SERVICE_INDEX)
    state["vc"] = vc
```

The `tick()` function was also changed to return a new list instead of mutating in place:

```python
def tick(vc, idx):
    vc = list(vc)   # copy first
    vc[idx] += 1
    return vc
```

## Files changed

### Proto definitions

| File | Change |
|---|---|
| `utils/pb/fraud_detection/fraud_detection.proto` | Added `ForwardVC` RPC and `VCForward` message |
| `utils/pb/suggestions/suggestions.proto` | Added `ForwardVC` RPC, `VCForward` message, `AwaitPipelineResult` RPC, `PipelineResultRequest`/`PipelineResultResponse` messages |
| `utils/pb/fraud_detection/fraud_detection_pb2*.py` | Regenerated from proto |
| `utils/pb/suggestions/suggestions_pb2*.py` | Regenerated from proto |

### Service implementations

| File | Change |
|---|---|
| `transaction_verification/src/app.py` | Added per-order lock. After event a: chains event c internally, calls SUG.PrecomputeSuggestions, forwards c's VC to FD. After event b: calls FD.CheckUserFraud. Imports FD and SUG stubs for inter-service calls. |
| `fraud_detection/src/app.py` | Added per-order lock. Added `ForwardVC` handler for receiving c's VC from TV. Added `_try_run_e()` causal gating: runs CheckCardFraud only when both d (local) and c (forwarded) are complete. After e: forwards e's VC to SUG. Imports SUG stubs. |
| `suggestions/src/app.py` | Added per-order lock. Added `ForwardVC` handler for receiving e's VC from FD. Added `_try_run_g()` causal gating: runs FinalizeSuggestions only when both f (local) and e (forwarded) are complete. Added `AwaitPipelineResult` RPC with `threading.Event` to block until pipeline completion. |
| `orchestrator/src/app.py` | Removed all `threading.Event`-based dependency management, `run_event()`, `run_finalize()`, `merged_from()`, and the 7-worker thread pool. Now only triggers root events a and b on TV, then calls `SUG.AwaitPipelineResult()`. Rejection responses changed from HTTP 400 to HTTP 200 with `"status": "Order Rejected"`. |

## Observed vector clocks (successful order)

The partial order and VC values remain the same as before, but the mechanism is now distributed:

| Step | Event | Service | VC | Triggered by |
|---|---|---|---|---|
| 1 | ValidateItems (a) | TV | `[1, 0, 0]` | Orchestrator (root) |
| 2 | ValidateUserData (b) | TV | `[2, 0, 0]` | Orchestrator (root) |
| 3 | ValidateCardFormat (c) | TV | `[3, 0, 0]` | TV internally (after a) |
| 4 | CheckUserFraud (d) | FD | `[2, 1, 0]` | TV calls FD (after b) |
| 5 | PrecomputeSuggestions (f) | SUG | `[1, 0, 1]` | TV calls SUG (after a) |
| 6 | CheckCardFraud (e) | FD | `[3, 2, 0]` | FD internally (gated on d + c's VC from TV) |
| 7 | FinalizeSuggestions (g) | SUG | `[3, 2, 2]` | SUG internally (gated on f + e's VC from FD) |

Note: steps 1-2 run in parallel on TV so their relative order may swap. Steps 3-5 also run concurrently across services. Steps 6 and 7 are causally ordered by the VCs themselves.

## Error propagation paths

| Scenario | What happens |
|---|---|
| a fails (empty items) | TV forwards `source="a"` failure to FD and SUG. FD marks c as failed, skips e, forwards e failure to SUG. SUG marks f and e as failed, skips g, pipeline result = failure. |
| b fails (terms not accepted) | TV forwards `source="d"` failure to FD (d will never run). FD marks d as failed, skips e when c arrives, forwards e failure to SUG. SUG receives e failure, waits for f, skips g, pipeline result = failure. |
| e fails (fraudulent card) | FD forwards `source="e"` failure to SUG. SUG waits for f, skips g, pipeline result = failure. |

## How to verify the inter-service flow in logs

```bash
docker compose logs --no-color --tail 200 transaction_verification fraud_detection suggestions \
  | grep -E "event=(ForwardVC|CheckUserFraud|CheckCardFraud|PrecomputeSuggestions|FinalizeSuggestions)"
```

Look for:
- `[FD] event=ForwardVC source=c` -- TV forwarded c's VC to FD
- `[FD] event=CheckCardFraud` -- FD ran e after causal gating on d + c
- `[SUG] event=ForwardVC source=e` -- FD forwarded e's VC to SUG
- `[SUG] event=FinalizeSuggestions` -- SUG ran g after causal gating on f + e

The orchestrator logs should show only `starting_root_events`, not individual event dispatches.
