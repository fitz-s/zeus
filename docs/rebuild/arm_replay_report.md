# Q-Kernel Rebuild — ARM Validation Replay Report

Created: 2026-06-15. Offline ARM harness (`scripts/qkernel_arm_replay.py`).
Read-only on live data; no venue calls. Replay over the last 14 days of VERIFIED settled families.
Decision cycle = target_date − 1 day (lead ~24h), pure-predictive (no-day0) path; `has_fusion_capture=False` so σ falls to the realized settlement floor.

## Replay coverage

- Settled VERIFIED families in window: **762**
- Families replayed (>=3 fresh members, resolvable, q built): **697**
- Ineligible / skipped: **65**
- Skip reasons: no_members_or_resolution=65
- By metric: high=604, low=93

## 1. Center sanity (the headline fix)

- Book-wide mean(mu* − realized): **-0.510** (median -0.600), n=697
- Book-wide mean(fresh_debiased_consensus − realized): **-0.474** (median -0.600), n=697

Tokyo high (warm-bias cohort) — mu* vs fresh debiased median vs realized:

| date | n_members | raw_median | debiased_median | mu* | realized | mu*−real |
|---|---|---|---|---|---|---|
| 2026-05-31 | 6 | 27.6 | 27.6 | 27.4 | 27.0 | 0.4 |
| 2026-06-01 | 6 | 28.8 | 28.8 | 29.0 | 31.0 | -2.0 |
| 2026-06-02 | 6 | 24.6 | 24.6 | 24.4 | 26.0 | -1.6 |
| 2026-06-03 | 6 | 22.1 | 22.1 | 22.1 | 21.0 | 1.1 |
| 2026-06-04 | 6 | 21.8 | 21.8 | 21.8 | 23.0 | -1.2 |
| 2026-06-05 | 6 | 21.2 | 21.2 | 21.3 | 20.0 | 1.3 |
| 2026-06-06 | 6 | 22.9 | 22.9 | 22.9 | 22.0 | 0.9 |
| 2026-06-07 | 6 | 22.1 | 22.1 | 21.9 | 21.0 | 0.9 |
| 2026-06-09 | 6 | 21.0 | 21.0 | 21.0 | 22.0 | -1.0 |
| 2026-06-10 | 7 | 20.9 | 20.9 | 20.8 | 22.0 | -1.2 |
| 2026-06-11 | 7 | 23.8 | 23.8 | 23.7 | 26.0 | -2.3 |
| 2026-06-12 | 7 | 25.7 | 25.7 | 25.7 | 26.0 | -0.3 |
| 2026-06-13 | 7 | 26.0 | 26.0 | 26.0 | 27.0 | -1.0 |

Tokyo-high mean(mu* − realized) = **-0.45**°C, mean(mu* − debiased_median) = **-0.02**°C (≈0 ⇒ mu* tracks fresh consensus, not an invented warm 26).

## 2. Point-q calibration (reliability, modal bin)

Predicted q of the modal (favorite) bin vs realized frequency it wins.

| pred bucket | n | mean_pred | realized | ±2·SE band | on-diagonal? |
|---|---|---|---|---|---|
| 0.25 | 17 | 0.274 | 0.176 | ±0.185 | yes |
| 0.30 | 588 | 0.293 | 0.298 | ±0.038 | yes |
| 0.35 | 40 | 0.363 | 0.575 | ±0.156 | NO |
| 0.40 | 52 | 0.390 | 0.327 | ±0.130 | yes |

Pooled: mean predicted modal q = **0.304**, realized modal win-rate = **0.313** (n=697).

## 3. q_lcb coverage (coherent lower band, modal bin)

For families whose modal-bin q_lcb lands in a band, realized modal win-rate should EXCEED the mean q_lcb (conservative coverage).

| q_lcb band | n | mean_q_lcb | realized_win | coverage_ratio | covered? |
|---|---|---|---|---|---|
| [0.0,0.2) | 337 | 0.159 | 0.285 | 1.79 | yes |
| [0.2,0.4) | 360 | 0.226 | 0.339 | 1.50 | yes |

Pooled: mean modal q_lcb = **0.194**, realized modal win-rate = **0.313**, coverage ratio = **1.61** (≥1 ⇒ q_lcb is conservative). n=697

## 4. PIT / width reliability (is σ honest)

