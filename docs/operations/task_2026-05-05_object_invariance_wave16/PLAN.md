# Wave 16 Object-Meaning Invariance: legacy outcome_fact -> operator diagnostics

Status: in progress
Scope: diagnostic/operator-report boundary only; not live unlock, not DB mutation, not report publication.

## Route Evidence

- Root `AGENTS.md`: read from prompt/context.
- Scoped reads: `scripts/AGENTS.md`, `tests/AGENTS.md`, `docs/operations/AGENTS.md`, `zeus-ai-handoff` skill.
- Semantic boot: `python3 scripts/topology_doctor.py --task-boot-profiles` -> `topology check ok`.
- First semantic route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "object-meaning settlement authority cutover Wave16 operator truth-surface diagnostics must not promote legacy outcome_fact row counts as settlement authority or live-readiness truth" --write-intent edit --files scripts/verify_truth_surfaces.py scripts/diagnose_truth_surfaces.py scripts/venus_sensing_report.py tests/test_live_safety_invariants.py docs/operations/task_2026-05-05_object_invariance_wave16/PLAN.md docs/operations/AGENTS.md`
  - Result: `navigation ok: False`; profile `object meaning settlement authority cutover`; admitted this plan, `docs/operations/AGENTS.md`, and `tests/test_live_safety_invariants.py`; rejected active scripts as out of profile.
- Script route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "Wave16 diagnostic script repair: outcome_fact/execution_fact row-count diagnostics must label legacy lifecycle projection as non-authoritative and require settlement-authority provenance before readiness PASS" --intent "add or change script" --task-class diagnostic --write-intent edit --files scripts/verify_truth_surfaces.py scripts/diagnose_truth_surfaces.py scripts/venus_sensing_report.py tests/test_truth_surface_health.py tests/test_market_scanner_provenance.py`
  - Result: `navigation ok: True`; profile `add or change script`; admitted scripts and focused tests.
