# Checkpoint 4 — Implementation Plan

This file is the working memo for the Checkpoint 4 effort. It is committed as the
first step of the `sten` branch so the team lead can sanity-check the direction
before any implementation lands.

The continuation point is commit `f33f8da04e7989e04d7d157e91ca068931da7a4b`
(the successful Checkpoint 3 submission). No Checkpoint 3 functionality may regress.

## 1. Requirement set (verbatim quotes from source)

### 1.1 Guide15 — Checkpoint 4 brief (graded, 5 points)

Source: <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide15>

| ID    | Verbatim requirement                                                                                                | Pts  |
|-------|---------------------------------------------------------------------------------------------------------------------|------|
| B-OP  | "Finish the implementation: Complete the remaining tasks in your distributed system project."                       | 1.0  |
| B-DOCKER | "Docker Setup: Verify that your services can be spun up seamlessly using Docker and Docker Compose."             | —    |
| B-TESTS  | "Test suites and demonstration coverage."                                                                        | 1.25 |
| B-METRICS | "Metrics/Traces collection and demonstration."                                                                  | 1.25 |
| B-LOG | "Add System Logs: Implement relevant system logs within your application."                                          | 0.25 |
| B-DOC | "Document Relevant Code: Provide concise and informative documentation for crucial sections." + project organization. | 0.25 |
| B-ARCH | Architecture diagram showing "multiple services, ports, communication protocols, and their relationships."         | 0.5  |
| B-DASH | "Explore the Grafana UI and create a dashboard to visualize the collected traces and metrics" — saved as JSON in repo. | 0.5  |
| B-TAG | "Create 'checkpoint-4' Tag: Add a version tag 'checkpoint-4' to mark this stage."                                   | —    |

`B-TAG` is **out of scope for this branch.** Per the task brief, tagging is the
team lead's responsibility after PR review. The plan will not apply `checkpoint-4`.

### 1.2 Guide14 — Monitoring & Observability (instructions for B-METRICS / B-DASH)

Source: <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide14>

> *"Add traces and metrics to some of your services, showcasing at least two
> meaningful examples for each: Span, Counter, UpDownCounter, Histogram, and
> Asynchronous Gauge."*

> *"Add OpenTelemetry API & SDK as dependencies (in requirements.txt) of the codebase."*

> Tools called out: Grafana OTEL-LGTM (single Docker image bundling Grafana +
> Prometheus + Tempo); ports 3000, 4317, 4318; OTLP HTTP endpoints
> `http://observability:4318/v1/metrics` and `http://observability:4318/v1/traces`.

> *"Save dashboard JSON model locally in repository (preferably docs folder)
> for persistence across restarts."*

### 1.3 Guide13 — End-to-end testing (instructions for B-TESTS)

Source: <https://courses.cs.ut.ee/2026/ds/spring/Main/Guide13>

Four required scenarios, verbatim:

1. *"Demonstrate a test scenario where a single non-fraudulent order is created
   from the frontend and verified for correctness."*
2. *"Create automated tests to handle scenarios involving multiple simultaneous
   non-fraudulent orders that do not conflict with each other."*
3. *"Automate test scenarios that involve a mixture of fraudulent and
   non-fraudulent orders, ensuring proper handling."*
4. *"Define and automate test scenarios for orders that contain conflicting
   requests, such as attempting to purchase the same book simultaneously."*

Recommended tools listed: Postman, Locust, Cypress, JMeter.

### 1.4 TA pre-flagged demo questions (mandatory deliverable, not in brief)

1. **"What if we have supersale?"** — load-spike behaviour.
2. **"How many can we handle?"** — sustainable throughput ceiling.
3. **"How many replicas do we need?"** — replica-sizing comparison.
4. **"What's the bottleneck?"** — named component, evidence-backed.

All four answers live in `docs/checkpoint-4-evaluation.md`, one section per
question. Each section quotes the question, gives the short answer in the first
sentence, then shows evidence.

### 1.5 Bonus criteria

Neither Guide13, Guide14, nor Guide15 list explicit bonus criteria with point
values for CP4. The four TA questions are treated as the optional/showcase work.
No additional bonus track is pursued.

## 2. Repo state at f33f8da

