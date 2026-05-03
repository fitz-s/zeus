# Zeus Oracle Density Discount Reference

Created: 2026-05-03
Last reused/audited: 2026-05-03
Authority basis: operator directive 2026-05-02/03 — three-part reasoning chain on
  Platt regime absorption + DDD as outage detector; verified against
  calibration_pairs_v2 residuals 2025-04-01 to 2026-04-19.

## Status

REFERENCE / DESIGN-LAW. The corrected DDD formula in §6 is the canonical
specification for any future implementation. Implementation tracker:
`docs/operations/task_2026-05-02_settlement_pipeline_audit/DATA_DENSITY_DISCOUNT.md`.

## What this document is

This document captures a million-dollar-class insight about how Platt
calibration, observation coverage, and oracle-mismatch interact in Zeus's
settlement pipeline. The TL;DR:

> **Platt calibration absorbs regime-conditional station artifacts under
> sufficiently large samples; therefore Data Density Discount must NOT
> double-penalize routine sparsity already priced into the model. DDD's
> correct role is detecting *sudden anomalous outages* relative to a hardened
> baseline, modulated by per-city Platt sample size.**

The reasoning chain that arrived at this insight is non-trivial and easily
forgotten; future agents touching oracle penalty, DDD, calibration weighting,
or settlement infrastructure should read this in full before changing any
related code or threshold.

## Background — the problem

### 1. Original gap: Mismatch is blind to source thinness

The oracle penalty system in `src/strategy/oracle_penalty.py` measures
**Mismatch Rate** between the oracle-time WU snapshot and PM's settlement
value. This catches snapshot-vs-PM pipeline drift but is **structurally blind
to source thinness**: when the underlying WU stream is thin (only 8 hours of
24 captured), both our snapshot AND PM's read agree on the same incomplete
number. Mismatch = 0% even though the trade was made on incomplete data.

Operator named this "fake safety" (`Mismatch == 0% does NOT mean Oracle is safe`).

### 2. Original proposal: flat coverage discount

First proposal (see `DATA_DENSITY_DISCOUNT.md` initial draft): introduce a
parallel signal `f(coverage_ratio)` and take the max:

```
Total Oracle Risk = max(Mismatch_Rate, f(Average_Coverage_Ratio))
```

with `coverage_ratio = num_distinct_hours_observed / 24` averaged over 90 days.

This was naive in three ways the operator surfaced.

## §3 First refinement — track-aware coverage

Operator insight: **HIGH and LOW are physically anchored at different hours of
day.** A HIGH contract resolves on the 12:00–18:00 daily peak window; a LOW
contract resolves on the 02:00–08:00 dawn-min window. Missing hours outside
the relevant window are irrelevant to that contract.

**Mechanism**: a station like KBKF (Buckley Space Force Base, Denver's
settlement station) might be 22h covered on aggregate but systematically miss
05:00. Flat coverage gives `22/24 = 0.92` (looks fine). Track-aware coverage
gives:
- HIGH window 12-18: 7/7 = 1.00 (no problem for HIGH contracts)
- LOW window 02-08: 6/7 = 0.86 (real risk for LOW contracts — missed the daily min)

**Implementation hook**: `cities.json` already stores `historical_peak_hour`;
add `historical_low_hour` and `ddd_window_radius_hours`. DDD becomes a
function of `(city, track)` not just `(city,)`.

**Empirical check** (run 2026-05-03): for Lagos (the only city with directional
asymmetry), HIGH window mean coverage ≈ 0.79, LOW window mean ≈ 0.74. For US
ICAOs (KBKF/KHOU/KLAX) the directional spread is 0–2pp because their misses
are *scattered* not *clustered* in any one window. Track-awareness is more
valuable as a future-proof framework than as a US-cities decisive lever.

## §4 Second refinement — the verification that flips everything

Operator question (2026-05-03): "为什么美国城市的coverage这么少？是我们的问题吗"

