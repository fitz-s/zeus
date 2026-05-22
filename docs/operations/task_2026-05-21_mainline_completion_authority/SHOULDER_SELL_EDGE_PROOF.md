# Shoulder-Sell Edge Proof

```
Created: 2026-05-22
Last reused or audited: 2026-05-22
Authority basis: 04_PHASE_3_SHOULDER.md (shoulder semantics, payoff, Kelly clamp)
                 + src/contracts/shoulder_strategy_vnext.py (classifier topology gate, L93)
                 + src/types/market.py::Bin.is_shoulder
                 + src/strategy/market_analysis.py (p_market YES-price semantics)
                 + src/contracts/execution_price.py::polymarket_fee
Data: READ-ONLY immutable snapshots in state/ at main@1ea6c96ba1.
      No live DB or source modified.
```

---

## VERDICT

**`NOT_PROVEN` — refuted in sign.**

The calibrated forecast is *sound* on shoulder bins (Claim 1 passes: mean p_cal ≈ realized
hit rate within ~1pp; positive Brier skill vs climatology). But the market trades shoulder
**YES at ~75% of the calibrated probability and ~82% of the realized hit rate** — shoulders are
systematically **UNDER**priced, the opposite of the thesis. Selling shoulder NO at prevailing
prices is **−EV before costs** in this sample, and the proposed entry rule (sell where
`p_mkt > p_cal`) selects the *worst* sub-population: at threshold τ=0.05 the realized sell EV is
**−$0.20 per $1** (boot-95 lower CB −$0.39). There is **no τ ≥ 0 at which net sell EV > 0**.

**Promote `shoulder_sell` to live → REJECTED** until the mispricing sign reverses on fresh,
independent data. The single best sub-slice (lower shoulders) has net EV ≈ +$0.004/\$1 with a
boot-95 CI of [−0.007, +0.019] that straddles zero — no significant edge even there.

**Headline numbers** (N = 253 market-complete, deduplicated shoulder cases; 2026-05-04 → 05-19):

| Claim | Quantity | Value | 95% interval |
|---|---|---|---|
| 1 — calibration | realized shoulder hit rate | 0.103 | Wilson [0.071, 0.146] |
| 1 — calibration | mean p_cal | 0.114 | — |
| 1 — calibration | Brier(p_cal) / skill vs climatology | 0.083 / +0.096 | — |
| 1 — calibration | reliability slope / intercept | 0.465 / −0.94 | (perfect = 1 / 0) |
| 2 — mispricing | mean (p_mkt_YES − p_cal) | **−0.030** | boot [−0.056, −0.004] |
| 2 — mispricing | frac cases p_mkt > p_cal (thesis dir.) | **0.336** | (thesis needs ≫0.5) |
| 3 — net edge | mean EV_sell (p_mkt − outcome − fee) | **−0.020** | boot [−0.044, +0.003] |
| 3 — net edge | EV_sell at thesis entry τ=0.05 | **−0.202** | boot [−0.385, …] |

**Single biggest threat to the proof's validity:** the data is effectively **one regime** — a
19-day May 2026 window (the late "half" is a single calendar day, 2026-05-19), 253 cases. The
sign-inversion is robust *within* this window across upper/lower shoulders and p_cal tertiles,
but it has not been observed across a heat-dome / cold-snap transition. The verdict is therefore
"NOT_PROVEN" (data refutes the thesis here) rather than "refuted forever". A regime where retail
lottery demand actually overprices the tail — the dossier's premise — is *possible* but is **not
present in any data Zeus currently holds**.

---

## 1. The instrument and the EV derivation (sign-checked)

### What "shoulder" is

`Bin.is_shoulder` (src/types/market.py L104) ≡ `is_open_low OR is_open_high`: an unbounded tail
bin ("X°F or higher", "X°C or below"). Width is `None`.

### What `shoulder_sell` actually does

The Phase-3 classifier topology gate (`shoulder_strategy_vnext.py` L93) is:

```python
if not (edge.direction == "buy_no" and edge.bin.is_shoulder):
    return None
```

So **shoulder_sell ⟺ buy_no on a shoulder bin** — a *short on the tail*. It does **not** sell YES
directly; it **buys the NO token**. This matters for the payoff sign.

### Payoff (per $1 of NO notional)

Let:
- `p_true` = true probability the shoulder bin resolves YES (tail realizes).
- `p_mkt`  = market YES price of the shoulder bin (verified below to be the YES leg).
- `q`      = NO price paid = `1 − p_mkt` on a complete two-sided book.
- `fee`    = Polymarket taker fee, `0.05 · q · (1−q)` per share (execution_price.py L130).

Buying NO at `q` pays **$1 if the shoulder does NOT hit** (prob `1−p_true`), $0 if it hits:

```
EV_NO = (1 − p_true)·$1 − q − fee
      = (1 − p_true) − (1 − p_mkt) − fee
      = p_mkt − p_true − fee.                          (★)
```

