# Dead Replacement-Forecast Promotion / Go-Live Apparatus — Surgical Removal (Implementation Receipt)

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: removal plan `docs/evidence/timing_audit/dead_promotion_apparatus_removal_plan_2026-06-16.md`;
  operator severance commits `b646f99339` + `54a53334a9` (promotion/capital-objective evidence gate REMOVED
  from BOTH live-authority sites — LIVE_AUTHORITY is FLAG-ONLY: `shadow -> veto -> trade_authority`).
- Worktree: `/Users/leofitz/zeus/.claude/worktrees/timing-fixes` (branch `fix/timing-semantics-2026-06-16`).
- Scope: REMOVE the dead go-live readiness-verdict apparatus. Did NOT touch `/Users/leofitz/zeus/src` (live tree).

## STATUS: DONE

All 4 ordered steps executed. HARD VERIFICATION GATE fully passed. Zero new test regressions (proved by
stash/restore failure-set delta: my tree 24 failures, baseline 28 failures, NEW-regressions set EMPTY).

## Pre-flight ground truth (verified, not assumed)

1. `resolve_replacement_forecast_runtime_policy` (runtime_policy.py:234-311) body reads ONLY the 5 flags;
   `promotion_evidence` / `capital_objective_evidence` params are NEVER referenced in the body. Passing `None`
   is behavior-identical. CONFIRMED by direct read.
2. `ReplacementForecastSwitchDecisionInput.__post_init__` (switch_decision.py:51-52) only `isinstance`-checks
   `capital_objective_evidence`; the guard is `if ... is not None`, so `None` skips it. Body (95-200) never reads
   the field. CONFIRMED.
3. `event_reactor_adapter.py:200-201` imports the dataclasses `ReplacementForecastPromotionEvidence` /
   `ReplacementForecastCapitalObjectiveEvidence` from `runtime_policy` (KEEP), NOT from the deleted
   `promotion_evidence` module. The promotion_evidence MODULE is imported in src ONLY by `go_live_report.py:45`
   (same dead batch). CONFIRMED — false-positive resolved.
4. `replacement_forecast_live_dry_run` is KEEP (operator veto-switch preflight). `build_replacement_forecast_
   live_dry_run_report` DID call `_configured_promotion_evidence` (line 615) and fed the result to the INERT
   resolver (618-619), discarding `_evidence_status`. Because the resolver ignores the evidence, removing the
   loader is behavior-identical to the preflight's output (`policy.status` is flag-derived). Trim is NOT
   load-bearing for the preflight -> proceeded (not STOPPED).

## Step 1 — DECOUPLE the live tie (main.py) — behavior-identical

- Removed `_replacement_forecast_promotion_evidence_from_settings` and
  `_replacement_forecast_capital_objective_evidence_from_settings` (the only `from ...go_live_report import`
  statements in main.py). Replaced with a removal-marker comment.
- Set the two live locals (`replacement_forecast_promotion_evidence`, `replacement_forecast_capital_objective_evidence`)
  to `None` at their assignment site (formerly the parser calls). The 4 downstream adapter kwargs
  (`event_bound_live_adapter` + `event_bound_no_submit_adapter`) reference these locals and now thread `None`
  (the adapter default), identical to before.
- ADDITIONAL consumer found (NOT in plan): `tests/test_replacement_forecast_reactor_hook.py` called these two
  `_from_settings` functions directly (3 tests + 1 helper). Handled in Step 3.
- py_compile src/main.py: OK. No remaining ref to the deleted parser fns or go_live_report in main.py.

## Step 2 — Trim live_dry_run's dead go_live_report dependency (KEEP module preserved)

- `build_replacement_forecast_live_dry_run_report`: replaced `_configured_promotion_evidence(root)` call with
  literal `promotion_evidence=None, capital_objective_evidence=None` (resolver ignores them).
- Deleted the 3 now-orphaned helpers: `_configured_promotion_evidence` (imported go_live_report),
  `_flat_promotion_evidence`, `_flat_capital_objective_evidence`. Removed the orphaned `PROMOTION_EVIDENCE_FILE`
  constant (zero importers).
- py_compile + `import src.data.replacement_forecast_live_dry_run`: OK.
- `import scripts.apply_replacement_forecast_shadow_veto_switch` (the operator LIVE tool that uses the preflight):
  OK — the ops tool still works without the deleted verdict apparatus. (Did NOT STOP — trim was inert, not
  load-bearing.)

## Step 3 — Trim other go_live_report consumers

