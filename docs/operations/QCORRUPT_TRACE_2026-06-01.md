# Q-Vector "Corruption" Numeric Trace — Singapore 2026-06-03 high

Created: 2026-06-01
Authority basis: read-only production-function reproduction at HEAD 6fcd05a69f
Scope: NO code edits, NO git, NO DB writes. Pure numeric trace.

## Verdict (8 lines)

1. The traded q is NOT corrupted by a defect. The warm shift is the **promoted
   per-city bias correction** `_maybe_apply_edli_bias_correction`
   (`event_reactor_adapter.py:3487-3567`), operating exactly as designed.
2. CULPRIT STAGE = **bias correction on member maxes, BEFORE p_raw**. It adds
   `+|effective_bias_c|` °C to every member (Singapore +1.584°C).
3. Members 30.63 mean → 32.21 mean. Modal mass moves 31→32 at p_raw; p_cal is
   identity passthrough (correct-by-design lockstep), so calibration is innocent.
4. Transform: `corrected = members - eff_native`, `eff_native = effective_bias_c`
   (°C city) = `members - (-1.5836) = members + 1.5836`. Sign is CORRECT
   (de-bias toward observed: obs = forecast + 1.58 for a cold forecast).
5. NOT MC rounding, NOT bin-index off-by-one, NOT Platt, NOT a sign error,
   NOT double-application (`members_already_corrected=True` guard verified).
6. Reproduced exactly: post-bias p_raw(31)=0.1229, p_raw(32)=0.5673 ==
   traded q_YES(31)=0.124, q_YES(32)=0.565.
7. Universal mechanism: Taipei +1.80°C (modal 33→35), Tokyo +3.45°C (modal 22→25).
8. This is the operator-activated A4 correction (config
   `edli_v1.edli_bias_correction_enabled=true`, settled-truth backtest
   Singapore bin_bias≤1 32%→63%). Whether the **magnitude** is desirable is a
   live-alpha policy question, not a code defect.

## Snapshot under trace (state/zeus-forecasts.db ensemble_snapshots)

```
city=Singapore target_date=2026-06-03 metric=high
snapshot_id=1151951  available_at=2026-06-01T00:00:00+00:00  source_id=ecmwf_open_data
dataset_id=ecmwf_opendata_mx2t3_local_calendar_day_max  settlement_unit=C  members_unit=degC
n_members=51  median=30.6646  mean=30.6308  min=29.43  max=32.00  lead_hours=48
```
City: WU_WSSS, settlement_unit=C, rounding=wmo_half_up, sigma_instrument=0.28.

## Per-stage vectors (bin labels …29,30,31,32,33…)

### STAGE 0 — raw members
mean 30.6308, median 30.6646.

### STAGE 1 — naive WMO-rounded member count (== task's "raw ensemble")
| bin | 29 | 30 | **31** | 32 | 33 |
|-----|----|----|--------|----|----|
| p   | 0.0392 | 0.3137 | **0.5882** | 0.0588 | 0.0000 |
Modal = 31 (0.588). **Matches task evidence exactly.**

### STAGE 2 — production p_raw via MC (p_raw_vector_from_maxes, sigma=0.28), NO bias
| bin | 29 | 30 | **31** | 32 | 33 |
|-----|----|----|--------|----|----|
| p   | 0.0348 | 0.3687 | **0.5188** | 0.0769 | 0.0008 |
Modal STILL = 31 (0.519). Analytic equivalent identical (0.5195).
**=> MC rounding does NOT collapse bin 31. p_raw is healthy.**

### STAGE 3 — BIAS CORRECTION (the culprit)  `event_reactor_adapter.py:3552`
`corrected = members - eff_native`, eff_native = effective_bias_c = -1.5836 °C
=> members shifted **+1.5836 °C**.  corrected mean 32.2144, median 32.2482.
Bias row (model_bias_ens, read_bias_model): effective_bias_c=-1.5835714,
weight_live=1.0, authority=VERIFIED, error_model_family=edli_per_city_v1,
season=JJA, month=6, live_data_version=ecmwf_opendata_mx2t3_local_calendar_day_max.

### STAGE 4 — p_raw ON CORRECTED members (production path, members_already_corrected=True)
| bin | 30 | **31** | **32** | 33 | 34 |
|-----|----|--------|--------|----|----|
| p   | 0.0011 | **0.1229** | **0.5673** | 0.2892 | 0.0195 |
Modal = 32 (0.567). **31 collapsed 0.519→0.123; 32 inflated 0.077→0.567.**

### STAGE 5 — p_cal (production _snapshot_p_cal)
IDENTITY passthrough because `payload['_edli_bias_corrected']=True`
(`event_reactor_adapter.py:3624-3629`): p_cal == normalized p_raw, bit-identical
to STAGE 4. Platt is bypassed by design (train/serve lockstep: existing Platt was
fit on UNcorrected p_raw, so corrected domain uses identity until refit).

### STAGE 6 — posterior q (MODEL_ONLY_POSTERIOR_MODE)
Fusion confirmed NOT pulling toward market (model-only posterior). q == p_cal:
q_YES(31)=0.123, q_YES(32)=0.567.
DB cross-check (no_trade_regret_events, direction-decoded):
- bin31 buy_no q_NO=0.8762 => q_YES(31)=0.1238 ✓
- bin32 buy_yes q_YES=0.5650 ✓
- bin30 buy_no q_NO=0.9988 => q_YES(30)=0.0012 ✓

## The single stage where 31 loses mass

| transition | p(31) | p(32) |
|------------|-------|-------|
| naive raw | 0.588 | 0.059 |
| MC p_raw (no bias) | 0.519 | 0.077 |
| **+bias +1.584°C → p_raw** | **0.123** | **0.567** |
| p_cal (identity) | 0.123 | 0.567 |
| q posterior | 0.123 | 0.567 |

The ENTIRE collapse occurs at the +1.584°C member shift. Nothing downstream of it
moves the vector.

## Cross-checks (same mechanism, universal)

| city | date | raw modal | eff_bias_c | shift | post-bias modal |
|------|------|-----------|-----------|-------|-----------------|
| Singapore | 06-03 | 31 (0.588) | -1.584 | +1.584 | 32 (0.567) |
| Taipei | 06-02 | 33 (0.745) | -1.803 | +1.803 | 35 (0.748); 33→0.000 |
| Tokyo | 06-02 | 22 (0.667) | -3.447 | +3.447 | 25 (0.533); 22→0.000 |

## Classification

NOT: bias-correction sign error / MC rounding / bin-index off-by-one / calibration.
IS: **bias-correction MAGNITUDE** — the operator-activated A4 per-city additive
warm correction (config `edli_v1.edli_bias_correction_enabled=true`), applied at a
single verified site, correct sign, no double-apply, settled-truth-validated
direction. The "corruption" symptom is the correction doing precisely what it was
turned on to do; the open question is whether a static seasonal +1.58–3.45°C
additive shift is the right LIVE magnitude (June extrapolation off May fit), which
is what `bias_decay_kelly_haircut` (also enabled) is meant to hedge.

## Critical unknown / discriminating probe

Critical unknown: is the JJA effective_bias_c (fit on May settled targets,
extrapolated to June) the right live magnitude, or is it over-warming June?
Discriminating probe: once 2026-06-03 settles, compare observed Singapore high
vs raw-modal (31) vs corrected-modal (32). If observed=31, the correction
over-warmed (instance of May→June extrapolation drift); if observed=32, the
correction recovered the true bin from a cold raw ensemble.
