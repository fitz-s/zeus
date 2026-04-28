# Contamination Remediation Work Log — 2026-04-28

## First-four-step execution

### Scope

Executed only the agreed first four steps:

1. Register remediation packet in operations routing.
2. Add plan evidence and validate planning-lock.
3. Repair stale supervisor contract tests without changing production supervisor contract.
4. Repair PnL harvester test helper source family without changing production harvester code.

### Files intentionally touched

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-28_contamination_remediation/plan.md`
- `docs/operations/task_2026-04-28_contamination_remediation/work_log.md`
- `tests/test_supervisor_contracts.py`
- `tests/test_pnl_flow_and_audit.py`

### Changes made

- Registered `task_2026-04-28_contamination_remediation/` as packet evidence in `docs/operations/AGENTS.md`.
- Added a thin current-state reference identifying the packet as contamination-remediation evidence, not live-deploy authority.
- Added `plan.md` as planning-lock evidence.
- Updated `tests/test_supervisor_contracts.py` so the test law matches current `src/supervisor_api/contracts.py`: valid envs are `live`, `test`, and `unknown_env`; `paper` is rejected.
- Updated the harvester live-path test helper in `tests/test_pnl_flow_and_audit.py` from `source="test_fixture"` to `source="wu_icao_history"`, matching `src/execution/harvester.py::_lookup_settlement_obs` accepted WU source family.

### Verification run

```text
python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

python3 scripts/topology_doctor.py --navigation "Repair stale supervisor API contract tests only..." --files tests/test_supervisor_contracts.py
=> navigation ok true; profile generic

python3 scripts/topology_doctor.py --navigation "Repair PnL harvester test fixture source family only..." --files tests/test_pnl_flow_and_audit.py
=> navigation ok true; profile generic

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_supervisor_contracts.py --no-header
=> 41 passed

.venv/bin/python -m pytest -q -p no:cacheprovider <six targeted harvester PnL tests> --no-header
=> 6 passed

git diff --check -- <first-four scoped files>
=> pass

git diff -- src/supervisor_api/contracts.py
=> empty

python3 scripts/topology_doctor.py --docs --json
=> ok false, 21 pre-existing/unrelated docs issues; no issue path/message referenced task_2026-04-28_contamination_remediation.
```

### Gate status

Implementation must pause here for independent critic review. Remaining implementation batches are blocked until critic verdict is APPROVE or requested revisions are completed and re-reviewed.

## Critic revision — 2026-04-28

Independent critic returned `REVISE` on workflow evidence, not on the code/test patch. Revisions applied:

- Added `work_log.md` to `plan.md` planned changed files.
- Replaced the combined docs+test navigation command in `plan.md` with a split topology strategy: aggregate planning-lock, per-test navigation, and filtered docs check for this packet.
- Documented that combined navigation can over-select an R3 live-readiness profile and is not the gate for mixed packet-evidence + test-only edits.

Re-run gates after revision before requesting critic re-review.

### Revision verification

```text
python3 scripts/topology_doctor.py --planning-lock ... plan.md work_log.md ... --plan-evidence plan.md --json
=> ok true

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_supervisor_contracts.py <six targeted PnL harvester tests> --no-header
=> 47 passed

git diff --check -- <first-four scoped files>
=> pass

split topology navigation after revision
=> supervisor navigation ok true; PnL navigation ok true

python3 scripts/topology_doctor.py --docs --json filtered for task_2026-04-28_contamination_remediation
=> global docs ok false with 21 unrelated issues; contamination_matches=0
```

## Critic revision 2 — 2026-04-28

Independent critic returned `REVISE` again because `tests/test_pnl_flow_and_audit.py` contained pre-existing non-harvester fixture hunks not described by the first-gate evidence. Revisions applied:

- `plan.md` now states that the first gate audits/verifies the pre-existing PnL fixture hunks in the dirty worktree as part of the file-level gate.
- `plan.md` now lists the non-harvester fixture categories: test-only R3 entry gate helper, kwargs-compatible `save_portfolio` monkeypatch, explicit `temperature_metric`, and `OrderResult.command_state`.
- Verification scope now includes `test_inv_control_pause_stops_entries` and `test_inv_strategy_tracker_receives_trades` in addition to the six harvester tests.

### Revision 2 verification

```text
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_pnl_flow_and_audit.py::test_inv_control_pause_stops_entries \
  tests/test_pnl_flow_and_audit.py::test_inv_strategy_tracker_receives_trades --no-header
=> 2 passed

python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence plan.md --json
=> ok true

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_supervisor_contracts.py <eight scoped PnL tests> --no-header
=> 49 passed

git diff --check -- <first-four scoped files>
=> pass
```

## Batch A start — state settlements schema parity

First-four-step gate was approved by critic and verifier. Batch A starts with no additional source edits yet. The existing dirty `src/state/db.py` hunk is being evaluated as schema parity for the existing harvester live write path that inserts `pm_bin_lo`, `pm_bin_hi`, `unit`, and `settlement_source_type`.

Topology/boot results:

```text
python3 scripts/topology_doctor.py --navigation --task "Review existing src/state/db.py settlements schema parity with harvester live write no production DB mutation" --files src/state/db.py
=> navigation ok true; profile r3 collateral ledger implementation

python3 scripts/topology_doctor.py semantic-bootstrap --task-class settlement_semantics --task "Review state settlements schema parity for harvester live settlement write" --files src/state/db.py src/execution/harvester.py --json
=> ok true; source/fatal-misread proof surfaces loaded; graph derived-only warnings noted

python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true
```

### Batch A verification

```text
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_db.py \
  tests/test_harvester_metric_identity.py \
  tests/test_harvester_dr33_live_enablement.py \
  tests/test_settlements_unique_migration.py --no-header
=> 73 passed, 19 skipped

python3 -m py_compile src/state/db.py src/execution/harvester.py
=> pass
```

Batch A source decision before critic: keep `src/state/db.py` schema hunk as nullable fresh-schema and legacy-ALTER parity for the already-existing harvester live `_write_settlement_truth` insert. No production DB was mutated and no harvester production behavior changed.

## Batch B — rebuild settlements source-family/data-version repair

### Topology and semantic boot

```text
python3 scripts/topology_doctor.py --navigation --task "Batch B repair scripts/rebuild_settlements.py settlement repair script source-family data_version and validation; no production DB apply" --files scripts/rebuild_settlements.py src/data/rebuild_validators.py src/contracts/settlement_semantics.py tests/test_rebuild_pipeline.py tests/test_structural_linter.py docs/operations/task_2026-04-28_contamination_remediation/plan.md
=> navigation ok false because including read-only `src/contracts/settlement_semantics.py` selected a profile that forbids editing contracts. No contract file was modified.

python3 scripts/topology_doctor.py --navigation --task "Batch B script work repair rebuild_settlements source-family data_version and validation; no production DB apply" --files scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py tests/test_structural_linter.py docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> navigation ok true; profile generic

python3 scripts/topology_doctor.py --navigation --task "Audit and update rebuild pipeline regression test file for source-family data_version behavior" --files tests/test_rebuild_pipeline.py
=> navigation ok true; profile generic

python3 scripts/topology_doctor.py --navigation --task "Register rebuild pipeline test lifecycle metadata in test topology trusted list" --files architecture/test_topology.yaml
=> navigation ok true; profile r3 live readiness gates implementation

python3 scripts/topology_doctor.py semantic-bootstrap --task-class settlement_semantics --task "Batch B repair rebuild_settlements source-family data_version and settlement validation" --files scripts/rebuild_settlements.py src/data/rebuild_validators.py src/contracts/settlement_semantics.py --json
=> ok true; source roles not interchangeable; HKO caution and graph-derived-only warnings noted
```

### Changes made

- Replaced the hard-coded `HIGH_DATA_VERSION = "wu_icao_history_v1"` with source-family mapping: WU → `wu_icao_history_v1`, HKO → `hko_daily_api_v1`, NOAA/Ogimet → `ogimet_metar_v1`, CWA → fail-closed/no collector.
- Added observation-source-family validation before constructing a VERIFIED settlement repair row.
- Retained the legacy WU fixture alias `wu_icao` while requiring live WU rows to remain in the WU family (`wu_icao_history`).
- Replaced broad per-row `except Exception` swallowing with explicit `SettlementRebuildSkip` reasons so DB/programming errors can surface.
- Added `rows_skipped_by_reason` to the summary for auditability.
- Repair writes now fill `unit`, `settlement_source_type`, source-family `data_version`, and JSON provenance for each written row.
- Added regression coverage for HKO and NOAA data-version/source-family writes and for wrong-source-family rejection.
- Added lifecycle headers to `tests/test_rebuild_pipeline.py` and registered it in `architecture/test_topology.yaml` trusted metadata, scoped to this test-trust update only.

### Verification run before critic

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_rebuild_pipeline.py --no-header
=> 12 passed

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_rebuild_pipeline.py tests/test_structural_linter.py tests/test_settlement_semantics.py --no-header
=> 31 passed, 1 existing SyntaxWarning in tests/test_structural_linter.py

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_authority_gate.py --no-header
=> 20 passed

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_rebuild_pipeline.py tests/test_structural_linter.py tests/test_settlement_semantics.py tests/test_authority_gate.py --no-header
=> 51 passed, 1 existing SyntaxWarning in tests/test_structural_linter.py

python3 -m py_compile scripts/rebuild_settlements.py src/data/rebuild_validators.py src/contracts/settlement_semantics.py
=> pass

python3 scripts/topology_doctor.py --scripts --json filtered for rebuild_settlements.py
=> global ok false from 5 unrelated script issues; rebuild_issues=[]

python3 scripts/topology_doctor.py --tests --json filtered for test_rebuild_pipeline.py
=> global ok false from 2 unrelated untracked test topology issues; rebuild_issues=[]
```

Pending for this batch before moving on: planning-lock, diff hygiene, independent critic, independent verifier.

```text
python3 scripts/topology_doctor.py --planning-lock --changed-files scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

git diff --check -- scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass
```

```text
python3 scripts/topology_doctor.py --planning-lock --changed-files scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true after adding script-manifest-required tests/test_authority_gate.py evidence to plan/work_log

git diff --check -- scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass after evidence update
```

## Batch B correction — Hong Kong has no WU ICAO

User correction accepted: Hong Kong/HKO has no WU ICAO settlement path. The implementation already gated the WU alias behind `city.settlement_source_type == "wu_icao"`, but the evidence now makes this explicit:

- Added script comment: the legacy `wu_icao` alias is only for WU-family cities and must not be inherited by HKO/Hong Kong.
- Strengthened `test_rebuild_settlements_skips_wrong_source_family`: Hong Kong rows with both `wu_icao_history` and legacy alias `wu_icao` are rejected; expected summary is `rows_seen=2`, `rows_written=0`, `rows_skipped=2`, `rows_skipped_by_reason={"source_family_mismatch": 2}`.

This reopens Batch B for repeat gates and critic/verifier after the correction.

### Correction verification

```text
python3 scripts/topology_doctor.py --navigation --task "Batch B correction: Hong Kong has no WU ICAO; ensure rebuild_settlements rejects WU alias for HKO city" --files tests/test_rebuild_pipeline.py docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md scripts/rebuild_settlements.py
=> navigation ok true; profile generic

python3 scripts/topology_doctor.py --planning-lock --changed-files scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_rebuild_pipeline.py tests/test_structural_linter.py tests/test_settlement_semantics.py tests/test_authority_gate.py --no-header
=> 51 passed, 1 existing SyntaxWarning in tests/test_structural_linter.py

python3 -m py_compile scripts/rebuild_settlements.py src/data/rebuild_validators.py src/contracts/settlement_semantics.py
=> pass

git diff --check -- scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass

git diff -- src/contracts/settlement_semantics.py
=> empty
```

