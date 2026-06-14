# EMOS μ* Bias Probe — D4

**Date:** 2026-06-14  
**Purpose:** Determine whether the live EMOS-served μ* absorbs the per-city cold bias, or lands cold — deciding whether S2/S3 defects are material for the live q or INFO-only.

---

## Producer trace

`edli_emos_sole_calibrator_enabled = True` (confirmed in `config/settings.json`).

Live q path: `event_reactor_adapter.py:10844-10876`  
→ `build_emos_q()` (`src/calibration/emos_q_builder.py`)  
→ `emos_predictive()` (`src/calibration/emos.py:339`)  
→ **μ* = a + b·x̄_ensemble** where x̄ is the mean of ECMWF ensemble member maxima in °C.

The returned `(q_vector, mu_native, sigma_native)` tuple is the live traded distribution. μ* IS the center that feeds both the point q and the q_lcb bootstrap. The IFS single-run `anchor_value_c` (in `deterministic_forecast_anchors`) is a separate artifact used by the replacement forecast lane (SHADOW_ONLY), NOT by the live EMOS calibrator.

EMOS calibration table: `state/emos_calibration.json`, 400 cells, `served="emos"` for all target city-season-high combinations. Shadow ledger (`state/emos_shadow_ledger.jsonl`) covers only 2026-06-02 to 2026-06-03 (284 603 entries but only 5 unique city-date pairs across target cities) — too thin for a statistical verdict. Primary evidence is computed from `ensemble_snapshots` (ECMWF, `contributes_to_target_extrema=1`) + EMOS params applied analytically.

**Evidence basis:** `zeus-forecasts.db` (copied to `/tmp/` for read-only access), `emos_calibration.json`, `settlement_outcomes` (VERIFIED authority only).

---

## Per-city signed residuals: μ* − settlement (°C)

Genuine forecast leads only: 24 h ≤ lead ≤ 144 h. Shortest qualifying snapshot per date used. Settlement converted to °C where F-settled.

| City | n | mean(μ*−settled) | median | std | cold days (<−0.5°C) | warm days (>+0.5°C) |
|------|---|-----------------|--------|-----|---------------------|---------------------|
| **Tokyo** | 31 | **−1.146°C** | −0.824°C | 2.035°C | 19/31 (61%) | 10/31 (32%) |
| **San Francisco** | 31 | +1.055°C | +0.072°C | 3.097°C | 13/31 (42%) | 14/31 (45%) |
| **Beijing** | 32 | +0.405°C | −0.274°C | 2.481°C | 13/32 (41%) | 13/32 (41%) |
| **Karachi** | 20 | +1.145°C | +0.938°C | 0.799°C | 0/20 (0%) | 14/20 (70%) |

Coverage: 88 VERIFIED settled dates for Tokyo, 73 SF, 75 Beijing, 55 Karachi. Ensemble snapshot availability covers 2026-05-07 onward; ~57 settled dates per city fall before that window (pre-deployment of the ensemble pipeline) and are excluded.

### Per-season breakdown

**Tokyo**

| Season | n | mean | median |
|--------|---|------|--------|
| MAM | 18 | **−1.368°C** | **−1.890°C** |
| JJA | 13 | −0.839°C | −0.368°C |

**San Francisco**

| Season | n | mean | median |
|--------|---|------|--------|
| MAM | 19 | −0.089°C | −0.414°C |
| JJA | 12 | **+2.868°C** | **+2.451°C** |

**Beijing**

| Season | n | mean | median |
|--------|---|------|--------|
| MAM | 19 | +0.117°C | −0.586°C |
| JJA | 13 | +0.825°C | +0.698°C |

**Karachi (MAM only in window)**

| Season | n | mean | median |
|--------|---|------|--------|
| MAM | 20 | +1.145°C | +0.938°C |

---

## IFS single-run anchor vs settlement (S2/S3 context — NOT the live μ*)

| City | n | mean(anchor−settled) | median |
|------|---|----------------------|--------|
| Tokyo | 6 | −1.100°C | −1.750°C |
| San Francisco | 5 | +0.816°C | −0.278°C |
| Beijing | 6 | −1.667°C | −1.700°C |
| Karachi | 6 | +1.700°C | +1.950°C |

The IFS single-run anchor is used only by the replacement forecast (SHADOW_ONLY lane). The live EMOS μ* uses the full 50-member ensemble mean, not this deterministic run. S2 (absent per-city representativeness de-bias) and S3 (dead `_bias_corrected=False` path under `edli_emos_sole_calibrator_enabled=True`) therefore act on the ensemble mean xbar, not on anchor_value_c.

---

## Verdict

