# BUG-CANONICAL-CLOSURE-TRACEABILITY

```yaml
work_packet_id: BUG-CANONICAL-CLOSURE-TRACEABILITY
packet_type: bugfix_packet
objective: Restore one truthful closure path so execution facts, outcome facts, and settlement legality stay durable and semantically aligned when positions move from exit attempts into settlement/closure.
why_this_now: BUG-BANKROLL-TRUTH-CONSISTENCY is closed. Fresh repo truth still shows the next highest-leverage end-to-end lifecycle loss on the close path: `log_execution_report()` and `log_settlement_event()` short-circuit on canonical-only substrates before emitting `execution_fact` / `outcome_fact`, while the harvester settlement path explicitly permits `pending_exit + backoff_exhausted` to settle even though `enter_settled_runtime_state()` does not legalize that transition. This keeps the discovered→bought→held→exited→settled trace from being durably complete and semantically lawful.
why_not_other_approach:
  - Patch execution_fact, outcome_fact, and settlement legality separately | would preserve one bottom-layer closure seam split across multiple local fixes
  - Jump directly to projection/query-hint cleanup | reader-shape cleanup matters, but durable close-path writes and legality must be truthful first
  - Widen into ETL/recalibration contamination now | separate external-data truth family, not the next close-path runtime seam
truth_layer: close-path truth must remain durable when canonical tables exist, and settlement legality for `backoff_exhausted` positions must match the lifecycle kernel instead of being silently special-cased by one module.
control_layer: limit the change to `src/state/db.py`, `src/execution/harvester.py`, `src/state/lifecycle_manager.py`, and packet-bounded tests that prove canonical-only present/absent behavior plus lawful settlement transitions. Do not widen into projection-query compatibility cleanup, control-plane work, migrations, or ETL contamination.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted closure-path pytest output, and a short closure-contract note describing present-path and absent-path semantics.
zones_touched:
  - K0_frozen_kernel
  - K2_runtime
invariants_touched:
  - INV-02
  - INV-03
  - INV-07
  - INV-08
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/governance/zeus_change_control_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - AGENTS.md
  - src/state/AGENTS.md
  - src/engine/AGENTS.md
  - docs/archives/sessions/session_2026_04_07_final_state.md
  - src/state/db.py
  - src/execution/harvester.py
  - src/state/lifecycle_manager.py
  - tests/test_db.py
  - tests/test_architecture_contracts.py
  - tests/test_runtime_guards.py
files_may_change:
  - work_packets/BUG-CANONICAL-CLOSURE-TRACEABILITY.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/state/db.py
  - src/execution/harvester.py
  - src/state/lifecycle_manager.py
  - tests/test_db.py
  - tests/test_architecture_contracts.py
  - tests/test_runtime_guards.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/control/**
  - src/supervisor_api/**
  - src/observability/**
  - src/riskguard/**
  - src/state/projection.py
  - src/state/portfolio.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_db.py::test_log_execution_report_emits_fill_telemetry tests/test_db.py::test_log_settlement_event_emits_durable_record tests/test_db.py::test_log_exit_retry_event_uses_backoff_exhausted_type
  - .venv/bin/pytest -q tests/test_architecture_contracts.py::test_log_execution_report_degrades_cleanly_on_canonical_bootstrap_db tests/test_architecture_contracts.py::test_log_settlement_event_degrades_cleanly_on_canonical_bootstrap_db tests/test_architecture_contracts.py::test_harvester_settlement_path_allows_backoff_exhausted_positions_to_settle
  - .venv/bin/pytest -q tests/test_runtime_guards.py::test_monitoring_skips_backoff_exhausted_chain_missing_until_settlement
parity_required: false
replay_required: false
rollback: Revert the close-path durability and lifecycle-legality edits together; repo returns to the current bankroll-truth boundary with canonical closure drift still explicitly open.
acceptance:
  - `log_execution_report()` and `log_settlement_event()` no longer silently skip durable closure facts when canonical surfaces are present
  - `backoff_exhausted` settlement handling is semantically aligned between harvester and the lifecycle kernel
  - targeted tests prove capability-present and capability-absent behavior without widening into projection or control-plane cleanup
  - targeted tests pass and the control surfaces record the packet boundary truthfully
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted closure-path pytest output
  - closure-contract note
```

## Notes

- This packet is the next K-level seam after bankroll truth: durable close-path traceability and lifecycle legality.
- Degraded-path rule for this packet: if canonical closure substrates are absent, the touched code must surface explicit skip/fail-loud semantics rather than silently implying durable completion.
- If implementation shows the fix requires projection-query compatibility cleanup, control-plane migration, or ETL/recalibration work, stop and freeze a new packet instead of widening this one.
- Closure-contract note:
  - present path: when canonical position tables exist and `execution_fact` / `outcome_fact` are available, `log_execution_report()` and `log_settlement_event()` must keep writing durable facts even if legacy runtime `position_events` is absent; only the legacy event append is skipped
  - absent path: when both legacy runtime event schema and canonical position surfaces are absent, the touched code still fails loud on missing legacy runtime schema; when fact tables are absent, `log_execution_fact()` / `log_outcome_fact()` continue to return explicit `skipped_missing_table`
  - settlement legality: `pending_exit` may fold to `settled` only for the bounded `backoff_exhausted` path; the harvester canonical phase-before must reflect that pending-exit truth instead of pretending the position stayed active
