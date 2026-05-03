# ZEUS DDD REALITY-FAILURE TRIBUNAL REPORT

## Evidence access note

I could not inspect the attached ZIP package `task_2026-05-03_ddd_implementation_plan.zip` because it was not available through the mounted files/search surface in this session. I therefore reconstructed the DDD plan and Phase 1 results from your supplied summary, then audited that reconstruction against the public `fitz-s/zeus` repository files I could inspect.

Claims that require direct inspection of `PLAN.md`, `phase1/*.py`, `phase1_results/*.md`, or `phase1_results/*.json` are marked `REVIEW_REQUIRED`. That does **not** soften the verdict. It means the current Phase 1 package cannot be accepted as verified evidence in this session.

Repo context materially relevant to this audit:

Zeus is a Polymarket weather-market system whose pipeline includes settlement-source semantics, ensemble generation, Platt calibration, model-market fusion, FDR filtering, Kelly sizing, CLOB execution, monitoring, settlement, and learning. The repo explicitly treats high and low markets as separate semantic families that do **not** share physical quantity, observation field, Day-0 causality, calibration parameters, or replay identity. ([GitHub][1])

The city config warns that station identity must match the actual settlement station, not downtown coordinates, and that Polymarket can change settlement station/source/unit; it also states that the most recent active market description is the source of truth for settlement source routing. ([GitHub][2])

The WU hourly client uses sub-hourly WU/METAR observations, aggregates them into hourly extrema, assigns `target_date` by local date using `ZoneInfo`, carries DST fields, and **skips** hours with no observation rather than emitting NaN-filled missing rows. ([GitHub][3])

The repo schema separates settlement truth, calibration pairs, forecast snapshots, observation instants, market events, and data-coverage facts; `data_coverage` is the intended surface for expected-vs-written/MISSING/LEGITIMATE_GAP/FAILED accounting, while observation tables contain rows that exist. ([GitHub][4])

---

## 0. Executive adversarial verdict

### 0.1 Verdict

The current DDD Phase 1 conclusions should **not** be trusted for live sizing, entry, exit, promotion evidence, or live reports.

The DDD concept is plausible as a **shadow diagnostic and readiness signal**, but the Phase 1 conclusions as summarized are not proven. They are vulnerable to calculation-object mismatch, source/station drift, HIGH/LOW conflation, local-date/UTC errors, missing-row invisibility, operator-overwrite overfitting, sparse-bin calibration, and live-trading translation failure.

**Strongest component:** the idea of a **Day-0 source/coverage hard gate**. Not the specific `0.35` threshold, but the concept that current-day observation integrity can be a hard eligibility gate.

**Weakest component:** the **discount curve**, especially the accepted 2–9% sizing discount and 9% cap. The evidence is explicitly sparse, noisy, and dominated by zero-shortfall days. A small discount can create false comfort while failing to block the trades most exposed to oracle/source failure.

**Biggest calculation-risk branch:** coverage may be computed over **observed rows** rather than expected local settlement slots. Because WU missing hours are skipped rather than emitted, `COUNT(DISTINCT utc_timestamp)` can measure “rows present in the database” rather than “required observation slots available.” That threatens every floor, σ, shortfall, and curve conclusion.

**Biggest live-trading-risk branch:** DDD may be implemented as a small **Kelly-size discount** even when the correct action is **city/metric/source readiness block**. A 2–9% discount cannot protect against settlement-source mismatch, current-day critical-hour outage, HIGH/LOW identity error, or station migration.

**Immediate recommendation:** `SHADOW_ONLY`. No DDD value should affect live sizing, entry, exit, or promotion reporting until Phase 1 is rerun with corrected local-time, source, station, metric, expected-slot, leakage, and executable-EV tests.

### 0.2 Direct answers to the 25 hard questions

|  # | Question                                                         | Adversarial answer                                                                                                                                                            |
| -: | ---------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  1 | Is DDD measuring the right object?                               | **Not proven.** It appears to measure historical observation-row density, not necessarily settlement-relevant extreme-hour truth availability. `REQUIRES_REDEFINITION`.       |
|  2 | Are Phase 1 scripts computing intended quantities?               | **REVIEW_REQUIRED.** Scripts were unavailable. Based on the summary, hidden branches are severe enough that outputs cannot be accepted.                                       |
|  3 | Are hard floors statistically proven or operator-imposed?        | Partly statistical, partly operator-imposed. Denver/Paris and Lagos overrides break falsifiability.                                                                           |
|  4 | Is Denver/Paris `0.85` evidence-based or asymmetric-loss policy? | As summarized, **policy**, not calibration. `ACCEPT_AS_HEURISTIC_ONLY`, not proven.                                                                                           |
|  5 | Is Lagos `0.45` reality-preserving or boiled-frog dangerous?     | Both possible; adversarially it is **boiled-frog dangerous** until segmented by station/source/regime.                                                                        |
|  6 | Does §2.2 `k` failure mean small-sample risk is fake?            | No. It means the chosen Brier/ECE tests did not support that multiplier. Small-sample risk remains.                                                                           |
|  7 | Is `k=0` live-safe?                                              | Not by itself. It is safer than an unjustified multiplier only if replaced by maturity gates/blacklists.                                                                      |
|  8 | Does σ=90 protect against outages?                               | Not proven. It may over-smooth regime change and normalize outage-prone cities.                                                                                               |
|  9 | Does `0.35` absolute kill catch right failures?                  | It may catch gross catastrophes. It misses critical-window and station-mismatch failures.                                                                                     |
| 10 | Does it miss 3-hour outages in stable cities?                    | Yes, especially if daily coverage remains high while the missing hours are near the max/min.                                                                                  |
| 11 | Is discount curve supported enough for live?                     | No. `REJECT_FOR_LIVE`.                                                                                                                                                        |
| 12 | Are non-zero shortfall bins too sparse?                          | Yes, per the summary.                                                                                                                                                         |
| 13 | Are Brier/log-loss/ECE right metrics?                            | Useful diagnostics, wrong as sole live-risk metrics.                                                                                                                          |
| 14 | Are calibration metrics enough for trading-risk decisions?       | No. They do not measure executable EV, slippage, fill risk, or asymmetric missed-extreme loss.                                                                                |
| 15 | Are HIGH results being generalized to LOW?                       | Possibly. Any such generalization is rejected. Repo architecture explicitly separates HIGH and LOW.                                                                           |
| 16 | Are local dates and UTC timestamps handled correctly?            | The repo has fields to do it correctly; Phase 1 is `REVIEW_REQUIRED`.                                                                                                         |
| 17 | Are DST and cross-midnight windows handled?                      | The ingestion layer carries DST fields; Phase 1 must prove it used them.                                                                                                      |
| 18 | Does station migration break baseline?                           | Yes. Paris-source caution in repo context proves this is live-real, not theoretical.                                                                                          |
| 19 | Are no-WU cities safely excluded?                                | Only if exclusion creates an explicit readiness block. Null can otherwise mean silently unprotected.                                                                          |
| 20 | Does DDD double-count or undercount Platt effects?               | Unknown; likely both depending on city/metric. Needs conditional calibration-vs-DDD attribution.                                                                              |
| 21 | Hidden branch most likely making current conclusion wrong?       | Expected-slot denominator / missing-row invisibility combined with local-date/UTC grouping.                                                                                   |
| 22 | Hidden branch most likely causing live money loss?               | Small sizing discount applied when source/station/current-day critical-window failure should block.                                                                           |
| 23 | What must rerun before Phase 2?                                  | Full Phase 1 with corrected local calendar, expected slots, HIGH/LOW split, source/station segmentation, robust σ, bootstrap CIs, and executable replay.                      |
| 24 | What should be blocked from live even if implemented?            | Curve discount, hard floors, σ-window, and `k=0` policy as live sizing inputs until rerun.                                                                                    |
| 25 | Safest revised plan?                                             | Shadow-only DDD telemetry plus hard readiness gates for source/station/current-day integrity; no live sizing effect until forward shadow replay proves executable EV benefit. |

---

## 1. Faithful reconstruction of current DDD plan

No critique in this section.

### 1.1 Intended DDD object

From the supplied summary, DDD appears to mean **Oracle/Data Density Discount**: a per-city/per-settlement-source risk modifier that discounts model/trading confidence when historical or current observation coverage is thin relative to a city-specific minimum acceptable floor.

The intended real-world object is:

> “How reliable is the weather observation surface for the city/date/metric settlement question, especially around the hours that determine the final high or low?”

### 1.2 Apparent formula

The exact formula in `PLAN.md` is `REVIEW_REQUIRED`, but the supplied summary implies a structure like:

```text
coverage_metric(city, date, metric)
    = daily_cov or directional_cov over expected observation slots

floor(city)
    = HARD_FLOOR_FOR_SETTLEMENT[city]

shortfall(city, date, metric)
    = max(0, floor(city) - coverage_metric(city, date, metric))

small_sample_adjustment(N)
    = 1 + k / sqrt(N)

sigma_component
    = rolling σ of coverage over sigma_window

adjusted_shortfall
    = function(shortfall, sigma_component, small_sample_adjustment)

DDD_discount
    = discount_curve(adjusted_shortfall)

size_after_DDD
    = size_before_DDD * (1 - DDD_discount)
```

`REVIEW_REQUIRED`: whether σ is used as a band, z-score, smoothing term, or diagnostic-only statistic cannot be verified without `PLAN.md` and scripts.

### 1.3 Intended inputs

| Input                           | Intended meaning                                                                              |
| ------------------------------- | --------------------------------------------------------------------------------------------- |
| `city`                          | Settlement city identity.                                                                     |
| `temperature_metric`            | `high` or `low`, not interchangeable.                                                         |
| `target_date`                   | Local settlement date.                                                                        |
| `source`                        | Settlement-relevant weather source, usually WU ICAO for WU cities.                            |
| `utc_timestamp`                 | Observation instant or UTC hour bucket.                                                       |
| `target_date` / local timestamp | City-local calendar date used for settlement.                                                 |
| `daily_cov`                     | Fraction of expected daily observation slots available.                                       |
| `directional_cov`               | Fraction of expected directional peak-window slots available.                                 |
| `N`                             | Sample size for city/source/metric/window calibration.                                        |
| `sigma_window`                  | Rolling window used to estimate coverage variability.                                         |
| `shortfall`                     | Coverage gap below floor or expected reliability threshold.                                   |
| calibration outcomes            | Settlement labels / bin outcomes used to relate DDD to forecast error or calibration metrics. |

### 1.4 HARD_FLOOR logic

From the summary:

| City/class         |      Current floor conclusion |
| ------------------ | ----------------------------: |
| Jakarta            |                        `0.35` |
| Lagos              |                        `0.45` |
| Lucknow            |                        `0.50` |
| Shenzhen           |                        `0.55` |
| Most stable cities |                        `0.85` |
| Hong Kong          | `null`, no WU primary surface |
| Istanbul           | `null`, no WU primary surface |
| Moscow             | `null`, no WU primary surface |
| Tel Aviv           | `null`, no WU primary surface |

Operator rulings:

| Ruling                      | Current stated reason                  |
| --------------------------- | -------------------------------------- |
| Denver/Paris must be `0.85` | asymmetric-loss principle              |
| Lagos must stay `0.45`      | preserve high-σ infrastructure reality |

### 1.5 §2.2 small-sample multiplier `k`

Current experiment:

```text
small_sample_multiplier = 1 + k / sqrt(N)
```

Current result:

| Metric/test          | Result                  |
| -------------------- | ----------------------- |
| Brier, all rows      | weak positive signal    |
| Brier, winning rows  | contradicted hypothesis |
| ECE                  | borderline weak support |
| train/test stability | failed                  |
| conclusion           | FAIL                    |
| recommendation       | `k = 0` in v1           |

### 1.6 §2.3 `sigma_window`

Current result:

| Item                            | Conclusion                                        |
| ------------------------------- | ------------------------------------------------- |
| original white-noise hypothesis | failed                                            |
| coverage drops                  | cluster in time                                   |
| recommendation                  | `sigma_window = 90 days`                          |
| Lagos/Shenzhen                  | high σ, σ-band can absorb anomalies               |
| catastrophic catch              | rely on §7 absolute hard kill at coverage `<0.35` |

### 1.7 §2.4 discount curve

Current accepted curve:

|      Shortfall | DDD discount |
| -------------: | -----------: |
|            `0` |         `0%` |
|    `(0, 0.10)` |       `0–2%` |
| `[0.10, 0.25)` |       `2–5%` |
| `[0.25, 0.40)` |       `5–8%` |
|       `>=0.40` |     `9% cap` |

Current result:

| Finding         | Status                       |
| --------------- | ---------------------------- |
| most shortfalls | `0`                          |
| non-zero bins   | very small `N`               |
| progression     | noisy                        |
| conclusion      | DIRECTIONAL PASS             |
| admission       | cannot be crisply calibrated |

### 1.8 Missing or unexecuted pieces

From the user summary:

| Section                             | Status               |
| ----------------------------------- | -------------------- |
| §2.5 `small_sample_floor` threshold | missing / unexecuted |
| §2.6 `peak_window` radius           | missing / unexecuted |

### 1.9 Intended Phase 2 implementation

Inferred intended Phase 2:

| Component                         | Intended use                                                         |
| --------------------------------- | -------------------------------------------------------------------- |
| `HARD_FLOOR_FOR_SETTLEMENT[city]` | per-city baseline coverage floor                                     |
| `k=0`                             | no small-sample multiplier in v1                                     |
| `sigma_window=90`                 | rolling σ estimation / banding                                       |
| accepted curve                    | map shortfall to discount                                            |
| Day-0 circuit breaker             | absolute block/kill if current coverage `<0.35`                      |
| rollout                           | likely shadow → report → sizing, but exact plan is `REVIEW_REQUIRED` |

---

## 2. Reality-object mapping table

| Plan variable         | Script/database field                                                                                           | Intended object                                                      | Actual measured object                                                                    | Mismatch risk                                                                                                | Affected conclusion        |
| --------------------- | --------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ | -------------------------- |
| `daily_cov`           | likely `COUNT(DISTINCT utc_timestamp) / 24`; repo fields `observation_instants_v2.utc_timestamp`, `target_date` | Fraction of required daily settlement observations available         | Fraction of rows that exist in DB for a date/source, unless expected slots are enumerated | `CALCULATION_RISK`: missing WU hours may be skipped, so missingness is invisible without expected-slot table | floors, σ, curve           |
| `directional_cov`     | likely local-hour subset around `historical_peak_hour`                                                          | Availability of observations during high/low-relevant extreme window | Availability in an assumed window, possibly HIGH-only and season-insensitive              | `TIME_SEMANTICS_RISK`: high and low windows differ; peak varies by city/season                               | floors, curve, peak_window |
| `shortfall`           | JSON/derived `floor - cov`                                                                                      | Operational data-quality deficit                                     | Difference between floor and a proxy coverage ratio                                       | `CALCULATION_RISK`: if coverage proxy wrong, shortfall is numerology                                         | curve, σ, hard kill        |
| `sigma_train`         | rolling/std of coverage                                                                                         | Stability/noise of observation coverage                              | Standard deviation of observed coverage proxy                                             | `OVERFIT_RISK`: high σ can normalize bad infrastructure                                                      | σ_window, Lagos/Shenzhen   |
| `sigma_window`        | `90 days`                                                                                                       | Timescale over which coverage-process volatility should be estimated | Retrospective window selected after ACF/outage observation                                | `OVERFIT_RISK`: selected post hoc; may over-smooth regime breaks                                             | σ                          |
| `N`                   | calibration rows, dates, or decision groups                                                                     | Independent evidence count                                           | Could be rows/bins, not independent market families/days                                  | `CALCULATION_RISK`: mutually exclusive bins inflate N                                                        | `k`, ECE, curve            |
| `Brier`               | `mean((p-outcome)^2)`                                                                                           | Calibration error relevant to DDD                                    | Probability score over rows/bins                                                          | `LIVE_MONEY_RISK`: not sizing/P&L/tail loss                                                                  | `k`, curve                 |
| `winning Brier`       | Brier restricted to winning rows                                                                                | Calibration on realized winner                                       | Post-outcome conditional subset                                                           | `LEAKAGE_RISK`: selected after winner known; not a live decision set                                         | `k`                        |
| `ECE`                 | binned calibration error                                                                                        | Calibration reliability                                              | Bin-sensitive, underpowered small-N diagnostic                                            | `CALCULATION_RISK`: unstable bins; repeated bins correlated                                                  | `k`, curve                 |
| `error_mean`          | likely mean absolute/model error by shortfall bin                                                               | Mean forecast/settlement error                                       | Average error, not tail loss                                                              | `LIVE_MONEY_RISK`: tail dominates trading                                                                    | curve                      |
| `outage day`          | manually labeled catastrophic dates or coverage below threshold                                                 | True catastrophic source unreliability                               | Label defined after known outages                                                         | `OVERFIT_RISK`: circular detection                                                                           | hard kill, floors          |
| `normal day`          | days not labeled outage                                                                                         | Safe operational days                                                | Could include hidden critical-hour missingness                                            | `CALCULATION_RISK`: normal label may be false                                                                | false-positive rates       |
| `hard_floor`          | `p2_1_FINAL_per_city_floors.json`                                                                               | City-specific minimum acceptable coverage                            | Mix of statistical recommendation and operator policy                                     | `OVERFIT_RISK`: overwritten results break falsifiability                                                     | §2.1                       |
| `absolute 0.35 floor` | §7 circuit breaker                                                                                              | Catastrophic current-day coverage kill                               | Daily/global coverage threshold                                                           | `LIVE_MONEY_RISK`: misses 3-hour critical outage                                                             | §2.3, live gating          |
| `target_date`         | observation/settlement `target_date`                                                                            | City-local settlement date                                           | May be local date if ingestion correct; script use unknown                                | `TIME_SEMANTICS_RISK`: UTC grouping can relabel day                                                          | all                        |
| `utc_timestamp`       | observation instant/bucket                                                                                      | UTC observation bucket                                               | Existing observed bucket only                                                             | `CALCULATION_RISK`: absent buckets not represented                                                           | coverage                   |
| `forecast_basis_date` | forecast `issue_time`, `available_at`, `lead_days`                                                              | When model evidence was knowable                                     | May be full-sample or post hoc derived                                                    | `LEAKAGE_RISK`: future info can enter train/test                                                             | `k`, curve                 |
| `source`              | `source`, `settlement_source_type`, `station_id`                                                                | Actual settlement provider/station                                   | Could be WU/Ogimet/HKO/fallback/fossil lineage                                            | `CALCULATION_RISK`: source mix changes measured coverage                                                     | floors                     |
| `data_version`        | `data_version`, `zeus_meta.observation_data_version`                                                            | Training surface identity                                            | Could select current view or legacy rows inconsistently                                   | `LEAKAGE_RISK`: mixed surfaces                                                                               | all                        |
| `temperature_metric`  | `high`/`low`                                                                                                    | Physical market family                                               | Default HIGH or legacy HIGH-only rows can infect LOW                                      | `TIME_SEMANTICS_RISK`: HIGH/LOW conflation                                                                   | all                        |
| `station_id`          | ICAO/station code                                                                                               | Physical observation site                                            | May migrate over time, e.g., station contract changes                                     | `CALCULATION_RISK`: baseline shifts                                                                          | floors, σ                  |
| `authority`           | `VERIFIED`, `UNVERIFIED`, etc.                                                                                  | Whether row is trusted                                               | May be missing/default in legacy surfaces                                                 | `CALCULATION_RISK`: unverifiable rows included                                                               | all                        |
| `training_allowed`    | v2 calibration/observation fields                                                                               | Eligibility for model training                                       | Nullable on altered legacy DB rows; scripts may ignore                                    | `LEAKAGE_RISK`: fallback rows enter training                                                                 | k, curve                   |
| `decision_group_id`   | calibration pairs                                                                                               | Independent market family group                                      | Rows may be per bin, not group                                                            | `CALCULATION_RISK`: duplicated family bins inflate evidence                                                  | k, ECE                     |

The repo has the fields needed to do this correctly, but the Phase 1 scripts must prove they used the correct fields. The v2 schema includes `temperature_metric`, `observation_field`, `source`, `timezone_name`, `local_timestamp`, `utc_timestamp`, DST flags, `training_allowed`, `causality_status`, and source-role/provenance fields; existing altered DB rows can still have nullable identity fields, so script filters matter. 

---

## 3. Script-level forensic audit

Because `phase1/*.py` was unavailable, every script verdict below is conditional. I reconstruct likely script roles from the result files and conclusions you supplied.

### 3.1 §2.1 hard-floor script family

| Audit item                    | Finding                                                                                                           |
| ----------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| Inputs                        | Likely observation coverage by city/date/source, possibly joined to settlement/calibration outcomes.              |
| Filters                       | Claimed WU primary surface; HIGH windows likely used. Exact filters `REVIEW_REQUIRED`.                            |
| Grouping                      | Likely city × date, possibly city × date × source.                                                                |
| Denominator                   | `REVIEW_REQUIRED`; likely 24 daily hours or directional-window slot count.                                        |
| Timestamp handling            | `REVIEW_REQUIRED`; must prove city-local date, not UTC date.                                                      |
| City/metric/track assumptions | HIGH floor likely; LOW generalization not proven.                                                                 |
| Statistical metric            | floor recommendations, false positive/false negative detection, catastrophic-day detection.                       |
| Silent exclusions             | no-WU primary cities set null; missing DB rows may be invisible; fallback sources possibly excluded or mixed.     |
| Duplicates                    | duplicate timestamps per source/station can affect coverage unless explicitly deduped.                            |
| Outputs                       | `p2_1_FINAL_per_city_floors.json`; per-city floor recommendations.                                                |
| Hidden risks                  | observed-row denominator, HIGH-only floor, operator overrides, source/station migration, Lagos degraded baseline. |
| Verdict                       | `REVIEW_REQUIRED` + `POSSIBLE_TIME_MISMATCH` + `POSSIBLE_SAMPLE_BIAS` + `POSSIBLE_FIELD_MISMATCH`.                |

### 3.2 §2.2 `k` multiplier script family

| Audit item         | Finding                                                                                                                     |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| Inputs             | calibration pairs, DDD coverage/shortfall features, Platt/calibrated probabilities.                                         |
| Filters            | `REVIEW_REQUIRED`; likely all rows vs winning rows split.                                                                   |
| Grouping           | Must group by decision family; if per-bin rows independent, N inflated.                                                     |
| Denominator        | calibration row count or date count; independent sample count unknown.                                                      |
| Timestamp handling | train/test split by `target_date` claimed; must use availability time to avoid leakage.                                     |
| Metric             | Brier all rows, Brier winning rows, ECE, train/test stability.                                                              |
| Silent exclusions  | immature buckets, null floor cities, source-missing rows, no settlement rows.                                               |
| Duplicates         | market-family bins are mutually exclusive but may be treated as independent.                                                |
| Outputs            | §2.2 conclusion FAIL; recommendation `k=0`.                                                                                 |
| Hidden risks       | Brier dominated by losing/non-winning bins; winning-row selection post-outcome; ECE underpowered; Platt may be full-sample. |
| Verdict            | `COMPUTES_PROXY_ONLY` unless scripts prove grouped, causal, metric-specific replay. `POSSIBLE_LEAKAGE`.                     |

