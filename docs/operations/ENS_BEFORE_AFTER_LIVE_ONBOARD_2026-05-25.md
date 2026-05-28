# Before/After Live Onboard: full_transport_v1 Calibration

Generated: 2026-05-25
Authority: ENS_REFIT_FULLDB_HIGH_2026-05-25.md §4.1, ENS_REFIT_FULLDB_LOW_2026-05-25.md §4.1
PR: #64 (full_transport_v1 flag-gate, currently OFF=byte-identical)

---

## What PR #64 changes in the live pipeline

**Before (#64 not landed / flag OFF):** `calibration_pairs_v2` rows used by the Platt calibrator come
exclusively from the `none` error-model family. The ensemble's raw bin probabilities (no EMOS mean
shift) are the calibration training signal. `p_raw` reflects a vanilla Monte Carlo draw over TIGGE
members with no location-correction applied.

**After (#64 landed / flag ON):** `calibration_pairs_v2` routing switches to `full_transport_v1`
rows. The EMOS model's mean-shift (estimated per city/cluster/season bucket, fitted on historical
TIGGE residuals) is folded into the Monte Carlo draw before binning. The Platt calibrator then
trains on these transport-adjusted probabilities. Live inference uses the transport-corrected p_raw
as the basis for edge estimates.

**Scale of change:** 17.6M transport rows (HIGH+LOW combined) replace 36.9M none rows as the
Platt training surface. Groups-per-city change because the transport model requires `ensemble_snapshots_v2`
membership; cities without snapshot coverage fall back to `none` automatically (the table simply
lacks `full_transport_v1` rows for them).

---

## Per-cohort before → after proper scores

All numbers from group-blocked 5-fold OOS evaluation on `/tmp/ens_refit/full.db`.
Positive delta = full_transport is WORSE. Negative delta = full_transport is BETTER.

### HIGH temperature (daily max)

| Cohort | Brier(raw→ft) | LogLoss(raw→ft) | RPS(raw→ft) | ECE(raw→ft) | Verdict |
|---|---|---|---|---|---|
| **global** | 1.0381 → 0.8838 (−0.154) | 7.2608 → 2.5543 (−4.71) | 1.5793 → 1.4999 (−0.079) | 0.0083 → 0.0010 (−0.007) | SHIP |
| coastal | 0.9951 → 0.8776 (−0.118) | 6.5743 → 2.7843 (−3.79) | 1.3414 → 1.4205 (+0.079) | 0.0076 → 0.0021 (−0.005) | MIXED (RPS +7.9%) |
| inland | 1.0434 → 0.8855 (−0.158) | 7.3452 → 2.4901 (−4.85) | 1.6085 → 1.5221 (−0.086) | 0.0083 → 0.0007 (−0.008) | SHIP |
| unit=°F | 0.9839 → 0.8812 (−0.103) | 6.2176 → 2.4347 (−3.78) | 1.4695 → 1.3784 (−0.091) | 0.0073 → 0.0013 (−0.006) | SHIP |
| unit=°C | 1.0487 → 0.8848 (−0.164) | 7.4640 → 2.6017 (−4.86) | 1.6006 → 1.5481 (−0.053) | 0.0084 → 0.0010 (−0.007) | SHIP |
| **city=Hong Kong** | 0.9775 → **1.1551** (+0.178) | 5.9922 → **9.9493** (+3.96) | 1.0993 → **5.1732** (+4.07) | 0.0067 → 0.0155 (+0.009) | REGRESSION |
| **city=Miami** | 0.7658 → **0.8909** (+0.125) | 1.7963 → **2.3662** (+0.570) | 0.6197 → **1.2228** (+0.603) | 0.0030 → 0.0039 (+0.001) | REGRESSION |
| city=Shanghai | 1.2012 → **1.0033** (−0.198) | 13.4605 → **5.1179** (−8.34) | 2.3652 → **2.8440** (+0.479) | 0.0124 → 0.0086 (−0.004) | MIXED |
| city=Tokyo | 1.0312 → 0.9026 (−0.129) | 7.4648 → 2.4179 (−5.05) | 1.4171 → 1.5436 (+0.127) | 0.0082 → 0.0023 (−0.006) | SHIP (RPS marginal) |
| city=Beijing | 1.0239 → 0.9354 (−0.088) | 6.1305 → 2.9727 (−3.16) | 1.5538 → 2.0571 (+0.503) | 0.0076 → 0.0000 (−0.008) | MIXED |
| lead=0 | 1.1081 → 0.8589 (−0.249) | 8.6321 → 2.4280 (−6.20) | 1.4765 → 1.3256 (−0.151) | 0.0102 → 0.0008 (−0.009) | SHIP |
| lead=6-7 | 0.9957 → 0.9054 (−0.090) | 6.2817 → 2.6952 (−3.59) | 1.7163 → 1.7494 (+0.033) | 0.0069 → 0.0012 (−0.006) | SHIP |

### LOW temperature (daily min)

