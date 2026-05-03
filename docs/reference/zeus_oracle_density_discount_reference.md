# Zeus Oracle Density Discount Reference

Created: 2026-05-03
Last reused/audited: 2026-05-03
Authority basis: operator directive 2026-05-02/03 — three-part reasoning chain on
  Platt regime absorption + DDD as outage detector; verified against
  calibration_pairs_v2 residuals 2025-04-01 to 2026-04-19.
  v2 redesign 2026-05-03: Two-Rail trigger + continuous linear curve + p05 hardened floor.
  See §X for full v2 rationale.

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

### Empirical finding 2026-05-03 — `k = 0` in v1 (noble failure recorded)

Phase 1 §2.2 of the implementation plan tested the hypothesis "small N causes
worse calibration" using three measures (Brier-all-rows, Brier-winning-rows,
ECE) with operator's Ruling 1 time-window split. The result:

| measure | R² | slope direction | verdict |
|---|---|---|---|
| Brier (all rows) | 0.036 | + | weak; dominated by non-winning bucket noise |
| Brier (winning rows) | 0.000 | **−** | **opposes hypothesis** |
| ECE (10-decile bins) | 0.077 | + | borderline; below 0.10 threshold |

|Δk_train − k_test| / k_train = 62-172% across measures, well above the 50%
time-window stability threshold. The hypothesis **failed** the operator's
pre-committed kill-switch criterion (PLAN.md §1).

**v1 spec**: `k = 0`. The multiplier `(1 + k/sqrt(N))` collapses to 1.0; small-
sample amplification is disabled. The discount-floor rule
`if N_platt < 100, DDD_actual := max(0.05, DDD_raw)` is preserved separately
(it operates on a different mechanism — guard against zero-discount when the
underlying calibration is unmeasurable, not regime-conditional bias compensation).

**Why preserve the structural form `(1 + k/sqrt(N))` in code despite k=0?**
Operator directive 2026-05-03:

> "保留架构骨架，保留这个字段，但将其注释清楚 [...] 这不是为了留恋错误，
> 而是为了记录一次高贵的失败。它向未来展示我们思考过这个问题，我们测试过，
> 数据告诉我们目前不需要。"

The structural placeholder lets future agents (or future operator decisions)
revive the multiplier with empirical justification: re-run §2.2 every ~6
months as more samples accumulate; if R² rises above 0.20 and time-window
stability passes, set k > 0 with documented rationale.

**Cold-start insight (load-bearing for future onboarding)**: the §2.2 result
implies Platt's L2 regularization is much more robust than originally
feared. The "borrowed strength" from the prior makes small-N (120-1000
samples) calibration approximately as good as large-N (600,000 samples).
Conventional wisdom that "we need 1+ year of data before a new city is
trade-eligible" is therefore overstated. New "Shenzhen-class" cities can
likely be onboarded with shorter warmup than previously assumed — empirical
re-run of §2.2 on the new city's first month should confirm. **This is the
single most valuable architectural insight surfaced in this Phase 1 work.**

See `docs/operations/task_2026-05-03_ddd_implementation_plan/phase1_results/p2_2_CONCLUSION.md`
for the full evidence chain.

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

## §6 Canonical DDD formula — v2 Two-Rail design (operator-approved 2026-05-03)

**Note on σ**: σ is computed and available for monitoring/diagnostics but does NOT
enter the trigger or floor selection. See §X for the v2 redesign rationale.

**Note on asymmetric loss preferences**: city-specific asymmetric loss preferences
(e.g. Denver conservative sizing) belong in the Kelly multiplier layer, NOT in
the floor. See `docs/reference/zeus_kelly_asymmetric_loss_handoff.md`.

```
DDD_v2(city, track, today) =

  let cov          = directional_coverage_today_in_window(city, track)        # §3
  let city_floor   = CITY_FLOORS_CONFIG[city]                                 # pre-computed per §6.1; p05-based + safety minimum
  let N            = N_platt_samples(city, track)
  let N_star       = N_STAR_CONFIG[city][track]                               # from p2_5 calibration

  # ── RAIL 1: Absolute hard kill ───────────────────────────────────────
  # Physics: below 0.35, no probability claim about daily extreme is defensible.
  if cov < 0.35 AND window_elapsed > 0.50:
      return DDDResult(action='HALT', discount=0.0, rail=1)

  # ── RAIL 2: Continuous linear discount ──────────────────────────────
  shortfall = max(0.0, city_floor - cov)
  discount  = min(0.09, 0.20 * shortfall)

  # Small-sample amplification (1.25× when N < N*)
  if N < N_star:
      discount = min(0.09, discount * 1.25)

  return DDDResult(action='DISCOUNT', discount=max(mismatch_rate, discount), rail=2)
```