### 3.3 §2.3 `sigma_window` script family

| Audit item         | Finding                                                                                               |
| ------------------ | ----------------------------------------------------------------------------------------------------- |
| Inputs             | daily coverage time series by city/source/metric.                                                     |
| Filters            | likely WU primary, possibly HIGH.                                                                     |
| Grouping           | city × time window; station/source segmentation unknown.                                              |
| Denominator        | daily coverage denominator unknown.                                                                   |
| Timestamp handling | local-date handling `REVIEW_REQUIRED`.                                                                |
| Metric             | ACF / clustering of coverage drops; rolling σ comparison.                                             |
| Silent exclusions  | no-source days, null cities, station migrations, fallback rows.                                       |
| Outputs            | conclusion PARTIAL PASS; `sigma_window=90`.                                                           |
| Hidden risks       | ACF on coverage not outage-process persistence; high σ absorbs anomalies; 90d selected after looking. |
| Verdict            | `COMPUTES_PROXY_ONLY` + `POSSIBLE_SAMPLE_BIAS` + `OVERFIT_RISK`.                                      |

### 3.4 §2.4 discount-curve script family

| Audit item         | Finding                                                                     |
| ------------------ | --------------------------------------------------------------------------- |
| Inputs             | shortfall bins and forecast/settlement error or calibration metrics.        |
| Filters            | nonzero shortfall bins sparse; exact exclusions `REVIEW_REQUIRED`.          |
| Grouping           | shortfall bucket; city/source/metric weighting unknown.                     |
| Denominator        | rows per shortfall bin; independent N unknown.                              |
| Timestamp handling | inherits coverage timestamp risks.                                          |
| Metric             | likely mean error / Brier/log-loss progression.                             |
| Silent exclusions  | zero-shortfall dominance; rare high-shortfall days; null-source cities.     |
| Outputs            | operator curve accepted directionally.                                      |
| Hidden risks       | no crisp calibration, no tail-loss metric, no executable EV, cap arbitrary. |
| Verdict            | `COMPUTES_PROXY_ONLY`; `REJECT_FOR_LIVE`.                                   |

### 3.5 §2.5 small-sample-floor script

| Audit item | Finding                                                                      |
| ---------- | ---------------------------------------------------------------------------- |
| Status     | Missing/unexecuted.                                                          |
| Threat     | Without independent sample floor, `k=0` can remove the only small-N penalty. |
| Verdict    | `REQUIRES_RERUN`; cannot proceed to live.                                    |

### 3.6 §2.6 peak-window-radius script

| Audit item | Finding                                                                     |
| ---------- | --------------------------------------------------------------------------- |
| Status     | Missing/unexecuted.                                                         |
| Threat     | Directional coverage may be wrong by city, season, and HIGH/LOW track.      |
| Verdict    | `REQUIRES_REDEFINITION`; cannot generalize daily/HIGH coverage to live DDD. |

---

## 4. §2.1 hard floor adversarial audit

### 4.1 Statistical basis

The per-city floors are not proven floors. They are a blend of:

1. empirical coverage distribution;
2. subjective asymmetric-loss policy;
3. known outage detection;
4. city infrastructure priors;
5. manual operator overrides.

That makes them useful hypotheses but not validated statistical thresholds.

`CALCULATION_RISK`: if coverage was computed from observed rows only, the floor calibration is anchored to rows already present in the DB, not to the expected observation process.

`OVERFIT_RISK`: if catastrophic examples were known before threshold choice, “15/15 catastrophic detection” or similar claims are circular unless validated forward on unseen outages.

### 4.2 Operator overrides

Operator rulings are legitimate policy inputs only if they are labeled as policy. They are not statistical evidence.

| Override                     | Adversarial classification                                                                                    |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------- |
| Denver `0.85`                | asymmetric-loss policy masquerading as calibration unless rerun evidence supports it                          |
| Paris `0.85`                 | especially suspect because station-source drift exists in repo context                                        |
| Lagos `0.45`                 | infrastructure prior; may encode degraded state as normal                                                     |
| stable cities default `0.85` | heuristic cohort default unless each city has confidence intervals and false-positive/false-negative analysis |

### 4.3 Denver/Paris

Denver/Paris at `0.85` is not evidence-based as summarized. It is a policy decision: “missing data in stable cities is suspicious, so require high coverage.”

That may be operationally sane, but it must be represented as:

```text
floor_source = "operator_asymmetric_loss_policy"
not
floor_source = "statistically_calibrated"
```

Paris specifically needs source-contract segmentation. The repo’s current-source validity surface records a Paris caution/quarantine path where active Polymarket markets resolved via WU station `LFPB` while config still used `LFPG`; new Paris entries were to remain blocked until conversion evidence completed. A DDD floor over the wrong Paris station is worse than useless: it can create a high-confidence discount over the wrong data surface. 

### 4.4 Lagos

Lagos `0.45` can be read two ways:

| Interpretation        | Meaning                                                                                                             |
| --------------------- | ------------------------------------------------------------------------------------------------------------------- |
| reality-preserving    | Lagos infrastructure really has lower WU coverage; requiring `0.85` would permanently block a viable but noisy city |
| boiled-frog dangerous | long degradation becomes the baseline, so DDD stops flagging the very unreliability it exists to penalize           |

Adversarial ruling: **boiled-frog dangerous until proven otherwise**.

Required proof:

```text
segment Lagos by:
- station_id
- source
- source_role
- date regime / change point
- wet/dry season
- HIGH vs LOW
- outage clusters
- settlement mismatch days
```

If Lagos high σ lets anomalies fall inside the σ-band, the σ-band is normalizing failure.

### 4.5 Null cities: Hong Kong / Istanbul / Moscow / Tel Aviv

A `null` floor is safe only if it means:

```text
DDD_APPLICABILITY = false
LIVE_DDD_INPUT = unavailable
ENTRY_ELIGIBILITY = fail-closed or separately gated
```

It is unsafe if it means:

```text
DDD does not apply
therefore no discount
therefore city proceeds normally
```

Repo context matters: source routing is not WU-only. The tier resolver maps WU, Ogimet/NOAA-proxy, and HKO surfaces separately; it explicitly treats settlement source as observation source and rejects a generic grid fallback. 

So null-WU cities should not be “no data penalty.” They need their own source-class DDD or a source-readiness gate.

### 4.6 HIGH vs LOW

Hard floors derived from HIGH cannot be applied to LOW.

The repository’s own architecture says high and low markets do not share physical quantity, observation field, Day-0 causality, calibration parameters, or replay identity. ([GitHub][1])

LOW-specific risks:

| LOW risk                         | Why floor transfer fails                  |
| -------------------------------- | ----------------------------------------- |
| occurs overnight / early morning | relevant hours often cross local midnight |
| missing nighttime observations   | daily 24h coverage may look acceptable    |
| DST fall-back ambiguity          | duplicate local hour can matter           |
| frost/cooling regimes            | weather-process different from high       |
| provider reporting cadence       | nighttime station reports may differ      |

Verdict: HIGH-derived floors are `REJECT_FOR_LIVE` on LOW until rerun.

### 4.7 Local-time windows

A floor is not a physical reality threshold unless it is tied to the settlement date’s local clock. The WU client stores both local and UTC timestamps and assigns `target_date` by local date, but the DDD scripts must prove they used those fields correctly. ([GitHub][3])

### 4.8 False positive / false negative risk

| Error                   | Example                                                       | Live consequence                 |
| ----------------------- | ------------------------------------------------------------- | -------------------------------- |
| false positive halt     | stable city has harmless missing 03:00 row                    | missed good trade                |
| false negative pass     | stable city misses 14:00–16:00 heat spike window              | false high-confidence trade      |
| false negative pass     | station migrated but coverage remains high                    | wrong settlement surface         |
| false negative pass     | LOW overnight missing but HIGH floor passes                   | wrong LOW market                 |
| false positive discount | Lagos low baseline treated as normal but discount overapplied | suppresses viable noisy city     |
| false negative discount | high σ absorbs outage                                         | trades through real data failure |

### 4.9 Live-trading implication

Hard floors are not ready as live sizing inputs.

Safer posture:

```text
floor can be logged in shadow
floor can label report cohorts
floor cannot change live size
floor cannot justify entry
floor cannot override source-readiness block
```

### 4.10 Verdict

| Component            | Verdict                                             |
| -------------------- | --------------------------------------------------- |
| per-city hard floors | `REQUIRES_RERUN`                                    |
| stable-city `0.85`   | `ACCEPT_AS_HEURISTIC_ONLY`                          |
| Denver/Paris `0.85`  | `ACCEPT_AS_HEURISTIC_ONLY`; policy, not calibration |
| Lagos `0.45`         | `REQUIRES_REDEFINITION`                             |
| null no-WU floors    | `REQUIRES_REDEFINITION`                             |
| HIGH-to-LOW transfer | `REJECT_FOR_LIVE`                                   |

---

## 5. §2.2 `k` multiplier adversarial audit

### 5.1 Does failure mean `k` is fake?

No.

The §2.2 result only says:

> The tested multiplier `1 + k/sqrt(N)` did not robustly improve the chosen diagnostics under the chosen sample construction.

It does **not** prove:

```text
small sample risk is absent
N is correctly defined
Brier detects small-N live risk
ECE is powered
sample size does not matter for trading
```

### 5.2 Metric choice failure

Brier and ECE are calibration diagnostics. They are not risk-capital diagnostics.

| Metric               | What it sees                        | What it misses                                                |
| -------------------- | ----------------------------------- | ------------------------------------------------------------- |
| Brier all rows       | average probability error over bins | tail loss, sizing convexity, executable cost, one winning bin |
| winning-row Brier    | realized winning-bin calibration    | selected after outcome; not a live tradable set               |
| ECE                  | binned reliability                  | unstable at small N; binning arbitrary                        |
| train/test stability | time generalization                 | can still leak if features/models built full-sample           |

The Platt code itself recognizes sample-size maturity: it refuses to fit under `n < 15`, can count independent `decision_group_ids`, and bootstraps by group if supplied. That is a better conceptual baseline than row-level `N`; DDD’s `N` must prove it is independent group count, not bin row count. 

### 5.3 Confounding

Small `N` can correlate with:

| Confound            | Effect                  |
| ------------------- | ----------------------- |
| stable cities       | small N but easy regime |
| rare shortfall bins | high noise, no power    |
| short lead windows  | easier forecasts        |
| mature WU stations  | better source quality   |
| only HIGH markets   | easier than LOW         |
| source exclusions   | worst cases removed     |

A failed `k` test can be caused by confounding, not by absence of small-sample uncertainty.

### 5.4 Is `k=0` safe?

`k=0` is safe only in this narrow sense:

```text
do not apply an empirically unsupported multiplicative penalty
```

It is unsafe if interpreted as:

```text
small sample risk requires no handling
```

Correct replacement:

```text
if independent_decision_groups < threshold:
    no live DDD-based sizing
    no promotion evidence
    shadow/report only

if nonzero_shortfall_bin_N < threshold:
    curve bin is uncalibrated
    no live curve use
```

### 5.5 Alternative handling

Use:

