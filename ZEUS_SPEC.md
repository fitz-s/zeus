# Zeus: Polymarket Weather Trading System

**Date:** 2026-03-30 (revised 2026-03-31)
**Status:** SPEC v2 — Position-Centric Architecture
**Predecessor:** Rainstorm (retired — operational wisdom and data inherited, signal code discarded)

---

**Design Authority:**

| # | Document | Domain | Precedence |
|---|----------|--------|-----------|
| **6** | **`zeus_blueprint_v2.md`** | **Architecture: Position lifecycle, CycleRunner, Decision Chain, Truth Hierarchy, Exit = Entry rigor** | **HIGHEST — architectural authority. Supersedes all prior architecture sections.** |
| 5 | `zeus_first_principles_rethink.md` | Why position management > signal precision. The reasoning behind Document 6. | High — design philosophy |
| 1 | `rainstorm_quantitative_research.md` | Calibration math, Kelly, sample sizes, overfitting | Domain reference |
| 2 | `rainstorm_architecture_blueprint.md` | **SUPERSEDED by Document 6.** Historical reference only — its signal-centric data flow (`observe → analyze → decide → execute`) is the root cause of both Rainstorm's death and Zeus v1's 10 P0 bugs. | Historical |
| 3 | `rainstorm_market_microstructure.md` | Edge thesis, participant types, entry timing | Domain reference |
| 4 | `rainstorm_statistical_methodology.md` | Three σ, instrument noise, FDR, data versioning | Domain reference |

**If code contradicts Document 6, STOP and restructure. Do not patch.**

---

## 0. What This System Is

Zeus is a **position management system** for Polymarket weather prediction markets. Its primary job is not finding edges — every bot finds edges with the same public data. Its primary job is **preserving position identity from discovery through settlement without losing information at module boundaries.**

Two independent code reviews (readiness scores 2/10 and 4/10) confirmed: Zeus's signal math is mature and correct. 100% of its catastrophic bugs were in position lifecycle. This is exactly how its predecessor Rainstorm died — signal-centric architecture that treated position management as a simple afterthought.

**The guiding principle:** A trading system's correctness is determined not by how well it finds edges, but by how completely it preserves position identity from discovery through settlement.

### 0.1 The Edge Thesis

Three structural edges, ranked by durability:

**Edge 1: Favorite-Longshot Bias (most durable)**

Retail participants systematically overpay for low-probability outcomes (shoulder bins at $0.03-0.08 when fair value is $0.01-0.03) and underpay for high-probability outcomes (center bins). This is a cognitive bias (prospect theory) that persists as long as retail participants exist. Academic literature and Polymarket data confirm: successful traders (gopfan2, $2M+ profit) exploit exactly this pattern.

The flip side is equally important: **center bins near the model consensus are systematically underpriced** because retail attention concentrates on the "exciting" tails. Buying underpriced center-bin YES at $0.05-0.20 has favorable risk-reward: small loss if wrong, large payout if right.

**Edge 2: Opening Price Inertia (structural, durable)**

Markets open 2-5 days before settlement. The first liquidity provider sets prices with high uncertainty and low information. These opening prices are sticky — subsequent participants anchor on them rather than repricing from scratch. The 6-24 hour window after opening has the largest gap between market price and model fair value, and the least bot competition (bots activate on model update cycles, not market opening cycles).

**Edge 3: Bin Boundary Discretization (unique, durable)**

Polymarket settles on Weather Underground's integer-rounded °F value. Most participants (including bots) model temperature as a continuous distribution and compute bin probabilities via integration. But the settlement value is discrete (integer after rounding from °C METAR → °F). At bin boundaries (forecast = 53.4°F, boundary at 53/54), the rounding step creates probability discontinuities that continuous models miss. This edge requires understanding the full settlement chain:

```
Atmosphere → NWP model → ASOS sensor → METAR (0.1°C) → WU display (integer °F) → Settlement
```

**What is NOT an edge:**
- Better weather forecasting (all bots use the same Open-Meteo/ECMWF data)
- Faster execution (we are not an HFT team)
- Better calibration (requires hundreds of samples per bucket — a future advantage, not a current one)

### 0.2 The Market

Polymarket weather markets: 11-bin multi-outcome markets on daily high temperature for ~10 cities. Each bin is a 2°F range (center) or open-ended (shoulder). Typical daily volume $50K-500K per market. Thin orderbooks ($500-2,000 depth). No designated market makers. Settlement via Weather Underground page value.

Key market microstructure facts (from research):
- Model update reaction window: 15-45 min for high-liquidity markets (NYC/London), 1-3h for medium (Chicago), 4-12h for low (Miami)
- Market vig (sum of all YES prices): ranges 0.95-1.05; when > 1.02, systematic buy_no opportunity exists
- Optimal lead time: T+3 to T+5 (prediction useful, market least efficient)
- Shoulder bin empirical win rates vary dramatically: NYC 15%, Atlanta 43%, London 6%

---

## 1. Data Foundation

Zeus inherits data from the retired Rainstorm system. No code, only data.

### 1.1 Inherited Data

| Source | Rows | Role in Zeus |
|--------|------|-------------|
| **Settlements** | 1,634 (1,626 with actuals) | Calibration outcomes: which bin won |
| **IEM ASOS daily** | 4,410 | Primary truth source for ASOS→WU offset calibration |
| **NOAA GHCND daily** | 6,520 | Climatological baseline (historical temperature distributions) |
| **Meteostat hourly** | 105,351 | London/Paris truth; Day0 hourly observation |
| **Meteostat daily** | 906 | European daily truth |
| **WU PWS daily** | 71 city-days | Settlement authority truth (growing) |
| **Open-Meteo forecasts** | 148,915 | Historical forecast skill analysis (NOT for live trading — v1 format) |
| **Ladder backfill** | 53,600 | Multi-lead forecast vs settlement enrichment |
| **Day1 settlement backfill** | 7,989 | Forecast error at T+1 |
| **Token price log** | 285,118 | Historical market prices for baseline backtesting |
| **Market events** | 14,901 | Bin structure and token ID mapping |

### 1.2 Data Gaps

| Gap | Impact | Resolution |
|-----|--------|-----------|
| **ENS 51-member history** | Cannot compute P_raw for historical settlements | Open-Meteo `past_days=92` backfill (~300 settlements). Older settlements: use climatology or skip. |
| **WU seasonal verification** | 2,000+ city-days collected, backfilling to 2024 — need to verify Summer/Fall coverage completeness per city | Agent running. Once backfill to 2024 completes, cross-validate ASOS→WU offset per city×season. |
| **token_price_log lacks bin labels** | Cannot run baseline without knowing which bin each price corresponds to | JOIN via token_id → market_events. First engineering task. |
| **Summer/Fall season gap** | Only 184/182 settlements each, 0 WU observations | Accept wider calibration uncertainty for these seasons. Use cluster-level (not city-level) Platt. |

### 1.3 Settlement Authority Hierarchy

For calibration training (determining which bin won):

```
Priority 1: Polymarket settlement result (authoritative — this IS the outcome)
Priority 2: WU PWS observed daily high (settlement source data)
Priority 3: IEM ASOS daily high + station offset (proxy for WU)
Priority 4: Meteostat daily high (Europe, when WU unavailable)
```

For Day0 real-time observation:
```
Priority 1: WU API (if available for that city)
Priority 2: IEM ASOS real-time + calibrated offset
Priority 3: Meteostat hourly (Europe)
```

---

## 2. Signal Generation

### 2.1 Primary Signal: ECMWF ENS 51-Member Ensemble

```python
# Open-Meteo Ensemble API (free, no key, 10K calls/day)
GET https://ensemble-api.open-meteo.com/v1/ensemble
  ?latitude={lat}&longitude={lon}
  &hourly=temperature_2m
  &models=ecmwf_ifs025
  &forecast_days={lead_days + 1}
  &temperature_unit={fahrenheit|celsius}
```

Processing:

```python
class EnsembleSignal:
    """51 ensemble members → probability vector over all bins."""

    def __init__(self, members_hourly: np.ndarray, city: City, target_date: date):
        """
        members_hourly: shape (51, hours) — all in city's settlement unit
        """
        # Daily max per member, respecting city timezone for day boundary
        tz_hours = self._select_hours_for_date(target_date, city.timezone, members_hourly.shape[1])
        self.member_maxes = members_hourly[:, tz_hours].max(axis=1)  # (51,)

        # WU settlement simulation: round to integer
        # This is the critical step most bots skip.
        # WU displays integer °F. The rounding changes bin assignment
        # at boundaries (53.4 → 53, 53.5 → 54).
        self.member_maxes_int = np.round(self.member_maxes).astype(int)

    def p_raw_vector(self, bins: list[Bin], n_mc: int = 5000) -> np.ndarray:
        """Probability vector over all bins with instrument noise.

        Per Statistical Methodology doc §1.2: ASOS sensor precision is ±0.5°F.
        Simple member counting ignores this — at bin boundaries, the measurement
        uncertainty can shift 5-10% of probability between adjacent bins.

        Method: Monte Carlo. For each ENS member, add instrument noise ε ~ N(0, 0.5²),
        then round to integer (simulating WU display). Repeat n_mc times.
        This correctly handles the full settlement chain:
            atmosphere → NWP member → sensor noise → METAR rounding → WU integer display
        """
        sigma_instrument = 0.5  # °F, ASOS sensor precision

        p = np.zeros(len(bins))
        for _ in range(n_mc):
            # Add instrument noise to each member's daily max
            noised = self.member_maxes + np.random.normal(0, sigma_instrument, 51)
            measured_int = np.round(noised).astype(int)

            for i, b in enumerate(bins):
                if b.is_open_low:
                    p[i] += np.sum(measured_int <= b.high)
                elif b.is_open_high:
                    p[i] += np.sum(measured_int >= b.low)
                else:
                    p[i] += np.sum((measured_int >= b.low) & (measured_int <= b.high))

        p = p / (51.0 * n_mc)
        total = p.sum()
        return p / total if total > 0 else p

    def spread(self) -> float:
        """Ensemble spread (°). Low = high consensus, High = uncertain."""
        return float(np.std(self.member_maxes))

    def is_bimodal(self) -> bool:
        """Detect regime split (e.g. cold front timing uncertainty).

        Uses Hartigan's dip test statistic. The naive max-gap/range heuristic
        (third-party review) misses subtle bimodality that still affects bin
        probabilities significantly. Dip test is the standard non-parametric
        test for unimodality.
        """
        from scipy.stats import gaussian_kde
        try:
            kde = gaussian_kde(self.member_maxes)
            x = np.linspace(self.member_maxes.min() - 1, self.member_maxes.max() + 1, 200)
            density = kde(x)
            # Count local maxima in KDE
            from scipy.signal import argrelextrema
            peaks = argrelextrema(density, np.greater, order=10)[0]
            return len(peaks) >= 2
        except Exception:
            # Fallback: simple gap heuristic
            s = np.sort(self.member_maxes)
            gaps = np.diff(s)
            rng = s[-1] - s[0]
            return rng > 0 and (gaps.max() / rng) > 0.3

    def boundary_sensitivity(self, boundary: float) -> float:
        """How many members are within ±0.5° of a bin boundary?
        High value = probability is sensitive to small forecast errors."""
        return float(np.sum(np.abs(self.member_maxes - boundary) < 0.5)) / 51.0
```

### 2.2 Cross-Check: GFS 31-Member Ensemble

Same API, `models=gfs025`. NOT blended into probability. Used only for conflict detection:

```python
def model_agreement(ecmwf_p: np.ndarray, gfs_p: np.ndarray) -> str:
    """Compare full probability vectors using symmetric divergence.

    JSD (Jensen-Shannon Divergence) is the symmetric version of KL.
    Bounded [0, ln2]. Third-party review caught that KL is asymmetric
    and its threshold (0.15) was a magic number.

    Additionally check: do the two models agree on which bin is most likely?
    If argmax bins differ by 2+ positions, that's a physically meaningful conflict.
    """
    from scipy.spatial.distance import jensenshannon
    jsd = jensenshannon(ecmwf_p, gfs_p) ** 2  # scipy returns sqrt(JSD)

    # Physical check: do modal bins agree?
    ecmwf_mode = int(np.argmax(ecmwf_p))
    gfs_mode = int(np.argmax(gfs_p))
    mode_gap = abs(ecmwf_mode - gfs_mode)

    if jsd < 0.02 and mode_gap <= 1:
        return "AGREE"
    elif jsd < 0.08 or mode_gap <= 1:
        return "SOFT_DISAGREE"
    else:
        return "CONFLICT"
```

When CONFLICT: skip this market entirely. When SOFT_DISAGREE: widen bootstrap CI (raise edge threshold).

### 2.3 Climatological Baseline

For each city × calendar month, compute historical bin probabilities from NOAA GHCND (6,520 rows spanning 2+ years):

```python
def climatology_vector(city: str, month: int, bins: list[Bin]) -> np.ndarray:
    """Historical temperature distribution — the 'dumb' model."""
    temps = ghcnd.query(city=city, month=month)
    p = np.zeros(len(bins))
    for i, b in enumerate(bins):
        if b.is_open_low:
            p[i] = np.mean(temps <= b.high)
        elif b.is_open_high:
            p[i] = np.mean(temps >= b.low)
        else:
            p[i] = np.mean((temps >= b.low) & (temps <= b.high))
    total = p.sum()
    return p / total if total > 0 else p
```

This is the **null model**. Any signal that can't beat climatology is noise.

### 2.4 Day0 Signal: Observation + Residual Forecast

For markets settling today:

```python
class Day0Signal:
    def __init__(self, observed_high_so_far: int, current_temp: int,
                 hours_remaining: float, ens: EnsembleSignal):
        self.obs_high = observed_high_so_far
        self.hours_remaining = hours_remaining
        self.ens = ens

    def p_vector(self, bins: list[Bin]) -> np.ndarray:
        """Combine observation with ENS remaining-hours forecast."""
        # Get ENS members for remaining hours only
        remaining = self.ens.members_for_remaining_hours(self.hours_remaining)
        # Final settlement high = max(observed_so_far, max_remaining)
        final_highs = np.maximum(remaining, self.obs_high)
        final_ints = np.round(final_highs).astype(int)

        p = np.zeros(len(bins))
        for i, b in enumerate(bins):
            if b.is_open_low:
                p[i] = np.mean(final_ints <= b.high)
            elif b.is_open_high:
                p[i] = np.mean(final_ints >= b.low)
            else:
                p[i] = np.mean((final_ints >= b.low) & (final_ints <= b.high))
        total = p.sum()
        return p / total if total > 0 else p
```

---

## 3. Probability Calibration

### 3.1 Platt Scaling

Two-parameter sigmoid calibration applied to individual bin probabilities:

```
P_cal = sigmoid(A × logit(P_raw) + B)
```

Trained on (P_raw_for_bin_i, did_bin_i_win) pairs from settled markets. One Platt model per calibration bucket.

**Bootstrap parameter uncertainty (Statistical Methodology doc §2.2):**

When fitting Platt, simultaneously generate 200 bootstrap parameter sets (A_i, B_i) for use in the double-bootstrap edge CI:

```python
class PlattCalibrator:
    def fit(self, p_raw: np.ndarray, outcomes: np.ndarray, n_param_bootstrap: int = 200):
        # Primary fit
        self.A, self.B = self._fit_logistic(p_raw, outcomes)
        self.n_samples = len(outcomes)
        self.fitted = True

        # Parameter uncertainty via bootstrap
        self.bootstrap_params = []
        for _ in range(n_param_bootstrap):
            idx = np.random.choice(len(outcomes), len(outcomes), replace=True)
            A_i, B_i = self._fit_logistic(p_raw[idx], outcomes[idx])
            self.bootstrap_params.append((A_i, B_i))
```

This is not optional — without `bootstrap_params`, the edge CI only captures ensemble uncertainty (σ_ensemble) and instrument noise (σ_instrument), but misses calibration model uncertainty (σ_parameter). The CI would be systematically too narrow, leading to overtrading.

### 3.2 Calibration Buckets

```
Bucket = cluster × season  (lead_days is a Platt INPUT FEATURE, not a bucket dimension)

Clusters:       US-Northeast (NYC), US-Midwest (Chicago),
                US-Southeast (Atlanta, Miami), US-SouthCentral (Dallas),
                US-Pacific (Seattle, SF, LA), Europe (London, Paris)
Seasons:        DJF, MAM, JJA, SON

Total:          6 × 4 = 24 buckets
```

**Why 24, not 72:** Third-party review identified that 72 buckets (with lead_band as a dimension) yields only ~45 positive samples per bucket. Each settlement produces 1 outcome=1 and 10 outcome=0 per market. The headline "496 pairs per bucket" masks that only ~45 are positive — and Platt's sigmoid upper segment is anchored by positive samples.

Removing lead_band as a bucket dimension and adding `lead_days` as a second input feature triples positive samples per bucket (45→135) while adding only 1 parameter (2→3):

```python
class ExtendedPlattCalibrator:
    """Platt with lead_days as input: P_cal = sigmoid(A * logit(P_raw) + B * lead_days + C)"""

    def fit(self, p_raw, lead_days, outcomes, n_param_bootstrap=200):
        p_clamped = np.clip(p_raw, 0.01, 0.99)
        logits = np.log(p_clamped / (1 - p_clamped))
        X = np.column_stack([logits, lead_days])  # 2D input

        from sklearn.linear_model import LogisticRegression
        lr = LogisticRegression(C=1.0, solver='lbfgs', max_iter=1000)
        lr.fit(X, outcomes)

        self.A = float(lr.coef_[0][0])  # logit weight
        self.B = float(lr.coef_[0][1])  # lead_days weight
        self.C = float(lr.intercept_[0])
        self.n_samples = len(outcomes)
        self.fitted = True

        # Bootstrap parameter uncertainty
        self.bootstrap_params = []
        for _ in range(n_param_bootstrap):
            idx = np.random.choice(len(outcomes), len(outcomes), replace=True)
            lr_b = LogisticRegression(C=1.0, solver='lbfgs', max_iter=1000)
            lr_b.fit(X[idx], outcomes[idx])
            self.bootstrap_params.append((
                float(lr_b.coef_[0][0]),
                float(lr_b.coef_[0][1]),
                float(lr_b.intercept_[0]),
            ))
```

### 3.3 Maturity Gate

| Bucket samples (n) | Calibration | Edge threshold multiplier |
|---------------------|-------------|--------------------------|
| n < 15 | None — use P_raw directly | 3× standard |
| 15 ≤ n < 50 | Platt with strong regularization (C=0.1) | 2× |
| 50 ≤ n < 150 | Platt standard (C=1.0) | 1.5× |
| n ≥ 150 | Platt standard | 1× |

### 3.4 Hierarchical Fallback

If a bucket has n < 15, fall back to the next more general level:
```
city+season+lead → cluster+season+lead → season+lead → global → uncalibrated
```

### 3.5 Post-Calibration Normalization

Platt is trained per-bin, not jointly. After calibrating each bin's P_raw independently, the vector may not sum to 1.0. Enforce the constraint:

```python
def calibrate_and_normalize(p_raw: np.ndarray, calibrator: PlattCalibrator) -> np.ndarray:
    p_cal = np.array([calibrator.predict(p) for p in p_raw])
    return p_cal / p_cal.sum()
```

### 3.6 Data Volume Assessment

Current settlement counts per bucket estimate (1,634 settlements × 11 bins = ~18,000 potential pairs):

| Season | Settlements | × 11 bins | Buckets (6 clusters × 3 leads) | Avg per bucket |
|--------|-------------|-----------|-------------------------------|---------------|
| Winter (DJF) | 811 | 8,921 | 18 | **496** ✓ Level 1 |
| Spring (MAM) | 457 | 5,027 | 18 | **279** ✓ Level 1 |
| Summer (JJA) | 184 | 2,024 | 18 | **112** ✓ standard Platt |
| Fall (SON) | 182 | 2,002 | 18 | **111** ✓ standard Platt |

**All seasons have enough data for standard Platt Scaling at cluster level.** Winter and Spring have enough for city-specific calibration. Summer and Fall need cluster-level pooling.

**ENS P_raw backfill:** Only the last 93 days (~300 settlements) can get true ENS 51-member P_raw via Open-Meteo API. For older settlements, three alternative data sources expand calibration coverage:

**Source A: TIGGE Archive (true ENS, growing)**
A data agent is downloading historical ECMWF ENS 51-member data via TIGGE to a separate data directory. Once imported into zeus.db, these provide true member-counted P_raw for settlements going back to 2024+. This is the primary path to breaking the 93-day ceiling.

**Source B: Rainstorm forecast_log (333K rows) → TIGGE Backfill Prioritization (NOT Platt training)**

Rainstorm's forecast_log has `ensemble_high_f` (mean) and `ensemble_std` for 14 months. These do NOT enter Platt training directly — Zeus's core differentiation is ENS member counting, not Gaussian CDF. Using Gaussian-approximated P_raw to train Platt would contaminate the calibration curve with the exact distributional assumption Zeus was designed to avoid.

Instead, forecast_log serves as a **prioritization index** for TIGGE backfill: settlements where `ensemble_std > 4°F` (high forecast uncertainty) are more valuable for Platt training because they cover the distribution's tails. The TIGGE download agent should prioritize these high-std city-dates first.

```python
# Use forecast_log to rank settlements by TIGGE backfill priority
priority = forecast_log.query("ensemble_std > 4.0") \
    .groupby(['city', 'target_date']) \
    .agg(max_std=('ensemble_std', 'max')) \
    .sort_values('max_std', ascending=False)
# Download TIGGE for these city-dates first
```

**Source C: Rainstorm Ladder Backfill (53,600 rows) → ENS Bias Correction (with discount)**

5 NWP models × 7 lead days × all cities, covering 14 months. Used to compute per-city per-season model biases.

**Critical nuance:** Ladder records deterministic forecast bias, not ensemble mean bias. The ensemble mean regresses more toward climatology than the deterministic forecast (regression-to-the-mean effect). Applying deterministic bias directly to ENS members would over-correct.

```python
det_bias = ladder_bias_table.get(city, season, "ecmwf")  # from deterministic forecast
discount = 0.7  # ensemble mean bias ≈ 70% of deterministic bias (empirical)
corrected_members = members - det_bias * discount
# The 0.7 discount should be calibrated from TIGGE data once available
# (compare true ensemble mean bias vs deterministic bias for the same city-seasons)
```

### 3.7 Recalibration Triggers

| Trigger | Action |
|---------|--------|
| 50 new settled pairs per bucket | Refit Platt |
| Hosmer-Lemeshow χ² > 7.81 on last 50 pairs | Force refit + alert |
| Seasonal boundary (equinox/solstice) | Refit all buckets |
| ECMWF model cycle update | Invalidate post-update data |
| 8/20 directional misses | Emergency: edge threshold 2× |

---

## 4. Market Analysis

### 4.1 Multi-Outcome Edge Scan

For each active market, compute edges across ALL bins simultaneously:

```python
class MarketAnalysis:
    """Full analysis of one market (one city, one date, all bins)."""

    def __init__(self, ens: EnsembleSignal, bins: list[Bin],
                 calibrator: PlattCalibrator, alpha: float):
        self.bins = bins
        self.p_raw = ens.p_raw_vector(bins)
        self.p_cal = calibrate_and_normalize(self.p_raw, calibrator)
        # Use VWMP (not mid-price) as market's implied probability
        self.p_market = np.array([b.vwmp for b in bins])

        # Posterior: blend model with market
        self.p_posterior = alpha * self.p_cal + (1 - alpha) * self.p_market

        # Market vig
        self.vig = float(self.p_market.sum())

        # Store ensemble for bootstrap
        self._ens = ens
        self._calibrator = calibrator
        self._alpha = alpha

    def find_edges(self, n_bootstrap: int = 500) -> list[BinEdge]:
        """Scan all bins for tradeable mispricing."""
        edges = []
        for i, b in enumerate(self.bins):
            # Bootstrap CI for this bin
            ci_lo, ci_hi = self._bootstrap_bin(i, n_bootstrap)

            # YES direction: model thinks bin is underpriced
            yes_edge = self.p_posterior[i] - self.p_market[i]
            if ci_lo > 0 and yes_edge > 0:
                edges.append(BinEdge(
                    bin=b, direction="buy_yes",
                    edge=yes_edge, ci_lower=ci_lo, ci_upper=ci_hi,
                    p_model=self.p_cal[i], p_market=self.p_market[i],
                    p_posterior=self.p_posterior[i],
                    entry_price=self.p_market[i],  # cost to buy YES
                ))

            # NO direction: model thinks bin is overpriced
            no_edge = self.p_market[i] - self.p_posterior[i]
            no_ci_lo = -ci_hi  # invert CI
            if no_ci_lo > 0 and no_edge > 0:
                edges.append(BinEdge(
                    bin=b, direction="buy_no",
                    edge=no_edge, ci_lower=no_ci_lo, ci_upper=-ci_lo,
                    p_model=1 - self.p_cal[i], p_market=1 - self.p_market[i],
                    p_posterior=1 - self.p_posterior[i],
                    entry_price=1 - self.p_market[i],  # cost to buy NO
                ))

        return edges

    def _bootstrap_bin(self, bin_idx: int, n: int) -> tuple[float, float]:
        """Double Bootstrap: resample ENS members AND Platt parameters.

        Per Statistical Methodology doc §2.2: there are three distinct σ sources:
          σ_ensemble (forecast uncertainty) — captured by resampling members
          σ_parameter (Platt A/B estimation error) — captured by resampling Platt params
          σ_instrument (sensor noise) — captured by MC noise in p_raw_vector

        Single-layer bootstrap (members only) underestimates CI width because it
        ignores the uncertainty in the calibration model itself. Double bootstrap
        correctly propagates both sources of uncertainty through the edge calculation.
        """
        members = self._ens.member_maxes
        b = self.bins[bin_idx]
        sigma_instrument = 0.5  # °F
        edges = np.empty(n)

        # Get Platt parameter bootstrap samples (pre-computed by calibration manager)
        platt_params = self._calibrator.bootstrap_params  # list of (A, B) tuples

        for i in range(n):
            # Layer 1: resample ENS members + instrument noise
            sample = np.random.choice(members, size=len(members), replace=True)
            noised = sample + np.random.normal(0, sigma_instrument, len(sample))
            measured = np.round(noised).astype(int)

            if b.is_open_low:
                p_raw = np.mean(measured <= b.high)
            elif b.is_open_high:
                p_raw = np.mean(measured >= b.low)
            else:
                p_raw = np.mean((measured >= b.low) & (measured <= b.high))

            # Layer 2: sample a random Platt parameterization
            if platt_params and len(platt_params) > 1:
                A, B = platt_params[np.random.randint(len(platt_params))]
                p_clamped = max(0.01, min(0.99, p_raw))
                logit = np.log(p_clamped / (1 - p_clamped))
                p_cal = 1.0 / (1.0 + np.exp(-(A * logit + B)))
            else:
                p_cal = self._calibrator.predict(p_raw)

            p_post = self._alpha * p_cal + (1 - self._alpha) * self.p_market[bin_idx]
            edges[i] = p_post - self.p_market[bin_idx]

        p_value = float(np.mean(edges <= 0))  # exact bootstrap p-value
        return float(np.percentile(edges, 5)), float(np.percentile(edges, 95)), p_value
```

### 4.2 Market Vig Detection

```python
def vig_signal(analysis: MarketAnalysis) -> str:
    """Detect systematic overpricing (vig > 1.0) or underpricing (vig < 1.0)."""
    if analysis.vig > 1.03:
        return "OVERPRICED"   # all bins overpriced → favor buy_no
    elif analysis.vig < 0.97:
        return "UNDERPRICED"  # bins underpriced → favor buy_yes
    else:
        return "NEUTRAL"
```

### 4.3 Boundary Sensitivity

```python
def boundary_edges(ens: EnsembleSignal, bins: list[Bin]) -> list[int]:
    """Identify bins where the boundary discretization effect is large.
    These bins have the most potential for mispricing due to integer rounding."""
    sensitive = []
    for i in range(len(bins) - 1):
        boundary = bins[i].high  # = bins[i+1].low
        sensitivity = ens.boundary_sensitivity(float(boundary))
        if sensitivity > 0.15:  # >15% of members within ±0.5° of boundary
            sensitive.append(i)
            sensitive.append(i + 1)
    return list(set(sensitive))
```

### 4.4 FDR Control for Multi-Bin Edge Testing

**Per Statistical Methodology doc §3.1:** Zeus scans 10 cities × 11 bins × 2 directions = 220 edge hypotheses per discovery cycle. At α=0.05, expect ~11 false positives per cycle even with zero real edge.

Apply Benjamini-Hochberg FDR control after computing all edges:

```python
def fdr_filter(edges: list[BinEdge], fdr_alpha: float = 0.10) -> list[BinEdge]:
    """Filter edges using False Discovery Rate control.

    FDR=0.10 means: of the edges we declare tradeable, at most 10% are
    false positives. This is acceptable because false positive edges
    produce ~zero P&L (not systematic loss), while false negatives
    (missed real edges) are just missed opportunities.
    """
    if not edges:
        return []

    # Sort by CI lower bound (most confident first)
    sorted_edges = sorted(edges, key=lambda e: -e.ci_lower)

    # p-value = fraction of bootstrap samples where edge ≤ 0
    # Computed directly in _bootstrap_bin, NOT approximated.
    # (Third-party review caught that the linear approximation
    # 1.0 - ci_lower/(ci_upper-ci_lower) is mathematically wrong —
    # it assumes uniform distribution. Bootstrap gives exact p-value.)

    # BH procedure
    m = len(sorted_edges)
    sorted_by_p = sorted(sorted_edges, key=lambda e: e.p_value)
    threshold_k = 0
    for k, e in enumerate(sorted_by_p, 1):
        if e.p_value <= fdr_alpha * k / m:
            threshold_k = k

    return sorted_by_p[:threshold_k] if threshold_k > 0 else []
```

### 4.5 α (Model Confidence Weight)

```python
def compute_alpha(
    calibration_level: int,    # 1-5
    ensemble_spread: float,
    model_agreement: str,
    lead_days: int,
    hours_since_open: float,   # NEW: how long has this market been open?
) -> float:
    # Base α: trust calibration maturity
    # Levels map directly to maturity gate (§3.3):
    #   Level 1 = city-specific Platt, n ≥ 150 → highest model trust
    #   Level 2 = standard Platt, 50 ≤ n < 150
    #   Level 3 = regularized Platt, 15 ≤ n < 50
    #   Level 4 = uncalibrated (P_raw direct), n < 15 → lowest model trust
    base = {4: 0.25, 3: 0.40, 2: 0.55, 1: 0.65}[calibration_level]

    a = base

    # Tight ensemble → trust model more
    if ensemble_spread < 2.0: a += 0.05
    if ensemble_spread > 5.0: a -= 0.10

    # Model disagreement → trust model less
    if model_agreement == "SOFT_DISAGREE": a -= 0.10
    if model_agreement == "CONFLICT": a -= 0.20

    # Short lead time → model is more reliable
    if lead_days <= 1: a += 0.05
    if lead_days >= 5: a -= 0.05

    # NEW: Recently opened markets have unreliable prices
    # Trust model MORE when market is fresh (prices haven't converged yet)
    if hours_since_open < 12: a += 0.10
    if hours_since_open < 6:  a += 0.05  # cumulative with above

    return max(0.20, min(0.85, a))
```

---

## 5. Position Sizing

### 5.1 Per-Bin Kelly

```python
def kelly_size(
    p_posterior: float,
    entry_price: float,      # cost to buy the token (YES or NO)
    bankroll: float,
    kelly_mult: float = 0.25,  # Quarter-Kelly
) -> float:
    """Kelly for a single binary outcome (one bin, one direction)."""
    if p_posterior <= entry_price:
        return 0.0

    f_star = (p_posterior - entry_price) / (1 - entry_price)
    return f_star * kelly_mult * bankroll
```

### 5.2 Dynamic Kelly Multiplier

```python
def dynamic_kelly_mult(
    base: float = 0.25,
    ci_width: float = 0.0,
    lead_days: int = 0,
    rolling_win_rate_20: float = 0.50,
    portfolio_heat: float = 0.0,
    drawdown_pct: float = 0.0,
    max_drawdown: float = 0.20,
) -> float:
    m = base
    if ci_width > 0.10: m *= 0.7
    if ci_width > 0.15: m *= 0.5
    if lead_days >= 5: m *= 0.6
    elif lead_days >= 3: m *= 0.8
    if rolling_win_rate_20 < 0.40: m *= 0.5
    elif rolling_win_rate_20 < 0.45: m *= 0.7
    if portfolio_heat > 0.40: m *= max(0.1, 1.0 - portfolio_heat)
    if drawdown_pct > 0 and max_drawdown > 0:
        m *= max(0.0, 1.0 - drawdown_pct / max_drawdown)
    return m
```

### 5.3 Direction Agnostic

Zeus does NOT have a buy_yes/buy_no preference. It evaluates ALL edges across ALL bins in ALL directions and selects the highest risk-adjusted EV trades. The direction with better risk-reward wins:

```python
def rank_edges(edges: list[BinEdge]) -> list[BinEdge]:
    """Rank by risk-adjusted expected value."""
    for e in edges:
        # EV per dollar risked
        if e.direction == "buy_yes":
            e.ev_per_dollar = e.p_posterior * (1 - e.entry_price) - (1 - e.p_posterior) * e.entry_price
            e.ev_per_dollar /= e.entry_price  # normalize by capital at risk
        else:  # buy_no
            e.ev_per_dollar = e.p_posterior * (1 - e.entry_price) - (1 - e.p_posterior) * e.entry_price
            e.ev_per_dollar /= e.entry_price
    return sorted(edges, key=lambda e: e.ev_per_dollar, reverse=True)
```

### 5.4 Portfolio Constraints

```python
LIMITS = {
    "max_single_position_pct":     0.10,   # 10% of bankroll per bin
    "max_portfolio_heat_pct":      0.50,   # 50% total at risk
    "max_correlated_exposure_pct": 0.25,   # 25% correlated positions
    "max_city_exposure_pct":       0.20,   # 20% in any one city
    "max_region_exposure_pct":     0.35,   # 35% in any one region
    "max_daily_loss_pct":          0.08,   # 8% daily → halt
    "max_weekly_loss_pct":         0.15,   # 15% weekly → halt
    "max_drawdown_pct":            0.20,   # 20% peak-to-trough → halt
    "min_order_usd":               1.00,   # Polymarket minimum
}
```

### 5.5 Correlation Heuristics