### §6.1 Floor selection

```
city_floor = max(0.35, p05_train_directional_coverage)
```

Policy overrides:
- Lagos 0.45: PHYSICAL_OVERRIDE (vendor archive has documented gaps; Ruling B)
- Denver: override REMOVED (Ruling A — asymmetric loss moved to Kelly layer)
- Paris: EXCLUDED pending workstream A DB resync

### §6.2 Continuous linear curve

```
shortfall | discount
0.00      | 0.0%
0.10      | 2.0%
0.20      | 4.0%
0.30      | 6.0%
0.40      | 8.0%
≥ 0.45    | 9.0% (cap — never exceeds 9%; stays in CAUTION, never BLACKLIST)
```

Formula: `D = min(0.09, 0.20 × shortfall)`

This replaces the v1 5-segment curve. Phase 1 v2 bootstrap CIs showed
adjacent mid-bins [0.10–0.20), [0.20–0.30), [0.30–0.50) are statistically
indistinguishable — the 5-segment parameterization implied false precision.

Combined with mismatch (existing oracle penalty signal):

```
oracle_error_rate(city, track) = max(mismatch_rate, DDD_discount)
```

`oracle_penalty._classify_rate(oracle_error_rate)` returns OK/INCIDENTAL/CAUTION
unchanged. **DDD is bounded at 9%** — it stays in CAUTION, never auto-blacklists.
Pipeline drift (mismatch >10%) IS a kill-switch trigger; thinness alone is NOT.

## §7 Day-0 dynamic circuit breaker — Two-Rail (v2 design, operator ruling 2026-05-03)

In addition to the historical-baseline DDD above, a real-time intraday circuit
breaker is required for catastrophic same-day dropouts. The v2 design unifies
this into the same Two-Rail framework as §6.

### Why Two-Rail (operator reasoning)

A uniform 0.40 absolute floor is wrong both ways:
- **Too lax for healthy stations**: Tokyo runs at 1.00 daily. If it drops to
  0.45 (still above a uniform 0.40 floor), the system would let trades fire —
  but Tokyo at 0.45 means the station is broken for HALF a day. Catastrophe.
- **Too strict for naturally thin stations**: Lagos's routine cloudy-day
  baseline floats around 0.40. A uniform floor would penalize every cloudy
  day, starving the market.

Both rails together: Rail 2 relative discount catches "abnormal for this city"
without false positives on chronic-thin cities; Rail 1 absolute kill catches
"physically indefensible" regardless of city baseline.

### The two rails (v2 aligned with §6)

```
cov = current_directional_coverage(city, track)  # partial-day view

# RAIL 1 — Absolute hard kill (fires HALT_TRADING_FOR_DAY):
#   if cov < 0.35 AND window_elapsed > 0.50:
#     return DDDResult(action='HALT', discount=0.0, rail=1)
#     → blocks all entries for the day; auto-clears next day

# RAIL 2 — Continuous linear discount (relative, per-city floor):
#   shortfall = max(0.0, city_floor - cov)
#   discount  = min(0.09, 0.20 * shortfall)
#   if N < N_star: discount = min(0.09, discount * 1.25)
#   return DDDResult(action='DISCOUNT', discount=max(mismatch_rate, discount), rail=2)
```

**Physics grounding for the 0.35 absolute floor**: if a day has lost more than
2/3 of its hourly observations, no extreme inferred from the remaining 8/24
hours has statistical significance. Below this floor, regardless of historical
baseline, no probability claim about daily max/min is defensible.

**Note on σ in Day-0 context**: the v1 Rail 1 used `city_floor - 2σ` as the
relative-drop threshold. In v2, σ is diagnostic-only and does NOT enter the
trigger. The relative trigger is simply `cov < city_floor` (Rail 2), and the
absolute kill is `cov < 0.35` (Rail 1). σ is logged as telemetry for regime-shift
detection (see §X).

### Relationship to §6 DDD

The §6 formula and §7 circuit breaker share the same Two-Rail structure.
They are conceptually the same mechanism applied at different time horizons:

| Mechanism | Time horizon | Action ceiling |
|---|---|---|
| §6/§7 Rail 1 (absolute) | Same-day, cov < 0.35 + window > 50% | Hard HALT for today |
| §6/§7 Rail 2 (relative) | Continuous, per-city floor | Max 9% kelly down (CAUTION) |