**Sell is +EV ⟺ `p_mkt > p_true + fee`** — i.e. the market YES price must *exceed* the true
tail probability by more than fees. This is the thesis: shoulder YES is overpriced.

> **Leg check (the sign-flipping gate).** `p_market` in `MarketAnalysis` is the **YES price per
> bin**: `market_analysis.py` L223 sets `self.vig = self.p_market.sum()` (summing YES prices
> across all bins → vig ≈ 1+), and a *separate* `p_market_no` vector exists for the NO book.
> `probability_trace_fact.p_market_json` is written from `decision.p_market`
> (src/state/db.py L6226). Empirically the per-trace `p_market_json` vector sums to a **median of
> 0.99** across 2000 sampled traces — confirming YES prices that sum to ~1. The mispricing
> measurement below uses the YES leg. The native-NO book (`native_no_quote` in the contract) is
> **not present** in this dataset; (★) therefore assumes a complete `q = 1 − p_mkt` book, which is
> a *favorable* assumption to the thesis (a thin NO book with a wider ask only makes selling worse).

---

## 2. Data and method

### Why the calibration grid cannot supply the proof directly

`calibration_pairs_v2` (91M rows, forecasts.db) is a **synthetic per-degree grid bounded at
physical extremes**. Its only shoulder labels are `−40°C/61°C or below/higher` and
`−40°F/141°F or higher` — degenerate tails with `sum(outcome)=0` and `avg(p_raw)=0`. The **real**
Polymarket shoulder thresholds (e.g. "74°F or higher") do not appear there. So `p_cal` for a real
shoulder cannot be looked up; it must come from the production decision log.

### Coverage bottleneck (independent confirmation of the operator's "quote-starved" call)

| Source | shoulder tokens w/ price history | … also settled |
|---|---|---|
| forecasts.db `market_price_history` (4,192 rows) | 21 | **1** |
| zeus_trades.db `market_price_history` (622,633 rows) | 1,446 | — |

`shoulder_sell` has **0** rows in `trade_decisions` and **0** in `probability_trace_fact.strategy_key`
— it has *never traded live and never produced a first-party would-be-decision record under its own
strategy key*. The empirical-replay track's "80 settled decisions" is not reconstructable as
first-party shoulder_sell records; the calibration track is starved at the *same* market-price join.

### The dataset actually used

`probability_trace_fact` (zeus_trades.db) stores, per decision snapshot, **index-aligned arrays**
`bin_labels_json` / `p_cal_json` / `p_market_json` / `p_raw_json` (bins ordered low→high, so
index 0 = lower shoulder, index −1 = upper shoulder). Method:

1. For each trace with non-empty `p_cal` **and** `p_market`, keep only **market-complete** books
   (YES-price vector sum ∈ [0.85, 1.25]) — drops phantom zero-price books.
2. Parse each shoulder label → (threshold, unit, side); infer metric from "highest/lowest".
3. Join to `settlements_v2` (forecasts.db) by (city, target_date, metric); compute outcome by
   comparing `settlement_value` to the threshold (robust; no string match).
4. **Deduplicate** intraday snapshots → one case per (city, date, metric, side, threshold),
   taking the median p_cal / p_mkt across the day's snapshots.

Result: **253 market-complete shoulder cases** (128 upper, 125 lower), from 5,172 underlying
snapshots, spanning 2026-05-04 → 2026-05-19. `p_true` is estimated by the realized binary outcome
(Claim 1 validates p_cal as an alternative estimator).

Queries are look-ahead-safe: `p_market` and `p_cal` are recorded at decision time; the settlement
outcome is only joined for scoring, never fed back into the price/forecast.

---

## 3. Claim 1 — Calibration soundness (PASSES, with a caveat)

`p_cal` is a **sound aggregate estimator** of shoulder `p_true`:

- realized hit rate **0.103** (Wilson-95 [0.071, 0.146]) vs mean p_cal **0.114** — within ~1pp.
- **Brier(p_cal) = 0.083**, log-loss 0.316; Brier(climatology const) = 0.092 →
  **skill score +0.096** (the model resolves shoulder risk better than a constant base rate).

**Caveat — resolution but over-confidence on the high tail.** The reliability logistic fit
(`outcome ~ logit(p_cal)`) gives **slope 0.465, intercept −0.94** (perfect = 1 / 0). The aggregate
match is partly averaging luck. Decile reliability:

```
bin   n  mean_pcal   hit    wilson95          mean_pmkt
 0   42   0.0032    0.000  (0.000, 0.084)     0.0103
 1   42   0.0050    0.071  (0.025, 0.190)     0.0394
 2   42   0.0069    0.024  (0.004, 0.123)     0.0075
 3   42   0.0124    0.024  (0.004, 0.123)     0.0354
 4   42   0.0410    0.095  (0.038, 0.221)     0.0475
 5   43   0.6042    0.395  (0.264, 0.544)     0.3595   <- p_cal over-predicts here
```

The top decile (mean p_cal 0.60) over-predicts (hit 0.395). The bias-corrected `p_true` on the
high tail is therefore *below* p_cal — which makes the NO-buy look momentarily better there — but in
**every** decile `mean_pmkt ≤ realized hit rate`, so the market underprices the tail across the
whole range. The correction does not rescue the sell.

