# Distributed Systems Practice — Checkpoint 4

A distributed online-bookshop checkout system. The orchestrator
validates each `/checkout` via a Checkpoint-2 input-and-fraud
pre-pipeline, enqueues the order on a single in-memory FIFO, and a
bully-elected leader from the three-replica order-executor tier runs
2PC across the three-replica books database and a payment service.
Checkpoint 4 adds OpenTelemetry instrumentation, a pre-provisioned
Grafana dashboard, four Guide13 end-to-end test scenarios, and a load
harness with the measured answers to the TA's four pre-flagged
demonstration questions.

See [docs/checkpoint-4-architecture.md](docs/checkpoint-4-architecture.md)
for the system diagram and port table, and
[docs/checkpoint-4-evaluation.md](docs/checkpoint-4-evaluation.md) for
the TA-question evidence.

## Quick demo (5 minutes)

1. **Start the stack** from the repository root.

```powershell
docker compose up --build -d
docker compose ps
```

Expected: 14 services running — 13 application services (`frontend`,
`orchestrator`, three CP2 backends, `order_queue`, three
`order_executor` replicas, `payment_service`, three `books_database`
replicas) plus the `observability` add-on.

2. **Run the verifier.**

```powershell
.\scripts\checkpoint4-checks.ps1
```

Expected: `4 passed` followed by `checkpoint4-checks PASSED`. The
four scenarios cover a single clean order, multiple concurrent
non-conflicting orders, a mix of fraudulent and non-fraudulent
orders, and concurrent orders on the same title.

3. **Open the Grafana dashboard** at
   `http://127.0.0.1:3000/d/cp4-overview/`. Anonymous Admin access is
   enabled; no login. Twelve panels render across four rows
   (orchestrator, executor, database, payment) plus a Tempo trace
   search.

4. **(Optional)** Open `http://127.0.0.1:8080` for a manual order, or
   POST to `http://127.0.0.1:8081/checkout` with one of the prepared
   payloads (`test_checkout.json`, `test_checkout_oversold.json`,
   `test_checkout_fraud.json`).

5. **Tear down** when finished.

```powershell
docker compose down
```

## Repository layout

| Path | Contents |
|------|----------|
| `<service>/` | Per-service `Dockerfile`, `requirements.txt`, and `src/`. Thirteen application services. |
| `utils/telemetry.py` | OpenTelemetry helper shared by every instrumented service. |
| `utils/pb/<service>/` | gRPC `.proto` definitions and generated Python stubs. |
| `utils/other/hotreload.py` | Container entrypoint that restarts a service on source changes. |
| `tests/e2e/` | Four Guide13 end-to-end scenarios. |
| `scripts/checkpoint4-checks.ps1` | Bring up the stack from a clean state and run the e2e suite. |
| `load_test/run_load.py` | Open-loop load harness (constant / step / spike modes). |
| `load_test/results/*.csv` | Captured runs that back the TA-question evidence in `docs/checkpoint-4-evaluation.md`. |
| `docs/grafana/` | Pre-provisioned Grafana dashboard JSON and provisioning manifest. |
| `docs/checkpoint-4-*.md` | CP4 deliverables: architecture diagram, TA-question evaluation, team-lead review. |