- 13 services in `docker-compose.yaml`: `frontend`, `orchestrator`,
  `fraud_detection`, `transaction_verification`, `suggestions`, `order_queue`,
  3× `order_executor_{1,2,3}`, `payment_service`, 3× `books_database_{1,2,3}`.
- Logging: every service emits `[SVC] event=... key=value` lines (CP3 R3). Good
  baseline for **B-LOG**.
- Tests: per-service tests under `<service>/tests/*.py` plus a 19-check
  PowerShell verifier at `scripts/checkpoint3-checks.ps1`.
- Docs: `README.md` (~20 KB), CP3 design rationale appendix, four diagrams in
  `docs/diagrams/`.
- No OpenTelemetry / Prometheus / Grafana / Locust dependencies anywhere — the
  monitoring + load-test work is greenfield.

## 3. Implementation sketch (per requirement)

### B-METRICS + B-OTEL-LGTM — OpenTelemetry instrumentation + observability backend

- New compose service `observability` using `grafana/otel-lgtm`, ports 3000
  (Grafana UI), 4317 (OTLP gRPC), 4318 (OTLP HTTP). Bind-mount
  `docs/grafana/dashboards` and `docs/grafana/provisioning` so the dashboard
  loads automatically on `compose up`.
- New shared module `utils/telemetry.py` — single `init_telemetry(service_name)`
  call that wires up:
  - `TracerProvider` + OTLPSpanExporter (HTTP, endpoint
    `http://observability:4318/v1/traces`).
  - `MeterProvider` + OTLPMetricExporter (HTTP).
  - Auto-detect via env var `OTEL_EXPORTER_OTLP_ENDPOINT`; default to
    `http://observability:4318`.
- Per-service instrumentation:
  - `orchestrator/src/app.py`: HTTP entry span on `/checkout`; Counter
    `checkout_requests_total`; Histogram `checkout_latency_seconds`;
    UpDownCounter `in_flight_checkouts`.
  - `order_executor/src/app.py`: Span around `run_2pc`; Counter `twopc_total{outcome=commit|abort}`;
    Histogram `twopc_latency_seconds`.
  - `books_database/src/app.py`: Counter `db_writes_total{role=primary}`;
    Histogram `db_write_latency_seconds`; UpDownCounter `pending_orders_size`;
    Asynchronous Gauge `kv_store_size_keys`.
  - `payment_service/src/app.py`: Span around `Charge`; Counter `payment_total{outcome}`;
    Histogram `payment_latency_seconds`.
- "Two meaningful examples" required for each of Span / Counter / UpDownCounter /
  Histogram / Asynchronous Gauge: covered by orchestrator + executor spans;
  orchestrator + executor counters; orchestrator + db UpDownCounters; orchestrator +
  executor + db + payment histograms; db `kv_store_size_keys` + executor
  `inflight_2pc_attempts` async gauges.
- Add `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`
  to each in-scope service's `requirements.txt`. Frontend (nginx) and pure-stub
  services are out of scope.

### B-DASH — Grafana dashboard JSON

- One dashboard in `docs/grafana/dashboards/checkpoint-4.json` with panels for:
  request rate (orchestrator), p50/p95 latency (orchestrator), 2PC outcome
  ratio, DB write throughput, payment success rate, pending-orders gauge,
  kv-store-size async gauge.
- Provisioning manifest in `docs/grafana/provisioning/dashboards/dashboards.yml`
  so the dashboard is preloaded on container start.

### B-TESTS — Four Guide13 end-to-end scenarios

- New `tests/e2e/` directory at the repo root.
- One pytest file per scenario:
  - `test_e2e_single_clean_order.py` — POST `test_checkout.json`, assert 200
    + payment in books_database log + stock decremented.
  - `test_e2e_multiple_non_conflicting.py` — submit N=10 orders for different
    titles via a thread pool, assert all 200, assert per-title stock matches
    expected end state.
  - `test_e2e_mixed_fraud.py` — submit a mix of `test_checkout.json` and
    `test_checkout_fraud.json`; clean orders commit, fraud orders reject with
    the documented payload.
  - `test_e2e_conflicting_orders.py` — submit N orders all targeting the same
    book quantity > current stock; assert that only `stock // qty_per_order`
    succeed and the rest abort cleanly. Ties B1 (concurrent writes) into the
    E2E surface.
- Single-command runner: `pytest tests/e2e -v`. Wrapped in
  `scripts/checkpoint4-checks.ps1` so the demo operator runs one command.