| Risk                   | Safer mechanism                                 |
| ---------------------- | ----------------------------------------------- |
| small calibration N    | maturity gate                                   |
| few nonzero shortfalls | curve disabled / pool hierarchically            |
| new city/source        | source-readiness quarantine                     |
| low independent groups | no Platt/DDD live promotion                     |
| sparse tails           | tail-risk prior or block, not smooth multiplier |
| unknown unknowns       | report label + shadow replay                    |

### 5.6 Verdict

| Component                             | Verdict                                                     |
| ------------------------------------- | ----------------------------------------------------------- |
| §2.2 statistical failure              | `ACCEPT_AS_HEURISTIC_ONLY`: the tested multiplier failed    |
| inference “small-sample risk absent”  | `REJECT_FOR_LIVE`                                           |
| `k=0` as v1 implementation            | `SHADOW_ONLY`; acceptable only with separate maturity gates |
| using Brier/ECE to waive small-N risk | `REQUIRES_REDEFINITION`                                     |

---

## 6. §2.3 `sigma_window` adversarial audit

### 6.1 ACF meaning

An ACF on coverage measures correlation in the coverage proxy. It does not necessarily measure:

```text
station outage process
provider outage process
source revision process
critical-hour missingness
settlement error risk
trading P&L risk
```

If the underlying coverage metric is wrong, the ACF is a clean statistic on the wrong object.

### 6.2 Outage clustering

The conclusion that coverage drops cluster in time is plausible. Weather-station outages and provider gaps are often regime-like: power issues, storms, local infrastructure, rate limits, endpoint changes, station maintenance, and provider backfill behavior can all cluster.

But the modeling implication is not automatically “use 90 days.”

It could imply:

```text
use change-point segmentation
use source/station quarantine
use robust σ
use outage-state model
use hard gate for clusters
```

### 6.3 90-day risk

A 90-day window can fail in both directions:

| Failure           | Mechanism                                                    |
| ----------------- | ------------------------------------------------------------ |
| over-smoothing    | real degradation gets averaged into baseline                 |
| under-reacting    | new outage regime not detected quickly                       |
| over-reacting     | one month of seasonal provider behavior contaminates quarter |
| city mismatch     | tropical/temperate/infrastructure regimes differ             |
| station migration | pre/post station data pooled                                 |
| metric mismatch   | HIGH/LOW coverage risk pooled                                |

### 6.4 Lagos/Shenzhen SNR problem

The summary says Lagos/Shenzhen have high σ, meaning σ-band can absorb anomalies.

Adversarial interpretation:

> High σ may be the signature of unreliable infrastructure. Absorbing anomalies there can invert DDD: the cities most in need of protection get the weakest signals.

A σ-band that says “Lagos bad days are normal for Lagos” may be statistically honest but trading-dangerous.

### 6.5 Robust σ alternatives

Required reruns:

| Alternative                  | Why                                |
| ---------------------------- | ---------------------------------- |
| rolling MAD                  | less sensitive to outage spikes    |
| Huberized σ                  | limits outlier absorption          |
| EWMA with change-point reset | adapts to new regimes              |
| two-state outage HMM         | separates normal and outage states |
| station-segmented σ          | prevents migration pooling         |
| source-role-segmented σ      | prevents fallback/primary mixing   |
| metric-specific σ            | HIGH/LOW separation                |
| season-specific σ            | weather/process seasonality        |

### 6.6 Interaction with absolute hard kill

Relying on `coverage < 0.35` as the catastrophic catch creates a gap:

```text
coverage = 0.80
but missing 13:00-16:00 local on heat-spike day
```

Daily coverage is fine; settlement-relevant availability is not.

The absolute hard kill catches broad data disappearance, not critical-window failure.

### 6.7 Verdict

| Component                   | Verdict                    |
| --------------------------- | -------------------------- |
| coverage clustering finding | `ACCEPT_AS_HEURISTIC_ONLY` |
| `sigma_window=90`           | `REQUIRES_RERUN`           |
| high σ absorbing anomalies  | `LIVE_MONEY_RISK`          |
| reliance on `0.35` kill     | `REQUIRES_REDEFINITION`    |
| live use                    | `SHADOW_ONLY`              |

---

## 7. §2.4 curve adversarial audit

### 7.1 Sparse nonzero bins

The curve is explicitly underpowered:

```text
most shortfalls = 0
nonzero bins = very small N
progression = noisy
```

That is not a live sizing curve. That is an exploratory plot.

### 7.2 Monotonicity

The accepted curve is monotone by operator design:

```text
more shortfall -> larger discount
```

But monotone plausibility is not calibration.

A monotone curve can still be wrong in magnitude, threshold, and action type.

### 7.3 Tail risk

Mean error is not enough.

Trading loss is dominated by:

```text
missing the one decisive extreme hour
station/source mismatch
posterior high-confidence wrong bin
entry price convexity
Kelly leverage
exit liquidity
```

A mean error curve can show modest degradation while tail losses explode.

### 7.4 Curve magnitude

A 2–9% discount is likely too small for the events that matter.

Example:

```text
posterior edge produces $1,000 Kelly size
DDD discount = 9%
size becomes $910
but correct action under source mismatch or critical-hour outage is $0
```

So the curve can reduce size while preserving the bad trade.

### 7.5 9% cap

The 9% cap has no empirical basis in the summary.

It looks like policy:

```text
avoid over-penalizing
preserve opportunity
keep DDD mild
```

That is not a data-derived live-risk bound.

### 7.6 Sizing discount vs gate

Curve should not be the first live DDD primitive.

Safer hierarchy:

```text
1. source/station/current-day validity gate
2. metric/local-date/DST identity gate
3. critical-window coverage gate
4. report/shadow DDD label
5. only then, if validated, mild sizing discount
```

### 7.7 Verdict

| Component            | Verdict                    |
| -------------------- | -------------------------- |
| directional relation | `ACCEPT_AS_HEURISTIC_ONLY` |
| curve values         | `REQUIRES_RERUN`           |
| 9% cap               | `REQUIRES_REDEFINITION`    |
| live sizing use      | `REJECT_FOR_LIVE`          |
| shadow logging       | `SHADOW_ONLY`              |

---

## 8. Missing §2.5 / §2.6 audit

### 8.1 §2.5 `small_sample_floor`

The absence of a completed small-sample-floor experiment invalidates the practical interpretation of §2.2.

If `k=0`, then small-N protection must come from somewhere else:

```text
minimum independent days
minimum independent decision groups
minimum nonzero-shortfall examples
minimum source-station regimes
minimum HIGH/LOW examples
minimum forward-shadow examples
```

Without §2.5, DDD can become:

```text
k = 0
no sample penalty
curve underpowered
floors operator-overwritten
live sizing affected anyway
```

That is unacceptable.

### 8.2 §2.6 `peak_window` radius

Peak-window radius is not cosmetic. It defines the target object.

If the market is daily high, coverage at 02:00 local usually matters less than coverage during the actual max-temperature window. If the market is daily low, overnight and early-morning windows matter, often across local midnight.

A daily 24h coverage ratio treats:

```text
missing 03:00
missing 15:00
missing the actual max report
missing a harmless off-peak hour
```

as equivalent. They are not equivalent.

### 8.3 HIGH vs LOW invalidation

Without §2.6:

| Result      | Why invalidated                                            |
| ----------- | ---------------------------------------------------------- |
| hard floors | floor may be daily/HIGH-specific                           |
| σ-window    | σ of daily coverage may not match critical-window coverage |
| curve       | shortfall bins may use wrong shortfall                     |
| `0.35` kill | daily threshold may miss directional critical outage       |
| `k`         | sample-size effects differ by metric/window                |

### 8.4 Verdict

| Missing item            | Verdict                                          |
| ----------------------- | ------------------------------------------------ |
| §2.5 small_sample_floor | `REQUIRES_RERUN`; blocks Phase 2 live use        |
| §2.6 peak_window radius | `REQUIRES_REDEFINITION`; blocks Phase 2 live use |
| HIGH/LOW transfer       | `REJECT_FOR_LIVE`                                |

---

## 9. Time / timezone / local-day audit

### 9.1 Core rule

The settlement object is:

```text
city-local settlement date × temperature metric × settlement source/station
```

not:

```text
UTC date × generic city name × any weather source
```

### 9.2 UTC timestamp

`utc_timestamp` is a necessary instant identity, but it is not the settlement day.

Correct usage:

```text
utc_timestamp -> convert to city timezone -> local_timestamp -> target_date
```

Wrong usage:

```text
DATE(utc_timestamp) = target_date
```

### 9.3 Target local date

The ingestion layer is designed to assign local date using timezone-aware conversion; the DDD scripts must prove they read `target_date` from the observation table or recomputed it identically. The WU hourly client explicitly uses local-date bucketing and stores local timestamp/offset/DST fields. ([GitHub][3])

### 9.4 DST

DST breaks fixed denominators.

| Day type               | Expected local-hour issue                                     |
| ---------------------- | ------------------------------------------------------------- |
| spring forward         | 23 local hours; one missing local hour                        |
| fall back              | 25 local hours; ambiguous repeated local hour                 |
| non-DST cities         | 24 local hours                                                |
| UTC bucket enumeration | may still be 24 UTC buckets but not 24 local wall-clock slots |

A denominator of `24` is not universally correct for local-day directional coverage.

### 9.5 Peak window crossing midnight

LOW windows can require hours from:

```text
target_date - 1 evening
target_date early morning
target_date + 1? depending on market definition
```

If the Polymarket question says “low on May 3 in City X,” the source’s settlement convention must define whether overnight hours before sunrise belong to local date May 3. Scripts cannot infer this from UTC date.

### 9.6 HIGH vs LOW windows

| Metric          | Usual relevant local regime                       | Primary risk                   |
| --------------- | ------------------------------------------------- | ------------------------------ |
| HIGH            | afternoon / early evening                         | missing heat-spike hour        |
| LOW             | overnight / sunrise                               | cross-midnight / DST ambiguity |
| tropical HIGH   | narrower distribution; small error can move bins  |                                |
| continental LOW | radiational cooling; local station siting matters |                                |

### 9.7 Station timezone

Weather stations can report in UTC, local standard time, or provider-normalized time. The repo stores `timezone_name`, `local_timestamp`, `utc_offset_minutes`, and DST flags; DDD must use them.

### 9.8 Settlement date

Settlement date is not forecast issue date, fetch date, or observation import date.

Bad branches:

```text
target_date = source fetch date
target_date = UTC date of max observation
target_date = market open date
target_date = forecast valid date without local calendar conversion
```

### 9.9 Tests required

| Test                | Required assertion                                                           |
| ------------------- | ---------------------------------------------------------------------------- |
| UTC/local roundtrip | every observation row’s `target_date` equals local date of `local_timestamp` |
| DST spring-forward  | expected slots = 23 or correct station convention                            |
| DST fall-back       | repeated hour disambiguated by UTC timestamp/fold                            |
| HIGH peak window    | local-hour window around actual/historical max not UTC                       |
| LOW window          | cross-midnight coverage correct                                              |
| source date         | fetch/import date never used as target date                                  |
| market date         | Polymarket question target date matches DB target date                       |
| station migration   | pre/post station days never pooled without segment key                       |

---

## 10. Overfitting / leakage / multiple-testing audit

