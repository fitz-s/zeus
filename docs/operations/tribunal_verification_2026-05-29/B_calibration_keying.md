# Tribunal Verification — B: Calibration Keying
**Date:** 2026-05-29
**Authority basis:** Live code read on stat-whole-refactor worktree (HEAD).
  Sources: `src/calibration/ens_bias_repo.py`, `src/calibration/ens_error_model.py`,
  `src/calibration/platt.py`, `src/calibration/ens_bias_model.py`
**Method:** Read-only. Claims verified against source; prior audit doc treated as a claim.
**Verifier:** executor agent (sonnet-4-6)

---

## Claim 1 — model_bias_ens PK is (city, season, month, metric, live_data_version); NO lead/horizon/cycle/product columns

**CONFIRMED.**

`ens_bias_repo.py:38-62` contains the verbatim DDL:

```sql
CREATE TABLE IF NOT EXISTS model_bias_ens(
    city TEXT NOT NULL,
    season TEXT NOT NULL,
    month INTEGER NOT NULL DEFAULT 0,
    metric TEXT NOT NULL,
    live_source_id TEXT,
    live_data_version TEXT NOT NULL,
    ...
    PRIMARY KEY (city, season, month, metric, live_data_version)
)
```

No `lead_days`, `lead_bucket`, `horizon_profile`, `cycle`, or `product` columns exist
in either the base DDL or the `_CANONICAL_EXTENSION_COLUMNS` list (`ens_bias_repo.py:70-96`).
The extension columns add `error_model_family`, `gate_set_hash`, `coverage_months`, and
scale fields — none are lead-dimension columns.

The bias repo was renamed from `model_bias_ens_v2` to `model_bias_ens`; variable
`MODEL_BIAS_ENS_V2_SCHEMA` at line 37 is a now-stale name for the DDL, confirming
the prior `_v2` suffix was the superseded name.

---

## Claim 2 — load_bucket_residuals pools residuals with lead_hours <= 48, one mean bias regardless of serving lead

**CONFIRMED.**

`ens_bias_repo.py:254`: `lead_max: float = 48.0` (default parameter).
`ens_bias_repo.py:281-282`:
```python
where = ["e.city = ?", "e.dataset_id = ?", "e.temperature_metric = ?", "e.lead_hours <= ?"]
params: list[object] = [city, data_version, metric, lead_max]
```

All snapshots with `lead_hours <= 48` are pooled into a single residual list. No
sub-bucket by lead hour or day. The resulting single `posterior_bias.bias` is written
as one row in `model_bias_ens` (no lead-stratified rows). A 6h-lead forecast and a
48h-lead forecast receive identical `bias_c` at inference.

The helper `_forecast_means` (called for paired-delta computation) uses the same filter
at `ens_bias_repo.py:643`: `"e.lead_hours <= ?"`.

---

## Claim 3 — Platt: P_cal = sigmoid(A*logit(P_raw) + B*lead_days + C). Lead is INPUT FEATURE, not bucket dim.

**CONFIRMED.**

`platt.py` module docstring (lines 1-5): *"Lead_days is NOT a bucket dimension — it's
a Platt input."*

`ExtendedPlattCalibrator.predict` at `platt.py` (predict method):
```python
z = self.A * logit + self.B * lead_days + self.C
p_cal = 1.0 / (1.0 + np.exp(-z))
```

`_build_features` constructs `[logit(P_raw), lead_days]` — exactly the two-column
feature matrix.

No `lead_days` column in `platt_models` bucket key. At serve time, the actual
`lead_days` value of the forecast being priced is passed to `predict_for_bin(p_raw,
lead_days, ...)`, so `B * lead_days` adjusts the calibrated probability continuously
per forecast without any bucket split.

---

## Claim 4 — Bias applied pre-MC in temperature domain (F' = F - b) BEFORE p_raw. Platt's lead covariate cannot undo a wrong pre-MC shift.

**CONFIRMED.**

`ens_error_model.py:211-237` (`p_raw_vector_with_error_model`):

```python
eff_bias_native = error_model.effective_bias_c * scale        # line 231
resid_sd_native = error_model.total_residual_sd_c * scale     # line 232
corrected = np.asarray(member_extrema, dtype=float) - eff_bias_native  # line 233
return p_raw_vector_from_maxes(
    corrected, city, settlement_semantics, bins,
    n_mc=n_mc, rng=rng, extra_member_sigma=resid_sd_native,    # line 234-236
)
```

The shift `corrected = raw - eff_bias_native` happens in member-extrema space before
`p_raw_vector_from_maxes` runs the Monte Carlo binning. `p_raw` is the *output* of that
MC step. Platt receives the post-MC `p_raw` and adds `B*lead_days` in logit space.

A wrong pre-MC shift (e.g., bias_c sign error, stale bucket match, wrong season) moves
the entire member distribution before binning; Platt's `B*lead_days` operates on a
different quantity (`p_raw` probability) and cannot undo or diagnose a temperature-domain
shift. The bias and Platt corrections are NOT symmetric inverses.

Also confirmed: `ens_bias_model.py`'s `apply_bias_to_extrema` (the pure-math equivalent)
docstring states: *"Correction MUST happen here, before binning + MC + rounding, because
those steps are non-linear."*

---

## Claim 5 — NO scale/sigma correction candidate. Only mean bias.

**WRONG (partially stale claim).**

`ens_error_model.py` introduces a full **location + scale + gate** error model — this is
NOT a mean-only system:

