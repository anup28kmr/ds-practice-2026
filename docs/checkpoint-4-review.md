# Checkpoint 4 — Team-lead review

> Temporary file. Land or rebase it out before the merge to `master`;
> the rubric does not grade it.

## 1. TL;DR

- **Ready to merge.** All BASE rubric items met; one CP3-pre-existing
  verifier check (`bonus:participant-failure-recovery`) is failing on
  the tip exactly as it failed at the CP3 submission `f33f8da` — not a
  CP4 regression.
- **7 / 7 BASE rubric items MET. 4 / 4 BONUS (TA pre-flagged) attempted
  and answered with measurements.**
- **Worth your attention:** the e2e suite for scenario 4 was hardened
  in the last commit to read live stock instead of assuming seed — the
  CP3 verifier's concurrent-writes test writes arbitrary values to
  every key, including the title scenario 4 uses, so the previous
  hard-coded `INITIAL_STOCK = 3` was brittle to test-order interaction.

## 2. What's new since Checkpoint 3

`git log f33f8da..HEAD --oneline`:

```
c23764b Make conflict e2e test robust to non-seed stock
5d6391e Honour Guide14 OTEL_METRIC_EXPORT_INTERVAL=1000
5e4ed40 Update plan with Phase-2 audit and remaining OTEL gap
0988050 Add CP4 architecture, evaluation, and summary docs
cf8f6b9 Add load harness, supersale and throughput measurements
c8f9280 Add four end-to-end test scenarios from Guide13
84b4176 Provision Grafana dashboard for CP4 metrics
1ffa8aa Add OpenTelemetry instrumentation and Grafana OTEL-LGTM stack
7d9e56f Plan Checkpoint 4 work
```

Grouped by behavior:

- **Observability stack and instrumentation.** New `observability`
  compose service runs `grafana/otel-lgtm` (Grafana + Prometheus +
  Tempo + OTel Collector in one image) on ports 3000 / 4317 / 4318.
  `utils/telemetry.py` is a single ~85-line helper that initialises
  OTLP-HTTP exporters in any service via `init_telemetry(name)`. Four
  services were instrumented: orchestrator, order_executor (all three
  replicas), books_database (all three replicas), payment_service.
- **Grafana dashboard auto-provisioned.** `docs/grafana/dashboards/checkpoint-4.json`
  (12 panels: orchestrator request rate / latency / in-flight; executor
  primary / 2PC outcomes / 2PC latency; DB writes per replica / pending
  reservations / kv-store key counts; payment outcomes / latency;
  Tempo trace search). Loaded on container start via the bind-mounted
  provisioning yaml so demo time is "open URL, see panels", not "import
  a JSON".
- **Four Guide13 end-to-end scenarios in `tests/e2e/`.** Stdlib + pytest
  only. Test 4 (conflicting orders on the same title) reads the live
  stock at start and asserts that exactly `min(stock, N_ORDERS)`
  commits — robust to the CP3 verifier having previously written to
  the same key.
- **Load harness and measurements (`load_test/run_load.py`).** Three
  modes (constant / step / spike); open-loop scheduler so the harness
  measures real throughput rather than closed-loop self-throttling.
  Result CSVs in `load_test/results/`.
- **Four TA-question answers (`docs/checkpoint-4-evaluation.md`).**
  Verbatim question → first-sentence answer → numbers → analysis.
- **Architecture documentation (`docs/checkpoint-4-architecture.md`).**
  Mermaid diagram and a printable port table covering all 14 services.
- **Host-port shift `502xx → 512xx`.** Windows 11 reserves the
  `50167-50266` dynamic-port range on at least one dev machine, so the
  CP3 ports refused to bind. Container-internal ports are unchanged so
  the CP3 logging / peer-discovery strings still match the source.
- **Guide14 compliance fix.** `OTEL_METRIC_EXPORT_INTERVAL=1000`
  honoured (the previous code only read a non-standard `_MS` suffix
  variable with a 10 s default).

