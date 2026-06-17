# Created: 2026-06-14
# Authority basis: docs/rebuild/consult_build_spec.md lines 734-802 (Create
#   src/decision/payoff_vector.py), 1166-1184 (Stage 8) + the spec-vs-live drift ledger.

# Stage 8a — payoff_vector implementation report

## What was built

Stage 8a of the q-kernel rebuild: the decision-economics layer that replaces the scalar
`q − price` edge and the binary-Kelly notional with (1) an **Arrow-Debreu vector edge**
`edge = quantile(q_sample.payoff − cost, alpha)` and (2) **vector-argmax sizing**
`s* = argmax_s robust_delta_u(candidate, s)` over band samples + the existing
`FamilyPayoffMatrix` ΔU. The scalar `q − price` trade_score is demoted to telemetry that
**cannot select**.

GREENFIELD — NO live file was touched. The reactor scalar-Kelly seam
(`event_reactor_adapter.py:8632`) and the `trade_score` demotion happen at Stage 11
(integration). This module is pure objects + pure functions, wired into the reactor later
(Wave 5).

## Files written (all new)

- `src/decision/payoff_vector.py` — the module.
- `tests/decision/test_payoff_vector_edge.py` — edge RED-on-revert tests.
- `tests/decision/test_vector_sizing_authority.py` — sizing RED-on-revert tests.

Report: `docs/rebuild/impl_w4_payoff_vector.md` (this file).

## Symbols (exact field names per spec)

Dataclasses (frozen, spec-verbatim fields):
- `CandidateRoute` (spec 738-746): `candidate_id, instrument, route_cost, payoff_vector,
  side, bin_id` + `payoff_vector_hash()` (the spec-1184 receipt anchor).
- `CandidateEconomics` (spec 747-758): `candidate_id, point_ev, edge_lcb, delta_u_at_min,
  optimal_stake_usd, optimal_delta_u, q_dot_payoff, cost (ExecutionPrice), route_id`.

Functions:
- `point_fair_value(joint_q, payoff)` → `q @ payoff` (spec 764).
- `edge_lower_bound(band, payoff, cost, alpha=None)` →
  `np.quantile(band.samples @ payoff − cost, alpha)` (spec 769-770).
- `robust_delta_u(candidate, stake, *, band, omega, matrix, exposure, alpha=None)` →
  the alpha-quantile across band draws of the EXISTING `FamilyPayoffMatrix` ΔU at that
  stake (spec 785-789).
- `optimize_vector_stake(...)` → `(optimal_stake_usd, optimal_delta_u, delta_u_at_min)` —
  `s* = argmax_s robust_delta_u` (spec 791) by the SAME coarse-to-fine grid + stake bounds
  utility_ranker uses.
- `compute_candidate_economics(...)` → the full `CandidateEconomics` (edge + size).
- `live_candidate_passes(economics, candidate_route, *, direction_law_proof_present,
  market_coherence_accepted)` → the spec 797-802 AND of vector conditions
  (`edge_lcb > 0 AND delta_u_at_min > 0 AND optimal_delta_u > 0 AND executable route AND
  direction-law proof AND coherence accepted`). The scalar is NOT an input.
- `scalar_trade_score(joint_q, candidate_route)` → the demoted telemetry scalar
  `q @ payoff − cost`; logged, never read by the pass.
- `build_candidate_route(...)` — assembles a `CandidateRoute` whose `payoff_vector` IS
  `instrument.payoff_vector(omega)` (derived, never supplied).

## Spec lines implemented

- 738-746 `CandidateRoute`; 747-758 `CandidateEconomics`.
- 759-774 edge: `point_fair_value = q @ payoff`; `point_edge = point_fair_value −
  route.avg_cost.value`; `sample_edges = band.samples @ payoff − route.avg_cost.value`;
  `edge_lcb = np.quantile(sample_edges, alpha)`; the YES_i → `q_i − ask_yes_i`, NO_i →
  `(1 − q_i) − cost_not_i`, basket → real bundle reductions.
- 776-791 sizing: `robust_delta_u` over band samples + `FamilyPayoffMatrix` ΔU;
  `s* = argmax_s robust_delta_u`.
- 793-802 the live candidate pass (vector quantities only).
- 1166-1184 Stage 8 RED-on-revert test names + live signal (selected candidate has
  edge_lcb / point_ev / delta_u / optimal_stake / payoff_vector_hash; scalar q-price
  logged, not selected on).

## The two corrected transformations (operator law — bad output mathematically impossible)