### B-LOG — existing logging

- Already meets the bar. The OpenTelemetry instrumentation will not displace the
  `[SVC] event=...` lines; both stay.

### B-ARCH — Architecture diagram

- New `docs/diagrams/checkpoint-4-architecture.svg` showing all 13 application
  services + the `observability` add-on, host ports, gRPC ports, and the
  request flow direction. Source kept as PlantUML or Mermaid for re-render.

### B-DOC — Documentation

- New `docs/checkpoint-4-summary.md` (the one-page operator/team-lead summary,
  produced in Phase 5).
- README gets a short "Checkpoint 4 additions" section pointing at the summary.

### TA Q1 — Supersale (load-spike)

- Locust file `load_test/locustfile.py` with two user classes:
  - `BaselineUser` — 1 request/s/user, clean checkout.
  - `SpikeUser` — fires a burst (e.g. `wait_time = constant(0.05)`).
- Spike script `scripts/run-supersale.ps1`: 30 s baseline → 60 s spike →
  60 s recovery. Captures Locust stats + extracts metrics from the
  observability backend. Output saved to `load_test/results/supersale.csv`.

### TA Q2 — Sustainable throughput

- Locust step-load profile: ramp arrival rate in steps of 5 RPS, hold each step
  ~30 s, stop when p95 latency > 2 s OR error rate > 1%. The last "good" step
  is reported as the ceiling.
- Output: `load_test/results/throughput.csv` + a small matplotlib bar/line plot
  generated by `load_test/plot_results.py` saved as PNG in `load_test/results/`.

### TA Q3 — Replica sizing

- Two compose override files:
  - `docker-compose.executors-1.yaml` — runs only `order_executor_1`.
  - `docker-compose.executors-3.yaml` — the default (1+2+3).
- Script `scripts/run-replica-comparison.ps1` brings the stack up under each
  override, runs the throughput profile, captures throughput + p95 latency,
  prints a side-by-side table and writes `load_test/results/replicas.csv`.

### TA Q4 — Bottleneck

- Use the throughput run's per-service histograms + per-service CPU
  (`docker stats` snapshot via a small PowerShell helper) to attribute the
  ceiling to a specific component.
- The hypothesis ahead of measurement: the `order_queue` `Dequeue` is a single
  `popleft()` with no concurrent consumers — only the elected leader executor
  dequeues. If the hypothesis holds, the bottleneck is *not* a hardware
  resource but a single-consumer queue. If it doesn't hold, the histograms
  attribute the wait to whichever service has the largest p95 delta vs. its
  median (likely DB primary `Prepare/Commit` or the orchestrator pipeline).

## 4. Design choices (with rationale)

- **OpenTelemetry over Prometheus client libraries.** Guide14 names
  OpenTelemetry explicitly and recommends the OTEL-LGTM image, which speaks
  OTLP. Prometheus client would force a `/metrics` pull endpoint per service.
- **OTLP HTTP, not gRPC.** Existing services already use gRPC for inter-service
  RPC; mixing OTLP gRPC adds a second gRPC client per process. OTLP HTTP is one
  `requests`-style dependency and is what Guide14's example URLs use.
- **Locust over k6 / JMeter.** Guide13 lists Locust; the rest of the stack is
  Python so a Locustfile keeps the toolchain consistent. No JVM, no separate
  installer.
- **pytest for the four E2E scenarios.** They already need to assert against
  logs and HTTP responses; pytest gives that for free. Not picking Postman
  collections because they don't fit "single command, reproducible from a clean
  clone."
- **No frontend instrumentation.** The frontend is nginx serving static files;
  OpenTelemetry there would be browser-side and out of scope.
- **One Grafana dashboard, provisioned.** Two dashboards would dilute the demo;
  one focused board satisfies B-DASH without churn.
- **No code refactors.** The task brief is explicit: stay in scope. Telemetry
  is added at the seams of each service; the service logic is not touched.

## 5. Risks and unknowns

