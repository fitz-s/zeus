# Alpha Hunt — Where Does the Model BEAT the Market Price, Out-of-Sample, After Fee?

**Date:** 2026-06-14
**Mode:** READ-ONLY. DBs opened `?mode=ro`, timeout 25s. Unit = de-duplicated `(city, target_date, bin_label, direction)` EVENT (never per-receipt). Raw scripts in `/tmp/*.py`, `/tmp/*.sql`.
**Mandate (RULE 1):** "no edge" is never a conclusion — only "we haven't found WHERE yet." The prior agent (`edge_existence_decisive.md`) found all price-BAND averages ≤0. This hunt goes BEYOND price band: it slices by lead-horizon, model-vs-market disagreement, city, calibration, direction, and validates promising cells OUT-OF-SAMPLE.

---

## VERDICT

**NO POSITIVE CELL survives at adequate n + out-of-sample**, across every slice tried: lead-horizon (day0/1/2 + hours-to-EOD), disagreement decile, per-city (with temporal OOS split), per-direction, and model-prob calibration band. The one in-sample positive cell (5 cities, buy_no, +0.083, CI[+0.050,+0.115], n=63) **collapses to −0.020 (CI spans 0) out-of-sample** — confirmed multiple-comparisons overfit, not alpha.

The genuine gate is **forecast precision**: the model's central-tendency error is ~1.5 units median ≈ one bin-width (reproduces the prior 1.30 °C), AND there is an **adverse-selection signature** — the candidate population resolves ~1.9¢/share WORSE than the market price implied *before* the fee, symmetric across both directions. The model's disagreement with the market is anti-predictive: where it disagrees MOST, it loses MOST. The highest-MAE-reduction lever is sharpening the forecast central tendency (data-source / nowcast precision), not any calibration/gate relaxation. **Confidence: HIGH** (OOS-validated, CI-backed, symmetric across directions).

---

## SLICE-BY-SLICE RESULTS (after-fee edge = WR − cost − 0.01; event-deduped; bootstrap 95% CI)

Substrate: `zeus-world.db::no_trade_regret_events`, 40,009 graded rows → **1,468 deduped events** (993 buy_no, 475 buy_yes), window 2026-05-29→06-14. This is the only large substrate carrying model-q + market-cost + settled-outcome jointly. `probability_trace_fact` (full model+market distributions) is a disjoint, smaller window (05-03→05-21, only 88 matched settled events) and corroborates.

### 1. By LEAD-HORIZON (suspect #1 — nowcast/day0) — NEGATIVE
| dir | lead | n | WR | cost | edge | CI |
|---|---|---:|---:|---:|---:|---|
| buy_no | day0 | 131 | 0.786 | 0.791 | **−0.0149** | [−0.061,+0.030] |
| buy_yes | day0 | 66 | 0.015 | 0.029 | **−0.0243** | [−0.051,+0.011] |
| buy_no | day1 | 633 | 0.907 | 0.929 | −0.0322 | [−0.054,−0.012] |
| buy_yes | day1 | 371 | 0.075 | 0.097 | −0.0312 | [−0.055,−0.009] |

Hours-to-EOD refinement: true-nowcast (≤12h) cells are **n=0–5** — the system decides 1–2 days out, so the regret substrate has no short-lead population to harvest. Day0 is the least-bad but still negative, CI spans 0. **No nowcast alpha exists in the recorded data; the day0 lane has near-zero settled sample.**

### 2. By DISAGREEMENT |q_live − cost| (suspect #2) — ANTI-PREDICTIVE
| dir | slice | n | edge | CI |
|---|---|---:|---:|---|
| buy_no | top-10% disagreement (≥+0.20) | 99 | **−0.0887** | [−0.180,−0.002] |
| buy_no | top-25% | 248 | −0.0504 | [−0.102,+0.001] |
| buy_yes | top-10% (≥+0.11) | 47 | −0.0243 | [−0.117,+0.080] |

**The model's disagreement is NOISE, not edge.** Where it disagrees MOST with the market, it loses MOST (top-decile buy_no −8.9¢, CI excludes 0). Classic adverse-selection / overconfidence signature — the opposite of an alpha signal.

