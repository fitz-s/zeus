# REPAIR-RESIDUAL-STALE-GHOST-EXCLUSION

```yaml
work_packet_id: REPAIR-RESIDUAL-STALE-GHOST-EXCLUSION
packet_type: repair_packet
objective: Remove residual stale-open ghost rows from runtime read views so past-target canonical leftovers stop poisoning open exposure and loader truth after the trace-convergence repair.
why_this_now: REPAIR-POSITION-SETTLEMENT-TRACE-CONVERGENCE is accepted and its post-close gate passed. Fresh live truth on the repaired branch still shows one more bounded contradiction on the same bottom layer: `query_position_current_status_view()` still surfaces 9 past-target ghost rows (`rt1`, `trade-1`, `00e8b187-731`, `19a7116d-36c`, `511c16a6-27d`, `dab0ddb6-e7f`, `e6f0d01d-2a3`, `ea9f44ef-23e`, `f465b107-f88`) and `query_portfolio_loader_view()` still returns `stale_legacy_fallback` because those ghosts remain in open phases despite target dates already being past. This is now a smaller residual read-side seam, not the broader close-path packet anymore.
why_not_other_approach:
  - Jump to center_buy strategy diagnosis | strategy work still benefits from clean residual open-truth reads
  - Widen into broad historical backfill or DB cleanup scripts | the remaining contradiction is specifically in read-side ghost exclusion
  - Leave the ghosts as advisory only | they still contaminate open exposure counts and keep loader status degraded
truth_layer: runtime read views must not count past-target open-phase ghosts as live exposure when they have already aged beyond the target date without durable current support; the residual read path should converge to genuinely open positions only.
control_layer: limit the change to `src/state/db.py`, packet-bounded tests, and control surfaces. Do not widen into exit writers, ETL, risk/status/operator summaries, migrations, or historical backfill.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted ghost-exclusion pytest output, and a direct live SQL/JSON truth note showing pre-repair ghost count and post-repair open-view convergence.
zones_touched:
  - K2_runtime
invariants_touched:
  - INV-03
  - INV-07
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
  - tests/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - /tmp/zeus_session_note_reaudit/docs/session_2026_04_07_leftovers_reaudit.md
  - src/state/db.py
  - tests/test_pnl_flow_and_audit.py
  - scripts/verify_truth_surfaces.py
files_may_change:
  - work_packets/REPAIR-RESIDUAL-STALE-GHOST-EXCLUSION.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/state/db.py
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
  - src/state/portfolio.py
  - src/execution/**
  - src/engine/**
  - src/state/ledger.py
  - src/state/projection.py
  - tests/test_runtime_guards.py
  - tests/test_architecture_contracts.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'stale_ghost'
parity_required: false
replay_required: false
rollback: Revert the residual ghost-exclusion edits and paired tests/control-surface updates together; repo returns to the accepted trace-convergence boundary with past-target ghost rows still contaminating read views.
acceptance:
  - `query_position_current_status_view()` excludes past-target ghost rows from open exposure
  - `query_portfolio_loader_view()` no longer returns `stale_legacy_fallback` only because of those past-target ghosts
  - targeted tests pass and the control surfaces record the packet boundary truthfully
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted ghost-exclusion pytest output
  - direct live SQL/JSON truth note
```

## Notes

- This packet is deliberately narrower than the prior trace-convergence packet: residual read-side ghost exclusion only.
- Adversarial test requirement: include at least one test that seeds mixed rows where only past-target ghost positions should disappear while future-target legitimate positions remain visible.
- If implementation shows the fix needs historical cleanup scripts, exit-writer changes, or status/risk/operator changes, stop and freeze a new packet instead of widening this one.

## Evidence log

- work-packet grammar output: `.venv/bin/python scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted ghost-exclusion pytest output: `.venv/bin/pytest -q tests/test_pnl_flow_and_audit.py -k 'stale_ghost or excludes_past_target_ghost or ignores_past_target_stale_legacy_ghost'` -> `2 passed, 55 deselected`
- direct live SQL/JSON truth note:
  - pre-repair live probe with the accepted trace packet applied still showed `status_open_positions = 12` and `loader_status = stale_legacy_fallback`
  - residual ghost ids were `rt1`, `trade-1`, `00e8b187-731`, `19a7116d-36c`, `511c16a6-27d`, `dab0ddb6-e7f`, `e6f0d01d-2a3`, `ea9f44ef-23e`, `f465b107-f88`
  - post-repair live probe with patched code against the same state shows `status_open_positions = 3`, `status_open_trade_ids = ['52280711-260', 'b33ff595-3cb', 'c25e2bfe-769']`, and `loader_status = ok`
- independent pre-close critic artifact: `.omx/artifacts/claude-repair-residual-stale-ghost-preclose-critic-20260408T090115Z.md` -> `PASS`
- pre-close verifier artifact: `.omx/artifacts/claude-repair-residual-stale-ghost-preclose-verifier-20260408T090355Z.md` -> `PASS`
