# DIAGNOSE-CENTER-BUY-FAILURE

```yaml
work_packet_id: DIAGNOSE-CENTER-BUY-FAILURE
packet_type: diagnosis_packet
objective: Produce a reproducible, strategy-isolated diagnosis of why `center_buy` is currently losing in paper mode, using one truthful aggregation path instead of mixed ad hoc queries.
why_this_now: The lower-layer ETL and close-path truth seams have been repaired enough for strategy diagnosis to become meaningful. Fresh live truth still shows `center_buy` settled performance at `8 trades / -9.0`, while other surfaces such as `trade_decisions` can tell a very different story unless deduped and filtered carefully. The next useful step is not another runtime mutation but a bounded diagnosis packet that pins down whether the losses come from settlement outcomes, unresolved ghosts, entry selection, or duplicate-reader distortion.
why_not_other_approach:
  - Jump straight into another strategy repair | would be premature without a reproducible diagnosis surface
  - Keep using one-off SQL snippets in chat | too easy to mix duplicate rows, mismatched statuses, or other strategies
  - Widen into broad reporting/dashboard work | diagnosis should stay bounded and scriptable first
truth_layer: `center_buy` diagnosis must come from a reproducible, strategy-isolated read path that clearly distinguishes settled `outcome_fact` truth from deduped latest `trade_decisions` status and any ghost/unresolved cohorts.
control_layer: limit the change to a read-only diagnostic script, packet-bounded tests, and control surfaces. Do not widen into runtime logic, risk/status/operator summaries, migrations, or strategy behavior changes.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted diagnosis pytest output, and a short diagnosis note or script output explaining the current center_buy loss structure.
zones_touched:
  - K2_runtime
  - K3_extension
invariants_touched:
  - INV-03
  - INV-06
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
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - /tmp/zeus_session_note_reaudit/docs/session_2026_04_07_leftovers_reaudit.md
  - src/state/db.py
  - scripts/profit_validation_replay.py
  - tests/test_pnl_flow_and_audit.py
files_may_change:
  - work_packets/DIAGNOSE-CENTER-BUY-FAILURE.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - scripts/diagnose_center_buy_failure.py
  - tests/test_center_buy_diagnosis.py
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
  - src/state/**
  - src/execution/**
  - src/engine/**
  - tests/test_runtime_guards.py
  - tests/test_architecture_contracts.py
  - tests/test_pnl_flow_and_audit.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_center_buy_diagnosis.py
parity_required: false
replay_required: false
rollback: Revert the diagnosis script, its tests, and paired control-surface updates together; repo returns to the accepted residual-ghost boundary without a reproducible center_buy diagnosis surface.
acceptance:
  - there is one packet-bounded script that reports `center_buy` settled truth, latest trade_decision status truth, and ghost/unresolved cohorts without mixing other strategies
  - adversarial tests prove the script ignores non-center_buy rows and dedupes duplicate trade_decisions correctly
  - targeted tests pass and the control surfaces record the packet boundary truthfully
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted diagnosis pytest output
  - center_buy diagnosis note
```

## Notes

- This packet is diagnosis-only: it should clarify the current failure shape, not change strategy behavior.
- Adversarial test requirement: include a mixed-strategy / duplicate-trade_decision fixture so the diagnosis path proves it does not overcount or attribute other strategies to `center_buy`.
- If diagnosis reveals a concrete strategy bug worth fixing, freeze a separate repair packet instead of widening this one.

## Evidence log

- work-packet grammar output: `.venv/bin/python scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted diagnosis pytest output: `.venv/bin/pytest -q tests/test_center_buy_diagnosis.py` -> `2 passed`
- center_buy diagnosis note:
  - live script output on `state/zeus-paper.db` shows `settled_summary.count = 8`, `pnl_total = -9.0`, `win_count = 0`, `loss_count = 8`
  - all settled losers are `buy_yes`
  - entry-price buckets are `{<=0.01: 6, <=0.02: 2}`
  - latest trade-decision statuses for those settled rows are `{day0_window: 6, exited: 2}`
  - legacy event summary still shows `ORDER_REJECTED = 7`
  - the emitted hypotheses are `all_settled_losses_are_ultra_low_price_tail_bets` and `rejection_path_exists_and_should_be_separated_from_settlement_truth`
- independent pre-close critic artifact: `.omx/artifacts/claude-diagnose-center-buy-failure-preclose-critic-20260408T091000Z.md` -> `PASS`
- pre-close verifier artifact: `.omx/artifacts/claude-diagnose-center-buy-failure-preclose-verifier-20260408T091530Z.md` -> `PASS`