| Risk | Default if it materializes |
|------|------|
| gRPC server-side instrumentation may need manual span management since service code uses raw `grpc.server`. | Wrap server methods with a tiny decorator in `utils/telemetry.py` rather than pulling in the full `opentelemetry-instrumentation-grpc` package. |
| OTEL-LGTM Tempo retention may drop spans during longer load tests. | Run the supersale + throughput profile against fresh `compose up` to avoid retention loss; cap test duration at ≤ 5 minutes. |
| Locust on Windows + Docker Desktop may have lower max sockets than Linux. | Cap concurrency at a level where the load generator itself is not the limiter; report the measured ceiling honestly. |
| The CP3 `/checkout` flow is heavy (CP2 pipeline + 2PC). Real RPS ceiling may be tens, not hundreds. | Report the actual number with a one-line explanation rather than tune the system. CP4 grades the measurement, not the magnitude. |
| `docker-compose.executors-1.yaml` may break `PEERS=` env var format used by bully election. | Set `PEERS=1@order_executor_1:50055` so single-replica run still satisfies the format. Verify executor still boots. |
| Asynchronous Gauge callback can hold a lock under load; if it deadlocks instrumentation, drop it. | Replace `kv_store_size_keys` with a periodic refresh and snapshot the size with a non-blocking `try_lock`. |

## 6. Branch & commit plan

1. ✅ Local working-tree drift stashed (`pre-CP4` stash).
2. ✅ `sten` branch created at `f33f8da` (no existing remote tip).
3. Commits, roughly in order:
   - `Plan Checkpoint 4 work` — this file only.
   - `Add OpenTelemetry instrumentation and Grafana OTEL-LGTM stack` — utils
     helper, per-service requirements/code updates, compose service.
   - `Provision Grafana dashboard for CP4 metrics` — dashboard JSON +
     provisioning manifest.
   - `Add CP4 architecture diagram` — `docs/diagrams/checkpoint-4-architecture.svg`.
   - `Add four end-to-end test scenarios from Guide13` — `tests/e2e/*` +
     `scripts/checkpoint4-checks.ps1`.
   - `Add Locust load-test harness and supersale scenario` —
     `load_test/locustfile.py` + `scripts/run-supersale.ps1`.
   - `Add throughput-ceiling measurement` — step-load profile + plotting
     script.
   - `Add replica-sizing comparison` — compose overrides +
     `scripts/run-replica-comparison.ps1`.
   - `Record CP4 measurements and TA-question answers` — populated
     `docs/checkpoint-4-evaluation.md` with captured numbers.
   - `Add CP4 summary` — `docs/checkpoint-4-summary.md`.
4. `git push -u origin sten` once all of the above land and Phase 5 passes.
5. **No `git tag` is run by this work. The `checkpoint-4` tag is the team
   lead's responsibility.**

## 7. Out of scope (explicit non-goals)

- Improving CP3 work (the brief says do not).
- Implementing the Guide14 poster assignment (separate deliverable, separate
  submission channel).
- Tagging the work as `checkpoint-4` (team lead).
- Cleaning up the stashed local drift (kept in stash for the developer's later
  recovery).
- Frontend instrumentation, frontend changes of any kind.

## 8. Current-state audit (2026-05-13, pre-final-review pass)

Recorded after the six implementation commits landed. Reproduces the
requirement set from §1 and tags each item with its observed status.

### 8.1 BASE rubric (Guide15)

| Item | Pts | Implementation pointer | Status |
|------|----:|------------------------|--------|
| System operational with all components and order execution flow | 1.0 | 14-service `docker-compose.yaml`; `scripts/checkpoint3-checks.ps1` (CP3 verifier) | MET (Docker bring-up to be re-verified from clean state — see §8.4) |
| Test suites (manual & automated) with demonstration | 1.25 | `tests/e2e/test_01..04_*.py` (Guide13 scenarios), `scripts/checkpoint4-checks.ps1`, plus CP3 verifier and per-service tests | MET |
| Metrics and traces collection with demonstration | 1.25 | `utils/telemetry.py`; orchestrator / order_executor / books_database / payment_service instrumented. Instrument counts: Span = 5 (orch HTTP, exec `run_2pc`, db `db_write`, payment `payment_prepare`+`payment_commit`); Counter = 4; UpDownCounter = 2; Histogram = 4; Async Gauge = 3 — all five kinds exceed Guide14's `≥ 2 examples` rule | MET |
| Logging | 0.25 | Pre-existing `[SVC] event=key=value` lines across all services (R3 from CP3) | MET |
| Project organization, documentation, collaboration | 0.25 | `README.md` + `docs/checkpoint-4-{plan,summary,architecture,evaluation}.md` | MET |
| Architecture diagram | 0.5 | `docs/checkpoint-4-architecture.md` (Mermaid + port table + B&W-friendly fallback) | MET |
| Grafana Dashboard | 0.5 | `docs/grafana/dashboards/checkpoint-4.json` (12 panels) + auto-provision yaml | MET |

