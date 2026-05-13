# Checkpoint 4 — Summary for the team lead

One page. Plain language. No code.

## What's new since CP3 (5 bullets)

- **Observability**: a new `observability` container (Grafana OTEL-LGTM
  bundle) plus a shared `utils/telemetry.py`. Four services
  (orchestrator, order_executor, books_database, payment_service) now
  emit OpenTelemetry traces and metrics over OTLP-HTTP. The metric
  inventory covers Guide14's mandatory list — ≥ 2 examples each of
  Span, Counter, UpDownCounter, Histogram, and Asynchronous Gauge.
- **Pre-provisioned Grafana dashboard** at `http://127.0.0.1:3000/d/cp4-overview/`
  with 12 panels (request rate, p95 latency, 2PC outcomes, DB write
  rate, pending 2PC reservations, payment outcomes, recent traces from
  Tempo). Source JSON lives in
  [docs/grafana/dashboards/checkpoint-4.json](grafana/dashboards/checkpoint-4.json).
- **Four Guide13 end-to-end test scenarios** in `tests/e2e/`, runnable
  with a single command. Tests 1-3 hit `/checkout` and verify response
  shape; test 4 parses the executors' `2pc_decision` log lines because
  the orchestrator returns "Order Approved" *before* the async 2PC
  resolves — so the only way to assert atomicity is to wait for the
  coordinator's decision.
- **Load harness + four TA-question answers** in `load_test/run_load.py`
  (constant / step / spike modes) and `docs/checkpoint-4-evaluation.md`.
  Measured: HTTP ceiling ~80 RPS, 2PC ceiling ~2.5 sessions/s,
  bottleneck = single-leader serialized consume_loop.
- **Host port shift 502xx → 512xx** in compose and the seven test
  files that connect to those ports. Windows 11 reserves 50167-50266
  on the dev machine, so the original CP3 ports refused to bind. CP3
  semantics are unchanged — internal container ports are the same as
  before.

## How to run each new thing (one command per)

| Goal                                 | Command                                                                  |
|--------------------------------------|--------------------------------------------------------------------------|
| Bring the full stack up              | `docker compose up --build -d`                                           |
| Run the 4 Guide13 E2E scenarios      | `.\scripts\checkpoint4-checks.ps1`                                       |
| Open the Grafana dashboard           | `http://127.0.0.1:3000/d/cp4-overview/` (anonymous Admin enabled)        |
| Load test — supersale (TA Q1)        | `python load_test/run_load.py --mode spike --base-rate 3 --spike-rate 25 --baseline-s 15 --spike-s 30 --recovery-s 15 --label supersale` |
| Load test — throughput ceiling (Q2)  | `python load_test/run_load.py --mode step --rates 2,5,10,15,20,30,40,60,80,100 --duration 12 --label throughput` |
| Replica comparison (Q3)              | `docker compose stop order_executor_2 order_executor_3` then re-run the load harness |
| Snapshot metrics after a load run    | `python scripts/_cp4_prom_snapshot.py`                                   |
| CP3 verifier (regression check)      | `.\scripts\checkpoint3-checks.ps1` (still 19/19 — see Phase 5 below)     |
| Tear down                            | `docker compose down`                                                    |

## The four TA questions, in one sentence each

1. **Supersale.** The orchestrator's HTTP layer absorbs an 8× burst
   with unchanged p95 (~60 ms), but the underlying 2PC commit rate
   is unchanged at ~0.6 commits/s — so the queue grows during the
   spike and shrinks afterwards. **Evidence**:
   [checkpoint-4-evaluation.md §1](checkpoint-4-evaluation.md#1-what-if-we-have-supersale).
2. **How many can we handle.** ~80 HTTP RPS at p95 < 200 ms, ~2.5 2PC
   sessions/s sustained. **Evidence**:
   [checkpoint-4-evaluation.md §2](checkpoint-4-evaluation.md#2-how-many-can-we-handle).
3. **How many replicas.** 1 executor is enough for throughput, 3 are
   needed for the failover semantics the rubric grades; 3 DB replicas
   give 1-failure tolerance (B2) and synchronous replication cost is
   well below the 2PC cost. **Evidence**:
   [checkpoint-4-evaluation.md §3](checkpoint-4-evaluation.md#3-how-many-replicas-do-we-need).
4. **Bottleneck.** The elected order_executor leader's `consume_loop`
   — it dequeues one order at a time and runs 2PC synchronously, so
   only one commit is ever in flight. **Evidence**:
   [checkpoint-4-evaluation.md §4](checkpoint-4-evaluation.md#4-whats-the-bottleneck)
   (queue/HTTP gap, single-vs-three executor throughput identity,
   `consume_loop` source).

## Known limitations / things to flag at demo time

- The orchestrator returns *"Order Approved"* immediately after
  `Enqueue` succeeds; the 2PC commit happens later on the elected
  executor. This is a CP3 design choice, not a CP4 regression, but it
  means an order can be "approved" by the orchestrator and then later
  aborted by 2PC (insufficient stock). The status pane on the
  frontend reflects only the orchestrator's response. The Grafana
  panel "2PC outcomes per minute" is the authoritative view.
- The Flask dev server is single-process, which is fine up to ~80 RPS
  but flakes above that. Production would swap it for gunicorn.
- The supersale and throughput tests deplete the seed stock (44 total
  copies across 5 titles) quickly. After depletion every subsequent
  2PC aborts with `insufficient_stock` — that's a correct rejection
  but it does make sustained-commit measurements awkward. The
  evaluation doc accounts for this in its analysis.
- 2PC commit p95 latency reported as 4.75 s on the dashboard during
  load is a Prometheus histogram bucket interpolation; raw values
  cluster between 2.5 s and 5 s due to queue-wait time, not per-session
  cost. Per-session 2PC cost on an empty system is ~80 ms (see the
  E2E test 04 timing).
- The `checkpoint-4` git tag is **not** applied by this branch. That
  is the team lead's responsibility after PR review per the team
  contract. The plan, the implementation, and the verification all
  ran on `sten` and the push will be a fast-forward push, never a
  force-push.
- The local stash `pre-CP4: local port-shift drift and CP3 demo-script
  artifacts` (created on 2026-05-13) holds the dev's CP3-era working
  copy of the port shift plus two scratch files (the CP3 demo PDF
  script). It's not relevant to CP4 but is preserved in case the dev
  wants it back.
- **Pre-existing CP3 verifier issue (not a CP4 regression).** The CP3
  verifier at `scripts/checkpoint3-checks.ps1` reports 18/19 passing
  on the `sten` tip; the failing check is `bonus:participant-failure-recovery`,
  which references a `docker-compose.fail-inject.yaml` override file
  that was deleted at commit `2b12c97 code cleanup` *before* the CP3
  submission (`f33f8da`). The CP3 README claims 19/19; the verifier
  was actually 18/19 at submission for the same reason. CP4 left
  `test_2pc_fail_injection.py` and the surrounding wiring untouched
  per the "no CP3 changes" rule, so the count is unchanged at 18/19.
  The B2 bonus is otherwise demonstrable via
  `test_2pc_crash_recovery.py` (which kills and restarts the DB
  primary in the same flow) and the verifier *does* pass
  `bonus:concurrent-writes` (B1) on a clean run.
