# Live Family/Qkernel Repair Plan - 2026-06-18

Status: active evidence for the live family selection and qkernel repair slice.
Scope: source, runtime decision, script artifact builder, and regression tests.
Runtime posture: no daemon restart from this slice.

## Objective

Fix the live decision defects that made weather family selection prefer adjacent NO exposure or
collapse optimized family intent instead of selecting the best capital-efficient executable claim.

## Required Repairs

- Family optimizer must use settlement-outcome probability for each bin. For `buy_no`, the
  optimizer outcome vector uses `1 - P(NO)` on the leg's own support.
- Optimizer-selected multi-leg portfolios must remain additive selected legs. Ranked fallback
  alternatives remain one-at-a-time execution candidates.
- q_lcb OOF reliability cells must be side-aware. YES grades "settled in this bin"; NO grades
  "settled outside this bin" using the NO complement lower-bound draw.
- Active q_lcb artifacts must not pass through missing cells. Artifact absent is inert; artifact
  present with missing/incompatible side-aware cell abstains.
- NO-on-modal may not bypass direction law from edge alone. It requires an active side-aware OOF
  license for the exact NO complement claim.
- qkernel direct selection must rank surviving candidates by robust utility density
  (`optimal_delta_u / optimal_stake_usd`) so capital-heavy adjacent NO substitutes cannot beat a
  better center YES solely by tying up more capital.
- qkernel proof overlay must keep selected-side probability fields separate from payoff-space
  selection economics.

## Verification

- Rebuild `state/qlcb_oof_reliability.json` with schema version 2 and side-aware cells.
- Add a Shanghai-style qkernel regression where a licensed, coherent center YES is selected over
  adjacent NO substitutes.
- Run targeted tests covering family optimizer, live execute fallback, q_lcb guard integration,
  qkernel bridge, and family decision selection.

## Non-Goals

- Do not restart live daemons.
- Do not manually close or mutate live positions.
- Do not implement a downstream ban on `buy_no`; fix the upstream probability, evidence, and
  utility-selection semantics.
