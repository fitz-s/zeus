# BACKFILL_NOW — retrospective U0R activation (no forward wait)

```
Created: 2026-06-08
Authority basis: operator directive 2026-06-08 ("不等 forward accrual; 现在就处理好"; validate retrospectively from existing evidence — the Big-Bang-from-present-evidence principle)
```

## Principle
U0R fusion does NOT need 25 days of FORWARD live accrual. The historical fixed-lead
multi-model data already exists and the math is already settlement-proven. Activation =
**backfill the history store from existing data NOW + validate retrospectively today.**

## Seed source (verified present, live tree, read-only)
`/Users/leofitz/zeus/.omc/research/polyweather_eval/B0_multilead_dataset.json` (6.6 MB):
- Shape `{city: {"leads": {lead: {model: {target_date: [high_c, low_c]}}}}, "_settle_high":…, "_settle_low":…}`
- **50 cities**, leads 1/2/3/5/7, **189 target_dates/cell** (2025-12-01 → 2026-06-… ).
- Models: `ecmwf_ifs`, `gfs_global`, `icon_global`, `gem_global`, `jma_seamless` (all 5 globals, ~250 city×lead cells each), `icon_eu` (33), `icon_d2` (5, EU only), `meteofrance_arome_france_hd` (6).
- **100% of (city,lead,model) cells have ≥189 dates ≫ MIN_TRAIN=25** → fusion reaches T2_BAYES for every city on seed.
- `u0r_fixed_lead_dataset.json` = the dedup-applied fixed-lead variant (10 cities, `_meta.created=2026-06-08`).

## Rails (Fault B, src/state/schema/v2_schema.py raw_model_forecasts + src/data/u0r_history_provider.py)
`raw_model_forecasts(model, city, target_date, metric∈{high,low}, source_cycle_time, source_available_at, captured_at, lead_days, forecast_value_c [degC], endpoint∈{single_runs,previous_runs}, trade_authority_status='SHADOW_ONLY', training_allowed=0, UNIQUE(model,city,target_date,metric,source_cycle_time,endpoint))`.
`U0RHistoryProvider(conn)(... target_date ...)` reads `endpoint='previous_runs'` JOIN `settlement_outcomes` (same zeus-forecasts.db) `authority='VERIFIED'` AND `r.target_date < decision_date` (strict no-leak).

## Seed mapping (B0 → raw_model_forecasts), provenance-correct
For each (city, lead, model, target_date, [high_c, low_c]):
- two rows: metric='high' → forecast_value_c=high_c; metric='low' → low_c (degC, no F conversion).
- lead_days=int(lead); endpoint='previous_runs'; trade_authority_status='SHADOW_ONLY'; training_allowed=0.
- source_cycle_time = (target_date − lead_days)T00:00:00Z; source_available_at = same (fixed-lead causality).
- **CITY CANONICALIZATION (the provenance trap):** B0 city keys (`NYC`, `San Francisco`, `Hong Kong`, …) MUST be mapped to the exact `settlement_outcomes.city` identifiers or the history JOIN yields ZERO training (silent EQUAL_WEIGHT). Verify the JOIN actually yields rows per city before declaring success.
- idempotent UPSERT on the UNIQUE key.

## Activation order (today, no forward wait)
1. (operator, redeploy) run the seed script on LIVE zeus-forecasts.db → raw_model_forecasts populated (~50 cities × 189 × models × 2 metrics).
2. capture flag ON (forward recurring keeps it fresh) — but training history is ALREADY sufficient from the seed.
3. validation (today, retrospective) confirms fused T2_BAYES > single-anchor on VERIFIED settlement → promotion evidence satisfied.
4. flip `replacement_0_1_u0r_fusion_enabled` ON → T2_BAYES live.
