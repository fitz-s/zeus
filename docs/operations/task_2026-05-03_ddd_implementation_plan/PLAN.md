# Data Density Discount (DDD) Implementation & Validation Plan

**Created**: 2026-05-03
**Authority**: Operator directive to empirically validate DDD parameters prior to active trading intervention.
**Reference Specification**: `docs/reference/zeus_oracle_density_discount_reference.md` §6.

## §1 Executive Summary

The transition to an anomaly-based Data Density Discount (DDD) resolves the "fake safety" of mismatching blind-spots without double-penalizing the regime-correlated sparsity that the Platt calibration already absorbs. However, as the operator correctly identified, "异常折价" (Anomaly-based Discounting) carries extreme parameter-sensitivity and overfitting risks.

This plan details the empirical validation pipeline required **before** the DDD specification alters any live trading logic. We will explicitly validate the 6 core tunable parameters (`HARD_FLOOR`, `k`, `sigma_window`, `curve breakpoints`, `small_sample threshold`, `peak_window radius`) using historical database residuals.

**Validation Outputs**: A set of empirically justified parameters backed by historical `calibration_pairs_v2` and `observation_instants_v2` data, minimizing in-sample optimism through strict train/test splits.
**Implementation Shape**: A multi-phase rollout starting with shadow-mode logging, progressing to a single-city canary (Lagos), and culminating in global activation, governed by clear entry/exit criteria.
**Kill-switch Criteria**: If validation reveals that anomalous coverage drops do not correlate with measurable Brier/log-loss degradation in the test set, or if the "white noise" variance dominates the anomaly signal, the anomaly-based DDD formulation will be halted and returned to the operator for re-spec.

**Operator success benediction (2026-05-03)**: "当 Brier Score 曲线证明了 (1 + k/sqrt(N)) 的确能平滑大样本与小样本的风险敞口时，你就亲手为 Zeus 打造了一面免疫系统级别的盾牌。" — the small-sample multiplier validation (§2.2) is the load-bearing experiment that defines whether DDD is an immune-system shield or just another patch.

---

## §1.1 Operator Rulings (2026-05-03) — binding for this plan

The following three rulings from the operator override any prior question-and-answer
positions in this plan. Every experiment, phase, and threshold below MUST be
consistent with these three rulings. Reviewers should reject any plan section
that drifts.

### Ruling 1 — Train/Test Split: Time-window, NEVER geography

**Mandate**: All parameter validation experiments must split data on **time**, never on geography (city/region).

**Reasoning** (operator verbatim, paraphrased): the core hypothesis is that Platt has internalized **city-specific** regime and infrastructure characteristics. Holding out APAC as a test set would test "can the US baseline generalize to Asia" — physically impossible given monsoon, marine effects, and infrastructure heterogeneity. What we DO need to test is **time-series generalization**: same city, fit on 2025 (one ENSO phase, one set of seasons), validate on 2026+ (different ENSO phase, different season transitions). If parameters survive on the time axis, they captured underlying causality, not short-run noise.

**Concrete cutoff**: train on data with `target_date < 2026-01-01`; test on `target_date >= 2026-01-01`. Apply uniformly across §2.1 – §2.6.

### Ruling 2 — Day-0 Circuit Breaker: composite (per-city scaled + absolute hard kill)

**Mandate**: Day-0 circuit breaker is a **two-rail composite**, not a uniform threshold.

**Reasoning**: a uniform 0.40 floor lets Tokyo trade at 0.45 (catastrophic — half a day broken!) while penalizing Lagos at 0.40 (its routine cloudy-day baseline). Both directions are wrong. Need both:

| Rail | Trigger | Action |
|---|---|---|
| **Relative** (per-city scaled) | `today_directional_cov < city_floor[city] - 2*sigma_window[city]` | CAUTION / dynamic position reduction (size-down, not halt) |
| **Absolute** (uniform hard kill) | `today_directional_cov < 0.35` regardless of city | **HARD BLACKLIST for the day** — halt all entries with `entries_blocked_reason="day0_observation_gap_absolute"` |