## Batch C — sigma floor test MetricIdentity alignment

### Topology

```text
python3 scripts/topology_doctor.py --navigation --task "Batch C audit sigma floor evaluation test fixture metric identity alignment only" --files tests/test_sigma_floor_evaluation.py docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> navigation ok false; task wording selected modify-types/contracts profile. No source type/contract files were modified.

python3 scripts/topology_doctor.py --navigation --task "Batch C audit sigma floor test constructor fixture alignment only; no source type or contract code changes" --files tests/test_sigma_floor_evaluation.py
=> navigation ok true; profile generic

python3 scripts/topology_doctor.py --navigation --task "Batch C packet evidence update only" --files docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> navigation ok true; profile generic

python3 scripts/topology_doctor.py --navigation --task "Register sigma floor test lifecycle metadata in test topology trusted list only" --files architecture/test_topology.yaml
=> navigation ok true; profile r3 live readiness gates implementation
```

### Changes audited/kept

- Added lifecycle header to `tests/test_sigma_floor_evaluation.py`.
- Imported `HIGH_LOCALDAY_MAX`.
- Passed `temperature_metric=HIGH_LOCALDAY_MAX` to the two `Day0Signal(...)` constructors that exercised high-track sigma floor behavior.
- Registered `tests/test_sigma_floor_evaluation.py` in `architecture/test_topology.yaml` trusted test metadata after adding the lifecycle header.

Rationale: `src/signal/day0_signal.py` now rejects `temperature_metric is None` and requires a `MetricIdentity`. The sigma floor test is high-track only, so `HIGH_LOCALDAY_MAX` aligns the stale fixture with current type-seam law without changing production code.

### Batch C verification before critic

```text
python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_sigma_floor_evaluation.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_sigma_floor_evaluation.py --no-header
=> 7 passed

python3 -m py_compile tests/test_sigma_floor_evaluation.py src/signal/day0_signal.py src/types/metric_identity.py
=> pass

python3 scripts/topology_doctor.py --tests --json filtered for test_sigma_floor_evaluation.py
=> global ok false from 2 unrelated untracked test topology issues; sigma_issues=[]

git diff --check -- tests/test_sigma_floor_evaluation.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass
```

## Batch D — riskguard stale-test cluster pre-edit audit

### Topology and reads

```text
python3 scripts/topology_doctor.py --navigation --task "Batch D audit riskguard tests and possible riskguard fail-closed behavior; no production edits before critic" --files tests/test_riskguard.py src/riskguard/riskguard.py docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> navigation ok false; riskguard production edit selected modify-risk/strategy profile and scope_expansion_required. No production riskguard edit made.

cat src/riskguard/AGENTS.md
=> RiskGuard is protective K1; risk levels must alter behavior; computation error -> RED/fail-closed.

python3 scripts/topology_doctor.py --navigation --task "Batch D audit riskguard tests only; no production riskguard edits" --files tests/test_riskguard.py
=> navigation ok true; profile generic
```

### Test audit evidence

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_riskguard.py --no-header
=> 16 failed, 31 passed
```

Failure clusters observed:

- Tests that do not initialize canonical `position_current` fail before their intended assertion because current `tick()` requires canonical portfolio truth and raises on `json_fallback` (`canonical snapshot unavailable: missing_table`).
- One portfolio-truth test expects legacy `working_state_metadata`; current code reports `dual_source_blended` and records consistency-lock source counts.
- Trailing-loss degraded-reference tests expect RED, but current reference law returns `DATA_DEGRADED` for missing/insufficient/inconsistent references and computes loss against stale-but-valid references.

Current authority checked:

- `docs/reference/zeus_risk_strategy_reference.md` §1.3: no valid reference -> `DATA_DEGRADED`; stale valid reference preserves RED but degrades GREEN to DATA_DEGRADED.
- `docs/reference/zeus_risk_strategy_reference.md` §2.2: RiskGuard `tick()` requires canonical `position_current`; non-`canonical_db` policy raises `RuntimeError`.
- `docs/authority/zeus_current_architecture.md` §10.1: broken truth input must not silently downgrade risk.

No `src/riskguard/riskguard.py` edit has been made. Next step is critic review of the test-only remediation plan before edits.

### Batch D edits applied after critic plan approval

Critic returned `APPROVE_PLAN` for test-only edits and explicitly said to stop/re-plan before any production RiskGuard edit. Changes applied:

- Added lifecycle header to `tests/test_riskguard.py`.
- Registered `tests/test_riskguard.py` in `architecture/test_topology.yaml` trusted metadata.
- Added `_init_canonical_portfolio_schema()` test helper to initialize canonical `position_current` for tick tests not focused on missing-canonical behavior; optional `include_risk_actions=False` preserves the missing-risk-actions test.
- Changed the explicit projection-unavailable test to assert `RuntimeError("riskguard requires canonical truth source...")` instead of legacy working-state fallback details.
- Updated stale expectations to current law:
  - canonical portfolio capital source is `dual_source_blended`;
  - insufficient/inconsistent/no trailing-loss reference yields `DATA_DEGRADED`;
  - stale-but-valid reference computes the current loss and reports `stale_reference`;
  - execution/strategy signal yellow tests now expect overall `YELLOW`, not forced `RED`, when no RED contributor exists.
- No `src/riskguard/riskguard.py` edits were made.

### Batch D verification before post-edit critic

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_riskguard.py --no-header
=> 47 passed

python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_riskguard.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

python3 -m py_compile tests/test_riskguard.py src/riskguard/riskguard.py src/riskguard/risk_level.py src/state/portfolio_loader_policy.py
=> pass

python3 scripts/topology_doctor.py --tests --json filtered for test_riskguard.py
=> global ok false from 2 unrelated untracked test topology issues; riskguard_issues=[]

git diff --check -- tests/test_riskguard.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass
```

## First-four gate recovery pass — 2026-04-28 current session

Context reset note: prior Batch A/B/C/D work-log entries are retained as historical packet evidence only. After the workspace unexpectedly lost tracked implementation hunks, this pass treats current `git diff` and current test output as the only implementation truth.

User correction accepted as active constraint: Hong Kong/HKO has no WU ICAO settlement path. Any later Batch B rebuild-settlements work must reject both `wu_icao_history` and legacy `wu_icao` for Hong Kong/HKO; the first-four PnL harvester fixture uses `wu_icao_history` only for NYC, whose `settlement_source_type` is `wu_icao`.

Current first-four edits applied:

- Registered `task_2026-04-28_contamination_remediation/` in `docs/operations/AGENTS.md`.
- Added temporary remediation pointer/no-go language in `docs/operations/current_state.md`.
- Repaired `tests/test_supervisor_contracts.py` as test-only: no `src/supervisor_api/contracts.py` edit, no `paper` env, `unknown_env` remains the accepted sentinel, and `SupervisorCommand` fixtures include timestamp.
- Repaired the scoped `tests/test_pnl_flow_and_audit.py` first-gate hunks:
  - added lifecycle header;
  - added test-local entry-gate opener for strategy-tracker materialization;
  - added explicit high `temperature_metric` to the cycle-runner market fixture;
  - made `save_portfolio` monkeypatches kwargs-compatible;
  - added durable `OrderResult.command_state="FILLED"`;
  - enabled `ZEUS_HARVESTER_LIVE_ENABLED=1` only inside targeted harvester tests;
  - seeded NYC/WU-family source-correct `wu_icao_history` observations;
  - stubs `_write_settlement_truth` in first-gate PnL tests so this gate does not depend on later Batch A settlement-schema parity;
  - updated Gamma settled-market fixtures to resolved UMA payloads (`umaResolutionStatus="resolved"`, `outcomes=["Yes","No"]`, string `outcomePrices`);
  - checked harvester learning assertions against `calibration_pairs_v2` for current high-track learning writes.
- Registered `tests/test_supervisor_contracts.py` and `tests/test_pnl_flow_and_audit.py` in `architecture/test_topology.yaml` trusted test metadata after adding lifecycle headers.

Verification observed in this session:

```text
python3 scripts/topology_doctor.py --navigation --task "Repair stale supervisor API contract tests only; production src/supervisor_api/contracts.py remains unchanged; paper env must stay rejected" --files tests/test_supervisor_contracts.py
=> navigation ok true; profile generic

python3 scripts/topology_doctor.py --navigation --task "Repair PnL harvester test fixture source family and first-four file-level fixture hunks only; production harvester and cycle code unchanged" --files tests/test_pnl_flow_and_audit.py
=> navigation ok true; profile generic

python3 scripts/topology_doctor.py --navigation --task "Register first-four gate test lifecycle metadata for PnL and supervisor contract tests only" --files architecture/test_topology.yaml
=> navigation ok true; profile r3 live readiness gates implementation

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_supervisor_contracts.py --no-header
=> 41 passed

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_pnl_flow_and_audit.py::test_inv_control_pause_stops_entries tests/test_pnl_flow_and_audit.py::test_inv_strategy_tracker_receives_trades tests/test_pnl_flow_and_audit.py::test_inv_harvester_triggers_refit tests/test_pnl_flow_and_audit.py::test_harvester_stage2_preflight_skips_canonical_bootstrap_shape tests/test_pnl_flow_and_audit.py::test_inv_harvester_falls_back_to_open_portfolio_snapshot_when_no_durable_settlement_exists tests/test_pnl_flow_and_audit.py::test_inv_harvester_uses_legacy_decision_log_snapshot_before_open_portfolio tests/test_pnl_flow_and_audit.py::test_inv_harvester_prefers_durable_snapshot_over_open_portfolio tests/test_pnl_flow_and_audit.py::test_inv_harvester_marks_partial_context_resolution --no-header
=> 8 passed
```

Next required before continuing beyond first-four: run aggregate first-gate planning-lock/docs/diff/compile checks, save current diff evidence to disk, then dispatch independent critic + verifier. Do not continue to Batch A/B/C/D implementation unless the gate approves.

### First-four independent gate results

Independent verifier (`019dd3a2-d258-7130-b9db-b906ff8601e4`) returned PASS:

- scoped diff has no production `src/` changes;
- planning-lock ok true;
- targeted first-gate pytest 49 passed;
- py_compile pass;
- git diff --check pass;
- `wu_icao_history` use is limited to NYC/WU-family tests and does not imply Hong Kong/HKO WU support.

Independent critic (`019dd3a2-8623-7580-91c6-f03ae21a867a`) returned APPROVE:

- first-four scope compliance passed;
- supervisor env law passed (`unknown_env` valid, `paper` rejected);
- PnL helper alignment accepted as test-only;
- Hong Kong/HKO no-WU-ICAO constraint recorded for later Batch B;
- test topology updates narrow and justified.

Proceeding to Batch A only after this gate. Because Batch A touches `src/state/db.py` canonical schema, it requires state topology, scoped state guidance, and independent plan review before editing.

## Batch A revised plan/test-first execution — 2026-04-28 current session

Pre-edit critic (`019dd3a6-c358-7353-98f8-cdf72b02b96c`) returned `REVISE_PLAN`, not approval, because the initial plan relied on broad existing tests that could pass without proving the missing-column bug. Required revisions accepted:

1. Add explicit regression coverage for fresh `init_schema()` creating `settlements.pm_bin_lo`, `pm_bin_hi`, `unit`, and `settlement_source_type`.
2. Add explicit legacy-ALTER coverage for a DB that already has `UNIQUE(city, target_date, temperature_metric)` but lacks those four harvester-live columns.
3. Remove the manual ALTER workaround in `tests/test_harvester_metric_identity.py` and assert fresh `init_schema()` supplies the columns.
4. Expand Batch A planning-lock changed files to include touched tests and `architecture/test_topology.yaml`.
5. Keep negative checks: no `src/contracts/settlement_semantics.py` or `src/execution/harvester.py` diff; no production DB mutation; no source/unit/rounding/bin topology edits.