```python
CORRELATION = {
    "same_city_same_date":       0.90,  # NYC 52-53 and NYC 54-55, same day
    "same_city_adjacent_date":   0.70,  # NYC tomorrow and NYC day-after
    "same_city_distant_date":    0.40,  # NYC tomorrow and NYC in 5 days
    "same_region_same_date":     0.50,  # NYC and Boston
    "diff_region":               0.20,  # NYC and Chicago
}
```

---

## 6. Position Lifecycle (Blueprint v2 — supersedes prior lifecycle design)

**See `zeus_blueprint_v2.md` for the complete Position object definition and all exit validation layers.**

The Position is the central entity. Signal is an input to its EVALUATED phase. Execution is an output of its ENTERED phase. Exit is a self-decision in its HOLDING phase. Settlement is its terminal state. Every module exists to serve the Position's lifecycle.

### 6.0 The Position Object

Position carries its full identity through every module. No downstream consumer should ever need to infer direction, probability space, or entry method — Position tells them.

Key fields (see blueprint v2 §2 for complete definition):
- `direction`, `token_id` — immutable after creation
- `p_held_side` — probability in the held side's space (flipped exactly once at creation)
- `entry_method`, `decision_snapshot_id` — immutable record of how and with what data
- `exit_strategy` — buy_no or buy_yes (Position owns its exit logic)
- `status` — pending → entered → holding → day0_window → settled/voided/admin_closed
- `chain_state` — unknown → synced / local_only / chain_only / quarantined
- `neg_edge_count`, `last_monitor_prob` — persisted exit state across cycles

