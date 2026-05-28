# Calibration Keying Audit — Lead/Horizon Dimension
**Date:** 2026-05-28  
**Scope:** model_bias_ens_v2 (bias), platt_models_v2 (Platt), calibration_pairs_v2 (training data)  
**Code root:** ens-bias-hierarchical worktree  
**Status:** READ-ONLY — no fixes applied

---

## Q1. Is bias_c keyed per lead/horizon/cycle?

**Answer: NO. bias_c is keyed only by (city, season, month, metric, live_data_version).**

- `model_bias_ens_v2` schema (zeus-world.db, audited live): columns are `city, season, month, metric, live_source_id, live_data_version, …, bias_c, …`. **No `lead_days`, `lead_bucket`, `horizon_profile`, or `cycle` column.**
- PK: `(city, season, month, metric, live_data_version)` — `ens_bias_repo.py:61`
- The fit loop in `fit_full_transport_error_models.py:433-436` iterates `(city, metric, season)` only.
- `load_bucket_residuals` (ens_bias_repo.py:248-363) takes `lead_max=48.0` as a fixed ceiling filter (`e.lead_hours <= ?` at line 281-282) — it **pools all snapshots with lead_hours ≤ 48h into one residual list**. There is no lead-stratified sub-bucket.

**Implication:** A 6h-lead forecast (day 0, issued same morning) and a 48h-lead forecast (day 2, issued two days prior) receive **the same bias_c correction**. The pooling filter (`lead_hours <= 48`) is applied at the `_coverage_months_set` helper too (line 270: `AND e.lead_hours <= 48` hardcoded).

**Is it empirically defensible?** Partly. The `lead_max=48` cutoff deliberately excludes day 3–7 (where ensemble skill degrades fastest and bias patterns differ most). Within the 0–48h window the bias is primarily a systematic model bias in the ensemble mean, and cycle-aware snapshot selection (0Z for HIGH, 12Z for LOW) already removes the dominant source of lead-correlated artifact. However, bias at lead=6h (day-0 near-imminent) vs lead=42h (day-2 early issuance) can still differ by 0.5–1°C due to observation assimilation recency. No stratification within 0–48h means this sub-lead variation is averaged away.

---

## Q2. Does platt_models_v2 calibrate per lead?

**Answer: PARTIALLY — lead_days is a Platt INPUT FEATURE (param_B coefficient), not a bucket dimension.**

- `platt_models_v2` schema: columns `temperature_metric, cluster, season, data_version, cycle, source_id, horizon_profile, error_model_family`. **No `lead_days` column.** One row per (metric, cluster, season, data_version, cycle, source_id, horizon_profile) bucket.
- `platt.py:1-5` explicitly documents: "Lead_days is NOT a bucket dimension — it's a Platt input. This triples positive samples per bucket (45→135) vs the 72-bucket approach."
- Model: `P_cal = sigmoid(A × logit(P_raw) + B × lead_days + C)` — `platt.py:99`
- `param_B` captures the **linear lead-days effect** across the pooled bucket. At predict time, `predict_for_bin(p_raw, lead_days)` passes the actual lead of the forecast being priced — `platt.py:218-244`.
- `bucket_key` (`store.py:612-617`) encodes `(metric, cluster, season, data_version, cycle, source_id, horizon_profile)` — lead is NOT part of key.

**Assessment:** The Platt design is architecturally sound for lead — it treats lead as a continuous covariate rather than creating sparse sub-buckets. With `B × lead_days` in the logistic link, it adjusts p_cal upward/downward continuously as lead grows, capturing the monotone skill decay. This is preferable to 7 separate buckets of ~45 samples each. The limitation is it cannot capture *non-linear* lead effects (e.g., a sharp reliability break at day 5), but that is a model expressiveness question, not a pooling defect.

---

## Q3. Lead range in calibration_pairs_v2 (requested SQL)

```sql
SELECT lead_days, COUNT(*) FROM calibration_pairs_v2
WHERE error_model_family='full_transport_v1' AND temperature_metric='high'
GROUP BY lead_days ORDER BY lead_days;
```

**Abbreviated results (by rounded integer day, ≥0.5d):**

| lead (days) | rows     | distinct cities |
|-------------|----------|-----------------|
| 1           | 2,148,040 | 51 |
| 2           | 2,149,794 | 51 |
| 3           | 2,142,086 | 51 |
| 4           | 2,128,726 | 51 |
| 5           | 2,122,154 | 51 |
| 6           | 2,106,584 | 51 |
| 7           | 2,074,292 | 51 |

