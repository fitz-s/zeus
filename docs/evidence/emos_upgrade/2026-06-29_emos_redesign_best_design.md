# EMOS latest-direction — measured, first-principles, current-frontier-grounded

**Date:** 2026-06-29
**Authority basis:** operator — "根据最新结构探索最新的emos方向" / "redesign, not upgrade a stale artifact" /
"act on first principles, not assert" / "我要答案不要更多问题". Explore the field's CURRENT (2024–2026)
EMOS direction, grounded in Zeus's latest structure AND in measured settled-data evidence.
**Money-path stage:** forecast signal → calibration (the full predictive distribution of settlement).
**Status:** DESIGN OF RECORD (supersedes the earlier "upgrade" + "skew-normal-as-headline" framings —
the measurements overturned those). No serving code changed yet; build phase next.

---

## TL;DR — the direction

**A hierarchical-Bayesian, CRPS-trained, tail-aware station post-processor** — the 2025 spatial-BHM
frontier line (branch B below), instantiated against Zeus's *measured* error channels. NOT the stale
parametric EMOS, NOT the generative/diffusion branch (consciously deferred), and NOT a "floor σ up"
move — the measured #1 defect is the opposite (gross OVER-dispersion).

---

## 1. Objective (first principles)
The market pays on which bin holds `Y = realized airport-METAR daily-MAX`. The whole job is one
function: `b ↦ P(Y ∈ b | today's info)`. Everything is instrumental.

## 2. First-principles error budget — the derivation
`Y` is the *max* of a continuous temp curve at *one point*. For any forecast `xₘ`, `Y − xₘ` decomposes
by physical origin into independent channels:
- `e_model(lead)` — NWP chaotic error. Grows with lead. ~symmetric.
- `e_repr(geometry)` — grid-vs-airport offset. ~lead-flat; bias + variance parts.
- `e_max(regime)` — order-statistic/timing error. **Right-skewed** (a max ignores cold off-peak hours).
- `e_obs` — METAR rounding.

Forced consequences: **center** = precision-weighted, representativeness-corrected estimate (Gauss-Markov;
the only legitimate "de-bias" is the physical `e_repr` offset, never a slope on `e_model` noise — this is
the exact, principled form of the RAW law). **width** = the lead/geometry/regime convolution of the four
channels (so a single lead-flat constant is wrong), floored at realized. **shape** = right-skew inherited
from `e_max`, shrinking as lead-dominated, growing at short lead / hot regimes. The channel variances are
EMPIRICAL — so the design is read off measurement, not asserted.

## 3. Measured channels (settled data, 2026-06-29; the data ruled over the derivation)
| Channel | Measured | Effect |
|---|---|---|
| **Width** | served σ = **3.0°C constant** (all 973 cases) vs realized RMSE **1.35°C**; PIT ∩ (χ²=218), 50%CI covers **82%** | **DOMINANT — ~2.2× too wide.** ~17–30% CRPS headroom. Inverts the "overconfidence=ruin, floor up" prior — the live posterior is grossly OVER-dispersed. |
| **Lead** | 972/973 settled cases are day0/1-day | lead-growth **untestable** now; derived hypothesis, not asserted |
| **Bias** | per-city: Taipei +1.6 warm, tropical warm (PIT>0.7), US cold; grid-distance = 0 everywhere (point interp) | real per-city bias, **NOT geometric** |
| **Shape** | center-residual skew **−0.27** (body ~Normal — "It Is Normal" 2025 agrees); right-skew is member-level + **hot-city upper tail (p99 mis-priced 3.5×)** | tail-localized, not a global family swap |
| **Pooling** | every (city,season,lead) cell n<100; 60% <50; median **38** | **mandatory** |
| **Station feature** | station ingest began 2026-06-28; **0** overlap with settled outcomes | **deferred** — revisit ~2026-07-10 |

## 4. The field's latest direction (2024–2026 frontier, researched)
Vector: EMOS/NGR ('05) → DRN ('18, CRPS-trained neural distributional regression) → boosted-EMOS / QRF /
Distributional-Regression U-Nets ('20–'24) → **the 2025–26 split:**
- **(A) Generative + direct-CRPS ensembles** — ECMWF **AIFS-ENS operational 2025-07, trained by direct
  CRPS** (replaced diffusion AIFS-DIFF); adopted by NVIDIA FourCastNet 3, DeepMind FGN; latent-diffusion +
  ViT to *enlarge* ensembles for extremes.
- **(B) Data-efficient hierarchical-Bayesian extreme-value station post-processing** — spatial BHM,
  non-stationary EV upper tail, elevation offset, station calibration + interpolation (Ertz-Friederichs 2025).