| Failure path                                            | How it enters                           | Threatened conclusion | Severity | Mitigation                                                       |
| ------------------------------------------------------- | --------------------------------------- | --------------------- | -------: | ---------------------------------------------------------------- |
| operator floors after seeing results                    | Denver/Paris/Lagos overrides            | §2.1                  |        9 | label policy vs statistical; rerun blind/frozen                  |
| outage threshold chosen around known events             | catastrophic detection tuned post hoc   | §2.1, §2.3            |        9 | forward validation on unseen outages                             |
| train/test split by target date only                    | model/features built full-sample        | §2.2, §2.4            |        8 | split by information availability; rebuild features inside train |
| Platt model full-sample                                 | calibrated probabilities leak test data | §2.2                  |        9 | train-only Platt; test frozen model                              |
| calibration pairs generated with full-sample parameters | labels/features contaminated            | §2.2, §2.4            |        8 | regenerate pairs causally                                        |
| city floor multiple comparisons                         | many cities/floors tried                | §2.1                  |        7 | multiple-comparison correction or hierarchical model             |
| sigma window selected post ACF                          | 90d chosen after observed clusters      | §2.3                  |        7 | nested validation / rolling forward                              |
| curve bins chosen after observing noise                 | monotone curve manually accepted        | §2.4                  |        8 | pre-register bins; bootstrap CI                                  |
| sparse nonzero bins                                     | noisy means look directional            | §2.4                  |        8 | minimum bin N; pool or disable                                   |
| HIGH-only data                                          | results transferred to LOW              | all                   |       10 | metric-specific rerun                                            |
| station migration ignored                               | pre/post baselines pooled               | §2.1, §2.3            |       10 | station-segmented baselines                                      |
| source fallback mixed                                   | primary/fallback rows pooled            | all                   |        9 | source_role filters                                              |
| DB view data_version shift                              | current view changes corpus             | all                   |        8 | freeze data_version hash                                         |
| duplicate bins treated independent                      | ECE/Brier N inflated                    | §2.2                  |        8 | decision_group bootstrap                                         |
| winning-row analysis post-outcome                       | conditional on realized winner          | §2.2                  |        6 | use ex-ante selected decision rows                               |
| report/backtest cohort mixing                           | diagnostic result treated executable    | live rollout          |       10 | cohort gates; executable replay only                             |

---

## 11. Live-trading translation audit

### 11.1 DDD layer choice

DDD has four possible layers:

| Layer            | Use                          | Live readiness                                 |
| ---------------- | ---------------------------- | ---------------------------------------------- |
| report label     | annotate data quality        | safe now                                       |
| shadow signal    | compute but do not act       | safe now                                       |
| sizing discount  | reduce Kelly size            | not proven                                     |
| eligibility gate | block city/metric/source/day | conceptually necessary, but threshold unproven |

The current evidence supports only report/shadow.

### 11.2 Interaction with Platt

Platt may already internalize historical city/source reliability if unreliable data affected calibration pairs. Or it may not, if coverage failures are absent from training rows because missing days were skipped.

Two failure modes:

| Mode              | Result                                                                |
| ----------------- | --------------------------------------------------------------------- |
| DDD double-counts | city already calibrated down; DDD further suppresses                  |
| DDD undercounts   | missing data absent from pairs; Platt sees clean subset; DDD too mild |

The calibration store has explicit v2 metric/data-version logic and warns about legacy HIGH-only behavior. Any DDD-Platt interaction must use metric-specific v2 rows and independent decision groups. 

### 11.3 Interaction with Kelly

Kelly sizing is convex in posterior edge:

```text
f* = (p_posterior - entry_price) / (1 - entry_price)
```

A small multiplicative discount does not correct a corrupted posterior. If the posterior is wrong because the settlement source is wrong, then the correct size is zero, not 91% of the original size. The repo’s Kelly function applies typed execution price and fractional Kelly, but DDD would need to enter upstream as eligibility or posterior uncertainty, not just a cosmetic multiplier. 

### 11.4 Interaction with market executable cost

Brier/ECE do not include:

```text
best_bid / best_ask
spread
fill probability
slippage
fees
exit cost
orderbook depth
venue resolution timing
```

A calibration improvement can still worsen executable EV if it suppresses good trades and fails to block bad source-failure trades.

### 11.5 Interaction with Day-0 circuit breaker

Day-0 current observation checks should dominate historical DDD.

Priority:

```text
if current source/station invalid: block
elif current observation stale: block
elif current critical window missing: block
elif historical DDD high: shadow/report or maybe reduce size after validation
```

Historical DDD cannot rescue current-day observation corruption.

### 11.6 False halt vs false trade tradeoff

| Error                 | Cost                                                   |
| --------------------- | ------------------------------------------------------ |
| false halt            | missed opportunity                                     |
| false trade           | realized loss, bad live evidence, possible compounding |
| false report          | promotion corruption                                   |
| false sizing discount | hidden underperformance                                |

For source/station/current-day failures, false trade dominates. For mild historical coverage noise, false halt may dominate. That means DDD must be split into gates vs discounts.

### 11.7 Verdict

| Live use                          | Verdict                                                              |
| --------------------------------- | -------------------------------------------------------------------- |
| report label                      | `SHADOW_ONLY` allowed                                                |
| shadow computation                | `SHADOW_ONLY` allowed                                                |
| Kelly-size discount               | `REQUIRES_RERUN`                                                     |
| source/current-day readiness gate | `REQUIRES_REDEFINITION`, then can be live if independently validated |
| entry/exit effect                 | `REJECT_FOR_LIVE` now                                                |
| promotion evidence                | `REJECT_FOR_LIVE` now                                                |

---

## 12. Hidden branch register

| Hidden branch                                           | Calculation consequence                  | Threatened conclusion | Live consequence                  | Detection test                             | Required rerun                  | Verdict                 |
| ------------------------------------------------------- | ---------------------------------------- | --------------------- | --------------------------------- | ------------------------------------------ | ------------------------------- | ----------------------- |
| global conclusion trusted without recomputing scripts   | accepts stale/wrong outputs              | all                   | false live promotion              | reproduce all JSON hashes from raw DB      | P0 reproduction                 | `REVIEW_REQUIRED`       |
| hard floors overwritten by operator ruling              | statistical result no longer falsifiable | §2.1                  | policy treated as evidence        | compare pre/post override manifest         | rerun with frozen policy labels | `OVERFIT_RISK`          |
| Denver/Paris `0.85` policy masquerades as calibration   | floor appears empirical                  | §2.1                  | false confidence                  | require `floor_source` field               | policy/evidence split           | `REQUIRES_REDEFINITION` |
| Lagos `0.45` encodes boiled-frog degraded baseline      | low reliability normalized               | §2.1/§2.3             | trades through bad infrastructure | change-point + station/source segmentation | Lagos regime rerun              | `LIVE_MONEY_RISK`       |
| null no-WU cities silently unprotected                  | no discount becomes default OK           | §2.1                  | trades with no DDD protection     | null semantics test                        | source-class gate rerun         | `REQUIRES_REDEFINITION` |
| HIGH floor applied to LOW                               | wrong physical object                    | all                   | LOW trades mis-sized              | metric-specific assertion                  | HIGH/LOW rerun                  | `REJECT_FOR_LIVE`       |
| peak window wrong by city/season                        | directional coverage wrong               | §2.1/§2.4             | misses critical hours             | empirical peak-hour distribution           | §2.6 rerun                      | `TIME_SEMANTICS_RISK`   |
| LOW window crosses local midnight                       | target date wrong                        | all LOW               | wrong LOW settlement object       | cross-midnight test cases                  | LOW rerun                       | `TIME_SEMANTICS_RISK`   |
| UTC grouping mislabels local target date                | wrong day coverage                       | all                   | wrong market/date                 | compare `DATE(utc)` vs local `target_date` | local-date rerun                | `TIME_SEMANTICS_RISK`   |
| DST 23h/25h denominator wrong                           | coverage ratio biased                    | floors/σ              | false halt/pass                   | DST expected-slot tests                    | DST rerun                       | `TIME_SEMANTICS_RISK`   |
| missing rows invisible to `COUNT`                       | denominator only observed rows           | all coverage          | false pass                        | expected-slot left join                    | coverage rerun                  | `CALCULATION_RISK`      |
| duplicated timestamps inflate coverage                  | numerator distorted                      | floors/σ              | false pass                        | uniqueness by city/source/utc/station      | dedupe rerun                    | `CALCULATION_RISK`      |
| station migration changes baseline                      | pre/post data pooled                     | floors/σ              | wrong city-source trust           | station_id timeline                        | station-segment rerun           | `LIVE_MONEY_RISK`       |
| WU source outage correlated with extreme weather        | MNAR missingness                         | curve/floors          | miss decisive extreme             | weather-event conditional missingness      | storm/outage rerun              | `LIVE_MONEY_RISK`       |
| source revision changes historical truth                | backfilled data differs from live        | all                   | live replay optimistic            | payload/revision audit                     | point-in-time rerun             | `LEAKAGE_RISK`          |
| Platt trained with future data                          | test probabilities contaminated          | k/curve               | false validation                  | train-only model refit                     | causal calibration rerun        | `LEAKAGE_RISK`          |
| `calibration_pairs_v2` generated full-sample            | features/labels leak                     | k/curve               | false DDD effect                  | rebuild pairs in train window              | causal pair rerun               | `LEAKAGE_RISK`          |
| Brier all-rows dominated by non-winning bins            | weak signal misleading                   | k                     | wrong risk decision               | compute decision-level selected-bin loss   | metric rerun                    | `CALCULATION_RISK`      |
| winning-Brier selected post-outcome                     | post-outcome subset                      | k                     | no live analogue                  | ex-ante selection replay                   | decision replay                 | `LEAKAGE_RISK`          |
| ECE underpowered                                        | false fail/pass                          | k/curve               | removes needed penalty            | bootstrap ECE CI                           | bootstrap rerun                 | `CALCULATION_RISK`      |
| nonzero shortfall bins too sparse                       | noisy curve                              | curve                 | arbitrary discount                | bin N threshold                            | curve rerun                     | `REJECT_FOR_LIVE`       |
| σ absorbs real anomalies                                | bad cities look normal                   | σ/floors              | trades through degradation        | robust σ/MAD comparison                    | robust rerun                    | `LIVE_MONEY_RISK`       |
| absolute `0.35` kill fires too late                     | only gross outages caught                | σ/hard kill           | critical-window outage missed     | inject 3-hour outage cases                 | critical-window rerun           | `LIVE_MONEY_RISK`       |
| 9% cap too small                                        | bad trades remain                        | curve                 | false comfort                     | EV sensitivity analysis                    | executable replay               | `REJECT_FOR_LIVE`       |
| DDD sizes down when it should block                     | wrong action type                        | live rollout          | real loss                         | gate-vs-discount replay                    | live-shadow replay              | `LIVE_MONEY_RISK`       |
| DDD double-penalizes Platt-internalized regime          | over-suppression                         | live                  | missed EV                         | conditional Platt residual test            | attribution rerun               | `LIVE_MONEY_RISK`       |
| DDD fails on current-day incomplete observations        | historical only                          | live                  | trades through live outage        | Day-0 live completeness test               | current-day rerun               | `LIVE_MONEY_RISK`       |
| backtest/report uses diagnostic not executable outcomes | promotion corruption                     | live reports          | false P&L                         | cohort/executable evidence gate            | report rerun                    | `LIVE_MONEY_RISK`       |
| fallback source rows enter training                     | source semantics corrupted               | all                   | wrong floor/curve                 | `training_allowed=0` audit                 | source-role rerun               | `CALCULATION_RISK`      |
| legacy HIGH-only calibration used for LOW               | Platt/DDD mismatch                       | k/curve               | LOW corruption                    | v2-only metric read test                   | metric rerun                    | `REJECT_FOR_LIVE`       |
| active data_version changes between runs                | non-reproducible corpus                  | all                   | unstable live behavior            | freeze `data_version` + hash               | P0/P1 rerun                     | `LEAKAGE_RISK`          |
| Hong Kong HKO vs airport station category error         | wrong source                             | null/floors           | wrong settlement truth            | HKO source-only test                       | HK-specific audit               | `LIVE_MONEY_RISK`       |
| Paris LFPG/LFPB mismatch                                | wrong station baseline                   | Paris floor           | wrong live city                   | source-contract monitor                    | station migration rerun         | `LIVE_MONEY_RISK`       |
| daily coverage penalizes irrelevant hours               | false discount                           | curve/floors          | missed good trades                | actual-extreme-hour weighting              | peak-window rerun               | `CALCULATION_RISK`      |
| daily coverage ignores critical missing hour            | false pass                               | curve/hard kill       | bad trade                         | critical-hour leaveout replay              | peak-window rerun               | `LIVE_MONEY_RISK`       |
| row count denominator ignores expected provider cadence | wrong coverage                           | floors                | false pass                        | `data_coverage` expected rows              | coverage rerun                  | `CALCULATION_RISK`      |
| local station reports sub-hourly                        | hourly collapse loses raw cadence risk   | coverage              | hidden sparse cadence             | use `observation_count` distribution       | cadence rerun                   | `CALCULATION_RISK`      |
| source fetch date confused with observation date        | wrong target                             | all                   | wrong settlement                  | import/fetch/obs date test                 | time rerun                      | `TIME_SEMANTICS_RISK`   |
| settlement labels from different provider               | calibration target mismatch              | k/curve               | wrong posterior trust             | source equality audit                      | settlement-source rerun         | `LIVE_MONEY_RISK`       |