### 8.2 BASE prerequisites (Guide14 monitoring spec)

| Item | Pointer | Status |
|------|---------|--------|
| observability service `grafana/otel-lgtm` on 3000/4317/4318 | `docker-compose.yaml` `observability:` block | MET |
| OpenTelemetry API+SDK+OTLP HTTP in each instrumented service's requirements | `{orchestrator,order_executor,books_database,payment_service}/requirements.txt` | MET |
| "≥ 2 examples each of Span, Counter, UpDownCounter, Histogram, Asynchronous Gauge" | see counts in §8.1 | MET |
| Dashboard JSON saved locally + re-importable | `docs/grafana/dashboards/checkpoint-4.json` (1 dashboard, `uid=cp4-overview`); provisioning yaml bind-mounted | MET |
| Set environment variable `OTEL_METRIC_EXPORT_INTERVAL=1000` | **Not honoured**: compose passes no `OTEL_*` variables; `utils/telemetry.py` reads a non-standard `OTEL_METRIC_EXPORT_INTERVAL_MS` with default `10000` ms. The Guide14 standard variable currently has no effect. | PARTIAL |

### 8.3 BASE prerequisites (Guide13 end-to-end testing)

| Scenario | Pointer | Status |
|----------|---------|--------|
| 1 — single non-fraudulent order | `tests/e2e/test_01_single_clean_order.py` | MET |
| 2 — multiple simultaneous non-conflicting | `tests/e2e/test_02_multiple_non_conflicting.py` (4 distinct titles, threaded barrier-start) | MET |
| 3 — mixed fraud + non-fraud | `tests/e2e/test_03_mixed_fraud.py` (3 clean + 3 fraud, interleaved) | MET |
| 4 — conflicting / same-book | `tests/e2e/test_04_conflicting_orders.py` (8 concurrent on stock=3 title; asserts via 2PC decision log because orchestrator returns `Order Approved` pre-2PC) | MET |

### 8.4 Open verifications

- Clean-state docker bring-up has not been re-run since the OTEL export-interval
  gap was identified. §8.5's fix changes only compose env-vars and the
  `telemetry.py` reader; nothing in the dataplane changes. Phase 4 runs
  `docker compose down -v && docker compose up --build -d` followed by the
  CP3 verifier and `pytest tests/e2e` to confirm no regression.
- CP3 verifier is at **18/19** on the current tip. The single FAIL is
  `bonus:participant-failure-recovery`, which references a
  `docker-compose.fail-inject.yaml` override that was deleted at commit
  `2b12c97 code cleanup` **before** the CP3 submission `f33f8da`. CP4 has
  not regressed this — the count was already 18/19 at CP3 submission. The
  B2 mechanism itself is still demonstrable via
  `order_executor/tests/test_2pc_crash_recovery.py`. CP3 work is out of
  scope per the task brief, so the verifier count stays as-is.

### 8.5 Remaining fix

Single PARTIAL item from §8.2: honour Guide14's
`OTEL_METRIC_EXPORT_INTERVAL=1000` env var.

- **utils/telemetry.py**: read the standard env var
  `OTEL_METRIC_EXPORT_INTERVAL` (milliseconds, per OTel spec) first, fall
  back to the legacy `OTEL_METRIC_EXPORT_INTERVAL_MS`, then default to
  `10000`. This both unblocks the Guide14 variable and preserves any
  in-flight scripts that set the legacy name.
- **docker-compose.yaml**: add `OTEL_METRIC_EXPORT_INTERVAL=1000` to the
  `environment:` blocks of the four instrumented services (orchestrator,
  order_executor_{1,2,3}, payment_service, books_database_{1,2,3}). 1 s
  cadence is Guide14's prescribed value and is well below the demo span
  (~30 s for any single load run), so dashboard panels refresh visibly
  during the demo without overwhelming the OTLP receiver.

This single fix is the only outstanding implementation work. It is one
commit, scoped to two files. After it lands, Phase 4 verification runs
end-to-end against the updated compose.