## 3. Checkpoint 4 requirements coverage

| # | Requirement (verbatim, source) | Service(s) | Implementation pointer | How to verify | Status |
|---|---|---|---|---|---|
| BASE-1 | "System operational with all components and order execution flow" (Guide15) | all | [docker-compose.yaml](../docker-compose.yaml); [scripts/checkpoint3-checks.ps1](../scripts/checkpoint3-checks.ps1) | `docker compose up --build -d && docker compose ps` → 14 services Up | **MET** |
| BASE-2 | "Test suites (manual & automated) with demonstration" (Guide15) | all | [tests/e2e/test_01..04](../tests/e2e/); [scripts/checkpoint4-checks.ps1](../scripts/checkpoint4-checks.ps1); [scripts/checkpoint3-checks.ps1](../scripts/checkpoint3-checks.ps1) | `.\scripts\checkpoint4-checks.ps1` → `4 passed`, banner `checkpoint4-checks PASSED` | **MET** |
| BASE-3 | "Metrics and traces collection with demonstration" (Guide15) + Guide14 "≥ 2 examples each of Span, Counter, UpDownCounter, Histogram, and Asynchronous Gauge" | orchestrator, order_executor, books_database, payment_service | [utils/telemetry.py](../utils/telemetry.py); [orchestrator/src/app.py:48-89](../orchestrator/src/app.py#L48-L89); [order_executor/src/app.py:75-105](../order_executor/src/app.py#L75-L105); [books_database/src/app.py:120-155](../books_database/src/app.py#L120-L155); [payment_service/src/app.py:30-45](../payment_service/src/app.py#L30-L45) | `curl 'http://127.0.0.1:3000/api/datasources/proxy/uid/prometheus/api/v1/query?query=checkout_requests_total'` returns `status:success` with non-zero values after one checkout | **MET** |
| BASE-4 | "Add System Logs: Implement relevant system logs within your application" (Guide15) | all | `[SVC] event=... key=value` lines across all services (pre-existing CP3 R3 work) | `docker compose logs orchestrator order_executor_1 order_executor_2 order_executor_3 books_database_1 books_database_2 books_database_3 payment_service order_queue` after any demo step | **MET** |
| BASE-5 | "Project organization, documentation, collaboration" (Guide15) | docs | [README.md](../README.md); [docs/checkpoint-4-plan.md](checkpoint-4-plan.md); [docs/checkpoint-4-summary.md](checkpoint-4-summary.md); [docs/checkpoint-4-architecture.md](checkpoint-4-architecture.md); [docs/checkpoint-4-evaluation.md](checkpoint-4-evaluation.md) | open the README — the top-of-file note links to the four CP4 docs | **MET** |
| BASE-6 | "Final Architecture Diagram: Develop an architecture diagram illustrating the multiple services, ports, communication protocols, and their relationships" (Guide15) | docs | [docs/checkpoint-4-architecture.md](checkpoint-4-architecture.md) (Mermaid + port table + legend) | open in any Markdown-aware viewer | **MET** |
| BASE-7 | "Grafana Dashboard: Create a Grafana Dashboard to visualize the collection of your metrics and traces" (Guide15) + Guide14 "Save dashboard JSON model locally in repository" | observability + all instrumented services | [docs/grafana/dashboards/checkpoint-4.json](grafana/dashboards/checkpoint-4.json); [docs/grafana/provisioning/dashboards/dashboards.yml](grafana/provisioning/dashboards/dashboards.yml) | with stack up: open `http://127.0.0.1:3000/d/cp4-overview/` (anonymous Admin enabled) — 12 panels render | **MET** |
| BONUS Q1 | TA pre-flagged: *"What if we have supersale?"* | observability, load harness | [docs/checkpoint-4-evaluation.md §1](checkpoint-4-evaluation.md#1-what-if-we-have-supersale) + [load_test/results/supersale.csv](../load_test/results/supersale.csv) | run the command quoted in §1 of evaluation.md | **MET** |
| BONUS Q2 | TA pre-flagged: *"How many can we handle?"* | observability, load harness | [docs/checkpoint-4-evaluation.md §2](checkpoint-4-evaluation.md#2-how-many-can-we-handle) + [load_test/results/throughput-step.csv](../load_test/results/throughput-step.csv) + [throughput-high.csv](../load_test/results/throughput-high.csv) | run the step command quoted in §2 | **MET** |
| BONUS Q3 | TA pre-flagged: *"How many replicas do we need?"* | observability, load harness | [docs/checkpoint-4-evaluation.md §3](checkpoint-4-evaluation.md#3-how-many-replicas-do-we-need) + [load_test/results/single-executor.csv](../load_test/results/single-executor.csv) | follow the commands quoted in §3 (stop two executors, re-run the constant-rate load) | **MET** |
| BONUS Q4 | TA pre-flagged: *"What's the bottleneck?"* | analysis | [docs/checkpoint-4-evaluation.md §4](checkpoint-4-evaluation.md#4-whats-the-bottleneck) (four pieces of evidence: throughput-gap, p95 inflation, single-vs-three executors identity, source-code citation) | read §4 | **MET** |

## 4. Where to start the review

Open these in order, one sentence per file on why:

1. [docs/checkpoint-4-summary.md](checkpoint-4-summary.md) — the
   one-pager: what changed, the four TA-question results in one
   sentence each, and the explicit "things to flag at demo time" list.
2. [docs/checkpoint-4-architecture.md](checkpoint-4-architecture.md) —
   the Mermaid system diagram + port table; orients you to what the
   observability service is doing and how it sits beside the 13 CP3
   services.
3. [utils/telemetry.py](../utils/telemetry.py) — 85 lines that decide
   how every instrumented service exports. If you suspect the
   instrumentation is wrong, this is the only file to read; the four
   service-level files are then just `init_telemetry("name")` + a
   handful of `_meter.create_*` calls.
4. [tests/e2e/test_04_conflicting_orders.py](../tests/e2e/test_04_conflicting_orders.py)
   — the only e2e test with non-trivial logic (it has to wait for
   coordinator decisions out-of-band of the HTTP response). The
   "read live stock first" hardening lives in this file and the helper
   in [tests/e2e/_common.py](../tests/e2e/_common.py).
5. [docs/checkpoint-4-evaluation.md](checkpoint-4-evaluation.md) — the
   four TA-question answers. Each section quotes the question, opens
   with the answer, then shows the numbers. The bottleneck section §4
   has four orthogonal pieces of evidence pointing at the same
   single-leader serializer.

## 5. How to verify end-to-end

From a clean clone of `origin/individual-sten-qy-li`, in PowerShell at
the repo root:

```powershell
# 1. Bring up the stack with a full rebuild. ~3 minutes on first run.
docker compose down -v
Remove-Item books_database/state/1/*, books_database/state/2/*, books_database/state/3/* -Recurse -Force -ErrorAction SilentlyContinue
docker compose up --build -d
docker compose ps
```

Expected: 14 services `Up`, including `observability`.

```powershell
# 2. Sanity-check the OTEL pipeline. The pre-warm POST exercises the
#    orchestrator, executor, DB, and payment in one shot.
$body = Get-Content -Raw test_checkout.json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8081/checkout `
    -ContentType 'application/json' -Body $body
Start-Sleep -Seconds 3
curl.exe -s 'http://127.0.0.1:3000/api/datasources/proxy/uid/prometheus/api/v1/query?query=checkout_requests_total'
```

Expected: `{"status":"success", ...}` with at least one
`status_class="2xx"` entry showing `value` ≥ `"1"`.

```powershell
# 3. Open the Grafana dashboard. Anonymous Admin is enabled, so no login.
Start-Process http://127.0.0.1:3000/d/cp4-overview/
```

Expected: page loads, four rows visible (Orchestrator / Executor / DB /
Payment / Traces). With the stack idle the panels show flat lines at
zero — they animate when you fire a checkout.

```powershell
# 4. Tear down and re-run from a verifier-grade clean state.
docker compose down -v
.\scripts\checkpoint4-checks.ps1
```

Expected output ends with `4 passed in <N>s` followed by
`checkpoint4-checks PASSED`.

```powershell
# 5. CP3 regression check (no build, since the previous step did the rebuild).
.\scripts\checkpoint3-checks.ps1 -SkipBuild
```

Expected: `Passed: 18, Failed: 1`. The single FAIL is
`bonus:participant-failure-recovery` and references a
`docker-compose.fail-inject.yaml` override that was deleted at commit
`2b12c97 code cleanup` **before** the CP3 submission (`f33f8da`). CP4
did not regress this; it was already 18/19 on the CP3 submission tip.
B2 behavior itself is exercised by
[order_executor/tests/test_2pc_crash_recovery.py](../order_executor/tests/test_2pc_crash_recovery.py).

```powershell
# 6. Tear down.
docker compose down -v
```

## 6. Things to specifically scrutinize

- **`tests/e2e/test_04_conflicting_orders.py` change.** The previous
  version asserted `commits <= INITIAL_STOCK = 3`. After the CP3
  verifier's `bonus:concurrent-writes` had run, DDIA stock was 205, so
  all 8 orders committed and the assertion mis-fired with the
  misleading "per-title lock failed" message. The hardening reads
  live stock at test start and asserts `min(stock, N_ORDERS)` commits.
  Confirm this is the property you want graded.
- **`utils/telemetry.py` env-var precedence.** I read
  `OTEL_METRIC_EXPORT_INTERVAL` first (per Guide14 / OTel SDK spec),
  fall back to the legacy `OTEL_METRIC_EXPORT_INTERVAL_MS`, then
  default to 10 000 ms. The legacy fallback is the only thing that
  keeps the dev-time scripts in [scripts/](../scripts/) working
  unchanged. If you'd rather drop the legacy name entirely, deleting
  the second branch in `_exporter_endpoint()` reduces 6 lines to 2.
- **Single-process Flask in the orchestrator.** The throughput
  measurement (§2 in evaluation.md) hits ~80 RPS before it saturates.
  This is a known Flask dev-server limitation, not a CP3 design defect,
  but worth flagging because the supersale demo intentionally drives
  the system past the 2PC commit rate.
- **Test-order coupling between CP3 verifier and CP4 e2e.** The CP3
  `bonus:concurrent-writes` test sets every seeded title to arbitrary
  values via raw `Write` RPCs. If you run `checkpoint3-checks.ps1`
  immediately followed by `pytest tests/e2e`, you'll see the stack
  in a non-seed state. The CP4 verifier sidesteps this by running
  `docker compose down -v` first, but it's a hazard to be aware of for
  ad-hoc reruns.
- **Dashboard panel "2PC latency (p95)" can report 4-5 s under load.**
  This is a histogram-bucket effect, not per-session 2PC cost. The
  per-session 2PC commit on an empty system takes ~80 ms (timed by
  scenario 4). Under load the histogram counts the queue-wait time on
  the order_queue side. The evaluation §4 explains this; the dashboard
  panel does not, so a reviewer reading the dashboard cold could
  misinterpret.
- **`grafana/otel-lgtm:0.11.0` version pin.** Newer otel-lgtm builds
  rearrange the in-image provisioning paths. I deliberately pinned an
  older image so the bind-mount paths in
  [docker-compose.yaml:7,17,18](../docker-compose.yaml#L7) keep
  working. If we bump the image, expect to update those paths.

## 7. Known limitations

- **`checkpoint-4` git tag is not applied.** Reserved for you to apply
  to the merge commit on `master` after this PR lands, exactly like
  CP3 did with `checkpoint-3`.
- **`bonus:participant-failure-recovery` (CP3 verifier check 18 of 19)
  has been failing since `2b12c97 code cleanup` deleted the
  `docker-compose.fail-inject.yaml` override.** Out of scope for CP4
  per the brief's "do not touch CP3 work" rule. The underlying B2
  mechanism is still exercised by
  [`order_executor/tests/test_2pc_crash_recovery.py`](../order_executor/tests/test_2pc_crash_recovery.py).
- **No frontend instrumentation.** The frontend is nginx serving
  static files; OpenTelemetry there would be browser-side and out of
  scope.
- **Single Grafana dashboard, not three.** Guide14 says "create a
  dashboard" (singular). One focused board satisfies the requirement
  without splitting the demo audience's attention. If you'd prefer
  separate dashboards per service tier, splitting is a JSON edit.
- **Load measurements were taken on this dev machine (Windows 11 +
  Docker Desktop 29.4.0).** The reported ceilings (~80 HTTP RPS,
  ~2.5 2PC sessions/s) reproduce on this hardware; absolute numbers
  will differ on the demo machine. The relative claims (HTTP » 2PC,
  bottleneck is single-leader serial commit) are hardware-independent.

## 8. Open questions for the reviewer

1. **Is the BONUS scope right?** I treated the four TA pre-flagged
   demo questions as the optional / showcase track, since Guide13 /
   Guide14 / Guide15 list no explicit numbered bonus criteria. If the
   team has a different reading of "bonus" for CP4, the work is in
   `load_test/` and `docs/checkpoint-4-evaluation.md` and can be
   relabelled without code changes.
2. **Should I delete the load-test result CSVs from the branch?**
   They're ~5 800 lines of CSV in
   [load_test/results/](../load_test/results/) and add 175 kB to the
   working tree. The evaluation doc cites specific numbers from them
   for reproducibility, but the runs can also be re-executed with one
   command. I left them in for evidentiary value; flag if you'd
   rather they be gitignored.
3. **Is the `OTEL_METRIC_EXPORT_INTERVAL=1000` choice right?** Guide14
   names that value verbatim. 1 s is aggressive for production but
   appropriate for a demo where you want panels to move during the
   "submit checkout" beat. Bumping to 5 s would still be visibly
   live and would lower the OTLP traffic by 5×. No strong opinion;
   happy to change.
4. **Operator demo script.** Per the team workflow, the operator's
   demonstration script lives outside the repo (uncommitted
   `local-only/checkpoint-4-script.tex`). Confirm you've received a
   copy via the channel we agreed; it should not be looked for in
   the merge.

## 9. Reviewer's checklist

- [ ] CP3 regression: `.\scripts\checkpoint3-checks.ps1` reports 18/19
      (same as `f33f8da`).
- [ ] `docker compose up --build -d` from a clean clone produces 14
      `Up` services, no restart loops.
- [ ] `docker compose exec orchestrator env | findstr OTEL_METRIC` shows
      `OTEL_METRIC_EXPORT_INTERVAL=1000`.
- [ ] `http://127.0.0.1:3000/d/cp4-overview/` loads with 12 populated
      panels after a checkout.
- [ ] `.\scripts\checkpoint4-checks.ps1` ends with
      `4 passed` + `checkpoint4-checks PASSED`.
- [ ] Each row in §3 reproduces via its stated `How to verify` step.
- [ ] §6 "Things to specifically scrutinize" — each item read and
      decision recorded.
- [ ] §8 open questions answered before merge.
- [ ] `git tag checkpoint-4` applied to the merge commit on `master`
      (post-merge, not pre-merge).
- [ ] This file deleted before the merge to `master`, OR left in place
      with the next checkpoint's review appended below — your call.
