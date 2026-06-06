# Live-Cap No-Cap Regression Evidence

Date: 2026-06-05

This change-set resolves repeated review regressions around EDLI shadow/live-cap rules after the repo migration.

Current law:
- `tiny_live_notional_cap_enabled=false` means no configured notional cap and no non-configurable notional limit.
- `tiny_live_daily_order_cap_enabled=false` means no hidden order-count cap, including the rate-window slot table.
- Count limiting exists only on the explicit enabled path; that path uses a fixed 60s window rather than a calendar-date key.
- Shadow `edli_shadow_no_submit` must still emit submit-disabled would-trade evidence while never calling venue submit.
- Real-submit mode must fail closed if portfolio state is unavailable; shadow may continue observation.
- `production_n` is the ARM artifact production-cohort field; `gate_pass_n` is a deprecated compatibility alias.
- `edli_settlement_sigma_floor_enabled=true` requires a valid settlement sigma floor artifact and candidate cell.

Regression sources removed:
- Stale hard notional limit constants and tests.
- Stale comments and plans that claimed a non-configurable notional limit remained in force.
- Stale 2026-06-01/2026-06-02 audit language that described `$5`, `$185`, or
  canary count caps as current live law. Those documents are historical evidence
  only and now carry supersession notes pointing back to this file.
- Stale artifact naming that made production cohort counts look like diagnostic gate-pass counts.
- Fail-soft settlement sigma floor API use when the operator flag is enabled.

Verification targets:
- `tests/events/test_live_cap.py`
- `tests/events/test_live_cap_no_caps_directive.py`
- `tests/money_path/test_edli_live_canary.py`
- `tests/money_path/test_edli_online_invariants.py`
- `tests/calibration/test_settlement_sigma_floor.py`
- `tests/test_emos_sole_calibrator.py`
- `tests/engine/test_emos_seam_serve_loud.py`
- `tests/test_arm_gate_artifact_emit.py`
- `tests/events/test_arm_gate_artifact_boot_binding.py`
- `tests/test_arm_gate_emit_scheduler_job.py`
- `tests/events/test_reactor.py`
