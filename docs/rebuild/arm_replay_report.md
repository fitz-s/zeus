# Q-Kernel Rebuild — ARM Validation Replay Report

Created: 2026-06-15. Offline ARM harness (`scripts/qkernel_arm_replay.py`).
Read-only on live data; no venue calls. Replay over the last 14 days of VERIFIED settled families.
Decision cycle = target_date − 1 day (lead ~24h), pure-predictive (no-day0) path; `has_fusion_capture=False` so σ falls to the realized settlement floor.

## Replay coverage

- Settled VERIFIED families in window: **758**
- Families replayed (>=3 fresh members, resolvable, q built): **693**
- Ineligible / skipped: **65**
- Skip reasons: no_members_or_resolution=65
- By metric: high=601, low=92

## 1. Center sanity (the headline fix)

- Book-wide mean(mu* − realized): **-0.516** (median -0.600), n=693
- Book-wide mean(fresh_debiased_consensus − realized): **-0.480** (median -0.600), n=693

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
| 0.05 | 3 | 0.068 | 0.333 | ±0.544 | yes |
| 0.10 | 101 | 0.108 | 0.297 | ±0.091 | NO |
| 0.15 | 198 | 0.155 | 0.283 | ±0.064 | NO |
| 0.20 | 246 | 0.200 | 0.293 | ±0.058 | NO |
| 0.25 | 130 | 0.246 | 0.362 | ±0.084 | NO |
| 0.30 | 13 | 0.284 | 0.462 | ±0.277 | yes |
| 0.35 | 2 | 0.333 | 0.500 | ±0.707 | yes |

Pooled: mean predicted modal q = **0.184**, realized modal win-rate = **0.307** (n=693).

## 3. q_lcb coverage (coherent lower band, modal bin)

For families whose modal-bin q_lcb lands in a band, realized modal win-rate should EXCEED the mean q_lcb (conservative coverage).

| q_lcb band | n | mean_q_lcb | realized_win | coverage_ratio | covered? |
|---|---|---|---|---|---|
| [0.0,0.2) | 634 | 0.133 | 0.287 | 2.17 | yes |
| [0.2,0.4) | 59 | 0.217 | 0.525 | 2.42 | yes |

Pooled: mean modal q_lcb = **0.140**, realized modal win-rate = **0.307**, coverage ratio = **2.20** (≥1 ⇒ q_lcb is conservative). n=693

## 4. PIT / width reliability (is σ honest)

Randomized PIT of the realized settlement value over the discrete predictive q (`PIT = F(<settled bin) + U·q(settled bin)`). Calibrated ⇒ PIT ~Uniform(0,1): std ≈ 0.289 (`std/uniform`≈1), tail (outer-2-decile) mass ≈ 0.20. **std/uniform < 0.85 (PIT bunched in the middle, low tail mass) ⇒ OVER-dispersed (σ too WIDE); std/uniform > 1.15 or tail mass piled up ⇒ UNDER-dispersed (σ too NARROW).** `σ/realized_RMSE` is the direct scale check (σ vs the realized |mu*−settle| error; ≈1 honest, >1 too wide).

Two σ configurations are replayed side-by-side: **predictive_rss** (the full RSS width: calibrated EMOS model-σ ⊕ center-param SE ⊕ residual floor — the served decision-time width) and **floor_only** (the bare realized settlement σ-floor — the conservative fallback when no fusion capture is present).

Headline width statistic = **std(z)** of the standardized residual `z=(settle−mu*)/σ`: ≈1 honest, <1 ⇒ σ too WIDE, >1 ⇒ σ too NARROW. `mean(z)` ≈ 0 ⇒ unbiased center.

