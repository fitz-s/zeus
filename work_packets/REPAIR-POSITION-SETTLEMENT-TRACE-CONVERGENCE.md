# REPAIR-POSITION-SETTLEMENT-TRACE-CONVERGENCE

```yaml
work_packet_id: REPAIR-POSITION-SETTLEMENT-TRACE-CONVERGENCE
packet_type: repair_packet
objective: Eliminate close-path trace loss between `position_current`, `positions-paper.json`, and `chronicle` so exited/settled positions stop remaining falsely open and settlement history carries durable `exit_price`.
why_this_now: VERIFY-ETL-RECALIBRATE-CONTAMINATION is accepted and its post-close gate passed. The next highest-priority leftover family is position/state/settlement trace convergence. Fresh live repo truth shows a concrete contradiction on this seam: `positions-paper.json` currently has 14 `recent_exits` whose trade_ids still remain open in `position_current` (5 `day0_window`, 9 `active`), and all 19 paper `chronicle` settlement rows are missing `exit_price`. This means the discovered→bought→exited→settled chain is still split across runtime/read surfaces.
why_not_other_approach:
  - Diagnose `center_buy` first | strategy diagnosis is downstream of lower-layer trace truth and would still read contaminated close-path state
  - Patch only reader filters or only write-side dual-write | reader-only would keep future corruption alive; write-only would leave current runtime truth contradicted by existing terminal trade_decisions rows
  - Widen into broad canonical migration cleanup | this packet should stay on the concrete close-path divergence, not reopen every stale seam
truth_layer: `position_current` open exposure, `positions-paper.json` runtime state, `trade_decisions` terminal statuses, `chronicle` settlement details, and canonical close-path events must stop contradicting each other on whether a position is still open and at what exit price it closed.
control_layer: limit the repair to close-path read/write seams in `src/state/db.py`, `src/state/portfolio.py`, `src/engine/lifecycle_events.py`, `src/execution/exit_lifecycle.py`, `src/execution/harvester.py`, and packet-bounded tests proving stale-open exclusion, future economic-close canonical updates, and chronicle `exit_price` durability. Do not widen into risk/status/operator summaries, migrations, ETL, or strategy diagnosis.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted trace-convergence pytest output, and a direct live SQL/JSON truth note showing pre-repair contradiction and post-repair convergence on the touched seam.
zones_touched:
  - K2_runtime
invariants_touched:
  - INV-01
  - INV-02
  - INV-03
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
  - tests/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - /tmp/zeus_session_note_reaudit/docs/session_2026_04_07_leftovers_reaudit.md
  - src/state/db.py
  - src/state/portfolio.py
  - src/engine/lifecycle_events.py
  - src/execution/exit_lifecycle.py
  - src/execution/harvester.py
  - src/state/chronicler.py
  - tests/test_runtime_guards.py
  - tests/test_architecture_contracts.py
  - tests/test_pnl_flow_and_audit.py
files_may_change:
  - work_packets/REPAIR-POSITION-SETTLEMENT-TRACE-CONVERGENCE.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/state/db.py
  - src/state/portfolio.py
  - src/engine/lifecycle_events.py
  - src/execution/exit_lifecycle.py
  - src/execution/harvester.py
  - tests/test_runtime_guards.py
  - tests/test_architecture_contracts.py
  - tests/test_pnl_flow_and_audit.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/control/**
  - src/observability/**
  - src/riskguard/**
  - src/supervisor_api/**
  - src/state/ledger.py
  - src/state/projection.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_architecture_contracts.py -k 'economic_close_builder or lifecycle_builder_module_exists or settlement_builder or harvester_settlement_path'
  - .venv/bin/pytest -q tests/test_runtime_guards.py -k 'load_portfolio or execute_exit or compute_economic_close or lifecycle_kernel'
  - .venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'exit_telemetry or query_position_current_status_view_excludes_terminal_trade_decision_rows or position_current_views_consult_legacy_terminal_trade_status_when_current_db_lags or harvester_settlement_chronicle_event_carries_exit_price'
parity_required: false
replay_required: false
rollback: Revert the trace-convergence edits and paired tests/control-surface updates together; repo returns to the current accepted ETL packet boundary with stale-open projections and chronicle `exit_price` loss still explicitly open.
acceptance:
  - close-path readers (`query_portfolio_loader_view` / open-status views) no longer surface positions as open when the latest durable terminal truth already says they exited or settled
  - future economic-close path updates canonical close-path truth on the touched seam instead of leaving `position_current` stranded at entry/open phases
  - harvester settlement chronicle writes now carry `exit_price`
  - targeted tests pass and the control surfaces record the packet boundary truthfully
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted trace-convergence pytest output
  - direct live SQL/JSON truth note
```

## Notes

- This packet stays on the concrete close-path seam only: stale-open projection exclusion, future economic-close canonical updates, and chronicle settlement `exit_price` durability.
- Degraded-path rule for this packet: if a canonical position has no prior event history, the touched code may skip append-first terminal dual-write only when that skip is explicit and does not pretend the position remained open.
- If implementation shows the fix needs migration scripts, broad historical backfill, or risk/status/operator rewrites, stop and freeze a new packet instead of widening this one.

## Evidence log

- work-packet grammar output: `.venv/bin/python scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted trace-convergence pytest output:
  - `.venv/bin/pytest -q tests/test_architecture_contracts.py -k 'economic_close_builder or lifecycle_builder_module_exists or settlement_builder or harvester_settlement_path'` -> `11 passed, 77 deselected`
  - `.venv/bin/pytest -q tests/test_runtime_guards.py -k 'load_portfolio or execute_exit or compute_economic_close or lifecycle_kernel'` -> `17 passed, 64 deselected`
  - `.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'exit_telemetry or query_position_current_status_view_excludes_terminal_trade_decision_rows or position_current_views_consult_legacy_terminal_trade_status_when_current_db_lags or harvester_settlement_chronicle_event_carries_exit_price'` -> `4 passed, 51 deselected`
- direct live SQL/JSON truth note:
  - pre-repair live inspection on `/Users/leofitz/.openclaw/workspace-venus/zeus/state` showed all 14 `positions-paper.json` `recent_exits` trade_ids still present in `position_current` open phases and all 19 paper `chronicle` settlement rows missing `exit_price`
  - post-repair reader probe with patched code against the same live state shows `recent_exit_trade_ids_still_open_after_reader_repair = 0`
  - post-repair `load_portfolio()` on the live `positions-paper.json` path returns `12` positions with state counts `{economically_closed: 9, entered: 3}` because `query_portfolio_loader_view()` now returns `stale_legacy_fallback` and cleanly falls back to JSON rather than surfacing contradicted canonical-open rows
- independent pre-close critic artifact: `.omx/artifacts/claude-repair-position-settlement-trace-preclose-critic-20260408T084208Z.md` -> `PASS`
- pre-close verifier artifact: `.omx/artifacts/claude-repair-position-settlement-trace-preclose-verifier-20260408T084558Z.md` -> `PASS`