| Cohort | Brier(raw→ft) | LogLoss(raw→ft) | RPS(raw→ft) | ECE(raw→ft) | Verdict |
|---|---|---|---|---|---|
| **global** | 1.0218 → 0.8697 (−0.152) | 6.5051 → 2.2147 (−4.29) | 1.4237 → 1.0220 (−0.402) | 0.0085 → 0.0032 (−0.005) | SHIP |
| coastal | 1.1295 → 0.8820 (−0.247) | 8.9700 → 2.2846 (−6.69) | 1.7706 → 1.0489 (−0.722) | 0.0107 → 0.0035 (−0.007) | SHIP |
| inland | 1.0112 → 0.8441 (−0.167) | 6.2641 → 2.0702 (−4.19) | 1.3898 → 0.9662 (−0.424) | 0.0083 → 0.0050 (−0.003) | SHIP |
| **city=Hong Kong** | 1.4376 → **0.8815** (−0.556) | 24.4051 → **2.1435** (−22.3) | 3.3148 → **0.9228** (−2.39) | 0.0178 → 0.0055 (−0.012) | SHIP (LOW rescues HK) |
| **city=Miami** | 1.3289 → **0.8461** (−0.483) | 11.7199 → **2.0685** (−9.65) | 1.9255 → **0.9199** (−1.006) | 0.0168 → 0.0069 (−0.010) | SHIP (LOW rescues Miami) |
| city=Tokyo | 0.9892 → **1.0081** (+0.019) | 4.5154 → **2.9669** (−1.55) | 1.0124 → **1.3890** (+0.377) | 0.0080 → 0.0088 (+0.001) | MARGINAL |

---

## Summary of production behavior shifts

**What improves (the majority):**
- Brier and LogLoss improve globally (HIGH: −15% Brier, −65% LogLoss; LOW: −15% Brier, −66% LogLoss).
- ECE drops sharply everywhere (HIGH global: 0.0083 → 0.0010; near-perfect calibration).
- Low-probability tropical events (Jeddah, Kuala Lumpur, Guangzhou HIGH) gain large LogLoss wins from
  the mean-shift correcting forecast warm bias in extreme regimes.
- LOW coastal regression (raw HIGH) is eliminated: coastal LOW improves by −0.722 RPS.

**What regresses:**
- HK HIGH: catastrophic across all scores (Brier +18%, LogLoss +66%, RPS +370%). The transport model
  over-disperses HIGH probabilities for Hong Kong daily max — the EMOS mean shift pushes mass to
  the wrong tail.
- Miami HIGH: regression on all scores (Brier +16%, LogLoss +32%, RPS +97%). Same mechanism.
- HK LOW and Miami LOW: both IMPROVE under full_transport (−22.3 LogLoss for HK LOW). The regression
  is HIGH-specific.

**What the operator approves when #64 lands:**
1. Global probability calibration improves substantially for both HIGH and LOW.
2. HK HIGH and Miami HIGH regress under unconditional full_transport application.
3. The SNR gate (ens_error_model.py correction_strength, PR #335) governs live inference — whether
   HK/Miami HIGH are gated to λ=0 (no shift → near-none behavior) or λ=1 (full shift → regression)
   in production has NOT been measured here. This measurement requires the production zeus-world.db
   with live bias posteriors and is delegated to the opus agent.
4. §4.2 p_cal audit (Platt-on-full_transport vs p_raw-direct) has not completed — Platt fit on
   16.9M groups timed out at 600s per fold. Result pending.

---

## Cohort ship/no-ship verdicts (ungated, from §4.1 only)

These verdicts apply if full_transport is deployed WITHOUT the SNR gate routing to raw for high-
variance buckets. The gated verdicts require live bias posteriors (see PENDING items below).

| Metric | HIGH | LOW |
|---|---|---|
| Global | SHIP | SHIP |
| Inland | SHIP | SHIP |
| Coastal | MARGINAL (RPS +7.9%) | SHIP |
| city=HK | DO NOT SHIP | SHIP |
| city=Miami | DO NOT SHIP | SHIP |
| city=Shanghai | MARGINAL | SHIP |
| city=Beijing | MARGINAL | SHIP |
| city=Tokyo | SHIP | MARGINAL |
| All other cities | SHIP (all show LogLoss improvement) | SHIP |

**Conclusion (ungated):** Full_transport ships globally with a carve-out for HK HIGH and Miami HIGH
unless the SNR gate already routes them to raw. The LOW metric is a blanket SHIP. The HIGH regression
is geographically confined to 2 of 48 cities and is specific to the HIGH (daily max) temperature
metric.

---

## PENDING — handed to opus agent

1. **Gated regression re-measurement**: does `correction_strength` = 0 for HK/Miami HIGH in
   production (live bias posteriors from zeus-world.db)? If yes, carve-out is unnecessary.
2. **§4.2 p_cal audit**: Platt-on-full_transport vs p_raw-direct. Requires restarting blocked
   5-fold Platt fit (16.9M HIGH rows × 5 folds, ~600s per fold — needs dedicated compute slot).
3. **§4.3 decision audit**: edge distribution, Kelly-size, candidate count, false-positive-edge rate.
   Requires production zeus-world.db + zeus-forecasts.db after refit calibration is migrated.
   full.db trade tables (decision_events, execution_fact, opportunity_fact) are empty.
   Tables needed: decision_events.edge, decision_events.target_size_usd, execution_fact.*,
   opportunity_fact.*, probability_trace_fact.
