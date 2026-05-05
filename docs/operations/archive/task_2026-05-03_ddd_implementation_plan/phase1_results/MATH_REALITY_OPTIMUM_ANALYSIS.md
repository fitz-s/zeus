# DDD Math+Reality Optimum Analysis

Created: 2026-05-03
Authority: Operator request 2026-05-03; feeds σ-band/floor/curve redesign decisions
Status: RESEARCH ONLY (no code/DB changes)

## §0 Executive Summary

The objective of the Data Density Discount (DDD) is to protect the live trading system from observation gaps without excessively penalizing routine station behavior already priced into the Platt calibration models. Following the operator's structural correction (`fire if cov < floor` rather than subtracting σ), this analysis evaluates the optimal mathematical and practical forms for the trigger, floor, curve, and σ-usage.

**Q1. Trigger Formula**: We recommend a **Two-Rail (kill + discount)** trigger. 
- *Rail 1 (Absolute Kill)*: Hard halt if `directional_cov < 0.35` (>65% missing). This prevents statistically indefensible extrapolation.
- *Rail 2 (Relative Discount)*: Continuous linear shortfall discount `max(0, floor_hardened - cov)` capped at 9%. This handles partial drops smoothly without step-function cliffs.

**Q2. Floor Selection**: We recommend a **Hardened Data-Derived Floor** per city/track.
- *Method*: Compute the `p05` of the train set's directional coverage.
- *Constraint*: `floor = max(p05, 0.35)`.
- *Policy Overrides*: Retain explicit overrides ONLY for documented physical realities (e.g., Lagos archive gaps = 0.45). Do not override for asymmetric loss preference (Denver). The 1% vs 5% FP tradeoff shows `p05` is already highly conservative (40/46 cities converge to 1.0 floor).

**Q3. Discount Curve Shape**: We recommend a **Continuous Linear Curve**.
- Phase 1 v2 data shows exact 0 shortfall has a mean error of ~0.76 (HIGH) / 0.70 (LOW). The only bins statistically worse are the tail bins (>0.50 shortfall). 
- A 5-segment curve is over-parameterized. A continuous formula `DDD = min(0.09, α * shortfall)` prevents edge-case gaming and boundary cliffs.

**Q4. σ Usage**: We recommend using σ as a **Day-0 Circuit Breaker diagnostic**, NOT in the historical shortfall calculation.
- The 24-hour Poisson noise for healthy stations naturally creates ±1/24 fluctuations. Using σ in the trigger conflates "noisy day" with "catastrophic drop". σ should be tracked separately to detect regime shifts (e.g., vendor changes).

**Q5. Outage vs. Noise Distinction**: We recommend a **Two-Component Mixture view**.
- Small fluctuations (shortfalls 0.05 - 0.20) are treated as noise/routine-thinness and absorbed by the linear curve (modest discount). 
- Complete outages (cov=0) or severe drops (cov < 0.35) hit the absolute kill rail. This naturally segments Lagos-style intermittent total outages from Jakarta-style chronic partial thinness.

---

## §1 Empirical Landscape

Key findings from Phase 1 v2 relevant to the DDD redesign:

| Finding | Value / Insight | Relevance |
|---|---|---|
| **Pervasive 1.0 Floors** | 40 of 46 cities default to a `p05` floor of 1.000. | Q2 (Floor): The `p05` metric is highly aggressive, minimizing FP risk. |
| **Lagos Reality** | `p05` is 0.4286. Lagos had 1 zero-coverage day in train, 23 in test. | Q2, Q5: Intermittent total outage pattern requires a specific approach; a 1.0 floor would starve it. |
| **Indistinguishable Bins** | Bootstrap CIs show adjacent shortfall bins [0.10, 0.20), [0.20, 0.30), [0.30, 0.50) are statistically indistinguishable in error rate. | Q3 (Curve): Over-segmented curves are empirically unsupported. |
| **Tail Severity** | Only the [0.50, 1.0] shortfall bin shows a distinct jump in mean error (0.8599 HIGH / 0.9070 LOW). | Q1, Q3: Confirms the need for a severe penalty or kill-switch at extreme shortfalls. |
| **Small Sample Constraint** | 96/100 city/metric pairs found a stability point N*. | Q1: Supports applying a penalty multiplier for under-sampled combinations. |
| **Radius Expansion** | 347 (city, metric, season) entries need a radius > 5 hours. | Context: Temporal observation distributions are highly variable. |