`Position.evaluate_exit(fresh_data)` is the ONLY exit decision point. Monitor calls it. Buy_no and buy_yes have completely separate internal paths (8 validation layers each, matching entry's rigor).

`Position.close(exit_price, reason)` vs `Position.void(reason)` — close records real P&L, void records P&L=0 with admin_exit_reason (excluded from all metrics).

### 6.0.1 Decision Chain and NoTradeCase

Every cycle produces immutable artifacts. Even when no trade happens.

```
CycleArtifact
  ├── for each candidate:
  │   ├── SignalResult → EdgeDecision
  │   │   ├── if trade: RiskVerdict → OrderIntent → ExecutionReport
  │   │   └── if no trade: NoTradeCase (rejection_stage + reasons)
  │   └── for each held position:
  │       └── MonitorResult (fresh_prob, exit_decision, neg_edge_count)
  └── CycleSummary
```

`NoTradeCase.rejection_stage` = MARKET_FILTER | SIGNAL_QUALITY | EDGE_INSUFFICIENT | FDR_FILTERED | RISK_REJECTED | SIZING_TOO_SMALL | EXECUTION_FAILED | ANTI_CHURN

Without NoTradeCase, when the system doesn't trade you can only see "0 trades today" and guess why. With it, you can see "147 candidates evaluated, 12 had edge, 3 passed FDR, 2 rejected by risk, 1 too small to size" — complete pipeline diagnosis.

### 6.0.2 Truth Hierarchy

Three sources of truth WILL disagree. The hierarchy is:

```
1. Chain (authoritative)    — what Polymarket's blockchain says
2. Chronicler (audit trail) — what Zeus recorded when it happened
3. Portfolio (working state) — what Zeus currently believes

Chain > Chronicler > Portfolio. Always.
```

Reconciliation every cycle (live mode):
- Local + chain match → SYNCED
- Local but NOT on chain → VOID immediately (don't ask why — chain is truth)
- Chain but NOT local → QUARANTINE (low confidence, 48h forced exit eval)

### 6.0.3 CycleRunner: Pure Orchestration

The CycleRunner is < 50 lines. It contains ZERO business logic. It doesn't know what an edge is, what buy_no means, or how Platt works. It orchestrates:

```
housekeeping → chain_reconciliation → risk_precheck → monitor_held_positions →
scan_candidates → evaluate → execute → record_artifacts → status_update
```

opening_hunt, update_reaction, day0_capture are NOT separate code paths. They are `DiscoveryMode` values that affect which markets the scanner finds and what signal method the evaluator uses. The lifecycle logic is identical for all modes.

## 6. Trade Lifecycle (continued)

### 6.1 State Machine

```
DISCOVERED → EVALUATED → ENTERED → HOLDING → EXITED
                 │
            CI ≤ 0 or
         risk rejected
                 │
              SKIPPED
```

Once ENTERED, a position is HELD until an exit trigger fires. The entry decision is NOT re-evaluated. Monitor only checks exit conditions.

### 6.2 Discovery Modes

**Mode A: Opening Hunt (primary edge source)**

Trigger: New market detected on Polymarket (opened < 24 hours ago).

```python
async def opening_hunt():
    """Scan for newly opened markets where prices haven't converged."""
    new_markets = scanner.find_markets(max_hours_since_open=24, min_hours_to_resolution=24)

    for market in new_markets:
        ens = await ensemble_client.fetch_ecmwf(market.city, market.target_date)
        gfs = await ensemble_client.fetch_gfs(market.city, market.target_date)

        if model_agreement(ens.p_raw_vector(market.bins),
                           gfs.p_raw_vector(market.bins)) == "CONFLICT":
            continue

        analysis = MarketAnalysis(ens, market.bins, calibrator, alpha=compute_alpha(
            calibration_level=...,
            hours_since_open=market.hours_since_open,  # key: fresh market
            ...
        ))

        for edge in analysis.find_edges():
            if edge.ci_lower > 0:
                place_limit_order(edge, market)
```

Schedule: Every 30 minutes. Markets open asynchronously on Polymarket.

**Mode B: Update Reaction**

Trigger: ECMWF 00Z/12Z data arrives (approximately 06-09 UTC / 18-21 UTC).

```python
async def update_reaction():
    """After ENS update, check if any HELD positions need exit and scan for new edges."""
    # 1. Check exit triggers on held positions
    for position in portfolio.open_positions():
        ens = await ensemble_client.fetch_ecmwf(position.city, position.target_date)
        trigger = check_exit_triggers(position, ens)
        if trigger:
            execute_exit(position, trigger)

    # 2. Scan existing markets (NOT newly opened — those are Mode A's job)
    for market in scanner.find_markets(min_hours_since_open=24, min_hours_to_resolution=6):
        # Only look for edges if we don't already hold a position in this market
        if portfolio.has_position(market.id):
            continue
        # ... same edge evaluation as Mode A
```

Schedule: 4 times daily, aligned with ENS data availability windows.

**Mode C: Day0 Settlement Capture**

Trigger: Market resolves within 6 hours AND live observation data available.

```python
async def day0_capture():
    """Use observation + residual ENS for near-settlement markets."""
    for market in scanner.find_markets(max_hours_to_resolution=6):
        obs = await observation_client.fetch(market.city)
        ens = await ensemble_client.fetch_ecmwf(market.city, market.target_date)
        day0 = Day0Signal(obs.high_so_far, obs.current_temp,
                          market.hours_to_resolution, ens)
        # Day0 has higher α (trust observation more)
        # ... evaluate edges with day0.p_vector()
```

Schedule: Every 15 minutes for markets within 6 hours of resolution.

### 6.3 Exit Triggers (Exhaustive)

```python
class ExitTrigger(Enum):
    SETTLEMENT       = "settlement"        # Market resolved
    EDGE_REVERSAL    = "edge_reversal"     # Edge sign flipped on 2 consecutive ENS runs
    # STOP_LOSS removed. Third-party review: in binary markets with hard settlement,
    # unrealized price loss doesn't change EV. If you bought at $0.10 believing P=25%,
    # price dropping to $0.06 doesn't invalidate your probability estimate.
    # Only EDGE_REVERSAL (model probability changed) should trigger exit.
    # Portfolio-level max_drawdown (§5.4) protects against aggregate ruin.
    RISK_HALT        = "risk_halt"         # RiskGuard halts trading
    NWS_EXTREME      = "nws_extreme"       # Extreme weather alert for city
    EXPIRY_EXIT      = "expiry_exit"       # < 4h to resolution + position unprofitable
    BIMODAL_SHIFT    = "bimodal_shift"     # Ensemble went bimodal, our range in minority
```

**What is NOT a trigger:** Edge shrinking (but still positive), ensemble sources disagreeing, price fluctuations within stop loss, new ENS run with slightly different P. These are noise. Hold.

### 6.4 Execution: Limit Orders Only + VWMP Fair Value

**Edge calculation must use VWMP, not mid-price.**

Using `(bid + ask) / 2` as the market price is wrong. The fair value is biased toward the side with more size:

```python
def vwmp(best_bid: float, best_ask: float,
         bid_size: float, ask_size: float) -> float:
    """Volume-Weighted Micro-Price.

    If bid_size >> ask_size, fair value is closer to the ask
    (lots of buying pressure). If ask_size >> bid_size, fair
    value is closer to the bid (lots of selling pressure).
    """
    total = bid_size + ask_size
    if total <= 0:
        return (best_bid + best_ask) / 2  # fallback to mid
    return (best_bid * ask_size + best_ask * bid_size) / total
```

All edge calculations throughout the system use VWMP as the market price, not mid-price, not last trade price.

**Toxicity avoidance: cancel orders when whale activity detected.**

```python
class Executor:
    """Limit orders only. Never market orders. Cancel on toxicity."""

    async def place_order(self, edge: BinEdge, size_usd: float, market: Market):
        # Use VWMP-derived limit price, offset by 2% toward model fair value
        if edge.direction == "buy_yes":
            limit_price = min(edge.p_posterior, edge.vwmp) - 0.02
        else:
            limit_price = min(1 - edge.p_posterior, 1 - edge.vwmp) - 0.02

        order = await self.clob.place_limit_order(
            token_id=edge.bin.token_id(edge.direction),
            side="BUY",
            size=size_usd / limit_price,
            price=limit_price,
        )

        # Timeout varies by discovery mode (third-party review: 10min is too short
        # for Opening Hunt where you're providing liquidity in thin markets)
        timeouts = {"opening_hunt": 14400, "update_reaction": 3600, "day0": 900}
        timeout = timeouts.get(edge.discovery_mode, 3600)
        filled = await self._wait_for_fill_or_toxicity(order, market, timeout=timeout)

        if filled:
            portfolio.add_position(order, edge)
            chronicler.log_entry(order, edge)
        else:
            await self.clob.cancel_order(order.id)
            # Don't retry — move on

    async def _wait_for_fill_or_toxicity(self, order, market, timeout: int) -> bool:
        """Wait for fill. Cancel immediately if whale sweeps adjacent bins."""
        start = time.time()
        while time.time() - start < timeout:
            status = await self.clob.check_order(order.id)
            if status == "FILLED":
                return True

            # Toxicity check: if any adjacent bin's price moved > 15%
            # in the last 60 seconds, a whale is sweeping — cancel
            if await self._detect_whale_sweep(market, order.token_id):
                return False

            await asyncio.sleep(30)
        return False
```

### 6.5 Monitor Frequency

| Time to Resolution | Check Frequency |
|--------------------|----------------|
| > 48h | Every 6h |
| 24-48h | Every 2h |
| 12-24h | Every 1h |
| 4-12h | Every 30min |
| < 4h | Every 15min (Day0 mode) |

---

## 7. Risk Guard

### 7.1 Independent Process

Separate process. Separate SQLite DB (`risk_state.db`). Cannot be overridden by the trading engine. 60-second tick.

### 7.2 Graduated Response

```python
class RiskLevel(Enum):
    GREEN  = "green"    # Normal
    YELLOW = "yellow"   # Edge threshold 2×, Kelly ÷ 2
    ORANGE = "orange"   # New entries halted, exit-only
    RED    = "red"      # Close all positions at market
```

### 7.3 Trigger Matrix

| Condition | Level |
|-----------|-------|
| Brier score > 0.25 (rolling 30 settled) | YELLOW |
| Brier score > 0.30 | ORANGE |
| Brier score > 0.35 | RED |
| Directional accuracy < 45% (rolling 30) | ORANGE |
| Rolling 20-trade win rate < 40% | YELLOW (reduce Kelly 50%) |
| Rolling 20-trade win rate < 35% | ORANGE |
| Daily loss > 8% bankroll | ORANGE |
| Weekly loss > 15% bankroll | ORANGE |
| Drawdown > 20% peak-to-trough | RED |
| Hosmer-Lemeshow χ² > 7.81 (last 50 pairs) | YELLOW + force recalibration |
| No ENS data received in 6h | ORANGE |
| 3 consecutive API failures | ORANGE |

---

## 8. Learning System

### 8.1 Settlement Processing

Every resolved market:

```python
async def process_settlement(market: Market, result: SettlementResult):
    # 1. Generate calibration pairs (one per bin)
    ens_snapshot = ensemble_store.get(market.city, market.target_date)
    for i, bin in enumerate(market.bins):
        outcome = 1 if result.winning_bin == i else 0
        calibration_store.add_pair(CalibrationPair(
            p_raw=ens_snapshot.p_raw_vector[i],
            outcome=outcome,
            city=market.city,
            season=get_season(market.target_date),
            cluster=get_cluster(market.city),
            lead_days=ens_snapshot.lead_days,
            target_date=market.target_date,
            settlement_value=result.wu_value,
        ))

    # 2. Refit Platt if bucket hit milestone
    for bucket in affected_buckets(market):
        if calibration_store.count(bucket) % 50 == 0:
            calibration_manager.refit(bucket)

    # 3. Update performance metrics
    chronicler.log_settlement(market, result)
    risk_guard.update_metrics(result)
```

### 8.2 Overfitting Prevention

1. **Walk-forward only**: Platt trained on pairs with `target_date < today`
2. **Complexity cap**: Platt (2 params) only. Beta Calibration (3 params) at n > 300 if residuals show systematic non-sigmoid pattern
3. **Permutation test**: After every 100 trades, permute outcomes 1000×. If P&L not in top 5%, alert
4. **Structural priors**: Forecast skill degrades with lead time — this is hardcoded, not fitted

---

## 9. Architecture (Blueprint v2)

**See `zeus_blueprint_v2.md` for complete architectural specification.** This section summarizes the key structural elements.

### 9.0 Design Principles

1. **Position is the center.** Signal is an input. Execution is an output. Every module serves Position's lifecycle.
2. **CycleRunner is a pure orchestrator.** Zero business logic. < 50 lines. Discovery modes are parameters, not separate code paths.
3. **Exit rigor equals entry rigor.** 8 validation layers for exit, matching entry's depth. Position owns its exit strategy.
4. **Every decision is recorded.** TradeCase when trading. NoTradeCase (with rejection_stage) when not. Decision chain is queryable from any trade_id.
5. **Chain is truth.** Portfolio is a cache. When they disagree, chain wins.
6. **Types prevent bug categories.** Temperature/TemperatureDelta make unit confusion a TypeError. Position object makes direction/space confusion structurally impossible.

### 9.1 Process Model

```
┌──────────────────────────────────────────────────────┐
│              Zeus Main Process                        │
│                                                      │
│  CycleRunner (pure orchestrator, < 50 lines)         │
│    ├── housekeeping + chain reconciliation            │
│    ├── risk precheck (RiskGuard level)                │
│    ├── monitor held positions (Position.evaluate_exit)│
│    ├── scan candidates (mode-dependent)               │
│    ├── evaluate + size + execute                      │
│    └── record artifacts (Decision Chain)              │
│                                                      │
│  Scheduled modes:                                     │
│    Opening Hunt  (30 min) ──┐                         │
│    Update Reaction (4×/day) ┼── same CycleRunner      │
│    Day0 Capture (15 min) ───┘   different parameters  │
│    Harvester (hourly)                                 │
│    ENS Collection (continuous)                        │
│  └──────────────┘  └────────────────┘  │
│  ┌──────────────┐                      │
│  │   Monitor     │  ← variable freq    │
│  └──────────────┘                      │
└────────────────────────────────────────┘

┌────────────────────────────────────────┐
│          Risk Guard (separate)         │
│           60-second tick               │
└────────────────────────────────────────┘
```

Two processes. SQLite for IPC. APScheduler for job management.

### 9.2 Module Layout

```
zeus/
├── src/
│   ├── main.py                    # Entry: ZEUS_MODE=live|paper
│   ├── config.py                  # Single config loader, strict (no fallback defaults)
│   │
│   ├── data/                      # Data acquisition
│   │   ├── ensemble_client.py     # Open-Meteo ENS 51 + GFS 31
│   │   ├── observation_client.py  # IEM ASOS + WU + Meteostat for Day0
│   │   ├── climatology.py         # NOAA GHCND historical distributions
│   │   ├── nws_client.py          # NWS extreme weather alerts
│   │   ├── polymarket_client.py   # CLOB order placement
│   │   └── market_scanner.py      # Gamma API market discovery
│   │
│   ├── signal/                    # Signal generation
│   │   ├── ensemble_signal.py     # 51 members → P_raw vector
│   │   ├── day0_signal.py         # Observation + residual ENS
│   │   └── model_agreement.py     # ECMWF vs GFS conflict
│   │
│   ├── calibration/               # Probability calibration
│   │   ├── platt.py               # PlattCalibrator
│   │   ├── manager.py             # Bucket routing, maturity gate
│   │   ├── store.py               # SQLite: pairs + models
│   │   ├── drift.py               # Hosmer-Lemeshow, seasonal triggers
│   │   └── backfill.py            # ENS P_raw backfill for historical settlements
│   │
│   ├── strategy/                  # Trading decisions
│   │   ├── market_analysis.py     # MarketAnalysis: full-distribution edge scan
│   │   ├── market_fusion.py       # α-weighted posterior
│   │   ├── kelly.py               # Per-bin Kelly + dynamic multiplier
│   │   ├── correlation.py         # Heuristic correlation + exposure check
│   │   └── risk_limits.py         # Hard caps
│   │
│   ├── execution/                 # Trade lifecycle
│   │   ├── opening_hunt.py        # Mode A: newly opened markets
│   │   ├── update_reaction.py     # Mode B: post-ENS update scan + exit check
│   │   ├── day0_capture.py        # Mode C: observation-based settlement capture
│   │   ├── monitor.py             # Variable-frequency exit trigger checking
│   │   ├── exit_triggers.py       # 7 exit conditions
│   │   ├── executor.py            # Limit-order-only execution
│   │   └── harvester.py           # Settlement → calibration pairs + P&L
│   │
│   ├── state/                     # Persistent state
│   │   ├── portfolio.py           # Atomic JSON + SQL mirror
│   │   ├── chronicler.py          # Append-only trade log
│   │   ├── ensemble_store.py      # ENS snapshot archive
│   │   └── db.py                  # Schema + migrations
│   │
│   ├── riskguard/                 # Independent process
│   │   ├── riskguard.py           # 60s main loop
│   │   ├── metrics.py             # Brier, H-L, accuracy, win rate
│   │   └── risk_level.py          # GREEN/YELLOW/ORANGE/RED
│   │
│   └── analysis/                  # Diagnostics
│       ├── dashboard.py           # Dash web UI
│       ├── performance.py         # P&L analysis
│       ├── calibration_report.py  # Platt diagnostics
│       └── baseline.py            # Phase 0 baseline experiment
│
├── config/
│   ├── settings.json              # All parameters, single file
│   └── cities.json                # City metadata
│
├── state/
│   ├── zeus.db                    # Main DB (schema below)
│   ├── risk_state.db              # RiskGuard (separate)
│   ├── positions.json             # Position state
│   └── ensemble-log/              # ENS snapshots (append-only)
│
│   ## zeus.db core tables (Statistical Methodology doc §1.1, §4.3):
│   ##
│   ## ensemble_snapshots:
│   ##   snapshot_id, city, target_date,
│   ##   issue_time     — when the ENS model run started
│   ##   valid_time     — forecast target time
│   ##   available_at   — when data became available to Zeus (prevents look-ahead)
│   ##   fetch_time     — when Zeus actually fetched from API
│   ##   lead_hours, members_json (51 daily maxes), p_raw_json (per-bin),
│   ##   spread, is_bimodal, model_version, data_version
│   ##   UNIQUE(city, target_date, issue_time, data_version)
│   ##
│   ## calibration_pairs:
│   ##   city, target_date, range_label, p_raw, outcome (0/1),
│   ##   lead_days, season, cluster, forecast_available_at, settlement_value
│   ##
│   ## platt_models:
│   ##   bucket_key, param_A, param_B, bootstrap_params_json (200 (A,B) pairs),
│   ##   n_samples, brier_insample, fitted_at, is_active
│   ##
│   ## trade_decisions:
│   ##   trade_id, market_id, bin_label, direction, size, price, timestamp,
│   ##   forecast_snapshot_id (FK → ensemble_snapshots),
│   ##   calibration_model_version,
│   ##   p_raw, p_calibrated, p_posterior, edge, ci_lower, ci_upper,
│   ##   kelly_fraction,
│   ##   -- Attribution (MANDATORY, see CLAUDE.md):
│   ##   edge_source TEXT,         -- 'favorite_longshot'|'opening_inertia'|'boundary'|'vig_exploit'|'mixed'
│   ##   bin_type TEXT,            -- 'shoulder_low'|'shoulder_high'|'center'|'adjacent_boundary'
│   ##   discovery_mode TEXT,      -- 'opening_hunt'|'update_reaction'|'day0_capture'
│   ##   market_hours_open REAL,   -- hours since market opened at entry
│   ##   fill_quality REAL         -- (exec_price - vwmp) / vwmp
│   ##
│   ## All queries that feed trading decisions MUST include:
│   ##   WHERE available_at <= @decision_time
│   ## This is enforced at the query layer, not optional.
│
├── scripts/
│   ├── baseline_experiment.py     # Phase 0: GO/NO-GO gate
│   ├── backfill_ens.py            # 92-day ENS P_raw backfill
│   └── migrate_rainstorm_data.py  # Import settlement + observation data from rainstorm.db
│
└── tests/
```

### 9.3 Config

```json
{
  "capital_base_usd": 150,
  "mode": "paper",

  "discovery": {
    "opening_hunt_interval_min": 30,
    "update_reaction_times_utc": ["07:00", "09:00", "19:00", "21:00"],
    "day0_interval_min": 15,
    "max_lead_days": 7,
    "preferred_lead_days": [3, 4, 5],
    "min_hours_to_resolution": 6
  },

  "ensemble": {
    "primary": "ecmwf_ifs025",
    "crosscheck": "gfs025",
    "conflict_kl_threshold": 0.15
  },

  "calibration": {
    "method": "platt",
    "refit_every_n": 50,
    "seasonal_dates": ["03-20", "06-21", "09-22", "12-21"],
    "maturity": {"level1": 150, "level2": 50, "level3": 15}
  },

  "edge": {
    "n_bootstrap": 500,
    "base_alpha": {"level5": 0.25, "level4": 0.35, "level3": 0.45, "level2": 0.55, "level1": 0.65},
    "opening_alpha_bonus": 0.15
  },

  "sizing": {
    "kelly_multiplier": 0.25,
    "max_single_position_pct": 0.10,
    "max_portfolio_heat_pct": 0.50,
    "max_correlated_pct": 0.25,
    "max_city_pct": 0.20,
    "max_region_pct": 0.35,
    "min_order_usd": 1.00
  },

  "exit": {
    "stop_loss_pct": 0.40,
    "reversal_confirmations": 2,
    "expiry_hours": 4
  },

  "riskguard": {
    "brier_yellow": 0.25,
    "brier_orange": 0.30,
    "brier_red": 0.35,
    "accuracy_orange": 0.45,
    "win_rate_yellow": 0.40,
    "win_rate_orange": 0.35,
    "max_daily_loss_pct": 0.08,
    "max_weekly_loss_pct": 0.15,
    "max_drawdown_pct": 0.20,
    "staleness_hours": 6
  },

  "execution": {
    "order_type": "limit_only",
    "limit_offset_pct": 0.02,
    "fill_timeout_seconds": 600,
    "cancel_if_not_filled": true
  }
}
```

---

## 10. Implementation Phases

### Phase 0: Baseline Experiment (GO/NO-GO)

**Duration:** 3-5 days. Analysis only — no system code.

| Task | Output |
|------|--------|
| `migrate_rainstorm_data.py`: Import settlements + market_events + token_price_log into zeus.db | Clean data |
| Join token_price_log → market_events to recover bin-level prices | Enriched price history |
| Compute climatology probabilities per city×month from GHCND | Null model |
| Simulate: for each settlement, which bins were mispriced (climatology vs market)? | Edge distribution |
| Simulate: favorite-longshot bias magnitude per city/bin-type | Structural edge size |
| Simulate: opening price inertia (price at T-24h vs fair value at T-0) | Timing edge size |
| Answer: is there exploitable structural mispricing? | **GO or STOP** |

**GO criterion:** At least one strategy variant shows Sharpe > 0.5 across 1,634 settlements.

### Phase A: Data + Signal Infrastructure

**Duration:** 1 week

| Task |
|------|
| `ensemble_client.py`: Fetch ECMWF 51 + GFS 31 from Open-Meteo |
| `ensemble_signal.py`: Members → P_raw vector with WU integer rounding |
| `backfill_ens.py`: 92-day ENS P_raw backfill for historical settlements |
| `calibration/store.py`: SQLite tables for calibration_pairs + platt_models |
| `calibration/platt.py`: PlattCalibrator fit/predict |
| Shadow logging: compute P_raw for every active market, log to shadow table |

### Phase B: Calibration + Edge Calculator

**Duration:** 1-2 weeks

| Task |
|------|
| `calibration/manager.py`: Hierarchical bucket routing + maturity gate |
| `strategy/market_analysis.py`: Full-distribution edge scan with Bootstrap CI |
| `strategy/market_fusion.py`: α-weighted posterior |
| `calibration/drift.py`: Hosmer-Lemeshow + seasonal triggers |
| Fit initial Platt models from backfilled pairs |
| Shadow comparison: v2 Brier score vs v1 Brier score on settled markets |

### Phase C: Execution + Lifecycle

**Duration:** 1-2 weeks

| Task |
|------|
| `execution/opening_hunt.py`: Mode A scan |
| `execution/update_reaction.py`: Mode B scan + exit check |
| `execution/day0_capture.py`: Mode C observation-based |
| `execution/executor.py`: Limit-order-only via CLOB |
| `execution/monitor.py`: Variable-frequency exit trigger checking |
| `strategy/kelly.py`: Per-bin Kelly + dynamic multiplier |
| `strategy/correlation.py`: Exposure checks |
| `riskguard/`: Graduated response |
| **2 weeks paper trading** |

### Phase D: Live

| Gate | Condition |
|------|-----------|
| Start | α=0.5, Quarter-Kelly, $1 minimum, all directions enabled |
| 25 trades | Win rate > 45%? P&L ≥ $0? |
| 50 trades | Calibration bucket review |
| 100 trades | Permutation test |
| 200 trades | Is this a viable business? |

---

## 11. Success Criteria

### After Phase 0

| Metric | Required |
|--------|----------|
| Identified ≥1 strategy with Sharpe > 0.5 on historical data | YES (GO/NO-GO) |
| Quantified favorite-longshot bias magnitude per city | YES |
| Identified optimal timing window (opening vs update) | YES |

### After 100 Live Trades

| Metric | Target |
|--------|--------|
| Win rate | ≥ 50% |
| Cumulative P&L | > $0 |
| Brier score (rolling 30 settled) | < 0.22 |
| Max drawdown (30-day) | < 15% bankroll |
| Trades per day | 5-15 |
| Zero churn/duplicate exits | Architecturally impossible |

---

## 12. Data Collection: Live Agent Pipeline

A dedicated data agent is continuously running, expanding Zeus's data foundation. This pipeline operates independently of the trading system and feeds the calibration layer.

### 12.1 Active Collection Status (Live, Growing)

| Source | Current Volume | Trend | Priority |
|--------|---------------|-------|----------|
| WU PWS daily | **2,000+ city-days** → backfilling to 2024 | Agent actively scraping, coverage expanding rapidly | P0 — settlement authority |
| IEM ASOS daily | 4,410 | Stable, daily append | P0 — truth backbone |
| NOAA GHCND | 6,520 | Stable | P1 — climatology |
| Meteostat hourly | 105,351 | Stable, daily append | P1 — European + hourly |
| Open-Meteo forecast family | 148,915 | Growing (agent backfilling) | P1 — forecast archive |
| Ladder backfill | 53,600 | Agent expanding | P1 — multi-lead verification |

### 12.2 Seasonal Gap Strategy

| Season | Settlements | WU city-days | Risk |
|--------|-------------|-------------|------|
| Winter (DJF) | 811 | expanding rapidly | Low — strong calibration base |
| Spring (MAM) | 457 | expanding rapidly | Low — WU agent backfilling |
| Summer (JJA) | 184 | backfilling to 2024 | **Medium** — coverage improving via backfill |
| Fall (SON) | 182 | backfilling to 2024 | **Medium** — coverage improving via backfill |

WU data is now **2,000+ city-days and growing**, with the agent backfilling to 2024. This changes the calibration landscape significantly: once backfill completes, most city×season buckets will have direct WU ground truth, enabling ASOS→WU offset calibration across all seasons. Until Summer/Fall backfill is verified, Zeus uses IEM ASOS with calibrated offset for Day0 and cluster-level Platt for those seasons.

### 12.3 City Coverage Expansion (38 Cities)

The data agent is actively building observation databases for 38 cities across 6 continents. This is forward-looking — Polymarket currently runs markets on ~10 cities, but the data foundation will be ready when new cities are listed.

**Tier A — Trading-ready (10 cities, multi-source exact truth + 15K forecasts + hourly obs):**
NYC, London, Chicago, Atlanta, Dallas, Miami, Seattle, Los Angeles, San Francisco, Paris

**Tier B — Pipeline established (8 cities, WU connected + some forecasts, accumulating depth):**
Seoul, Shanghai, Shenzhen, Wellington, Munich, Buenos Aires, Houston, Austin

**Tier C — Early stage (3 cities, WU or local source connected, shallow):**
Denver, Hong Kong (HKO non-WU path), Tokyo (expanding)

**Tier D — Not yet started (18 cities, data agent will reach these):**
Ankara, Beijing, Chengdu, Chongqing, Istanbul, Lucknow, Madrid, Mexico City, Milan, Moscow, Sao Paulo, Singapore, Taipei, Tel Aviv, Toronto, Warsaw, Wuhan

**Data agent status (live, continuously updating):**
- **TIGGE**: 16/16 cities complete for 2024-03-01..03-03, expanding to 03-04..03-07 (NYC/Chicago/Seattle landed). True 51-member ENS history.
- **ECMWF Open Data**: 2026-03-30 00z, steps 24/48/72, 16 cities × 50+1 member vectors extracted. Near-real-time ENS bridge.
- **WU**: 3,256 city-days (up from 3,009), JJA now 423 days (up from 381), SON 362. Summer gap closing.
- Forecasts: 171K rows, 5 NWP models, 20 cities
- Hourly obs: 219K+ rows, 13 cities

**Data collected per city:** WU daily observation (backfilling to 2024), NOAA GHCND (where available), Meteostat hourly, Open-Meteo archive. Each new city automatically expands the calibration cluster pool and climatological baseline.

**Architectural note:** Zeus's calibration hierarchy (city → cluster → season → global) means new cities benefit immediately from global/seasonal Platt models even before accumulating city-specific data. A new city with 0 settlements can still trade using Level 3-4 calibration.

### 12.4 Rainstorm Data Assets → Zeus Integration Roadmap

All rainstorm data enters Zeus through strict ETL (unit validation, timestamp reconstruction, contamination rejection). The data agent is still writing to rainstorm.db — migration happens after stabilization.

| Rainstorm Asset | Rows | Zeus Integration Point | Priority |
|-----------------|------|----------------------|----------|
**Signal chain layer:**

| Asset | Rows | Zeus Use | Phase |
|-------|------|---------|-------|
| **TIGGE ENS archive** | growing | True 51-member P_raw for pre-93-day settlements → Platt training weight 1.0. THE path to breaking the 93-day ceiling and getting DJF/JJA/SON Platt models. | Phase B+ |
| **ladder backfill** | 53,600 | ENS bias correction (× 0.7 discount) + forecast bust detection: track how much T-7 → T-1 forecasts shift per city/season, calibrating when EDGE_REVERSAL should fire more aggressively | Phase B+ |
| **forecast_log** | 333K | TIGGE backfill prioritization only (std > 4°F = high priority). NOT for Gaussian P_raw training. | Phase B+ |

**Trading system layer (beyond signal chain):**

| Asset | Rows | Zeus Use | Phase |
|-------|------|---------|-------|
| **token_price_log** | 319K | Market microstructure timing validation. Group by market_slug → price trajectory from open to settlement. Quantify: opening inertia window (how long prices are stale after open), model update reaction speed (price change within 1h of ECMWF update), settlement convergence curve. Directly validates Opening Hunt timing parameters. | Phase C paper trading |
| **forecasts (5 models)** | 171K | Dynamic α calibration: when model X in city Y season Z has MAE > 3°F, do other models do better? Computes per-city per-season model skill ranking → α should be higher where ECMWF historically outperforms, lower where it doesn't. | Phase B/D |
| **observations** | 238K | Temperature persistence model: today's high vs tomorrow's high autocorrelation by city/season. When ENS predicts a 10°F drop but historical persistence says that only happens 5% of the time, flag as "ENS anomalous forecast" → reduce α or widen CI. Independent reality check on ENS. | Phase D+ |
| **WU daily** | 3,009 | Two uses: (a) ASOS→WU offset per city/season for Day0, (b) Settlement precision audit — cross-validate WU recorded value vs Polymarket winning bin. Any inconsistency = potential edge calculation error or settlement rule misunderstanding. Audit can run NOW. | NOW + Phase C+ |
| **calibration_records.jsonl** | 295 MB | Extract raw (forecast_value, actual_value, city, date) pairs. Discard Rainstorm's calibration conclusions. Re-process with Zeus math if format contains usable forecast-actual tuples. Needs structure inspection first. | Phase B (after format validation) |

**Integration principle:** Each asset enhances a specific step in the signal chain. None replaces ENS member counting — they make it more accurate (bias correction), extend its historical reach (Gaussian approx + TIGGE), or improve downstream decisions (Day0 curves, α calibration).

### 12.5 New Sources Under Exploration

The data agent autonomously discovers and evaluates new data sources:

- **KWEA (NWS warnings/alerts)**: For the NWS_EXTREME exit trigger
- **NOAA NDFD probes**: National Digital Forecast Database grid point data
- **Weatherbit API**: Additional international city coverage
- **ECMWF direct archive**: If Open-Meteo historical ensemble proves insufficient

Any new source the agent validates is added to the calibration pipeline without requiring code changes to the trading engine — the agent writes to `zeus.db` tables that the calibration manager reads.

---

## 13. System Maturity Model

Zeus is a **trade-centric position management system**, not a signal engine. Engineering investment in entry signals (ENS → P_raw → Platt → edge) was 90% of Phase A effort. But Rainstorm's 14-month history shows value is 90% in position lifecycle — holding and exiting correctly. 14 of Rainstorm's 20 best designs are about lifecycle management, not signal quality.

### The Interface Bug Problem

Two independent code reviews (readiness score 2-4/10) found 10 P0 bugs. ALL were interface bugs, not logic bugs. Each module's internal math was correct — Platt's sigmoid, Kelly's formula, bootstrap CI. But semantic context was lost at module boundaries:
- Probability space flipped twice (buy_no direction lost between BinEdge and executor)
- Time context ignored (multi-day forecast always used day-0 hours)
- Processing state not tracked (harvester re-processed already-settled markets)
- Decision-time data not preserved (exit evaluated against stale entry-time posterior)

**Root cause: Zeus has no Position object that carries its identity across the lifecycle.** A trade exists as 7 different representations in 7 modules. Direction, probability space, entry method, and decision-time snapshot are re-derived (and often derived wrong) instead of being carried by the trade itself.

**Solution: Position as self-aware entity.** The Position object must own: its direction, its probability space convention, its entry method, its decision-time data snapshot, and its exit strategy. When Position carries this context, interface bugs become structurally impossible — executor doesn't need to guess the probability space because Position tells it.

### Maturity Levels (20 stages to stable live trading)

| # | Capability | Category | Phase D Required? |
|---|-----------|----------|-------------------|
| 1 | Chain-first reconciliation | Lifecycle | YES |
| 2 | Administrative exit reasons (ghost/phantom excluded from P&L) | Lifecycle | YES |
| 3 | void_position vs close_position (unknown price → pnl=0) | Lifecycle | YES |
| 4 | close_position closes ALL same-token entries | Lifecycle | YES |
| 5 | Buy-no independent exit path (consecutive cycles, cal_std threshold) | Lifecycle | YES |
| 6 | City-aware near-settlement window (peak_hour driven) | Lifecycle | YES |
| 7 | Source-divergence penalty (> 3°F → confidence reduction) | Signal | NO |
| 8 | Execution-time edge recheck (re-fetch best_ask before order) | Execution | YES |
| 9 | NWS pooling guard | Signal | N/A |
| 10 | Coverage gate 70% (partial bin → no trade) | Signal | YES |
| 11 | Bayesian prior-blended std | Calibration | NO (Platt covers) |
| 12 | Fat-tail penalty on confidence | Calibration | NO |
| 13 | Sell-side price (best_bid) for exit evaluation | Execution | YES |
| 14 | Dynamic force-hold threshold (confidence-scaled) | Lifecycle | NO |
| 15 | Stale order cleanup per cycle | Execution | YES |
| 16 | Orphan position quarantine (low confidence + 48h timeout) | Lifecycle | YES |
| 17 | Wallet delta circuit breaker (real balance, not estimated P&L) | Risk | YES |
| 18 | 95% exposure gate (monitor-only, NOT force-reduce) | Risk | YES |
| 19 | Chronicler bootstrap deadlock prevention | Risk | YES |
| 20 | Multi-factor confidence (5-factor weighted geometric mean) | Signal | NO |

**Phase D GO requires ≥ 15 of 20 levels implemented.** Count ❌ to measure distance.

### Implementation Path (Blueprint v2 Phases)

The v1 implementation path (Phase 0/A/B/C/D) was signal-first. The v2 path is Position-first:

| Phase | Focus | Deliverables |
|-------|-------|-------------|
| **2A** | Position Object + Lifecycle | Position dataclass, close/void/admin_exit, evaluate_exit with buy_no/buy_yes paths, PENDING_TRACKED status |
| **2B** | CycleRunner Refactor | Extract logic from opening_hunt/update_reaction/day0_capture into pure orchestrator + called modules. Fix P0-1 (forecast slicing) and P0-3 (GFS member count) during extraction. |
| **2C** | Decision Chain + NoTradeCase | Artifact schema, full decision recording, NoTradeCase with rejection_stage, fix P0-7 (harvester dedup) via decision_snapshot_id, fix P0-8 (RiskGuard blind) via decision artifacts |
| **2D** | Chain Reconciliation | Port sync_engine_v2 three rules, QUARANTINED status, truth hierarchy (chain > chronicler > portfolio), fix P0-5 (pending not tracked) |
| **2E** | Observability | Status summary every cycle, control plane for runtime commands, per-strategy P&L tracking, edge compression monitoring |

After Phase 2E: restart paper trading with new architecture. 2 weeks of clean attributed data before considering live.

### Day0 is the Terminal Phase of Every Position

Every position eventually enters the Day0 window. Day0 is not a third discovery mode — it is the convergence point of the entire trading lifecycle:

- Opening Hunt is the seed (find mispriced bins)
- Update Reaction is tending (confirm edge persists)
- Day0 is the harvest (observation confirms or denies, with near-certainty)

Three Day0 outcomes, in order of execution priority:
1. Observation confirms your position → settlement capture locks profit
2. Observation denies your position → DAY0_OBSERVATION_REVERSAL exits immediately
3. Observation uncertain (pre-peak) → blended probability with elevated α

### Exit Decisions Need the Same Rigor as Entry Decisions

The false EDGE_REVERSAL bug revealed a structural asymmetry: entry goes through Platt calibration + bootstrap CI + FDR filtering + Kelly sizing (4 validation layers). Exit went through a single "refresh ENS and check direction" step (1 validation layer). The 8-layer anti-churn defense is not "adding protection" — it is raising exit validation to the same standard as entry validation.

**Default behavior when uncertain: HOLD to settlement.** The entry decision was validated; the exit decision must clear an equally high bar to override it.

---

## 14. Phase E: Data-Driven Calibration (Post Phase D Live)

**Zeus sits on 1.35M records and uses 56K (4.1%). This is not a design decision — it is the exact boundary of what was explicitly instructed. Everything beyond it requires inference chains that Claude Code does not make unprompted.**

### 14.0 The SQL Conversion Principle

**ALL data enters Zeus through `zeus.db` via ETL scripts. No module reads raw files, rainstorm.db, or TIGGE JSON directly at runtime.** ETL validates units, reconstructs timestamps, rejects contaminated rows, and writes to Zeus-schema tables.

This is not optional hygiene — it is architectural. If Zeus reads rainstorm.db directly, it inherits Rainstorm's mixed-unit `_f` column naming, its contaminated pre-epoch-zero data, and its schema that Zeus's type system cannot guard.

### 14.1 Phase E0: TIGGE → Full-Season Platt (Largest Signal Improvement)

**DO BEFORE Phase D live.** This is the only data task that blocks live deployment.

```
Source: 5,026 TIGGE city-date vectors (38 cities, 282 dates, all 4 seasons)
Target: zeus.db:calibration_pairs + ensemble_snapshots
Method: ETL reads member JSON → rounds to int → counts per bin → matches against settlements → generates (P_raw, outcome) pairs

Expected yield:
  5,026 vectors × match rate vs 1,643 settlements ≈ 1,000-2,000 matched
  × 11 bins = 11,000-22,000 new calibration pairs
  Current: 1,126 pairs, 6 MAM Platt models
  After: 12,000-23,000 pairs, up to 24 models (all clusters × all seasons)

Per-season bucket estimate:
  DJF: 4,032 vectors → ~8,800 pairs → ~1,467/bucket → Level 1
  MAM: 499 + 562 existing → ~2,000/bucket → Level 1
  JJA: 391 → ~5,500 pairs → ~917/bucket → Level 1
  SON: 104 → ~1,430 pairs → ~238/bucket → standard Platt
```

### 14.2 Phase E1: Observation Infrastructure (Day0 Accuracy)

```
ETL 1: ASOS→WU Offset (P1)
  Source: rainstorm.db observations (wu_daily_observed 4,136 + iem_asos 4,410)
  Target: zeus.db:asos_wu_offsets(city, season, offset, n_samples)
  Method: match WU and ASOS on same city+date → offset = mean(WU - ASOS)
  Impact: Day0 observation accuracy. Without this, settlement capture may lock
          on wrong temperatures.

ETL 2: Diurnal Curves (P2)
  Source: rainstorm.db observations (hourly: 219,519 rows)
  Target: zeus.db:diurnal_curves(city, season, local_hour, avg_temp, std_temp)
  Method: convert obs_hour to LOCAL time (timezone!), group by city × season × hour
  Impact: Day0 post-peak detection. Replaces hardcoded peak_hour with per-city distribution.
  WARNING: timezone conversion is critical. If meteostat stores UTC, the diurnal curve
           shifts by the city's UTC offset. Validate before use.
```

### 14.3 Phase E2: Market Intelligence (Timing Calibration)

```
ETL 3: Token Price History (P2)
  Source: rainstorm.db token_price_log (365,444 rows)
  Target: zeus.db:market_price_history(market_slug, token_id, price, recorded_at,
          hours_since_open, hours_to_resolution)
  Method: JOIN token_price_log → market_events via token_id to recover bin labels
  Impact: validates Opening Hunt timing thesis, vig analysis, settlement convergence speed

  Key analysis after import:
  - Price change magnitude by hours_since_open → validates 6-24h window
  - Vig (sum YES prices) time series → when is vig > 1.02?
  - Winning bin price trajectory → when does it reach $0.90? (Day0 timing)

  CRITICAL INSIGHT: 365K prices / 1,643 settlements = 222 price snapshots per settlement.
  Zeus currently uses 0 of these 222 intermediate prices. The market speaks 222 times
  between entry and settlement. Zeus is deaf to all of them.

ETL 4: Forecast Volatility (P3)
  Source: rainstorm.db forecast_log (337,227 rows)
  Target: zeus.db:forecast_volatility(city, target_date, lead_bucket, volatility)
  Method: for each city-date, compute std of ensemble_high_f across all snapshots
  Impact: identifies "forecast bust" opportunities where model changed dramatically
          but market hasn't adjusted
```

### 14.4 Phase E3: Model Skill Analysis (α Calibration)

```
ETL 5: Historical Forecasts (P2)
  Source: rainstorm.db forecasts (171,003 rows, 5 NWP models)
  Target: zeus.db:historical_forecasts + model_skill(city, season, source, mae, bias)
  Method: match forecasts against settlements → compute per-model per-city MAE
  Impact: replaces hardcoded α adjustments with data-driven per-city model skill ranking

  Example output:
  NYC DJF: ICON MAE=2.1°F (best), ECMWF MAE=3.6°F (worst) → α lower for NYC DJF
  London MAM: ECMWF MAE=0.8°C (best), GFS MAE=1.4°C → α higher for London MAM

ETL 6: Underdispersion Quantification (P2, after TIGGE ETL)
  Source: TIGGE ensemble snapshots (matched against settlements)
  Target: zeus.db:underdispersion(city, season, lead_days, ratio)
  Method: ratio = ens_spread / actual_error. Expected ~0.75-0.85.
  Impact: validates SIGMA_INSTRUMENT (0.5°F/0.28°C). If underdispersion is worse
          than assumed, MC noise parameter needs adjustment.

ETL 7: Temperature Persistence (P3)
  Source: rainstorm.db daily observations
  Target: zeus.db:temp_persistence(city, season, delta_bucket, frequency, reversion)
  Impact: anomaly detection. When ENS predicts 10°F drop but persistence says
          that only happens 5% of the time → flag and widen CI.
```

### 14.5 Phase E4: Replay Infrastructure (Out-of-Sample Validation)

The 1.35M records TOGETHER constitute a market simulator. Individually each dataset has limited value. Combined, they enable time-travel replay:

```
For any historical date:
  1. TIGGE → what ENS predicted (51 members)
  2. forecast_log → how prediction evolved over time
  3. token_price_log → what the market priced at each moment
  4. hourly observations → what actually happened hour-by-hour
  5. settlements → what the final outcome was

Replay ≠ backtest. Backtest knows the outcome and checks "what if."
Replay doesn't know the outcome, strictly enforces available_at <= decision_time,
then checks "was my decision correct?"

Replay is the ONLY way to get out-of-sample validation without waiting for live data.
It requires all 5 datasets in SQL, time-aligned, with proper available_at constraints.
```

### 14.6 Hardcoded Constants This Data Replaces

| Constant | Current Source | Data to Replace With | ETL | Priority |
|----------|---------------|---------------------|-----|----------|
| α base values {1:0.65, 2:0.55, 3:0.40, 4:0.25} | Theoretical | Model vs Market Brier per calibration level | E3 ETL 5 | After 200+ settlements |
| exit CI scaling 0.5/0.3 | New design | Optimal from historical edge noise | E0 pairs | After 200+ exits |
| SIGMA_INSTRUMENT 0.5/0.28 | ASOS spec | TIGGE underdispersion measurement | E3 ETL 6 | P2 |
| diurnal rise 1.5°F/h, cap 12°F | Rainstorm IEM fit | Fresh per-city fit from hourly obs | E1 ETL 2 | P2 |
| JSD thresholds 0.02/0.08 | Theoretical | TIGGE vs GFS historical agreement | E3 | P3 |
| Opening Hunt 30-min schedule | Microstructure doc | Token price trajectory analysis | E2 ETL 3 | P2 |
| Day0 max_entry_price 0.85 | Rainstorm config | Settlement convergence speed | E2 ETL 3 | P4 |
| near_settlement_hours 4.0 | Rainstorm experience | Per-city (peak_hour driven) | E1 ETL 2 | P2 |

### 14.7 Hidden Insight: The 85% Gap

Rainstorm's chronicler.db has 1,381 cycle configs but only 204 engine trades. **Trading rate = 14.8%.** 85.2% of cycles produced no trade — but Rainstorm had no NoTradeCase to explain why.

Zeus's paper trading shows ~55% trading rate. The difference may be:
- Rainstorm's bugs suppressed trading (GFS conflict veto never fired, etc.)
- or Rainstorm's higher edge threshold filtered more correctly
- We can't tell without NoTradeCase data

Zeus's NoTradeCase + rejection_stage, once properly wired, will for the first time quantify "alpha lost to system problems" vs "markets where no edge exists." This may be a larger alpha source than better signals.

---

## 15. Zeus Evolution Roadmap (Phase F+)

Four advanced capabilities, each gated by specific data/performance prerequisites.

### 13.1 HRRR Overlay for Coastal Stations (Phase E)

**Prerequisite:** Day0 signal live and validated with ASOS observations.

**Problem:** ECMWF 9km grid smoothes sea-land boundaries. At KLGA, KLAX, KMIA, sea breeze onset can swing temperature 5-10°F in 30 minutes — a discontinuity the 9km grid cannot resolve.

**Implementation:** Add HRRR as weighted "super-members" to the ensemble, NOT as a separate μ:

```python
def hrrr_augmented_ensemble(ecmwf_members: np.ndarray,
                             hrrr_forecast: float,
                             hrrr_weight: int = 5) -> np.ndarray:
    """Add HRRR as 'super-members' to preserve ensemble framework.

    HRRR gets weight=5 (≈5 ENS members) based on RMSE advantage
    at T+6-18h: HRRR 1.0°C vs ECMWF 1.5°C.
    """
    return np.concatenate([ecmwf_members, np.full(hrrr_weight, hrrr_forecast)])
    # 56 effective members: 51 ECMWF + 5 HRRR pseudo-members
```

**Why not HRRR μ + ECMWF σ:** These come from different models with different error structures. Mixing them creates a mathematically incoherent distribution. The "super-member" approach preserves ensemble consistency.

**Scope:** Day0/Day1 only, CONUS cities only. Via Herbie library (AWS S3).

### 13.2 Partial Synthetic NO (Phase D+)

**Prerequisite:** 50+ live trades completed, vig monitoring operational.

**Problem:** Buying NO on an overpriced shoulder (e.g., $0.95 to earn $0.05) is capital-inefficient and carries tail risk.

**Implementation:** When vig > 1.05, instead of buying NO, buy YES on the 3-4 most likely OTHER bins:

```python
def partial_synthetic_no(analysis: MarketAnalysis, target_bin: int) -> list[BinEdge]:
    """Construct synthetic NO via YES on competing bins."""
    if analysis.p_market[target_bin] <= analysis.p_cal[target_bin]:
        return []  # not overpriced

    others = sorted(
        [(i, analysis.p_cal[i]) for i in range(len(analysis.bins)) if i != target_bin],
        key=lambda x: -x[1]
    )[:4]

    # Only if combined probability > 70%
    if sum(p for _, p in others) < 0.70:
        return []

    return [BinEdge(bin=analysis.bins[i], direction="buy_yes", ...)
            for i, _ in others]
```

**Why not full 10-bin arbitrage:** 10 simultaneous limit orders have cumulative slippage that likely exceeds the vig. Partial (3-4 bins) captures most of the value with manageable execution risk.

### 13.3 XGBoost Residual Prediction (Phase E+, n > 1000)

**Prerequisite:** 1,000+ calibration pairs per bucket (estimated 12-18 months of operation). Platt residuals show systematic non-sigmoid pattern.

**Problem:** Platt Scaling is 1D (maps P_raw → P_cal). It cannot capture conditional biases like "ECMWF is only wrong when it's raining and the wind is from the North."

**Implementation (deferred):** Train XGBoost to predict `settlement_temp - ecmwf_mean_temp` using auxiliary features (wind direction, cloud cover, snowpack). Apply prediction as a **per-member perturbation** (not uniform shift), preserving ensemble structure:

```python
# WRONG: uniform shift destroys member diversity
members_shifted = ecmwf_members - 2.5  # BAD

# RIGHT: per-member conditional adjustment
for i, member in enumerate(ecmwf_members):
    member_features = extract_features(member)  # wind, cloud, etc.
    residual = xgboost_model.predict(member_features)
    ecmwf_members[i] += residual  # each member gets its own adjustment
```

**Why deferred:**
1. n < 500 per bucket = overfitting guaranteed
2. Wind/cloud/snowpack are already implicitly in ENS members (each is a full atmospheric simulation)
3. The quantitative research doc's principle: "simpler methods > complex methods at current sample sizes"
4. If Platt residuals at n=1000 are random (no pattern), XGBoost adds nothing

### 13.4 Full Vig Arbitrage (Phase E+)

**Prerequisite:** Partial synthetic NO validated. Execution engine supports atomic multi-leg orders.

**Implementation:** When vig > 1.08, buy YES on ALL other bins to construct a risk-free synthetic NO. Requires solving the multi-leg execution problem (all orders must fill or none).

---

## 14. Reference Documents

Zeus's design is derived from four independent research documents and the ongoing data agent's output. These are the authoritative sources — code decisions must be traceable to them. These same four documents are listed at the top of this spec as **Design Authority**.

### 14.1 Core Research (read before writing ANY code)

| Document | Location | Role | Key Sections Referenced in Spec |
|----------|----------|------|-------------------------------|
| **Quantitative Research** | `project level docs/rainstorm_quantitative_research.md` | Data sources, calibration math (Platt vs Isotonic), Kelly derivation, sample size requirements, overfitting prevention | §2.1 Platt Scaling rationale → Spec §3.1; §2.3 Kelly derivation → Spec §5.1; §3.2 過拟合 vs 真实 edge → Spec §8.2 |
| **Architecture Blueprint** | `project level docs/rainstorm_architecture_blueprint.md` | Agent topology, risk guard independence, cost layering, failure modes, deployment phases | §5 RiskGuard independence → Spec §7; §8 Failure modes → Spec risk design; §7 Deployment phases → Spec §10 |
| **Market Microstructure** | `project level docs/rainstorm_market_microstructure.md` | Participant typology, favorite-longshot bias, opening inertia, bin boundary discretization, entry timing, liquidity tiers | §2.1 Favorite-longshot → Spec §0.1 Edge 1; §4.1 Entry timing → Spec §6.2 Mode A; §4.3 buy_yes vs buy_no → Spec §5.3 direction agnostic |
| **Statistical Methodology** | `project level docs/rainstorm_statistical_methodology.md` | Sampling traps, three σ separation, instrument noise, multi-source correlation, small-sample pitfalls, FDR control, data versioning | §1.1 Four timestamps → Spec §9.2 zeus.db schema; §1.2 Instrument noise MC → Spec §2.1 p_raw_vector; §2.2 Three σ → Spec §3.1 bootstrap_params + §4.1 double bootstrap; §3.1 FDR → Spec §4.4; §4.3 Data versioning → Spec §9.2 |

### 14.2 Data Agent Outputs (continuously updated)

The data backfill agent produces operational documents in `project level docs/data/`. Key references:

| Document | Role |
|----------|------|
| `DATA_SUPPLY_SCOREBOARD.md` | Live inventory: WU now 1,809+ city-days (2024-06 to 2026-03), settlements 1,634 with 1,627 WU-sourced exact truth, forecast source breakdown |
| `EXACT_TRUTH_DEPTH_AUDIT_20260330.md` | Resolved: settlement truth depth is 1,631/1,634 after IEM/NOAA promotion. Not a data gap — was a policy gap. |
| `DATA_SUPPLY_REMEDIATION_PLAN.md` | Comprehensive gap analysis and remediation roadmap across all data sources |
| `FORECAST_LADDER_SCOREBOARD.md` | Multi-lead forecast vs settlement coverage per city |
| `RAINSTORM_REBUILD_MASTER_RECORD_20260329.md` | Complete rebuild audit trail |
| `WU_OBSERVATION_AUDIT_20260330.md` | WU data quality, ASOS→WU offset analysis |
| `ENTRY_EXIT_MATH_VALIDATION_20260330.md` | Validation of edge and exit mathematics |

### 14.3 Debate Results

The Rainstorm adversarial debate process produced findings that directly shaped Zeus's edge thesis:

| Document | Key Insight |
|----------|------------|
| `rainstorm debate results/results/DISCOVERED_ISSUES.md` | 21 issues found in v1; several (CHURN_DUPLICATE, mixed-unit bugs) are architecturally impossible in Zeus |
| `rainstorm debate results/results/RISK_REGISTER_R2R5.md` | Risk classification that informed RiskGuard graduated response design |
| `rainstorm debate results/findings/SESSION_FINDINGS_20260328.md` | Defender's concession on shoulder bin buy_no — the origin of the "structural edge, not predictive edge" insight |
| `methodology/RAINSTORM_DEBATE_LESSONS.md` | Process lessons for future adversarial review of Zeus |

### 14.4 How to Use These References

During implementation:
- **Before writing a module**, check the corresponding research doc section. If your implementation contradicts the research doc's recommendation, document WHY in a code comment.
- **Data questions** → check DATA_SUPPLY_SCOREBOARD.md first. The agent updates it continuously.
- **Mathematical derivations** → trace back to rainstorm_quantitative_research.md. Every formula in Zeus should have a citation to its source in that document.
- **Edge strategy decisions** → trace back to rainstorm_market_microstructure.md. "Why this entry timing?" and "Why this bin type?" should always have answers rooted in that analysis.
- **Statistical methodology** → trace back to rainstorm_statistical_methodology.md. Instrument noise handling, bootstrap design, FDR control, DB schema time constraints, and CI construction all originate there. If you're unsure whether a statistical choice is correct, this document is the arbiter.
