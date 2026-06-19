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

## Current Context Snapshot - 2026-06-19

Status: PR #412 pushed and CI-green at `00c8f24d09707d672ae2edb79f46351d2fa733aa`.
Runtime posture remains unchanged: no daemon restart from this slice.

### Implemented in latest PR #412 head

- qLCB licensed deflection no longer pairs a guarded edge with stale pre-guard ΔU/stake.
- `compute_candidate_economics`, `optimize_vector_stake`, and `robust_delta_u` accept a
  candidate-local `guarded_payoff_q_lcb`.
- Guarded economics uses a candidate-local payoff distribution:
  - `YES_i`: own-bin win mass is `q_safe`; loss mass is distributed over non-own outcomes.
  - `NO_i`: own-bin loss mass is `1 - q_safe`; win mass is distributed over non-own outcomes.
- The global `JointQBand` and point q / mu are not mutated or reserialized as guard evidence.
- `FamilyDecisionEngine._apply_qlcb_reliability_guard` now recomputes edge, robust ΔU, and
  stake on licensed deflection using the same guarded payoff lower bound.
- Thin/missing/failing active guard cells remain fail-closed abstains.
- Family selection objective is explicit:
  - default live objective: `utility_density = optimal_delta_u / optimal_stake_usd`.
  - optional non-default objective: `total_delta_u`, only when explicitly constructed.
  - live qkernel bridge does not pass `total_delta_u`.

### Verification completed

- Local syntax and targeted tests:
  - `python3 -m py_compile src/decision/payoff_vector.py src/decision/family_decision_engine.py tests/decision/test_payoff_vector_edge.py tests/decision/test_family_decision_engine.py`
  - `/Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/decision/test_payoff_vector_edge.py tests/decision/test_family_decision_engine.py tests/decision/test_qlcb_guard_decision_integration.py tests/decision/test_qlcb_reliability_guard.py tests/integration/test_qkernel_spine_blockers_pr409.py tests/integration/test_qkernel_spine_routing.py`
  - Result: `53 passed, 10 warnings`.
- Local topology gates passed:
  - `git diff --check`
  - `python3 scripts/topology_doctor.py --task-boot-profiles`
  - `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...`
  - `python3 scripts/topology_doctor.py --planning-lock --plan-evidence docs/operations/current/plans/live_family_qkernel_repair_2026-06-18.md --changed-files ...`
- GitHub checks passed on PR #412:
  - `selected-relationship-tests`
  - `money-path-integration`
  - `static-semantic`
  - `money-path-release-gate (required)`
  - `replay-correctness-gate`
  - `topology-context (required, no_override)`
  - `gitleaks (REQUIRED)`
  - `full-pytest-sweep (ADVISORY - Phase 1)`
- PR #412 merge state is `CLEAN`; Copilot review threads are resolved/outdated.
- Shanghai-style probe with active side-aware OOF guard and settlement sigma-floor selected
  `buy_yes` on center `20C` at `0.27`; adjacent NO substitutes had negative robust ΔU and
  stake `0`.

### Known verification gaps / do not overclaim

- `python3 scripts/topology_doctor.py --core-claims --json` currently raises a checker
  `AttributeError` for missing `_locator_exists`.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` reports pre-existing registry
  drift unrelated to this repair: missing low-backfill proof files and unknown task classes.
- Pro consult re-review was manually recovered from the ChatGPT page because the detached
  waiter did not write the answer file. The recovered answer is at
  `/tmp/cgc_answer_REQ-20260619-011724-7e3m5hgy.txt`.
  - request: `REQ-20260619-011724-7e3m5hgy`
  - thread: `https://chatgpt.com/g/g-p-6a2990f77bdc81919f9702e3cb6ae20d-claude-code/c/6a34df05-bc6c-83ea-ab80-5bf8021196fb`
  - verdict: NO-GO until guarded qkernel execution economics flow into submit sizing.

### Pro NO-GO repair - 2026-06-19

Review blocker: qLCB guarded economics were repaired inside selection, but bridge overlay
preserved proof `q_posterior` / `q_lcb_5pct` and downstream submit sizing could rebuild stake
from those unguarded proof fields. That made the repair selection-local rather than
execution-complete.

Repair implemented locally:

- `_CandidateProof` now carries `qkernel_execution_economics`, separate from receipt-facing
  posterior fields.
- `qkernel_spine_bridge._overlay_spine_economics_onto_proof` writes a guarded execution
  certificate with `payoff_q_lcb`, `edge_lcb`, `optimal_delta_u`, `optimal_stake_usd`,
  `route_id`, selected candidate id, and qLCB guard provenance.
- `event_reactor_adapter._native_side_candidate_from_proof` uses the certificate's guarded
  payoff qLCB for qkernel execution materialization without mutating `q_posterior` /
  `q_lcb_5pct`.
- `event_reactor_adapter._robust_marginal_utility_stake_and_price` consumes qkernel execution
  economics directly when present, applies the existing fractional/concentration/free-cash and
  venue-min-order bounds, reprices on the selected native cost curve, and does not rebuild
  stake from unguarded proof qLCB.