---

## §2 Math Frame

We formalize the DDD design as an optimization problem:

**Decision Variable**: D(c, t, h) ∈ [0, D_max], the discount applied for city c, track t, given coverage history h.

**Loss Function**: Asymmetric.
- L_FP: Loss from over-discounting (False Positive). Cost = foregone alpha, starvation of profitable Kelly entries.
- L_FN: Loss from under-discounting (False Negative) during a true outage. Cost = high probability of ruin (betting on noise).
- L_FN >> L_FP.

**Optimization Objective**:
min_D E[L_FN * 1_outage + L_FP * 1_normal]

**Constraint**: The expected EV impact per city must be bounded by ε under normal conditions to prevent chronic under-sizing.

**Bayesian View**: The coverage cov_d is a signal emitted by a hidden Markov state S_d ∈ {Normal, Outage}. Platt models internalize P(Y | X, S=Normal). DDD's job is to estimate P(S=Outage | cov_d) and adjust the Kelly fraction accordingly.

---

## §3 Reality Constraints

Any optimal mathematical design must survive these production constraints:
1. **Discrete Coverage**: Coverage is not a continuous real number. It exists in discrete quanta depending on the window radius (e.g., {0, 1/7, 2/7, ..., 1.0} for a ±3 hour radius). Thresholds must not sit exactly on these quanta to avoid floating-point toggle issues.
2. **Oracle Penalty Composition**: DDD must compose cleanly with the existing `mismatch_rate`. The current logic `oracle_error_rate = max(mismatch_rate, DDD)` must be preserved. DDD cannot exceed 0.09 (9%) to avoid auto-blacklisting via the existing categorizer, EXCEPT via a separate explicit hard-kill.
3. **Live Trade Frequency**: With hundreds of trades per day, a systematic FP discount of even 2% compounds into massive volume loss.
4. **Data Tier Fallbacks**: WU is primary. Ogimet/NOAA are fallbacks. DDD evaluates the *settlement source's* availability, not just WU.

---

## §4 Q1 — Trigger Formula

**Current**: `fire if cov < floor`

**Alternative 1: Continuous Penalty (No hard threshold)**
- *Formula*: D = max(0, (floor - cov)/floor) * D_max
- *Pros*: No edge-case cliffs. Graceful degradation.
- *Cons*: Penalizes even minor 1-hour drops. Can create "boiled frog" if floor degrades.

**Alternative 2: Two-Rail (Kill + Discount) [RECOMMENDED]**
- *Formula*:
  - Rail 1 (Absolute): `If cov < 0.35 AND window > 50% elapsed -> KILL (Halt entries)`
  - Rail 2 (Relative): `If cov < floor -> D = min(D_max, α * (floor - cov))`
- *Pros*: Separates "statistically useless" (Rail 1) from "partially degraded" (Rail 2). Safely handles Lagos (frequent 0 cov -> Kill) and Jakarta (frequent 0.8 cov -> mild discount).
- *Cons*: Requires tuning α.

**Alternative 3: Bayesian Posterior**
- *Formula*: P(O_d | c_d) = [P(c_d | O)P(O)] / [P(c_d | O)P(O) + P(c_d | N)P(N)], fire if P > τ.
- *Pros*: Mathematically pure.
- *Cons*: Computationally expensive for Day-0 live paths. Requires strong priors on outage probability which we lack.

**Evaluation Matrix**:

| Design | Precision (FP) | Recall (FN) | Discrete-Friendly | Interpretability |
|---|---|---|---|---|
| Current | Medium | Medium | Yes | High |
| Continuous | Low (Many FPs) | High | Yes | Medium |
| **Two-Rail** | **High** | **High** | **Yes** | **High** |
| Bayesian | High | High | No | Low |

**Recommendation**: Adopt the **Two-Rail (Kill + Discount)**. It provides the absolute ruin-protection of a hard cutoff while allowing smooth Kelly-sizing for partial degradation.