This drove a verification experiment — the **Platt residual × peak local hour
audit** (`docs/operations/task_2026-05-02_settlement_pipeline_audit/PLATT_HOUR_RESIDUAL_AUDIT.md`).

### Method

1. For each `(city, target_date, metric)`, find the actual local hour at which
   the daily extreme occurred (the "peak hour", computed from
   `observation_instants_v2.running_max`).
2. For each settlement, find the winning bucket in `calibration_pairs_v2`
   (the bin where `outcome=1`).
3. Group `p_raw` of the winning bucket by `(city, metric, peak_hour, lead_bucket)`.
4. Compare mean `p_raw` across peak hours, restricted to common hours (n≥100).

**The hypothesis under test**: if Platt has internalized per-station
artifacts, mean `p_raw` should be roughly flat across peak hours. If the model
has *not* internalized, p_raw should drop on peak hours where coverage is
chronically thin.

### Result (lead_days ∈ [3,8))

| city | metric | spread of mean p_raw across hours | relative spread |
|---|---|---|---|
| Dallas | high | 0.0078 | **5.8%** ✅ flat |
| NYC | high | 0.0183 | 14.8% |
| Houston | low | 0.0579 | 26.5% |
| LA | high | 0.0703 | 33.7% |
| Denver | low | 0.0362 | 35.4% |
| Denver | high | 0.0441 | 43.8% |
| Dallas | low | 0.0871 | 46.6% |
| Seattle | high | 0.1011 | 51.9% ⚠ |
| **Houston** | **high** | **0.0972** | **147.7%** ⚠ |

### The non-obvious interpretation

The naive reading is "Platt fails for Houston/Seattle, succeeds for Dallas."
The **correct** reading is:

> The hour-dependent variation is **regime-correlated, not coverage-correlated.**
> Houston's hours 11/12/13/14/15 all have similar coverage. Yet `p_raw` drops
> 4× from peak_hr=11 (stable clear-day regime, 0.132) to peak_hr=14 (typical
> convective summer regime, 0.035). The model correctly admits low confidence
> on convective days because they ARE genuinely harder to predict — and the
> peak-hour distribution is one observable correlate of the regime.

In other words, **Platt has internalized the joint distribution
`(regime_at_peak_hour, settlement_outcome)`, not the marginal
`(hour, outcome)`.** It is regime-aware, conditional on its training samples.

This is the million-dollar insight: a coverage-based DDD that fires on
"hours less than 24 of expected" is **not adding orthogonal information** for
high-sample cities — Platt has already priced the regime-correlated part of
sparsity through its winning-bucket distribution. Adding DDD on top would
double-penalize hard regimes.

### What DDD actually catches that Platt cannot

Platt cannot internalize:
1. **Random outages** uncorrelated with weather regime (vendor stream death,
   ingest pipeline failures, on-call missed restart). These look like white
   noise to the regime-conditional Platt surface.
2. **Coverage drops on the specific day being scored** that have no historical
   training analog. If Lagos drops to 3/24 today, that's atypical even for
   Lagos and Platt has no per-day adjustment for it.
3. **Sample-size insufficiency**: on cities/tracks where Platt has too few
   samples to reach regime convergence, ALL bias is unabsorbed. Lagos LOW
   has ~120 samples — Platt cannot statistically pin down the joint
   distribution at that scale.

Conclusion: **DDD's correct role is detecting *anomalous-relative-to-baseline*
coverage drops, not absolute thinness.**

## §5 Three failure modes the naive `shortfall = floor − today_cov` formula has

Operator (2026-05-03) identified three concrete ways the naive corrected
formula still fails. Each must be addressed in the canonical specification.

### 5.1 The Boiled Frog Problem

> Gradual infrastructure degradation slides the rolling baseline downward
> in lockstep with the actual decay; shortfall remains near zero; DDD never
> fires; eventually the station is unfit for financial settlement and the
> system has not noticed.