- `PredictiveErrorModel.residual_sd_c` (line 138): forecast/station residual scale, degC.
- `PredictiveErrorModel.total_residual_sd_c` (line 143): `sqrt(residual_sd^2 + heterogeneity_var)`.
- Applied as `extra_member_sigma=resid_sd_native` in `p_raw_vector_from_maxes` (line 236).

`fit_predictive_error_bucket` (line 240-270): *"Fit location (via #334 fit_bucket) AND
scale (residual SD) for one bucket."* The scale uses live OOS spread when n_live >=
DEFAULT_MIN_LIVE_N (20), else falls back to TIGGE spread, floored at
`DEFAULT_RESIDUAL_FLOOR_C=0.5°C`.

However, the scale IS stored in `model_bias_ens` in the canonical extension columns
(`residual_sd_c`, `total_residual_sd_c` at `ens_bias_repo.py:78,82`) — so scale IS
modeled. The Tribunal claim that "there is NO scale correction candidate" is incorrect
for the full-transport path. The legacy `ens_bias_model.py` pure-math interface (mean bias
only via `fit_bucket` / `posterior_bias`) has no scale, but the production error-model
layer (`ens_error_model.py`) adds residual_sd on top.

**Nuance:** the scale is a fixed additive sigma to the MC draw — it does not adjust the
bin probability after MC. It is NOT a dispersion correction in probability space. A wrong
pre-MC spread correction still cannot be reversed by Platt.

---

## Omissions the audit/report missed

### O1. dataset_id / data_version split rename is LIVE and INCONSISTENT within load_bucket_residuals

`ens_bias_repo.py:281`: the SQL filter on ensemble_snapshots uses `e.dataset_id` (the
column name in the DB table). The Python function parameter is named `data_version` (line
252), and the `model_bias_ens` table stores `live_data_version`. There are 702 `data_version`
refs vs 46 `dataset_id` refs in `src/`. This is a live two-name reality: DB column =
`dataset_id`, Python layer = `data_version`. Any caller that passes a `dataset_id` string
under the name `data_version` will work correctly, but the mismatch is a silent grep trap.
If the DB column were ever renamed to `data_version`, `e.dataset_id` at line 281 (and line
643) would silently return zero rows.

### O2. season_months filter is applied in Python AFTER the SQL query, not in SQL — missing index use

`load_bucket_residuals` fetches ALL rows for `(city, data_version, metric, lead_hours <= 48)`,
then filters by season month in a Python loop (`ens_bias_repo.py:332-334`):
```python
if season_months is not None and int(str(td)[5:7]) not in season_months:
    continue
```
This means the DB always scans the full city/metric/lead window regardless of requested
season. For TIGGE datasets with multi-year history (~1000 rows per city), the penalty is
small. For future high-volume products, the missing `WHERE MONTH(target_date) IN (...)`
predicate is a latent performance issue. More importantly, the Python-side filter does NOT
appear in `_coverage_months_set` (the preflight that returns months present in training
data) — that function also fetches all rows and counts `target_date[:7]` in Python. No SQL
index on `MONTH(target_date)` can help here.

### O3. coverage_months field does NOT protect against season-label / SH flip mismatch at READ time

`model_bias_ens.coverage_months` (canonical extension column, `ens_bias_repo.py:95`)
stores the months actually covered in the fit window as a CSV string (e.g. `"3,4,5"`).
The B1/sd3 fix (`_GATE_SET_VERSION = "ftgate-2026-05-28-sd3"`) updated the gate hash to
pin hemisphere-aware season labelling. However, the READ path (`read_bias_model`) filters
by `(city, season, metric, live_data_version, ...)` — the `season` field is the
SH-flipped label. If `coverage_months` is `"3,4,5"` but the serving query asks for
`season='MAM'` on a SH city (where MAM maps to autumn, months 3-5 in calendar but
autumn in SH), the `gate_set_hash` rejects pre-sd3 rows correctly — but rows written
post-sd3 with SH-flipped `season` still carry `coverage_months="3,4,5"` (calendar
months). A reader or diagnostic tool that checks `coverage_months` against a target month
of `3` will see `3 in [3,4,5]` and conclude "covered" — even though calendar March is
autumn in SH, and the row's own `season` label says "SON" (SH autumn). The
`coverage_months` field is calendar-month-indexed but `season` is hemisphere-aware;
this is an inherent semantic ambiguity that could mislead diagnostic tooling.

---

## Summary table

| Claim | Verdict | Key evidence |
|-------|---------|-------------|
| 1. model_bias_ens PK has no lead/cycle/product cols | CONFIRMED | `ens_bias_repo.py:38-62` DDL |
| 2. load_bucket_residuals pools lead_hours <= 48, one bias | CONFIRMED | `ens_bias_repo.py:254,281-282` |
| 3. Platt: B*lead_days covariate, not bucket dim | CONFIRMED | `platt.py` docstring + predict() |
| 4. Bias subtracted pre-MC in temperature domain | CONFIRMED | `ens_error_model.py:231-236` |
| 5. NO scale/sigma correction candidate | WRONG | `PredictiveErrorModel.residual_sd_c` + `total_residual_sd_c` in `ens_error_model.py:132-143`; scale IS modeled in the full-transport path |
| O1. dataset_id / data_version live naming split | MISSED | `ens_bias_repo.py:281` uses `e.dataset_id`; Python param is `data_version` |
| O2. season_months filter is Python-side, not SQL | MISSED | `ens_bias_repo.py:332-334`; no DB index benefit |
| O3. coverage_months is calendar-indexed, season is SH-flipped | MISSED | semantic ambiguity for SH cities post-B1 |
