# impl_132 — spine→legacy q_lcb overlay: map the spine's genuine robust lower bound into q-space

- Created: 2026-06-16
- Authority basis: live trace (158/174 `SUBMIT_ABORTED_PRICE_MOVED:recapture failed`),
  operator directive (Tier-0 fix, NOT the rejected band-aid), q-kernel spine bridge
  contract (`docs/rebuild/consult_review_pr409.md`).
- Branch: `live/iteration-2026-06-13` (main tree `/Users/leofitz/zeus`).
- Files touched (only): `src/engine/qkernel_spine_bridge.py::_overlay_spine_economics_onto_proof`
  and its test in `tests/integration/test_qkernel_spine_blockers_pr409.py`.

## Root cause (confirmed against the real code, not the task's stale line numbers)

The spine→legacy overlay `_overlay_spine_economics_onto_proof`
(`src/engine/qkernel_spine_bridge.py`, ~line 1130) restamps the proof's
`q_posterior` to the spine's **payoff-space** point fair value
`q_dot_payoff` (e.g. ~0.052 for a neg-risk buy_no) but, as it stood, **left
`q_lcb_5pct` at its original probability-space value** (~0.990) — the prior
comment explicitly chose to "keep the proof's own q_lcb_5pct". (Note: the
task brief described an older revision that *wrote* `q_lcb = q_dot_payoff`;
the live HEAD already differed — it left q_lcb UNSET. Both states produce the
same downstream abort.)

Downstream, `_native_side_candidate_from_proof`
(`src/engine/event_reactor_adapter.py:7218-7239`) reads:

```
q_point = float(proof.q_posterior)     # 0.052  (now payoff-space)
q_lcb   = float(proof.q_lcb_5pct)      # 0.990  (still probability-space)
if ... or q_lcb > q_point:             # 0.990 > 0.052  -> TRUE
    return NativeSideCandidate.no_trade(reason=Q_LCB_INVALID)
```

A `Q_LCB_INVALID` no-trade has `is_tradeable=False`, so Gate 1 in
`_evaluate_submit_recapture_for_selected`
(`src/engine/event_reactor_adapter.py:9322-9334`) routes to the missing-recapture
branch with `recaptured_cost_curve=None`, emitting the spurious
`SUBMIT_ABORTED_PRICE_MOVED: no fresh executable snapshot; fail closed (§13)`
that aborted every spine submit.

## The fix

In `_overlay_spine_economics_onto_proof`, after restamping `q_posterior =
q_dot_payoff`, also set:

```python
cost_value     = float(selected.cost.value)
edge_lcb_value = float(selected.edge_lcb)
new_q_lcb      = max(0.0, min(1.0, edge_lcb_value + cost_value))  # clamp01
overlay["q_lcb_5pct"] = new_q_lcb
```

(wrapped in a try/except so a typeless/partial economics leaves `q_lcb_5pct`
untouched rather than throwing).

## Verification of `edge_lcb` / `cost` units (required before implementing)

All confirmed by reading the real code, not assumed:

1. **`CandidateEconomics` exposes `edge_lcb: float` and `cost: ExecutionPrice`**
   — `src/decision/payoff_vector.py:226-257`. Access pattern `.cost.value` is
   the same one the bridge already uses at `qkernel_spine_bridge.py:881`
   (`float(c.cost.value)`).

2. **`cost` is the same all-in per-share cost the legacy edge/Kelly subtract.**
   `src/decision/family_decision_engine.py:872,883`:
   `cost = float(route.route_cost.avg_cost.value)` and
   `cost=route.route_cost.avg_cost`. `RouteCost.avg_cost` is a typed
   `ExecutionPrice` in `probability_units`, fee-applied (payoff_vector.py:322-329).
   The legacy binary Kelly subtracts the same all-in cost:
   `event_reactor_adapter.py:8879` — `f*_binary = (q_lcb − cost) / (1 − cost)`,
   "the all-in execution cost".

3. **`edge_lcb` and `point_ev` are both EV-minus-cost in the SAME units.**
   `src/decision/payoff_vector.py:298-319`:
   `edge_lcb = quantile(samples @ payoff - cost, alpha) = quantile(samples @ payoff) - cost`.
   `family_decision_engine.py:877`: `point_ev = q_dot - cost` where
   `q_dot = q @ payoff = q_dot_payoff`.

   Therefore:
   - `q_lcb := edge_lcb + cost = quantile(samples @ payoff)` — the robust LOWER
     BOUND of the payoff-space fair value (q-space), exactly the quantity
     `q_lcb_5pct` is supposed to carry.
   - `q_point = q_posterior = q_dot_payoff = point_ev + cost`.

4. **Legacy edge faithfully reproduces the spine's own `edge_lcb`.** The legacy
   pipeline computes `edge = q_lcb − all_in_cost`. With `q_lcb = edge_lcb + cost`
   and `all_in_cost == cost` (same basis), `edge = edge_lcb`. Binary Kelly then
   sizes on `f* = (q_lcb − cost)/(1 − cost) = edge_lcb/(1 − cost)` — the spine's
   ROBUST lower bound, never the point and never the raw probability. ✓

## Proof the robustness margin is preserved (q_lcb < q_point), NOT a clamp

Because the per-draw edge quantile is a lower bound of the per-draw fair value
and `edge_lcb ≤ point_ev` always:

```
q_lcb  = edge_lcb + cost
q_point = point_ev + cost
=> q_lcb ≤ q_point,  with STRICT inequality whenever edge_lcb < point_ev.
```