**Concrete scenario**: Lagos coverage drifts 0.80 (March) → 0.60 (April) → 0.40
(May). The 90-day rolling `city_floor` slides from 0.80 to ≈0.45. When today
hits 0.40, `shortfall = 0.05`. DDD ≈ 0%. **System verdict: "normal day."
Reality: station is unusable.**

**Mitigation**: replace pure rolling baseline with a **hardened floor**:

```
city_floor_hardened = max(
  HARD_FLOOR_FOR_SETTLEMENT,                  # e.g., 0.75 (physical minimum)
  historical_90d_median_under_normal_conditions
)
```

The `HARD_FLOOR_FOR_SETTLEMENT` is invariant — even if rolling drift pulls the
soft baseline down, the hard floor catches the fish-boil. Per-city overrides
allowed only for stations with documented vendor-side ceiling (Lagos may need
a lower hard floor, e.g., 0.65, because its archive natively has gaps).

**Open question**: should `historical_90d_median` exclude weekends / weekdays
or other suspected-noise days? Initial implementation: simple median; fold
back if signal is weak.

### 5.2 Noise vs True Outage

> Linear `shortfall = floor − today_cov` over-reacts to Poisson hourly noise.

Denver baseline 0.83. Today the model misses one hour, today_cov = 0.79,
shortfall = 0.04. This 1-hour-out-of-24 fluctuation is statistically
indistinguishable from daily noise — the binomial standard error around 0.83
on n=24 is ≈ 0.077. A 0.04 shortfall is **half a standard deviation** — pure
noise. DDD that fires here unnecessarily kills profitable Kelly size.

**Mitigation**: introduce a **σ-band tolerance**:

```
sigma_baseline = stddev(daily_directional_coverage,
                        last_N_normal_days_excluding_today)
shortfall = max(0,
                city_floor_hardened - today_directional_coverage - 1*sigma_baseline)
```

Only deviations exceeding ~1σ trigger DDD. This separates daily fluctuation
from true outage. Choose 1σ initially; calibrate later from realized DDD/PnL.

### 5.3 Small-Sample Penalty Multiplier

> Lagos LOW has ~120 samples in `calibration_pairs_v2`. Platt cannot reach
> the same regime-conditional convergence Houston (with thousands) has.
> Same shortfall on Lagos vs Houston deserves a stricter discount.

**Mechanism**: the §4 result that "Platt absorbs regime bias" is a
large-sample asymptotic claim. Below some sample threshold, the joint
distribution `(regime, outcome)` is undersampled and residual bias remains
unabsorbed. Lagos LOW operates in this small-sample regime.

**Mitigation**: scale DDD by the inverse-square-root of the Platt sample
count for that `(city, metric)` pair:

```
DDD_actual = DDD_raw * (1 + k / sqrt(N_platt_samples_for_city_metric))
```

For Dallas HIGH (n ≈ 600k pairs): multiplier ≈ 1.0. For Lagos LOW (n ≈ 120
pairs): multiplier ≈ 1 + k/11. With k=2: multiplier ≈ 1.18 — modest but
non-trivial. Calibrate `k` empirically; the design constraint is that the
multiplier is monotone-decreasing in N and asymptotes to 1.0 for large N.

**Open question**: is there a hard small-sample cutoff below which DDD is no
longer reliable and the city should simply be excluded from Kelly entry until
samples accumulate? Lagos LOW with 120 samples may be in that zone.
Provisional rule: if `N_platt < 100`, `DDD_actual := max(0.05, DDD_raw)` —
floor the discount because we cannot measure precisely what to discount.