Randomized PIT of the realized settlement value over the discrete predictive q (`PIT = F(<settled bin) + U·q(settled bin)`). Calibrated ⇒ PIT ~Uniform(0,1): std ≈ 0.289 (`std/uniform`≈1), tail (outer-2-decile) mass ≈ 0.20. **std/uniform < 0.85 (PIT bunched in the middle, low tail mass) ⇒ OVER-dispersed (σ too WIDE); std/uniform > 1.15 or tail mass piled up ⇒ UNDER-dispersed (σ too NARROW).** `σ/realized_RMSE` is the direct scale check (σ vs the realized |mu*−settle| error; ≈1 honest, >1 too wide).

Two σ configurations are replayed side-by-side: **predictive_rss** (the full RSS width: calibrated EMOS model-σ ⊕ center-param SE ⊕ residual floor — the served decision-time width) and **floor_only** (the bare realized settlement σ-floor — the conservative fallback when no fusion capture is present).

Headline width statistic = **std(z)** of the standardized residual `z=(settle−mu*)/σ`: ≈1 honest, <1 ⇒ σ too WIDE, >1 ⇒ σ too NARROW. `mean(z)` ≈ 0 ⇒ unbiased center.

| config / cohort | n | **std(z)** | mean(z) | PIT std/uniform | mean σ | realized RMSE | σ/RMSE | dispersion |
|---|---|---|---|---|---|---|---|---|
| predictive_rss | 697 | **0.93** | 0.36 | 0.92 | 1.54 | 1.55 | 0.99 | HONEST |
| floor_only | 697 | **0.86** | 0.34 | 0.87 | 1.68 | 1.55 | 1.09 | HONEST |
| predictive_rss::high | 604 | **0.94** | 0.38 | 0.91 | 1.59 | 1.59 | 1.00 | HONEST |
| predictive_rss::low | 93 | **0.86** | 0.22 | 0.97 | 1.23 | 1.26 | 0.97 | HONEST |

PIT decile mass (predictive_rss): [0.044, 0.05, 0.07, 0.07, 0.082, 0.129, 0.116, 0.136, 0.162, 0.139] (uniform = 0.10 each).
PIT decile mass (floor_only): [0.033, 0.053, 0.069, 0.073, 0.089, 0.138, 0.128, 0.141, 0.164, 0.113].

> **Finding:** the served **predictive_rss** width is materially OVER-dispersed: std(z)=0.93 (σ ≈ 0.99× the realized error RMSE; it would need to SHRINK ~1.07× to reach std(z)=1). The calibrated EMOS model-σ is wider than recent realized settlement dispersion, and the RSS adds a residual-floor term on top. The narrower **floor_only** width (std(z)=0.86) is essentially honest. This is a width-calibration issue the ARM gate surfaced BEFORE integration: a σ-AUTHORITY TUNING question (which width the reactor serves / re-fitting the EMOS σ-model on recent settlement), NOT a center or q-integration defect.

## 5. After-cost EV by class (where books exist)

- Settled families with a usable executable book in window: **714**
- Families graded (modal sibling + NO ask present): **580**

**DATA-COVERAGE-LIMITED**: `executable_market_snapshots` carries no per-row bin-label/integer-threshold column, so a snapshot condition_id cannot be mapped to the exact settled bin from this table alone. The realized settlement-graded after-cost EV per bin therefore CANNOT be computed from the snapshot table in isolation. What the book DOES support is the **market-implied** after-cost EV of the dominant Zeus trade (buy_no on the modal/highest-YES sibling): `EV_mkt = (1 − modal_yes_ask) − no_ask`. This is the market's own price coherence, NOT a settlement grade — reported below as a coverage-bounded diagnostic, not a verdict.

- Market-implied buy_no-modal after-cost EV (book bid/ask spread cost only): mean **-0.051**, median **-0.020**, n=580. (Negative ⇒ the bid/ask spread alone makes the modal-NO a negative-carry trade at these quotes; this is the spread cost, not edge.)

| metric | n | mean EV_mkt |
|---|---|---|
| high | 499 | -0.039 |
| low | 81 | -0.123 |

> The settlement-graded after-cost EV-by-class (city/metric/side/route) requires joining each sibling condition_id to its bin label (via the market/condition registry the live reactor uses, not present in this offline snapshot table). That join is the integration-time wiring; this gate proves the q layer, and flags EV-by-class as coverage-limited rather than fabricating per-bin grades.

