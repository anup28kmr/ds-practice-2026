# Checkpoint 4 — Cleanup plan

> Audit run on `individual-sten-qy-li` at commit `2f0f335`. This file
> records the reachability findings. No files have been deleted by the
> audit; Phase 4 is blocked on your explicit confirmation.

## 1. Baseline (Phase 1)

Run from a clean state (the verifier does its own `down -v` + rebuild):

| Verifier | Result | Notes |
|----------|--------|-------|
| `scripts/checkpoint4-checks.ps1` | **PASSED** | `4 passed in 20.83s`; final banner `checkpoint4-checks PASSED`. |
| `scripts/checkpoint3-checks.ps1 -SkipBuild` | **18 / 19** | The single FAIL is `bonus:participant-failure-recovery`, which references the deleted `docker-compose.fail-inject.yaml`. This is the same 18/19 the repo had at the CP3 submission `f33f8da` — not a regression. |

Branch position confirmed: HEAD = `2f0f3359d610ffa73facaa78bef3a7bb71c294bd`,
`origin/individual-sten-qy-li` = same SHA, working tree clean (one untracked
file `load_test/results/smoke.csv` predating the audit; not staged).

## 2. Required for Checkpoint 4 (must remain)

These files are the CP4 surface evaluated by the rubric or invoked by
`scripts/checkpoint4-checks.ps1`. Auditor does not touch them.

- `docker-compose.yaml` (includes `observability:` service)
- `utils/telemetry.py`
- All four instrumented services'
  `requirements.txt` and `src/app.py`:
  `orchestrator/`, `order_executor/`, `books_database/`,
  `payment_service/`.
- `docs/grafana/dashboards/checkpoint-4.json`
- `docs/grafana/provisioning/dashboards/dashboards.yml`
- `tests/e2e/__init__.py`, `tests/e2e/_common.py`,
  `tests/e2e/conftest.py`,
  `tests/e2e/test_01_single_clean_order.py`,
  `tests/e2e/test_02_multiple_non_conflicting.py`,
  `tests/e2e/test_03_mixed_fraud.py`,
  `tests/e2e/test_04_conflicting_orders.py`.
- `scripts/checkpoint4-checks.ps1`,
  `scripts/_cp3_db_probe.py`
  (used by `tests/e2e/_common.py::read_stock_quorum`),
  `scripts/_cp4_prom_snapshot.py`
  (referenced by `docs/checkpoint-4-summary.md` and
  `docs/checkpoint-4-evaluation.md`).
- `load_test/run_load.py`,
  `load_test/results/{single-executor,supersale,throughput-high,throughput-step}.csv`
  (evidence for the four TA-question answers).
- `docs/checkpoint-4-{plan,summary,architecture,evaluation,review,cleanup-plan}.md`.
- `README.md`.

## 3. Required for Checkpoint 3 (must remain to avoid regression)

These are invoked by `scripts/checkpoint3-checks.ps1` or are CP3
deliverables the rubric grades. Auditor does not touch them.

- All 13 CP3 services' Dockerfiles, `requirements.txt`, `src/app.py`,
  including the entire frontend, fraud_detection, transaction_verification,
  suggestions, order_queue trees.
- `scripts/checkpoint3-checks.ps1`,
  `scripts/_cp3_db_probe.py`.
- `books_database/tests/test_concurrent_writes.py`
  (invoked directly by the CP3 verifier).
- `order_executor/tests/test_2pc_fail_injection.py`
  (invoked directly by the CP3 verifier — failing on missing
  `docker-compose.fail-inject.yaml` since `2b12c97 code cleanup`; the
  brief excludes touching CP3 work, so this stays as-is).
- `test_checkout.json`, `test_checkout_oversold.json`,
  `test_checkout_fraud.json`
  (used by the CP3 verifier — see §3.x of the verifier).
- `docs/diagrams/consistency-protocol.svg`,
  `docs/diagrams/commitment-protocol.svg`
  (embedded in README as the R5 / R6 rubric diagrams).
- All gRPC artefacts in `utils/pb/<service>/` (proto + generated stubs
  imported by service code).
- `utils/other/hotreload.py` (COPY'd into every service Dockerfile as
  the container entrypoint).
- `utils/README.md` (describes the `api/` and `pb/` layout).