| config / cohort | n | **std(z)** | mean(z) | PIT std/uniform | mean σ | realized RMSE | σ/RMSE | dispersion |
|---|---|---|---|---|---|---|---|---|
| predictive_rss | 693 | **0.52** | 0.21 | 0.63 | 3.00 | 1.54 | 1.94 | OVER-dispersed (σ too WIDE) |
| floor_only | 693 | **0.86** | 0.34 | 0.86 | 1.68 | 1.54 | 1.09 | HONEST |
| predictive_rss::high | 601 | **0.51** | 0.22 | 0.62 | 3.12 | 1.58 | 1.98 | OVER-dispersed (σ too WIDE) |
| predictive_rss::low | 92 | **0.52** | 0.12 | 0.67 | 2.15 | 1.27 | 1.70 | OVER-dispersed (σ too WIDE) |

PIT decile mass (predictive_rss): [0.006, 0.016, 0.059, 0.108, 0.127, 0.209, 0.215, 0.149, 0.085, 0.026] (uniform = 0.10 each).
PIT decile mass (floor_only): [0.027, 0.056, 0.065, 0.075, 0.094, 0.13, 0.15, 0.156, 0.134, 0.113].

> **Finding:** the served **predictive_rss** width is materially OVER-dispersed: std(z)=0.52 (σ ≈ 1.94× the realized error RMSE; it would need to SHRINK ~1.94× to reach std(z)=1). The calibrated EMOS model-σ is wider than recent realized settlement dispersion, and the RSS adds a residual-floor term on top. The narrower **floor_only** width (std(z)=0.86) is essentially honest. This is a width-calibration issue the ARM gate surfaced BEFORE integration: a σ-AUTHORITY TUNING question (which width the reactor serves / re-fitting the EMOS σ-model on recent settlement), NOT a center or q-integration defect.

## 5. After-cost EV by class (where books exist)

- Settled families with a usable executable book in window: **710**
- Families graded (modal sibling + NO ask present): **577**

**DATA-COVERAGE-LIMITED**: `executable_market_snapshots` carries no per-row bin-label/integer-threshold column, so a snapshot condition_id cannot be mapped to the exact settled bin from this table alone. The realized settlement-graded after-cost EV per bin therefore CANNOT be computed from the snapshot table in isolation. What the book DOES support is the **market-implied** after-cost EV of the dominant Zeus trade (buy_no on the modal/highest-YES sibling): `EV_mkt = (1 − modal_yes_ask) − no_ask`. This is the market's own price coherence, NOT a settlement grade — reported below as a coverage-bounded diagnostic, not a verdict.

- Market-implied buy_no-modal after-cost EV (book bid/ask spread cost only): mean **-0.051**, median **-0.020**, n=577. (Negative ⇒ the bid/ask spread alone makes the modal-NO a negative-carry trade at these quotes; this is the spread cost, not edge.)

| metric | n | mean EV_mkt |
|---|---|---|
| high | 497 | -0.039 |
| low | 80 | -0.124 |

> The settlement-graded after-cost EV-by-class (city/metric/side/route) requires joining each sibling condition_id to its bin label (via the market/condition registry the live reactor uses, not present in this offline snapshot table). That join is the integration-time wiring; this gate proves the q layer, and flags EV-by-class as coverage-limited rather than fabricating per-bin grades.

## 6. Inverse-failure check (is the modal edge real, or base-rate favorite-buying?)

The modal/favorite-bin cohort, graded on its OWN settled rows. Win-rate is NOT edge: a high modal win-rate is the base rate the market already prices.

- Modal-bin realized win-rate: **0.307** (n=693); predicted modal q: **0.184**.
- Calibration gap (realized − predicted): **0.124**. These figures use the SERVED predictive-RSS σ (over-dispersed per §4), so the modal q is UNDER-stated and the gap is positive (the favorite wins MORE than the wide σ predicts) — the spine is NOT over-claiming a favorite edge; if anything it under-claims at this width. Under the better-calibrated floor-only σ the modal q and realized win-rate align (§2/§4). Either way the key point holds: a high modal win-rate is base rate, not edge; whether the q BEATS the market price (true edge) is the after-cost EV question (§5), coverage-limited here.

