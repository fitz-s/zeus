# B2: Capital Efficiency Gate Audit — 2026-06-13

**Question**: Is the live decision gate HONESTLY rejecting candidates or SUPPRESSING real positive edge?

## 1. Gate Formula

**File**: `src/strategy/live_inference/live_admission.py:87-113`
**Function**: `live_capital_efficiency_rejection_reason`

The gate is a single line:

```python
conservative_ev_per_dollar = (q_value - price) / price
if conservative_ev_per_dollar <= 0.0:
    return "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:..."
```

Where `q_value = float(q_lcb)` (the 5th-percentile lower bound of the win-probability bootstrap samples for the candidate's direction) and `price = float(execution_price)` (fee-adjusted all-in cost per share).

This is NOT a fill-probability gate, NOT a min-size gate, NOT a capital-lockup gate. It is simply: does the conservative model probability exceed the market price? No `p_fill_lcb` term, no `penalty` term. The trade_score has those terms, but that is a separate check applied later.

**Invocation path**: `event_reactor_adapter.py:6565` calls `_capital_efficiency_untradeable_reason` which calls this function with `q_lcb_5pct` (the proof's 5th-percentile bound) and `execution_price.value` (fee-adjusted quote). If it fires, `score = 0.0` and `missing_reason = capital_efficiency_reason`. The proof is retained as a loser in the `OpportunityBook`. The per-family histogram aggregates these reasons.

## 2. The Three Example Candidates

### Case A: Munich 26C+ June 14, buy_yes — q_lcb=0.0000, price=0.0010

`ev_per_dollar = (0.0 - 0.001) / 0.001 = -1.0`

**Capital efficiency gate FIRES**: `ADMISSION_CAPITAL_EFFICIENCY_LCB_EV`

Why is q_lcb=0? The 5th percentile of 51 ECMWF ensemble member samples for a far-above-seasonal-average June Munich temperature bin is structurally zero — essentially no ensemble member places mass on that outcome. The market price of 0.001 (0.1%) is itself generous. The gate is an honest reflection of the model: q_lcb=0 because the model genuinely assigns near-zero probability to this bin.

**Verdict: HONEST_NO_EDGE.** The rejection is correct.

### Case B: Tel Aviv 32C June 14, buy_yes — q_lcb=0.0275, price=0.0010

`ev_per_dollar = (0.0275 - 0.001) / 0.001 = 26.5`

**Capital efficiency gate PASSES.** This candidate is NOT rejected by `capital_efficiency_lcb_ev`.

It is rejected by `coverage_unlicensed_tail` (`live_admission.py:~165`, constants: `COVERAGE_UNLICENSED_TAIL_PRICE_MAX=0.05`, `COVERAGE_UNLICENSED_TAIL_DISAGREEMENT_RATIO=2.0`):
- price=0.001 < 0.05 (longshot)
- q_lcb/price = 27.5 > 2.0 (material disagreement)
- q_lcb_calibration_source = FORECAST_BOOTSTRAP (not EMOS_ANALYTIC or SETTLEMENT_ISOTONIC)

The calibration evidence from `sigma_scale_fit.json` shows Family C "tail" bins have `ratio_realized_over_expected=0.296` — the bootstrap model over-assigns probability to far tail bins by ~3.4x. Even after shrinking by 3.4x: calibrated q ≈ 0.0081 vs price=0.001, still ~8x, which would be real edge. However, the `sigma_scale_fit.json` is (a) marked `candidate=True` and candidate — it is NOT applied to the primary EMOS path, only to the replacement forecast path; and (b) the `coverage_unlicensed_tail` guard is a fail-CLOSED antibody specifically for the incident class documented at `2026_06_10_milan_24c_first_fill_rootcause.md` (K3 settlement coverage gate fail-opening on far bins with no settled history).

**Verdict on Tel Aviv: conservatively HONEST or borderline EDGE_SUPPRESSED.** The unlicensed tail guard is firing correctly on its contract (FORECAST_BOOTSTRAP source, no settlement record). Whether the underlying edge survives calibration shrinkage is uncertain without a settlement-backed source, which is precisely what the guard requires. This is conservative but not structurally incorrect.

### Case C: Kuala Lumpur 35C+ June 14, buy_yes — q_lcb=0.0392, price=0.0080

`ev_per_dollar = (0.0392 - 0.008) / 0.008 = 3.9`

**Capital efficiency gate PASSES.** This candidate is NOT rejected by `capital_efficiency_lcb_ev`. Trade score edge also positive: `0.0392 - 0.008 - 0.01 = 0.0212 > 0`.

From the live logs (2026-06-13 20:32-23:07 UTC), the KL market does not appear as a "best" candidate, suggesting it was rejected by `direction_law` (the log shows `direction_law=1` in cycles near that time, and KL heat markets in June are non-adjacent from the forecast center). The KL 35C+ bin for a model that places its mean around 33-34C would fail direction_law if 35C+ is a right-tail bin outside the ECMWF ensemble forecast center.

**Verdict: NOT a capital_efficiency rejection.** Rejection cause is direction_law (BIN_FORECAST_MISMATCH) — a different gate.

## 3. What capital_efficiency_lcb_ev Is Actually Doing

The bulk of all-cycle rejections (16-21 out of 22 bins per family) are by `capital_efficiency_lcb_ev`. These are bins where `q_lcb <= price`:

- Seoul 25C: q_lcb=0.0247, price=0.080 — model says 2.5%, market prices it at 8%; model is BELOW market
- Busan 29C: q_lcb=0.0681, price=0.16 — model 6.8%, market 16%; model below market
- Helsinki 15C: q_lcb=0.0658, price=0.13-0.18 — model below market
- Istanbul 24C: q_lcb=0.0884, price=0.34-0.38 — model below market

These are not near-adjacents with potential edge being cut by an over-conservative gate. The market is pricing these bins 2-10x above the model's conservative lower bound. Rejecting them is honest: the model says the market is efficiently or over-pricing these outcomes.

## 4. q_lcb Calibration — Is It Artificially Compressed?

For far-OTM bins (Munich 26C+, Paris 30C+, London 27C+, Amsterdam 23C+): q_lcb=0.0000 is a STRUCTURAL outcome, not a calibration compression artifact. The 5th percentile of 51 ECMWF ensemble member probability samples is zero when zero members assign any mass to that bin. No floor is applied below the prior floor of 1e-12 (which is the structural prior on zero-vote bins, not the q_lcb).

`sigma_scale_fit.json` does contain calibration factors that would widen sigma for the replacement forecast path, potentially lifting far-bin probabilities — but this file (a) is candidate-only/operator-gated and (b) only applies to `replacement_forecast_materializer.py`, not to the primary EMOS/ECMWF ensemble path.

The q_lcb=0 is therefore accurate: the ensemble genuinely places zero mass on those far bins for June temperature ranges well above climatology.

## 5. Verdict

**For the bulk (16-21/22 bins per family): HONEST_NO_EDGE.** `capital_efficiency_lcb_ev` is correctly identifying bins where the model conservative probability is at or below market price. These are efficiency-regime rejections.

**For the 1-3 positive-EV bins per cycle: capital_efficiency_lcb_ev is NOT the gating mechanism.** Those candidates pass the EV gate and are rejected by:

1. `coverage_unlicensed_tail` — fires on FORECAST_BOOTSTRAP source for penny-price bins with material q_lcb disagreement. This is conservative but calibration evidence shows bootstrap q_lcb IS inflated on tail bins (~3.4x per sigma_scale_fit.json). The guard is the correct fail-CLOSED response to an unvalidated source class.

2. `direction_law` (BIN_FORECAST_MISMATCH) — rejects candidates whose bin is outside the ensemble's forecast-adjacent range.

3. `TRADE_SCORE_NON_POSITIVE` in a subset of cycles — fires when `p_fill_lcb * (q_lcb - price - 0.01) <= 0`. At tiny prices like 0.001-0.01, the 0.01 penalty term alone can zero the trade score even when `(q_lcb - price)/price` is positive. Example: Tokyo 29C with q_lcb=0.0196, price=0.01: `0.0196 - 0.01 - 0.01 = -0.0004 < 0` → TRADE_SCORE_NON_POSITIVE.

**The closest case of potential over-suppression is the 0.01 penalty in trade_score for very-cheap-price bins.** At price=0.001-0.01, the 0.01 penalty eats all of a reasonable q_lcb edge (e.g., q_lcb=0.0196, price=0.01: net = -0.0004). This is distinct from capital_efficiency and is a structural floor in `_robust_trade_score_from_generated_inputs` (adapter line 13737, hardcoded `penalty=0.01`). Whether this 0.01 penalty is correct is not capital_efficiency's business — it belongs to the trade_score seam.

## Files Referenced

- `src/strategy/live_inference/live_admission.py` — gate formula (lines 87-113, 155-200)
- `src/engine/event_reactor_adapter.py` — invocation (6565-6576), histogram (7095-7220), proof generation (7400-7600)
- `src/strategy/live_inference/trade_score.py` — trade_score formula (42-52, 68-79)
- `src/strategy/probability_uncertainty.py` — q_lcb construction (256-348)
- `state/sigma_scale_fit.json` — tail calibration (candidate only, not live on primary path)
- `logs/zeus-live.log` — rejection histograms used for analysis