**Note on `HARD_FLOOR_FOR_SETTLEMENT[city]`** (operator ruling 2026-05-03):
the per-city hard floor must be **data-driven**, not pre-approved by operator
intuition. Phase 1 §2.1 of the implementation plan sets each city's floor
from the actual 2025 H2 directional-coverage distribution (e.g., P10 of the
non-outage days). The `cities.json` schema MUST reserve a
`hard_floor_for_settlement` field as an operator override interface (default
`null` → fall through to the data-derived value). Operators can manually
ratchet the floor up later when local infrastructure improves. Pre-setting
0.65 for Lagos was rejected because if reality's H2 median is 0.60, a
hardcoded 0.65 would trigger penalty on every routine cloudy day and starve
the market of all alpha.

## §6 Canonical DDD formula (corrected — this is the spec)

```
DDD_actual(city, track, today) =

  let cov         = directional_coverage_today_in_window(city, track)        # §3
  let floor_soft  = median(directional_coverage_last_90d, exclude=today)
  let floor_hard  = HARD_FLOOR_FOR_SETTLEMENT[city]                          # §5.1
  let floor       = max(floor_hard, floor_soft)
  let sigma       = stddev(directional_coverage_last_60d_normal,
                           exclude=today)
  let shortfall   = max(0, floor - cov - 1*sigma)                             # §5.2
  let N           = count_calibration_pairs_v2(city, track, authority=VERIFIED)
  let multiplier  = 1 + k / sqrt(max(N, 1))                                  # §5.3, k=2 initial
  let DDD_raw     = apply_discount_curve(shortfall)                          # below
  let DDD_actual  = small_sample_floor(DDD_raw * multiplier, N)              # §5.3
  return DDD_actual
```

Discount curve (unchanged from initial proposal — operator-tunable):

```
shortfall range | DDD value
0               | 0.00
0.0  - 0.10     | linear 0% → 2%
0.10 - 0.25     | linear 2% → 5%
0.25 - 0.40     | linear 5% → 8%
> 0.40          | 9% (cap; never blacklist)
```

Combined with mismatch (existing oracle penalty signal):

```
oracle_error_rate(city, track) = max(mismatch_rate, DDD_actual)
```

`oracle_penalty._classify_rate(oracle_error_rate)` returns OK/INCIDENTAL/CAUTION
unchanged. **DDD is bounded at 9%** — it stays in CAUTION, never auto-blacklists.
Pipeline drift (mismatch >10%) IS a kill-switch trigger; thinness alone is NOT.

## §7 Day-0 dynamic circuit breaker — composite two-rail (operator ruling 2026-05-03)

In addition to the historical-baseline DDD above, a real-time intraday circuit
breaker is required for catastrophic same-day dropouts. Per operator ruling
2026-05-03, this is a **two-rail composite**: a per-city scaled relative rail
that fires CAUTION/dynamic-down sizing, and an absolute uniform hard-kill rail
that halts all entries for the day regardless of city.

### Why composite (operator reasoning)

A uniform 0.40 floor is wrong both ways:
- **Too lax for healthy stations**: Tokyo runs at 1.00 daily. If it drops to
  0.45 (still above a uniform 0.40 floor), the system would let trades fire —
  but Tokyo at 0.45 means the station is broken for HALF a day. Catastrophe.
- **Too strict for naturally thin stations**: Lagos's routine cloudy-day
  baseline floats around 0.40. A uniform floor would penalize every cloudy
  day, starving the market.

Both rails together: relative rail catches "abnormal for this city" without
false positives on chronic-thin cities; absolute rail catches "physically
indefensible" regardless of city baseline.

### The two rails

```
def today_directional_coverage_so_far(city, track, now_utc) -> float:
    target_window = peak_window(city, track)
    target_hours_so_far = [h for h in target_window if h <= now_local.hour]
    if not target_hours_so_far:
        return 1.0  # too early to judge
    distinct_hours_so_far = count_distinct_hours_in(target_hours_so_far)
    return distinct_hours_so_far / len(target_hours_so_far)

cov_today = today_directional_coverage_so_far(city, track, now_utc)

# RAIL 1 — Relative (per-city scaled) — fires CAUTION / dynamic size-down:
#   if cov_today < city_floor[city] - 2 * sigma_window[city]
#       AND target_window_elapsed >= 50%:
#     emit risk-event "day0_relative_drop" with kelly_mult ≈ 0.7-0.9
#     (NOT a hard halt — this is a position-shrink signal)

# RAIL 2 — Absolute (uniform) — fires HARD BLACKLIST for the day:
#   if cov_today < 0.35 AND target_window_elapsed >= 50%:
#     reject ALL entries today with entries_blocked_reason=
#       "day0_observation_gap_absolute"
#     This is unconditional and does not care about city_floor.
```