- Boundary case (`edge_lcb == point_ev`): `q_lcb == q_point`. This is the
  genuine zero-margin case, not a clamp — the spine itself reports no robust gap.
- Realistic case (`edge_lcb < point_ev`, the robust gap is real): `q_lcb < q_point`
  strictly. The robustness margin is RETAINED.

This is fundamentally different from the **rejected band-aid**
`q_lcb = min(orig, q_dot_payoff) = q_point`, which forced `q_lcb == q_point`
unconditionally (erasing the margin) and did exactly what the guard comment at
`event_reactor_adapter.py:7221-7227` forbids. The fix maps the spine's GENUINE
lower bound; it does not clamp to the point.

## Why it does not loosen any gate

- The `Q_LCB_INVALID` gate (`q_lcb > q_point` → no-trade) still fires for any
  truly inverted input; the fix supplies a *correct* (non-inverted) q_lcb, it
  does not bypass the check (the second case test proves `q_lcb < q_point`).
- No change to the 30s freshness window, the recapture engine, the family-rank
  gate, the coherence/edge gates, or any submit-forcing path. `q_posterior`,
  `trade_score`, `q_source`, and the executable identity (row/token/
  execution_price/native_quote_available) are untouched by this change.
- Sizing is on the robust lower bound (`edge_lcb/(1−cost)`), strictly
  conservative — never the point, never the raw probability.

## Diff (bridge)

`src/engine/qkernel_spine_bridge.py::_overlay_spine_economics_onto_proof`: the
prior "keep the proof's own q_lcb_5pct" comment is replaced; after the
`q_posterior`/`trade_score`/`q_source` overlay it now sets
`overlay["q_lcb_5pct"] = clamp01(selected.edge_lcb + selected.cost.value)`
inside a try/except that leaves q_lcb unchanged on a typeless economics.

## Tests (RED-on-revert)

Added to `tests/integration/test_qkernel_spine_blockers_pr409.py` (BLOCKER 5):

- `test_overlay_maps_spine_robust_lower_bound_into_q_lcb_negrisk_buy_no` —
  realistic neg-risk buy_no (edge_lcb=0.05, cost=0.002, q_dot_payoff=0.052,
  point_ev=0.050, original q_lcb_5pct=0.990). Asserts after overlay
  `q_posterior == 0.052`, `q_lcb_5pct == clamp01(edge_lcb+cost) == 0.052`,
  `q_lcb_5pct != 0.990`, and `q_lcb_5pct <= q_point` (the §13 gate now passes).
- `test_overlay_preserves_robustness_margin_when_edge_lcb_below_point_ev` —
  edge_lcb=0.03, point_ev=0.05, q_dot_payoff=0.052. Asserts
  `q_lcb_5pct == 0.032` and `q_lcb_5pct < q_posterior` (STRICT margin retained).
- `test_overlay_q_lcb_is_clamped_into_unit_interval` — edge_lcb+cost>1 clamps to 1.0.

### RED-on-revert evidence (proved both wrong implementations fail)

- Revert to **q_lcb left unset** (the live bug): all 3 new tests FAIL
  (`assert 0.99 == 0.052`/`1.0`).
- Revert to the **rejected clamp-to-point band-aid** (`q_lcb = min(orig, q_dot_payoff)`):
  `test_overlay_preserves_robustness_margin...` FAILS (q_lcb forced to q_point,
  margin erased) and the clamp01 test FAILS. The first (boundary) test passes
  there — which is exactly why the second, margin-strict, case is the
  load-bearing RED guard against the band-aid.
- Fixed version: all 3 GREEN.

## Test output (fixed)

```
$ .venv/bin/python -m pytest -q tests/integration/test_qkernel_spine_blockers_pr409.py -k overlay
3 passed, 10 deselected

$ .venv/bin/python -m pytest -q tests/integration/test_qkernel_spine_blockers_pr409.py
13 passed, 3 warnings

# all bridge-consuming integration tests
$ pytest -q test_qkernel_spine_blockers_pr409.py test_qkernel_spine_routing.py test_qkernel_spine_musigma_threading.py
22 passed, 7 warnings

# required money-path command
$ .venv/bin/python -m pytest -q tests/money_path/ tests/strategy/live_inference/ \
      tests/integration/test_qkernel_spine_blockers_pr409.py
357 passed, 3 warnings    # 0 failed
```

## Note on the task's stated "known pre-existing failures"

- `tests/money_path/test_finding_b_free_cash_bound.py` — the task expected 3
  pre-existing failures here; on current HEAD it **passes** (included in the 357).
  Those failures appear already resolved upstream; unrelated to this change.
- `tests/decision_kernel/test_certificate_ledger.py::test_ledger_rejects_no_submit_forecast_snapshot_causal_snapshot_mismatch`
  — FAILS on untouched HEAD (a regex-match drift: expected `source_truth.snapshot_id`,
  actual message `source_truth.derived_from_snapshot_id != forecast.snapshot_id`).
  This file is under `tests/decision_kernel/`, is NOT in the required money-path
  command's collection set, and is untouched by this change. Confirmed pre-existing.

## Guardrails honored

- PRESERVES the `q_lcb < q_point` margin (strict when edge_lcb < point_ev); NOT a
  clamp-to-point.
- Does NOT bypass any gate, widen the freshness window, or force a submit.
- Only `_overlay_spine_economics_onto_proof` and its test were edited.
- Not committed, not deployed, no daemon restart (orchestrator owns those).