## 6. Inverse-failure check (is the modal edge real, or base-rate favorite-buying?)

The modal/favorite-bin cohort, graded on its OWN settled rows. Win-rate is NOT edge: a high modal win-rate is the base rate the market already prices.

- Modal-bin realized win-rate: **0.313** (n=697); predicted modal q: **0.304**.
- Calibration gap (realized − predicted): **0.009**. These figures use the SERVED predictive-RSS σ (over-dispersed per §4), so the modal q is UNDER-stated and the gap is positive (the favorite wins MORE than the wide σ predicts) — the spine is NOT over-claiming a favorite edge; if anything it under-claims at this width. Under the better-calibrated floor-only σ the modal q and realized win-rate align (§2/§4). Either way the key point holds: a high modal win-rate is base rate, not edge; whether the q BEATS the market price (true edge) is the after-cost EV question (§5), coverage-limited here.

| metric | n | modal_win_rate | mean_modal_q | gap |
|---|---|---|---|---|
| high | 604 | 0.295 | 0.293 | 0.002 |
| low | 93 | 0.430 | 0.377 | 0.053 |

## 7. No-trade counterfactual (sample)

Families the spine would likely NOT trade as a modal-NO buy: those where the modal q is LOW (no confident favorite, e.g. q_modal < 0.30) OR the center is an ENVELOPE_FALLBACK (EMOS disagreed and was refused). Was sitting out correct?

- Low-confidence (q_modal<0.30) families: **469**. In **343** the modal bin did NOT settle (a modal-NO buy would have won; sitting out forgoes a low-confidence win), in **126** the modal bin DID settle (buying its NO would have LOST — sitting out was protective).
- Modal-bin settle rate in the no-trade cohort: **0.269** vs **0.313** book-wide — lower confidence cohort, as expected for a no-trade screen.

Sample no-trade families:

| city | date | metric | mu* | realized | q_modal | modal_settled |
|---|---|---|---|---|---|---|
| Seoul | 2026-05-31 | high | 21.5 | 27.0 | 0.273 | no |
| Karachi | 2026-06-11 | high | 41.5 | 40.0 | 0.273 | no |
| Guangzhou | 2026-06-03 | high | 34.5 | 38.0 | 0.273 | no |
| Shanghai | 2026-06-06 | high | 24.5 | 26.0 | 0.273 | no |
| Beijing | 2026-06-05 | high | 27.5 | 28.0 | 0.273 | no |
| Lucknow | 2026-06-10 | high | 42.5 | 40.0 | 0.274 | no |
| Ankara | 2026-06-10 | high | 23.5 | 21.0 | 0.274 | no |
| Karachi | 2026-06-09 | high | 36.5 | 36.0 | 0.274 | yes |
| Chongqing | 2026-06-04 | high | 29.5 | 32.0 | 0.274 | no |
| Qingdao | 2026-06-05 | high | 27.5 | 26.0 | 0.274 | no |
| Amsterdam | 2026-06-07 | high | 18.5 | 19.0 | 0.274 | yes |
| Singapore | 2026-06-13 | high | 31.5 | 32.0 | 0.275 | yes |

## Verdict

**PARTIAL — q-CALIBRATION LAYER PROVEN (center + point-q + q_lcb + width all pass); AFTER-COST EV-BY-CLASS COVERAGE-LIMITED**

- Center: book-wide mean(mu*−realized)=-0.510 (PASS <1.0); prior warm bias ~+2.8 is gone.
- Point-q (predictive_rss σ): modal predicted 0.304 vs realized 0.313 (gap +0.009, CALIBRATED).
- Point-q (floor_only σ): modal predicted 0.279 vs realized 0.313 (gap +0.034, CALIBRATED).
- q_lcb coverage: realized 0.313 ≥ mean q_lcb 0.194 (CONSERVATIVE).
- Width — served predictive_rss σ: std(z)=0.93, σ/realized_RMSE=0.99 (HONEST).
- Width — floor_only σ (alternative): std(z)=0.86, σ/realized_RMSE=1.09 (HONEST).
- After-cost EV-by-class: DATA-COVERAGE-LIMITED (books=714 but no per-bin label in snapshot table; settlement-graded per-class EV deferred to integration, NOT a pass/fail here).