Plus `lead_days=0.000` (2,102,304 rows) and many fractional values (102 rows each) for days 0–3 — these appear to be non-integer ECMWF opendata leads.

**Key finding:** The full lead range 0–7 days is present and well-populated across 51 cities. The bias fit consumes **only lead_hours ≤ 48** (days 0–2), pooled. Days 3–7 pairs exist in the table and are used by Platt (which reads all rows) but not by the bias fit.

---

## Q4. Is there data_version / product mixing within a calibration bucket?

**Answer: YES — tigge + opendata BOTH appear in the same (city, season, metric) bucket in calibration_pairs_v2.**

SQL evidence:
```
Buckets with >1 data_version: Amsterdam/MAM/high → [tigge_mx2t6_local_calendar_day_max_v1, ecmwf_opendata_mx2t3_local_calendar_day_max_v1]
(same for Ankara, Atlanta, Austin, Beijing … all cities in MAM)
```

**However:** This mixing is BY DESIGN and handled correctly at the fit layer:
- `fit_full_transport_error_models.py:449-461` calls `load_bucket_residuals` **separately** for `_prior_dv` (tigge) and `_live_dv` (opendata), then combines them via the hierarchical Bayesian prior (transport-term) — `ens_error_model.py:fit_city_predictive_error`.
- The `data_version` column in `calibration_pairs_v2` distinguishes which source each row came from. The **pairs table is not pooled across products at query time** — the fit queries each product independently.
- The `error_model_key` written to `model_bias_ens_v2` is `city|metric|season|full_transport_v1|{live_dv}` — it is keyed to the LIVE data_version (opendata), not a mixed key.

**Risk:** The `calibration_pairs_v2` table itself holds rows from both products coexisting within a (city, season) scope. A naive query that ignores `data_version` would pool tigge and opendata into one Platt fit. Checking `audit_refit_proper_scores.py:101-118`: `_load_rows` does NOT filter by `data_version` — it reads all rows for a given `family+metric`. The Platt fit in this script therefore **does pool tigge and opendata pairs** within one (cluster, season) bucket. This is intentional (as documented in platt.py line 4), but means the Platt model's `param_B` reflects an average over both product families' lead-skill curves. If tigge and opendata have different lead-reliability slopes, `param_B` blends them.

---

## Summary Table

| Layer | Bucket key | Lead dimension | Product mixing |
|-------|-----------|---------------|----------------|
| `model_bias_ens_v2` | (city, season, month, metric, live_dv) | **None** — pooled ≤48h | Separate fit per dv |
| `platt_models_v2` | (metric, cluster, season, dv, cycle, source_id, horizon) | **param_B covariate** (continuous) | Both products feed one bucket |
| `calibration_pairs_v2` | row-level: lead_days stored per row | Full 0–7d stored | Both dvs in same table |

---

## Verdict

**The system is NOT lead-blind at the Platt layer** — `param_B × lead_days` adjusts calibrated probabilities continuously with horizon. This is the correct design choice given sample sizes (~45 outcomes per cluster/season/cycle before lead stratification).

**The system IS lead-blind at the bias layer** — `bias_c` is a single number applied regardless of whether a forecast is issued 6h or 48h before the target. For the narrow 0–48h window this is a reasonable approximation (cycle selection already removes the dominant artifact), but sub-lead variation within that window (0.5–1°C plausible) is not modeled.

**Product mixing in Platt** is intentional but implies `param_B` blends tigge and opendata lead-skill curves. If the two products diverge in lead-reliability slope (likely given different resolutions), this introduces systematic Platt error for opendata forecasts at the extremes of the lead range.

**No mixing occurs at the bias fit level** — each (city, metric, season, live_dv) row is fit from a single product's residuals. Correct.

---

**Key file:line citations:**
- `src/calibration/ens_bias_repo.py:61` — model_bias_ens_v2 PK (no lead)
- `src/calibration/ens_bias_repo.py:254,281-282` — `load_bucket_residuals` lead_max=48, pools all ≤48h
- `src/calibration/ens_bias_repo.py:270` — coverage probe also hardcodes `lead_hours <= 48`
- `scripts/fit_full_transport_error_models.py:433-436` — outer loop: (city, metric, season), no lead iteration
- `src/calibration/platt.py:1-5,99,218` — lead_days is param_B covariate, not bucket key
- `src/calibration/store.py:612-617` — Platt model_key: no lead component
- `scripts/audit_refit_proper_scores.py:101-118` — Platt fit pools both products (no dv filter)
