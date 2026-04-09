# INTEGRATE-TRUTH-MAINLINE-WITH-DATA-EXPANSION

```yaml
work_packet_id: INTEGRATE-TRUTH-MAINLINE-WITH-DATA-EXPANSION
packet_type: integration_packet
objective: Preserve the accepted truth-repair mainline while integrating the current Architects data-expansion lane so expanded collection/calibration surfaces land without regressing closure, terminal-truth, or audit semantics.
why_this_now: The current live Architects worktree contains a real data-expansion lane that must be kept, but it is mixed with local regressions that would remove accepted truth-repair behavior if taken wholesale. The next lawful step is a bounded integration packet that ports the true expansion surfaces onto the accepted truth-repair tip and leaves unresolved expansion follow-up issues explicitly visible.
why_not_other_approach:
  - Keep truth and expansion on separate branches indefinitely | runtime scheduling, config, and ETL surfaces need one integrated mainline
  - Take the dirty Architects worktree wholesale | it would regress accepted truth seams in state/engine/execution paths
  - Rebuild the expansion lane from memory | the current dirty files already contain the intended expansion behavior and should be preserved directly where safe
truth_layer: truth-repair behavior in `src/state/**`, close-path canonical writes, and audited settlement payload semantics remain authoritative; expansion changes may extend data coverage and scheduling but may not delete or weaken accepted truth guarantees.
control_layer: restrict edits to integration control surfaces, expansion files, one scheduler/orchestration file, and one mixed runtime test file. Do not widen into fresh truth-path rewrites or unrelated strategy logic.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted ETL/runtime pytest output, and an integration note listing preserved expansion files plus explicit post-merge follow-up gaps.
zones_touched:
  - K1_governance
  - K2_runtime
  - K3_extension
invariants_touched:
  - INV-03
  - INV-08
  - INV-09
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
  - scripts/AGENTS.md
  - tests/AGENTS.md
  - src/engine/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - work_packets/REPAIR-CENTER-BUY-ULTRA-LOW-PRICE-TAIL-BETS.md
  - src/main.py
  - tests/test_runtime_guards.py
files_may_change:
  - work_packets/INTEGRATE-TRUTH-MAINLINE-WITH-DATA-EXPANSION.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - config/cities.json
  - src/main.py
  - scripts/etl_tigge_ens.py
  - src/data/observation_client.py
  - scripts/backfill_hourly_openmeteo.py
  - scripts/backfill_wu_daily_all.py
  - scripts/etl_tigge_direct_calibration.py
  - scripts/migrate_rainstorm_full.py
  - src/data/wu_daily_collector.py
  - tests/test_etl_recalibrate_chain.py
  - tests/test_runtime_guards.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/state/**
  - src/execution/**
  - src/engine/lifecycle_events.py
  - src/execution/exit_lifecycle.py
  - src/execution/harvester.py
  - src/engine/evaluator.py
  - tests/test_architecture_contracts.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_center_buy_*.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_observation_instants_etl.py tests/test_run_replay_cli.py tests/test_etl_recalibrate_chain.py
  - .venv/bin/pytest -q tests/test_runtime_guards.py -k 'chain_reconciliation_updates_live_position_from_chain or run_cycle_monitoring_uses_attached_shared_connection or exposure_gate_skips_new_entries_without_forcing_reduction or trade_and_no_trade_artifacts_carry_replay_reference_fields or execute_discovery_phase_logs_rejected_live_entry_telemetry or load_portfolio_prefers_position_current_when_projection_exists or load_portfolio_falls_back_to_json_when_projection_empty or load_portfolio_falls_back_to_json_when_legacy_events_are_newer_than_projection or execute_exit_paper_mode_dual_writes_economic_close_when_canonical_history_present'
parity_required: false
replay_required: false
rollback: Revert the integration commit(s) that port data-expansion files onto the truth-repair tip; this returns the branch to the accepted truth-repair boundary without the new expanded collection/scheduling surfaces.
acceptance:
  - accepted truth-repair behavior remains intact
  - data-expansion files are preserved on the integrated branch
  - `src/main.py` runs the expanded ETL/daily collection schedule without removing existing truth-safe subprocess behavior
  - `tests/test_runtime_guards.py` preserves both runtime-adaptation assertions and truth-repair economic-close assertions
  - explicit follow-up gaps created by the expansion are recorded without being silently papered over
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted ETL/runtime pytest output
  - integration note with preserved files and follow-up gaps
```

## Notes

- This packet is an integration slice, not a broad new feature packet.
- Preserve the data-expansion lane where it is additive; reject any hunk that weakens accepted truth-repair behavior.
- Known likely follow-up family: expansion coverage/proof gaps (for example TIGGE city-map coverage and new scheduler fan-out verification) if they remain after merge.

## Evidence log

- Pending implementation.
