# Contamination Remediation Implementation Plan — 2026-04-28

Status: closed/frozen for the initial handoff remediation packet; final packet-level verifier PASS_WITH_NOTES and critic APPROVE_WITH_NOTES recorded in `work_log.md`. Closure excludes unrelated/co-tenant dirty work and does not authorize live deployment, production DB mutation, TIGGE/data-readiness, or history-lore remediation.
Branch: `plan-pre5`.

## Objective

Convert the Codex drift handoff into a controlled implementation lane, then execute only the first-four-step gate before an independent critic/verifier gate.

## Authority / routing basis

- Root `AGENTS.md` money-path and topology rules.
- `docs/operations/AGENTS.md` packet routing rules.
- `docs/operations/current_state.md` live pointer and R3 no-go freeze.
- `docs/operations/task_2026-04-28_contamination_remediation/CODEX_DRIFT_SELF_AUDIT_HANDOFF_2026-04-28.md` as packet evidence, not authority.
- Current source/test contracts in `src/supervisor_api/contracts.py`, `src/execution/harvester.py`, `src/state/portfolio.py`, and touched tests.

## In-scope first-four steps

1. Register this packet in `docs/operations/AGENTS.md` and reference it from `docs/operations/current_state.md` so docs topology can route it.
2. Use this file as `--plan-evidence` for planning-lock checks.
3. Repair stale supervisor contract tests without modifying `src/supervisor_api/contracts.py` or reintroducing `paper` as a valid supervisor env.
4. Repair the PnL harvester test helper so its mocked observation source matches the current accepted harvester source family, and audit/verify the pre-existing `tests/test_pnl_flow_and_audit.py` fixture hunks already present in the dirty worktree for this file-level gate (entry-gate helper, `save_portfolio` kwargs-compatible monkeypatch, explicit `temperature_metric`, and `OrderResult.command_state`).

## Not now

- No TIGGE training/data-readiness work.
- No `architecture/history_lore.yaml` remediation.
- No settlement bin topology changes.
- No production DB mutation or live venue side effects.
- No live deployment/cutover authorization.
- No remaining implementation batches until independent critic review passes.

## Planned changed files for this first gate

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-28_contamination_remediation/plan.md`
- `docs/operations/task_2026-04-28_contamination_remediation/work_log.md`
- `architecture/test_topology.yaml`
- `tests/test_supervisor_contracts.py`
- `tests/test_pnl_flow_and_audit.py`

## Verification for first gate

```bash
python3 scripts/topology_doctor.py --planning-lock --changed-files \
  docs/operations/AGENTS.md \
  docs/operations/current_state.md \
  docs/operations/task_2026-04-28_contamination_remediation/plan.md \
  docs/operations/task_2026-04-28_contamination_remediation/work_log.md \
  architecture/test_topology.yaml \
  tests/test_supervisor_contracts.py \
  tests/test_pnl_flow_and_audit.py \
  --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json

# Split topology strategy: aggregate planning-lock proves cross-zone permission;
# scoped navigation is intentionally split because combined docs+test wording can
# over-select an R3 live-readiness profile and report scope_expansion_required.
python3 scripts/topology_doctor.py --navigation --task "Repair stale supervisor API contract tests only; production src/supervisor_api/contracts.py remains unchanged; paper env must stay rejected" --files \
  tests/test_supervisor_contracts.py

python3 scripts/topology_doctor.py --navigation --task "Repair PnL harvester test fixture source family only; production harvester code unchanged" --files \
  tests/test_pnl_flow_and_audit.py

python3 scripts/topology_doctor.py --navigation --task "Register first-four gate test lifecycle metadata for PnL and supervisor contract tests only" --files \
  architecture/test_topology.yaml

python3 scripts/topology_doctor.py --docs --json
# Expected for this packet: global docs may remain red from unrelated issues,
# but there must be no issue path/message for task_2026-04-28_contamination_remediation.

.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_supervisor_contracts.py \
  tests/test_pnl_flow_and_audit.py::test_inv_harvester_triggers_refit \
  tests/test_pnl_flow_and_audit.py::test_harvester_stage2_preflight_skips_canonical_bootstrap_shape \
  tests/test_pnl_flow_and_audit.py::test_inv_harvester_falls_back_to_open_portfolio_snapshot_when_no_durable_settlement_exists \
  tests/test_pnl_flow_and_audit.py::test_inv_harvester_uses_legacy_decision_log_snapshot_before_open_portfolio \
  tests/test_pnl_flow_and_audit.py::test_inv_harvester_prefers_durable_snapshot_over_open_portfolio \
  tests/test_pnl_flow_and_audit.py::test_inv_harvester_marks_partial_context_resolution \
  tests/test_pnl_flow_and_audit.py::test_inv_control_pause_stops_entries \
  tests/test_pnl_flow_and_audit.py::test_inv_strategy_tracker_receives_trades \
  --no-header