The 0.35 absolute floor is grounded in physics: if a day has lost more than 2/3 of its hourly observations, no extreme inferred from the remaining 8/24 has statistical significance. Below this floor, regardless of historical baseline, no probability claim is defensible.

**Note**: this introduces a DDD-adjacent BLACKLIST path that the canonical reference §6 explicitly disclaimed for the historical-baseline DDD itself. The absolute hard kill at 0.35 is the **Day-0 §7** mechanism, not the historical DDD §6 mechanism — they remain conceptually distinct. See `zeus_oracle_density_discount_reference.md` §7 for the corresponding update.

### Ruling 3 — Lagos Hard Floor: data-driven, override interface preserved

**Mandate**: do NOT pre-approve `HARD_FLOOR_FOR_SETTLEMENT["Lagos"] = 0.65` ahead of validation. Run §2.1 first; let the data set the value.

**Reasoning**: pre-setting 0.65 is operator-experience overriding objective data. If Lagos's actual 2025 H2 median was 0.60, hardcoding 0.65 would trigger penalty every day, eventually starving Lagos out of the trading portfolio entirely — losing whatever marginal alpha that edge market provides.

**Implementation requirement**: Phase 2 MUST add a `hard_floor_for_settlement` field to the `cities.json` schema as an operator override interface. Default: `null` → falls through to the data-derived value. Operator may set explicit overrides in the future when local infrastructure improves and they want to ratchet the floor up.

**Phase 1 §2.1 deliverable** must include a per-city table of "data-driven floor recommendation" alongside the empirical evidence — Lagos's number is whatever 2025 H2 P10 (or operator-chosen percentile) of directional coverage actually is. No rounding to "nice" numbers.

---

## §2 Validation Experiments

For each parameter in the `DDD_actual` formula, we must prove its value against historical reality.

### 2.1 `HARD_FLOOR_FOR_SETTLEMENT[city]`
- **Hypothesis**: The hard floor should strictly exceed the coverage observed during documented catastrophic outages (e.g., Lagos gap, Ogimet stoppage) but remain below the lowest *routine* noise floor, preventing the "温水煮青蛙" (Boiled Frog) effect.
- **Data Slice**: `observation_instants_v2` for 6 US cities + Lagos + 3 stable international cities. Dates: 2025-07-01 to 2026-04-30.
- **Train/Test Split**: Train on 2025-07-01 to 2025-12-31 to find the floor. Test on 2026-01-01 to 2026-04-30 (which contains the known Lagos/Ogimet outages).
- **Acceptance Criteria**: The hard floor triggers on 100% of the known historical outage days in the test set, but triggers on < 1% of the days operator marked as "normal" or "verified" in settlement.
- **Rejection Criteria**: If no scalar value can separate the outage days from the 1st percentile of normal variance days without excessive false positives.
- **SQL Skeleton**:
  ```sql
  SELECT city, target_date,
         CAST(COUNT(DISTINCT utc_timestamp) AS REAL)/24.0 as daily_cov
  FROM observation_instants_v2
  WHERE source = 'wu_icao' AND data_version = 'v1.wu-native'
  GROUP BY city, target_date
  ORDER BY daily_cov ASC;
  ```