## 4. Stale candidate table

No file in this list is deleted by the audit. Every row is **FLAGGED
for review**.

| # | Path | Cat | Why stale | Default action | Risk of deletion |
|---|------|----:|-----------|----------------|------------------|
| 1 | `orchestrator/tests/test_cp3_execution_only.py` | 2 | Phase 8 CP3-era standalone test that re-creates `orchestrator` with the now-deleted `docker-compose.cp3-only.yaml` override file. Not invoked by either verifier. Cannot run today (file missing). | FLAG | Low — neither verifier touches it; removing it does not change CP3 / CP4 verifier outcomes. |
| 2 | `order_executor/tests/test_2pc_end_to_end.py` | 2 | Phase 5 standalone smoke test for the 2PC happy path. Superseded by the CP3 verifier's `2pc:valid-commit` and `convergence:read-all-replicas` checks, which exercise the same surface. Not invoked anywhere. | FLAG | Low. |
| 3 | `payment_service/tests/smoke_test.py` | 2 | Phase 4 standalone smoke for individual participant RPCs (Prepare / Commit / Abort, both DB and payment). Superseded by the CP3 verifier's full 2PC happy-path and oversold-abort checks. Not invoked anywhere. | FLAG | Low. |
| 4 | `test_checkout_empty_items.json` | 2 | "Prepared payload" mentioned only in README §3 (the manual-poke list). Not invoked by any verifier. The CP3 verifier exercises empty-items rejection elsewhere via the orchestrator's input validation. | FLAG | Low — verifiers untouched; README link breaks if you keep the README sentence verbatim, so deletion needs the README edit in the same commit. |
| 5 | `test_checkout_terms_false.json` | 2 | Same as #4, for the terms-not-accepted variant. | FLAG | Low — same README caveat. |
| 6 | `utils/api/bookstore.yaml` | 2 | OpenAPI example template described by `utils/README.md` as a starting point for code generation. Not imported by any service; the services use the gRPC `.proto` files in `utils/pb/` instead. | FLAG | Medium — these are course-distributed seed material rather than team-authored work; team-lead may want them preserved for reference. |
| 7 | `utils/api/fintech.yaml` | 2 | Same as #6. | FLAG | Medium — same. |
| 8 | `utils/api/ridehailing.yaml` | 2 | Same as #6. | FLAG | Medium — same. |
| 9 | `docs/diagrams/architecture-diagram.jpg` | 2 | CP2-era artefact. Not referenced by README, plan, summary, architecture, evaluation, or review. The CP4 architecture is now `docs/checkpoint-4-architecture.md` (Mermaid + port table). | FLAG | Low — but the brief says "err toward keeping docs". |
| 10 | `docs/diagrams/system-flow-diagram.jpg` | 2 | CP2-era artefact. Not referenced anywhere current. | FLAG | Low — same caveat. |
| 11 | `docs/diagrams/leader-election.svg` | 2 | CP2-era artefact (leader-election figure). Not referenced. | FLAG | Low — same caveat. |
| 12 | `docs/diagrams/vector-clocks.svg` | 2 | CP2-era artefact (vector-clocks figure). Not referenced. | FLAG | Low — same caveat. |
| 13 | `.idea/.gitignore` | Other | IntelliJ IDEA per-project gitignore committed by accident. | FLAG | Low — affects only IDE state for the original author. |
| 14 | `.idea/ds-practice-2026.iml` | Other | IntelliJ project module file. | FLAG | Low — same. |
| 15 | `.idea/inspectionProfiles/profiles_settings.xml` | Other | IDE inspection profile. | FLAG | Low — same. |
| 16 | `.idea/misc.xml` | Other | IDE misc settings. | FLAG | Low — same. |
| 17 | `.idea/modules.xml` | Other | IDE modules registry. | FLAG | Low — same. |
| 18 | `.idea/vcs.xml` | Other | IDE VCS mapping. | FLAG | Low — same. |

## 5. Files to delete (default action DELETE — auto-execute in Phase 4)

**Empty.** No file in the repo is unambiguously Category 1 (obviously
abandoned via naming convention or under an `old/`, `backup/`,
`archive/` directory). Everything that's stale is also documented or
referenced somewhere, which puts it in Category 2 (FLAG) or Other
(FLAG). The brief's "conservative defaults" rule applies.