1. **Edge is a vector dot product, not a scalar `q − price`.** The edge is only ever
   formed as `q @ payoff − cost` (point) and `quantile(samples @ payoff − cost, alpha)`
   (lcb), where `payoff` is the instrument's Arrow-Debreu vector over the complete Omega.
   There is no code path that forms `q_i − price` as the selection quantity. For a NO_i
   this gives `(1 − q_i) − cost` — the whole other-bin basket — which differs in sign and
   magnitude from the broken `q_i − cost`.
2. **Size is a vector ΔU argmax, not a binary `f*`.** The stake is
   `argmax_s robust_delta_u`, where `robust_delta_u` is the alpha-quantile across band
   draws of the EXISTING `FamilyPayoffMatrix` log-growth objective against the existing
   exposure. It is never `(q − c)/(1 − c) × bankroll`. A correlated existing position
   shrinks the size by the concavity of the log — by construction of the ΔU objective,
   not a cap applied afterward.

No gate/cap/clamp/haircut/sanity-check/shadow-flag catches a bad scalar and leaves a
broken scalar transform in place. The scalar is computed only as labelled telemetry
(`scalar_trade_score`) and is structurally excluded from `live_candidate_passes`.

## Drift resolved (toward the live type, per the ledger directive)

1. **ΔU machinery shape.** The spec sizing pseudocode (785-789) writes a bare
   `delta_u(candidate, stake, q_k, exposure)`. The LIVE ΔU objective
   (`src/strategy/utility_ranker.py`) is parameterized by a `FamilyPayoffMatrix`, a `pi`
   outcome-probability MAPPING (over every bin + `OUTSIDE_OUTCOME`), a
   `PortfolioExposureVector`, and a `NativeSideCandidate` (carrying the native
   `ExecutableCostCurve` the stake-sweep walks). **Resolution:** each band draw `q_k`
   (a coherent simplex row over the complete Omega) is converted to the live `pi` mapping
   by `_draw_to_pi` — each matrix bin's mass is read off the draw, and `OUTSIDE` absorbs
   the residual `1 − Σ_bins q_k` — the exact `pi` shape `robust_probabilities` produces,
   but from ONE coherent draw instead of per-bin q_lcb. The per-draw ΔU is the live
   `_delta_u_at_stake` at that `pi`, with `effective_outcome_pi` re-anchoring the NO side
   to its own bound (unchanged YES). So the vector sizing **reuses the existing ΔU code
   unchanged** and only varies the probability vector per draw (the robustness). Two
   private helpers are imported from `utility_ranker` (`_delta_u_at_stake`,
   `effective_outcome_pi`) plus the public `FamilyPayoffMatrix`, `PortfolioExposureVector`,
   `OUTSIDE_OUTCOME`.
2. **`CandidateEconomics.cost` type.** Spec line 756 types it `ExecutionPrice`; carried
   verbatim from `RouteCost.avg_cost` (a typed fee-adjusted probability-unit price). The
   edge subtracts `avg_cost.value` (the only cost term — no midpoint/last/complement,
   already forbidden at the route leaf).
3. **`alpha` default.** The spec writes `np.quantile(..., alpha)` without naming the
   source. Resolved to default to `band.alpha` (the lower tail the band was built at), so
   edge_lcb and robust ΔU are read at the same tail the band carries — coherent by default.

## Test results

Module tests — `tests/decision/test_payoff_vector_edge.py` +
`tests/decision/test_vector_sizing_authority.py`: **8 passed in 142.70s**.

Spec-named RED-on-revert tests (all present and green):
- `test_payoff_vector_edge.py::test_edge_is_q_dot_payoff_minus_route_cost`
- `test_payoff_vector_edge.py::test_scalar_q_minus_cost_cannot_select_candidate`
- `test_vector_sizing_authority.py::test_family_total_uses_vector_argmax_not_binary_kelly`
- `test_vector_sizing_authority.py::test_correlated_existing_position_reduces_delta_u_size`

Supporting tests: `test_point_fair_value_is_q_dot_payoff_yes_and_no`,
`test_edge_lcb_subtracts_cost_inside_the_quantile`,
`test_vector_positive_but_unexecutable_route_cannot_select`,
`test_no_trade_when_robust_delta_u_nonpositive`.

Each RED-on-revert test fails if the corrected transform is reverted:
- the NO edge sign-flips if `q_i − cost` replaces `(1 − q_i) − cost`;
- a scalar-positive / vector-negative candidate is admitted if the pass selects on the
  scalar;
- `s*` differs from `bankroll × f*` and genuinely maximizes robust ΔU;
- the size shrinks with a correlated existing position (a binary `f*` ignores exposure).

Money-path regression — `tests/money_path tests/strategy/live_inference`:
**331 passed in 4.16s** (money path unaffected; new files only).