### 2.2 Small-Sample Penalty Multiplier `k` in `(1 + k/sqrt(N))`
- **Hypothesis**: Platt prediction error (measured via Brier score or cross-entropy against `outcome`) increases for small `N`. The scalar `k` must proportionally counterweight the empirical degradation of calibration confidence at small `N`.
- **Data Slice**: `calibration_pairs_v2` spanning all cities.
- **Train/Test Split**: **Time-window per Ruling 1** — train on rows with `target_date < 2026-01-01` to find optimal `k`; evaluate Brier score flattening on rows `target_date >= 2026-01-01` for the SAME cities. Geography splits are forbidden (Ruling 1).
- **Why this works**: each (city, metric) pair is its own degradation curve. We're testing whether a single `k` value, fit on 2025, generalizes to 2026 across all (city, metric) Brier-vs-N points. If `k` is regime-dependent rather than universal, this split reveals it.
- **Acceptance Criteria**: Applying the `k`-scaled discount to the 2026 Platt predictions normalizes the Brier/log-loss curve between N=120 (Lagos) and N=600k (Dallas) within ±15% relative error of the 2025-fit value.
- **Rejection Criteria**: If log-loss does not monotonically decrease with `N`, or if the noise at small `N` is too high to fit a `1/sqrt(N)` curve, OR if 2026's optimal `k` differs from 2025's by >50%, meaning small-sample cities require flat exclusion rather than continuous scaling. **This is the operator's "immune-system shield" experiment** (§1 benediction) — its outcome decides whether DDD is structurally sound.
- **Python Skeleton**:
  ```python
  import numpy as np
  from sklearn.metrics import brier_score_loss
  # Group by city, metric to get N and outcomes
  # Optimize k to minimize variance in (Brier_Score / (1 + k/sqrt(N))) across bins
  ```

### 2.3 `sigma_window` (1σ band days)
- **Hypothesis**: Daily coverage variance is Poisson-like white noise that does not persist. A relatively short window (30-60 days) accurately captures the variance without dragging old regime data into the current standard deviation.
- **Data Slice**: Directional coverage ratios for US cities (where data is routine but sparse).
- **Train/Test Split**: Not applicable (time-series autocorrelation analysis).
- **Acceptance Criteria**: The autocorrelation function (ACF) of daily coverage drops to near-zero within 3-5 days. If true, a 30-day window is sufficient to compute `sigma`. If persistent, 60 or 90 days is required.
- **Rejection Criteria**: The standard deviation over any chosen window is larger than the typical `shortfall` we care about, making the 1σ band swallow all anomalies (meaning the signal-to-noise ratio of coverage is too low).

### 2.4 Discount Curve Breakpoints
- **Hypothesis**: The financial penalty of an oracle blind-spot is proportional to the size of the coverage shortfall, and caps out at ~9% (the threshold of absolute unreliability short of blacklisting).
- **Data Slice**: `settlements_v2` vs `observation_instants_v2` coverage.
- **Train/Test Split**: Train on 2025; Test on 2026.
- **Acceptance Criteria**: The historical mismatch rate (when observable) roughly tracks the proposed 0/0.10/0.25/0.40 shortfall buckets.
- **Rejection Criteria**: Mismatch vs Shortfall relationship is bimodal (e.g., either 0% error or 15% error, with no 2-8% middle ground). In this case, the continuous curve must be replaced with a step function.

### 2.5 `small_sample_floor` threshold (N<100)
- **Hypothesis**: Below a critical mass `N`, Platt calibration has not achieved the regime-conditional convergence required to trust the baseline at all.
- **Data Slice**: `calibration_pairs_v2`.
- **Train/Test Split**: **Time-window per Ruling 1** — bin (city, metric) pairs by their N-as-of-2025-12-31 (the train cutoff). Compute Brier on each bin's 2026+ observations. Random sample splits forbidden (Ruling 1: would leak time and conflate regime).
- **Acceptance Criteria**: Brier score variance explodes below N=100, justifying a hard floor for the discount (e.g., `max(0.05, ...)`).
- **Rejection Criteria**: The threshold of variance explosion is found to be much higher (e.g., N=1000), requiring us to reconsider how many cities can safely trade.

### 2.6 `peak_window` radius
- **Hypothesis**: The directional coverage window must capture the physical temperature curve. LOW peaks (dawn min) are sharp and require a narrower window; HIGH peaks (afternoon max) are flatter and require a wider window.
- **Data Slice**: `observation_instants_v2` for 10 geographically diverse cities.
- **Train/Test Split**: All data (physical observation).
- **Acceptance Criteria**: 95% of all daily extremes fall within `historical_peak_hour ± radius_high` and `historical_low_hour ± radius_low`.
- **Rejection Criteria**: The distribution of peak hours is so uniform or bimodal for certain cities that a fixed radius cannot capture 95% of extremes without expanding to the entire 24h day.