This means Phase 4 cannot run unattended — please mark which of the
rows in §4 you want deleted and I will then `git rm` exactly those.

## 6. Files to flag for your review

All 18 rows in §4 are flagged. Suggested groupings for your decision:

- **High-confidence ready-to-delete group (rows 1, 2, 3).** Three
  CP3-era standalone smoke tests, none invoked by either verifier,
  one of them (row 1) literally cannot run because its compose
  override was deleted before CP3 submission. Removing them does not
  touch the CP3 verifier or the CP4 e2e suite. Delete-if-confirmed.
- **README-coupled deletion (rows 4, 5).** Two test_checkout JSON
  files mentioned only in the README's optional-poke list. Deletion
  requires editing the README sentence in the same commit so the link
  list still parses. Confirm scope (keep / delete-with-README-edit).
- **Course seed material (rows 6, 7, 8).** OpenAPI example templates
  shipped with the course skeleton. Not used by our services but they
  are the starting point a future team would copy from. Recommend
  keeping unless the team has decided to drop them.
- **CP2-era diagrams (rows 9, 10, 11, 12).** Genuinely orphaned, but
  small (each < 200 kB). Brief says "err toward keeping docs". Easy
  to drop, easy to keep; default keep.
- **`.idea/` (rows 13–18).** Six IDE config files. Standard
  convention is to gitignore the entire `.idea/` directory. If you
  agree, the right action is `git rm -r .idea/` and add `.idea/` to
  `.gitignore`. Default keep until I'm asked.

## 7. Files to leave alone (Category 3 + unclassifiable)

- **Category 3 (unreferenced source modules).** None found. Every
  `.py` file in a service tree is either an import target reached
  from `<service>/src/app.py` or it is the entry-point itself.
- **`load_test/results/smoke.csv`.** Not tracked by git, predates this
  audit, not a stale-file matter.
- **`order_executor/tests/test_2pc_crash_recovery.py`.** This file is
  reached by README §B2, summary, plan, and review as the B2 bonus's
  "alternative demonstration" — i.e. the docs explicitly cite it as a
  CP3 deliverable artefact, so it is *not* unreferenced. But see §8
  oddity (1): the test itself depends on the same missing
  `docker-compose.fail-inject.yaml` as `test_2pc_fail_injection.py`,
  so the documentation claim is technically false. The right action
  is documentation reconciliation, not file deletion; the brief
  excludes both touching CP3 work and "fixing bugs you notice", so
  the file stays and the doc-claim discrepancy is logged in §8.

## 8. Oddities surfaced during inspection (no action taken)

1. **`order_executor/tests/test_2pc_crash_recovery.py` documents B2
   alongside the verifier-fail-injection check, but it also requires
   the deleted `docker-compose.fail-inject.yaml`.** README line 61
   ("plus `test_2pc_crash_recovery.py`") and `docs/checkpoint-4-summary.md`
   line 110, plan.md line 332, review.md line 173/231 all imply the
   test is currently runnable. It is not — same root cause as the
   verifier's failing `bonus:participant-failure-recovery`. Logged
   here; no action taken per the brief's "don't fix bugs you notice".
2. **`docs/checkpoint-4-plan.md` lines 166 and 257 reference
   `docs/diagrams/checkpoint-4-architecture.svg`.** That file was
   never created — the implementation pivoted to Mermaid embedded in
   `docs/checkpoint-4-architecture.md`. This is a stale planning
   sentence inside an otherwise current plan; not a file matter.
3. **`load_test/results/smoke.csv` is an untracked working-tree file
   that has been present since before this audit.** Not a stale-file
   issue (it is not tracked by git), and the user explicitly noted
   that this file predated the prior session in the original git
   status.

## 9. Next step (Phase 4 gate)

Phase 4 will not run automatically. The "Files to delete" list in
§5 is intentionally empty.

Reply with the row numbers from §4 (e.g. "delete rows 1, 2, 3" or
"delete the high-confidence group and the .idea/ tree") and I'll
batch-delete with `git rm`, re-run both verifiers after each batch,
and if anything that passed at `2f0f335` now fails I'll
`git restore --staged .` + `git checkout -- .` and report which
deletion caused the failure. Otherwise I commit and push.