Test-first red evidence before `src/state/db.py` edit:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_settlements_unique_migration.py::test_fresh_db_has_harvester_live_schema_parity_columns tests/test_settlements_unique_migration.py::test_legacy_new_unique_schema_gets_harvester_live_columns_via_alter tests/test_harvester_metric_identity.py::test_fresh_schema_supplies_harvester_live_columns --no-header
=> 3 failed
   - fresh schema missing pm_bin_lo/pm_bin_hi/unit/settlement_source_type
   - legacy post-REOPEN-2 schema missing those columns after init_schema()
   - harvester metric identity fixture no longer manually adds them, so fresh schema assertion fails
```

Implementation applied after red tests:

- `src/state/db.py` fresh `CREATE TABLE settlements` now declares nullable `pm_bin_lo`, `pm_bin_hi`, `unit`, `settlement_source_type`.
- `src/state/db.py` legacy settlements ALTER loop now adds those four nullable columns before INV-14 identity columns.
- Existing REOPEN-2 rebuilt table already had those columns; no behavior change there.
- `tests/test_settlements_unique_migration.py` last-audited date updated and two regression tests added.
- `tests/test_harvester_metric_identity.py` lifecycle reused date updated, manual ALTER workaround removed, fresh-schema assertion added.
- `architecture/test_topology.yaml` trusted-test metadata updated for the two reused Batch A tests; registry count updated to 114.

Immediate post-fix proof:

```text
python3 - <<'PY'
import yaml
with open('architecture/test_topology.yaml') as f:
    data=yaml.safe_load(f)
print(len(data['test_trust_policy']['trusted_tests']))
PY
=> 114

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_settlements_unique_migration.py::test_fresh_db_has_harvester_live_schema_parity_columns tests/test_settlements_unique_migration.py::test_legacy_new_unique_schema_gets_harvester_live_columns_via_alter tests/test_harvester_metric_identity.py::test_fresh_schema_supplies_harvester_live_columns --no-header
=> 3 passed
```

Next: run full Batch A topology/planning/targeted state-harvester test gates, compile, diff-check, and negative production-diff checks before post-edit critic/verifier.

### Batch A full verification before post-edit critic/verifier

```text
python3 scripts/topology_doctor.py --navigation --task "Batch A db schema parity only: add nullable settlement provenance columns already inserted by harvester live write path; no R3 collateral ledger, no settlement semantics behavior" --files src/state/db.py
=> navigation ok true; profile r3 collateral ledger implementation

python3 scripts/topology_doctor.py --navigation --task "Batch A revised test-first schema parity coverage for settlements nullable harvester columns only" --files tests/test_settlements_unique_migration.py tests/test_harvester_metric_identity.py
=> navigation ok true; profile generic

python3 scripts/topology_doctor.py --navigation --task "Register Batch A test lifecycle metadata for settlements migration and harvester metric identity tests only" --files architecture/test_topology.yaml
=> navigation ok false; false-positive profile=modify types or contracts; aggregate planning-lock below is authoritative for changed-file permission.

python3 scripts/topology_doctor.py semantic-bootstrap --task-class settlement_semantics --task "Batch A settlements schema parity for harvester live write columns only; no bin topology/rounding/source changes" --files src/state/db.py src/execution/harvester.py src/contracts/settlement_semantics.py --json
=> ok true; fatal misreads include Hong Kong/HKO caution path. Batch A does not touch source routing.

python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py tests/test_settlements_unique_migration.py tests/test_harvester_metric_identity.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_db.py tests/test_harvester_metric_identity.py tests/test_harvester_dr33_live_enablement.py tests/test_settlements_unique_migration.py --no-header
=> 76 passed, 19 skipped

python3 -m py_compile src/state/db.py src/execution/harvester.py tests/test_settlements_unique_migration.py tests/test_harvester_metric_identity.py
=> pass

git diff --check -- src/state/db.py tests/test_settlements_unique_migration.py tests/test_harvester_metric_identity.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass

git diff -- src/contracts/settlement_semantics.py src/execution/harvester.py
=> empty

python3 scripts/topology_doctor.py --tests --json filtered for tests/test_settlements_unique_migration.py and tests/test_harvester_metric_identity.py
=> global ok false from unrelated existing issues; batch_a_test_issues=0

python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory --changed-files src/state/db.py tests/test_settlements_unique_migration.py tests/test_harvester_metric_identity.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --json
=> ok true with advisory warnings that added packet files are covered by docs registry/current-state companions; first-four already updated docs/operations/AGENTS.md and current_state.md.
```

Batch A ready for post-edit critic/verifier. Do not proceed to Batch B until both pass.

### Batch A independent gate results

Post-edit verifier (`019dd3ac-f94a-7512-b988-06af5c9318bf`) returned PASS for Batch A:

- Batch A production diff is only `src/state/db.py`.
- `src/contracts/settlement_semantics.py` and `src/execution/harvester.py` diffs are empty.
- planning-lock ok true.
- Batch A targeted pytest: `76 passed, 19 skipped`.
- py_compile and scoped diff-check pass.
- in-memory fresh schema probe confirmed all four harvester-live columns.

Post-edit critic (`019dd3ac-ca15-7fb3-b4d6-48f7fd6d0303`) returned APPROVE:

- no hidden schema migration/table rebuild/trigger/UNIQUE/test-masking hazards found;
- fresh schema and legacy ALTER path are covered;
- REOPEN-2 rebuild target already contains the columns;
- UNIQUE remains `UNIQUE(city, target_date, temperature_metric)`;
- settlement semantics, HKO/Hong Kong source routing, and harvester production behavior are untouched.

Proceeding to Batch B only after this gate. Batch B must encode the user correction: Hong Kong/HKO has no WU ICAO; `wu_icao` and `wu_icao_history` may only be accepted for `settlement_source_type == "wu_icao"` cities.

## Batch B implementation + self-verification before independent gate — 2026-04-28 current session

Reviewer correction applied as a hard source-family invariant: Hong Kong/HKO has no WU ICAO path. In `scripts/rebuild_settlements.py`, the legacy `wu_icao` alias is accepted only for cities whose `settlement_source_type == "wu_icao"`; Hong Kong/HKO accepts only `hko_daily_api` and skips both `wu_icao_history` and `wu_icao` as `source_family_mismatch`.

Implementation applied:

- `scripts/rebuild_settlements.py` now resolves each VERIFIED observation row through `config.cities_by_name`, validates source family before settlement conversion, calls `validate_observation_for_settlement`, and then calls `SettlementSemantics.for_city(city).assert_settlement_value(...)`.
- Data-version provenance is selected by settlement source family: `wu_icao_history_v1`, `hko_daily_api_v1`, `ogimet_metar_v1`, or fail-closed CWA placeholder.
- Row-level expected skips are explicit (`unknown_city`, `source_family_mismatch`, `unsupported_source_family`, `invalid_observation`); broad programming/DB exceptions are not swallowed.
- Settlement writes now preserve `unit`, `settlement_source_type`, and provenance JSON containing observation source, source family, and data version.
- `tests/test_rebuild_pipeline.py` gained source-family provenance coverage for Hong Kong/HKO and Istanbul/NOAA, plus a Hong Kong negative regression proving WU aliases write zero settlements.
- `architecture/test_topology.yaml` includes lifecycle trust metadata for the reused rebuild pipeline test.

Verification before independent critic/verifier:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_rebuild_pipeline.py::test_rebuild_settlements_writes_source_family_data_versions tests/test_rebuild_pipeline.py::test_rebuild_settlements_skips_hong_kong_wu_source_aliases --no-header
=> 2 passed

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_rebuild_pipeline.py tests/test_structural_linter.py tests/test_settlement_semantics.py tests/test_authority_gate.py --no-header
=> 51 passed, 1 warning (existing SyntaxWarning in structural-linter fixture parsing)

python3 -m py_compile scripts/rebuild_settlements.py src/data/rebuild_validators.py src/contracts/settlement_semantics.py
=> pass

python3 scripts/topology_doctor.py semantic-bootstrap --task-class settlement_semantics --task "Batch B rebuild_settlements source-family provenance and Hong Kong HKO no-WU-ICAO validation" --files scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py --json
=> ok true; fatal misreads include Hong Kong/HKO explicit caution path

python3 scripts/topology_doctor.py --planning-lock --changed-files scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

python3 scripts/topology_doctor.py --scripts --json
=> global ok false from unrelated unregistered scripts; no rebuild_settlements.py issue

python3 scripts/topology_doctor.py --tests --json
=> global ok false from unrelated existing test_topology_missing entries; no tests/test_rebuild_pipeline.py issue

python3 scripts/topology_doctor.py --fatal-misreads --json
=> ok true

python3 scripts/topology_doctor.py --core-claims --json
=> global ok false from existing locator issues in architecture/core_claims.yaml; no Batch B code/schema edit made there

git diff --check -- scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass

git diff -- src/contracts/settlement_semantics.py src/data/rebuild_validators.py
=> empty
```

Batch B is ready for independent critic + verifier. Do not proceed to Batch C until both pass.

### Batch B independent gate results

Post-edit verifier (`019dd3b6-93e7-7441-8347-8efcc1d20d9f`) returned PASS:

- actual script gates WU aliases behind `settlement_source_type == "wu_icao"`;
- Hong Kong/HKO accepts only `hko_daily_api` and rejects both `wu_icao_history` and `wu_icao`;
- new source-family tests passed (`2 passed`);
- Batch B targeted suite passed (`51 passed, 1 warning`);
- py_compile, planning-lock, protected diff, and diff hygiene passed;
- scripts/tests topology have no Batch-B-specific issue.

Post-edit critic (`019dd3b6-939f-7860-992b-49f6da9bbf90`) returned APPROVE:

- HKO no-WU-ICAO invariant is implemented in code and tests;
- NOAA and CWA branches are source-family bounded;
- high-track identity/data-version/provenance are preserved;
- expected row skips are counted explicitly without broad exception swallowing;
- no protected settlement semantics, validator, or harvester production diff.

Proceeding to Batch C only after this gate.

## Batch C test-only fixture alignment — 2026-04-28 current session

Batch C red evidence before edit:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_sigma_floor_evaluation.py --no-header
=> 2 failed, 5 passed
   - both failures were Day0Signal TypeError: explicit MetricIdentity required for temperature_metric
```

Implementation applied after red test:

- `tests/test_sigma_floor_evaluation.py` gained lifecycle provenance header.
- Imported `HIGH_LOCALDAY_MAX` from `src.types.metric_identity`.
- Added `temperature_metric=HIGH_LOCALDAY_MAX` to the two high-track `Day0Signal` fixture constructors.
- `architecture/test_topology.yaml` trusted-test metadata registered `tests/test_sigma_floor_evaluation.py`; trusted count now 117.
- No production signal/type files edited.

Verification before independent critic/verifier:

```text
python3 scripts/topology_doctor.py --navigation --task "Batch C audit sigma floor test constructor fixture alignment only; no source type or contract code changes" --files tests/test_sigma_floor_evaluation.py
=> navigation ok true

python3 scripts/topology_doctor.py --navigation --task "Register sigma floor test lifecycle metadata in test topology trusted list only" --files architecture/test_topology.yaml
=> navigation ok true

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_sigma_floor_evaluation.py --no-header
=> 7 passed

python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_sigma_floor_evaluation.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

python3 -m py_compile tests/test_sigma_floor_evaluation.py src/signal/day0_signal.py src/types/metric_identity.py
=> pass

python3 scripts/topology_doctor.py --tests --json filtered for tests/test_sigma_floor_evaluation.py
=> global ok false from unrelated existing issues; batch_c_test_issues=0

git diff --check -- tests/test_sigma_floor_evaluation.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass

git diff -- src/signal/day0_signal.py src/types/metric_identity.py
=> empty
```

Batch C is ready for independent critic + verifier. Do not proceed to Batch D until both pass.

### Batch C independent gate results

Post-edit critic (`019dd3bb-3f64-7a71-859c-1ba2fcf68e42`) returned APPROVE:

- Batch C scoped diff is test-only;
- protected production diff is empty for `src/signal/day0_signal.py` and `src/types/metric_identity.py`;
- `HIGH_LOCALDAY_MAX` is the correct high-track fixture identity;
- both `Day0Signal` constructors now pass explicit `temperature_metric=HIGH_LOCALDAY_MAX`;
- pytest, py_compile, planning-lock, diff-check, and scoped test-topology checks passed.

Post-edit verifier (`019dd3bb-3fa1-7e22-909e-d7ad916ad45f`) returned PASS:

- lifecycle header and `HIGH_LOCALDAY_MAX` import exist;
- exactly two `Day0Signal(...)` call sites exist and both include the explicit metric;
- `tests/test_sigma_floor_evaluation.py` passed (`7 passed`);
- planning-lock ok true;
- protected production diffs empty;
- no `tests/test_sigma_floor_evaluation.py` topology issue.

Proceeding to Batch D only after this gate. Batch D remains test-only unless a separate critic approves any production riskguard edit.

## Batch D pre-edit critic + test-only implementation — 2026-04-28 current session

Pre-edit riskguard run reproduced the handoff failures:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_riskguard.py --no-header
=> 16 failed, 31 passed
```

Pre-edit critic (`019dd3bf-3d29-74e2-8aac-6694d5e39af3`) returned APPROVE for the test-only plan, with the explicit constraint that no `src/riskguard/*` production files be changed without separate approval. The critic verified current RiskGuard authority:

- `tick()` requires canonical `position_current`; missing table resolves to `json_fallback` and raises `RuntimeError`.
- Empty canonical `position_current` after `init_schema()` is a healthy canonical DB state for tests whose focus is not missing truth.
- Missing/invalid trailing-loss references are `DATA_DEGRADED`, not RED.
- Execution decay / edge compression / tracker-unavailable contributors are YELLOW and still change behavior by blocking new entries/recommending controls.

Implementation applied after critic approval:

- `tests/test_riskguard.py` gained lifecycle provenance header.
- Added `_init_empty_canonical_portfolio_schema(...)` test helper to satisfy the canonical DB precondition without seeding positions.
- The explicit projection-unavailable test now asserts the fail-closed `RuntimeError` for `json_fallback` instead of expecting legacy working-state fallback details.
- Tests focused on settlement/tracker/strategy behavior now initialize an empty canonical portfolio schema before `tick()`.
- The durable-risk-actions-missing-table test initializes canonical schema and then drops `risk_actions`, preserving the intended missing-optional-table condition.
- Stale RiskGuard expectations were aligned to current law: `dual_source_blended`, `DATA_DEGRADED` for missing/invalid trailing-loss references, stale-valid trailing-loss reference behavior, and YELLOW for execution/strategy signal contributors.
- `architecture/test_topology.yaml` registered `tests/test_riskguard.py`; trusted count now 118.
- No production RiskGuard files were edited.

Verification before independent post-edit critic/verifier:

```text
python3 scripts/topology_doctor.py --navigation --task "Batch D audit riskguard tests only; no production riskguard edits" --files tests/test_riskguard.py
=> navigation ok true

python3 scripts/topology_doctor.py --navigation --task "Register riskguard test lifecycle metadata in test topology trusted list only" --files architecture/test_topology.yaml
=> navigation ok true

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_riskguard.py --no-header
=> 47 passed

python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_riskguard.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

python3 -m py_compile tests/test_riskguard.py src/riskguard/riskguard.py src/riskguard/risk_level.py src/state/portfolio_loader_policy.py
=> pass

python3 scripts/topology_doctor.py --tests --json filtered for tests/test_riskguard.py
=> global ok false from unrelated existing issues; batch_d_test_issues=0

git diff --check -- tests/test_riskguard.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass

git diff -- src/riskguard/riskguard.py src/riskguard/risk_level.py src/state/portfolio_loader_policy.py
=> empty
```

Batch D is ready for independent post-edit critic + verifier.

### Batch D independent gate results

Post-edit verifier (`019dd3c5-2af4-71d2-8e79-9cae806fbef1`) returned PASS:

- `tests/test_riskguard.py` has lifecycle header and helper;
- projection-unavailable test asserts `RuntimeError` with `json_fallback`;
- no protected production diff in `src/riskguard/riskguard.py`, `src/riskguard/risk_level.py`, or `src/state/portfolio_loader_policy.py`;
- `tests/test_riskguard.py` passed (`47 passed`);
- py_compile, planning-lock, diff-check passed;
- test topology has no `tests/test_riskguard.py` issue; trusted metadata count is coherent at 118.

Post-edit critic (`019dd3c5-2abe-7e72-b528-0012031d3ae1`) returned APPROVE:

- changes match the approved test-only plan;
- missing canonical projection still fails closed via `json_fallback` RuntimeError;
- degraded trailing-loss and stale-reference expectations match current law;
- contributor-level execution/strategy signal expectations remain YELLOW and behavior-changing;
- no Batch D blockers found.

## Remaining handoff open-cluster audit — 2026-04-28 current session

After Batch D gate closure, remaining non-TIGGE/non-history clusters were audited read-only:

```text
python3 scripts/topology_doctor.py --navigation --task "Audit runtime guard tests only; read-only failure localization; do not modify production" --files tests/test_runtime_guards.py
=> navigation ok true

python3 scripts/topology_doctor.py --navigation --task "Audit tick_size tests only for finite price validation ordering; read-only" --files tests/test_tick_size.py
=> navigation ok true

python3 scripts/topology_doctor.py --navigation --task "Audit execution executor finite price validation before cutover gate; read-only before critic plan" --files src/execution/executor.py
=> navigation ok true; profile r3 heartbeat supervisor implementation

python3 scripts/topology_doctor.py --navigation --task "Audit topology doctor test failures only; read-only" --files tests/test_topology_doctor.py
=> navigation ok true; profile modify topology kernel
```

Observed current failures:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tick_size.py --no-header
=> 3 failed, 20 passed
   - all failures are TestExitPathNaNGuard: CutoverPending is raised before malformed_limit_price rejection.

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header
=> 20 failed, 99 passed
   - failures require separate audit; many appear fixture/metric-identity/runtime-gate related.

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_topology_doctor.py --no-header
=> 2 failed, 226 passed, 16 deselected
   - docs-mode synthetic git-visible tests are not producing expected docs issues; history_lore remains excluded.
```

Batch E will address the narrow tick-size/executor finite-price guard ordering first, because it is small, safety-preserving, and already described in the handoff. Production executor edit requires pre-edit critic approval before any code change.

### Batch E pre-edit critic revision request

Pre-edit critic (`019dd3cb-1e7e-71d3-8fe9-9d316c44e7b1`) returned `REVISE_PLAN`, not approval:

- Direction is authority-backed and likely safe.
- Missing proof: valid exits must still hit CutoverGuard before risk allocator, command persistence, heartbeat/ws/collateral gates, or SDK/client contact.
- Required revision: add a narrow `tests/test_tick_size.py` regression asserting a finite/valid exit intent raises `CutoverPending` under the current non-live cutover state.

No production edit made before satisfying this critic revision.

### Subagent context gate update — 2026-04-28

Operator clarified that all critic/verifier subagents must read `AGENTS.md` and the important checking context, not just the narrow diff. Applied as a hard rule for remaining gates:

- every critic/verifier prompt must explicitly require reading root `AGENTS.md`;
- require the relevant scoped `AGENTS.md` for touched modules/tests/docs;
- require current handoff, plan, work_log, evidence artifact, and relevant authority/reference files;
- if a critic/verifier verdict does not state enough context coverage, rerun/ratify before proceeding.

Current Batch E pre-edit approval will be re-ratified under this stricter context rule before editing `src/execution/executor.py`.

### Batch E context-complete pre-edit critic approval

After the operator clarified subagent context requirements, Batch E was re-ratified by context-complete pre-edit critic (`019dd3d1-15bf-7070-83d2-ad5ea41a8cac`). The critic explicitly read root `AGENTS.md`, `src/execution/AGENTS.md`, `tests/AGENTS.md`, `docs/operations/AGENTS.md`, the contamination handoff, `plan.md`, `work_log.md`, `docs/reference/modules/execution.md`, `src/execution/executor.py`, `tests/test_tick_size.py`, `architecture/test_topology.yaml`, and `docs/operations/current_state.md`.

Verdict: APPROVE.

Rationale:

- revised Batch E includes the required safety-half proof: finite/valid exit intent still raises `CutoverPending` before side effects;
- pre-edit tick-size run is exactly `3 failed, 21 passed`, where the 3 failures are malformed NaN/+inf/-inf exits being blocked by cutover before local malformed-price rejection;
- proposed move keeps CutoverGuard before risk allocator, command persistence, heartbeat/client gates, and SDK contact;
- no AGENTS/reference violation found.

## Batch E implementation + self-verification before independent gate — 2026-04-28 current session

Implementation applied after context-complete pre-edit critic approval:

- `src/execution/executor.py`: moved `_assert_cutover_allows_submit(IntentKind.EXIT)` from the top of `execute_exit_order()` to immediately after pure local validation of `limit_price`, rounded `shares`, and `token_id`, and before `_assert_risk_allocator_allows_exit_submit()`, command persistence, heartbeat/ws/collateral gates, or SDK contact.
- `tests/test_tick_size.py`: updated lifecycle reused date and added `test_valid_exit_still_hits_cutover_guard_before_side_effects` to prove finite/valid exit intents still hit CutoverGuard under non-live cutover state.
- `architecture/test_topology.yaml`: updated `tests/test_tick_size.py` trusted metadata to `last_used: 2026-04-28`.

Verification before independent post-edit critic/verifier:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tick_size.py --no-header
=> 24 passed

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tick_size.py tests/test_executor.py tests/test_execution_price.py tests/test_unknown_side_effect.py --no-header
=> 68 passed, 1 skipped, 1 xfailed, 1 warning (existing PolymarketClient compatibility deprecation)

python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/executor.py tests/test_tick_size.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

python3 -m py_compile src/execution/executor.py tests/test_tick_size.py src/contracts/tick_size.py src/contracts/execution_price.py
=> pass

python3 scripts/topology_doctor.py --tests --json filtered for tests/test_tick_size.py
=> global ok false from unrelated existing issues; batch_e_test_issues=0

python3 scripts/topology_doctor.py --map-maintenance --changed-files src/execution/executor.py tests/test_tick_size.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --map-maintenance-mode advisory --json
=> ok true with existing packet-file advisory warnings already handled by docs/operations/AGENTS.md and current_state.md updates from first gate.

git diff --check -- src/execution/executor.py tests/test_tick_size.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass
```

Batch E is ready for context-complete independent post-edit critic + verifier.

### Batch E independent gate results

Context-complete post-edit verifier (`019dd3d4-a4cb-7c31-81d7-405ffcfe4e85`) returned PASS after reading root/scoped AGENTS, current_state, contamination handoff, plan/work_log/evidence, execution reference, code/tests, and test topology:

- local price/share/token validation now occurs before CutoverGuard;
- CutoverGuard remains before risk allocator, command persistence, heartbeat/ws/collateral gates, client construction, and SDK contact;
- tick-size tests passed (`24 passed`);
- Batch E suite passed (`68 passed, 1 skipped, 1 xfailed, 1 warning`);
- py_compile, planning-lock, diff-check passed;
- no `tests/test_tick_size.py` topology issue.

Context-complete post-edit critic (`019dd3d4-a489-7cc2-903d-99ea038f371a`) returned APPROVE after reading the required AGENTS/reference/handoff context:

- malformed NaN/Inf exits reject before live-money gates;
- valid finite exit regression preserves CutoverGuard safety half;
- test topology metadata is current;
- no Batch E issues found.