---

## §3 Overfitting Stress Tests

Because "Anomaly-based Discounting" relies on deviations from a derived baseline, it is highly susceptible to overfitting. We must explicitly test against these scenarios.

### 3.1 In-sample optimism (TIME-WINDOW, NOT geography — per Ruling 1)
- **Test Scenario**: The formula is tuned entirely on data up to 2025-12-31 (training period spans different ENSO phase, different summer/winter regime). Validate on 2026-01-01 onward — same cities, different time, naturally different climate state.
- **Expected Behavior**: Parameters (`k`, breakpoints, sigma) generalize across the time axis on the same cities. False-positive rate on 2026 holdout is within 1.5× of training-period rate per (city, metric).
- **Detection Criterion**: Per-city false-positive DDD trigger rate on 2026 holdout > 2× the 2025 training-period rate, OR optimal hyperparameters differ by >50% between train and test windows.
- **Fallback Policy**: If a parameter fails time-window stability, fall back to a fixed conservative value (e.g., `k=0`, no small-sample multiplier; `sigma_window=∞`, no σ-band) and document the failure as a blocker for that parameter's adoption. Do NOT attempt to tune via geography hold-out — that would test the wrong invariant (Ruling 1).
- **Anti-pattern explicitly forbidden**: holding out APAC cities as a "test set" tests whether the US baseline generalizes to monsoon climates, which is physically impossible and not what we're trying to prove. The thing we ARE proving is that a city's own historical regime is stable enough that its 2025-fit baseline predicts its 2026 anomalies.

### 3.2 Regime brittleness
- **Test Scenario**: A city/track pair operates in a small-sample regime where Platt fails to internalize the bias (e.g. Lagos LOW).
- **Expected Behavior**: The `small_sample_floor` and `(1+k/sqrt(N))` multiplier aggressively down-size the trades.
- **Detection Criterion**: Track the running sum of `outcome - p_raw` for small N cities. If calibration consistently misprices by >10% over 20 settlements, the regime has broken.
- **Fallback Policy**: Auto-quarantine `(city, metric)` pairs where N < 100 AND running bias exceeds 10%, overriding the DDD calculation entirely.

### 3.3 WU outage Black Swan
- **Test Scenario**: WU returns HTTP 200 OK, but the JSON contains a stale cached snapshot from 4 days ago. Cells exist, so coverage ratio = 1.0.
- **Expected Behavior**: DDD will NOT catch this (coverage is nominally 1.0).
- **Detection Criterion**: A separate temporal integrity check must verify the delta between `utc_timestamp` of the observation and the wall-clock time of the ingest.
- **Fallback Policy**: If temporal drift > 2 hours, `coverage` for that hour evaluates to 0, forcing DDD to spike.

### 3.4 Adversarial baseline drift (Boiled Frog variant)
- **Test Scenario**: Instrument calibration drifts slowly by 0.1°C per week.
- **Expected Behavior**: `HARD_FLOOR` prevents coverage-based boiled frog, but cannot detect pure value-drift if the station never drops offline.
- **Detection Criterion**: Monitor the shift in the empirical distribution of winning bins for a given temperature value month-over-month.
- **Fallback Policy**: Oracle penalty handles this via `Mismatch Rate` (which will spike as snapshot diverges from PM settlement). DDD is explicitly not responsible for this.

### 3.5 Curve over-fit
- **Test Scenario**: Mismatch errors actually follow a step-function rather than a linear gradient.
- **Expected Behavior**: The validation of 2.4 will reveal the true distribution.
- **Detection Criterion**: R² of the linear curve fit vs a step-function fit on the validation data.
- **Fallback Policy**: Modify the curve in the Python implementation to match the empirical distribution (e.g. 0% for shortfall < 0.20, 9% for shortfall >= 0.20).