Unifying thread: **direct CRPS · tail-aware (non-Gaussian) · hierarchical/spatial pooling · physical
covariates (elevation/representativeness) · ensemble enlargement for tails.**

## 5. Committed design — branch B, instantiated on the measured channels
A **hierarchical-Bayesian, CRPS-trained, tail-aware station post-processor**, as a **post-processing layer
over the RAW fusion** (fusion consensus = one predictor):
- **partial pooling** across city/season/lead (thin-n forces it);
- **Normal body + EV/skew upper tail ONLY where measured** (hot cities) — the body stays Normal;
- **width = realized error** (kills the 2.2× over-dispersion), and **separate calibration from the q_lcb
  conservative margin** — the 3.0 constant conflated "match settlement" with "safety margin"; split them;
- **elevation/representativeness covariates** → the per-city bias channel;
- **direct CRPS** training (the frontier objective; the stale fitter already did CRPS — keep);
- **consume ECMWF AIFS-ENS** (new CRPS-trained ensemble) as a source member — a source-lane (加数据) upgrade.

RAW law relaxed narrowly: pooling + envelope-bounding + OOS gating cure the unpooled-overshoot that
justified the blanket ban; the layer owns a bounded, pooled, evidence-gated location correction whose
no-evidence default is the RAW consensus unchanged.

## 6. Consciously deferred (named, not omitted)
- **Branch A generative/diffusion** — it's a *forecast-model* technique (we consume NWP, not train it),
  and data/compute-hungry. Horizon, not near-term.
- **Station feature as load-bearing** — until settled overlap accrues (~2026-07-10).
- **Geometric representativeness** — grid-distance is 0 everywhere (point interpolation); revisit only if a
  per-model repr/elevation table is populated.

## 7. Build + validation (the move from explore → act)
1. **Prototype (next):** walk-forward, on settled data — recalibrate the width to realized + per-city bias +
   hot-city upper-tail skew — scored (CRPS, PIT/coverage) against the live σ=3.0 baseline, to **empirically
   confirm the ~17–30% headroom** before any model complexity.
2. If confirmed → the hierarchical-Bayesian pooled model (the full branch-B design).
3. Serving integration: parity / identity-hash discipline; conservatism applied separately at the q_lcb seam;
   no silent center/σ change to the served posterior.
Bar: CRPS + PIT/coverage + after-cost settlement EV, walk-forward, do-no-harm, no overconfidence regression.

## 7b. Walk-forward validation result (2026-06-29, settled data, strict no-leak)
The width lever is EMPIRICALLY VALIDATED; the magnitude is capped by the thin 19-day window; and the
data forced one important refinement.
- **Validated (#1 lever):** σ = 3.0 constant → per-city EB-shrunk realized error (~1.25°C) cuts walk-forward
  mean CRPS **11.4%** and fixes coverage (50%-interval 87% → 44%; PIT χ² 650 → 135). Over-dispersion (2.2×)
  confirmed as the dominant defect. Moderate-climate cities gain big (Tel Aviv +43%, Warsaw +42%, Karachi
  +41%, LA +41%, NYC +32%).
- **The surprise — naive recalibration HURTS hot/tropical cities:** Taipei −28%, Guangzhou −29%, Kuala Lumpur
  −32%, Seoul −15%. Cause: large warm bias (+1.4–1.6°C) + high variance, but ~8 prior cases ⇒ EB under-fixes
  the bias AND over-tightens σ ⇒ worse than the over-wide 3.0 baseline that at least covered the big residuals.
  **These are exactly the CWA/HKO station-data cities** — the station feature is the right fix for the hot-city
  bias the blanket recalibration cannot reach.
- **Refinements the evidence dictates:** width floor 1.0–1.5°C (not 0.8) to avoid over-sharpening thin cells;
  hot-city safeguard — elevated floor (σ≥2.0) or hold Taipei/Guangzhou/KL/Seoul until ≥30 prior cases or the
  station feature lands; **C2 skew-normal = NO-GO** (skewness un-estimable at n≤8; degrades CRPS), revisit at
  n≥30; full 17–30% headroom needs more settled history (now capped ~11% by the window) — re-validate ~4–6 wks.
- **Decision gate:** GO on C1 width+bias recalibration for moderate cities; HOLD the hot cities for the station
  data / more history; NO-GO on C2; offline + do-no-harm until it clears the bar (no silent live σ change).

## 8. Risks
Thin-n (pooling mitigates); lead-growth untestable until long-lead settles; station deferred but is now the
load-bearing fix for the hot cities where pure recalibration backfires; the over-dispersion fix interacts with
the q_lcb conservative margin (must be separated, not silently tightened).
