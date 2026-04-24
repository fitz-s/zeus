# Zeus Market & Settlement Reference

Durable reference for Polymarket weather market structure, bin topology,
settlement semantics, sensor physics, and probability generation.

Authority: executable source, tests, machine manifests, and authority docs win
on disagreement with this document.

---

## 1. Market Structure

### 1.1 Event → market → bin hierarchy

Polymarket weather markets follow a three-level hierarchy:

```
Event (slug: "highest-temperature-in-new-york-on-april-25-2026")
├── Market 1 (YES/NO contract on "48-49°F")
│   ├── YES token (clobTokenIds[0])
│   └── NO token (clobTokenIds[1])
├── Market 2 (YES/NO contract on "50-51°F")
│   ├── YES token
│   └── NO token
├── ...
└── Market N (YES/NO contract on "58°F or higher")
    ├── YES token
    └── NO token
```

Each market is a binary YES/NO contract. The event groups all bins
for one city-date. Zeus discovers events via the Gamma API
(`gamma-api.polymarket.com`) using tag slugs `["temperature", "weather",
"daily-temperature"]` with keyword fallback on `"temperature"`.

### 1.2 Token ID mapping

`clobTokenIds` is a JSON array `[token_0, token_1]` paired with `outcomes`
`["Yes", "No"]`. The market scanner validates this mapping:

```python
if label_0 == "no" and label_1 == "yes":
    # Tokens reversed — swap
    yes_token, no_token = no_token, yes_token
elif label_0 != "yes" or label_1 != "no":
    # Unknown labels — skip market
    continue
```

This swap guard prevents a systematic position-direction error that would
cause the system to buy YES tokens when intending to buy NO.

### 1.3 Market price

VWMP (Volume-Weighted Micro-Price) is the canonical market price:

```python
vwmp = (best_bid × ask_size + best_ask × bid_size) / (bid_size + ask_size)
```

If `total_size = 0` → `ValueError` (illiquid market, fail-closed).
Mid-price is forbidden for edge calculations. VWMP captures order book
imbalance: when ask_size >> bid_size, price is pulled toward bid (buy
pressure); when bid_size >> ask_size, price is pulled toward ask.

### 1.4 Market vig

The YES-side prices across all bins in an event sum to approximately 1.0,
but typically exceed it by 1-10% (the vig). Before computing posterior,
`VigTreatment.from_raw()` normalizes complete market vectors:

- Complete vector (≥2 positive components, sum in [0.90, 1.10]) → divide
  by sum to remove vig
- Sparse vector (monitor path, single held bin) → use raw prices as-is

### 1.5 City matching

`_match_city()` uses boundary-aware regex matching against city aliases
and slug names from `cities.json`. Short aliases ("LA", "SF") use word-
boundary patterns to prevent false matches inside longer city names.

Additional sanity: `_market_city_sanity_rejection()` rejects events where
the matched city conflicts with another configured city's tokens appearing
in the event's text fields.

### 1.6 Temperature metric inference

`infer_temperature_metric()` scans event text for LOW_METRIC_KEYWORDS:
`{"lowest temperature", "low temperature", "daily low", "overnight low", ...}`.
Default is `"high"`. This determines which ENS extremum is used (daily max
vs daily min) and which Platt model is loaded.

**Key file**: `src/data/market_scanner.py`

---

## 2. Bin Topology

### 2.1 Three bin types

```python
class Bin:
    low: float | None    # None = open lower shoulder
    high: float | None   # None = open upper shoulder
    label: str
    unit: str
```

| Type | Example | Cardinality | `width` property |
|------|---------|-------------|-----------------|
| `point` | "10°C" → `Bin(10, 10, ...)` | 1 integer | `1` |
| `finite_range` | "50-51°F" → `Bin(50, 51, ...)` | 2 integers | `2` |
| `open_shoulder` | "58°F or higher" → `Bin(58, None, ...)` | unbounded | `None` |

### 2.2 Bin membership test

`bin_probability_from_values(measured, bin)` counts what fraction of
measured values fall in the bin:

```
For finite bins:  low <= value <= high  (inclusive both sides)
For lower shoulder (low=None): value <= high
For upper shoulder (high=None): value >= low
```

Settlement is to integers, so for a bin "50-51°F", the settling values
are {50, 51} — exactly 2 integers. For a point bin "10°C", the settling
value is {10} — exactly 1 integer.

### 2.3 Width-normalized calibration

For Platt calibration, finite bins are normalized by width to get
per-degree density:

```python
p_input = p_raw / bin_width  # 2°F range bin: 0.20 → 0.10 density
```

Shoulder bins remain in raw probability space (they have no finite width).
This prevents Platt from seeing different probability magnitudes for the
same actual density across bins of different widths.

### 2.4 Bin parsing

`_parse_temp_range()` extracts `(low, high)` from market question text:
- `"X-Y°F"` → `(X, Y)` — finite range
- `"X°F or below"` → `(None, X)` — lower shoulder
- `"X°F or higher"` → `(X, None)` — upper shoulder
- `"X°C"` → `(X, X)` — point bin

---

## 3. Settlement Semantics

### 3.1 The settlement chain

The physical chain from atmosphere to settlement:

```
Real temperature → NWP ensemble member forecast → ASOS sensor reading
→ METAR report → Weather Underground display → Polymarket settlement integer
```

This chain means settlement is not a continuous value — it is a discrete
integer that has passed through sensor noise, rounding, and display truncation.

### 3.2 `SettlementSemantics` class

Every market has a typed settlement object with five fields:

```python
@dataclass(frozen=True)
class SettlementSemantics:
    resolution_source: str       # e.g., "WU_KJFK"
    measurement_unit: "F" | "C"
    precision: float             # 1.0 = whole degrees (all current markets)
    rounding_rule: RoundingRule  # "wmo_half_up" | "floor" | "ceil" | "oracle_truncate"
    finalization_time: str       # "12:00:00Z"
```

### 3.3 Rounding rules (from code)

