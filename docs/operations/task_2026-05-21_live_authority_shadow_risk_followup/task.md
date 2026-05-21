# Task: Live Authority / Shadow / Risk Follow-up

Created: 2026-05-21
Last reused or audited: 2026-05-21
Authority basis: User secondary audit preserved in `analysis_live_authority_shadow_risk_followup.md`; baseline `origin/main` `a4707d1beb`.

## Operating Rule

Before starting any finding, reread the corresponding original section in `analysis_live_authority_shadow_risk_followup.md`. Do not mark a finding complete until the implementation and focused verification evidence are recorded here.

## Progress

| ID | Finding | Status | Evidence |
| --- | --- | --- | --- |
| F1 | Live legacy `ExecutionIntent` escape hatch | completed | `execute_intent()` hard-fails in live before runtime gate/client; `VenueAdapterExecutor` rejects legacy `ExecutionIntent`; tests: `test_execute_intent_legacy_entry_path_blocked_for_live`, `test_execute_intent_env_override_still_blocked_for_live`. |
| F2 | Post-SDK terminal rejection persistence failure can return `rejected` | completed | Entry/exit `success=False` and missing-order-id persistence failures now call `_mark_post_submit_persistence_failure()` and return `unknown_side_effect`; tests: entry/exit terminal rejection persistence fault injection. |
| F3 | Shadow candidates write `phase0_backfill` and fabricate `gamma_explicit` anchor | completed | Added `write_shadow_decision_event()`; all four shadow enter candidates use `source='shadow_decision'`; missing anchor becomes `unknown_legacy`; schema v25 admits the new source/anchor. |
| F4 | Candidate no-trade writer bypasses canonical schema guard | completed | World-DB candidate no-trade now routes through `write_no_trade_event()` with live schema guard; in-memory tests explicitly stamp `schema_compatibility='current'` and shadow provenance detail; `no_trade_events_schema` CHECK now accepts global schema v25. |
| F5 | Shoulder exposure ledger is post-submit fail-soft | completed | Minimal release-safe path: submit success + ledger write failure now auto-pauses entries via control plane and marks degraded; test locks `_freeze_entries_after_shoulder_ledger_failure()`. |
| F6 | Variance cluster exposure silently replaces gross heat | completed | Added `ClusterExposureResult(gross_heat, variance_heat, method, fallback_reason)`; float wrapper/evaluator use conservative `max(gross, variance)` policy heat and validation details. |
| F7 | Regime correlation matrix JSON lacks validation | completed | `RegimeCorrelationStore.fit()` and `.get()` validate uniqueness, square shape, dimension, finite values, diag=1, symmetry, bounds, and PSD; parametrized invalid-matrix tests added. |

## Verification Plan

- Relationship tests first for each boundary: legacy object -> live submit, SDK terminal result -> durable command truth, shadow candidate -> learning provenance, shoulder decision -> risk ledger, correlation matrix -> risk heat.
- Focused pytest: 39 relationship tests passed covering F1-F7.
- Broader related pytest: 149 passed across executor, command split, Phase 4 candidate provenance/no-trade, shoulder cluster/ledger, cluster exposure, and regime correlation store tests.
- Schema pin: `python3 scripts/check_schema_version.py` passed with SCHEMA_VERSION 25.
- Compile smoke: `python3 -m py_compile` passed for touched runtime modules.
- Topology: planning-lock and map-maintenance both passed after registering this packet in `docs/operations/AGENTS.md`.