---

## 13. Revised validation plan

### 13.1 P0 — exact reproduction

Required:

```text
- unzip package
- list every script/result file
- compute SHA256 of scripts and outputs
- rerun each Phase 1 script from clean DB copy
- compare output JSON/MD byte-for-byte or with declared tolerances
- record exact command, DB path, data_version, git SHA, Python env
```

Block if reproduction fails.

### 13.2 Corrected SQL / expected-slot coverage

Do not compute coverage from observation rows alone.

Required object:

```text
expected_slot(city, target_date, metric, source, station_id, local_hour, utc_timestamp)
LEFT JOIN observed_slot(...)
```

Use:

```text
coverage = observed_expected_slots / expected_slots
critical_coverage = observed_critical_slots / expected_critical_slots
```

Expected slots must account for:

```text
city timezone
DST
source cadence
station convention
HIGH vs LOW
cross-midnight LOW
source/station migration
```

Use `data_coverage` or a purpose-built expected-slot calendar, not `COUNT` over observed observations alone.

### 13.3 City-local date grouping

Rerun every coverage and error calculation with:

```text
target_date = city-local date
not DATE(utc_timestamp)
```

Emit a diff report:

```text
rows where DATE(utc_timestamp) != target_date
coverage change by city/date
affected floor/shortfall bins
```

### 13.4 HIGH/LOW separation

Separate:

```text
high:
  physical_quantity = daily_max_temperature
  observation_field = high_temp / running_max

low:
  physical_quantity = daily_min_temperature
  observation_field = low_temp / running_min
```

No shared floor, σ, curve, calibration, or N unless a hierarchical model proves pooling.

### 13.5 Peak-window validation

For every city/metric/season:

```text
actual_extreme_hour_distribution
historical_peak_hour_distribution
coverage sensitivity by radius r ∈ {1,2,3,4,6,12,24}
missed-extreme-hour frequency
```

Do not use fixed `peak_hour ± 3` until validated.

### 13.6 Station-migration segmented baselines

Build segment key:

```text
city
temperature_metric
settlement_source_type
source
station_id
data_version
source_contract_version
date_range
```

Floors and σ cannot cross segment boundaries unless explicitly pooled with evidence.

### 13.7 Robust σ / MAD test

Compare:

```text
rolling std
rolling MAD
EWMA volatility
change-point reset σ
two-state outage model
```

Outcome metrics:

```text
critical outage detection
false halt rate
tail settlement error
executable EV
```

### 13.8 Confidence intervals and bootstrap

For every floor/curve/sigma component:

```text
bootstrap over independent decision_group_id or city-date
not per-bin row unless justified
report CI for false-positive and false-negative rates
report bin N
report effective independent N
```

### 13.9 Tail-loss metric

Replace mean-error-only evidence with:

```text
P(|settlement_error| >= 1 bin)
P(winning bin outside top posterior mass)
missed critical-hour rate
conditional log-loss on selected live trades
expected dollar loss under Kelly
max drawdown contribution
```

### 13.10 Live-shadow replay

Replay DDD as if live, but no live action:

```text
decision_time evidence only
actual executable bid/ask where available
same strategy eligibility
same Kelly path
same source gates
same exit assumptions
```

Compare:

```text
baseline live-eligible strategy
DDD discount strategy
DDD gate strategy
source-readiness gate strategy
hybrid
```

### 13.11 Promotion criteria

DDD can affect live only after:

```text
- exact reproduction passes
- expected-slot coverage implemented
- HIGH/LOW split passes
- local-time/DST tests pass
- station segments pass
- no-WU source classes explicitly handled
- confidence intervals reported
- forward shadow replay improves executable EV or reduces tail loss
- false halt rate acceptable
- policy overrides labeled and isolated
```

---

## 14. Final component classifications

| Component               | Current conclusion          | Adversarial verdict        | Required action                               | Live eligibility                                 |
| ----------------------- | --------------------------- | -------------------------- | --------------------------------------------- | ------------------------------------------------ |
| DDD concept             | useful risk modifier        | `SHADOW_ONLY`              | redefine target object                        | no live effect                                   |
| `daily_cov`             | coverage input              | `REQUIRES_REDEFINITION`    | expected-slot local-date rerun                | no                                               |
| `directional_cov`       | peak-window coverage        | `REQUIRES_REDEFINITION`    | §2.6 rerun by city/metric/season              | no                                               |
| per-city hard floors    | PASS with overrides         | `REQUIRES_RERUN`           | rerun without hidden overrides; label policy  | no                                               |
| stable default `0.85`   | accepted                    | `ACCEPT_AS_HEURISTIC_ONLY` | policy field, not statistical field           | shadow/report only                               |
| Denver/Paris `0.85`     | operator override           | `ACCEPT_AS_HEURISTIC_ONLY` | source/station segmented proof                | no                                               |
| Lagos `0.45`            | operator override           | `REQUIRES_REDEFINITION`    | change-point/source segmentation              | no                                               |
| null no-WU cities       | excluded                    | `REQUIRES_REDEFINITION`    | explicit source-class gates                   | no                                               |
| `k` multiplier          | FAIL, `k=0`                 | `ACCEPT_AS_HEURISTIC_ONLY` | replace with maturity gates                   | shadow only                                      |
| small-sample risk       | implicitly reduced          | `REQUIRES_REDEFINITION`    | §2.5 floor and independent N                  | no                                               |
| `sigma_window=90`       | PARTIAL PASS                | `REQUIRES_RERUN`           | robust σ/MAD/change-point validation          | no                                               |
| high σ absorption       | accepted for Lagos/Shenzhen | `LIVE_MONEY_RISK`          | do not absorb without outage-state model      | no                                               |
| absolute `0.35` kill    | safety backstop             | `REQUIRES_RERUN`           | critical-window and source-specific tests     | no, except separately validated hard source gate |
| discount curve          | DIRECTIONAL PASS            | `REJECT_FOR_LIVE`          | tail-loss/executable replay                   | no                                               |
| 9% cap                  | accepted                    | `REQUIRES_REDEFINITION`    | empirical EV basis or remove                  | no                                               |
| §2.5 small_sample_floor | missing                     | `REQUIRES_RERUN`           | complete before Phase 2                       | no                                               |
| §2.6 peak_window radius | missing                     | `REQUIRES_REDEFINITION`    | complete before Phase 2                       | no                                               |
| HIGH-to-LOW transfer    | unknown                     | `REJECT_FOR_LIVE`          | metric-specific rerun                         | no                                               |
| report label            | not specified               | `SHADOW_ONLY`              | safe if cohort-labeled                        | yes, diagnostic only                             |
| live sizing             | intended Phase 2 risk       | `REJECT_FOR_LIVE`          | wait for reruns                               | no                                               |
| entry/exit gating       | intended risk               | `REQUIRES_REDEFINITION`    | source/current-day gate only after validation | no                                               |
| promotion evidence      | not proven                  | `REJECT_FOR_LIVE`          | executable cohort replay                      | no                                               |

---

## 15. Codex/local-agent prompts

### P0 — reproduce Phase 1 results exactly

````text
Role:
You are a forensic reproducibility auditor. Your job is to reproduce the DDD Phase 1 package outputs exactly before any interpretation.