### 3.6 Today's incomplete day
- **Test Scenario**: At 14:00 UTC, the current day only has 14 hours of data. This incomplete day is accidentally included in the 90-day rolling baseline calculation.
- **Expected Behavior**: The formula explicitly excludes `today` from `floor_soft` and `sigma`.
- **Detection Criterion**: Unit tests must explicitly verify that a simulated 0-coverage "today" does not drop the 90-day median or inflate the 60-day standard deviation.
- **Fallback Policy**: Strict array slicing in the implementation.

---

## §4 Implementation Phases

### Phase 1: Empirical Validation (Current)
- **Action**: Execute the SQL and Python validation scripts defined in §2. No code changes to `src/strategy/`.
- **Entry Criteria**: Operator approves this plan.
- **Exit Criteria**: All 6 parameters are empirically bound and documented in a validation report.
- **Rollback**: N/A (read-only).

### Phase 2: Core Logic Implementation
- **Action**: Implement `src/strategy/data_density_discount.py` with the validated parameters hardcoded. Expose `density_discount(city, track, today_utc)`. Implement `tests/test_data_density_discount.py`.
- **Entry Criteria**: Phase 1 report approved by Operator.
- **Exit Criteria**: All unit tests pass, confirming the mathematics match the canonical reference §6.
- **Rollback**: `git revert` the PR.

### Phase 3: Wiring & Feature Flag
- **Action**: Wire DDD into `src/strategy/oracle_penalty._load`. Protect with a feature flag `ZEUS_ENABLE_DDD=0`. Bridge writes `density_discount` to `oracle_error_rates.json` for visibility.
- **Entry Criteria**: Phase 2 merged.
- **Exit Criteria**: `oracle_error_rates.json` shows the new fields, but `effective_rate` remains purely mismatch-driven.
- **Rollback**: Toggle `ZEUS_ENABLE_DDD=0`.

### Phase 4: Shadow Mode
- **Action**: Run the system in live production with `ZEUS_ENABLE_DDD=0`. Log what the DDD *would* have been and the corresponding Kelly size impact.
- **Entry Criteria**: Phase 3 deployed to daemon.
- **Exit Criteria**: 7 days of shadow logs prove that DDD behaves sanely (no wild spikes on noise, properly catches any real outages).
- **Rollback**: N/A.

### Phase 5: Single-City Canary (Lagos)
- **Action**: Enable `ZEUS_ENABLE_DDD=1` but restrict its application in `oracle_penalty.py` to `city == "Lagos"`.
- **Entry Criteria**: Shadow mode proves safe. Lagos provides the strongest theoretical signal for the feature.
- **Exit Criteria**: Lagos Kelly sizes dynamically adjust to coverage drops over 5 settlements without disrupting the rest of the portfolio.
- **Rollback**: Revert the city restriction or toggle `ZEUS_ENABLE_DDD=0`.

### Phase 6: Global Activation
- **Action**: Remove the city restriction. DDD applies globally.
- **Entry Criteria**: Canary successful.
- **Exit Criteria**: DDD actively modulates total oracle risk across the global portfolio.
- **Rollback**: `ZEUS_ENABLE_DDD=0`.

---

## §5 Test Plan (Antibody Tests)

These tests ensure the §5 failure modes never regress. Must be added to `TEST_FILES` in `.claude/hooks/pre-commit-invariant-test.sh` (baseline 658 -> 662).

1. `tests/test_ddd_boiled_frog.py`
   - **Prevents**: Gradual infrastructure degradation slipping past the baseline.
   - **Mechanism**: Mocks a 90-day history where coverage decays linearly from 0.95 to 0.40. Asserts that the `HARD_FLOOR_FOR_SETTLEMENT` takes over and triggers a CAUTION discount, rather than the baseline following the decay to 0.