### Tokyo — EMOS-LANDS-COLD

mean(μ* − settlement) = **−1.146°C**, median = **−0.824°C**, n=31.  
Cold-day fraction: 61% of days have μ* more than 0.5°C below settlement.  
MAM season worst: median −1.890°C. JJA: median −0.368°C (less severe but still net cold).  
The cold bias IS present in the live EMOS μ* and is substantial. The EMOS calibration does NOT absorb the per-city cold bias for Tokyo.

### San Francisco — EMOS-INDETERMINATE (season-split)

Overall mean = +1.055°C, median = +0.072°C, n=31. High variance (std=3.097°C).  
MAM: near-zero (mean −0.089°C). JJA: strongly warm-biased (mean +2.868°C, median +2.451°C).  
The MAM season (which dominates the window) is approximately unbiased. JJA shows an apparent warm overshoot in EMOS μ*, opposite to the cold-bias hypothesis for that season. The SF JJA warm bias in EMOS μ* warrants a separate investigation (possible MAM-fit params crossing into early JJA; SF summer is unusual for a forecast).

### Beijing — EMOS-APPROXIMATELY-UNBIASED

mean = +0.405°C, median = −0.274°C, n=32. Residuals are approximately symmetric around zero. MAM median = −0.586°C (mild cold lean) but JJA mean = +0.825°C. No clear systematic cold bias in EMOS μ* for Beijing. The EMOS calibration largely absorbs the bias here.

### Karachi — EMOS-LANDS-WARM

mean = +1.145°C, median = +0.938°C, n=20 (MAM only). EMOS μ* is consistently warm-biased vs settlement, not cold. Zero cold-day events. This is inconsistent with the warm-crusher story — EMOS is already over-predicting warm in Karachi, which would over-weight warm bins, not crush them.

---

## S2/S3 materiality verdict

| Defect | Tokyo | San Francisco | Beijing | Karachi |
|--------|-------|---------------|---------|---------|
| S2 (absent de-bias artifact) | **MATERIAL** | INFO-only (MAM) / investigate JJA overshoot | INFO-only | INFO-only (warm not cold) |
| S3 (dead bias-correction lane) | **MATERIAL** | INFO-only (MAM) | INFO-only | INFO-only |

**For Tokyo specifically:** the live EMOS μ* is cold by ~1.1°C mean (1.9°C median in MAM). This cold center shifts q mass toward cold bins, reducing probability on warm-side winners. The S2/S3 defects are REAL and MATERIAL for Tokyo. The offset refit (#90) is on the critical path for Tokyo.

**For SF, Beijing, Karachi:** the EMOS calibration absorbs enough of the per-city signal that S2/S3 are not primary drivers. SF JJA warm overshoot in μ* is a separate anomaly that requires its own investigation (potentially the `b=1.161` slope amplifying ensemble warm signals in summer).

---

## Critical unknown

Whether the Tokyo cold residual (−1.1°C mean) is driven primarily by:
(a) the absent S2 representativeness de-bias on xbar (the grid-to-station cold offset), or
(b) the EMOS `a` intercept being fit on a training set that was itself cold-biased (so the EMOS fit absorbed the cold anchor and perpetuates it), or
(c) both acting additively.

Distinguishing (a) from (b) matters for whether the fix is a data-artifact correction (S2 refit) or an EMOS re-calibration with clean observations.

## Discriminating probe

Compare the EMOS training residuals for `Tokyo|MAM|high` against the per-city representativeness correction magnitude. Specifically: run `emos_calibration.py` with and without the S2 de-bias applied to xbar on held-out 2025 data, and compare the resulting CRPS. If applying S2 de-bias before EMOS fitting reduces the cold offset in μ*, (a) is the dominant driver. If the EMOS intercept `a=−1.25` (Tokyo|MAM) already absorbs it and residuals remain cold, (b) is the driver and requires fresh observation-sourced re-calibration.

---

## Provenance

- DB: `state/zeus-forecasts.db` copied to `/tmp/zeus-forecasts-probe.db`, ro
- EMOS params: `state/emos_calibration.json` (created 2026-06-02, last audited 2026-06-07)
- Shadow ledger: `state/emos_shadow_ledger.jsonl` (284 603 entries, 2026-06-02 to 2026-06-03 only — too thin for independent verdict)
- Code path confirmed: `src/engine/event_reactor_adapter.py:10820-10876`, `src/calibration/emos_q_builder.py:33-113`, `src/calibration/emos.py:317-345`
- Settings: `edli_emos_sole_calibrator_enabled=True`, `edli_bias_correction_enabled=True` (bias correction active on the else/DAY0 branch only — not reached when `_emos_q is not None`)
