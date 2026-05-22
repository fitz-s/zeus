# Zeus Math & Statistics Specification

**Purpose**: A reference document that explains Zeus's mathematical and statistical machinery. It is used to understand and review executable math law, not to override root authority, `architecture/**`, or tests.

**Authority status**: High-confidence factual math specification, but not top-level authority. Top-level authority is `AGENTS.md`, `architecture/**`, `docs/authority/**`, executable contracts, and blocking tests. If this file disagrees with those surfaces, treat it as a governance inconsistency: do not silently discard the spec, and do not override executable law without a packet.

**Version 2** (2026-04-13): corrections incorporated per user review of v1 — logit clipping explicit, open-boundary bins allowed, Monte Carlo pseudocode deduplicated across bins, stream-of-consciousness removed, `decision_group` concept added as independent sample unit.

---

## 0. Scope

This spec covers every math/stat operation that touches a price, probability, or settlement value:

1. Unit handling and rounding
2. Forecast data (X side: TIGGE ECMWF ensemble)
3. Settlement data (Y side: WU / HKO / NOAA; CWA is legacy-retroactive-quarantine only, see lore CWA_STATION_IS_LEGACY_QUARANTINE_PATH_NOT_DEAD_CODE)
4. Monte Carlo simulation of the WU reporting chain (P_raw)
5. Bins and market structure (including open-boundary bins)
6. Extended Platt calibration (P_cal) with numerical safety
7. Market fusion (P_posterior)
8. Edge detection with double-bootstrap CI (block-resampled by decision_group)
9. Benjamini-Hochberg FDR filter
10. Fractional Kelly sizing
11. Settlement outcome mapping
12. Decision group + training pair construction
13. Verification checklist
14. Known defects (blocking implementation)
15. Deferred upgrades (future work, not in scope)

Out of scope: execution (order placement), lifecycle (position states), risk manager internals, infrastructure.

---

## 1. Units and rounding — the atomic rule

### 1.1 Settlement unit per city