---

## §5 Q2 — Floor Selection Method

**Current**: floor = max(p05_train, 0.35) with overrides.

**1. p05 vs p01**:
Phase 1 v2 shows that at `p05`, 40 of 46 cities default to a floor of 1.000. Moving to `p01` would relax this, but since most cities have 0 zero-coverage days in train (see v2 summary), the empirical `p01` for healthy cities is likely still 1.000. For noisy cities, `p01` risks capturing true outages as "normal". Stick to `p05`.

**2. Data-Derived vs Policy-Derived**:
Floors MUST be data-derived by default. Policy overrides should ONLY apply for documented *physical infrastructure realities* (e.g., Lagos archive gaps = 0.45). Overriding for *asymmetric loss preference* (e.g., Denver 0.85) is wrong; asymmetric loss should be handled in the sizing/Kelly layer, not by lying about the physical baseline.

**3. City Archetype Adaptation**:
- *Lagos Archetype (Intermittent Total)*: Train `p05` is 0.4286. 23 zero-cov days in test. The 0.35 absolute kill rail handles the 0-cov days. The relative rail (floor 0.4286) applies mild discounts on the 0.40 days.
- *Jakarta Archetype (Chronic Partial)*: Train `p05` is 0.7143. 0 zero-cov days. The relative rail correctly identifies 0.60 as anomalous and discounts, while ignoring 0.75 as normal.

**Recommendation**: 
floor = max(p05_train_directional_cov, 0.35). 
Remove the Denver override. Keep the Lagos override ONLY if physical vendor limitations prevent achieving the 0.4286 empirical `p05`.

---

## §6 Q3 — Discount Curve Shape

**Current**: 5-segment linear (0% / 0->2 / 2->5 / 5->8 / 9% cap).

**Phase 1 v2 Evidence**: The bootstrap CIs (Section C3) show that adjacent bins `[0.10, 0.20)`, `[0.20, 0.30)`, and `[0.30, 0.50)` are statistically indistinguishable. The only significant jump in error rate occurs at `[0.50, 1.0]`. Therefore, a 5-segment curve implies a false precision that the data does not support.

**Alternative 1: Step Function**
- *Formula*: `If shortfall > 0 -> D = 0.05`
- *Cons*: Cliff edge. Shortfall of 0.01 vs 0.49 get same penalty.

**Alternative 2: Two-Step**
- *Formula*: `If 0 < shortfall < 0.5 -> D = 0.04; If shortfall >= 0.5 -> D = 0.09`
- *Cons*: Still has cliffs.

**Alternative 3: Continuous Linear [RECOMMENDED]**
- *Formula*: D = min(D_max, α * shortfall)
- *Parameters*: D_max = 0.09, α = 0.20 (so a 0.45 shortfall hits the 9% cap).
- *Pros*: Smooth. Reflects that risk increases with sparsity, without pretending we know exact bin boundaries.

**Alternative 4: Continuous Nonlinear**
- *Formula*: D = D_max * (1 - exp(-shortfall / τ))
- *Cons*: Too complex. Hard to explain to operators.

**Recommendation**: **Continuous Linear**. D = min(0.09, 0.20 * shortfall). If shortfall = 0.10, D = 2%. If shortfall = 0.30, D = 6%.

---

## §7 Q4 — σ Usage

The structural fix moved σ out of the trigger (`fire if cov < floor`).

**Why σ failed in the trigger**:
For a healthy station (cov=1.0), missing 1 hour out of 24 is a 4% drop. By Poisson statistics, this is ~0.5σ noise. Subtracting σ_90 from the floor meant the trigger required a massive, multi-hour drop to fire. It made the system blind to severe (but not total) degradation.

**Recommended Roles for σ**:
1. **Diagnostic Regime Shift Detector**: Track σ_recent_7d vs σ_historical_90d. A spike in σ_recent indicates the vendor connection is flapping. This is an alert metric, not a trade-sizing metric.
2. **Small-Sample N* Scaling (Defensible)**: For cities where N < N* (the stability threshold from p2_5), uncertainty is higher. Instead of a flat multiplier, we could use σ_historical to scale the penalty: high-variance small-sample cities get penalized more than low-variance small-sample cities.
3. **DO NOT use σ as a posterior weight**. Platt calibration already internalizes the variance of the input features.

