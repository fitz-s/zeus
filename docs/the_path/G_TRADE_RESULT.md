# G_TRADE_RESULT — Same-CLOB After-Cost Before/After (U0R vs Single-Anchor)

> Created: 2026-06-08
> Author role: INDEPENDENT SKEPTIC (re-probed the headline from scratch; did NOT run the replay).
> Authority basis: settlement truth = zeus-forecasts.settlement_outcomes WHERE authority='VERIFIED'
>   AND settlement_value IS NOT NULL. Real prices = native YES/NO orderbook_top_ask +
>   depth_at_best_ask from zeus_trades.db executable_market_snapshots (captured_at <= decision).
>   Forecast = walk-forward U0R proof engine (run_u0r_bayes_fusion.py).
> Method: READ-ONLY (sqlite mode=ro); no src/ or live-DB writes.
> Full report + scripts: /Users/leofitz/zeus/.omc/research/polyweather_eval/
>   u0r_same_clob_after_cost.md, G_TRADE_VERDICT.md, scripts/g_trade_*.py
> Companion baseline: OBSERVE_BASELINE.md (the live OLD-book before: −6.6%, 20.8% wr, n=24).

## VERDICT: AFTER_COST_POSITIVE — directional, forward-live still required

Independently re-probed and the headline HOLDS: the U0R selective subset is after-cost POSITIVE
on real native CLOB asks and beats the single-anchor baseline on every gate. This is the
strongest real-economics before/after that exists today, but it is one EU cohort (n small) and
does NOT by itself license a production flag-flip or full-size capital.

## BEFORE → AFTER (after-cost, real native asks, selective q_lcb−ask>δ subset)

| | BEFORE (single-anchor A1) | AFTER (U0R C1 core) | AFTER (U0R D1 +regional) |
|---|---:|---:|---:|
| ROI @ δ=0.00 | **−16.7%** (191 tr) | **+17.4%** (124 tr) | **+26.0%** (128 tr) |
| ROI @ δ=0.02 | −11.5% (124 tr) | +10.9% (81 tr) | +23.4% (83 tr) |
| ROI @ δ=0.05 | −7.0% (75 tr) | +6.9% (43 tr) | +23.4% (43 tr) |
| win-rate @ δ=0.02 | 17.7% | 23.5% | 28.9% |

Single-anchor is after-cost NEGATIVE (strictly worse than not trading) on every gate; U0R is
after-cost POSITIVE (strictly better than not trading) on every gate. U0R trades FEWER markets at
HIGHER hit-rate — the fewer-but-better selectivity signature (10.8% of the 1,151 ask-available
opportunities vs the baseline's 16.6%).

**Paired per-market (same markets both trade, removes selection confound):** at δ=0, baseline
−$88.27 vs U0R-C1 +$58.75, C1 wins 34/49 markets (sign-test p=0.009). At δ=0.02/0.05 the dollar
direction stays positive but the paired sign test is within noise (p=0.43 / 1.0) — the honest
small-n boundary.

**Relation to OBSERVE_BASELINE:** this reproduces the live OLD-book's losing DIRECTION
(single-anchor loses on real asks) and shows the U0R fusion flips it positive. But the "before"
here is the single-anchor FORECAST variant replayed on the SAME EU markets, NOT the OLD traded
ledger (whose losing cities have no recoverable U0R forecast). The live native-NO replacement_0_1
book still has ZERO settled positions.

## SKEPTIC RE-PROBE — what was independently re-verified

1. **5-trade from-scratch recompute** (raw EMS ask/depth + VERIFIED settlement + bin mapping →
   PnL): all 5 reproduced to 1e-6. ✓
2. **Anti-lookahead**: 0/892 trade rows with captured_at > decision; forecast walk-forward
   (`train_dates = dates[:i]`, strict < td) verified in engine source. ✓
3. **Real-price**: native YES/NO top-ask (separate condition_ids/token_ids), distinct from
   midpoint and 1-complement; depth-capped fills. ✓
4. **Fee**: EMS carries non-zero fee metadata (0.05/0.10) BUT the 173 real on-chain fills show
   `fee_paid_micro` NULL and `fee_rate_bps=0` → realized fee genuinely zero. Verdict survives an
   adversarial 5% (C1) / 10% (D1) fee anyway. ✓
5. **Settlement truth**: 0/112 covered cells lack a VERIFIED numeric settlement. ✓
6. **Selectivity**: 124/1,151 traded at δ=0 — genuinely the q_lcb−ask>δ subset, not buy-all. ✓

## WHAT IT LICENSES

GO to **forward-live shadow / small-capital** on the EU cohort to accumulate a live native-NO
settled track record. It does NOT license an automatic production flag-flip or full-size capital,
because (a) n is small / one cohort and only the δ=0 paired test is significant, (b) the "before"
is a forecast variant not the live ledger, and (c) the live replacement_0_1 book has no settled
outcomes yet. The bar cleared is "deploy to measure," not "deploy at scale."

## OPEN QUESTIONS

- Live native-NO realized PnL is unmeasurable until the June replacement_0_1 fills settle — the
  number that ultimately matters does not exist yet.
- C1's pooled positivity is YES-lottery-tail-assisted (collapses to +0.5% if YES<$0.10 dropped);
  prefer D1 (tail-robust both ways: drop-cheap-YES +13.6%, NO-only +23.3%, YES-only +23.5%).
- NO-side n is very thin (11–16 distinct markets); the 79–83% NO win-rates are directional.
- Lead-3 has no asks before its decision (snapshots start 2026-05-15) — clean data boundary, not
  a failure; only lead-1 (primary) and lead-2 are economically testable today.
