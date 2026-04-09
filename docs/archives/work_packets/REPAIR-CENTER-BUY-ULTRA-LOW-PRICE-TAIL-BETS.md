# REPAIR-CENTER-BUY-ULTRA-LOW-PRICE-TAIL-BETS

```yaml
work_packet_id: REPAIR-CENTER-BUY-ULTRA-LOW-PRICE-TAIL-BETS
packet_type: repair_packet
objective: Stop `center_buy` from entering the ultra-low-price `buy_yes` cohort that the accepted diagnosis isolated as the current settled-loss cluster.
why_this_now: DIAGNOSE-CENTER-BUY-FAILURE is accepted locally and its post-close gate passed. That diagnosis produced one clean first repair candidate: current `center_buy` paper losses are `8 trades / -9.0`, all are `buy_yes`, and all entered at `<= 0.02`. The next lawful step is a bounded strategy repair packet that rejects this ultra-low-price entry cohort before more behavior work is attempted.
why_not_other_approach:
  - Keep diagnosing forever | the loss cluster is now isolated enough to justify one bounded repair attempt
  - Widen into global yes-price rules | diagnosis is strategy-specific; do not change other strategies yet
  - Jump to complex probability/alpha rewrites | the failure cluster is currently most visible at entry-price gating, not posterior math
truth_layer: `center_buy` entry decisions should no longer admit the specific ultra-low-price `buy_yes` cohort that dominates current settled losses, while leaving other strategies and non-matching center_buy entries unchanged.
control_layer: limit the change to `src/engine/evaluator.py`, packet-bounded tests, and control surfaces. Do not widen into ETL, close-path truth, risk/status summaries, migrations, or other strategies.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted repair pytest output, and a short note showing the repaired rejection reason for the ultra-low-price cohort.
zones_touched:
  - K2_runtime
  - K3_extension
invariants_touched:
  - INV-03
  - INV-05
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
  - tests/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - work_packets/DIAGNOSE-CENTER-BUY-FAILURE.md
  - src/engine/evaluator.py
  - tests/test_center_buy_diagnosis.py
files_may_change:
  - work_packets/REPAIR-CENTER-BUY-ULTRA-LOW-PRICE-TAIL-BETS.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/engine/evaluator.py
  - tests/test_center_buy_repair.py
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
  - tests/test_runtime_guards.py
  - tests/test_architecture_contracts.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_center_buy_diagnosis.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_center_buy_repair.py
parity_required: false
replay_required: false
rollback: Revert the bounded `center_buy` entry-cohort guard, paired tests, and control-surface updates together; repo returns to the accepted diagnosis boundary with the ultra-low-price cohort still open.
acceptance:
  - `center_buy` rejects `buy_yes` entries in the diagnosed ultra-low-price cohort with an explicit rejection reason
  - non-center_buy strategies are unchanged
  - targeted tests pass and the control surfaces record the packet boundary truthfully
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted repair pytest output
  - center_buy repair note
```

## Notes

- This packet is intentionally narrow and hypothesis-driven: it only targets the diagnosed `center_buy` ultra-low-price `buy_yes` cohort.
- Adversarial test requirement: include one test that proves another strategy with the same low entry price is unaffected.
- If implementation shows the threshold is not stable enough or causes broader collateral behavior change, stop and freeze a revised packet instead of widening this one.

## Evidence log

- work-packet grammar output: `.venv/bin/python scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted repair pytest output: `.venv/bin/pytest -q tests/test_center_buy_repair.py` -> `2 passed`
- center_buy repair note:
  - `src/engine/evaluator.py` now applies `CENTER_BUY_ULTRA_LOW_PRICE_MAX_ENTRY = 0.02`
  - `center_buy` + `buy_yes` + `entry_price <= 0.02` now rejects with `MARKET_FILTER` and explicit reason `CENTER_BUY_ULTRA_LOW_PRICE(...)`
  - adversarial test proves `opening_inertia` with the same low entry price still trades
- independent pre-close critic artifact: `.omx/artifacts/claude-repair-center-buy-ultra-low-price-tail-bets-preclose-critic-20260408T095000Z.md` -> `PASS`
- pre-close verifier artifact: `.omx/artifacts/claude-repair-center-buy-ultra-low-price-tail-bets-preclose-verifier-20260408T095030Z.md` -> `PASS`
- post-close critic artifact: `.omx/artifacts/claude-repair-center-buy-ultra-low-price-tail-bets-postclose-critic-20260408T100200Z.md` -> `PASS`
- post-close verifier artifact: `.omx/artifacts/claude-repair-center-buy-ultra-low-price-tail-bets-postclose-verifier-20260408T100230Z.md` -> `PASS`

## Evidence log

- work-packet grammar output: `.venv/bin/python scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted repair pytest output: `.venv/bin/pytest -q tests/test_center_buy_repair.py` -> `2 passed`
- center_buy repair note:
  - `src/engine/evaluator.py` now applies `CENTER_BUY_ULTRA_LOW_PRICE_MAX_ENTRY = 0.02`
  - `center_buy` + `buy_yes` + `entry_price <= 0.02` now rejects with `MARKET_FILTER` and explicit reason `CENTER_BUY_ULTRA_LOW_PRICE(...)`
  - adversarial test proves `opening_inertia` with the same low entry price still trades
- independent pre-close critic artifact: `.omx/artifacts/claude-repair-center-buy-ultra-low-price-tail-bets-preclose-critic-20260408T095000Z.md` -> `PASS`
- pre-close verifier artifact: `.omx/artifacts/claude-repair-center-buy-ultra-low-price-tail-bets-preclose-verifier-20260408T095030Z.md` -> `PASS`
