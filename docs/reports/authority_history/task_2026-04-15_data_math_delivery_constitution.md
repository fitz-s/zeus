# task_2026-04-15_data_math_delivery_constitution

Status: active for this packet only
Scope: Zeus data/math work on branch `data-improve`
Subordinate to:
- docs/authority/zeus_current_delivery.md
- docs/authority/zeus_current_architecture.md
- docs/authority/zeus_live_backtest_shadow_boundary.md
- AGENTS.md
- architecture/*.yaml manifests

## 1. Purpose

This constitution governs Zeus math/data work that touches calibration truth, replay evidence, or the live/backtest/shadow boundary.

It does not create a parallel architecture.
If this file conflicts with code truth or current authority docs, code truth and current authority docs win.

## 2. Primary path

Math-truth hardening before math expansion.

This packet exists to:
1. remove stale active authority that can misroute Codex,
2. preserve calibration lineage truth,
3. keep shadow metrics advisory-only,
4. keep replay diagnostic-only while making its limitation surface more honest,
5. prepare data-rebuild certification boundaries without running rebuilds.

## 3. Implement now

- Calibration lineage parity:
  - every harvested calibration pair must preserve decision-time truth,
  - `decision_group_id` must be explicit,
  - `bias_corrected` must be explicit,
  - authority lineage must remain `VERIFIED`-aware.

- Stale authority cleanup:
  - remove active paper-mode support claims,
  - remove stale replay limitation prose,
  - remove stale alpha-override route claims.

- Replay honesty surface:
  - report actual limitation states,
  - do not promote replay authority.

## 4. Instrument first

- `effective_sample_size.py`
- `blocked_oos.py`
- Day0 residual facts

These may surface advisory metrics only.
They are not live blockers in this packet.

## 5. Backtest-only sandbox

Replay fidelity and completeness improvements may proceed only as diagnostic work.
They may write to `zeus_backtest.db` only and must retain non-promotion semantics.

## 6. Defer but prepare interface

Prepare preflight / certification surfaces for:
- DST historical diurnal rebuild cleanup,
- future independent-group certification,
- future replay-promotion evidence doctrine.

Do not execute rebuilds in this packet.

## 7. Not now

Do not introduce:
- James–Stein live shrinkage
- hierarchical Bayes live routing
- regime-switch live gating
- conformal live blockers
- whole-cycle BH
- replay-driven live promotion
- new autonomy over live resume / widening strategy eligibility

## 8. Human gates

Human approval is required for:
- live cutover,
- schema migration,
- authority doc merge that changes repo law,
- shadow metric promotion to live blocker,
- historical rebuild execution,
- permanent control-plane policy change.

## 9. Stop conditions

Stop and escalate if:
- change crosses zones,
- change exceeds 4 files and is not pre-planned,
- targeted law tests go red,
- hidden coupling changes data semantics,
- a schema change is required,
- replay begins to imply promotion authority.

## 10. Exit criteria

This packet closes only when:
- stale active claims are corrected or explicitly marked stale,
- calibration lineage parity is tested,
- shadow metrics remain advisory-only,
- replay remains diagnostic_non_promotion and reports its actual limitation state,
- DST rebuild remains blocked from live certification until separately cleared.
