# PROVE: EDLI cost/fill economics — structural trade suppression

- Created: 2026-06-01
- Last reused/audited: 2026-06-01
- Authority basis: READ-ONLY adversarial proof. HEAD `6fcd05a69f`. Targets: `src/engine/event_reactor_adapter.py`, `src/strategy/live_inference/trade_score.py`, `src/strategy/live_inference/executable_cost.py`; design refs `docs/reference/zeus_execution_lifecycle_reference.md`, canonical `src/engine/evaluator.py`.
- Mode: no edits, no git, no DB writes. Live data read from `state/zeus-world.db` (no_trade_regret_events) + real book in `state/zeus_trades.db`.

## VERDICT: DEFECT (structural trade suppression confirmed)

The economics layer is **over-stated on cost via a double flat-stress stack**. The killer is a hardcoded **+0.01 `penalty`/`stress_penalty`** applied in `trade_score` *on top of* a `c_cost_95pct` that already adds **+1 tick** to the real executable cost. Net ≈ **2.0c of flat cost stress** straddles exactly where genuine-edge weather-derivative candidates live. `p_fill_lcb` is correctly sized-to-min-order (NOT against the full intended order) — that axis is sound. The fee model is the real Polymarket taker fee. So: cost OVER-stated (defect), p_fill correctly sized (correct), fee correct.

---

## AXIS 1 — DESIGN-FAITHFULNESS: reactor is a stripped/over-conservative substitute

Live trade_score (`trade_score.py:68-79`, fed by `event_reactor_adapter.py:4301-4323`):

```
robust_edge = min( q_5pct    - c_95pct  - penalty,
                   q_posterior - c_stress - stress_penalty )
score       = p_fill_lcb * robust_edge
```

At the EDLI call site (`event_reactor_adapter.py:4313-4322`) the arguments collapse:
- `c_95pct == c_stress == c_cost_95pct` (THE SAME VALUE passed to both legs)
- `penalty == stress_penalty == 0.01` (**hardcoded**, the only occurrence in src is `event_reactor_adapter.py:4320-4321`; not config-driven, not in the canonical evaluator)

And `c_cost_95pct = executable_cost.value + min_tick_size` (`event_reactor_adapter.py:4095`).

**Canonical design (`src/engine/evaluator.py`) does NOT do this.** It models cost-uncertainty as `σ_market` with bootstrap sampling `c_b ~ N(all_in, σ_market)` (`evaluator.py:312`) and gates on a **dollar floor** `min_expected_profit_usd` (default $0.05) scaled by `fill_probability` (`evaluator.py:1268-1395`), with size capped to depth-walked authority (`evaluator.py:1671-1687`). `evaluator.py:1602-1606` explicitly warns against a "double-haircut". The reactor replaced σ_market + dollar floor with a flat **(tick + 0.01 + 0.01)** additive stress — a stripped, structurally over-conservative substitute. **NOT design-faithful.**

## AXIS 2 — MATH-CORRECTNESS: wrong estimators

- `executable_cost` (`executable_cost.py:70-85`): VWMP book-walk over `shares` + taker fee. Correct marginal-cost estimator. **OK.**
- `c_cost_95pct` (`event_reactor_adapter.py:4095`): labelled "95th percentile" but is just `cost + 1 tick` — a fixed worst-case nudge, **not a percentile of any cost distribution**. Mislabeled, plausible-but-arbitrary.
- The `penalty`/`stress_penalty = 0.01`: a SECOND flat cost haircut with no statistical basis, redundant with the tick already in `c_cost_95pct`. **Double-penalty.**
- Because `c_95 == c_stress` and `penalty == stress_penalty`, both `min()` legs reduce to `min(q5, q) − c_cost_95pct − 0.01`. The "robust min of two distinct stress scenarios" is **structurally a no-op** — it is one cost with two stacked flat haircuts, not two estimators.

## AXIS 3 — VALUE-PROVENANCE: stress applied twice; p_fill against the right size

- **Stress applied twice:** +1 tick (into `c_cost_95pct`) **and** +0.01 flat (`penalty`). Confirmed double-count.
- **p_fill against the RIGHT size:** `shares = book.min_order_size` (`event_reactor_adapter.py:4092`), and `_p_fill_lcb_for_direction` (`:4218-4271`) computes a Wilson LCB on depth-coverage of that min-size order — NOT prob-of-filling-the-whole-$43-at-top-level. The operator's feared failure mode is ALREADY FIXED here (docstring `:4224-4228`). **p_fill axis is sound.**
- Fee = real Polymarket taker fee (`executable_cost.py:78` `with_taker_fee(fee_rate)`, rate from `fee_rate_fraction_from_details`). **Not over-estimated.**