- `scripts/replay_downloaded_replacement_economic.py` (economic-replay tournament): removed the
  `replacement_forecast_go_live_payload_template` import, the `_write_go_live_payload_json` function (1291-1412),
  its single call site, and the `--go-live-payload-json` arg. The economic tournament / before-after / executive
  summary artifacts are untouched. py_compile: OK. (NOTE: 3 helper fns — `_empirical_q_lcb_coverage_for_rows`,
  `_nested_walk_forward_passed_for_rows`, `_q_lcb_source_counts` — are now unreachable but left in place: they
  import NO deleted module, are pure analysis math, and removing them is scope-creep beyond the verdict
  apparatus. Flagged as orphaned, not deleted.)
- `tests/test_replacement_forecast_reactor_hook.py`: removed the 3 dead tests that exercised the deleted
  `_from_settings` parsers + the orphaned `_promotion_evidence_payload_dict` helper. KEPT `_promotion_evidence`
  / `_capital_objective_evidence` helpers (used by ~13 other KEEP-API tests). 32/32 remaining tests PASS.
- `tests/engine/test_replacement_0_1_authority_evidence_gate.py`: removed `_real_on_disk_failing_promotion_
  evidence` (the only go_live_report consumer; also read an uncommitted LIVE-tree file) + the one "both present
  but one fails" sub-block in `test_evidence_gate_pure_predicate_contract`. KEPT the None-promotion /
  None-capital / both-passing assertions (they test the KEEP `replacement_live_authority_evidence_gate`
  predicate in runtime_policy). Removed now-orphaned `import json`, `from pathlib import Path`, `_LIVE_EVIDENCE_PATH`.
  `test_evidence_gate_pure_predicate_contract`: PASSES.

## Step 4 — Delete SAFE_DELETE modules + direct tests + pure drivers (zero remaining importers)

Grep-confirmed zero remaining importers (post Steps 1-3) before deleting. Re-pointed the ONE live-apply-path
test's `REPLACEMENT_SHADOW_TABLES` import from the dying `simple_switch_bundle` to the canonical LIVE source
`scripts.init_replacement_forecast_shadow_schema` (byte-identical 4-tuple; already the source used by
`test_replacement_forecast_live_dry_run.py`).

### Deleted (20 files)
src modules (7):
- src/data/replacement_forecast_go_live_report.py
- src/data/replacement_forecast_promotion_evidence.py
- src/data/replacement_forecast_before_after_report.py
- src/data/replacement_forecast_rollback_plan.py
- src/data/replacement_forecast_runtime_wiring_audit.py
- src/data/replacement_forecast_simple_switch_bundle.py
- src/data/replacement_forecast_simple_switch_evidence.py

tests (8):
- tests/test_replacement_forecast_go_live_report.py
- tests/test_replacement_forecast_promotion_evidence.py
- tests/test_replacement_forecast_before_after_report.py
- tests/test_replacement_forecast_rollback_plan.py
- tests/test_replacement_forecast_runtime_wiring_audit.py
- tests/test_replacement_forecast_simple_switch_bundle.py
- tests/test_replacement_forecast_simple_switch_evidence.py
- tests/test_replacement_forecast_simple_switch_rehearsal.py

scripts / pure drivers (5):
- scripts/report_replacement_forecast_go_live.py
- scripts/audit_replacement_forecast_runtime_wiring.py
- scripts/plan_replacement_forecast_simple_switch_bundle.py
- scripts/build_replacement_forecast_simple_switch_evidence.py
- scripts/plan_replacement_forecast_live_authority_switch.py  (standalone go-LIVE-authority readiness driver;
  built entirely on go_live_report verdict; nothing imports it)

### Edited (7 files)
- src/main.py (Step 1)
- src/data/replacement_forecast_live_dry_run.py (Step 2)
- scripts/replay_downloaded_replacement_economic.py (Step 3)
- tests/test_replacement_forecast_reactor_hook.py (Step 3)
- tests/engine/test_replacement_0_1_authority_evidence_gate.py (Step 3)
- tests/test_replacement_forecast_shadow_veto_switch_apply.py (Step 4 re-point)
- architecture/money_path_objects.yaml (registry hygiene — see below)

## KEEP set — confirmed untouched + import clean
`replacement_forecast_runtime_policy`, `replacement_forecast_bundle_reader`, `replacement_forecast_refit_gate`,
`replacement_forecast_production` (shadow materialize), `replacement_forecast_live_switch_surface`,
`replacement_forecast_switch_decision`, `replacement_forecast_config_switch`, `replacement_forecast_live_dry_run`
(trimmed-but-KEEP), `replacement_forecast_readiness`, `replacement_forecast_refit_handoff(_install)`,
`replacement_forecast_current_fact_patch`, `replacement_forecast_hook_factory`, `event_reactor_adapter`.

## HARD VERIFICATION GATE — all PASS