```

## Topology strategy note

The first-four-step patch deliberately uses split topology evidence:

- Aggregate `--planning-lock` with this plan evidence authorizes the cross-zone docs/test change set.
- Per-test `--navigation` validates the two implementation edits independently.
- Docs routing is validated by `--docs --json` filtered for this packet. A repo-wide docs red state is not treated as clean, but this packet must not be one of the reported issues.
- A combined docs+test navigation command is not the gate because its broad wording can over-select the R3 live-readiness profile and produce a false `scope_expansion_required` for packet evidence files.

## PnL file-scope note

`tests/test_pnl_flow_and_audit.py` entered this remediation lane with pre-existing Codex-owned fixture hunks from the interrupted implementation pass. This first gate does not claim those hunks as new production semantics; it includes them in the file-level audit because they remain in the scoped diff. The non-harvester hunks are limited to test fixture compatibility with existing runtime gates and current function signatures:

- `_allow_entry_gates_for_cycle_test()` opens R3 entry gates only in tests that exercise trade materialization.
- `_enable_live_harvester_test_path()` enables `ZEUS_HARVESTER_LIVE_ENABLED=1` only for targeted harvester tests and stubs `_write_settlement_truth` so this first gate can verify harvester learning/settlement flow without depending on the later Batch A nullable-settlements-schema parity edit.
- Harvester Gamma fixtures include resolved UMA status plus `outcomes=["Yes","No"]`, because `_find_winning_bin()` now intentionally requires resolved UMA vote payloads rather than pre-resolution prices.
- Harvester observation fixtures use `wu_icao_history` for the NYC/WU-family tests; this does not generalize to HKO/Hong Kong, which has no WU ICAO path.
- `save_portfolio` monkeypatches accept `*args, **kwargs` because production `save_portfolio()` accepts keyword audit metadata.
- Strategy-tracker market fixtures carry explicit `temperature_metric`.
- Filled `OrderResult` fixtures include `command_state` to match current execution result shape.

These hunks are verified by `test_inv_control_pause_stops_entries` and `test_inv_strategy_tracker_receives_trades` in addition to the six harvester tests.

## Critic gate

After the first-four-step patch and verification, dispatch an independent critic. Do not continue to remaining implementation batches unless the critic verdict is APPROVE or the requested revisions are completed and re-reviewed.

## Rollback

Rollback is file-specific via `git restore -- <file>` for these scoped files only. Do not restore or stage unrelated dirty work from parallel sessions.

## Batch A — state settlements schema parity

Status: complete; post-edit verifier PASS and critic APPROVE recorded in `work_log.md`.

Objective: decide whether the existing dirty `src/state/db.py` hunk that adds nullable `settlements.pm_bin_lo`, `pm_bin_hi`, `unit`, and `settlement_source_type` columns should be kept as schema parity for the existing harvester live write path.

Authority basis:

- `src/execution/harvester.py::_write_settlement_truth` already inserts these columns.
- `src/state/AGENTS.md` and `docs/reference/modules/state.md` require planning-lock evidence for state schema changes.
- Semantic bootstrap for settlement semantics is required because the schema supports settlement truth writes.

In scope:

- `src/state/db.py` nullable fresh-schema/legacy-ALTER parity only.
- `tests/test_settlements_unique_migration.py` explicit fresh-schema and
  legacy-ALTER parity coverage for `pm_bin_lo`, `pm_bin_hi`, `unit`, and
  `settlement_source_type`.
- `tests/test_harvester_metric_identity.py` removal of the manual test-only
  ALTER workaround plus a direct fresh-schema assertion.
- `architecture/test_topology.yaml` trusted-test metadata for the two reused
  Batch A tests.

Not in scope:

- No production DB mutation.
- No settlement rounding/bin topology changes.
- No harvester production behavior changes.
- No live harvester enablement.

Batch A verification:

```bash
python3 scripts/topology_doctor.py --navigation --task "Review existing src/state/db.py settlements schema parity with harvester live write no production DB mutation" --files src/state/db.py
python3 scripts/topology_doctor.py --navigation --task "Batch A revised test-first schema parity coverage for settlements nullable harvester columns only" --files tests/test_settlements_unique_migration.py tests/test_harvester_metric_identity.py
python3 scripts/topology_doctor.py semantic-bootstrap --task-class settlement_semantics --task "Review state settlements schema parity for harvester live settlement write" --files src/state/db.py src/execution/harvester.py --json
python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py tests/test_settlements_unique_migration.py tests/test_harvester_metric_identity.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_db.py tests/test_harvester_metric_identity.py tests/test_harvester_dr33_live_enablement.py tests/test_settlements_unique_migration.py --no-header
python3 -m py_compile src/state/db.py src/execution/harvester.py
git diff -- src/contracts/settlement_semantics.py src/execution/harvester.py  # must be empty
```

Batch A requires independent critic + verifier before moving to Batch B.

## Batch B — repair `scripts/rebuild_settlements.py` source-family provenance

Status: complete; Hong Kong no-WU correction included; post-edit verifier PASS and critic APPROVE recorded in `work_log.md`.

Batch A critic+verifier approved the nullable settlements schema parity hunk. Batch B is now scoped to the high-track settlement repair script and its regression evidence.

### Scope

KEEP/modify only:

- `scripts/rebuild_settlements.py`
- `tests/test_rebuild_pipeline.py`
- `architecture/test_topology.yaml` (test trust metadata only)
- this packet's `plan.md` / `work_log.md`

Do not modify settlement bin topology, `src/contracts/settlement_semantics.py`, production harvester logic, or production DBs. Do not run `scripts/rebuild_settlements.py --apply` against `state/zeus-world.db`.

### Required behavior

- VERIFIED observation rows are eligible only if their observation `source` matches the city's `settlement_source_type` family.
- WU cities accept `wu_icao_history` and the legacy WU fixture alias `wu_icao`; the settlement `data_version` is `wu_icao_history_v1`.
- HKO cities accept only `hko_daily_api`; the settlement `data_version` is `hko_daily_api_v1`.
- NOAA-backed cities accept only `ogimet_metar_*`; the settlement `data_version` is `ogimet_metar_v1`.
- CWA remains fail-closed until an accepted collector/proxy is explicitly wired.
- Expected row-level failures are counted with explicit skip reasons; unexpected DB/programming failures must not be swallowed.
- Repair writes preserve high-track identity (`temperature_metric='high'`, `mx2t6_local_calendar_day_max`, `high_temp`) and record unit/source-family/data-version provenance.

### Gates

```bash
python3 scripts/topology_doctor.py --navigation --task "Batch B script work repair rebuild_settlements source-family data_version and validation; no production DB apply" --files scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py tests/test_structural_linter.py docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
python3 scripts/topology_doctor.py --navigation --task "Audit and update rebuild pipeline regression test file for source-family data_version behavior" --files tests/test_rebuild_pipeline.py
python3 scripts/topology_doctor.py --navigation --task "Register rebuild pipeline test lifecycle metadata in test topology trusted list" --files architecture/test_topology.yaml
python3 scripts/topology_doctor.py semantic-bootstrap --task-class settlement_semantics --task "Batch B repair rebuild_settlements source-family data_version and settlement validation" --files scripts/rebuild_settlements.py src/data/rebuild_validators.py src/contracts/settlement_semantics.py --json
python3 scripts/topology_doctor.py --planning-lock --changed-files scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_rebuild_pipeline.py tests/test_structural_linter.py tests/test_settlement_semantics.py tests/test_authority_gate.py --no-header
python3 -m py_compile scripts/rebuild_settlements.py src/data/rebuild_validators.py src/contracts/settlement_semantics.py
python3 scripts/topology_doctor.py --scripts --json  # global may remain red; no rebuild_settlements issue allowed
python3 scripts/topology_doctor.py --tests --json    # global may remain red; no test_rebuild_pipeline issue allowed
git diff --check -- scripts/rebuild_settlements.py tests/test_rebuild_pipeline.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

Batch B requires independent critic + verifier before proceeding to the next implementation batch.

### Batch B correction — Hong Kong has no WU ICAO

Reviewer correction: Hong Kong must never inherit WU/ICAO source aliases. The `wu_icao` legacy fixture alias is allowed only after `city.settlement_source_type == "wu_icao"`; for Hong Kong/HKO (`settlement_source_type == "hko"`), both `wu_icao_history` and `wu_icao` are wrong-source-family rows and must be skipped rather than written.

Additional regression: `test_rebuild_settlements_skips_hong_kong_wu_source_aliases` seeds Hong Kong with both WU source names and requires zero settlements plus `source_family_mismatch: 2`.