- Regression added: a qkernel-selected proof with unguarded `q_lcb_5pct=0.90` and guarded
  `payoff_q_lcb=0.30` submits at the guarded `optimal_stake_usd=6.25`, while the unguarded
  proof would size above `100`.

Verification completed after this repair:

- `python3 -m py_compile src/engine/event_reactor_adapter.py src/engine/qkernel_spine_bridge.py tests/engine/test_s5_chosen_stake_execution_price.py tests/integration/test_qkernel_spine_blockers_pr409.py`
- `/Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/engine/test_s5_chosen_stake_execution_price.py::test_qkernel_execution_certificate_bounds_submit_sizing tests/integration/test_qkernel_spine_blockers_pr409.py::test_overlay_preserves_probability_fields_and_updates_score`
  - Result: `2 passed`.
- `/Users/leofitz/zeus/.venv/bin/python -m pytest -q tests/engine/test_s5_chosen_stake_execution_price.py tests/engine/test_single_application_kelly.py tests/integration/test_qkernel_spine_blockers_pr409.py tests/integration/test_qkernel_spine_routing.py tests/decision/test_family_decision_engine.py tests/decision/test_payoff_vector_edge.py tests/decision/test_qlcb_guard_decision_integration.py tests/decision/test_qlcb_reliability_guard.py`
  - Result: `76 passed, 10 warnings`.
- `git diff --check` passed.

### Live runtime re-alignment snapshot - 2026-06-19 06:30 UTC

This is a separate current-fact line from PR #412. Do not use it to claim the qkernel
math repair is deployed, and do not use the qkernel PR state to claim live runtime is
healthy.

- Live root: `/Users/leofitz/zeus`.
- Loaded live SHA file: `state/loaded_sha.json` generated at `2026-06-19T00:28:32Z`,
  git head `41cb8891ae...`.
- Trading daemon projections are stale:
  - `state/daemon-heartbeat.json`: `2026-06-19T00:40:38Z`.
  - `state/status_summary.json`: `2026-06-19T00:41:23Z`.
  - `state/live_health_composite.json`: `DEGRADED`, computed `2026-06-19T00:53:35Z`.
- Sidecars are not the same fact as live trading:
  - `com.zeus.forecast-live` is running and `state/forecast-live-heartbeat.json` was fresh
    at about `2026-06-19T06:29Z`.
  - `com.zeus.data-ingest`, `com.zeus.riskguard-live`, and `com.zeus.venue-heartbeat`
    were present in `launchctl list`.
  - `com.zeus.live-trading` was not present in `launchctl list`.
- `launchctl print gui/$(id -u)/com.zeus.live-trading` returned "Could not find service",
  while `launchctl print-disabled gui/$(id -u)` shows the label enabled. The plist exists
  with `RunAtLoad=true`, `KeepAlive=true`, and command `.venv/bin/python -m src.main`.
- macOS launchd log for `com.zeus.live-trading` shows repeated `service inactive` and
  `removing service` entries, with the final observed removal at local `2026-06-18 19:41:25`
  after live logs recorded SIGTERM for pid `58136`.
- Canonical trade DB writes for position/order monitoring stopped around the same time:
  - `position_events` latest `MONITOR_REFRESHED`: `2026-06-19T00:41:20Z`.
  - `venue_commands` latest update: cancel at `2026-06-19T00:41:03Z`.
  - `position_current` still has active/day0/pending positions, but their monitor fields are
    timestamped at about `2026-06-19T00:41Z`.
- Forecast DB continued separately after that:
  - `forecast_posteriors` latest `openmeteo_ecmwf_ifs9_bayes_fusion` rows were recorded
    after `2026-06-19T06:03Z`.

Runtime implication: the current "no continuous redecision" symptom is not explained by
forecast materialization alone. The trading/monitoring daemon is not loaded/running, while
forecast and ingest sidecars continue to update. Treat this as an automation/launch
orchestration break before any live restart claim.

### Next continuation tasks

1. Read Pro consult result when the watcher returns. Treat it as advisory, then verify any
   claimed blocker locally against source, CI, and tests before editing.
2. If Pro review finds a material qkernel/math/live-wiring defect, implement one focused
   repair batch on PR #412, rerun the same qkernel/decision/topology gates, commit, push, and
   re-monitor required checks.
3. If Pro review is GO / no material blockers, keep PR #412 merge-ready and prepare the next
   live-runtime observation pass without restarting daemons from this slice.
4. Next live-runtime observation pass must focus on continuous redecision behavior after code
   deployment:
   - first prove `com.zeus.live-trading` is loaded/running under launchd, not merely enabled
     or represented by stale JSON.
   - held positions enter redecision only when they have trade value or active holding risk.
   - candidates enter redecision when there is executable trade value, not every closed or
     inactive event.
   - new entry and held-position decisions must surface current timestamped belief, price,
     route, stake, and reason evidence.
   - verify buy_yes is admitted when center-bin YES dominates adjacent NO substitutes; do not
     convert this into a downstream `buy_no` ban.
5. Do not restart live daemon or manually mutate positions until the operator asks for the
   runtime pass and the restart preflight/current live DB surfaces are rechecked.