- py_compile main.py + event_reactor_adapter.py + every KEEP module + the 3 edited scripts/modules: **OK**
- `import src.engine.event_reactor_adapter` (live reactor): **OK**
- `import src.engine.replacement_forecast_hook_factory`: **OK**
- runtime_policy / bundle_reader / switch_decision / config_switch / live_switch_surface / live_dry_run imports: **OK**
- `import scripts.apply_replacement_forecast_shadow_veto_switch` (operator preflight tool): **OK**
- `import src.main` (heaviest): **OK**
- `tests/test_runtime_guards.py -q`: **284 passed of the suite; the 4 failures are PRE-EXISTING** (proved by
  stash/restore — identical on baseline: test_live_dynamic_cap_flows_to_evaluator, test_strategy_gate_blocks_
  trade_execution, test_strategy_phase_gate_blocks_key_mode_mismatch, test_run_cycle_surfaces_fdr_family_scan_
  failure_without_entries). None touch the promotion apparatus. Reactor-path sanity preserved.
- grep deleted-module IMPORTS in src/ + scripts/ + tests/: **ZERO**
- grep deleted-module string/path refs in src/ + scripts/ (excluding removal-comment markers): **ZERO**
- Whole-suite `pytest --collect-only`: 17062/17081 collect clean; the ONE collection error
  (tests/contracts/test_venue_amount_grid_sdk_faithful.py) is a PRE-EXISTING missing-SDK env issue
  (`ModuleNotFoundError: py_clob_client_v2`), unrelated to this change.

## Regression proof (stash/restore failure-set delta)

On the concrete affected test files (runtime_guards, reactor_hook, shadow_veto_switch_apply, live_dry_run,
config_switch, runtime_policy, switch_decision, 0_1_authority_evidence_gate):
- MY tree: 24 distinct failures
- BASELINE (my work stashed): 28 distinct failures
- **NEW regressions (mine NOT in baseline): EMPTY** — my change adds zero failures.
- 4 baseline-only failures (monitor/ens-refresh/reconcile) are test-ordering nondeterminism in the broader
  worktree WIP, not caused by this change.

The pre-existing runtime_policy / switch_decision failures are STALE tests asserting the OLD evidence-gate
(LIVE_AUTHORITY requires promotion proof) — that gate was severed by the operator on 2026-06-08, so they have
been failing since then. They were NOT modified by this task and import no deleted module.

## Registry hygiene (architecture/money_path_objects.yaml)

- One stale ref: `money_path_objects.yaml:464` listed the deleted `tests/test_replacement_forecast_go_live_report.py`
  as an invariant_test for the replacement-intent-immutability money-path object. Removed only that dead test-path
  line (a non-existent path would break map-maintenance). `REPLACEMENT_INTENT_IMMUTABILITY_PASS` coverage retained
  by sibling tests `test_replacement_forecast_intent_immutability.py` + `test_replacement_forecast_reactor_hook.py`.
- source_rationale.yaml / test_topology.yaml / script_manifest.yaml: **0** refs to any deleted module (verified).

## FLAGGED for orchestrator / topology-doctor (not unilaterally changed)

1. `architecture/money_path_objects.yaml` states `REPLACEMENT_GO_LIVE_ROLLBACK_PLAN_MISSING_FLAG_UPDATES` and
   `REPLACEMENT_GO_LIVE_ROLLBACK_MAY_DELETE_SHADOW_ROWS` (lines ~452-453) are now ORPHANED — their emitting
   modules (go_live_report, rollback_plan) are deleted and have zero surviving refs in src/ or tests/. Removing
   money-path STATE definitions is a governance decision beyond "remove the dead code"; left for operator +
   topology_doctor. Marked inline with a removal comment.
2. The 3 orphaned analysis helpers in `scripts/replay_downloaded_replacement_economic.py`
   (`_empirical_q_lcb_coverage_for_rows`, `_nested_walk_forward_passed_for_rows`, `_q_lcb_source_counts`) are now
   unreachable; left in place (pure math, no deleted-module dependency).
3. `config_switch` live-AUTHORITY functions (`apply/build_replacement_forecast_live_authority_config_switch[_plan]`)
   are now referenced ONLY by `tests/test_replacement_forecast_config_switch.py` (their other callers — go_live_report
   + the deleted live-authority planner — are gone). config_switch is KEEP (operator live flag mutator) per plan;
   these live-authority mutators were NOT removed (out of scope; removing functions from a KEEP module + breaking
   its test is a separate decision).
4. Registries (source_rationale/test_topology/script_manifest) carry hundreds of PRE-EXISTING drift entries in this
   worktree — registry reconciliation is a deferred map-maintenance/closeout task for the orchestrator, not this
   focused change. This change adds NO new broken registry rows beyond the one money_path line fixed above.