## Batch F planning — topology_doctor docs-mode synthetic visible-path regression

Operator protocol update applied: all critic/verifier subagents must explicitly read root `AGENTS.md`, relevant scoped `AGENTS.md`, current handoff/plan/work_log/evidence/current_state, and touched code/tests/reference context before verdict.

Read-only evidence before implementation:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_topology_doctor.py --no-header
=> 2 failed, 226 passed, 16 deselected
   failing: docs-mode synthetic git-visible path tests only.

python3 scripts/topology_doctor.py --navigation --task "Batch F topology doctor docs-mode synthetic git-visible path regression; no history_lore remediation" --files scripts/topology_doctor_docs_checks.py tests/test_topology_doctor.py docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> ok false; profile=modify topology kernel; out_of_scope scripts/topology_doctor_docs_checks.py plus packet plan/work_log.

python3 scripts/topology_doctor.py --planning-lock --changed-files scripts/topology_doctor_docs_checks.py architecture/topology.yaml tests/test_topology_doctor.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true
```

No code implementation performed yet. Dispatching context-complete pre-edit critic before touching topology kernel/helper files.

### Batch F pre-edit critic revision response

Context-complete pre-edit critic (`019dd3db-4ffa-72d0-9a7d-24af443df0e2`) returned `REVISE_PLAN`, not approval.

Required revisions applied to `plan.md` before any implementation:

- docs gate revised from global-green to baseline/filtered comparison; current `/tmp/zeus_batch_f_docs_baseline.json` contains 21 unrelated pre-existing docs issues;
- topology admission step narrowed to only `scripts/topology_doctor_docs_checks.py` under the existing `modify topology kernel` profile; no wildcard broadening;
- implementation scope narrowed to `check_hidden_docs()` only; `check_docs_registry()` excluded unless a future batch adds its own regression/risk note;
- history-lore exclusion reaffirmed.

No Batch F source/topology implementation has been performed yet. Re-dispatching context-complete pre-edit critic for approval.

### Batch F second pre-edit critic revision response

Second context-complete pre-edit critic (`019dd3e1-755a-77d3-a187-ddd84705dae6`) returned `REVISE_PLAN`.

Additional required revisions applied to `plan.md` before implementation:

- added explicit post-edit navigation proof excluding packet plan/work_log and requiring `scripts/topology_doctor_docs_checks.py`, `architecture/topology.yaml`, `tests/test_topology_doctor.py`, and `architecture/test_topology.yaml` to be admitted with `ok true`;
- added the `modify topology kernel` digest/admission regression tests: `tests/test_digest_admission_policy.py`, `tests/test_digest_profile_matching.py`, and `tests/test_digest_regression_false_positive.py`;
- added `tests/test_topology_doctor.py -k 'navigation or digest or admission'` gate;
- clarified docs baseline gate as filtered-only, failing on new Batch-F-attributable docs issues while reporting but not remediating unrelated co-tenant docs issues.

No Batch F source/topology implementation has been performed yet. Dispatching third context-complete pre-edit critic for approval.

## Batch F implementation + self-verification before independent gate — 2026-04-28 current session

Implementation applied after third context-complete pre-edit critic approval:

- `scripts/topology_doctor_docs_checks.py`: updated lifecycle reuse metadata and changed `check_hidden_docs()` to treat `api._git_visible_files()` as the visibility source for docs policy checks without requiring `(api.ROOT / rel).is_file()` first. This is limited to `check_hidden_docs()`; `check_docs_registry()` remains unchanged.
- `architecture/topology.yaml`: admitted exactly `scripts/topology_doctor_docs_checks.py` in the `modify topology kernel` profile allowed/downstream lists. No wildcard or unrelated helper admission.
- `tests/test_topology_doctor.py`: refreshed lifecycle reuse metadata only; no weakening of the two failing docs-mode tests.
- `architecture/test_topology.yaml`: refreshed `tests/test_topology_doctor.py` trusted-test last_used date.

Verification before independent post-edit critic/verifier:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_topology_doctor.py::test_docs_mode_rejects_unregistered_visible_subtree tests/test_topology_doctor.py::test_docs_mode_rejects_non_md_artifact_outside_artifact_subroot tests/test_topology_doctor.py::test_docs_mode_allows_registered_reports_json tests/test_topology_doctor.py::test_docs_mode_excluded_roots_drive_space_path_exemption --no-header
=> 4 passed

python3 scripts/topology_doctor.py --navigation --task "Batch F modify topology kernel docs-check helper admitted narrowly; no history_lore remediation" --files scripts/topology_doctor_docs_checks.py architecture/topology.yaml tests/test_topology_doctor.py architecture/test_topology.yaml
=> navigation ok true; profile=modify topology kernel

python3 -m py_compile scripts/topology_doctor.py scripts/topology_doctor_docs_checks.py
=> pass

python3 scripts/topology_doctor.py --planning-lock --changed-files scripts/topology_doctor_docs_checks.py architecture/topology.yaml tests/test_topology_doctor.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_admission_policy.py tests/test_digest_profile_matching.py tests/test_digest_regression_false_positive.py --no-header
=> 45 passed

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_topology_doctor.py -k 'navigation or digest or admission' --no-header
=> 25 passed, 219 deselected

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_topology_doctor.py --no-header
=> 228 passed, 16 deselected

python3 - <<'PY'
from scripts import topology_doctor
issues = topology_doctor._check_schema(topology_doctor.load_topology(), topology_doctor.load_schema())
if issues:
    print(topology_doctor.format_issues(issues))
    raise SystemExit(1)
print('schema check passed: no topology schema issues')
PY
=> schema check passed: no topology schema issues

python3 scripts/topology_doctor.py --docs --json filtered against /tmp/zeus_batch_f_docs_baseline.json
=> before=21, after=22, new_batch_f_docs_issues=0; one new unrelated issue: docs_registry_unclassified_doc docs/operations/edge_observation/AGENTS.md

python3 scripts/topology_doctor.py --scripts --json filtered for scripts/topology_doctor_docs_checks.py
=> global ok false from unrelated untracked scripts; topology_doctor_docs_checks_issues=0

python3 scripts/topology_doctor.py --tests --json filtered for tests/test_topology_doctor.py
=> global ok false from unrelated co-tenant tests; test_topology_doctor_issues=0

python3 scripts/topology_doctor.py --map-maintenance --changed-files scripts/topology_doctor_docs_checks.py architecture/topology.yaml tests/test_topology_doctor.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --map-maintenance-mode advisory --json
=> ok true with existing packet-file companion warnings for plan.md/work_log.md already handled by first gate packet registration/current_state pointer.

git diff --check -- scripts/topology_doctor_docs_checks.py architecture/topology.yaml tests/test_topology_doctor.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass
```

Note: `python3 scripts/topology_doctor.py --schema` is a stale/nonexistent CLI flag in the current executable, so Batch F used the existing `_check_schema(load_topology(), load_schema())` checker directly rather than broadening into CLI repair.

Batch F is ready for context-complete independent post-edit critic + verifier.

### Batch F independent gate results

Context-complete post-edit verifier (`019dd3ee-96c9-7c02-8f0b-082e1560bf89`) returned PASS after reading root/scoped AGENTS, reference/module context, current_state, handoff, plan/work_log, Batch F evidence, touched code/tests/topology manifests, and confirming `architecture/history_lore.yaml` was untouched.

Verifier findings:

- `check_hidden_docs()` now trusts `_git_visible_files()` directly for docs policy evaluation; old physical `Path.is_file()` gate is gone from that path.
- `check_docs_registry()` was not changed.
- `architecture/topology.yaml` admits exactly `scripts/topology_doctor_docs_checks.py`; no wildcard broadening.
- `architecture/history_lore.yaml` is untouched by Batch F.
- Reran/checked targeted docs-mode tests, digest admission/profile/false-positive tests, full `tests/test_topology_doctor.py`, py_compile, direct schema check, navigation, planning-lock, and diff-check; all passed for Batch F.

Context-complete post-edit critic (`019dd3ee-2892-74d3-8121-41a699efb92b`) returned APPROVE after reading the required root/scoped AGENTS, reference/module context, handoff, plan/work_log/evidence, and touched code/tests/topology manifests.

Critic findings:

- implemented diff matches the approved minimal scope;
- topology admission invariants preserved; no generic fallback/no-echo/forbidden-wins/profile-admission weakening;
- trusting Git-visible docs paths in `check_hidden_docs()` is safe for this batch and fail-closed for visible docs entries;
- direct schema check is sufficient for this scoped batch because CLI `--schema` is stale/unavailable and CLI repair is out of scope;
- no blocker before moving to `runtime_guards` audit/planning.

## Batch G planning — runtime guard fixture alignment

Read-only runtime guard audit after Batch F:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header
=> 20 failed, 99 passed

python3 scripts/topology_doctor.py --navigation --task "Batch G audit runtime guard tests only; no production changes before critic" --files tests/test_runtime_guards.py
=> navigation ok true; profile=generic