**Recommendation**: Keep σ purely as a diagnostic telemetry metric. Do not wire it back into the critical path of the sizing engine.

---

## §8 Q5 — Outage vs Noise Distinction

The core issue: Lagos had 23 zero-coverage days, but the train σ didn't capture this because σ measures continuous variance, not discrete jump processes.

**How to distinguish:**
1. **Two-Component Mixture Model [RECOMMENDED]**:
   Treat the station state S_t as a mixture:
   - State 0 (Outage): cov = 0 (probability p_outage)
   - State 1 (Normal): cov ~ N(μ, σ^2) (probability 1 - p_outage)
   The absolute hard-kill rail (cov < 0.35) handles State 0. The relative discount rail handles variance within State 1.
2. **Rolling Outage Rate**:
   Track R = (days with cov=0) / (last 30 days). If R > 0.10, the station is fundamentally unreliable. 
3. **Source-Tier Failover**:
   A WU outage should trigger an Ogimet fallback BEFORE it triggers a discount. The discount should apply to the *post-fallback* coverage.

**Recommendation**: The **Two-Rail Trigger** (Q1) effectively implements the Two-Component Mixture model. Rail 1 catches the State 0 discrete jumps. Rail 2 catches the State 1 continuous degradation.

---

## §9 Synthesis: Integrated Recommended Design

Based on math and empirical reality, here is the optimal end-to-end design:

**1. Floor Definition (Pre-computed per city/track)**
```python
floor_empirical = p05(train_directional_coverage)
city_floor = max(0.35, floor_empirical)
# NO override for Denver. Lagos override ONLY if physically required.
```

**2. Day-0 Live Evaluation (The Two Rails)**
```python
cov = current_directional_coverage(city, track)

# RAIL 1: Absolute Hard Kill (State 0 / Catastrophe)
if cov < 0.35 and window_elapsed > 0.50:
    return HALT_TRADING_FOR_DAY

# RAIL 2: Relative Discount (State 1 / Degradation)
shortfall = max(0.0, city_floor - cov)

# Continuous Linear Curve
discount_raw = min(0.09, 0.20 * shortfall)

# Small Sample Amplification (if N < N*)
if N_platt_samples < N_star:
    discount_raw = min(0.09, discount_raw * 1.25) # Example multiplier

return max(mismatch_rate, discount_raw)
```

**Expected Impact**:
- **Healthy Cities (40/46)**: `city_floor = 1.0`. Missing 1 hour (shortfall ≈ 0.04) gives D ≈ 0.8%. Missing 3 hours (shortfall ≈ 0.12) gives D ≈ 2.4%. Missing >65% of hours halts.
- **Lagos**: `city_floor = 0.42`. Day at 0.40 gives shortfall = 0.02, D = 0.4%. Day at 0.0 halts.

---

## §10 What this analysis CANNOT settle

1. **The Exact Value of α**: We propose α=0.20 to map a 0.45 shortfall to a 9% cap. This needs backtesting against historical PnL to verify it doesn't over-penalize profitable tail events.
2. **The Small Sample Multiplier**: We proposed a 1.25x heuristic for N < N*. The true mathematical value requires fitting a mixed-effects model to the calibration residuals, which is beyond Phase 1 scope.
3. **Failover Integration**: If WU drops but Ogimet is 100%, does `cov` = 1.0? This analysis assumes `cov` measures the *actual oracle path used*, but the DB schema must support tracking this accurately.

---

## §11 References
- `PHASE1_V2_FINAL_SUMMARY.md`: Line 25-69 (Per-city floors showing pervasive 1.0s).
- `PHASE1_V2_FINAL_SUMMARY.md`: Line 75-97 (High/Low bins showing tail severity).
- `PHASE1_V2_FINAL_SUMMARY.md`: Line 103-106 (Bootstrap CIs showing indistinguishable mid-bins).
- `zeus_oracle_density_discount_reference.md`: §4 (Platt internalizing regime bias).
- `zeus_oracle_density_discount_reference.md`: §7 (Day-0 composite two-rail operator ruling).