## Batch C — `tests/test_sigma_floor_evaluation.py` fixture alignment

Status: complete; post-edit verifier PASS and critic APPROVE recorded in `work_log.md`.

Scope: test-only Day0Signal constructor alignment. Do not edit `src/signal/day0_signal.py`, `src/types/metric_identity.py`, or production signal behavior.

Decision: keep the dirty test hunk because `Day0Signal` now requires an explicit `MetricIdentity` and rejects `None`/bare strings. The test is a high-track Day0 sigma floor test, so `HIGH_LOCALDAY_MAX` is the correct explicit identity.

Batch C files:

- `tests/test_sigma_floor_evaluation.py`
- `architecture/test_topology.yaml` (trusted test metadata only)
- this packet's `plan.md` / `work_log.md`

Required gates:

```bash
python3 scripts/topology_doctor.py --navigation --task "Batch C audit sigma floor test constructor fixture alignment only; no source type or contract code changes" --files tests/test_sigma_floor_evaluation.py
python3 scripts/topology_doctor.py --navigation --task "Register sigma floor test lifecycle metadata in test topology trusted list only" --files architecture/test_topology.yaml
python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_sigma_floor_evaluation.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_sigma_floor_evaluation.py --no-header
python3 -m py_compile tests/test_sigma_floor_evaluation.py src/signal/day0_signal.py src/types/metric_identity.py
git diff --check -- tests/test_sigma_floor_evaluation.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

Batch C requires independent critic + verifier before the next implementation batch.

## Batch D — `tests/test_riskguard.py` stale-risk-law remediation plan

Status: complete; test-only remediation with post-edit verifier PASS and critic APPROVE recorded in `work_log.md`.

Scope starts as test-only. Do not edit `src/riskguard/riskguard.py` unless a separate critic explicitly approves a production riskguard change. Current authority checked:

- `src/riskguard/AGENTS.md`: RiskGuard is protective K1; computation/truth errors must not silently downgrade risk.
- `docs/reference/zeus_risk_strategy_reference.md` §1.3: missing/invalid trailing-loss reference returns `DATA_DEGRADED`, not GREEN and not forced RED.
- `docs/reference/zeus_risk_strategy_reference.md` §2.2: `tick()` requires canonical `position_current`; `choose_portfolio_truth_source()` must return `canonical_db` or RiskGuard raises `RuntimeError`.
- `docs/authority/zeus_current_architecture.md` §10.1: broken truth input must not silently downgrade risk.

Observed failures from the audited run (`16 failed, 31 passed`):

1. Several tests invoke `riskguard.tick()` on a zeus DB with no `position_current` table and still expect legacy JSON/working-state fallback. Current law says this is not a valid tick precondition; either initialize canonical schema for tests focused on other risk behavior, or assert `RuntimeError` for the explicit unavailable-canonical test.
2. `test_tick_prefers_position_current_for_portfolio_truth` expects `portfolio_capital_source == "working_state_metadata"`, but current implementation exposes `"dual_source_blended"` plus a consistency lock/counts; this is current dual-source RiskGuard law.
3. Trailing-loss degraded-reference tests expect `RED` and `no_trustworthy_reference_row`; current reference law says degraded references yield `DATA_DEGRADED` with source `no_trustworthy_reference_row`, while stale-but-valid references compute loss and degrade only GREEN outcomes.
4. Execution/strategy/degraded-settlement tests are blocked by missing canonical portfolio schema before reaching the behavior they intend to test.

Planned edits after critic approval:

- Add lifecycle header to `tests/test_riskguard.py` and register it in `architecture/test_topology.yaml` trusted metadata.
- Add a small test helper that initializes canonical portfolio schema (`init_schema`) for tick tests whose focus is not missing-canonical fail-closed behavior.
- Change the explicit projection-unavailable fallback test to assert the current `RuntimeError` contract rather than expecting a working-state fallback write.
- Update stale expected values to current law (`dual_source_blended`, `DATA_DEGRADED`, `risk_state_history`/`no_trustworthy_reference_row` as actually defined by reference status).
- Do not change production RiskGuard behavior in this batch.

Required gates after edits:

```bash
python3 scripts/topology_doctor.py --navigation --task "Batch D audit riskguard tests only; no production riskguard edits" --files tests/test_riskguard.py
python3 scripts/topology_doctor.py --navigation --task "Register riskguard test lifecycle metadata in test topology trusted list only" --files architecture/test_topology.yaml
python3 scripts/topology_doctor.py --planning-lock --changed-files tests/test_riskguard.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_riskguard.py --no-header
python3 -m py_compile tests/test_riskguard.py src/riskguard/riskguard.py src/riskguard/risk_level.py src/state/portfolio_loader_policy.py
git diff --check -- tests/test_riskguard.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

Batch D plan requires critic approval before test edits because it encodes RiskGuard semantics.

## Batch E — `tests/test_tick_size.py` / `src/execution/executor.py` exit finite-price guard ordering

Status: complete; context-complete post-edit verifier PASS and critic APPROVE recorded in `work_log.md`.

Scope: narrow execution safety bug only. `execute_exit_order()` currently calls CutoverGuard before pure exit-price derivation and finite-price validation, so malformed `current_price` values surface as `CutoverPending` in non-live test/runtime contexts instead of deterministic `OrderResult(status="rejected", reason="malformed_limit_price...")`.

Authority:

- `src/execution/AGENTS.md`: executor is live-money K2; control gates must still run before venue-command persistence or SDK contact.
- `docs/reference/modules/execution.md` §11-12: do not bypass CutoverGuard/Heartbeat/RiskAllocator for live placement; tests must not create live side effects.
- `tests/test_tick_size.py` lifecycle header: T5.b exit-path NaN/Inf guard must reject non-finite limit_price before tick clamp.

Planned edit after critic approval:

- Move `_assert_cutover_allows_submit(IntentKind.EXIT)` in `execute_exit_order()` from the very start of the function to after pure local validation of limit price, share rounding, and token id, but before `_assert_risk_allocator_allows_exit_submit()`, command persistence, heartbeat/ws/risk gates, or SDK contact.
- Do not bypass CutoverGuard for valid exit orders; only malformed local intents return typed rejection before any pre-submit gate.
- Update `tests/test_tick_size.py` last-used metadata and `architecture/test_topology.yaml` trusted metadata only.

Required gates:

```bash
python3 scripts/topology_doctor.py --navigation --task "Batch E tick-size exit finite-price guard ordering; executor safety-preserving production edit" --files src/execution/executor.py
python3 scripts/topology_doctor.py --navigation --task "Batch E tick-size regression tests only" --files tests/test_tick_size.py
python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/executor.py tests/test_tick_size.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tick_size.py tests/test_executor.py tests/test_execution_price.py tests/test_unknown_side_effect.py --no-header
python3 -m py_compile src/execution/executor.py tests/test_tick_size.py src/contracts/tick_size.py src/contracts/execution_price.py
python3 scripts/topology_doctor.py --tests --json  # global may remain red; no tests/test_tick_size.py issue allowed
git diff --check -- src/execution/executor.py tests/test_tick_size.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

Batch E requires pre-edit critic approval and post-edit critic + verifier.

### Batch E critic-required revision

Pre-edit critic required an explicit safety-half regression before approving production executor changes:

- Add a narrow `tests/test_tick_size.py` test proving a finite/valid `ExitOrderIntent` still raises `CutoverPending` in the current non-live cutover state.
- The malformed NaN/Inf tests prove invalid local input rejects before cutover; the new valid-exit test proves valid live-money exits still hit CutoverGuard before persistence/SDK contact.
- This test is expected to pass before and after the production move if CutoverGuard remains present and early enough; it fails if the guard is removed/bypassed.

Revised Batch E gate adds this test to the targeted tick-size run before requesting critic approval again.

## Batch F — topology_doctor docs-mode synthetic visible-path regression

Status: complete; context-complete post-edit verifier PASS and critic APPROVE recorded in work_log. Batch H remains a separate production-source audit.

Objective: repair the non-history `tests/test_topology_doctor.py` docs-mode failures where synthetic monkeypatched `_git_visible_files()` entries are ignored before docs policy checks run.

### Scope

Candidate touched files:

- `scripts/topology_doctor_docs_checks.py` — docs checker helper currently filters `_git_visible_files()` through `Path.is_file()` before checking hidden docs/docs registry policy.
- `architecture/topology.yaml` — only if needed to route the already-registered topology_doctor helper as an allowed topology-kernel companion file.
- `tests/test_topology_doctor.py` — only freshness metadata / regression-selector reuse evidence; do not weaken the two failing tests.
- `architecture/test_topology.yaml` — only trusted-test metadata if `tests/test_topology_doctor.py` is reused as evidence.
- this packet's `plan.md` / `work_log.md`.

### Non-scope

- Do not touch `architecture/history_lore.yaml` or history-lore routing failures.
- Do not weaken topology admission kernel invariants: forbidden-wins, no-echo, ambiguity detection, or profile/admission separation.
- Do not broaden generic fallback admission.
- Do not change docs subroot policy semantics beyond ensuring git-visible docs paths are actually checked.

### Current evidence

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_topology_doctor.py --no-header
=> 2 failed, 226 passed, 16 deselected
   - test_docs_mode_rejects_unregistered_visible_subtree
   - test_docs_mode_rejects_non_md_artifact_outside_artifact_subroot

Both failing tests monkeypatch topology_doctor._git_visible_files() to return
"docs/to-do-list/zeus_bug100_reassessment_table.csv" without creating the file.
`check_hidden_docs()` currently filters visible docs through `(api.ROOT / rel).is_file()`,
so the synthetic visible path is dropped and no docs issue is emitted.

python3 scripts/topology_doctor.py --navigation --task "Batch F topology doctor docs-mode synthetic git-visible path regression; no history_lore remediation" --files scripts/topology_doctor_docs_checks.py tests/test_topology_doctor.py docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> navigation ok false; profile=modify topology kernel; out_of_scope includes scripts/topology_doctor_docs_checks.py and packet plan/work_log.

python3 scripts/topology_doctor.py --planning-lock --changed-files scripts/topology_doctor_docs_checks.py architecture/topology.yaml tests/test_topology_doctor.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true
```

### Proposed implementation, subject to critic

1. Update topology routing only if necessary so `scripts/topology_doctor_docs_checks.py` is admitted as a topology_doctor helper file for this profile. The helper is already registered in `architecture/script_manifest.yaml` as imported by `topology_doctor.py` for docs checker family.
2. In docs checks, trust `_git_visible_files()` as the visibility source for docs policy checks instead of requiring physical `Path.is_file()` existence before policy evaluation. Preserve excluded-root checks and existing path normalization.
3. Keep or add tests that prove synthetic git-visible docs paths produce `docs_unregistered_subtree` and `docs_non_markdown_artifact`; do not weaken expected issue codes.
4. Run targeted topology doctor tests and governance checks.

### Gates