---

## 4. Claim 2 — Systematic mispricing (FAILS IN SIGN)

The thesis requires `p_mkt > p_cal` (and `> p_true`) systematically. The data shows the reverse:

- **mean (p_mkt_YES − p_cal) = −0.030** (boot-95 [−0.056, −0.004]) — statistically below zero.
- median = −0.003.
- **frac cases with p_mkt > p_cal = 0.336** — the thesis direction holds in barely a third of cases.
- **mean (p_mkt − outcome) = −0.018** (boot-95 [−0.043, +0.004]) — gross sell EV before fees,
  centered negative.

The mispricing is **sign-consistent toward underpricing**, not overpricing. The retail-lottery
overpricing premise of dossier §7 is **not observed** in current data.

---

## 5. Claim 3 — Margin vs costs, and the entry threshold τ (no viable τ)

Fees are tiny here: mean Polymarket fee per NO share = **$0.0014** (because NO prices on shoulders
are near 1, where `q·(1−q)` is small). Costs do **not** drive the result — the gross edge sign does.

Net sell EV (★) with fees:

- using realized outcome as truth: **mean −0.020 /\$1** (boot-95 [−0.044, +0.003]).
- using p_cal as truth: **mean −0.031 /\$1** (boot-95 [−0.058, −0.006]).

**τ derivation.** The proposed production rule is "enter shoulder_sell when
`(p_mkt − p_cal) ≥ τ`". Sweeping τ over the cases that *pass* the filter:

```
  τ    n_pass   mean_EV_sell   boot-95 lower
 0.00    85       -0.061         -0.122
 0.02    33       -0.144         -0.283
 0.05    21       -0.202         -0.385
 0.10    18       -0.135         -0.312
 0.15    15       -0.176         -0.384
 0.20    11       -0.106         -0.324
```

**Raising τ makes it worse, not better.** This is the mechanistic core of the refutation: a
shoulder priced *above* the model is the case where the market knows something the model misses
(or the ensemble under-disperses the tail) — exactly where the tail is *more* likely to realize.
Selling into market-rich shoulders is anti-selection. **No τ ≥ 0 yields net EV > 0.** A production
entry rule cannot be derived; the only τ consistent with the data is "do not enter."

---

## 6. Robustness / failure modes

| Cut | Result | Reading |
|---|---|---|
| **Upper shoulders** (n=128) | EV_sell −0.041 [−0.087, +0.002] | clearly −EV |
| **Lower shoulders** (n=125) | EV_sell +0.005 [−0.006, +0.020]; net w/ fee +0.004 [−0.007, +0.019] | best slice; CI straddles 0 → no edge |
| **HIGH p_cal tertile** (extreme-regime proxy, n=85) | EV_sell −0.042 [−0.093, +0.006] | selling is *worst* in extreme regimes — the heat-dome crash risk §7 warned of |
| **LOW p_cal tertile** (n=84) | EV_sell −0.011 [−0.048, +0.016] | flat/negative |
| **Temporal early/late** | −0.020 / −0.015 | no drift; "late" = single day → one regime |

**Proof weaknesses (be explicit before moving capital):**

1. **Single-regime sample.** 19 days, one season (late spring), no heat-dome/cold-snap transition
   in-window. The sign-inversion is the *current* state of the market, not a law of nature.
2. **p_true ≈ realized outcome.** With n=253 binary outcomes, the truth estimator is noisy;
   bias-corrected p_cal is the alternative and yields a *more* negative sell EV.
3. **Synthetic NO leg.** Native NO depth (`native_no_quote`) is absent; (★) assumes `q = 1−p_mkt`.
   A real thin NO ask widens `q`, making the sell *worse* — the assumption favors the thesis, and
   the thesis still fails.
4. **Selection / liquidity.** Market-complete filter (sum ∈ [0.85, 1.25]) drops illiquid books;
   the surviving cases are the *most* liquid shoulders, where mispricing should be *smallest* — yet
   it is still underpriced. Illiquid shoulders are likely more underpriced, not less.
5. **No look-ahead leak found**, but the same `decision_snapshot_id` underlies both p_cal and
   p_market, so any shared-snapshot timing skew would affect both legs symmetrically (does not
   create a fake mispricing sign).

---

## 7. What would change the verdict

The thesis becomes provable only if, on **fresh, independent** data spanning ≥2 weather regimes:
`p_mkt_YES − p_cal` turns **positive and sign-consistent** on shoulders, with the positive cases
*also* realizing low hit rates (i.e. the overpriced shoulders are the ones that don't hit). Until
that is observed, `shoulder_sell` stays at `shadow` (per 04_PHASE_3_SHOULDER.md §"does NOT do") and
the production entry rule is **no-trade**. Recommend the shadow lane continue logging shoulder
`p_cal` / native-NO-quote / outcome triples so this proof can be re-run with a native-NO leg and a
multi-regime sample.