**Physics grounding for the 0.35 absolute floor**: if a day has lost more than
2/3 of its hourly observations, no extreme inferred from the remaining 8/24
hours has statistical significance. Below this floor, regardless of historical
baseline, no probability claim about daily max/min is defensible.

### Relationship to §6 DDD

The historical DDD in §6 is **bounded at 9% and never auto-blacklists**
(thinness alone never kills the city long-term). The Day-0 absolute hard
kill is a **separate, today-only mechanism** — it pauses trading for that
day specifically, then auto-clears the next day. They are conceptually
distinct:

| Mechanism | Time horizon | Action ceiling |
|---|---|---|
| §6 historical DDD | Rolling baseline, persists | Max 9% kelly down (CAUTION) |
| §7 rail 1 (relative) | Same-day partial-window | Dynamic size-down |
| §7 rail 2 (absolute) | Same-day, < 0.35 only | Hard blacklist for today |

The §7 mechanisms cite Ruling 2 (2026-05-03) as authority basis. The 0.35 and
2σ thresholds are operator-set initial values; Phase 1 §2.6 of the
implementation plan must validate them empirically on time-window holdout.

## §8 The reasoning chain (preserve this for future agents)

The journey from naive flat-coverage to the hardened formula:

```
Step 1: "Mismatch is structurally blind to source thinness"
        → propose flat coverage discount

Step 2: "HIGH and LOW are anchored at different hours physically"
        → upgrade to track-aware directional coverage

Step 3: "Why does US show 0.80-0.95 coverage when stations work?"
        → run hour-of-peak distribution analysis
        → discover: scattered misses, not regime-clustered

Step 4: "Does Platt internalize this?"
        → run Platt residual × peak_hour audit (PLATT_HOUR_RESIDUAL_AUDIT.md)
        → discover: Dallas flat (5.8% spread) BUT Houston huge (147% spread)

Step 5: "What explains Houston's 147% if it's not coverage?"
        → realize: Platt absorbed REGIME-conditional bias, not COVERAGE-conditional
        → corollary: coverage-based DDD is largely redundant for high-sample cities

Step 6: "When IS DDD adding orthogonal information?"
        → answer: random outages (uncorrelated with weather)
        → answer: catastrophic today-specific drops
        → answer: small-sample cities where Platt hasn't converged

Step 7: "Is rolling baseline immune to gradual decay?"
        → no — boiled-frog problem
        → fix: add hard floor

Step 8: "Does linear shortfall over-react to noise?"
        → yes — Poisson on n=24 has σ≈0.077
        → fix: add 1σ tolerance band

Step 9: "Should small-sample cities have stricter DDD?"
        → yes — Platt regime convergence requires N
        → fix: 1+k/sqrt(N) multiplier
```

Each step's discovery overturns or refines the previous. The final formula is
not arrived at by intuition — it is forced by empirical evidence at every
step. Future implementations that drop any element should re-run the
verification audit before doing so.

## §9 Implementation order (for the executor agent)

1. Verify `calibration_pairs_v2` sample counts per `(city, track)`. Identify
   small-sample cities (N < 1000 — heuristic threshold).
2. Add to `cities.json`: `historical_low_hour`, `ddd_window_radius_hours`,
   per-city `hard_floor_for_settlement` (default `null` — data-derived per
   Phase 1 §2.1 of the implementation plan; operator override interface only).