```bash
python3 scripts/topology_doctor.py --planning-lock --changed-files \
  scripts/topology_doctor_docs_checks.py \
  architecture/topology.yaml \
  tests/test_topology_doctor.py \
  architecture/test_topology.yaml \
  docs/operations/task_2026-04-28_contamination_remediation/plan.md \
  docs/operations/task_2026-04-28_contamination_remediation/work_log.md \
  --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json

.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_topology_doctor.py::test_docs_mode_rejects_unregistered_visible_subtree \
  tests/test_topology_doctor.py::test_docs_mode_rejects_non_md_artifact_outside_artifact_subroot \
  tests/test_topology_doctor.py::test_docs_mode_allows_registered_reports_json \
  tests/test_topology_doctor.py::test_docs_mode_excluded_roots_drive_space_path_exemption \
  --no-header

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_topology_doctor.py --no-header
python3 -m py_compile scripts/topology_doctor.py scripts/topology_doctor_docs_checks.py
python3 scripts/topology_doctor.py --schema
python3 scripts/topology_doctor.py --docs --json
python3 scripts/topology_doctor.py --scripts --json   # global may remain red from unrelated untracked scripts; no topology_doctor_docs_checks issue allowed
python3 scripts/topology_doctor.py --tests --json     # global may remain red from unrelated co-tenant tests; no test_topology_doctor issue allowed
git diff --check -- scripts/topology_doctor_docs_checks.py architecture/topology.yaml tests/test_topology_doctor.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

Batch F requires context-complete pre-edit critic approval before implementation and context-complete post-edit critic + verifier before moving to runtime_guards remediation.

### Batch F critic-required plan revisions

Pre-edit critic returned `REVISE_PLAN`; this section supersedes ambiguous wording above before any code/topology implementation.

1. **Docs gate is baseline/filtered, not global-green.** `python3 scripts/topology_doctor.py --docs --json` is currently red with 21 pre-existing unrelated docs issues (`/tmp/zeus_batch_f_docs_baseline.json`). Batch F closeout must compare against this baseline or filter to Batch-F-touched paths and prove no new Batch-F-attributable docs issue. It must not remediate unrelated docs issues or require global docs green.
2. **Topology admission update is explicit and narrow.** If implementation proceeds, update `architecture/topology.yaml` only by adding the already-registered helper `scripts/topology_doctor_docs_checks.py` to the `modify topology kernel` allowed-files/downstream surface as needed. Do not add `scripts/**`, do not admit unrelated topology helpers, and do not weaken no-echo/forbidden-wins/admission invariants.
3. **Implementation scope is `check_hidden_docs()` only.** Batch F will not alter `check_docs_registry()` because the current failures only prove hidden-docs policy is skipping synthetic git-visible paths. If a future batch wants docs-registry synthetic-path behavior, it must add a dedicated regression and explicitly assess deleted-but-still-indexed tracked docs risk.
4. **History-lore exclusion remains hard.** No `architecture/history_lore.yaml` or history-lore routing issue remediation in this batch.

Revised Batch F gates add:

```bash
# Baseline/filtered docs gate: global docs red is acceptable only when every issue
# matches the pre-existing baseline and no issue path is attributable to Batch F.
python3 scripts/topology_doctor.py --docs --json > /tmp/zeus_batch_f_docs_after.json || true
python3 - <<'PY'
import json, pathlib
before=json.loads(pathlib.Path('/tmp/zeus_batch_f_docs_baseline.json').read_text())
after=json.loads(pathlib.Path('/tmp/zeus_batch_f_docs_after.json').read_text())
key=lambda i:(i.get('code'), i.get('path'), i.get('message'))
new=sorted(set(map(key, after.get('issues', []))) - set(map(key, before.get('issues', []))))
blocked=[i for i in new if any(p in i[1] for p in ('scripts/topology_doctor_docs_checks.py','architecture/topology.yaml','tests/test_topology_doctor.py','task_2026-04-28_contamination_remediation'))]
assert not blocked, blocked
print({'before': len(before.get('issues', [])), 'after': len(after.get('issues', [])), 'new_batch_f_docs_issues': len(blocked)})
PY
```

### Batch F second critic-required plan revisions

Second context-complete pre-edit critic also returned `REVISE_PLAN`. This section is binding before implementation.

Additional required gates now included:

```bash
# Post-edit topology-kernel admission proof. Packet plan/work_log stay governed by
# planning-lock only and are intentionally excluded from this navigation command.
python3 scripts/topology_doctor.py --navigation \
  --task "Batch F modify topology kernel docs-check helper admitted narrowly; no history_lore remediation" \
  --files \
    scripts/topology_doctor_docs_checks.py \
    architecture/topology.yaml \
    tests/test_topology_doctor.py \
    architecture/test_topology.yaml
# Expected: navigation ok true, with scripts/topology_doctor_docs_checks.py admitted only because
# architecture/topology.yaml lists that exact helper in the modify-topology-kernel profile.

# Required by the modify-topology-kernel profile when architecture/topology.yaml admission changes.
.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_digest_admission_policy.py \
  tests/test_digest_profile_matching.py \
  tests/test_digest_regression_false_positive.py \
  --no-header

.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_topology_doctor.py -k 'navigation or digest or admission' \
  --no-header
```

Docs baseline gate clarification:

- Batch F uses a **filtered-only** docs gate, not strict global equality and not global-green.
- The gate fails on any new docs issue attributable to Batch-F-touched paths or this packet.
- New unrelated docs issues, if caused by co-tenant concurrent work, must be reported as unrelated and must not expand Batch F remediation scope.
- Current baseline remains `/tmp/zeus_batch_f_docs_baseline.json` with 21 unrelated docs issues.

Implementation scope remains unchanged: `check_hidden_docs()` only plus exact topology profile admission for `scripts/topology_doctor_docs_checks.py`; no `check_docs_registry()` edit.

### Batch F schema gate execution note

During Batch F verification, the topology profile's historical gate text `python3 scripts/topology_doctor.py --schema` was found to be a stale/nonexistent CLI flag in the current executable (`topology_doctor.py: error: unrecognized arguments: --schema`). Batch F does **not** add a CLI flag or broaden into topology CLI repair. The equivalent current schema check is executed directly through the existing checker surface:

```bash
python3 - <<'PY'
from scripts import topology_doctor
issues = topology_doctor._check_schema(topology_doctor.load_topology(), topology_doctor.load_schema())
if issues:
    print(topology_doctor.format_issues(issues))
    raise SystemExit(1)
print('schema check passed: no topology schema issues')
PY
```

## Batch G — runtime guard test-current-law fixture alignment

Status: complete; context-complete post-edit verifier PASS and critic APPROVE recorded in `work_log.md`. Batch H remains a separate production-source audit.

Objective: repair `tests/test_runtime_guards.py` failures caused by stale test fixtures after current live-entry gates, metric identity, Day0 router, ENS snapshot, live-safe boot, collateral, and canonical Day0 event contracts. This is a test-only batch: production source behavior remains unchanged.

### Scope

Candidate touched files:

- `tests/test_runtime_guards.py` — add lifecycle header and update stale fixtures/expectations to current executable contracts.
- `architecture/test_topology.yaml` — add/update trusted-test metadata for `tests/test_runtime_guards.py`.
- this packet's `plan.md` / `work_log.md`.

No production `src/**` edits in Batch G. If review determines any failure represents a production lifecycle bug rather than stale test fixture, stop and split that into a separate production batch with source topology + module references.

### Current failure inventory

Current run:

```text
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header
=> 20 failed, 99 passed
```

Failures and planned current-law treatment:

1. `test_trade_and_no_trade_artifacts_carry_replay_reference_fields` — stale run-cycle fixture blocked by current entry gates; open test-local runtime gates and keep production gates unchanged.
2. `test_live_dynamic_cap_flows_to_evaluator` — stale run-cycle fixture blocked before evaluator; open test-local gates, add explicit `temperature_metric`, and make `save_portfolio` monkeypatch kwargs-compatible with current `commit_then_export` audit metadata.
3. `test_execute_discovery_phase_logs_rejected_live_entry_telemetry` — stale market fixture lacks explicit `temperature_metric` and run-cycle gates; add metric/open gates; preserve rejected telemetry assertion.
4. `test_strategy_gate_blocks_trade_execution` — stale run-cycle fixture blocked by outer gates before strategy gate; open outer gates while preserving test-local strategy-gate disable.
5. `test_run_cycle_surfaces_fdr_family_scan_failure_without_entries` — stale run-cycle fixture lacks metric/outer gates; add metric/open gates so FDR no-trade path is exercised.
6. `test_materialize_position_preserves_evaluator_strategy_key` — non-`MarketCandidate` fixture lacks `temperature_metric`; add explicit `temperature_metric="high"` to the test fixture so the test still proves strategy-key preservation without relying on the removed silent-HIGH fallback.
7. `test_evaluator_projects_exposure_across_multiple_edges` — dummy `EnsembleSignal` lacks current `bias_corrected` field required by `_store_snapshot_p_raw`; add `bias_corrected=False`.
8. `test_update_reaction_degenerate_ci_fails_closed_before_sizing` — same dummy ENS `bias_corrected` fixture update; keep fail-closed-before-sizing assertion.
9. `test_update_reaction_brier_alpha_fails_closed_before_sizing` — same dummy ENS `bias_corrected` fixture update; keep alpha-target fail-closed assertion.
10. `test_day0_observation_path_reaches_day0_signal` — evaluator now routes through `Day0Router.route()` rather than an `evaluator_module.Day0Signal` symbol; update monkeypatch to intercept `Day0Router.route()` and inspect `Day0SignalInputs` while preserving Day0 probability assertion.
11. `test_day0_observation_path_rejects_missing_solar_context` — fixture has incomplete bin topology (missing low shoulder) and fails at `MARKET_FILTER` before solar context; add a lower shoulder bin so the solar-context guard is tested.
12. `test_gfs_crosscheck_uses_local_target_day_hours_instead_of_first_24h` — dummy ENS lacks `bias_corrected`; add it so the GFS local-day crosscheck is reached.
13. `test_store_ens_snapshot_marks_degraded_clock_metadata_explicitly` — current `_snapshot_valid_time_value()` intentionally returns `None` rather than synthetic `FORECAST_WINDOW_START(...)`; align expectation with current no-fake-valid-time law.
14. `test_main_registers_ecmwf_open_data_jobs` — live boot now refuses non-allowlisted strategies before scheduler registration; monkeypatch the live-safe strategy guard in this scheduler-registration test instead of weakening production `LIVE_SAFE_STRATEGIES`.
15. `test_incomplete_exit_context_near_settlement_escalates_monitor_chain` — fixture time is 8h before settlement while code escalates at ≤6h; move test clock to ≤6h so it actually tests near-settlement escalation.
16. `test_monitoring_phase_persists_live_exit_telemetry_chain` — current Day0 canonical event emits before exit for positions crossing Day0 in the monitor cycle. Update the test to seed canonical entry baseline (matching production reality that entries precede Day0) and expect the DAY0 event plus exit fill, rather than relying on exit-lifecycle legacy-entry backfill after Day0 already exists.
17. `test_materialize_position_carries_semantic_snapshot_jsons` — wrapper now requires explicit `env` and candidate metric; add `env="test"` plus `temperature_metric="high"`.
18. `test_execute_exit_routes_live_sell_through_executor_exit_path` — live exit now checks sell collateral fail-closed before order placement; monkeypatch `check_sell_collateral` to `(True, None)` in the routing test.
19. `test_execute_exit_rejected_orderresult_preserves_retry_semantics` — same collateral monkeypatch so rejection semantics, not collateral availability, are under test.
20. `test_discovery_phase_records_rate_limited_decision_as_availability_fact` — direct discovery market fixture lacks explicit `temperature_metric`; add it so rate-limit availability fact is tested.

### Non-scope

- Do not change `src/engine/cycle_runner.py`, `src/engine/cycle_runtime.py`, `src/engine/evaluator.py`, `src/execution/exit_lifecycle.py`, `src/execution/collateral.py`, `src/main.py`, or supervisor contracts in Batch G.
- Do not add `paper` to any production env allowlist or supervisor contract.
- Do not relax live-safe strategy guard, CutoverGuard, heartbeat, WS gap, portfolio governor, collateral, metric identity, or Day0Router production behavior.
- Do not touch TIGGE training/data-readiness or `architecture/history_lore.yaml`.

### Gates

```bash
python3 scripts/topology_doctor.py --navigation --task "Batch G runtime guard tests fixture alignment only; no production source edits" --files tests/test_runtime_guards.py
python3 scripts/topology_doctor.py --planning-lock --changed-files \
  tests/test_runtime_guards.py \
  architecture/test_topology.yaml \
  docs/operations/task_2026-04-28_contamination_remediation/plan.md \
  docs/operations/task_2026-04-28_contamination_remediation/work_log.md \
  --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header

# Cross-check focused production modules compile but remain behavior-unchanged.
python3 -m py_compile \
  tests/test_runtime_guards.py \
  src/engine/cycle_runner.py \
  src/engine/cycle_runtime.py \
  src/engine/evaluator.py \
  src/execution/exit_lifecycle.py \
  src/main.py

git diff -- src/engine/cycle_runner.py src/engine/cycle_runtime.py src/engine/evaluator.py src/execution/exit_lifecycle.py src/execution/collateral.py src/main.py src/supervisor_api/contracts.py
# Expected: empty

python3 scripts/topology_doctor.py --tests --json
# Global may remain red from unrelated co-tenant tests; no `tests/test_runtime_guards.py` issue allowed.

git diff --check -- tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

Batch G requires context-complete pre-edit critic approval before implementation and context-complete post-edit critic + verifier before moving to any production/runtime follow-up.

### Batch G critic-required plan revisions

Pre-edit critic returned `REVISE_PLAN`. The following revisions are binding before implementation:

1. **Split item 16.** `test_monitoring_phase_persists_live_exit_telemetry_chain` may be adjusted only as a test for current canonical-entry-baseline behavior. It must be renamed/commented accordingly and seed canonical entry events before the same-cycle Day0 transition/exit. The legacy case uncovered by the current failure — a legacy position that receives `DAY0_WINDOW_ENTERED` before exit and then skips entry-event backfill because `_has_canonical_position_history()` is already true — is a real lifecycle-backfill ambiguity and is explicitly deferred to a separate production-source Batch H/audit. Batch G must not silently claim that legacy-backfill concern is fixed.
2. **Entry-gate helper is targeted, not autouse.** A helper may open CutoverGuard/heartbeat/ws/portfolio-governor/posture only in named tests that need discovery to run. Do not apply it to tests that verify risk-level, entries-paused, quarantine, exposure, or other gate-block behavior.
3. **Prefer `env="test"`.** Updated fixtures should use `env="test"` unless the test explicitly covers a legacy/paper compatibility seam. Do not add or expand production `paper` env handling.
4. **Add `src/control/control_plane.py` to source-protection gates.** This explicitly protects `LIVE_SAFE_STRATEGIES` from accidental weakening.

Revised gates add/update:

```bash
python3 -m py_compile \
  tests/test_runtime_guards.py \
  src/engine/cycle_runner.py \
  src/engine/cycle_runtime.py \
  src/engine/evaluator.py \
  src/execution/exit_lifecycle.py \
  src/execution/collateral.py \
  src/main.py \
  src/control/control_plane.py

git diff -- \
  src/engine/cycle_runner.py \
  src/engine/cycle_runtime.py \
  src/engine/evaluator.py \
  src/execution/exit_lifecycle.py \
  src/execution/collateral.py \
  src/main.py \
  src/control/control_plane.py \
  src/supervisor_api/contracts.py
# Expected: empty
```

Batch H placeholder (not implemented in Batch G): production-source audit for legacy positions with `DAY0_WINDOW_ENTERED` but missing entry events causing exit-lifecycle entry backfill to skip. Batch H will require source topology, scoped AGENTS, lifecycle/state/execution references, regression first, and its own critic/verifier gates.

## Batch H — legacy Day0-only canonical history entry-backfill audit

Status: complete; context-complete re-review verifier PASS_WITH_NOTES and critic APPROVE_WITH_NOTES recorded in `work_log.md` after the H0b profile-law erratum fix.

Objective: close the lifecycle-backfill ambiguity split out of Batch G: a legacy position may already have a canonical `DAY0_WINDOW_ENTERED` event but no entry events, causing `exit_lifecycle._dual_write_canonical_economic_close_if_available()` to treat "any canonical history" as sufficient and append `EXIT_ORDER_FILLED` without `POSITION_OPEN_INTENT` / `ENTRY_ORDER_POSTED` / `ENTRY_ORDER_FILLED` backfill lineage.

### Scope

Candidate touched files:

- `src/execution/exit_lifecycle.py` — refine canonical-history detection so economic-close dual-write backfills missing legacy entry event types even when non-entry canonical events already exist.
- `tests/test_runtime_guards.py` — add regression for an existing Day0-only canonical history that must be healed before appending `EXIT_ORDER_FILLED`.
- `architecture/test_topology.yaml` — update trusted-test `last_used` for `tests/test_runtime_guards.py` if needed.
- this packet's `plan.md` / `work_log.md`.

No settlement semantics, source routing, calibration, TIGGE/data-readiness, supervisor contract env grammar, or live side-effect behavior changes in Batch H.

### Read-only evidence before implementation

```text
python3 scripts/topology_doctor.py --navigation --task "Batch H production-source audit for legacy positions whose canonical history starts at DAY0_WINDOW_ENTERED and may skip entry-event backfill before EXIT_ORDER_FILLED; regression first" --files src/execution/exit_lifecycle.py src/engine/cycle_runtime.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
=> navigation ok false; profile=r3 live readiness gates implementation; scope_expansion_required for source/test/packet files. Treat as a stop-and-plan signal; do not edit before critic approval.

python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/exit_lifecycle.py src/engine/cycle_runtime.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
=> ok true

python3 scripts/topology_doctor.py semantic-bootstrap --task-class day0_monitoring --task "Batch H production-source audit for legacy positions whose canonical history starts at DAY0_WINDOW_ENTERED and may skip entry-event backfill before EXIT_ORDER_FILLED" --files src/execution/exit_lifecycle.py tests/test_runtime_guards.py --json
=> ok true; current_source_validity fresh (last audited 2026-04-21, age 7d), current_data_state fresh (last audited 2026-04-23, age 5d); Code Review Graph stale/unavailable and may be used only as derived context, not authority.

Throwaway reproduction script (no repo file edits) showed current bug shape:
=> [(1, 'DAY0_WINDOW_ENTERED', 'repro'), (2, 'EXIT_ORDER_FILLED', 'src.execution.exit_lifecycle')]
```

### Semantic proof answers

- Day0 source vs settlement source: Batch H does **not** read or change live Day0 observation values, settlement sources, station routing, or source freshness. It only repairs canonical event lineage when an already-held position exits. The source-role fatal misread remains avoided; Code Review Graph is not used as source truth.
- High/low Day0 causality: Batch H does **not** change high/low observed-extrema logic, Day0Router, forecast/observation probability construction, or settlement rounding. It preserves lifecycle event order/lineage only.
- Hong Kong: no Hong Kong source claim is made in Batch H. The standing correction remains: Hong Kong has no WU ICAO route in this code path; HKO current truth requires fresh audit evidence and is out of scope.

### Planned implementation shape

Regression first:

1. Add a focused test creating a canonical history with only `DAY0_WINDOW_ENTERED` for a legacy position, then invoke the economic-close dual-write path.
2. Assert the existing `DAY0_WINDOW_ENTERED` row is not mutated or renumbered.
3. Assert the resulting canonical events include the missing entry lineage plus `EXIT_ORDER_FILLED`, with `ENTRY_ORDER_POSTED` carrying `decision_evidence_reason="backfill_legacy_position"`.
4. Assert appended missing entry events start at `_next_canonical_sequence_no`, `EXIT_ORDER_FILLED` comes after appended backfill, and `sequence_no`, `event_id`, and `idempotency_key` remain unique.
5. Add a partial-entry-history case proving existing entry event types are not duplicated.
6. The regression should fail on current code because only `DAY0_WINDOW_ENTERED` + `EXIT_ORDER_FILLED` are written for the Day0-only case.

Production change:

1. Replace/augment `_has_canonical_position_history()` usage with event-type-aware entry-history detection in `src/execution/exit_lifecycle.py`.
2. If any required entry event type is missing, build the legacy entry backfill events using existing `build_entry_canonical_write(..., decision_evidence_reason="backfill_legacy_position")` and append only missing event types.
3. Renumber appended backfill events from `_next_canonical_sequence_no(conn, trade_id)` so append-only `UNIQUE(position_id, sequence_no)` is preserved when a prior Day0 event already occupies sequence 1.
4. Set `EXIT_ORDER_FILLED` sequence after any appended backfill events.
5. Do not mutate existing canonical events, do not invent lifecycle phase strings, and do not change settlement/source/trading decisions.

### Gates

```bash
# Failing regression first after adding the test, before source fix.
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py::<new_batch_h_test_name> --no-header

# Post-fix focused + broader gates.
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py::<new_batch_h_test_name> --no-header
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py --no-header
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_runtime_guards.py tests/test_entry_exit_symmetry.py tests/test_day0_exit_gate.py --no-header

python3 -m py_compile src/execution/exit_lifecycle.py src/engine/lifecycle_events.py src/engine/cycle_runtime.py tests/test_runtime_guards.py
python3 scripts/topology_doctor.py --planning-lock --changed-files src/execution/exit_lifecycle.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
python3 scripts/topology_doctor.py --tests --json  # global may remain red from unrelated/co-tenant files; no tests/test_runtime_guards.py issue allowed

git diff --check -- src/execution/exit_lifecycle.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

Batch H requires context-complete pre-edit critic approval before any test/source implementation and context-complete post-edit critic + verifier before moving to the next packet/remediation item.

## Batch H0 — topology admission for legacy exit-lifecycle backfill remediation

Status: complete; H0a/H0b topology admission closed. The later Batch H post-edit critic found and reopened a profile-law erratum (`ENTRY_ORDER_PLACED` wording); the erratum is fixed in topology YAML + generated mirror, guarded by regression coverage, and re-approved by context-complete critic/verifier re-review.

Objective: unblock Batch H safely by adding a narrow topology digest/admission route for the exact contamination-remediation exit-lifecycle backfill scope while preserving the YAML→Python digest-profile mirror contract. This is governance/topology work only; it does not edit production runtime behavior.

### Scope

Candidate touched files:

- `architecture/topology.yaml` — first admit generated companion surfaces under the modify-topology-kernel profile, then add one exact digest profile for Batch H legacy Day0-only canonical-history entry-backfill remediation.
- `architecture/digest_profiles.py` — generated mirror regenerated from `architecture/topology.yaml` via `scripts/digest_profiles_export.py` after digest-profile edits.
- `tests/test_digest_profile_matching.py` — add regression proving the exact task routes to the new profile and admits only the intended files.
- `tests/test_digest_profiles_equivalence.py` — reuse/register the YAML/Python mirror relationship test as an H0 gate.
- `architecture/test_topology.yaml` — update trusted-test metadata for reused digest tests.
- this packet's `plan.md` / `work_log.md`.

Planned new Batch H profile boundaries:

- allowed: `src/execution/exit_lifecycle.py`, `tests/test_runtime_guards.py`, `architecture/test_topology.yaml`, exact packet evidence files `docs/operations/task_2026-04-28_contamination_remediation/plan.md`, `docs/operations/task_2026-04-28_contamination_remediation/work_log.md`, and tightly scoped evidence outputs `docs/operations/task_2026-04-28_contamination_remediation/evidence/critic-harness/batch_h*.md`.
- downstream/context only, not admitted: `src/engine/lifecycle_events.py`, `src/state/ledger.py`, `src/engine/cycle_runtime.py`, `tests/test_entry_exit_symmetry.py`, `tests/test_day0_exit_gate.py`. If Batch H later needs builder/ledger/cycle edits, stop and plan a separate source batch.
- forbidden/out of scope: settlement semantics, source routing/current-fact rewrites, calibration/TIGGE/data-readiness, supervisor env grammar, production DB/state artifacts, live side effects/cutover/credentials, `architecture/history_lore.yaml`, and broad R3 M4/M5 semantics.

### Required H0 sequencing

H0 must run in two ordered substeps because `architecture/digest_profiles.py` is a generated companion that is not currently admitted by the `modify topology kernel` profile.

**H0a — companion admission only**

1. Edit `architecture/topology.yaml` (plus packet `plan.md` / `work_log.md`) to admit `architecture/digest_profiles.py` and `tests/test_digest_profiles_equivalence.py` as generated/relationship companions under the existing `modify topology kernel` profile.
2. Register/reuse `tests/test_digest_profiles_equivalence.py` in `architecture/test_topology.yaml` before relying on it as an H0a gate.
3. Immediately regenerate `architecture/digest_profiles.py` via `python3 scripts/digest_profiles_export.py` because the generated mirror must stay equivalent after any `digest_profiles` YAML edit. Never hand-edit the generated file.
4. Verify H0a with topology-kernel gates, exporter/equivalence gates, filtered tests topology (`tests/test_digest_profiles_equivalence.py` issue count zero), and post-edit critic/verifier if critic requires a separate gate.

**H0b — exact Batch H profile + mirror regeneration**

1. After H0a navigation admits the companion files, add the exact Batch H digest profile and H0 regression tests.
2. Regenerate `architecture/digest_profiles.py` with `python3 scripts/digest_profiles_export.py`; never hand-edit the generated file.
3. Run the full H0b gates below, including mirror equivalence.

### Gates

```bash
# H0a proof: current pre-edit navigation without generated companions is ok; post-edit includes regenerated mirror.
python3 scripts/topology_doctor.py --navigation --task "Batch H0a modify topology kernel admit digest profile generated companion surfaces" --files architecture/topology.yaml
# Post-H0a navigation must include generated companion + reused relationship test once admitted.
python3 scripts/topology_doctor.py --navigation --task "Batch H0a modify topology kernel admit digest profile generated companion surfaces" --files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml
python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json
python3 scripts/digest_profiles_export.py
python3 scripts/digest_profiles_export.py --check
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_profiles_equivalence.py --no-header
python3 scripts/topology_doctor.py --tests --json  # global may remain red from unrelated/co-tenant tests; no tests/test_digest_profiles_equivalence.py issue allowed

# H0b proof after H0a companion admission lands.
python3 scripts/topology_doctor.py --navigation --task "Batch H0 modify topology kernel add exact contamination remediation exit lifecycle backfill profile" --files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml
python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md --plan-evidence docs/operations/task_2026-04-28_contamination_remediation/plan.md --json

.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::<new_batch_h_profile_test> --no-header
# H0 regression must also prove near-miss wording does not select/admit the new profile,
# downstream-only files are out-of-scope, forbidden surfaces are blocked/not admitted,
# and routing does not fall back to R3 live-readiness or broad R3 M4/M5 profiles.
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_admission_policy.py tests/test_digest_profile_matching.py tests/test_digest_regression_false_positive.py --no-header
python3 scripts/digest_profiles_export.py --check
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_digest_profiles_equivalence.py --no-header
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_topology_doctor.py -k 'navigation or digest or admission' --no-header
python3 - <<'PY_SCHEMA'
from scripts import topology_doctor
issues = topology_doctor._check_schema(topology_doctor.load_topology(), topology_doctor.load_schema())
if issues:
    print(topology_doctor.format_issues(issues))
    raise SystemExit(1)
print('schema check passed: no topology schema issues')
PY_SCHEMA

# Post-edit proof for Batch H production scope; must become navigation ok true before any source edit.
python3 scripts/topology_doctor.py --navigation --task "Batch H legacy Day0-only canonical history entry backfill remediation" --files src/execution/exit_lifecycle.py tests/test_runtime_guards.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
# Negative navigation/admission proofs: downstream-only/forbidden files must stay out-of-scope or blocked.
python3 scripts/topology_doctor.py --navigation --task "Batch H legacy Day0-only canonical history entry backfill remediation" --files src/engine/lifecycle_events.py src/state/ledger.py src/engine/cycle_runtime.py architecture/history_lore.yaml docs/authority/zeus_current_architecture.md src/supervisor_api/contracts.py || true

python3 -m py_compile scripts/topology_doctor.py tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py architecture/digest_profiles.py
python3 scripts/topology_doctor.py --tests --json  # global may remain red from unrelated/co-tenant tests; no test_digest_profile_matching issue allowed

git diff --check -- architecture/topology.yaml architecture/digest_profiles.py tests/test_digest_profile_matching.py tests/test_digest_profiles_equivalence.py architecture/test_topology.yaml docs/operations/task_2026-04-28_contamination_remediation/plan.md docs/operations/task_2026-04-28_contamination_remediation/work_log.md
```

Batch H0 requires context-complete pre-edit critic approval and context-complete post-edit critic/verifier before Batch H production-source implementation.