The §7 mechanisms cite Ruling 2 (2026-05-03) as authority basis. The 0.35
threshold is operator-set; the 0.20 linear coefficient (α) requires backtest
validation deferred to operator.

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
        → fix: 1+k/sqrt(N) multiplier (k=0 in v1 — noble failure; structure preserved)

Step 10: "Does σ-band trigger have an inverted incentive?"
         → yes — worse infra (higher σ) causes algorithm to LOWER floor to maintain
           FP rate, giving LESS DDD protection to bad stations (H1 / Denver empirical proof)
         → fix (v2): remove σ from trigger; floor = max(p05, 0.35) only
         → σ retained as diagnostic / regime-shift telemetry

Step 11: "Does 5-segment curve imply false precision?"
         → yes — Phase 1 v2 bootstrap CIs show mid-bins [0.10–0.50) are
           statistically indistinguishable
         → fix (v2): replace with continuous linear D = min(0.09, 0.20 × shortfall)

Step 12: "Does Denver 0.85 asymmetric-loss policy override belong in floor?"
         → no — asymmetric loss is a sizing preference, not a physical coverage baseline
         → fix (v2): remove Denver override from floor; move to Kelly multiplier layer
         → Lagos 0.45 retained as PHYSICAL_OVERRIDE (documented infrastructure gap)
```

Each step's discovery overturns or refines the previous. The final v2 formula is
not arrived at by intuition — it is forced by empirical evidence at every
step. Future implementations that drop any element should re-run the
verification audit before doing so.

## §X v2 Redesign Rationale (2026-05-03)

**Operator-approved 2026-05-03. Authority: `MATH_REALITY_OPTIMUM_ANALYSIS.md`.**

### What changed from v1

| Aspect | v1 | v2 |
|---|---|---|
| Trigger | `fire if cov < floor - σ` | Two-Rail: Rail 1 absolute kill `cov < 0.35`, Rail 2 `cov < floor` |
| σ role | In trigger formula | Diagnostic-only; NOT in trigger or floor selection |
| Curve | 5-segment linear table | Continuous `D = min(0.09, 0.20 × shortfall)` |
| Floor basis | p05 + σ-aware adjustment | p05 only: `max(p05_train_directional_cov, 0.35)` |
| Denver override | 0.85 (asymmetric loss) | REMOVED — algorithm output stands (p05 ≈ 0.879) |
| Lagos override | 0.45 PHYSICAL_OVERRIDE | Retained (Ruling B — documented vendor archive gaps) |
| Small-sample amp | `1 + k/sqrt(N)`, k=0 | Same structure; 1.25× amplifier when `N < N_star` |

### Why σ was removed from the trigger (structural bug)

The σ-band formula `fire if cov < floor - σ` has an inverted incentive:
higher σ (worse infrastructure, more outages) forces the algorithm to LOWER
the floor to maintain the FP constraint. A city with bad infrastructure
therefore gets LESS DDD protection. Denver's H1 result (4 zero-cov training
days → σ 0.064 → 0.158 → algorithm recommends floor 0.35) is the empirical proof.

**σ is kept as out-of-band diagnostic**. It is logged and monitored to detect
regime shifts (vendor flapping, infrastructure degradation), but it never enters
the critical sizing path.

### Why Denver override was removed (Ruling A)

Denver's 0.85 floor was a policy override for asymmetric loss preference
(operators prefer conservative sizing on Denver LOW due to convective risk).
This is a correct preference but the wrong mechanism. A floor override lies
about the physical baseline. The right place for sizing preferences is the
Kelly multiplier layer. See `docs/reference/zeus_kelly_asymmetric_loss_handoff.md`.

### Why the 5-segment curve was replaced

Phase 1 v2 bootstrap CIs (95% confidence intervals grouped by `decision_group_id`)
showed that adjacent shortfall bins [0.10–0.20), [0.20–0.30), and [0.30–0.50)
have statistically indistinguishable mean error rates. The only significant
discontinuity is the tail bin [0.50+]. A 5-segment curve with distinct
breakpoints at 0.10, 0.25, 0.40 implies empirical precision the data does not
support. A single-parameter linear formula is more defensible.

### What is deferred

- **Comprehensive backtest validation of α=0.20**: operator will run separately.
- **Kelly layer implementation**: hand-off at `docs/reference/zeus_kelly_asymmetric_loss_handoff.md`.
- **Live wiring into `src/engine/evaluator.py`**: separate workstream.
- **Paris floor**: pending workstream A DB resync.

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