### 3. PER-CITY + OUT-OF-SAMPLE (the only in-sample winner — FAILS OOS)
- In-sample, 5 cities (Guangzhou, Wellington, Lucknow, Cape Town, Istanbul) buy_no pooled: n=63, WR=0.984, **edge +0.0826, CI[+0.050,+0.115]** — looked real.
- **Temporal OOS test:** select positive-edge cities (n≥4) on the first half (≤06-06), apply the rule to the held-out second half → **n=97, edge −0.0203, CI[−0.071,+0.032]** — indistinguishable from the −0.0238 baseline of buying *every* favorite. **The city edge does NOT persist. In-sample overfit.**

### 4. CALIBRATION — model is OVERCONFIDENT (the root mechanism)
- buy_no pooled: model claims q_live(NO)=0.945, realized WR=**0.884** → miscalibrated HIGH by 6 pts. Claimed +4.2¢ edge → realized −2.9¢.
- buy_yes pooled: n=475, edge **−0.0281, CI[−0.048,−0.008]** (significant). High-confidence bands invert: q≈0.35 band realizes 0.059; q≈0.56 band realizes 0.333.

### 5. MARKET-EFFICIENCY / ADVERSE-SELECTION SIGNATURE (unifying finding)
| dir | n | WR | cost | WR−cost (pre-fee) | after-fee |
|---|---:|---:|---:|---:|---:|
| buy_yes | 475 | 0.0695 | 0.0876 | **−0.0181** | −0.0281 |
| buy_no | 993 | 0.8842 | 0.9032 | **−0.0190** | −0.0290 |
| BOTH | 1468 | 0.6206 | 0.6393 | **−0.0187** | −0.0287 |

Symmetric **−1.9¢ pre-fee** gap in BOTH directions: this candidate population (trades the system flags as model-favored) resolves ~1.9¢ worse than the market price implied, *before* the fee. The market is not merely efficient here — the system's candidate generation is **anti-selected**: it surfaces exactly the bins where the model is wrong and the market is right. −1.9¢ adverse selection + 1.0¢ fee = the −2.9¢ seen in every powered cell.

### 6. FORECAST-PRECISION GATE (confirms prior, fresh measurement)
Posterior-panel central tendency (`forecast_posteriors`, settled, n=295): **median |model_mean − settled| = 1.57 units**; within-1.0 = 36%. ≈ one bin-width. A model whose center is one bin-width uncertain cannot pin the exact bin — the residual is irreducible NWP center noise at this lead, not a tunable bound.

---

## WHAT WOULD CHANGE THE VERDICT (powered cell not yet available)
- **True nowcast (≤12h-to-EOD, day-of):** the regret substrate has n≤5 here because the system decides 1–2 days out. The day0-obs/nowcast lane (tasks #68–75) is the one structure NOT empirically refuted — only because **no settled sample exists yet**. To test it needs the system to record same-day, post-observed-extreme decisions and accumulate ≈150 settled events. This is the single highest-value experiment: short-lead is where forecast MAE collapses and the only place a precision edge could appear.
- **Forecast central-tendency sharpening:** median error 1.5→<0.7 units would move exact-bin hit from ~24% toward the ~50% needed for directional edge. Highest-MAE cities to target (0.00 exact-hit in prior measurement): Seoul, Taipei, Chengdu, Wuhan — i.e., the data-source-audit track on those stations.

---

## RAW (deciding numbers)
- buy_yes pooled after-fee: **−0.0281**, CI[−0.0476,−0.0078], n=475.
- buy_no pooled after-fee: **−0.0290**, CI[−0.0466,−0.0126], n=993.
- Top-decile disagreement buy_no: **−0.0887**, CI[−0.180,−0.002], n=99 (anti-predictive).
- 5-city buy_no in-sample +0.0826 → **OOS −0.0203, CI[−0.071,+0.032]** (overfit, refuted).
- Adverse selection: WR−cost = **−0.019** pre-fee, symmetric both directions.
- Central-tendency median error **1.57 units ≈ 1 bin-width**.

*End alpha hunt. Read-only. Every slice tried before the negative verdict; the one promising cell was OOS-validated and failed. Scripts: /tmp/regret_panel.py, /tmp/oos.py, /tmp/baserate.py, /tmp/buyyes_pool.py, /tmp/nowcast_fine.py, /tmp/market_eff.py, /tmp/build_panel.py, /tmp/dir_skill.py.*
