# BUG-MONITOR-SHARED-CONNECTION-REPAIR

```yaml
work_packet_id: BUG-MONITOR-SHARED-CONNECTION-REPAIR
packet_type: bugfix_packet
objective: Give the monitoring / exit-context path an explicit trade+shared connection seam so it can read shared calibration / observation truth without silently falling back to the legacy monolithic connection, and so the known `monitor_incomplete_exit_context` gap can be reduced on positions that already have complete data.
why_this_now: Current repo truth still routes the cycle runner monitoring path through the legacy single-DB connection seam, while the live/session evidence shows `monitor_incomplete_exit_context` remaining non-zero for positions that should have complete exit authority. The session notes explicitly called out the question of whether monitoring uses a trade+shared connection or just the legacy seam.
why_not_other_approach:
  - Leave monitoring on the legacy connection and treat the incomplete-context count as acceptable | preserves the isolation/drift risk and keeps shared calibration truth hidden behind the old seam
  - Split this into separate packets for connection helper, runtime wiring, and tests | the seam change is small enough to remain one bounded non-destructive packet if kept inside the runtime/DB/test boundary
  - Fix bankroll semantics first | that is a different question and does not address the shared-truth access gap that drives the current incomplete-context evidence
truth_layer: The monitoring path should read trade truth plus shared world truth through an explicit attached connection seam; if the shared substrate is unavailable, the path must fail closed or skip loudly rather than pretend the legacy monolithic seam is equivalent.
control_layer: Limit the change to the runtime monitoring connection seam, the explicit shared-attachment helper if one is needed, and targeted regression tests. Do not widen into migration/cutover, RiskGuard policy, or bankroll redesign.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted runtime tests proving shared-connected monitoring behavior, explicit absent-path evidence, and a short connection-contract note.
zones_touched:
  - K1_governance
  - K2_runtime
invariants_touched:
  - INV-03
  - INV-05
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
  - .claude/CLAUDE.md
  - src/engine/AGENTS.md
  - src/state/AGENTS.md
  - docs/session_2026_04_07_final_state.md
  - docs/zeus_FINAL_spec.md
  - docs/isolation_design.md
  - src/engine/cycle_runner.py
  - src/engine/cycle_runtime.py
  - src/engine/monitor_refresh.py
  - src/state/db.py
  - tests/test_runtime_guards.py
  - tests/test_live_safety_invariants.py
  - tests/test_pnl_flow_and_audit.py
files_may_change:
  - work_packets/BUG-MONITOR-SHARED-CONNECTION-REPAIR.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/engine/cycle_runner.py
  - src/engine/cycle_runtime.py
  - src/engine/monitor_refresh.py
  - src/state/db.py
  - tests/test_runtime_guards.py
  - tests/test_live_safety_invariants.py
  - tests/test_pnl_flow_and_audit.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/control/**
  - src/execution/**
  - src/supervisor_api/**
  - src/state/portfolio.py
  - src/state/ledger.py
  - src/state/projection.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_runtime_guards.py -k exit_context
  - .venv/bin/pytest -q tests/test_live_safety_invariants.py -k exit_context
  - .venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k monitor
parity_required: false
replay_required: false
rollback: Revert the shared-connection helper / runtime wiring / targeted test changes together; the repo returns to the current single-DB monitoring seam and the incomplete-context gap remains explicitly open.
acceptance:
  - the monitoring path uses an explicit trade+shared connection seam rather than the legacy monolithic connection
  - the packet proves shared-present monitoring can resolve exit-context completeness for the known gap without inventing new truth
  - the packet proves the shared-absent path fails closed or skips loudly rather than silently pretending the old seam is equivalent
  - targeted tests pass and the control surfaces record the packet boundary truthfully
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted runtime pytest output
  - connection-contract note
```

## Notes

- This packet is intentionally narrower than a full runtime or migration rewrite.
- If the fix requires wider lifecycle or cutover changes, stop and freeze a new packet instead of widening this one.
- Team work inside this packet is allowed only as bounded read-review / implementation / verification slices under one owner.

## Evidence log

- work-packet grammar output: `python3 scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `python3 scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted runtime pytest output: `.venv/bin/pytest -q tests/test_runtime_guards.py -k "exit_context or monitor"` -> `12 passed, 68 deselected`; `.venv/bin/pytest -q tests/test_live_safety_invariants.py -k incomplete_context` -> `2 passed, 52 deselected`; `.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k monitor` -> `1 passed, 48 deselected`
- connection-contract note: `src/engine/cycle_runner.py` preserves the `get_connection` monkeypatch surface but now defaults it to `get_trade_connection_with_shared()`, and `src/state/db.py` exports one non-shadowed helper that opens the mode-specific trade DB then `ATTACH`es `ZEUS_SHARED_DB_PATH` as `shared`; `tests/test_runtime_guards.py::test_run_cycle_monitoring_uses_attached_shared_connection` and `::test_run_cycle_monitoring_fails_loudly_when_shared_seam_unavailable` cover shared-present and shared-absent behavior.
- independent pre-close critic artifact: `.omx/artifacts/gemini-bug-monitor-shared-connection-preclose-critic-20260408T011600Z.md` -> `APPROVE / no blockers`
- independent pre-close verifier artifact: `.omx/artifacts/claude-bug-monitor-shared-connection-preclose-verifier-20260408T011600Z.md` -> `SUFFICIENT`
- third-party post-close critic artifact: `.omx/artifacts/gemini-bug-monitor-shared-connection-postclose-critic-20260408T012006Z.md` -> `PASS`
- third-party post-close verifier artifact: `.omx/artifacts/claude-bug-monitor-shared-connection-postclose-verifier-20260408T012006Z.md` -> `PASS`