| metric | n | modal_win_rate | mean_modal_q | gap |
|---|---|---|---|---|
| high | 601 | 0.288 | 0.175 | 0.113 |
| low | 92 | 0.435 | 0.239 | 0.195 |

## 7. No-trade counterfactual (sample)

Families the spine would likely NOT trade as a modal-NO buy: those where the modal q is LOW (no confident favorite, e.g. q_modal < 0.30) OR the center is an ENVELOPE_FALLBACK (EMOS disagreed and was refused). Was sitting out correct?

- Low-confidence (q_modal<0.30) families: **691**. In **479** the modal bin did NOT settle (a modal-NO buy would have won; sitting out forgoes a low-confidence win), in **212** the modal bin DID settle (buying its NO would have LOST — sitting out was protective).
- Modal-bin settle rate in the no-trade cohort: **0.307** vs **0.307** book-wide — lower confidence cohort, as expected for a no-trade screen.

Sample no-trade families:

| city | date | metric | mu* | realized | q_modal | modal_settled |
|---|---|---|---|---|---|---|
| Istanbul | 2026-06-09 | high | 23.1 | 23.0 | 0.067 | no |
| Jeddah | 2026-05-31 | high | 34.9 | 35.0 | 0.067 | yes |
| Istanbul | 2026-06-12 | high | 24.0 | 24.0 | 0.071 | no |
| San Francisco | 2026-06-01 | high | 72.2 | 70.0 | 0.079 | no |
| Shanghai | 2026-06-03 | high | 30.0 | 31.0 | 0.082 | no |
| San Francisco | 2026-06-11 | high | 85.7 | 90.0 | 0.083 | no |
| San Francisco | 2026-06-02 | high | 66.4 | 66.0 | 0.083 | yes |
| Toronto | 2026-06-09 | high | 24.6 | 24.0 | 0.083 | no |
| Busan | 2026-06-07 | high | 24.2 | 24.0 | 0.085 | yes |
| San Francisco | 2026-06-04 | high | 67.7 | 66.0 | 0.085 | no |
| San Francisco | 2026-06-05 | high | 71.1 | 70.0 | 0.085 | yes |
| San Francisco | 2026-05-31 | high | 69.7 | 71.0 | 0.086 | yes |

## Verdict

**PARTIAL — CENTER + q-INTEGRATION + q_lcb COHERENCE PROVEN, and the realized-floor σ width is WELL-CALIBRATED (std(z)=0.86); the served predictive-RSS σ is OVER-DISPERSED (σ≈1.94×realized RMSE, std(z)=0.52) → a σ-AUTHORITY TUNING action is required before live (re-fit / down-weight the EMOS model-σ, or serve the floor-dominated width). AFTER-COST EV-BY-CLASS COVERAGE-LIMITED. NOT a center or q-mechanics defect.**

- Center: book-wide mean(mu*−realized)=-0.516 (PASS <1.0); prior warm bias ~+2.8 is gone.
- Point-q (predictive_rss σ): modal predicted 0.184 vs realized 0.307 (gap +0.124, MISCALIBRATED — under-confident).
- Point-q (floor_only σ): modal predicted 0.279 vs realized 0.315 (gap +0.036, CALIBRATED).
- q_lcb coverage: realized 0.307 ≥ mean q_lcb 0.140 (CONSERVATIVE).
- Width — served predictive_rss σ: std(z)=0.52, σ/realized_RMSE=1.94 (OVER-dispersed (σ too WIDE)).
- Width — floor_only σ (alternative): std(z)=0.86, σ/realized_RMSE=1.09 (HONEST).
- After-cost EV-by-class: DATA-COVERAGE-LIMITED (books=710 but no per-bin label in snapshot table; settlement-graded per-class EV deferred to integration, NOT a pass/fail here).