python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true
```

Planned Batch G is test-only. It will update stale `tests/test_runtime_guards.py` fixtures to current contracts (entry gates, explicit metric identity, Day0Router, ENS snapshot no-fake valid_time, live-safe boot guard, collateral fail-closed, Day0 canonical event ordering) and register the test lifecycle metadata. No production `src/**` behavior edit is planned.

No Batch G implementation has been performed yet. Dispatching context-complete pre-edit critic before modifying `tests/test_runtime_guards.py`.

### Batch G pre-edit critic revision response

Context-complete pre-edit critic (`019dd3f9-a75e-7872-9653-4a3a27def3d8`) returned `REVISE_PLAN`, not approval.

Required revisions applied to `plan.md` before implementation:

- split item 16: Batch G may only align the monitoring telemetry test to current canonical-entry-baseline behavior; the legacy DAY0-before-exit backfill ambiguity is deferred to a separate production-source Batch H/audit;
- entry-gate helper must be targeted, never autouse;
- updated fixtures should prefer `env="test"` unless explicitly testing a legacy/paper seam;
- added `src/control/control_plane.py` to py_compile and expected-empty-diff gates to protect `LIVE_SAFE_STRATEGIES`.

No Batch G implementation has been performed yet. Re-dispatching context-complete pre-edit critic for approval.

### Batch G context-complete pre-edit approval

Second context-complete pre-edit critic (`019dd3fd-f2df-7ec3-97ec-85ccfe38a2ba`) returned APPROVE after the required context reads, including root/scoped AGENTS, current handoff/plan/work_log/current_state, `tests/test_runtime_guards.py`, `architecture/test_topology.yaml`, and relevant runtime source surfaces.

Critic-approved constraints for implementation:

- test-only fixture alignment; protected production source diff must remain empty;
- targeted entry-gate helper only, not autouse;
- prefer `env="test"` in updated fixtures;
- `LIVE_SAFE_STRATEGIES`/`src/control/control_plane.py` protected by py_compile and empty-diff gates;
- legacy DAY0-before-exit entry-backfill ambiguity split to Batch H, not silently claimed fixed by Batch G.

### Batch G implementation + self-verification before independent gate — 2026-04-28 current session

Implementation applied after context-complete pre-edit critic approval:

- `tests/test_runtime_guards.py`: added trusted-test lifecycle header; updated stale runtime fixtures to current entry-gate, metric identity, Day0Router, ENS snapshot clock, live-safe boot, collateral, and canonical-entry-baseline contracts; renamed the monitor telemetry-chain test to make its canonical-entry-baseline scope explicit.
- `architecture/test_topology.yaml`: registered `tests/test_runtime_guards.py` as trusted with `created/last_used: 2026-04-28`.
- `docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_g_current_diff_2026-04-28.md`: captured Batch G diff notes and verification evidence.

Verification before independent post-edit critic/verifier:

```text
python3 -m py_compile tests/test_runtime_guards.py src/engine/cycle_runner.py src/engine/cycle_runtime.py src/engine/evaluator.py src/execution/exit_lifecycle.py src/execution/collateral.py src/main.py src/control/control_plane.py
=> pass

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header
=> 119 passed

python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

python3 scripts/topology_doctor.py --navigation --task "Audit and reuse tests/test_runtime_guards.py as trusted current-law test evidence; update lifecycle header and architecture/test_topology only; no source behavior changes" --files tests/test_runtime_guards.py architecture/test_topology.yaml
=> navigation ok false; profile=r3 live readiness gates implementation; direct blocker says tests/test_runtime_guards.py out_of_scope. This is recorded as topology admission false-positive/known limitation for this test-current-law reuse batch; planning-lock passed and no production source edit was made.

python3 scripts/topology_doctor.py --tests --json filtered for tests/test_runtime_guards.py
=> global ok false from unrelated/co-tenant missing topology entries; runtime_guards_issues=[]; global_issue_count=5

git diff -- src/engine/cycle_runner.py src/engine/cycle_runtime.py src/engine/evaluator.py src/execution/exit_lifecycle.py src/execution/collateral.py src/main.py src/control/control_plane.py src/supervisor_api/contracts.py | wc -c
=> 0

git diff --check -- tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> pass
```

Batch G is ready for context-complete independent post-edit critic + verifier. Batch H remains the separate production-source audit for legacy positions whose canonical history starts at `DAY0_WINDOW_ENTERED` and may skip entry-event backfill.

### Batch G independent gate results

Context-complete post-edit verifier (`019dd411-3911-7843-a575-607c78797367`) returned PASS after reading root/scoped AGENTS, current_state, contamination handoff, plan/work_log, Batch G evidence, touched test/topology files, and the protected production source set.

Verifier evidence:

- `python3 -m py_compile tests/test_runtime_guards.py src/engine/cycle_runner.py src/engine/cycle_runtime.py src/engine/evaluator.py src/execution/exit_lifecycle.py src/execution/collateral.py src/main.py src/control/control_plane.py` exited 0.
- `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header` => `119 passed in 4.03s`.
- Batch G planning-lock => `ok true`.
- filtered `topology_doctor --tests --json` => no `tests/test_runtime_guards.py` issue; global red remains unrelated/co-tenant.
- protected production source diff => empty.
- Batch G `git diff --check` => pass.

Context-complete post-edit critic (`019dd411-38d8-7163-bf13-36051a012b5b`) returned APPROVE after reading the required AGENTS/current packet/source/test context.

Critic findings:

- Batch G is safe to approve as test-only runtime guard fixture alignment.
- Protected production sources are unchanged for both staged and unstaged protected-source diffs.
- `_allow_entry_gates_for_runtime_test()` is targeted and used only to reach discovery-path assertions, not to weaken gate-block tests.
- `src/supervisor_api/contracts.py` did not receive `paper`; `_VALID_ENVS` remains `("live", "test", "unknown_env")`.
- Updated fixtures use explicit `temperature_metric`; rate-limited discovery fixture uses `env="test"`.
- The monitor telemetry test is explicitly canonical-entry-baseline-only and Batch H remains the separate legacy backfill audit.
- Topology navigation false-positive is documented rather than silently ignored.

Batch G is closed for implementation purposes. Do not start production Batch H without source topology, scoped AGENTS/reference reads, regression-first plan, and context-complete pre-edit critic approval.

## Batch H planning — legacy Day0-only canonical history entry-backfill audit

Batch H opened from the Batch G split-out. Read-only audit found the current bug shape without modifying repo files: a position with only `DAY0_WINDOW_ENTERED` canonical history exits through `_dual_write_canonical_economic_close_if_available()` and receives only `EXIT_ORDER_FILLED`, because the current helper treats any canonical event as sufficient history.

Read-only gates/context:

```text
python3 scripts/topology_doctor.py --navigation --task "Batch H production-source audit for legacy positions whose canonical history starts at DAY0_WINDOW_ENTERED and may skip entry-event backfill before EXIT_ORDER_FILLED; regression first" --files src/execution/exit_lifecycle.py src/engine/cycle_runtime.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> navigation ok false; profile=r3 live readiness gates implementation; scope_expansion_required. Treating this as stop-and-plan signal.

python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/exit_lifecycle.py src/engine/cycle_runtime.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

python3 scripts/topology_doctor.py semantic-bootstrap --task-class day0_monitoring --task "Batch H production-source audit for legacy positions whose canonical history starts at DAY0_WINDOW_ENTERED and may skip entry-event backfill before EXIT_ORDER_FILLED" --files src/execution/exit_lifecycle.py tests/test_runtime_guards.py --json
=> ok true; current fact surfaces fresh enough; graph stale/unavailable and derived-only.

Throwaway reproduction result:
=> [(1, 'DAY0_WINDOW_ENTERED', 'repro'), (2, 'EXIT_ORDER_FILLED', 'src.execution.exit_lifecycle')]
```

Required reads completed before critic dispatch include root `AGENTS.md`, `workspace_map.md`, `tests/AGENTS.md`, `src/AGENTS.md`, `src/engine/AGENTS.md`, `src/execution/AGENTS.md`, `src/state/AGENTS.md`, `docs/operations/AGENTS.md`, `architecture/AGENTS.md`, `docs/reference/modules/execution.md`, `docs/reference/modules/engine.md`, `docs/reference/zeus_domain_model.md`, `docs/authority/zeus_current_architecture.md`, `docs/operations/current_source_validity.md`, `docs/operations/current_data_state.md`, `architecture/source_rationale.yaml`, `architecture/city_truth_contract.yaml`, and `architecture/fatal_misreads.yaml` targeted excerpts.

No Batch H implementation has been performed yet. Dispatching context-complete pre-edit critic before editing tests or source.

## Batch H0 planning — topology admission for Batch H

Batch H pre-edit critic (`019dd41b-e5d4-7b21-b078-ce1b73c70e27`) returned BLOCK, not approval, because topology navigation for `src/execution/exit_lifecycle.py` returned `scope_expansion_required`. Per contamination handoff §7, production edits are prohibited under generic/advisory/scope-expansion topology; planning-lock does not override navigation.

Critic-required plan changes:

1. Resolve topology admission before editing `src/execution/exit_lifecycle.py`.
2. Remove or audit/register `tests/test_exit_authority.py` gate; Batch H0 plan removes it from Batch H gates rather than widening test scope.
3. Tighten Batch H regression assertions to include existing Day0 non-mutation, sequence/idempotency uniqueness, backfill ordering, reason sentinel, and partial-entry-history no-duplication.

No Batch H production implementation has occurred. Opening Batch H0 as a narrow topology/governance batch to add exact admission for the Batch H production-source scope.

### Batch H0 pre-edit critic revision response

Context-complete pre-edit critic (`019dd423-499c-7d82-b8b1-3ee514fa0d6c`) returned `REVISE_PLAN`, not approval.

Plan revisions applied before any H0 topology/test implementation:

- removed untrusted `tests/test_exit_authority.py` from Batch H gates; Batch H gates now use `tests/test_runtime_guards.py`, `tests/test_entry_exit_symmetry.py`, and `tests/test_day0_exit_gate.py` for the broader exit/lifecycle check;
- narrowed planned Batch H profile packet-doc admission from whole-folder wildcard to exact `plan.md`, `work_log.md`, and tightly scoped `evidence/critic-harness/batch_h*.md` outputs;
- explicitly kept `src/engine/lifecycle_events.py`, `src/state/ledger.py`, and `src/engine/cycle_runtime.py` as downstream/context-only, not admitted;
- strengthened Batch H regression requirements to cover existing Day0 non-mutation, sequence/event/idempotency uniqueness, missing-entry append ordering, reason sentinel, and partial-entry-history no-duplication;
- strengthened H0 regression/gates to include near-miss routing, downstream-only/forbidden path negative admission, no fallback to R3 live-readiness or broad R3 M4/M5, direct topology schema check, and `tests/test_topology_doctor.py -k 'navigation or digest or admission'`.

No H0 implementation has been performed yet. Re-dispatching context-complete pre-edit critic for approval.

### Batch H0 second pre-edit critic revision response

Second context-complete H0 pre-edit critic (`019dd428-5978-73e1-8797-c121c5701419`) returned `REVISE_PLAN`, not approval.

Additional plan revisions applied before any H0 topology/test implementation:

- added the digest-profile YAML→Python mirror contract to H0 scope;
- added required sequencing: first admit `architecture/digest_profiles.py` and `tests/test_digest_profiles_equivalence.py` under `modify topology kernel`, then regenerate the generated mirror via `scripts/digest_profiles_export.py` after adding the new profile;
- added `architecture/digest_profiles.py` and `tests/test_digest_profiles_equivalence.py` to H0 touched files, planning-lock, py_compile, and diff-check gates;
- added `python3 scripts/digest_profiles_export.py --check` and `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_profiles_equivalence.py --no-header` gates;
- `tests/test_digest_profiles_equivalence.py` already has lifecycle header; H0 will register/update it in `architecture/test_topology.yaml` if reused.

No H0 implementation has been performed yet. Re-dispatching context-complete pre-edit critic for approval.

### Batch H0 third plan revision — H0a/H0b split

After applying the digest-profile mirror-contract revisions, a full H0 navigation including `architecture/digest_profiles.py` and `tests/test_digest_profiles_equivalence.py` still returns `scope_expansion_required`, as expected before the companion-admission edit. The plan now explicitly splits H0:

- H0a: edit only `architecture/topology.yaml` to admit `architecture/digest_profiles.py` and `tests/test_digest_profiles_equivalence.py` as generated/relationship companions under `modify topology kernel`; do not regenerate the mirror yet.
- H0b: after H0a navigation admits those companions, add the exact Batch H profile/regression tests and regenerate `architecture/digest_profiles.py` using `scripts/digest_profiles_export.py`.

No H0a/H0b implementation has been performed yet. Re-dispatching context-complete critic for the revised sequencing.

### Batch H0a pre-edit critic revision response

Narrow H0a critic (`019dd440-db7a-7273-abeb-1090222137df`) returned `REVISE_PLAN`, not approval.

Plan revision applied before implementation:

- H0a now includes immediate regeneration of `architecture/digest_profiles.py` via `python3 scripts/digest_profiles_export.py` after editing `architecture/topology.yaml`, because any `digest_profiles` YAML edit otherwise violates the YAML→Python mirror contract.
- H0a planning-lock/diff-check/gates now include `architecture/digest_profiles.py`, `scripts/digest_profiles_export.py --check`, and `tests/test_digest_profiles_equivalence.py`.
- H0a still must not add the Batch H production-scope profile, touch production source, flip the mirror truth source, or edit `scripts/digest_profiles_export.py`.

No H0a implementation has been performed yet. Re-dispatching narrow context-complete H0a critic for approval.

### Batch H0a second pre-edit critic revision response

Narrow H0a critic (`019dd444-fd68-7360-a62f-beb5afd9b4d9`) returned `REVISE_PLAN`, not approval.

Plan revision applied before implementation:

- H0a now includes `architecture/test_topology.yaml` in touched files/gates and explicitly registers/reuses `tests/test_digest_profiles_equivalence.py` before relying on it as a gate;
- H0a adds post-edit navigation proof including `architecture/topology.yaml`, `architecture/digest_profiles.py`, `tests/test_digest_profiles_equivalence.py`, and `architecture/test_topology.yaml`;
- H0a adds filtered tests-topology proof that `tests/test_digest_profiles_equivalence.py` has zero topology issues (global may remain red from unrelated/co-tenant tests).

No H0a implementation has been performed yet. Re-dispatching narrow context-complete H0a critic for approval.

### Batch H0a pre-edit critic approval and implementation

Narrow H0a context-complete critic (`019dd448-94fb-7771-8261-22d0b4ef53f1`) returned APPROVE for the companion-admission-only step with these constraints: add only `architecture/digest_profiles.py` and `tests/test_digest_profiles_equivalence.py` as `modify topology kernel` companions, register/reuse the equivalence test in `architecture/test_topology.yaml`, regenerate `architecture/digest_profiles.py` only via `python3 scripts/digest_profiles_export.py`, and do not add the Batch H production-source profile or edit production source.

Implementation completed inside that approved H0a envelope:

- updated `architecture/topology.yaml` under `modify topology kernel` to admit the generated digest-profile mirror and equivalence relationship test as companions;
- registered/reused `tests/test_digest_profiles_equivalence.py` in `architecture/test_topology.yaml` trusted metadata and `core_law_antibody` category;
- regenerated `architecture/digest_profiles.py` with `python3 scripts/digest_profiles_export.py` (no hand edit);
- preserved the already-present Batch F admission of `scripts/topology_doctor_docs_checks.py` while regenerating the mirror;
- did not edit Batch H production source, settlement semantics, supervisor contracts, history lore, current-fact files, or live/prod artifacts.

Self-verification evidence is captured at `docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h0a_current_diff_2026-04-28.md`:

- H0a navigation: `navigation ok: True`, profile `modify topology kernel`;
- planning lock: `{ "ok": true, "issues": [] }`;
- `python3 scripts/digest_profiles_export.py --check`: mirror matches YAML;
- `tests/test_digest_profiles_equivalence.py`: 4 passed;
- digest admission/profile/regression suite: 45 passed;
- direct topology schema check: zero issues;
- py_compile for topology doctor, equivalence test, and generated mirror: passed;
- filtered `topology_doctor --tests --json`: global pre-existing issue count 4, `tests/test_digest_profiles_equivalence.py` issue count 0;
- `git diff --check` over H0a files/packet docs: passed;
- protected Batch H production-source diff byte count: 0.

Dispatching context-complete post-edit critic and verifier before H0b. Per operator instruction, both prompts require reading root/scoped `AGENTS.md` plus packet, evidence, current-state, relevant topology/code/test context, and both verdicts must list the contexts they read.

### Batch H0a post-edit critic/verifier gate

Context-complete post-edit verifier (`019dd456-6257-74a1-9ed6-090aaf8238e1`) returned PASS and explicitly listed required contexts read, including root `AGENTS.md`, `workspace_map.md`, the workflow bridge runbook, scoped `architecture/AGENTS.md`, `tests/AGENTS.md`, `docs/operations/AGENTS.md`, current state, contamination handoff, plan/work log, H0a evidence, and topology/test implementation surfaces. Verifier reproduced the core H0a evidence: navigation OK, planning-lock OK, digest mirror check OK, equivalence test 4 passed, digest suite 45 passed, py_compile OK, protected production/exporter diff 0, and no filtered topology issue for `tests/test_digest_profiles_equivalence.py`.

Context-complete post-edit critic (`019dd456-61f4-7ea1-b6ce-0fe1e1e880fd`) returned APPROVE and explicitly listed required contexts read, including root/scoped `AGENTS.md` files and the H0a evidence plus Batch F evidence. Critic found H0a inside the approved companion-admission scope, confirmed the Batch F docs-check carryover was not introduced by H0a, confirmed the YAML/generated mirror relation, confirmed no Batch H production profile was added, confirmed test-topology registration, and confirmed no production/runtime/settlement/supervisor/HK source semantics changed.

H0a is closed for implementation. H0b may proceed only through its own context-complete pre-edit critic gate before editing the exact Batch H digest profile/tests.

### Batch H0b pre-edit critic gate

Context-complete H0b pre-edit critic (`019dd45b-99d6-7363-8566-c4588cb4fdc9`) returned APPROVE. The critic explicitly listed root/scoped `AGENTS.md` reads, the Zeus handoff workflow, workflow bridge, current state, contamination handoff, plan/work log, H0a evidence, topology/test surfaces, and digest resolver context.

Key critic findings:

- H0b is authorized after H0a because the H0a post-edit verifier PASS and critic APPROVE are recorded and H0b topology-kernel navigation/planning-lock pass.
- H0b remains governance/topology/test admission only and does not authorize `src/execution/exit_lifecycle.py` edits yet.
- Planned over-admission controls are sufficient: exact legacy Day0-only canonical-history wording, exact source/test/packet files, `batch_h*.md` evidence only, downstream files as context-only, and explicit forbidden surfaces.
- Planned regressions are sufficient in shape: exact-route admission, near-miss rejection, downstream-only out-of-scope behavior, forbidden-file blocking, and no fallback to R3 G1/M4/M5 broad profiles.
- Mirror contract is covered: YAML edit first, exporter regeneration, `--check`, and equivalence test.

Non-blocking execution constraint from critic: do not add `file_patterns` or broad weak terms that let `src/execution/exit_lifecycle.py` file evidence alone select the new profile. H0b implementation will rely on strong phrases only.

### Batch H0b implementation and self-verification

Implemented H0b inside the pre-edit critic-approved topology/test-only envelope:

- added one exact digest profile, `batch h legacy day0 canonical history backfill remediation`, to `architecture/topology.yaml`;
- used strong Batch-H/legacy-Day0/canonical-history phrases only and no `file_patterns`, preserving the critic constraint that `src/execution/exit_lifecycle.py` file evidence alone must not select the profile;
- limited allowed files to `src/execution/exit_lifecycle.py`, `tests/test_runtime_guards.py`, `architecture/test_topology.yaml`, exact packet `plan.md`/`work_log.md`, and `evidence/critic-harness/batch_h*.md` outputs;
- kept `src/engine/lifecycle_events.py`, `src/state/ledger.py`, `src/engine/cycle_runtime.py`, `tests/test_entry_exit_symmetry.py`, and `tests/test_day0_exit_gate.py` downstream/context-only;
- explicitly forbade settlement semantics, supervisor env grammar, history lore, current-fact/source/data/calibration surfaces, prod state artifacts, archive/runtime scratch, and rebuild settlement script scope;
- added `tests/test_digest_profile_matching.py` regressions for exact routing/admission, file-evidence-only near miss, downstream-only out-of-scope behavior, and forbidden-surface blocking;
- updated `architecture/test_topology.yaml` last-used metadata for `tests/test_digest_profile_matching.py`;
- regenerated `architecture/digest_profiles.py` with `python3 scripts/digest_profiles_export.py`.

Self-verification gates passed:

- H0b topology-kernel navigation OK under `modify topology kernel`;
- planning-lock OK for H0b topology/test/doc surfaces;
- new exact profile regression passed;
- digest admission/profile/regression suite: 49 passed;
- digest mirror check OK and equivalence test 4 passed;
- `tests/test_topology_doctor.py -k 'navigation or digest or admission'`: 25 passed, 219 deselected;
- direct topology schema check: zero issues;
- Batch H production-scope navigation now OK under the new Batch H profile for `src/execution/exit_lifecycle.py`, `tests/test_runtime_guards.py`, `architecture/test_topology.yaml`, packet `plan.md`, and packet `work_log.md`;
- negative navigation against downstream/forbidden files exits non-zero and reports `navigation_admission_blocked` for forbidden surfaces; the digest regression also proves downstream files are context-only/out-of-scope;
- py_compile passed for topology doctor, profile matching tests, equivalence tests, and generated mirror;
- filtered test-topology check reports zero issues for `tests/test_digest_profile_matching.py` and `tests/test_digest_profiles_equivalence.py` while global pre-existing issues remain unrelated;
- protected production/exporter/settlement/supervisor diff byte count remains 0.

Detailed evidence is being captured at `docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h0b_current_diff_2026-04-28.md`. Dispatching context-complete post-edit critic and verifier before any Batch H source/test implementation.

### Batch H0b post-edit critic/verifier gate

Context-complete H0b verifier (`019dd466-9cc4-75d2-8fdc-504a8f09d4d7`) returned PASS and explicitly listed required root/scoped `AGENTS.md` contexts plus packet/evidence/topology/test surfaces. Verifier confirmed the exact profile id, narrow typed match policy with no `file_patterns`, mirror equivalence, digest suite 49 passed, equivalence suite 4 passed, topology-doctor subset 25 passed, exact Batch H admission OK, downstream-only scope-expansion behavior, forbidden-surface blocking, file-only near miss not selecting the Batch H profile, schema/py_compile/diff-check clean, touched tests topology clean, and protected production/exporter/settlement/supervisor diff byte count 0.

Context-complete H0b critic (`019dd466-9ca0-78f1-9ce9-a3ed9d0aee04`) returned APPROVE. Critic explicitly listed required root/scoped `AGENTS.md` contexts and found H0b narrowly scoped to topology/test admission only, with no `file_patterns`, no file-evidence-only Batch H selection, bounded allowed/downstream/forbidden surfaces, generated mirror equivalence, and no targeted production-source/settlement/supervisor/HK-source accidental change. Approval does not cover unrelated dirty workspace files or later Batch H production implementation.

H0b is closed for implementation. Batch H production-source implementation may now proceed through the newly admitted profile, but only within the Batch H allowed files and with tests-first regression coverage.

### Batch H pre-edit critic gate

Context-complete Batch H production-source pre-edit critic (`019dd46b-b675-7c03-b989-97f8081fd8b0`) returned APPROVE. Critic explicitly listed root/scoped `AGENTS.md` reads, current source/data state, packet plan/work log, H0a/H0b evidence, module references, source rationale, lifecycle/event/ledger/db/projection/portfolio code, and relevant tests.

Key findings:

- Batch H topology navigation and planning-lock now pass under the new profile.
- The bug is real and current: a temp-DB reproduction with only `DAY0_WINDOW_ENTERED` followed by exit writes only `EXIT_ORDER_FILLED`, proving missing legacy entry lineage.
- Planned source approach is safe inside lifecycle law: event-type-aware entry-history inspection in `src/execution/exit_lifecycle.py`, reuse `build_entry_canonical_write()`, append only missing entry event types, and avoid engine/state-builder edits.
- Appending missing entry events after existing `DAY0_WINDOW_ENTERED` is acceptable and preferred because the event spine is append-only; renumbering/mutating Day0 would violate stronger law. Chronology is mitigated by historical `occurred_at`, backfill `source_module`, and `decision_evidence_reason="backfill_legacy_position"`.
- Regression requirements are sufficient and current-law aligned.
- Implementation caution: use real event names `POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, and `ENTRY_ORDER_FILLED`; do not introduce `ENTRY_ORDER_PLACED`.

Proceeding tests-first in `tests/test_runtime_guards.py`, then source fix in `src/execution/exit_lifecycle.py` only.

### Batch H implementation and self-verification

Implemented Batch H after context-complete pre-edit critic approval:

- added tests-first regressions in `tests/test_runtime_guards.py` for a Day0-only canonical history and a partial-entry-history canonical history;
- confirmed the new regressions failed before the source fix (`FF`: Day0-only produced only sequences `[1, 2]`; partial-entry produced only `[1, 2, 3, 4]`);
- updated `src/execution/exit_lifecycle.py` so economic-close dual-write inspects existing canonical entry event types rather than treating any canonical event as complete history;
- reused `build_entry_canonical_write(..., decision_evidence_reason="backfill_legacy_position")`, filtered to missing real event types (`POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, `ENTRY_ORDER_FILLED`), and resequenced those backfill events after the current max sequence without mutating existing rows;
- kept `src/engine/lifecycle_events.py`, `src/state/ledger.py`, `src/engine/cycle_runtime.py`, settlement semantics, supervisor contracts, source/data/calibration/TIGGE/current-fact/history_lore surfaces, and production state artifacts untouched for this Batch H implementation.

Self-verification results:

- new Batch H regressions: 2 passed;
- canonical-entry baseline + new regressions: 3 passed;
- full `tests/test_runtime_guards.py`: 121 passed;
- `tests/test_runtime_guards.py tests/test_entry_exit_symmetry.py tests/test_day0_exit_gate.py`: 146 passed;
- `tests/test_decision_evidence_entry_emission.py tests/test_exit_evidence_audit.py`: 23 passed;
- `python3 -m py_compile src/execution/exit_lifecycle.py tests/test_runtime_guards.py`: passed;
- Batch H topology navigation: `navigation ok: True`, profile `batch h legacy day0 canonical history backfill remediation`;
- Batch H planning-lock: `{ "ok": true, "issues": [] }`;
- semantic bootstrap for `day0_monitoring`: `ok true`; source/data current-fact surfaces fresh; Code Review Graph remains stale/derived-only and was not used as semantic authority;
- filtered `topology_doctor --tests --json`: zero issues for `tests/test_runtime_guards.py` while global pre-existing issue count remains 4;
- `git diff --check` over Batch H files/packet docs: passed.

Detailed evidence is being captured at `docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h_current_diff_2026-04-28.md`. Dispatching context-complete post-edit critic and verifier before declaring Batch H closed or moving to any next remediation item.


### Batch H post-edit critic/verifier gate — first pass and profile-law erratum

Context-complete Batch H verifier (`019dd47b-f2db-7372-918c-1de9580b7696`) returned `PASS_WITH_NOTES` after reading required root/scoped `AGENTS.md`, packet/state/evidence, module references, source/state code, and relevant tests. Verifier reran the required Batch H gates: new regressions (2 passed), full `tests/test_runtime_guards.py` (121 passed), runtime/entry-exit/day0 combined suite (146 passed), decision evidence + exit audit (23 passed), py_compile, topology navigation, planning-lock, semantic bootstrap, filtered topology-test issue check, diff-check, protected downstream/source diff byte count 0, and HK/WU grep over touched Batch H code/tests. Verifier found no functional/code blockers but correctly noted formal closeout still required critic approval.

Context-complete Batch H critic (`019dd47b-f298-7be1-9ab5-d7856b3cd11a`) returned `REQUEST_CHANGES`. The source implementation itself passed the critic's rerun tests and append-only review, but critic found a blocker in the machine-readable H0b profile law: `architecture/topology.yaml` and generated `architecture/digest_profiles.py` still named invented `ENTRY_ORDER_PLACED`. That violates the real canonical event contract (`POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, `ENTRY_ORDER_FILLED`) and the pre-edit critic caution. Per the critic verdict, no next handoff item may start until this is corrected and re-reviewed.

Erratum fix implemented immediately within the topology-kernel/profile scope:

- changed the Batch H profile `required_law` in `architecture/topology.yaml` to name only `POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, and `ENTRY_ORDER_FILLED`;
- regenerated `architecture/digest_profiles.py` via `python3 scripts/digest_profiles_export.py`;
- added `tests/test_digest_profile_matching.py::test_batch_h_profile_law_names_real_canonical_entry_events_only` so the machine-readable law cannot reintroduce `ENTRY_ORDER_PLACED`;
- did not change Batch H runtime source semantics, protected downstream source, settlement semantics, supervisor contracts, current-fact surfaces, history lore, production state artifacts, or Hong Kong source assumptions.

Erratum re-verification passed:

- topology-kernel navigation for architecture/test profile fix: OK;
- Batch H packet-doc/evidence navigation: OK;
- planning-lock for topology/test/packet/evidence surfaces: OK;
- `python3 scripts/digest_profiles_export.py --check`: OK;
- Batch H digest-profile focused tests including the new law regression: 5 passed;
- full `tests/test_digest_profile_matching.py`: 30 passed;
- `tests/test_digest_profiles_equivalence.py`: 4 passed;
- `tests/test_topology_doctor.py -k 'navigation or digest or admission'`: 25 passed, 219 deselected;
- direct topology schema check: 0 issues;
- exact Batch H production-scope navigation: OK;
- forbidden/downstream negative navigation: non-zero/blocked as expected;
- py_compile for topology surfaces: passed;
- `rg ENTRY_ORDER_PLACED architecture/topology.yaml architecture/digest_profiles.py`: no matches;
- Batch H regressions: 2 passed; canonical-entry baseline + new regressions: 3 passed;
- full `tests/test_runtime_guards.py`: 121 passed;
- `tests/test_runtime_guards.py tests/test_entry_exit_symmetry.py tests/test_day0_exit_gate.py`: 146 passed;
- `tests/test_decision_evidence_entry_emission.py tests/test_exit_evidence_audit.py`: 23 passed;
- py_compile for `src/execution/exit_lifecycle.py` and `tests/test_runtime_guards.py`: passed;
- semantic-bootstrap day0_monitoring: OK with fresh current source/data surfaces and stale graph treated as derived-only;
- filtered `topology_doctor --tests --json`: global unrelated issue count 5, zero issues for `tests/test_runtime_guards.py`;
- `git diff --check` over changed topology/source/test/packet/evidence surfaces: passed;
- protected downstream/source diff byte count for lifecycle builder/ledger/cycle_runtime/supervisor/settlement/projection surfaces: 0;
- HK/WU grep matched only the standing stop-condition wording in topology/mirror (`Hong Kong has no WU ICAO`), not a WU alias/source assertion.

Dispatching context-complete re-review critic/verifier after evidence updates. Batch H remains not closed until both re-review verdicts pass.


### Batch H re-review closure

Context-complete re-review verifier (`019dd487-6f5b-70f2-a09c-f8239bc14078`) returned `PASS_WITH_NOTES` after reading required root/scoped `AGENTS.md`, current packet/source/data/evidence, topology/profile/test surfaces, execution/engine/state module references, and Batch H source/tests. Verifier reran or checked: no `ENTRY_ORDER_PLACED` in topology YAML/generated mirror, digest mirror check OK, direct topology schema issue count 0, Batch H navigation OK, Batch H touched-test topology issues 0, focused Batch H profile tests 5 passed, full profile matching 30 passed, mirror equivalence 4 passed, topology-doctor subset 25 passed, Batch H regressions 2 passed, full runtime guards 121 passed, runtime/entry-exit/day0 146 passed, decision evidence + exit audit 23 passed, py_compile, diff-check, and protected downstream/source diff byte count 0. Verifier found no Batch H-specific blocker and stated Batch H can close after critic approval.

Context-complete re-review critic (`019dd487-6f1c-7902-b369-374170ac8837`) returned `APPROVE_WITH_NOTES` after reading required root/scoped `AGENTS.md`, workflow bridge/methodology, packet/source/data/evidence, module/reference/current architecture/context manifests, topology/profile/test surfaces, and Batch H source/tests. Critic reran and trusted the same key gates, confirmed `ENTRY_ORDER_PLACED` is absent from the machine-readable profile law, confirmed the law names only `POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, and `ENTRY_ORDER_FILLED`, confirmed generated mirror equivalence, confirmed downstream-only/forbidden navigation behavior, confirmed Batch H runtime append-only semantics and regressions, and found no blocking issues.

Batch H is closed for implementation. The closure does not cover unrelated/co-tenant dirty work. The standing Hong Kong correction remains preserved: no WU ICAO/alias assumption was introduced; the only Hong Kong wording in Batch H/H0b surfaces is the stop-condition/guardrail that Hong Kong has no WU ICAO.


### Packet closeout preparation

After Batch H re-review closure, the original handoff's non-TIGGE/non-history open clusters are all addressed and independently gated:

- §5.1 `tests/test_supervisor_contracts.py`: handled in the first-four gate; production `src/supervisor_api/contracts.py` remained unchanged and `paper` remains invalid.
- §5.2 `tests/test_riskguard.py`: handled in Batch D as test-only stale-risk-law remediation; no production riskguard edit.
- §5.3 `tests/test_runtime_guards.py`: handled in Batch G (test fixture alignment) and Batch H (the production lifecycle-backfill bug split out by Batch G critic).
- §5.4 `tests/test_tick_size.py` / `src/execution/executor.py`: handled in Batch E with finite exit-price validation before cutover side effects plus safety-half regression.
- §5.5 `tests/test_topology_doctor.py` docs-mode failures: handled in Batch F for the non-history synthetic visible-path failure; `architecture/history_lore.yaml` remained out of scope.
- §5.6 `tests/test_structural_linter.py`: covered by Batch B rebuild-settlements verification; no separate implementation required.

Preparing packet-level closeout verification and a final context-complete critic/verifier pass before freezing any next packet, per the current Zeus P3 loop directive.


### Packet closeout targeted + full-suite verification

Packet-level closeout verification is captured at `evidence/critic-harness/packet_closeout_current_diff_2026-04-28.md`. Results:

- planning-lock over scoped remediation surfaces: `ok true`;
- digest profile mirror check: OK;
- direct topology schema check: 0 issues;
- py_compile over changed Python/script/test surfaces: passed;
- targeted aggregate suites passed: first-four supervisor/PnL 97 passed/5 skipped; Batch A 76 passed/19 skipped; Batch B 51 passed/1 warning; Batch C 7 passed; Batch D 47 passed; Batch E 68 passed/1 skipped/1 xfailed/1 warning; Batch F/H0 topology suites 262 passed/16 deselected; Batch G/H runtime/lifecycle/evidence suites 169 passed;
- filtered topology lanes remain globally red from unrelated issues, but touched/remediation issue counts are zero (`--tests` touched_issue_count 0; `--docs` contamination_packet_issue_count 0; `--scripts` rebuild_issue_count 0);
- scoped diff-check passed; protected forbidden-surface diff byte count 0;
- clean full-suite rerun passed: `3484 passed, 107 skipped, 16 deselected, 1 xfailed, 1 xpassed, 31 warnings, 7 subtests passed`, `full_pytest_exit_status=0`;
- live-readiness informational gate remains expected operator-gated FAIL at G1-02 only (`passed_gates=16/17`, `live_deploy_authorized=false`), so this packet does not authorize live deployment.

Dispatching final packet-level context-complete critic/verifier before marking the packet closed/frozen.


### Final packet-level critic/verifier closeout

Final packet-level verifier (`019dd49a-5660-7ac2-b1da-634658bf3441`) returned `PASS_WITH_NOTES` after reading required root/scoped `AGENTS.md`, workflow bridge, operations current state/source/data, the original contamination handoff, plan/work log, all batch evidence including packet closeout evidence, relevant module references, topology/profile/test manifests, and representative touched source/tests. Verifier reran/checked diff hygiene, absence of `ENTRY_ORDER_PLACED` in topology/mirror, digest mirror equivalence, topology schema, supervisor `paper` env rejection/current env set, protected forbidden-surface diff byte count 0, live readiness (expected G1-02 operator-gated fail only), and a representative 462-test subset. Verifier found no packet-specific blockers and stated the packet can be marked closed/frozen after critic approval.

Final packet-level critic (`019dd49a-55fd-7f33-9cfb-2b2f4f440123`) returned `APPROVE_WITH_NOTES` after reading required authority/orchestration contexts, scoped AGENTS, packet/state/evidence surfaces, module references, topology/profile/test manifests, and representative touched source/tests. Critic found the closeout evidence truthful and sufficiently spot-checked, confirmed all handoff §5.1-§5.6 clusters are implemented/gated or explicitly out of scope, confirmed no forbidden semantic expansion, confirmed Batch H erratum fixed, confirmed live readiness remains blocked only by operator-gated G1-02, and emphasized that unrelated/co-tenant dirty work must not be frozen with this packet.

Packet status: closed/frozen for the initial Codex drift handoff remediation scope. Closure excludes unrelated/co-tenant dirty work, does not approve live deployment, does not approve production DB mutation, does not approve TIGGE/data-readiness or `architecture/history_lore.yaml` remediation, and does not approve unrelated attribution/edge artifacts. The standing Hong Kong correction remains preserved: Hong Kong has no WU ICAO.