- Downstream sweep route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "Wave16 downstream contamination sweep only for P4 fact smoke summary outcome_fact legacy authority" --write-intent read_only --files src/state/db.py tests/test_db.py tests/test_runtime_guards.py`
  - Result: `navigation ok: True`; advisory-only read route; high-fanout files needed typed intent before editing.
- P4 smoke-summary route:
  - Command: `python3 scripts/topology_doctor.py --navigation --task "Phase 5 forward substrate producer implementation Wave16 p4 fact smoke summary must label outcome_fact as legacy non-authoritative and not expose legacy pnl as settlement authority" --write-intent edit --files src/state/db.py tests/test_db.py docs/operations/task_2026-05-05_object_invariance_wave16/PLAN.md`
  - Result: `navigation ok: False`; admitted `src/state/db.py` and `tests/test_db.py`; rejected this active plan because the phase-5 profile does not list packet docs.
  - Follow-up command without packet path: `python3 scripts/topology_doctor.py --navigation --task "Phase 5 forward substrate producer implementation Wave16 p4 fact smoke summary must label outcome_fact as legacy non-authoritative and not expose legacy pnl as settlement authority" --write-intent edit --files src/state/db.py tests/test_db.py`
  - Result: `navigation ok: True`; admitted `src/state/db.py` and `tests/test_db.py`; T3, planning-lock required.

Topology compatibility notes recorded for system improvement:
- The semantic object-meaning profile detected the right class but did not admit active diagnostic/runtime-support scripts that preserve the same settlement authority invariant.
- A broad `add or change script` task without typed intent was downgraded to `generic`/advisory-only because high-fanout tests and missing packet paths made profile selection ambiguous.
- Typed `--intent "add or change script"` plus `--task-class diagnostic` admitted the script repair correctly. This is a route usability requirement for future high-level invariance audits.
- The phase-5 forward substrate profile admitted `src/state/db.py`/`tests/test_db.py` but rejected the active packet plan path, so evidence documentation and source admission had to be composed from separate routes.

## Phase 0 Map Delta

Relevant money-path/report segment:

`legacy outcome_fact + execution_fact + verified settlement events -> truth-surface diagnostics -> Venus sensing report / operator readiness interpretation`

Authority surfaces:
- `position_events`/legacy `decision_log` through `query_authoritative_settlement_rows()`: settlement authority when rows are metric-ready, not degraded, and backed by verified settlement truth.
- `execution_fact`: execution lifecycle projection; materializes submitted/fill/cancel state but is not settlement authority.
- `outcome_fact`: legacy lifecycle projection of outcome/PnL; it has no field-level settlement authority, evidence class, or learning eligibility columns.
- `verify_truth_surfaces.py`: diagnostic/readiness gate with exit code semantics for truth-surface checks.
- `diagnose_truth_surfaces.py` and `venus_sensing_report.py`: operator diagnostic/report surfaces; non-promotion but active runtime support.

Canonical hierarchy for this wave:

`verified settlement authority rows` outrank `outcome_fact` row counts. `outcome_fact` may be counted as a legacy projection, but cannot make a diagnostic/readiness surface green as settlement truth.

## Phase 1 Boundary Selection

Candidate boundaries:

| Boundary | Live-money relevance | Material values | Downstream consumers | Stale/legacy bypass | Scoped repair |
|---|---|---|---|---|---|
| `outcome_fact`/`execution_fact` counts -> `verify_truth_surfaces.py` PASS | Can green-light operator truth-surface checks from legacy outcome rows | row counts, terminal execution status | direct script exit code, closeout/readiness evidence | `outcome_fact > 0` meant apparent truth-surface health | yes, typed script route |
| `outcome_fact`/`execution_fact` counts -> `diagnose_truth_surfaces.py` PASS | Can make health JSON look clean while settlement authority is absent | row counts | CLI/operator JSON, Venus layer 1 | only both empty warned | yes |
| raw fact counts -> `venus_sensing_report.py` report | Runtime report can preserve old meaning into operator telemetry | row counts | `state/venus_sensing_report.json`, operator/cron consumers | counts lacked authority/evidence labels | yes |
| raw outcome PnL/counts -> `query_p4_fact_smoke_summary()` | P4 substrate smoke summaries can make legacy PnL look like resolution economics | total/wins/pnl | DB utility callers, tests, future readiness consumers | `outcome.pnl_total` lacked authority labels | yes, admitted via phase-5 route |

Selected boundary: diagnostic fact-table row counts to operator truth-surface/readiness meaning. It is the highest-risk reachable residual from Wave15 because it can preserve the old `outcome_fact` meaning after replay/economics paths were hardened.

## Phase 2 Material Value Lineage

| Value | Real object denoted | Origin | Authority/evidence | Unit/side | Time basis | Transform | Persistence | Consumers | State |
|---|---|---|---|---|---|---|---|---|---|
| `outcome_fact` row count | legacy lifecycle projection rows | `outcome_fact` | legacy, no settlement authority | count | lifecycle projection/write time | diagnostic count only | stdout/report JSON | diagnostics/report | repaired |
| `execution_fact` row count | execution lifecycle projection rows | `execution_fact` | execution persistence projection | count | submit/fill/cancel lifecycle time | diagnostic count | stdout/report JSON | diagnostics/report | preserved with label |
| `terminal_exec_status` count | execution facts with terminal status | `execution_fact` | execution lifecycle projection | count | execution terminal time | terminal materialization count | stdout/report JSON | diagnostics/report | repaired |
| settlement authority ready count | verified settlement-authority rows | `query_authoritative_settlement_rows()` | position events / decision log with verified settlement truth | count | settlement time | authority readiness count | stdout/report JSON | diagnostics/report | repaired |
| settlement learning eligible count | rows with decision snapshot and verified settlement truth | `query_authoritative_settlement_rows()` | settlement authority + decision-time linkage | count | decision + settlement time | learning eligibility count | stdout/report JSON | diagnostics/report | repaired |
| diagnostic status | operator health/readiness state | scripts | derived non-promotion diagnostic | PASS/WARN/FAIL | run time | explicit fail/warn closed gate | stdout/report JSON | operator/cron | repaired |
| `query_p4_fact_smoke_summary().outcome` | legacy outcome fact totals/PnL | `src/state/db.py` | legacy lifecycle projection, no settlement authority | count/USD | outcome_fact write time | smoke-summary projection with authority labels | returned dict | P4 tests/consumers | repaired |
| `query_p4_fact_smoke_summary().settlement_authority` | verified settlement-authority materialization | `src/state/db.py::query_authoritative_settlement_rows()` | position events / decision log with verified settlement truth | count | settlement time | explicit authority summary | returned dict | P4 tests/consumers | repaired |

UNKNOWN: current local/live DB row populations were not mutated or reclassified. This wave repairs readers only.

## Phase 3 Findings

W16-F1 (S1): `verify_truth_surfaces.py::check_7_fact_tables_populated()` returned PASS when `outcome_fact > 0` and `execution_fact > 0`. A legacy lifecycle projection row could become apparent truth-surface readiness at a script exit-code boundary.

W16-F2 (S1): `diagnose_truth_surfaces.py::check_fact_tables()` returned PASS whenever either legacy table had rows. Its structured diagnostic could show health even if no verified settlement-authority row existed.

W16-F3 (S1): `venus_sensing_report.py::_collect_fact_tables()` emitted raw `outcome_fact`/`execution_fact` counts without source authority, evidence class, learning eligibility, or promotion barrier labels. Runtime support consumers could not distinguish legacy lifecycle projection from settlement authority.

W16-F4 (S2, topology compatibility): the semantic settlement authority profile admitted packet/test surfaces but rejected active diagnostic/runtime-support scripts. The actual repair needed typed script routing even though the invariant class was semantic, not merely "script work".

W16-F5 (S1): downstream sweep found `src/state/db.py::query_p4_fact_smoke_summary()` still exposed `outcome.total`, `outcome.wins`, and `outcome.pnl_total` from `outcome_fact` without any authority/evidence labels or settlement-authority companion summary. This could preserve legacy outcome economics into future P4 readiness/report consumers after script-level diagnostics were repaired.

## Phase 4 Repair

Restored invariant: diagnostic and operator report surfaces may count legacy `outcome_fact`, but they must preserve its object meaning as a non-authoritative lifecycle projection. PASS/healthy readiness for settlement outcome evidence requires verified settlement-authority rows, and execution materialization requires terminal execution projection rows.

Code repair:
- Added `build_fact_table_authority_report()` in `verify_truth_surfaces.py` as the shared diagnostic classifier.
- Added explicit authority labels:
  - `legacy_lifecycle_projection_not_settlement_authority`
  - `position_events_or_decision_log_verified_settlement`
  - `execution_lifecycle_projection_not_settlement_authority`
- Changed `verify_truth_surfaces.py` check 7 to FAIL unless settlement authority ready rows and terminal execution rows are present.
- Changed `diagnose_truth_surfaces.py` check 7 to WARN, not PASS, when only legacy `outcome_fact`/execution counts exist.
- Extended `venus_sensing_report.py` fact table report with authority status, learning/promotion ineligibility, settlement authority counts, and blocking reasons while preserving old count keys.
- Extended `query_p4_fact_smoke_summary()` with `outcome.authority_scope`, `outcome.learning_eligible=False`, `outcome.promotion_eligible=False`, `execution.authority_scope`, and a separate `settlement_authority` summary derived from `query_authoritative_settlement_rows()`. Legacy `outcome.pnl_total` remains for smoke/debug compatibility but is no longer unlabeled settlement economics.

Relationship tests:
- `test_truth_surface_fact_table_check_rejects_legacy_outcome_fact_as_authority`
- `test_truth_surface_fact_table_check_passes_with_verified_settlement_authority`
- `test_diagnose_truth_surfaces_warns_on_legacy_outcome_fact_only`
- `test_venus_sensing_report_labels_fact_tables_as_diagnostic_non_authority`
- `test_query_p4_fact_smoke_summary_separates_verified_settlement_authority`

## Phase 5 Verification

Initial checks:
- `python3 -m py_compile scripts/verify_truth_surfaces.py scripts/diagnose_truth_surfaces.py scripts/venus_sensing_report.py tests/test_truth_surface_health.py tests/test_market_scanner_provenance.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_truth_surface_health.py -k 'fact_table'` -> `2 passed, 76 deselected`.
- `pytest -q -p no:cacheprovider tests/test_market_scanner_provenance.py -k 'venus_sensing_report_labels_fact_tables_as_diagnostic_non_authority'` -> `1 passed, 65 deselected`.
- `pytest -q -p no:cacheprovider tests/test_truth_surface_health.py -k 'fact_table or diagnose_truth_surfaces_warns'` -> `3 passed, 75 deselected`.
- `pytest -q -p no:cacheprovider tests/test_market_scanner_provenance.py -k 'venus_sensing_report_labels_fact_tables_as_diagnostic_non_authority or venus_sensing_report_labels_positions_json_as_legacy_telemetry or venus_sensing_report_flags_canonical_empty_legacy_active_conflict'` -> `3 passed, 63 deselected`.
- `python3 -m py_compile src/state/db.py tests/test_db.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_db.py -k 'query_p4_fact_smoke_summary'` -> `3 passed, 59 deselected`.
- `pytest -q -p no:cacheprovider tests/test_db.py -k 'query_authoritative_settlement_rows_requires_verified_settlement_truth'` -> `1 passed, 61 deselected`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave16/PLAN.md` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files ...` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --task-boot-profiles` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --schema` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --naming-conventions` -> `topology check ok`.
- `python3 scripts/digest_profiles_export.py --check` -> `OK: architecture/digest_profiles.py matches YAML`.
- `git diff --check` -> pass.
- Static downstream sweep:
  - `rg "COUNT\\(\\*\\) FROM outcome_fact|outcome_fact=.*execution_fact|pnl_total|wins.*outcome|check_fact_tables|_collect_fact_tables|query_p4_fact_smoke_summary" scripts src tests ...`
  - Remaining source hits are the repaired/labeled `check_fact_tables`, `_collect_fact_tables`, and `query_p4_fact_smoke_summary` paths; test/doc hits assert the new labels. `scripts/backfill_outcome_fact.py` remains a writer/backfill path, not a Wave16 diagnostic consumer, and was not run.
- Global script manifest gate:
  - `python3 scripts/topology_doctor.py --scripts --json` -> failed on pre-existing script manifest/naming issues outside touched Wave16 scripts (`arm_live_mode.sh`, `backfill_hko_xml.py`, weekly observation scripts, etc.). This is recorded as global topology debt, not changed-surface failure.
- Critic review (`019dfb0e-ac09-75c1-82a9-ec9b8bffd2bf`) -> `APPROVE`.
  - Confirmed `outcome_fact` row counts no longer make diagnostics PASS/healthy by themselves.
  - Confirmed compatibility keys remain but now carry explicit non-authority labels plus separate settlement-authority summaries.
  - Confirmed `diagnose_truth_surfaces` import path does not execute `verify_truth_surfaces.py` CLI body.
  - Confirmed `row_factory` restoration around settlement-authority summaries.
  - Critic reran focused tests for truth-surface, Venus report, and P4 smoke summary; all passed.

Pending:
- none for Wave16.