**WMO half-up** (`wmo_half_up`): `floor(x + 0.5)`
- Used by: 49 of 51 cities (all WU ICAO + CWA + NOAA cities)
- This is NOT Python `round()` (banker's rounding) and NOT half-away-from-zero
- Example: 74.5 → 75, -0.5 → 0, -1.5 → -1

**Oracle truncate** (`oracle_truncate`): `floor(x)`
- Used by: Hong Kong (HKO) only
- HKO reports 0.1°C precision. UMA voters apply truncation bias:
  "28.7 hasn't reached 29, so it's 28"
- Empirically verified: `floor()` achieves 14/14 (100%) match on HKO
  same-source settlement days vs 5/14 (36%) with `wmo_half_up`

### 3.4 Source routing (`for_city()`)

The single entry point for constructing settlement semantics:

```python
SettlementSemantics.for_city(city):
    if source_type == "wu_icao":
        if unit == "C": → default_wu_celsius(wu_station)
        else:           → default_wu_fahrenheit(wu_station)
    elif source_type == "hko":
        → oracle_truncate, precision=1.0, "HKO_HQ"
    else:  # CWA, NOAA, etc.
        → wmo_half_up, precision=1.0
```

### 3.5 `assert_settlement_value()` — the DB write gate

Every settlement value DB write must pass through this gate:

```python
def assert_settlement_value(self, value, *, context=""):
    if not np.isfinite(value):
        raise SettlementPrecisionError(...)
    rounded = self.round_single(value)
    return rounded
```

This is the mandatory boundary between continuous atmospheric data and
discrete settlement truth. No code path may store a `settlement_value`
without calling this first.

**Key file**: `src/contracts/settlement_semantics.py`

---

## 4. Sensor Physics & Monte Carlo

### 4.1 ASOS sensor noise

ASOS/AWOS airport weather stations have documented precision ±0.5°F / ±0.28°C.
44 of 51 Zeus cities settle off ASOS-class stations. The 4 exceptions:

| City | Station | σ override | Reason |
|------|---------|-----------|--------|
| Hong Kong | HKO HQ | 0.10°C | Research-grade, tighter than ASOS |
| Taipei | CWA 46692 | 0.10°C | Professional station, tighter than ASOS |
| Istanbul | NOAA LTFM | default | ICAO standard, ASOS-equivalent |
| Moscow | NOAA UUWW | default | ICAO standard, ASOS-equivalent |

### 4.2 Monte Carlo P_raw generation

`p_raw_vector_from_maxes()` is the single code path for both live inference
and offline calibration rebuilds:

```python
for _ in range(n_mc):                           # 10,000 iterations
    noised = member_maxes + rng.normal(0, σ, N)  # Add sensor noise
    measured = settlement_semantics.round_values(noised)  # Apply rounding
    p += bin_counts_from_array(measured, bins)    # Count bin hits

p = p / (N_members × n_mc)    # Normalize
p = p / p.sum()                # Ensure sums to 1.0
```

This simulates the full physical chain: each ENS member temperature
is perturbed by sensor noise, rounded by settlement semantics, then
counted into bins. The result is a probability vector over bins that
reflects settlement physics, not just ensemble member counting.

Naive member counting (directly testing bin membership without MC) is
forbidden because it produces a distribution shape that diverges from
the MC-generated P_raw space, and Platt models trained on MC-generated
P_raw would not generalize.

### 4.3 `EnsembleSignal` class

```python
EnsembleSignal.__init__(members_hourly, times, city, target_date,
                        settlement_semantics, decision_time, temperature_metric):
    1. Reject if member count < ensemble_member_count() (51)
    2. Validate times length matches hourly columns
    3. Compute daily extrema per member via local-timezone day slice
       HIGH → member_maxes_for_target_date(...).max(axis=1)
       LOW  → member_maxes_for_target_date(...).min(axis=1)
    4. Apply ECMWF bias correction if settings flag enabled
    5. Simulate settlement: round_values(member_extrema)
```

The `temperature_metric` parameter must be a `MetricIdentity` instance —
bare strings are rejected with `TypeError`. This is the HIGH/LOW type
safety gate.

### 4.4 Bimodal detection

`is_bimodal()` detects regime splits (e.g., cold front timing uncertainty):
1. If ensemble range < σ_instrument → unimodal (all members agree)
2. Try KDE peak counting via `argrelextrema(density, np.greater, order=...)`
3. If ≥2 peaks → bimodal
4. KDE failure fallback → gap heuristic: `max(gaps) / range > gap_ratio`

### 4.5 Boundary sensitivity

`boundary_sensitivity(boundary)` = fraction of 51 members within ±σ of a
bin boundary. High sensitivity means the probability estimate is fragile —
a small temperature shift would significantly change which bin wins.

**Key files**: `src/signal/ensemble_signal.py`, `src/contracts/settlement_semantics.py`

---

## 5. Probability Chain

### 5.1 End-to-end flow

```
51 ENS members
  → daily max/min per member (local timezone day slice)
  → + ECMWF bias correction (if enabled)
  → Monte Carlo (N=10,000): member + N(0, σ²) sensor noise → settlement rounding
  → P_raw vector (per bin)
  → Extended Platt: P_cal = sigmoid(A·logit(P_raw) + B·lead_days + C)
  → calibrate_and_normalize() → P_cal vector sums to 1.0
  → α-weighted fusion: P_posterior = α·P_cal + (1-α)·P_market
  → edge = P_posterior - P_market
  → double-bootstrap CI (3σ layers: ensemble, instrument, Platt params)
  → Kelly sizing (if CI_lower > 0)
```

### 5.2 Extended Platt calibration

Three-parameter logistic model: `P_cal = sigmoid(A·logit(P_raw) + B·lead_days + C)`

- `lead_days` is a Platt input feature, not a bucket dimension (triples
  effective sample size: 45→135 per bucket)
- Maturity gate: `n ≥ 50` → C=1.0; `15 ≤ n < 50` → C=0.1 (regularized);
  `n < 15` → don't fit, use P_raw directly
- 200 bootstrap parameter sets `(A_i, B_i, C_i)` for σ_parameter in
  double-bootstrap CI
- Width-normalized input: finite bins are divided by width before logit

### 5.3 Alpha computation

`compute_alpha()` returns `AlphaDecision` with value clamped to [0.20, 0.85]:

```
Base α from calibration level:
  level 1 (n < 15):  base from settings
  level 2 (15-49):   base from settings
  level 3 (50-99):   base from settings
  level 4 (100+):    base from settings

Adjustments (additive):
  ENS spread < tight threshold:  +0.10
  ENS spread > wide threshold:   -0.15
  Model agreement SOFT_DISAGREE: -0.10
  Model agreement CONFLICT:      -0.20
  Lead days ≤ 1:                 +0.05
  Lead days ≥ 5:                 -0.05
  Hours since open < 12:         +0.10
  Hours since open < 6:          +0.05 (cumulative: +0.15)
```

Authority hard gate: `authority_verified=False` → `AuthorityViolation`
exception. No edge computation proceeds on UNVERIFIED calibration data.

Spread thresholds are defined in °F and auto-converted via
`TemperatureDelta.to()` for °C cities. This prevents the legacy bug where
a single numeric threshold was used for both units.

### 5.4 Tail alpha scaling

Shoulder bins (open-ended) get reduced alpha: `α_tail = α × 0.5` (clamped
at 0.20 minimum). This is based on empirical analysis: tail bins are 5.3×
harder for the model (Brier 0.67 vs 0.11). Scaling at 0.5 reduces overall
Brier by 0.042.

### 5.5 Double-bootstrap CI

`MarketAnalysis._bootstrap_bin()` captures three σ sources per iteration:

```python
for i in range(n_bootstrap):    # default: edge_n_bootstrap()
    # σ_ensemble: resample ENS members with replacement
    sample = rng.choice(members, size=N, replace=True)
    # σ_instrument: add sensor noise
    noised = sample + rng.normal(0, σ, N)
    # Apply settlement rounding
    measured = settle(noised)
    # Recompute P_raw for ALL bins (cross-bin correlation preservation)
    p_raw_all = [bin_probability(measured, bin) for bin in bins]
    # σ_parameter: sample from Platt bootstrap params
    A, B, C = random_choice(bootstrap_params)
    p_cal_boot = sigmoid(A·logit(p_raw) + B·lead_days + C) for each bin
    # Compute posterior and edge
    p_post = compute_posterior(p_cal_boot, p_market, α, bins)
    edges[i] = p_post[bin] - p_market[bin]

ci_lower = percentile(edges, 5)
ci_upper = percentile(edges, 95)
p_value = mean(edges <= 0)    # exact, not approximated
```

A positive edge is tradeable only if `ci_lower > 0`.

**Key files**: `src/strategy/market_analysis.py`, `src/calibration/platt.py`,
`src/strategy/market_fusion.py`

---

## 6. Settlement Mismatch Triage

When Zeus and Polymarket settlement disagree, triage in order:

1. Wrong station or wrong provider
2. Source/provider drift (station changed or API updated)
3. Bad or partial observation data from source
4. Date-mapping / local-day timezone bug
5. Rounding/semantic bug inside Zeus

Authority distinction: settlement bin identity comes from Polymarket (market
authority). Temperature values come from the weather station (observation
authority). These are separate truth surfaces.

For present-tense city/provider validity: `docs/operations/current_source_validity.md`

---

## 7. Cross-References

- Domain model: `docs/reference/zeus_domain_model.md`
- Math spec: `docs/reference/zeus_math_spec.md`
- Architecture law: `docs/authority/zeus_current_architecture.md`
- Execution/lifecycle: `docs/reference/zeus_execution_lifecycle_reference.md`
- Source AGENTS:
  - `src/signal/AGENTS.md` — signal domain rules
  - `src/calibration/AGENTS.md` — calibration domain rules
  - `src/contracts/AGENTS.md` — contract/semantics domain rules
  - `src/data/AGENTS.md` — data ingest domain rules