3. Implement `src/strategy/data_density_discount.py` exposing
   `density_discount(city, track, today_utc) -> float` per §6.
4. Wire into `src/strategy/oracle_penalty._load`:
   `effective_rate = max(mismatch, DDD_actual)`.
5. Bridge writes both `mismatch_rate` and `density_discount` to
   `data/oracle_error_rates.json` for transparency.
6. Test plan (antibody tests):
   - Boiled-frog regression: simulate a city with rolling decay; assert hard
     floor catches it.
   - Noise tolerance: simulate 1-hour daily fluctuation; assert no DDD fires.
   - Small-sample multiplier: assert Lagos LOW (N=120) gets stricter DDD than
     Dallas HIGH (N=600k) for identical shortfall.
   - Day-0 circuit breaker rail 1 (relative): assert CAUTION/size-down when
     today_cov < city_floor − 2σ after window 50% elapsed.
   - Day-0 circuit breaker rail 2 (absolute): assert hard reject when
     today_directional_cov < 0.35 after window 50% elapsed, regardless of
     city_floor.
7. Run bridge to populate `data/oracle_error_rates.json`.

## §10 Cross-references

- DDD design proposal (active implementation tracker):
  `docs/operations/task_2026-05-02_settlement_pipeline_audit/DATA_DENSITY_DISCOUNT.md`
- Platt residual verification (the empirical evidence base):
  `docs/operations/task_2026-05-02_settlement_pipeline_audit/PLATT_HOUR_RESIDUAL_AUDIT.md`
- Settlement pipeline audit:
  `docs/operations/task_2026-05-02_settlement_pipeline_audit/AUDIT.md`
- Lagos source-thinness investigation:
  `docs/operations/task_2026-05-02_full_launch_audit/LAGOS_GAP_FOLLOWUP.md`
- Calibration weighting authority:
  `docs/reference/zeus_calibration_weighting_authority.md`
- Existing oracle penalty code (must be modified per §6):
  `src/strategy/oracle_penalty.py`
- Bridge (sole writer to `data/oracle_error_rates.json`):
  `scripts/bridge_oracle_to_calibration.py`

## §11 Failure modes this design does NOT yet handle (open work)

1. **Regime-shifted city**: a city whose underlying climate changes (e.g.,
   urbanization, station relocation) such that historical Platt training is
   no longer representative of future. DDD with sample-size multiplier
   doesn't help; what's needed is a Platt-recalibration trigger driven by
   recent residual drift (separate from DDD).
2. **Cross-source contamination**: if WU starts using a different upstream
   for a city without notice, mismatch may stay 0% (both sides agree on the
   new wrong number) AND coverage may stay healthy. DDD doesn't catch this.
   Detection requires source-fingerprint comparison or a parallel
   second-vendor channel for sanity check.
3. **PM oracle-side drift**: if Polymarket changes its WU page parsing
   or its "official" hour-of-day, our snapshot vs PM divergence can grow
   even with healthy WU upstream. Mismatch catches this; DDD does not
   contribute.

These are tracked as future-iteration design items, not blockers for v1 DDD.

## §12 Why this insight is load-bearing

A Kelly-sized weather-trading system has narrow edges (often <5%). Mis-pricing
oracle uncertainty by even 2 percentage points on a Kelly-multiplier basis
compounds across ~100 trades/week into materially wrong sizing. The wrong
direction is always the same: **excessive caution costs alpha; insufficient
caution risks ruin**. DDD that double-penalizes regime-already-priced bias
costs alpha. DDD that misses true outages risks ruin. The corrected formula
walks the edge — penalizing only the residual not absorbed by Platt, while
hardening against gradual decay and small-sample fragility.

The reasoning encoded in this document took multiple verification rounds and
operator-driven refinements to arrive at. Future agents must not regress to
the naive flat-coverage formulation. If you find yourself proposing
`coverage_ratio / 24` as the discount source, **you have not read this
document**.