2. `tests/test_ddd_noise_tolerance.py`
   - **Prevents**: Over-reaction to routine Poisson hourly noise.
   - **Mechanism**: Mocks a stable 0.85 coverage history. Mocks a "today" coverage of 0.80 (a 1-hour drop). Asserts that `shortfall` is absorbed by the `1*sigma` band and `DDD_actual == 0.0`.

3. `tests/test_ddd_small_sample_multiplier.py`
   - **Prevents**: Treating N=120 and N=600,000 as having equal regime-convergence.
   - **Mechanism**: Injects identical shortfalls into a mock Dallas (N=600k) and mock Lagos (N=120). Asserts that `DDD_actual(Lagos) > DDD_actual(Dallas)`.

4. `tests/test_ddd_day0_circuit_breaker.py`
   - **Prevents**: Trading blindly into a catastrophic same-day outage.
   - **Mechanism**: Mocks a live evaluation at 16:00 local time where only 2 of the 4 elapsed peak-window hours have data (`today_coverage_so_far = 0.50`). Asserts trade rejection via `day0_observation_gap`.

---

## §6 Risk Register

| Risk / Assumption | Probability | Impact | Monitoring Signal | Mitigation |
|---|---|---|---|---|
| **Assumption**: `sigma` stabilizes over 60 days. | Medium | False positives on naturally volatile cities. | High variance in `sigma` across consecutive days. | Cap `sigma` at a maximum permissible noise level (e.g. 0.15). |
| **Risk**: Operator manually overrides `HARD_FLOOR` too low. | Low | Boiled frog succeeds. | `city_floor_hardened` < 0.50. | Hardcode an absolute global minimum of 0.60 in the python logic. |
| **Assumption**: Platt's small-N error scales as `1/sqrt(N)`. | High | Lagos penalized incorrectly. | Validation experiment 2.2 fails. | Adopt a step-function for small-N rather than continuous scaling. |
| **Risk**: Polymarket shifts the official settlement hour, moving the peak outside our `peak_window`. | Medium | Directional coverage measures the wrong hours. | Mismatch rate spikes abruptly. | Rely on existing Mismatch blacklist trigger to halt trading. |

---

## §7 Open Questions for Operator

The original three open questions were closed by operator rulings 2026-05-03
(see §1.1). They are recorded here for traceability:

1. ~~**Test Set Selection**: cities vs time window?~~ → **CLOSED by Ruling 1**: time-window split, train `< 2026-01-01`, test `>= 2026-01-01`. Geography splits forbidden.
2. ~~**Day-0 Circuit Breaker Threshold**: uniform vs per-city?~~ → **CLOSED by Ruling 2**: composite two-rail (relative `city_floor − 2σ` for size-down + absolute `0.35` for hard kill).
3. ~~**Lagos Hard Floor**: pre-approve 0.65 vs data-driven?~~ → **CLOSED by Ruling 3**: data-driven from Phase 1 §2.1 output; `cities.json` reserves `hard_floor_for_settlement` as operator override interface (default `null`).

### Newly surfaced questions (require operator before Phase 2 begins)

1. **Confidence interval for Phase 1 §2.2 acceptance**: ±15% relative error on `k` between train and test windows is the proposed bar (§2.2 acceptance criteria). Is this tight enough? Operator may want stricter (±10%) to avoid declaring success on borderline-stable parameter.
2. **2σ absolute-vs-relative for Day-0 rail 1**: the Phase 2 implementation uses `2*sigma_window`. Should `sigma_window` here be the same 60-day window used by §6 historical DDD, or a tighter (e.g., 14-day) window for intraday responsiveness? Tighter = more sensitive to recent regime; longer = more stable.
3. **`hard_floor_for_settlement` recompute cadence**: once Phase 1 sets values, how often do we recompute? Quarterly recompute keeps it current; never-recompute treats it as engineering constant. The 温水煮青蛙 protection cuts both ways: if we recompute, slow decay slides the floor; if we don't, the floor goes stale on stations that legitimately improved.