---

## LIVE BOOK REPRODUCTION (decisive)

Real killed candidate — NYC buy_no, 56-57°F, target 2026-06-02, snapshot `ems2-082fc8cd8569f068a1b6e10f4d797020aa956015` (`state/zeus_trades.db`). NO-ask book top: 0.99×75, 0.98×39, 0.97×307…; min_order_size=5; tick=0.01.

| Quantity | Value | Source |
|---|---|---|
| `q_live` (posterior) | 0.94137 | stored |
| `q_lcb_5pct` | 1.0 | stored |
| real executable cost `c_fee_adjusted` (VWMP@5sh + fee) | **0.92736** | reproduced |
| `c_cost_95pct` (= cost + 1 tick) | 0.93736 | reproduced (=cfee+0.01) |
| `p_fill_lcb` (sized to 5sh) | 0.787 | stored, healthy |
| **raw edge** = q − real cost | **+1.40c** | genuine positive-edge |
| − tick stress (→c_cost_95pct) | +0.40c | |
| − 0.01 flat penalty | **−0.60c** | |
| `trade_score` (live) | **−0.004717** | reproduced EXACTLY = stored |

**True executable edge is +1.40c (a real buy_no). The 2.0c flat stress stack drives it to −0.6c → rejected as TRADE_SCORE_NON_POSITIVE.** A design-style single-cost gate (`q − c_fee_adjusted`) passes.

## POPULATION IMPACT (`state/zeus-world.db`, no_trade_regret_events)

- `TRADE_SCORE_NON_POSITIVE` is the **#1 rejection: 6691** (next: stale-trade 5879).
- Of native-quote killed (n=6710): **5792 (86%) recover positive edge if ONLY the hardcoded 0.01 flat `penalty` is dropped** (keeping the +1 tick c_cost_95pct).
- 5819 (87%) recover with zero flat stress (raw edge q−cfee).
- 77% are buy_no with q≤q5, so the `q` leg binds — the "robust min" never engages a second scenario.
- These are thin-edge (sub-2c) candidates: there are **0** wide (>2c) candidates among them. The 2c stack is precisely calibrated to erase the entire thin-edge book that weather-derivative NO-side trading depends on.

## DOUBLE-PENALTY, NAMED

`event_reactor_adapter.py:4317-4321` feeds `c_95pct = c_stress = c_cost_95pct` (already = real_cost + 1 tick) **and** `penalty = stress_penalty = 0.01`. Effective cost charged = `real_cost + tick + 0.01` per leg ≈ **real_cost + 2.0c**. The tick and the 0.01 are two independent flat haircuts for the same "execution uncertainty" the design models once via σ_market.

## WRONG-SIZE: NOT present
p_fill is computed against `min_order_size` and Wilson-LCB'd on that size's depth coverage (`:4092-4094`, `:4218-4271`). The operator's hypothesized "p_fill against full $43 at top level" defect does **not** exist at HEAD.

## RECOMMENDATIONS (read-only; for the fix session)
1. **Drop the hardcoded `penalty`/`stress_penalty = 0.01`** (`event_reactor_adapter.py:4320-4321`) — highest impact, recovers 86% of killed edge, low effort. It double-counts the tick already in `c_cost_95pct`.
2. Replace `c_cost_95pct = cost + tick` with the **VWMP-to-fill-size** cost the design intends, or a real cost-distribution percentile (σ_market) — medium effort; corrects the mislabel and the second stress layer.
3. If a single uncertainty budget is desired, port the canonical `σ_market` + `min_expected_profit_usd` dollar floor from `evaluator.py` so EDLI and the legacy path use one estimator — medium effort, removes the design split.
4. Keep p_fill sized-to-depth as-is (it is correct).

## REFERENCES
- `src/engine/event_reactor_adapter.py:4079-4096` — `_execution_price_from_snapshot`: cost + p_fill + c_cost_95pct (+1 tick).
- `src/engine/event_reactor_adapter.py:4301-4323` — feeds c_95==c_stress, penalty==stress_penalty==0.01.
- `src/engine/event_reactor_adapter.py:4218-4271` — p_fill_lcb sized to min_order (correct).
- `src/strategy/live_inference/trade_score.py:68-79` — robust_edge = min(...) − penalties.
- `src/strategy/live_inference/executable_cost.py:70-85` — VWMP + taker fee (correct cost kernel).
- `src/engine/evaluator.py:312, 1268-1395, 1602-1687` — canonical σ_market + dollar-floor + depth-cap economics (design reference).
- Live: `state/zeus-world.db::no_trade_regret_events` (6691 TRADE_SCORE_NON_POSITIVE), `state/zeus_trades.db` snapshot `ems2-082fc8cd…`.