Each city has a single settlement unit ∈ {F, C} fixed in `config/cities.json` via `City.settlement_unit`. The settlement unit is contract-fixed (determined by the Polymarket market's `market_slug`), not derivable from geography.

### 1.2 WMO asymmetric half-up rounding (THE CORE RULE)

WU and NWS report whole degrees using the **WMO asymmetric half-up** rule. This is the single rounding rule every part of Zeus must use for settlement-aligned values.

**Canonical formula**:

```
round_wmo(x) = floor(x + 0.5)
```

**Verification table**:

| x | x + 0.5 | floor(x + 0.5) | WMO expected | Python `round(x)` (banker's) | Away-from-zero | Match? |
|---|---|---|---|---|---|---|
| 52.45 | 52.95 | **52** | 52 | 52 | 52 | ✓ |
| 52.50 | 53.00 | **53** | 53 | 52 | 53 | ✓ |
| 74.49 | 74.99 | **74** | 74 | 74 | 74 | ✓ |
| 74.50 | 75.00 | **75** | 75 | 74 | 75 | ✓ |
| -0.50 | 0.00 | **0** | 0 | 0 | -1 | ✓ |
| -1.49 | -0.99 | **-1** | -1 | -1 | -1 | ✓ |
| -1.50 | -1.00 | **-1** | -1 | -2 | -2 | ✓ |
| -2.50 | -2.00 | **-2** | -2 | -2 | -3 | ✓ |
| -3.50 | -3.00 | **-3** | -3 | -4 | -4 | ✓ |

Only `floor(x + 0.5)` matches the WMO column for every row.

### 1.3 Forbidden implementations

The following produce incorrect results for WU-aligned values. Each is forbidden on any settlement-aligned path.

- **Python built-in `round()`** — banker's (round-half-to-even). `round(74.5) == 74` (wrong; WMO = 75). `round(-1.5) == -2` (wrong; WMO = -1). Forbidden.
- **NumPy `np.round()` / `np.around()`** — same banker's rounding as Python built-in. `np.round(74.5) == 74.0`. Forbidden.
- **Python `int(x + 0.5)`** — truncates toward zero. For `x = -1.6`, computes `int(-1.1) = -1`, while WMO requires `floor(-1.1) = -2`. Forbidden on all negative inputs where fractional part exceeds 0.5.
- **`math.ceil(x - 0.5)`** — produces "round half down" (toward -∞ at half-values). `ceil(74.0) = 74` (wrong; WMO = 75 for x=74.5). Forbidden.
- **`Decimal.quantize(ROUND_HALF_EVEN)`** — banker's. Forbidden.
- **`Decimal.quantize(ROUND_HALF_UP)`** — despite the name, this is "round half away from zero", not "round half toward +∞". Forbidden on negatives (gives -2 for -1.5).
- **`Decimal.quantize(ROUND_CEILING)`** — ceiling of the raw value, not half-up. Forbidden.
- **`Decimal.quantize(ROUND_FLOOR)`** — floor of the raw value, not half-up. Forbidden.

### 1.4 Correct implementations

- Python: `math.floor(x + 0.5)`
- NumPy (vectorized): `np.floor(x + 0.5)`

These are the only two permitted implementations.

### 1.5 Unit conversion

- F → C: `(f - 32) * 5/9`
- C → F: `c * 9/5 + 32`
- K → C: `k - 273.15`
- K → F: `(k - 273.15) * 9/5 + 32`

**Conversion must precede rounding.** Round only in the final settlement unit.

Example: raw temperature 24.44°C for a Fahrenheit city:
- Correct: C→F first (75.992°F), then `round_wmo` → 76°F
- Wrong: `round_wmo` in C (24°C), then C→F → 75.2°F → 75°F (off by 1)

---

## 2. Forecast data (X side) — ECMWF 51-member ensemble

### 2.1 Data source

- **Canonical**: ECMWF TIGGE GRIB 51-member ensemble for 2-meter temperature (GRIB param 167.128). 1 control member + 50 perturbed.
- **Temporary fallback (live path only)**: Open-Meteo API's ECMWF ensemble endpoint. Same 51-member structure; used only while TIGGE GRIB download catches up.
- **Rebuild/training-data path**: TIGGE GRIB only. Open-Meteo is not used for training data ingestion.

### 2.2 Lead times

```
lead_hours ∈ {24, 48, 72, 96, 120, 144, 168}
lead_days  = lead_hours / 24 ∈ {1, 2, 3, 4, 5, 6, 7}
```

Lead is measured from forecast `issue_time` to market `target_date`. Extended Platt uses `lead_days` as a continuous feature (§6).

### 2.3 Aggregation: per-member daily max

```
For each member m ∈ 1..51, for each target_date:
    member_max[m] = max{ member_m(t) : t ∈ [target_date 00:00 local, target_date 23:59 local] }
```

Max is computed in the **city's local time**, because Polymarket "daily high" follows the local calendar day. This matters at DST boundaries.

### 2.4 Storage unit

`ensemble_snapshots.members_json` stores the 51-member list **in the city's settlement unit**. TIGGE native Kelvin is converted at ingest via §1.5.

### 2.5 Schema

One row per (city, target_date, issue_time, lead_hours):
- `members_json`: list of 51 floats in city settlement unit (daily max per member)
- `authority`: VERIFIED iff row passed the blessed ingestion path
- `provenance_metadata`: GRIB file(s), extractor version, run_id
- `source_model_version`: string identifying the TIGGE cycle + model version (used in decision_group; §12.1)

---

## 3. Settlement data (Y side) — WU / HKO

### 3.1 Source

- **45 cities**: Weather Underground historical daily highs (backfill: WU ICAO historical API; live: WU timeseries API).
- **Hong Kong**: Hong Kong Observatory (HKO).
- No other source is permitted as settlement truth.

### 3.2 Value

- Integer in city settlement unit
- Produced by WU/HKO applying WMO half-up (§1.2) to raw station records
- Zeus stores the integer directly; no further rounding on ingest
- `SettlementSemantics.assert_settlement_value(raw, city)` is the mandatory gate for every DB write
- `assert_settlement_value` must implement WMO half-up, not banker's

### 3.3 Schema

`settlements (city, target_date, settlement_value, settlement_source, authority)`:
- `settlement_value`: integer in city settlement unit
- `settlement_source`: provenance string
- `authority`: VERIFIED iff derived from VERIFIED observation via `rebuild_settlements`

---

## 4. Monte Carlo simulation — the P_raw generator

### 4.1 Why Monte Carlo (not member counting)

A naive approach `P_raw[bin] = count(m_i ∈ bin) / 51` is wrong at bin boundaries: it ignores the downstream chain

```
atmosphere → NWP member → ASOS sensor noise → METAR rounding → WU integer display
```

Near a bin edge, a member predicting 74.3°F when the boundary is 74/75 has non-zero probability of being reported as 75 after sensor noise and rounding. Member counting misses this tail.

### 4.2 The simulation (single-histogram formulation)

**Per (city, target_date, issue_time, lead_hours)**, run the Monte Carlo **once** to produce an integer histogram. Bin probabilities are read from this histogram by summation — do NOT re-run Monte Carlo per bin.

```
Load members m = [m_1, ..., m_51]  in city settlement unit
sigma_instrument = {"F": 0.5, "C": 0.3}[city.settlement_unit]   // §4.3
n_samples = 10000                                                // §4.4
N_total = 51 * n_samples                                          // 510,000 samples

integer_histogram = empty dict (int → count)
for m_i in m:
    for k in 1..n_samples:
        epsilon = N(0, sigma_instrument**2)
        simulated_reading = m_i + epsilon
        reported_integer = floor(simulated_reading + 0.5)        // WMO half-up, §1.2
        integer_histogram[reported_integer] += 1

// integer_histogram now represents the full predictive distribution over integer readings
// (one histogram per snapshot, reused for every bin below)
```

### 4.3 Parameter σ_instrument

- ASOS sensor measurement noise
- 0.5°F or 0.3°C per AGENTS.md §1 and
  `docs/reference/legacy/legacy_reference_statistical_methodology.md`
- Per-unit value at `src/signal/ensemble_signal.py::sigma_instrument`

### 4.4 Parameter n_samples

- 10,000 per `docs/reference/legacy/legacy_reference_statistical_methodology.md`
- Trades off variance (large n) vs compute cost

### 4.5 Non-negotiable property

The Monte Carlo's rounding function must match `SettlementSemantics.assert_settlement_value`. If they disagree at any real x, then at bin boundaries P_raw and Y live in different rounding conventions and Platt trains on systematically biased pairs.

---

## 5. Bins and market structure

### 5.1 Bin definition (including open-boundary outer bins)

A `Bin` is an integer interval in the city's settlement unit:

```
Bin(unit=U, low=L, high=H)   where L, H ∈ ℤ ∪ {-∞, +∞}  and  L ≤ H
```

The interval is **closed on both sides where finite**: `value ∈ Bin iff L ≤ value ≤ H`.

**Outer (unbounded) bins exist** for Polymarket's "X° or lower" and "Y° or higher" options. They are mandatory for every market's bin set to preserve §5.3 coverage.

Representation convention:

```
"X° or lower"   → Bin(low=-inf, high=X)       // X is the largest integer still covered
"Y° or higher"  → Bin(low=Y,    high=+inf)    // Y is the smallest integer still covered
```

Code representation: `float('-inf')` and `float('inf')`. Python's `v >= float('-inf')` and `v <= float('inf')` are always True, so `find_bin(v)` handles open edges with plain comparisons — no sentinel integers, no special-case branching.

DB representation: store `low` and `high` as real (float) columns. SQLite stores `-inf` and `+inf` natively via the IEEE 754 binary64 representation. `WHERE low <= v AND v <= high` works as expected at the SQL layer.

### 5.2 Directions

Each bin has two binary options:
- **buy_yes**: pays off iff `settlement_value ∈ Bin`
- **buy_no**: pays off iff `settlement_value ∉ Bin`

`src/types/market.py::Bin` carries a runtime `unit` field with a `__post_init__` validator that cross-checks the label and the unit. Unit errors are unconstructable at runtime.

### 5.3 Coverage invariant (exactly-one-bin-wins)

The full bin set of a market must cover ℤ:

```
∪_{b ∈ market.bins} [b.low, b.high] = ℤ
(where ℤ is the set of integers that `settlement_value` can take)
```

This guarantees every possible `settlement_value` falls in exactly one bin. Because outer bins have `-∞` / `+∞` edges (§5.1), coverage is automatic once the market's inner bins are contiguous.

**If a market's bin set does NOT cover ℤ** (e.g., Polymarket drops the outer bins for some reason, or there's a gap between inner bins), a settlement_value outside the covered range would produce `outcome_yes = 0` for every bin, violating "exactly one winning bin". This is a **market-contract deviation** that must be detected and treated as a data error (log + quarantine the settlement), not silently glossed over. `probability_group_integrity` checks this condition.

---

## 6. Extended Platt calibration

### 6.1 Model (with logit numerical safety)

For each (city, season) bucket, a logistic regression maps P_raw to the probability of `outcome = 1`:

```
P_cal = σ(A · logit_safe(P_raw) + B · lead_days + C)

where σ(x)      = 1 / (1 + exp(-x))
      logit(p)  = log(p / (1 - p))
```

**Numerical safety — explicit clipping**: Monte Carlo with 10,000 samples routinely produces `P_raw = 0` or `P_raw = 1` for extreme forecasts (e.g., "heatwave, all 510,000 simulated readings above 75°F"). Naively applying `logit(0)` yields `-inf`; `logit(1)` yields `+inf`; either propagates to `NaN` downstream and blows up the entire (city, season) bucket's training loss.

We clamp before logit:

```
def logit_safe(p, eps=1e-6):
    p_clamped = max(eps, min(1.0 - eps, p))
    return log(p_clamped / (1.0 - p_clamped))
```

`eps = 1e-6` is the default, configurable per bucket if needed. It's small enough not to distort meaningful probabilities but large enough to keep the log finite. Pairs with `P_raw` at exactly 0 or 1 are NOT dropped — they are clipped, contribute a large but finite logit, and remain informative.

**Three learnable parameters** per bucket: A, B, C.
- A: slope of the logit-logit linearization (ideal calibration: A=1)
- B: temporal skill decay coefficient (how fast forecast skill falls off with lead time)
- C: intercept / systematic bias

### 6.2 Bucket dimension: (city × season)

```
For northern hemisphere cities (lat ≥ 0):
    month ∈ {12, 1, 2}  → season = "DJF"
    month ∈ {3, 4, 5}   → season = "MAM"
    month ∈ {6, 7, 8}   → season = "JJA"
    month ∈ {9, 10, 11} → season = "SON"

For southern hemisphere cities (lat < 0), labels are FLIPPED so "JJA" always means local warm season:
    month ∈ {12, 1, 2}  → season = "JJA"   (SH summer)
    month ∈ {3, 4, 5}   → season = "SON"
    month ∈ {6, 7, 8}   → season = "DJF"   (SH winter)
    month ∈ {9, 10, 11} → season = "MAM"
```

Implementation: `src/calibration/manager.py::season_from_date(date, lat)`.

**Convention warning**: downstream code must not interpret `season='JJA'` as "June-August calendar". It means "local summer regardless of hemisphere". Wellington's January pair has `season='JJA'`.

### 6.3 Cross-validation and the flipped-season hazard

Cross-validation of Extended Platt parameters must be **time-blocked by decision_group** (§12.1), not row-randomized. Two reasons:

1. **Time correlation**: weather and forecast skill have strong auto-correlation at multi-day scales. Random shuffling a dataset with time structure yields artificially optimistic OOS estimates.
2. **Flipped-season leakage**: because the SH flip maps Wellington's January → `JJA`, a naive "leave one calendar month out" split would keep January in-sample when evaluating July, but those two months belong to opposing local seasons for SH cities. Equivalently, training on Wellington's 2024 JJA (which is January 2024) and evaluating on Wellington's 2024 DJF (which is July 2024) is perfectly valid, but doing random row shuffle mixes them in ways that leak future-calendar information.

**The correct pattern**:
- Split the `decision_group`s (§12.1) by chronological `forecast_available_at`, not by row index
- Forward split (train t < t_cut, validate t ≥ t_cut) or rolling origin
- Blocked bootstrap: resample **decision_groups** with replacement; keep all rows within a group together

The specific split strategy (forward vs rolling, block size) is implementation-level and lives outside this spec. The invariant is: **no random row shuffle on calibration_pairs for CV or bootstrap**.

### 6.4 Loss function

Binomial log-loss over pair rows in the bucket, with per-pair sample weight from decision_group weighting (§12.1 `w_g`):

```
L(A, B, C) = - Σ_pair w_pair · [ y · log(P_cal) + (1 - y) · log(1 - P_cal) ]
```

Where:
- `y = outcome ∈ {0, 1}` per pair
- `w_pair = 1 / (#bin_rows in pair's decision_group)` — so all decision_groups contribute equally regardless of how many bins their market has

### 6.5 Maturity gates

The number of **decision_groups** `n_eff` in a bucket determines Platt behavior:

| n_eff | Behavior | Regularization C_reg |
|---|---|---|
| n_eff < 15 | Skip Platt fit; use P_raw directly | n/a |
| 15 ≤ n_eff < 50 | Strong regularization | 0.1 |
| n_eff ≥ 50 | Standard fit | 1.0 |

**`n` here means `n_eff = #{decision_groups in bucket}`, NOT `#pair_rows`.** Using row count would overstate effective sample size by a factor of ~(bins per market), inflating false confidence in small buckets.

Note: `C_reg` (regularization strength) is distinct from the Platt intercept `C` in §6.1.

---

## 7. Market fusion

### 7.1 Model

```
P_posterior = α · P_cal + (1 - α) · P_market
```

Where `P_market` is the Polymarket CLOB mid-price for the (bin, direction), interpreted as market-implied probability of YES.

### 7.2 α weight

α ∈ [0, 1] is the model weight, computed per decision by `src/strategy/market_fusion.py::compute_alpha`. Inputs that reduce α:

- Low calibration maturity (few decision_groups in bucket)
- Long lead time (less forecast signal)
- High market liquidity (stronger market prior)
- Wide Platt parameter uncertainty

At the boundary α=0 the system trusts the market; at α=1 it trusts its own calibrated forecast. Typical live α ∈ [0.2, 0.7].

The exact formula lives at `src/strategy/market_fusion.py::compute_alpha`. This spec does not lock the formula but requires `compute_alpha` to be deterministic given its inputs and monotone in the expected directions.

---

## 8. Edge detection with double-bootstrap CI

### 8.1 Point estimate

```
Edge = P_posterior - P_market
```

Positive edge = our posterior exceeds market. This is the raw trading signal before FDR and sizing.

### 8.2 Double bootstrap CI (blocked by decision_group)

Edge has three independent uncertainty sources:
1. **σ_model**: ensemble spread + instrument noise
2. **σ_parameter**: Platt A/B/C posterior uncertainty
3. **σ_bootstrap**: finite-sample resampling

The double bootstrap draws `B = edge_n_bootstrap()` samples. Each sample:

1. Resamples the 51 ensemble members with replacement; redraws instrument noise realizations (σ_model)
2. Samples (A, B, C) from the Platt parameter bootstrap distribution (σ_parameter)
3. Recomputes P_raw → P_cal → P_posterior → Edge for that sample

**Resampling unit**: when the outer-loop bootstrap resamples rows from the calibration dataset (for σ_parameter), it must resample **decision_groups**, not individual bin rows. Resampling rows independently treats correlated bin rows as independent samples, which produces artificially tight CIs.

**CI**: `[5th percentile, 95th percentile]` of the bootstrap edge distribution = 90% CI.

**P-value**: fraction of bootstrap edges ≤ 0 (one-sided for H₀: Edge ≤ 0).

### 8.3 Caching

Bootstrap results are memoized per `(direction, bin_idx, n_bootstrap)` within a cycle. Performance optimization, not correctness.

---

## 9. Benjamini-Hochberg FDR filter

### 9.1 Family definition

Each cycle tests `H` hypotheses. The hypothesis family must cover **every tested (city, target_date, mode, bin, direction)** in the cycle, not just the ones that passed pre-filters. With ~16 active cities, ~5-10 bins per market, 2 directions per bin, and potentially multiple markets per (city, target_date), `H ≈ 200-250`.

### 9.2 Procedure

1. Collect p-values `{p_1, ..., p_H}` from §8.2 for every tested (city, target_date, bin, direction)
2. Sort ascending: `p_(1) ≤ p_(2) ≤ ... ≤ p_(H)`
3. Find largest `k` such that `p_(k) ≤ (k / H) · α` where α = 0.10
4. Accept as "FDR-significant edge" all hypotheses with rank ≤ k

---

## 10. Fractional Kelly sizing

### 10.1 Kelly base

For a binary bet with win probability `p` and decimal odds `b`:

```
f* = (p · (b + 1) - 1) / b
```

In Polymarket's probability-based structure, `b = (1 - P_market) / P_market`, so for buy_yes:

```
f* = (P_posterior - P_market) / (1 - P_market)
```

For buy_no, replace `P_posterior` with `1 - P_posterior` and `P_market` with `1 - P_market`.

### 10.2 Fractional Kelly with dynamic multiplier

```
size = f* · kelly_mult · bankroll
```

`kelly_mult ∈ [0.001, 1.0]` from `src/strategy/kelly.py::dynamic_kelly_mult`. Reduces (multiplicatively) based on:
- Edge CI width (wider → lower)
- Calibration maturity (fewer decision_groups → lower)
- Elevated risk state (GREEN → YELLOW → ORANGE → lower)

### 10.3 Floor and ceiling

- **Floor**: `kelly_mult ≥ 0.001`. Never zero, never NaN. NaN → 0.001. Per INV-05.
- **Ceiling**: `kelly_mult ≤ 1.0` (full Kelly cap).

---

## 11. Settlement outcome mapping

### 11.1 Per-bin outcome

For a market's bins `{b_1, ..., b_K}` and `settlement_value = v`:

```
For each bin b_i:
    outcome_yes[b_i] = 1 if v ∈ [b_i.low, b_i.high] else 0
    outcome_no[b_i]  = 1 - outcome_yes[b_i]
```

### 11.2 Invariant

Exactly one `outcome_yes[b_i]` is 1; the rest are 0. This is the "exactly one winning bin" invariant, enforced by §5.3 coverage. `probability_group_integrity` detects violations (e.g., `yes_count_not_one`).

### 11.3 Edge case: no bin matches

If `settlement_value = v` and every `outcome_yes = 0`, the market's bin set failed §5.3 coverage. This is a market-contract deviation (likely missing outer bins) and should be:
1. Logged as `probability_group_integrity` failure
2. Quarantined — do not store as a calibration pair
3. Escalated to human review for the market taxonomy

### 11.4 Complete-bin basket arbitrage (neg_risk_basket) — deterministic payoff identity

The `neg_risk_basket` strategy is **not** a predictive edge. It does not estimate any
probability `p`. Its profit is a settlement payoff *identity* that follows directly from
the §5.3 coverage invariant and the §11.2 exactly-one-winning-bin invariant. This makes it
the only strategy whose edge is provable pathwise (for every possible settlement, not in
expectation) — there is no model misspecification, no sample-size CI, no regime dependence.

**Premise.** A weather temperature market exposes an exhaustive, mutually exclusive bin set
`{B_1, ..., B_K}` (§5.3). For any realized settlement temperature `T`:

```
Σ_{i=1..K} 1{T ∈ B_i} = 1                         (exactly one bin wins — §11.2)
```

YES token `i` pays `Y_i(T) = 1{T ∈ B_i}`; NO token `i` pays `N_i(T) = 1 − 1{T ∈ B_i}`.

**Complete YES basket (payoff = 1).** Holding 1 share of YES on every bin:

```
Σ_{i=1..K} Y_i(T) = Σ 1{T ∈ B_i} = 1            ∀T   (constant payoff)
```

**Complete NO basket (payoff = K−1).** Holding 1 share of NO on every bin:

```
Σ_{i=1..K} N_i(T) = Σ (1 − 1{T ∈ B_i}) = K − 1   ∀T   (constant payoff)
```

Both payoffs are constants independent of `T`. The strategy is therefore an exact
arbitrage when the executable cost to acquire the basket is below its constant payoff.

### 11.5 Executable cost with order-book sweep and fees

Cost is the **order-book sweep cost**, not `top_ask × q`. For basket size `q` shares per leg,
leg `i`'s YES asks form discrete levels `(p_{i,ℓ}, Δq_{i,ℓ})`. Define the consumed-depth sweep
cost and per-level fee:

```
A_i(q) = Σ_ℓ p_{i,ℓ} · Δq_{i,ℓ}                  (sweep notional, levels consumed up to q)
F_i(q) = Σ_ℓ r · p_{i,ℓ} · (1 − p_{i,ℓ}) · Δq_{i,ℓ}   (per-level taker fee)
```

The Polymarket fee model is `fee = C · r · p · (1 − p)` where `C` = shares, `p` = share price,
`r` = taker fee rate (maker fee `r = 0`). The weather taker rate is `r = 0.05` per the
Polymarket fees documentation. **`r` MUST be sourced from live venue config / fee schedule,
never hardcoded** — code-provenance rule: a frozen `0.05` constant silently breaks if the
venue reprices. Symbols `B_i(q)`, `G_i(q)` denote the same sweep notional and fee for the NO leg.

### 11.6 Profit functions and the exact arbitrage condition

```
Π_Y(q) = q        − Σ_{i=1..K} [ A_i(q) + F_i(q) ]      (complete YES basket)
Π_N(q) = (K−1)·q  − Σ_{i=1..K} [ B_i(q) + G_i(q) ]      (complete NO basket)
```

**Theorem (pathwise positive PnL).** If there exists `q > 0` with `Π(q) > 0`, the basket is a
deterministic profit: `Π(T) = Π(q) > 0 ∀T`. This replaces the heuristic threshold currently
in code. The exact condition is:

```
EXECUTE  ⇔  max( Π_Y(q*), Π_N(q*) ) > 0
```

The **maker-only, single-share** special case collapses to the familiar identity-form gate:

```
Σ_i a_i < 1          (YES basket, r = 0)        — NOT the hardcoded 0.97
Σ_i b_i < K − 1      (NO basket,  r = 0)
```

The constant `0.97` in `src/strategy/candidates/neg_risk_basket.py` is an empirical
mis-statement of this condition (see §14.11). The correct gate is the fee-adjusted exact
inequality above, evaluated on swept depth — not a top-ask sum vs a hand-tuned constant.

### 11.7 Sizing by arbitrage optimization (NOT Kelly)

Sizing is not §10 fractional-Kelly. The basket has no probabilistic edge to fraction; its
size is the profit-maximizing depth:

```
q* = argmax_{q ∈ D} Π(q)        where D = the set of order-book depth breakpoints across all legs
```

`Π(q)` is piecewise-linear in `q` (slope changes only at level boundaries), so the optimum lies
at a breakpoint in `D`; enumerate breakpoints rather than solving continuously. In the registry,
`kelly_default_multiplier = 0.0` for `neg_risk_basket` denotes "sizing does not come from Kelly,"
not "size zero."

### 11.8 Vector-fill accounting identity (the basket is the asset)

Only a **complete vector** of legs is the arbitrage asset; a partial fill is not. If leg `i`
fills `f_i` shares, the completed-basket quantity and realized profit are:

```
q_complete = min_{i=1..K} f_i
Π_realized = payoff(q_complete) − Σ_i cost_i(q_complete)
```

Any fill beyond `q_complete` on a leg is an **execution residual**, not strategy alpha, and must
be flattened or merged — it carries directional (non-arbitrage) risk. Consequently a
`decision_events(outcome="enter")` row for `neg_risk_basket` is valid only when all `K` legs are
confirmed with `f_i ≥ q` for a common `q > 0`; otherwise the attempt is a failed-vector /
residual event, not a strategy trade. Order-placement mechanics (FOK/marketable-limit per leg,
`basket_execution_id` binding) are **execution semantics and out of scope here per §16** — this
section specifies only the accounting identity that defines the strategy asset.

### 11.9 Why this is application-grade where predictive strategies are not

The promotion machinery in §8/§10 and the EvidenceReport Beta-CI gate are built for *stochastic*
edges (`EV = p − a − fee`, where `p` is estimated and needs a sample-size CI to bound). The basket
edge is `Π = const − cost`: deterministic, so its evidence is the realized-vs-computed payoff
identity per completed basket, not a win-rate sample. If the existing promotion machinery must be
reused, write `regret_decompositions.total_regret_usd = Π_realized` per completed basket — but the
edge's existence does not require `N` settled samples to establish. Promotion semantics for a
deterministic strategy are governed by the strategy authority docs, not by this math spec.

---

## 12. Decision groups and training pair construction

### 12.1 Decision group: the independent sample unit

The **independent sample unit** for calibration is the decision group, NOT the individual (city, target_date, bin, direction) row. A decision group is:

```
g = (city, target_date, forecast_available_at, source_model_version)
```

All pair rows from a single ensemble snapshot at a single `issue_time` share the same underlying forecast and are NOT independent. The per-row observations differ only in which bin they test, not in the underlying physics.

**Consequences** — every statistical operation that assumes independence must use decision groups:

- **Maturity gates (§6.5)**: `n_eff = #{g}`, not `#rows`.
- **Bootstrap resampling (§8.2)**: resample decision_groups with replacement; within each resampled group, keep all bins together.
- **Cross-validation splits (§6.3)**: block by decision_group, split by time.
- **OOS aggregation**: compute Brier, log-loss, ECE, reliability over groups, not rows.

**Per-row sample weight**: when fitting Platt (§6.4), each pair row gets `w_pair = 1 / (#rows in its decision_group)`. This ensures a market with 10 bins and a market with 3 bins contribute equally per decision_group regardless of bin count.

**Persistence**: `calibration_pairs.decision_group_id` is the column that ties rows to groups. Every pair insert must populate this column with a stable group key (hash of `(city, target_date, forecast_available_at, source_model_version)`).

### 12.2 Training pair construction (efficient, MC per snapshot)

**CRITICAL efficiency point**: Monte Carlo runs **ONCE per (city, target_date, issue_time, lead_hours) snapshot**, producing the integer histogram from §4.2. Then every bin in the market reads its P_raw from this same histogram by summation — no per-bin Monte Carlo re-run. For a market with 10 bins, naive per-bin MC would waste 10× the compute; the efficient form runs MC once and sums the histogram 10 times.

```
For each (city, target_date) with a VERIFIED settlement_value:

    For each ensemble_snapshot(city=city, target_date=target_date):  // one per (issue_time, lead_hours)
        lead_days = (target_date - snapshot.issue_time.date()).days
        source_model_version = snapshot.source_model_version
        decision_group_id = hash(city, target_date, snapshot.issue_time, source_model_version)

        members = snapshot.members_json  // 51 values in city settlement unit

        // -------- Monte Carlo ONCE --------
        integer_histogram = run_monte_carlo(members, sigma_instrument, n_samples)  // §4.2
        N_total = 51 * n_samples

        // -------- Derive P_raw per bin from the histogram --------
        market_bins = get_bins_for_market(city, target_date)
        for b in market_bins:
            count_in_bin = sum(integer_histogram[i] for i in integers_in_bin(b))
            // integers_in_bin(b) handles open edges:
            //   b.low = -inf → iterate from min(histogram keys)
            //   b.high = +inf → iterate to max(histogram keys)
            p_raw_yes = count_in_bin / N_total

            outcome_yes = 1 if (b.low <= settlement_value <= b.high) else 0
            store_pair(
                decision_group_id = decision_group_id,
                city = city,
                target_date = target_date,
                range_label = b.label,
                p_raw = p_raw_yes,
                outcome = outcome_yes,
                lead_days = lead_days,
                season = season_from_date(target_date, city.lat),
                direction = 'yes',
                authority = 'VERIFIED'
            )
            store_pair(
                decision_group_id = decision_group_id,
                ...same as above but with...
                p_raw = 1.0 - p_raw_yes,
                outcome = 1 - outcome_yes,
                direction = 'no'
            )
```

Compute cost: `O(51 * n_samples + K_bins * average_bin_width)` per snapshot, vs naive `O(51 * n_samples * K_bins)`. For K=10 bins per market, this is a ~10× speedup.

### 12.3 Equivalence with live inference

The `run_monte_carlo` function used in §12.2 must be **identical** to the function `src/signal/ensemble_signal.py::p_raw_vector` uses at live decision time. Not "equivalent up to a simplification". Identical: same import, same σ_instrument lookup, same WMO rounding, same n_samples.

This invariant is currently violated in `scripts/rebuild_calibration.py`, whose own docstring says: "simplified local p_raw computation ... bin taxonomy may differ from live-trading bins ... TODO integrate full Bin/SettlementSemantics pipeline". The data-rebuild packet must fix this before running.

---

## 13. Verification checklist

A reviewer can verify code compliance with this spec by running these checks:

1. **Rounding**: grep `np.round`, `round(` in `src/contracts/settlement_semantics.py`, `src/signal/ensemble_signal.py`, `src/data/rebuild_validators.py`. Any hit is a candidate violation unless the path is provably non-settlement.
2. **`floor(x + 0.5)`**: grep `np.floor` in `src/contracts/settlement_semantics.py::round_values` and `src/signal/ensemble_signal.py::_simulate_settlement`. Both must use the `floor(x + 0.5)` pattern (or call a shared helper that does).
3. **Monte Carlo identity**: `_simulate_settlement` calls the same rounding function as `SettlementSemantics.round_values`. Ideally one function, not two.
4. **σ_instrument values**: `ensemble_instrument_noise('F')` ≈ 0.5, `('C')` ≈ 0.3.
5. **n_samples = 10,000**: at `src/signal/ensemble_signal.py::p_raw_vector`.
6. **Logit safety**: `src/calibration/platt.py` applies `clip(p, eps, 1-eps)` before `logit`. `eps` is documented and small (1e-6 or finer).
7. **Maturity gates use n_eff**: `src/calibration/manager.py::maturity_level` (or equivalent) computes `n_eff = #{decision_group_id}`, not `COUNT(*)` on calibration_pairs.
8. **decision_group_id populated**: every row in `calibration_pairs` has a non-null `decision_group_id`. SQL: `SELECT COUNT(*) FROM calibration_pairs WHERE decision_group_id IS NULL` must return 0.
9. **Bootstrap resamples decision_groups**: `src/strategy/market_analysis.py::_bootstrap_bin` resamples by group, not by row.
10. **FDR α = 0.10** and covers full tested family: `src/strategy/fdr_filter.py` receives every (city, bin, direction) tested in the cycle, not just those passing pre-filters.
11. **Kelly floor**: `dynamic_kelly_mult` has `min_mult = 0.001` (or equivalent) and NaN handling.
12. **Probability group integrity**: `wu_settlement_sweep` flags any `(city × season)` bucket with `p_sum_not_one`, `duplicate_labels`, `yes_count_not_one`.
13. **Extended Platt formula**: `src/calibration/platt.py::ExtendedPlattCalibrator.fit` minimizes weighted log-loss of `sigmoid(A·logit_safe(p_raw) + B·lead_days + C)` with sample weights from §12.1.
14. **Hemisphere flip**: `season_from_date(d, lat<0)` returns the flipped label.
15. **Outer bins represented as ±inf**: `Bin.low == float('-inf')` / `Bin.high == float('inf')` for the bottom/top bins of each market. No integer sentinels.
16. **Time-blocked CV**: any CV or OOS-evaluation code (in `tests/` or analysis scripts) splits chronologically by decision_group, never by random row shuffle.
17. **Coverage invariant**: for every market, `∪ bins = ℤ` (i.e., outer bins use `-inf`/`+inf`); any market failing this is flagged.

---

## 14. Known defects (blocking implementation)

Current code disagrees with this spec at the following points. The data-rebuild packet must fix all of them before running against production data.

### 14.1 `SettlementSemantics` uses WMO half-up rounding
- **Resolved in Packet 1**: `src/contracts/settlement_semantics.py` uses `rounding_rule="wmo_half_up"` for WU settlement semantics.
- The active formula is `np.floor(scaled + 0.5)`.
- `round_half_to_even` / `np.round` was the historical defect and must not be restored for settlement values.

### 14.2 `ensemble_signal._simulate_settlement` inherits WMO half-up
- Calls `SettlementSemantics.round_values`, which now uses WMO half-up for WU settlement semantics.
- Keep this dependency; do not hand-roll rounding in signal code.

### 14.3 `docs/reference/legacy/legacy_reference_statistical_methodology.md` had `np.round` (now patched)
- Lines 122/130/139 had `round(...)` and `np.round(...)`.
- Patched in this commit to `floor(... + 0.5)` with explicit WMO note.

### 14.4 AGENTS.md §1 stated banker's (now patched)
- Said "74.50°F rounds to 74°F (banker's rounding)".
- Patched in this commit to WMO asymmetric half-up with verification table.

### 14.5 `rebuild_calibration.py` uses simplified p_raw (category error vs §12.3)
- Script TODO: "simplified local p_raw computation ... bin taxonomy may differ".
- **Fix**: import `src/signal/ensemble_signal.py::p_raw_vector` and use live Bin taxonomy. Data-rebuild packet Change D.

### 14.6 `calibration_pairs.decision_group_id` population coverage unverified
- Column exists in schema per `src/state/db.py:285-300`.
- Not yet verified that every row-insertion path populates it (some legacy paths may write NULL).
- **Fix**: audit every `add_calibration_pair` call site; reject inserts with NULL `decision_group_id`.

### 14.7 Maturity gate may use row count instead of group count
- `n_eff` vs `#rows` distinction not confirmed in code.
- **Fix**: audit `src/calibration/manager.py::maturity_level` and `get_pairs_for_bucket` to confirm group-count is used.

### 14.8 Bootstrap in `market_analysis.py` may resample rows
- `src/strategy/market_analysis.py:185-244` `_bootstrap_bin` needs audit.
- **Fix**: confirm it resamples by decision_group; rewrite if not.

### 14.9 Logit clipping — deployed with eps = 0.01

- `src/calibration/platt.py` applies `clip(p, eps, 1 - eps)` before `logit` at line 170–181.
- Deployed value: **`eps = 0.01`** (constant `P_CLAMP_LOW`). This is an operator-approved
  deviation from the §13 theoretical default of `1e-6`.
- **Rationale**: lbfgs numerical stability at the ±13.8 logit range with sparse tail samples;
  cost asymmetry makes a full 91M-row refit to recover the theoretical `1e-6` unjustified.
  The deviation is annotated in `src/calibration/platt.py` and pinned by CI antibody test
  `tests/test_inv_eps_spec_conformance.py` (INV-eps-spec-conformance).
- **Status**: RESOLVED. §13 definition (`eps = 1e-6`) remains the spec default; §14.9
  documents the approved production deployment at `eps = 0.01`.

### 14.10 Outer-bin representation unconfirmed
- `Bin.low`/`high` type is float per current `src/types/market.py`; `-inf`/`+inf` allowed by Python but unverified across all Bin consumers.
- **Fix**: audit `find_bin`, `market_events` storage, and Bin construction for ±inf handling.

### 14.11 `neg_risk_basket` uses a heuristic threshold, not the exact arbitrage condition (§11.6)
- `src/strategy/candidates/neg_risk_basket.py:54` — `_BASKET_ARB_THRESHOLD = Decimal("0.97")`,
  gated at line 195 as `yes_ask_sum >= 0.97 → no_trade`. This is an empirical mis-statement of
  the §11.6 condition. Correct gate: fee-adjusted exact inequality `Π(q*) > 0` on swept depth
  (maker-only special case: `Σ a_i < 1`), not a top-ask sum vs a hand-tuned constant.
- The strategy only checks a scalar `neg_risk_yes_ask_sum`; it has no NO-basket scan (§11.6
  `Π_N`) and no order-book sweep (§11.5) — it cannot detect the larger of the two arbitrages
  nor size by §11.7 `q*`.
- The required inputs are **unwired**: `neg_risk_family_complete`, `neg_risk_token_count`,
  `neg_risk_yes_ask_sum` are documented "not yet wired in MarketAnalysisVNext" (file docstring
  lines 20–23). `MarketAnalysisVNext` is a single-market snapshot and lacks a family-level
  executable book (`A_i(q)`, `B_i(q)` level data per leg).
- The enter-path `CandidateDecision` (lines 230–237) is predictive-shaped (`side`,
  `target_price`, `edge`, `p_posterior`) and cannot express a multi-leg deterministic basket
  (`legs`, `deterministic_profit_usd`, `basket_execution_id` per §11.8).
- **Fix**: replace the threshold with the §11.6 exact condition; add a `FamilyOrderBookSnapshot`
  + family book fields to `MarketAnalysisVNext`; scan both `Π_Y` and `Π_N` over depth breakpoints
  (§11.7); emit a basket-shaped decision (§11.8). This converts the strategy from a permanent-
  no_trade shadow stub into the application-grade arbitrage of §11.4–11.9.

---

## 15. Deferred upgrades (future work, not in current scope)

These concepts come from the prior `02_mathematics_and_statistics_upgrade.md` document and are **not part of current Zeus math**. They are documented here so the current spec cannot be confused with a future upgraded one, and so reviewers know what is intentionally not yet implemented.

### 15.1 Empirical-Bayes partial pooling
Replace hard fallback (city → cluster → global → uncalibrated) with shrinkage:
```
θ_shrunk = λ · θ_local + (1 - λ) · θ_parent
λ = n_eff / (n_eff + τ)
```
Advantages: smooth transition between buckets, stable with sparse data. Deferred.

### 15.2 EMOS-style distributional correction
Replace mean bias correction with `Y | μ_ens, s_ens ~ N(a + b·μ_ens, c + d·s_ens²)`, then apply WMO rounding to produce bin probabilities. Learns spread-error relationship in addition to mean bias. Deferred.

### 15.3 Full tested-family FDR
Current §9 treats `H ≈ 220` as the family size. Upgrade: record every tested `(cycle, city, target_date, mode, bin, direction)` in a hypothesis ledger and apply BH to the ledger, not just the pre-filtered edges. Deferred.

### 15.4 Correlation matrix via shrinkage
Layer A: settlement anomaly correlation `a_{c,d} = T_{c,d} - E[T_{c,month(d)}]`.
Layer B: forecast error correlation `e_{c,d,ℓ,s} = T̂_{c,d,ℓ,s} - T_{c,d}`.

**Ledoit-Wolf optimal shrinkage intensity over diagonal target D** (Ledoit & Wolf 2003; see also Ledoit & Wolf 2004, "Honey, I shrunk the sample covariance matrix"):

```
Σ_shrunk = (1 - δ*) · S + δ* · D
```

where `D` is the diagonal matrix formed from the diagonal entries of the sample covariance `S` (i.e., retain variances, zero all off-diagonal covariances), and the optimal intensity is:

```
δ* = π / (γ × n)
```

- `π` = sum of asymptotic variances of the sample covariance entries (estimable from the data itself without additional assumptions)
- `γ` = squared Frobenius distance between the sample covariance matrix `S` and the diagonal target `D` (measures how far the sample matrix is from diagonal)
- `n` = number of observations

`δ*` is the analytically optimal convex-combination weight that minimises the expected mean-squared error of the estimator over the class `(1-δ)·S + δ·D, δ ∈ [0,1]`. Implementation MUST clip `δ*` to `[0, 1]` before applying — raw `π / (γ × n)` can exceed 1 on degenerate inputs (e.g. near-diagonal S), which would violate the convex-combination property and produce a non-PSD estimate.

**Verification cohort (back-test design, planned Phase 5 test harness)**: synthetic AR(1) temperature-residual sequences of increasing length `n ∈ {50, 100, 250, 500, 1000}` across `p = 20` city series with known underlying diagonal-dominant true covariance. The fitted `δ*` must converge toward 0 as `n → ∞` (sample covariance becomes reliable) and remain bounded away from 0 for `n < p`. Planned test name: `tests/test_correlation_shrinkage.py::test_intensity_converges_diagonal_target` (does not exist yet; ships with Phase 5 shrinkage estimator).

### 15.5 Day0 two-stage residual model
When a position is in day0 window, the observed running max `R` is a hard floor:
```
final_high ≥ R  (hard physical constraint)
Y = max(0, S - R)  where S is final settlement high
```
Two stages: P(Y > 0 | x), E[Y | Y > 0, x]. Preserves the physics constraint that a day's high cannot decrease. Deferred. **But the hard floor invariant itself is still enforced in current day0 code** — only the upgrade to two-stage residual modeling is deferred.

### 15.6 Execution microstructure (edge half-life)
Use the existing `token_price_log` to model whether an edge persists 5/15/30 minutes into the future. Deferred.

---

## 16. What this spec does NOT specify

- Execution semantics (order placement, maker/taker, fill logic)
- Lifecycle state machine (9 states, transitions)
- Risk manager internals (beyond INV-05 Kelly floor)
- Backfill mechanics (covered by data-rebuild plan)
- Test harnesses (live in `tests/`, reference spec by section number)

---

## 17. If something here is wrong

If any formula, parameter, or rule in this spec appears to disagree with executable law, do not treat the doc alone as authority. Verify against `AGENTS.md`, `architecture/**`, `docs/authority/**`, contracts, tests, and the relevant packet before changing code or docs.

**Version history**:
- **v1 (2026-04-13 earlier)**: initial draft based on Zeus ground-truth investigation + user's WMO rounding correction.
- **v2 (2026-04-13 now)**: corrections per user review of v1 — logit clipping explicit, open-boundary bins allowed (`-inf`/`+inf`), Monte Carlo pseudocode deduplicated across bins (histogram once per snapshot), stream-of-consciousness removed from §1.3, `decision_group` concept added as §12.1 with cascading updates to §6.3 (CV), §6.4 (loss weights), §6.5 (maturity gates with n_eff), §8.2 (bootstrap by group), §13 (verification items 7-9, 16), §14 (new known defects 14.6-14.10).
- **v3 (2026-05-22)**: added §11.4–11.9 complete-bin basket arbitrage (`neg_risk_basket`) as a deterministic payoff identity derived from §5.3/§11.2 — YES basket payoff = 1, NO basket payoff = K−1, fee-adjusted sweep-cost profit functions `Π_Y(q)/Π_N(q)`, exact arbitrage condition replacing the heuristic `0.97`, sizing by `q* = argmax Π(q)` (not Kelly), and the `q_complete = min_i f_i` vector-fill accounting identity. Added known defect §14.11 (current code uses heuristic threshold, unwired family-book inputs, predictive-shaped decision). Authority basis for the strategy reframe: operator directive 2026-05-22 + Polymarket CTF/negRisk/fees/order-lifecycle docs.