Read-first files:
- task_2026-05-03_ddd_implementation_plan.zip
- PLAN.md inside the package
- phase1/*.py
- phase1_results/*.md
- phase1_results/*.json
- README / repo status files needed to locate DB paths

Allowed files:
- read all package files
- read repo source files needed to run scripts
- write only to docs/archives/packets/task_2026-05-03_ddd_reproduction/
- write only copied/temp DB files under /tmp or an explicitly named scratch directory

Forbidden files/actions:
- do not mutate production DBs
- do not edit source code
- do not edit config/cities.json
- do not run live trading
- do not write state/control files
- do not silently patch scripts

Invariants:
- reproduction must start from a clean worktree or record dirty diff
- every command must be logged
- every output file must have SHA256
- exact DB path, git SHA, Python version, package hash, and data_version must be recorded
- if output differs, mark REVIEW_REQUIRED, not PASS

Tasks:
1. Unzip/list the package.
2. Compute SHA256 for every file.
3. Read PLAN.md and reconstruct intended commands.
4. Run each phase1 script exactly as intended.
5. Compare generated outputs with phase1_results outputs.
6. Record byte diffs or JSON semantic diffs.
7. Identify any implicit dependencies: DB path, environment variable, data_version, current view, random seed.

Commands:
```bash
set -euo pipefail
mkdir -p docs/archives/packets/task_2026-05-03_ddd_reproduction
unzip -l task_2026-05-03_ddd_implementation_plan.zip
rm -rf /tmp/ddd_phase1_pkg
mkdir -p /tmp/ddd_phase1_pkg
unzip task_2026-05-03_ddd_implementation_plan.zip -d /tmp/ddd_phase1_pkg
find /tmp/ddd_phase1_pkg -type f -print0 | sort -z | xargs -0 sha256sum > docs/archives/packets/task_2026-05-03_ddd_reproduction/package_sha256.txt
python --version
git rev-parse HEAD
git status --short
````

Tests:

* Every result file either reproduces exactly or has an explained diff.
* No script writes outside the declared scratch/output path.
* No production DB modified.

Expected outputs:

* P0_REPRODUCTION_REPORT.md
* package_sha256.txt
* command_log.txt
* output_diff_summary.json
* exact_reproduction_pass=false/true

Closeout evidence:

* Paste the command log.
* Paste SHA256 manifest.
* Paste diff summary.
* State whether Phase 1 can be audited as reproducible.

````

### P1 — schema/field/time-object audit

```text
Role:
You are a schema-field semantics auditor. Your job is to prove whether each Phase 1 variable maps to the intended real-world object.

Read-first files:
- PLAN.md
- phase1/*.py
- src/state/db.py
- src/state/schema/v2_schema.py
- src/data/observation_instants_v2_writer.py
- src/data/tier_resolver.py
- src/calibration/store.py
- config/cities.json
- architecture/city_truth_contract.yaml
- docs/operations/current_source_validity.md

Allowed files:
- read repo and package
- write docs/archives/packets/task_2026-05-03_ddd_schema_audit/

Forbidden files/actions:
- no DB mutation
- no source code changes
- no config changes
- no live calls

Invariants:
- Each variable must map to a table.column or explicit derived expression.
- Every derived expression must state denominator, grouping, source filter, metric filter, and timestamp basis.
- If a script reads legacy tables, mark legacy vs v2 explicitly.
- If a field can be null/default on existing DBs, mark the failure branch.

Tasks:
1. Build a variable-to-field table for daily_cov, directional_cov, shortfall, sigma_train, N, Brier, ECE, target_date, utc_timestamp, source, data_version.
2. Grep scripts for table names and SQL.
3. Identify every unqualified SELECT and every legacy table read.
4. Identify whether scripts use `observation_instants_v2`, `observation_instants_current`, legacy `observation_instants`, or `data_coverage`.
5. Identify whether `temperature_metric` is filtered.
6. Identify whether `authority`, `training_allowed`, `source_role`, and `data_version` are filtered.

Commands:
```bash
rg -n "observation_instants|observation_instants_v2|observation_instants_current|data_coverage|calibration_pairs|calibration_pairs_v2|platt|target_date|utc_timestamp|temperature_metric|source_role|training_allowed|data_version|authority" phase1 src scripts docs > docs/archives/packets/task_2026-05-03_ddd_schema_audit/rg_schema_hits.txt
sqlite3 "$ZEUS_WORLD_DB" ".schema observation_instants_v2" > docs/archives/packets/task_2026-05-03_ddd_schema_audit/schema_observation_instants_v2.sql
sqlite3 "$ZEUS_WORLD_DB" ".schema calibration_pairs_v2" > docs/archives/packets/task_2026-05-03_ddd_schema_audit/schema_calibration_pairs_v2.sql
sqlite3 "$ZEUS_WORLD_DB" ".schema data_coverage" > docs/archives/packets/task_2026-05-03_ddd_schema_audit/schema_data_coverage.sql
````

Tests:

* No variable remains unmapped.
* No table read remains unclassified.
* Any script without metric/source/date/data_version filters is flagged.

Expected outputs:

* P1_SCHEMA_FIELD_TIME_OBJECT_AUDIT.md
* variable_mapping.csv
* sql_read_surface_register.csv
* REVIEW_REQUIRED_register.md

Closeout evidence:

* Show exact SQL snippets for each Phase 1 calculation.
* State whether scripts compute target object, proxy, or wrong object.

````

### P2 — city-local date and DST rerun

```text
Role:
You are a time-semantics rerun auditor. Your job is to recompute coverage using city-local settlement dates, DST-aware expected slots, and explicit expected-slot denominators.

Read-first files:
- PLAN.md
- phase1 coverage scripts
- src/data/wu_hourly_client.py
- src/data/observation_instants_v2_writer.py
- config/cities.json
- src/state/schema/v2_schema.py

Allowed files:
- write new audit scripts under scripts/audit/ddd_time_semantics/
- write outputs under docs/archives/packets/task_2026-05-03_ddd_time_semantics/
- use copied DB only

Forbidden files/actions:
- do not edit Phase 1 scripts except in copied scratch
- do not mutate production DB
- do not change city config

Invariants:
- `target_date` means city-local settlement date.
- `DATE(utc_timestamp)` must not be used as settlement date.
- DST days must not assume fixed 24 local hours.
- Expected slots must be generated independently of observed rows.

Tasks:
1. Generate expected local-hour slots for each city/date/source/station.
2. LEFT JOIN observed rows.
3. Compute daily and directional coverage.
4. Compare to Phase 1 coverage.
5. Produce list of dates where UTC grouping differs from local grouping.
6. Produce DST-specific coverage diffs.

Commands:
```bash
python scripts/audit/ddd_time_semantics/build_expected_slots.py \
  --db-copy /tmp/zeus_world_ddd_time.db \
  --out docs/archives/packets/task_2026-05-03_ddd_time_semantics/expected_slots.parquet

python scripts/audit/ddd_time_semantics/compare_utc_vs_local.py \
  --db-copy /tmp/zeus_world_ddd_time.db \
  --out docs/archives/packets/task_2026-05-03_ddd_time_semantics/utc_local_diff.json

python scripts/audit/ddd_time_semantics/rerun_coverage.py \
  --db-copy /tmp/zeus_world_ddd_time.db \
  --expected-slots docs/archives/packets/task_2026-05-03_ddd_time_semantics/expected_slots.parquet \
  --out docs/archives/packets/task_2026-05-03_ddd_time_semantics/coverage_local_dst.json
````

Tests:

* Known DST transition dates produce 23/25-hour local behavior or documented station convention.
* Every observed row maps to one city-local target_date.
* Missing expected slots are visible even when no observation row exists.

Expected outputs:

* P2_TIME_SEMANTICS_RERUN.md
* coverage_local_dst.json
* utc_local_diff.json
* dst_case_table.csv
* changed_floor_candidates.json

Closeout evidence:

* State how many city-date rows changed coverage.
* State which Phase 1 conclusions change.

````

### P3 — HIGH/LOW/peak-window rerun

```text
Role:
You are a metric-identity and directional-window auditor. Your job is to rerun DDD separately for HIGH and LOW using validated city/season peak windows.

Read-first files:
- PLAN.md
- phase1 scripts
- src/types/metric_identity.py
- src/calibration/store.py
- src/signal/ensemble_signal.py
- config/cities.json
- docs/reference or architecture files on high/low identity

Allowed files:
- scripts/audit/ddd_metric_windows/
- docs/archives/packets/task_2026-05-03_ddd_metric_windows/

Forbidden files/actions:
- no live code mutation
- no production DB mutation
- no shared HIGH/LOW assumptions unless proven in output

Invariants:
- HIGH and LOW are separate physical objects.
- LOW windows may cross local midnight.
- Peak-window radius is an empirical variable, not a constant assumption.
- Daily coverage must be reported separately from critical-window coverage.

Tasks:
1. Compute actual extreme-hour distributions by city, metric, season.
2. Evaluate radius r = 1,2,3,4,6,12,24.
3. Compute coverage sensitivity and missed-extreme-hour risk.
4. Rerun floors using metric-specific directional coverage.
5. Identify any Phase 1 HIGH-to-LOW leakage.

Commands:
```bash
python scripts/audit/ddd_metric_windows/extreme_hour_distribution.py \
  --db-copy /tmp/zeus_world_ddd_metric.db \
  --out docs/archives/packets/task_2026-05-03_ddd_metric_windows/extreme_hour_distribution.json

python scripts/audit/ddd_metric_windows/window_radius_sensitivity.py \
  --db-copy /tmp/zeus_world_ddd_metric.db \
  --radii 1,2,3,4,6,12,24 \
  --out docs/archives/packets/task_2026-05-03_ddd_metric_windows/window_radius_sensitivity.json

python scripts/audit/ddd_metric_windows/rerun_metric_specific_floors.py \
  --db-copy /tmp/zeus_world_ddd_metric.db \
  --out docs/archives/packets/task_2026-05-03_ddd_metric_windows/metric_specific_floors.json
````

Tests:

* Any LOW use of HIGH calibration/floor fails.
* Any missing `temperature_metric` filter fails.
* Any UTC-date LOW overnight grouping fails.

Expected outputs:

* P3_HIGH_LOW_PEAK_WINDOW_RERUN.md
* extreme_hour_distribution.json
* window_radius_sensitivity.json
* metric_specific_floors.json
* high_low_leakage_register.md

Closeout evidence:

* State whether Phase 1 floors/curve survive HIGH/LOW split.

````

### P4 — station migration / source segmentation audit

```text
Role:
You are a source-contract and station-migration auditor. Your job is to determine whether DDD baselines pooled incompatible source/station regimes.

Read-first files:
- config/cities.json
- docs/operations/current_source_validity.md
- src/data/tier_resolver.py
- src/data/observation_instants_v2_writer.py
- scripts/watch_source_contract.py
- docs/runbooks/settlement_mismatch_triage.md
- Phase 1 scripts/results

Allowed files:
- scripts/audit/ddd_source_segments/
- docs/archives/packets/task_2026-05-03_ddd_source_segments/

Forbidden files/actions:
- no city config changes
- no source release
- no settlement rebuild
- no calibration rebuild
- no live orders

Invariants:
- Baselines cannot pool different settlement stations without explicit segment.
- Fallback source rows are not equivalent to primary source rows for training.
- Null WU does not mean no risk.
- Active market description/source contract dominates static assumptions.

Tasks:
1. Build city/source/station timeline.
2. Detect station_id changes, source changes, and fallback-source rows.
3. Segment all coverage by source, station_id, source_role, data_version.
4. Rerun floors/sigma per segment.
5. Flag cities where Phase 1 pooled incompatible segments.
6. Special-case Paris, Hong Kong, Istanbul, Moscow, Tel Aviv.

Commands:
```bash
python scripts/audit/ddd_source_segments/build_source_station_timeline.py \
  --db-copy /tmp/zeus_world_ddd_source.db \
  --out docs/archives/packets/task_2026-05-03_ddd_source_segments/source_station_timeline.json

python scripts/audit/ddd_source_segments/rerun_segmented_coverage.py \
  --db-copy /tmp/zeus_world_ddd_source.db \
  --out docs/archives/packets/task_2026-05-03_ddd_source_segments/segmented_coverage.json

python scripts/audit/ddd_source_segments/compare_pooled_vs_segmented.py \
  --phase1-results phase1_results \
  --segmented docs/archives/packets/task_2026-05-03_ddd_source_segments/segmented_coverage.json \
  --out docs/archives/packets/task_2026-05-03_ddd_source_segments/pooled_vs_segmented_diff.json
````

Tests:

* Paris LFPG/LFPB-like source shifts produce separate segments.
* HKO cannot be replaced by airport ICAO.
* Fallback rows do not enter training-eligible baselines silently.

Expected outputs:

* P4_STATION_SOURCE_SEGMENTATION_AUDIT.md
* source_station_timeline.json
* segmented_coverage.json
* pooled_vs_segmented_diff.json
* null_city_semantics.md

Closeout evidence:

* State which city floors are invalid due to source/station pooling.

````

### P5 — robust statistics and bootstrap confidence intervals

```text
Role:
You are a robust-statistics auditor. Your job is to replace point estimates with uncertainty intervals and robust alternatives.

Read-first files:
- phase1 statistics scripts
- phase1_results/*.json
- src/calibration/platt.py
- src/calibration/store.py
- corrected outputs from P2/P3/P4

Allowed files:
- scripts/audit/ddd_robust_stats/
- docs/archives/packets/task_2026-05-03_ddd_robust_stats/

Forbidden files/actions:
- no live code mutation
- no production DB mutation
- no policy overrides during fit

Invariants:
- Bootstrap unit is independent city-date or decision_group_id, not bin row, unless justified.
- Every curve bin must report N and effective N.
- Confidence intervals must be reported before accepting component.
- Multiple testing over cities/floors must be acknowledged.

Tasks:
1. Compute bootstrap CIs for floors.
2. Compute false-positive/false-negative intervals.
3. Compare std vs MAD vs Huber vs EWMA σ.
4. Bootstrap discount curve bins.
5. Test k using tail-loss and executable decision metrics, not just Brier/ECE.
6. Report underpowered bins as disabled.

Commands:
```bash
python scripts/audit/ddd_robust_stats/bootstrap_floors.py \
  --corrected-coverage docs/archives/packets/task_2026-05-03_ddd_time_semantics/coverage_local_dst.json \
  --out docs/archives/packets/task_2026-05-03_ddd_robust_stats/floor_bootstrap_ci.json

python scripts/audit/ddd_robust_stats/robust_sigma_compare.py \
  --corrected-coverage docs/archives/packets/task_2026-05-03_ddd_time_semantics/coverage_local_dst.json \
  --out docs/archives/packets/task_2026-05-03_ddd_robust_stats/robust_sigma_compare.json

python scripts/audit/ddd_robust_stats/bootstrap_curve.py \
  --corrected-ddd-pairs docs/archives/packets/task_2026-05-03_ddd_metric_windows/ddd_pairs_metric_specific.json \
  --out docs/archives/packets/task_2026-05-03_ddd_robust_stats/curve_bootstrap_ci.json
````

Tests:

* Any bin with effective N below threshold is marked DISABLED.
* Operator overrides are excluded from statistical CI.
* Bootstrap unit documented.

Expected outputs:

* P5_ROBUST_STATS_BOOTSTRAP.md
* floor_bootstrap_ci.json
* robust_sigma_compare.json
* curve_bootstrap_ci.json
* underpowered_bins.md

Closeout evidence:

* State which current Phase 1 conclusions survive confidence intervals.

````

### P6 — live-trading EV translation test

```text
Role:
You are an executable-EV auditor. Your job is to test whether DDD improves live-relevant decisions, not just calibration diagnostics.

Read-first files:
- src/engine/evaluator.py
- src/strategy/kelly.py
- src/strategy/oracle_penalty.py
- src/strategy/market_fusion.py
- src/state/db.py
- report/backtest code
- corrected DDD outputs from P2-P5

Allowed files:
- scripts/audit/ddd_live_ev/
- docs/archives/packets/task_2026-05-03_ddd_live_ev/

Forbidden files/actions:
- no live submit
- no production state writes
- no pretending midpoint/VWMP equals executable fill
- no promotion claims from diagnostic-only replay

Invariants:
- Use decision-time evidence only.
- Separate diagnostic replay from executable replay.
- If orderbook/fill evidence is absent, mark diagnostic only.
- Compare discount vs gate vs no-DDD.
- Tail loss and missed EV must both be reported.

Tasks:
1. Replay historical live-eligible decisions with frozen evidence.
2. Apply candidate DDD as:
   a. report label only
   b. Kelly discount
   c. eligibility gate
   d. source/current-day gate
3. Measure executable EV where bid/ask/fill evidence exists.
4. Measure diagnostic calibration where executable evidence absent.
5. Identify bad trades DDD would prevent and good trades it would suppress.
6. Evaluate interactions with Platt, oracle penalty, Kelly, and Day-0 gates.

Commands:
```bash
python scripts/audit/ddd_live_ev/build_decision_replay_set.py \
  --db-copy /tmp/zeus_trades_ddd_ev.db \
  --out docs/archives/packets/task_2026-05-03_ddd_live_ev/decision_replay_set.json

python scripts/audit/ddd_live_ev/replay_ddd_policies.py \
  --decision-set docs/archives/packets/task_2026-05-03_ddd_live_ev/decision_replay_set.json \
  --ddd-candidates docs/archives/packets/task_2026-05-03_ddd_robust_stats/ddd_candidates.json \
  --out docs/archives/packets/task_2026-05-03_ddd_live_ev/policy_replay_results.json

python scripts/audit/ddd_live_ev/summarize_ev_tail_loss.py \
  --policy-results docs/archives/packets/task_2026-05-03_ddd_live_ev/policy_replay_results.json \
  --out docs/archives/packets/task_2026-05-03_ddd_live_ev/ev_tail_loss_summary.md
````

Tests:

* No diagnostic-only replay can be labeled executable.
* DDD discount cannot be accepted if gate policy dominates tail-loss reduction.
* Any policy increasing tail loss is rejected.

Expected outputs:

* P6_LIVE_EV_TRANSLATION_TEST.md
* decision_replay_set.json
* policy_replay_results.json
* ev_tail_loss_summary.md
* prevented_bad_trades.csv
* suppressed_good_trades.csv

Closeout evidence:

* State whether any DDD policy improves executable EV with confidence.

````

### P7 — revised DDD shadow-only implementation guard

```text
Role:
You are a live-safety implementation guard auditor. Your job is to ensure any DDD implementation remains shadow-only until promotion gates pass.

Read-first files:
- PLAN.md
- src/engine/evaluator.py
- src/strategy/kelly.py
- src/state/db.py
- report/backtest code
- docs/authority/zeus_current_architecture.md if present
- corrected outputs from P0-P6

Allowed files:
- add or modify only shadow/report code paths approved by operator
- write docs/archives/packets/task_2026-05-03_ddd_shadow_guard/
- add tests under tests/ if implementation is requested

Forbidden files/actions:
- no live sizing effect
- no live entry/exit effect
- no control-plane changes
- no live submit
- no changing Kelly multiplier in production path
- no using DDD in promotion evidence
- no default-OK when DDD unavailable

Invariants:
- DDD values must be persisted with `ddd_shadow_only=true`.
- Any attempt to read DDD into live sizing fails tests.
- Null city/source DDD state must be explicit, not silent OK.
- HIGH/LOW fields must be explicit.
- Source/station/data_version must be included.
- Report cohorts must label DDD as diagnostic.

Tasks:
1. Locate every possible sizing/entry/report integration point.
2. Add guard tests proving DDD cannot affect live size.
3. Add shadow telemetry schema or report sidecar if approved.
4. Add unavailable/null semantics tests.
5. Add metric/source/time identity tests.
6. Add promotion gate requiring P0-P6 artifacts before live activation.

Commands:
```bash
rg -n "kelly_size|dynamic_kelly_mult|oracle_penalty|size_usd|should_trade|EdgeDecision|report|promotion|backtest" src tests > docs/archives/packets/task_2026-05-03_ddd_shadow_guard/integration_points.txt

pytest tests/test_ddd_shadow_guard.py -q
pytest tests/test_runtime_guards.py -q
````

Tests:

* `test_ddd_shadow_does_not_change_size_usd`
* `test_ddd_shadow_does_not_change_should_trade`
* `test_null_ddd_city_not_default_ok`
* `test_high_low_ddd_identity_required`
* `test_source_station_data_version_required`
* `test_report_labels_ddd_diagnostic_only`
* `test_live_activation_requires_p0_to_p6_closeout`

Expected outputs:

* P7_DDD_SHADOW_ONLY_IMPLEMENTATION_GUARD.md
* integration_points.txt
* guard_test_results.txt
* live_activation_blockers.md

Closeout evidence:

* Show tests proving live path unchanged.
* State exact condition required for future live activation.

````

---

## 16. Final recommendation

### 16.1 Implement now

Implement only:

```text
DDD shadow telemetry
DDD report labels
source/station/metric/time identity audit outputs
expected-slot coverage audit
null-source explicit status
````

Allowed now:

| Item                                           | Status                           |
| ---------------------------------------------- | -------------------------------- |
| compute DDD in shadow                          | yes                              |
| persist DDD diagnostic sidecar                 | yes, if labeled shadow           |
| show DDD in internal reports                   | yes, with diagnostic-only banner |
| add tests preventing live influence            | yes                              |
| add source/current-day readiness observability | yes                              |

### 16.2 Keep shadow-only

Keep these shadow-only:

```text
per-city floors
shortfall
sigma window
discount curve
k=0 setting
critical-window coverage
0.35 hard-kill candidate
```

### 16.3 Rerun

Rerun before Phase 2:

```text
P0 reproduction
P1 schema/field audit
P2 local-date/DST expected-slot coverage
P3 HIGH/LOW/peak-window split
P4 source/station segmentation
P5 robust statistics/bootstrap
P6 executable EV replay
P7 live guard tests
```

### 16.4 Reject for live now

Reject now:

| Component                           | Reason                                   |
| ----------------------------------- | ---------------------------------------- |
| discount curve as Kelly input       | sparse/noisy evidence, wrong action type |
| 9% cap                              | unsupported and likely too small         |
| HIGH floors on LOW                  | physical-object mismatch                 |
| null no-WU default OK               | silent unprotected city/source           |
| Lagos `0.45` live floor             | boiled-frog risk                         |
| Denver/Paris evidence claim         | policy override, station risk            |
| `k=0` as “small sample solved”      | false inference                          |
| `sigma_window=90` live              | over-smoothing and anomaly absorption    |
| `0.35` absolute kill as sole safety | misses critical-window outages           |

### 16.5 Must not affect live trading

Until reruns pass, DDD must not affect:

```text
live entry
live exit
Kelly size
market eligibility
portfolio caps
promotion reports
P&L claims
strategy comparison
operator dashboards that imply live-safe evidence
```

The safest revised plan is:

```text
Phase A: Shadow-only DDD with exact source/metric/time/station identity.
Phase B: Expected-slot local-date rerun and HIGH/LOW split.
Phase C: Source/station segmented baselines with robust intervals.
Phase D: Live-shadow replay comparing discount vs gate vs no-DDD.
Phase E: Only source/current-day readiness gates can be considered first.
Phase F: Sizing discount only if executable EV improves and tail loss falls.
```

---

## 17. Final verification loop

|  # | Check                                                                       | Answer                                                                                                                                                 |
| -: | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
|  1 | Did you distrust the current conclusions?                                   | Yes. Every conclusion was treated as unproven.                                                                                                         |
|  2 | Did you reconstruct the plan before attacking it?                           | Yes. Section 1 reconstructs formula, components, rulings, and Phase 1 results.                                                                         |
|  3 | Did you map variables to real-world objects?                                | Yes. Section 2 maps plan variables to fields, intended objects, measured proxies, and mismatch risks.                                                  |
|  4 | Did you audit scripts, not just summaries?                                  | Partially. The package scripts were unavailable, so script-level claims are `REVIEW_REQUIRED`; repo context and inferred script surfaces were audited. |
|  5 | Did you identify calculation-result hidden branches?                        | Yes. Section 12 is the hidden-branch register.                                                                                                         |
|  6 | Did you attack hard floors, `k`, `sigma_window`, and curve separately?      | Yes. Sections 4–7.                                                                                                                                     |
|  7 | Did you account for time, timezone, local day, HIGH/LOW, and DST?           | Yes. Sections 8–9 and hidden-branch register.                                                                                                          |
|  8 | Did you account for overfitting and leakage?                                | Yes. Section 10.                                                                                                                                       |
|  9 | Did you judge live-trading translation separately from calibration metrics? | Yes. Section 11.                                                                                                                                       |
| 10 | Did you produce concrete rerun prompts?                                     | Yes. Section 15 includes P0–P7 prompts.                                                                                                                |
| 11 | Did you give a decisive live/shadow/reject recommendation?                  | Yes. Section 16: DDD is `SHADOW_ONLY`; curve/live sizing is rejected; Phase 1 must rerun.                                                              |

[1]: https://github.com/fitz-s/zeus "GitHub - fitz-s/zeus · GitHub"
[2]: https://raw.githubusercontent.com/fitz-s/zeus/main/config/cities.json "raw.githubusercontent.com"
[3]: https://github.com/fitz-s/zeus/blob/main/src/data/wu_hourly_client.py "zeus/src/data/wu_hourly_client.py at main · fitz-s/zeus · GitHub"
[4]: https://raw.githubusercontent.com/fitz-s/zeus/main/src/state/db.py "raw.githubusercontent.com"
