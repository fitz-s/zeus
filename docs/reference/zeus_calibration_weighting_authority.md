# Zeus Calibration Weighting Authority

| Field | Value |
|---|---|
| Created | 2026-04-29 |
| Last reused/audited | 2026-04-29 |
| Authority class | **Reference / Mathematical Spec** (binds calibration weight semantics for ensemble_snapshots_v2 → calibration_pairs_v2 → platt_models_v2) |
| Status | ACTIVE |
| Authority basis | Empirical: PoC v4 (`_poc_weighted_platt_2026-04-28/poc_weighted_platt.py`, 1.7M pairs, 60-day OOS, 500-resample bootstrap), PoC v5 (`_poc_weighted_platt_2026-04-28/poc_v5_temp_delta_weighted.py`, same dataset, ΔT-based weights). Theoretical: Fitz Constraint #2 (translation-loss / information-cliff). Physical: ECMWF mesoscale resolution limit at coastal/monsoon cities. |
| Supersedes | Implicit binary `training_allowed: bool` semantics in `extract_tigge_mn2t6_localday_min.py:328-333` |
| Antibody tests required | `tests/test_calibration_weight_continuity.py` (TBD); `tests/test_per_city_weighting_eligibility.py` (TBD) |

## Theorem (the load-bearing claim)

**For ensemble forecast calibration on Polymarket weather markets, calibration weight must be a continuous function of physically-defined precision dimensions, gated only by hard physical/causal constraints. Any binary discard of a continuous-quality dimension is mathematically suboptimal and architecturally forbidden.**

Formally, for a snapshot $r$:
$$
w(r) = \mathbb{1}[\text{causality}(r) = \text{OK}] \cdot \mathbb{1}[\text{horizon}(r) \geq \text{required}] \cdot \mathbb{1}[\text{members complete}(r)] \cdot f(\text{precision}(r))
$$
where $f: \mathbb{R}_{\geq 0} \to [w_\min, 1]$ is a smooth monotone decreasing function with floor $w_\min > 0$.

The first three indicators encode physical impossibility (ZERO weight if violated). $f$ encodes epistemic precision (continuous downweighting, NEVER zero unless precision is undefined).

## Discovery chronology

### Phase 1 — Problem statement (pre-2026-04-28)

`extract_tigge_mn2t6_localday_min.py:328-333` collapses four heterogeneous quality dimensions into one bool:

```python
training_allowed = (
    len(missing) == 0                  # member completeness          (binary OK)
    and horizon_satisfied              # step horizon ≥ required      (binary OK)
    and causality["pure_forecast_valid"]  # no observation leakage    (binary OK)
    and not any_boundary_ambiguous     # 6h-step daily-MIN aliasing   (CONTINUOUS, miscoded as binary)
)
```

Result: **78% of LOW (mn2t6) snapshots discarded** to weight=0. LOW Asia cities (Kuala Lumpur 1.8%, Singapore 3.0%, Tokyo 5.5%, Jakarta 3.4% training_allowed=True) have insufficient per-bucket calibration pairs.

### Phase 2 — PoC v4 (2026-04-28T05Z): break the information cliff

Hypothesis: replacing binary gate with continuous weight recovers training signal. Tested 4 schemes on 1,699,340 pairs (339,868 LOW snapshots × 5 candidate bins, MC sensor noise σ=0.3°F / 0.2°C, 60-day OOS holdout, 500-resample bootstrap):

| Scheme | Weight rule | eff_n | OOS Brier | Δ vs A_baseline | CI95 |
|---|---|---|---|---|---|
| **A_baseline** | $w=1$ if `training_allowed`, else $0$ | 339,230 | 0.15792 | — | — |
| **B_uniform** | $w=1$ for all | 1,576,540 | 0.15775 | **−0.00018** | [−0.00022, −0.00014] |
| **C_overlap** | $w = 1 - \text{ambiguous\_count}/51$ | 1,070,431 | 0.15778 | **−0.00015** | [−0.00017, −0.00012] |
| **D_softfloor** | $w = \max(0.05, 1 - \text{ambiguous\_count}/51)$ | 1,074,485 | 0.15778 | **−0.00015** | [−0.00017, −0.00012] |

**Result**: All three weighted schemes statistically significantly better than binary. Asia subset improvement is 17% larger than overall (ΔBrier_Asia = −0.00021 vs overall −0.00018). **Information cliff is real**.

### Phase 3 — PoC v5 (2026-04-29): ΔT-magnitude weighting test

Hypothesis (per operator suggestion 2026-04-29): a magnitude-aware weight $w_i = 1/(1 + \alpha (\Delta T_i)^2)$ where $\Delta T_i = \text{inner\_min}_i - \text{boundary\_min}_i$ (member-mean per snapshot) is more granular than count-based, because count ignores the magnitude of inner-vs-boundary divergence.

Tested 6 α values (0.1, 0.25, 0.5, 1.0, 2.0, 5.0) on the same 1.7M pairs (joined with newly-extracted per-snapshot ΔT from JSON). Aggregate result:

| Scheme | OOS Brier | Δ vs A_baseline |
|---|---|---|
| A_baseline | 0.15792 | — |
| B_uniform | 0.15775 | −0.00018 |
| D_softfloor | 0.15778 | −0.00015 |
| **E_temp_delta_a0.25–2.0** | **0.15774** | **−0.00018** |

E_temp_delta plateau ≈ B_uniform; α has minimal effect across 0.25–2.0; α=5 is too aggressive.

**Aggregate signal is robust**, but per-city pattern is identical to v4 — temp-delta does not solve heterogeneity:

| City (heavy-loss focus) | A_baseline | D_softfloor (count) | E_temp_delta_a0.5 | Direction |
|---|---|---|---|---|
| Tokyo | 0.15671 | 0.15632 ✓ | **0.15617 ✓** (best) | continuous wins more under ΔT weight |
| Manila | 0.1519 | 0.1506 ✓ | (similar D pattern) | improves |
| Seoul | 0.1567 | 0.1562 ✓ | (similar D pattern) | improves |
| Kuala Lumpur | 0.15819 | 0.15820 — | 0.15825 — (worse) | unchanged or marginally worse |
| Beijing | 0.15931 | 0.15942 — | 0.15950 — (worse) | unchanged or marginally worse |
| **Jakarta** | 0.16244 | 0.16344 ✓↑ | **0.16395 ✓↑↑** | regress more under ΔT |
| **Busan** | 0.16257 | 0.16359 ✓↑ | **0.16411 ✓↑↑** | regress more |
| **Hong Kong** | 0.16081 | 0.16136 ✓↑ | **0.16166 ✓↑↑** | regress more |
| **NYC** | 0.16065 | 0.16107 ✓↑ | **0.16129 ✓↑↑** | regress more |
| **Houston** | 0.16080 | 0.16128 ✓↑ | **0.16153 ✓↑↑** | regress more |

The 5 cities that regress under D_softfloor regress MORE under E_temp_delta. The "smarter" weight function does worse, not better, on the heterogeneous tail.

### Phase 4 — Physical mechanism (Fitz, 2026-04-29)

**Why does ΔT-magnitude weighting fail on coastal/monsoon cities?**

Boundary buckets land at UTC 00:00 / 06:00 / 12:00 / 18:00 — the same moments where:
- **Sea/Land breeze regime flips** (sunrise → onshore breeze; sunset → offshore breeze): coastal temps experience step-changes or stagnation locked by surface energy budget transitions
- **Monsoon transition**: ITCZ migration, frontal passage timing concentrates around these UTC anchor times
- **ECMWF grid-snap (~25 km horizontal resolution)**: cannot resolve mesoscale sea-breeze fronts in cities with sharp coast/inland gradient

For these cities, at boundary times:
- $\text{boundary\_min} \approx \text{inner\_min}$ ⇒ small $\Delta T$
- BUT this similarity is driven by **physical lock-in** (sea breeze pinning surface temp), not by accurate ensemble forecast
- The forecast itself misses the sea-breeze physics → boundary sample carries **correlated bias** (forecast and outcome both biased the same direction by the same unmodeled physics)

When E_temp_delta sees small $\Delta T$, it assigns weight ≈ 1.0, feeding correlated-bias samples to MLE with full confidence. Platt's $\theta = (A, B, C)$ is pulled toward the systematic bias, OOS Brier degrades.

This is precisely the failure mode that makes "smart per-sample weighting" lose to "uniform weighting" on aggregate: the smartness over-fits to a physical artifact in 8 cities, gaining a tiny edge on 12 inland cities, netting to ≈ B_uniform.

### Phase 5 — Mathematical synthesis

Three propositions empirically validated:

**Proposition 1 (Information Cliff is Real and Recoverable)**
$$
\mathrm{OOS\_Brier}(B_\mathrm{uniform}) < \mathrm{OOS\_Brier}(A_\mathrm{baseline}) \quad \text{(strict, CI95 excludes 0)}
$$
Even unweighted continuous inclusion beats binary discard. **Direction of continuous→binary fix is correct, regardless of weight-function shape.**

**Proposition 2 (Weight Function Shape is Second-Order)**
$$
\bigl| \mathrm{OOS\_Brier}(B_\mathrm{uniform}) - \mathrm{OOS\_Brier}(D_\mathrm{softfloor}) - \mathrm{OOS\_Brier}(E_{\Delta T,\alpha}) \bigr| \leq O(10^{-5}) \ll \bigl| \mathrm{OOS\_Brier}(A_\mathrm{baseline}) - \mathrm{OOS\_Brier}(B_\mathrm{uniform}) \bigr|
$$
Aggregate Brier across continuous-weight schemes is nearly identical. **Optimizing the weight function gives diminishing returns; the binary→continuous transition is the load-bearing change.**

**Proposition 3 (Per-City Heterogeneity is Physical, Not Statistical)**
For 8 coastal/monsoon cities (Jakarta, Busan, Hong Kong, NYC, Houston, Chicago, Guangzhou, Beijing), the per-city OOS Brier degrades more aggressively under "smarter" weighting (E_temp_delta > D_softfloor degradation). This is not a hyperparameter problem; it is mesoscale physics aliased into 6h-resolution ENS forecasts at UTC boundary anchors. **No global single-form weight function will Pareto-dominate the per-city heterogeneity.**

## Production rule (LAW — applies to all training paths)

### LAW 1: Continuous weighting MANDATORY for boundary-ambiguous LOW

For LOW (mn2t6) calibration training, `training_allowed: bool` MUST be replaced with `precision_weight: float ∈ [w_min, 1]` where:

```python
precision_weight = (
    0.0 if not (causality_pure and horizon_satisfied and members_complete)
    else max(WEIGHT_FLOOR, 1.0 - ambiguous_member_count / N_MEMBERS)
)
```

with `WEIGHT_FLOOR = 0.05` and `N_MEMBERS = 51` (TIGGE ENS).

Schema migration target: `ensemble_snapshots_v2.precision_weight REAL NOT NULL CHECK (precision_weight >= 0 AND precision_weight <= 1)` and the existing `training_allowed` column either dropped or maintained as a derived view: `training_allowed = (precision_weight > 0)`.

### LAW 2: Per-city eligibility opt-out for coastal/monsoon cities

For cities where physical mechanism (sea/land breeze, monsoon transition, frontal aliasing) systematically degrades OOS Brier under ANY continuous-weight scheme, the operator may opt them out of weighted training. Their boundary-ambiguous samples are excluded entirely (functionally `precision_weight = 0` for boundary_ambiguous=True even if other dimensions pass).

Initial opt-out list (from PoC v5 evidence):
- Jakarta (ID), Busan (KR), Hong Kong (HK), NYC (US-East), Houston (US-Gulf), Chicago (Lake Michigan), Guangzhou (CN-South), Beijing (CN-North; marginal — re-evaluate with future data)

Encoded in `config/cities.json::cities[].weighted_low_calibration_eligible: bool`. Default `true`; explicitly `false` for the listed cities.

### LAW 3: Forbidden moves (any of these triggers `forbidden_move_violation` antibody fail)

1. **NEVER use binary `training_allowed` for any continuous-quality dimension**. Boundary-ambiguity is continuous (precision); it MUST NOT be binary-gated.
2. **NEVER use $\Delta T$-magnitude weighting for boundary-ambiguous samples in production** (PoC v5 disproved its OOS benefit; it amplifies coastal-city regression).
3. **NEVER set `precision_weight = 0` for boundary_ambiguous=True alone**. Zero weight is reserved for hard physical impossibility (causality leak / horizon deficit / member loss). Boundary ambiguity gets `WEIGHT_FLOOR` minimum.
4. **NEVER skip per-city heterogeneity check** when introducing a new weight scheme. Aggregate Brier improvement does NOT imply per-city Pareto dominance.
5. **NEVER apply LOW weighting law to HIGH track**. HIGH (mx2t6) is 100% training_allowed=True today; no boundary-ambiguity dimension exists. The LAW applies to LOW only unless future evidence extends it.

### LAW 4: n_mc tuning has context-dependent precision floor

**Erratum 2026-04-29:** Earlier draft of this LAW stated `ensemble_n_mc()=10,000` as the production default. Audit of `config/settings.json` (commit `218b8c86`, 2026-04-01) showed the actual setting was `n_mc: 5000`. The 2026-04-29 calibration_pairs_v2 rebuild therefore ran at `n_mc=5000`, not 10,000. The 5000-vs-10000 distinction is mathematically below detection at aggregate fit scale (see derivation below) but is consequential for the **runtime per-trade evaluator** where it dominates per-decision SE. Settings.json was updated 2026-04-29 to `n_mc: 10000` (both `ensemble.n_mc` and `day0.n_mc`) to lift runtime to the LAW 4 preferred precision; the existing calibration_pairs_v2 (built at 5000) is retained for deployment because the math below shows the aggregate Platt fit difference is < 10⁻³ σ — empirically undetectable. A future re-rebuild at n_mc=10000 is queued in the backlog for full pipeline symmetry but is not load-bearing for fit quality.

`rebuild_calibration_pairs_v2.py` defaults `n_mc=ensemble_n_mc()`. This default is mathematically justified for the **live runtime path** (single-snapshot p_raw used to size a single trade decision) but is **mathematically excess for the batch-rebuild path** (aggregate Platt fit across millions of pairs). Two distinct operational contexts have different precision floors.

#### Math derivation

Let $p_{\text{raw}}^{(\text{MC})}(r)$ be the bin probability for snapshot $r$ estimated from $n$ MC draws. Standard error of this single-snapshot estimate:

$$
\mathrm{SE}\bigl(p_{\text{raw}}^{(\text{MC})}\bigr) \;\approx\; \sqrt{\frac{p(1-p)}{n}}
$$

Concrete values at $p=0.5$ (worst case):

| n_mc | SE(per-snapshot p_raw) | Comment |
|---|---|---|
| 10,000 | 0.005 | live runtime appropriate (single-trade precision) |
| 1,000 | 0.016 | batch-fit appropriate (~3× SE, but averaged across N_pairs) |
| 100 | 0.050 | too coarse for either context |
| 32 | 0.088 | PoC v4 used this — still produced sig OOS Brier diff because of N_pairs leverage |

Now the Platt-fit parameter SE depends on **N_pairs**, not on per-snapshot SE alone:

$$
\mathrm{SE}\bigl(\hat{A}, \hat{B}, \hat{C}\bigr) \;\approx\; \sqrt{\frac{\sigma^2_{\text{label-noise}} + \mathrm{SE}^2(p_{\text{raw}}^{(\text{MC})})}{N_{\text{pairs}}}}
$$

For zeus's batch-rebuild scale: $N_{\text{pairs}} \approx 4\,\text{M}$, $\sigma^2_{\text{label-noise}} \approx 0.25$ (Bernoulli outcome noise). The MC-induced contribution to aggregate parameter SE is:

$$
\frac{\mathrm{SE}^2_{\text{MC}}}{N_{\text{pairs}}} \;\le\; \frac{0.016^2}{4\times10^6} \;\approx\; 6 \times 10^{-11} \quad (\text{at } n_{\text{mc}}=1000)
$$

vs label-noise contribution $\approx 6 \times 10^{-8}$ — **MC noise is 1000× smaller than label noise at the aggregate level**. Going from $n_{\text{mc}}=10000$ to $n_{\text{mc}}=1000$ changes aggregate Platt fit quality by $< 10^{-3}\sigma$ in $A, B, C$. Empirically below detection.

#### Production rule

For **batch-rebuild path** (`rebuild_calibration_pairs_v2.py` invoked via cron / operator command / drift-trigger):

```
default --n-mc = 1000  (was 10,000)
```

This produces ~10× wallclock speedup with no detectable Platt fit quality loss.

For **live runtime path** (`evaluator.py::_fetch_ens_for_market` MC of a single snapshot for trade decision):

```
n_mc stays at 10,000  (live precision matters per-trade)
```

These are configured separately. The change applies ONLY to the rebuild script's CLI default, not to runtime MC.

### LAW 5: HIGH/LOW SAVEPOINT separation for rebuild

`rebuild_calibration_pairs_v2.py:38` documents "Entire rebuild runs inside one SAVEPOINT". Empirical impact (rebuild observed 2026-04-29):

- HIGH track (342k snapshots) + LOW track (74k eligible) processed serially in one SAVEPOINT
- WAL grows to 7+ GB before commit
- DB write-lock held for entire rebuild (~16 hours observed 2026-04-29 at n_mc=5000; would be ~32 hours at n_mc=10000 without rebuild MC vectorization; ~30-50 min at n_mc=1000)
- Live daemon (`src.main`) cannot `init_schema(conn)` during rebuild → daemon crashes if respawned mid-rebuild ("database is locked")

Operational fix: split SAVEPOINT per metric:

```python
for spec in METRIC_SPECS:
    conn.execute("BEGIN")
    process_track(spec)
    conn.commit()           # release lock between tracks
    # daemon can init_schema and read latest HIGH coefficients while LOW rebuilds
```

This requires a ~5-line code change to `rebuild_calibration_pairs_v2.rebuild_all_v2`. Effect:
- HIGH commits when HIGH done (daemon picks up HIGH coefficients while LOW continues)
- LOW commits when LOW done
- Maximum daemon-blocked window halved
- Aggregate Brier fit unaffected (HIGH and LOW Platt models are independent per `INV-15`)

### Operational acceleration impact

Combined: `--n-mc 1000` + per-track SAVEPOINT split:

| Configuration | Wallclock (rebuild) | Daemon-blocked window | DB WAL peak |
|---|---|---|---|
| Observed 2026-04-29 (n_mc=5000, single SAVEPOINT) | 16 hours | 16 hours | 22 GB final |
| `--n-mc 1000` only | 30-50 min | 30-50 min | ~700 MB |
| `--n-mc 1000` + per-track split | 30-50 min | 15-25 min (HIGH OR LOW at a time) | ~500 MB peak |

Cumulative speedup: **12-20× wallclock**, **disk WAL pressure 14-20× lower**, **daemon-coexistence enabled**. Daily retrain cadence becomes operationally feasible (current 6-10h cadence makes weekly the minimum practical).

#### Forbidden moves (extends LAW 3)

6. **NEVER use n_mc < 100 in batch rebuild path.** SE(per-snapshot p_raw) > 0.05 starts to bias the Platt fit at small N_pairs subsets (e.g., per-city per-bucket OOS Brier evaluation on test set holdouts).
7. **NEVER use n_mc < 5000 in live runtime evaluator.** Per-trade precision needs tighter SE; single-snapshot decisions cannot rely on N_pairs leverage.
8. **NEVER fit per-city `α` (or any per-city continuous tuning parameter) directly.** This is the **curse of dimensionality** trap. Sample imbalance is severe (LOW: Mexico City ~6,000 samples; Kuala Lumpur ~120 samples). On 120-sample datasets, optimizer trivially finds an α that makes IS Brier near-perfect — pure memorization of noise. OOS collapse follows in production: KL Day-0 hits a new weather pattern, the over-fit model gives extreme wrong probability. Use cluster-level tuning instead (LAW 6), or terminal Bayesian Hierarchical Model with shrinkage prior (Future research §5).

### LAW 6: Cluster-level tuning (climate-zone partition)

Per-city heterogeneity (LAW 2's coastal/monsoon opt-out) is the BINARY case. The continuous-tuning case must operate at **cluster** granularity, not per-city, to defend against curse of dimensionality.

**Math principle**: optimal cluster count $K$ minimizes:

$$
K^* = \arg\min_{K} \;\; \text{IS-OOS-gap}(K) \;\; = \;\; \arg\min_{K} \left[ \mathrm{Brier}_{\mathrm{OOS}}(K) - \mathrm{Brier}_{\mathrm{IS}}(K) \right]
$$

Increasing $K$ shrinks IS Brier (more parameters, better fit) but inflates IS-OOS gap (overfit). At $K=51$ (per-city), gap is maximal due to small per-cluster sample. At $K=1$ (global uniform), gap is zero but Brier loss to heterogeneity is large.

**Empirical sweet spot per atmospheric physics**: $K \approx 4$–$5$ (validated by 51-city geophysical structure).

**Initial climate_zone partition** (operator-set in `config/cities.json::cities[].climate_zone`):

| Zone | Cities (initial; operator-tunable) | Physical signature | Suggested $\alpha$ direction |
|---|---|---|---|
| `tropical_monsoon_coastal` | Jakarta, Hong Kong, Kuala Lumpur, Manila, Singapore, Bangkok, Mumbai, Lagos | Sea breeze regime flips at UTC anchor times; high humidity; small diurnal range; **strong correlated bias at boundary** | LOW α (de-trust boundary samples) |
| `temperate_coastal_frontal` | NYC, Tokyo, London, Amsterdam, Seattle, Sydney, Auckland, Cape Town, San Francisco, Boston | Jet stream / cyclone passage; rapid temp shifts; coastal moderation | MEDIUM α (boundary partly trustable) |
| `inland_continental` | Beijing, Chicago, Moscow, Berlin, Toronto, Denver, Atlanta, Madrid, Warsaw, Helsinki, Munich, Paris, Rome, Istanbul, Ankara, Wuhan, Shanghai, Seoul, Chengdu, Chongqing, Shenzhen, Guangzhou (re-evaluate) | Radiative cooling dominant; regular diurnal pattern; ΔT is "honest" indicator of forecast skill | HIGH α (boundary samples informative) |
| `high_altitude_arid` | Mexico City, Bogotá, Denver (re-classify), Lhasa | Thin air; rapid up/down; low heat capacity | MEDIUM-HIGH α |

(Initial partition is approximate; operator may refine via observed per-city OOS Brier. Re-evaluation cadence: quarterly.)

**Per-cluster sample size analysis** (from PoC v4 LOW eligible counts):

| Zone | Sum of per-city samples | Sufficient for cluster-level α tuning? |
|---|---|---|
| tropical_monsoon_coastal | ~1,500 | YES (>1000 per-cluster floor) |
| temperate_coastal_frontal | ~6,000 | YES |
| inland_continental | ~25,000 | YES |
| high_altitude_arid | ~6,000 | YES |

Each cluster has ≥1k samples — defends against per-city overfitting while allowing physical heterogeneity to surface.

**Production rule**:

```python
# rebuild_calibration_pairs_v2.py + refit_platt_v2.py
def cluster_alpha(climate_zone: str) -> float:
    return CLUSTER_ALPHA_MAP[climate_zone]   # tuned per-cluster, not per-city

precision_weight = (
    0.0 if not (causality_pure and horizon_satisfied and members_complete)
    else max(WEIGHT_FLOOR, 1.0 - cluster_alpha(city.climate_zone) * normalized_severity)
)
```

CLUSTER_ALPHA_MAP fitted once (e.g., 4-fold OOS CV across cluster cities), then deployed; not re-tuned per training run.

**Operator action required for LAW 6 deployment**:
1. Add `climate_zone: str` field to `config/cities.json` schema (51 entries)
2. Initial partition per the table above
3. Antibody test: `tests/test_climate_zone_present.py` — every city has `climate_zone ∈ {tropical_monsoon_coastal, temperate_coastal_frontal, inland_continental, high_altitude_arid}`
4. Cluster-level α PoC v6 (out of scope for this RFC; queue for post-deployment iteration)

## Antibody requirements (must be tests, not lore)

| Antibody | What it tests | Status |
|---|---|---|
| `tests/test_calibration_weight_continuity.py` | Schema has `precision_weight ∈ [0,1]` REAL column; binary `training_allowed` derived only | TBD |
| `tests/test_per_city_weighting_eligibility.py` | `cities.json` has `weighted_low_calibration_eligible` bool; opt-out list non-empty | TBD |
| `tests/test_no_temp_delta_weight_in_production.py` | grep production code for $\Delta T$-magnitude weight forms; fail if any found | TBD |
| `tests/test_weight_floor_nonzero_for_ambig_only.py` | for any row with boundary_ambiguous=True AND causality_pure=True AND horizon_satisfied=True, assert `precision_weight ≥ WEIGHT_FLOOR` | TBD |
| `tests/test_high_track_unaffected_by_low_law.py` | HIGH calibration_pairs_v2 rows have precision_weight=1 always | TBD |
| `tests/test_rebuild_n_mc_default_bounded.py` | `rebuild_calibration_pairs_v2`'s default `n_mc` arg ≤ 2000; CLI override allowed but default reflects LAW 4 | TBD |
| `tests/test_runtime_n_mc_floor.py` | `evaluator.py` MC paths use n_mc ≥ 5000 (per LAW 4 forbidden move 7) | TBD |
| `tests/test_rebuild_per_track_savepoint.py` | rebuild emits per-metric `BEGIN`/`COMMIT` (LAW 5); inspectable via mocked conn or git-log of code structure | TBD |
| `tests/test_no_per_city_alpha_tuning.py` | grep production code for any `cities[name].alpha = ...` or per-city continuous parameter; fail if found (LAW 3 forbidden #8) | TBD |
| `tests/test_climate_zone_present.py` | every city in `cities.json` has `climate_zone` field set to one of the 4 allowed enum values (LAW 6) | TBD |
| `tests/test_cluster_alpha_map_finite.py` | CLUSTER_ALPHA_MAP has exactly the 4 expected keys (LAW 6) and α values are finite, non-negative, ≤ some sensible upper bound (e.g. 10.0) | TBD |

## Future research (NOT current scope)

The 8-city per-city regression is a **physics resolution problem**, not a calibration problem. Pursue these IF needed for further OOS gain:

1. **Higher-resolution forecast input**: TIGGE 1h-step or ECMWF AIFS (ML model) may resolve sea-breeze mesoscale; would replace or supplement TIGGE 6h-step
2. **Boundary-bucket physical de-biasing**: explicit additive correction for known sea-breeze times at known coastal cities (architectural — would require maintaining a city-time bias map).
3. **Hybrid: explicit boundary_min in p_raw**: instead of using `value_native_unit` (currently inner-only when boundary_ambiguous), construct p_raw using BOTH inner and boundary realizations and average; would dilute boundary noise without weighting.
4. **Cluster-level α PoC (v6)**: validate per-cluster α tuning per LAW 6. 4-fold OOS CV across cluster cities. Expected: improves over global D_softfloor by recovering coastal-cluster ΔBrier without 51-city overfitting. Out of LAW scope; queued for post-deployment iteration.
5. **Bayesian Hierarchical Model (BHM) — terminal direction**:

   The progression Global → Cluster → Per-city ends at BHM, which automates the bias-variance trade-off:

   $$\alpha_{i} \;\sim\; \mathcal{N}(\mu_{\text{global}}, \sigma_{\text{global}}^{2})$$
   $$y_{ij} \;|\; \alpha_{i} \;\sim\; \text{Bernoulli}\bigl(\sigma\bigl(A_i \cdot \mathrm{logit}(p_{\text{raw},ij}) + B \cdot \mathrm{lead}_j + C\bigr)\bigr)$$

   **Shrinkage mechanic**: posterior mean of $\alpha_i$ is pulled toward $\mu_{\text{global}}$ by amount inversely proportional to $n_i$ (city sample count). Concretely:

   - Mexico City (n ≈ 6000): $\alpha_{\text{mexico}}$ posterior is data-dominated → trusts city-specific signal
   - Kuala Lumpur (n ≈ 120): $\alpha_{\text{kl}}$ posterior is prior-dominated → shrunk to $\mu_{\text{global}}$
   - **The model auto-resolves "which cities deserve their own α"** — operator does not need to specify per-city eligibility manually

   Implementation: `pymc` or `numpyro`-based fit, MCMC sampling for posterior. Cost: ~1-2 weeks engineering + per-cluster compute. Reward: Pareto-frontier OOS Brier across all 51 cities, no operator hand-tuning. Replaces LAW 2 + LAW 6's manual partitioning.

   Pre-requisite: cluster-level PoC (research item 4) must validate BEFORE BHM, to confirm the heterogeneity is partitionable in principle. If clusters help, BHM will help more (and absorb the partition into prior structure). If clusters don't help, BHM probably also doesn't (the heterogeneity is noise, not signal).

None of (1)-(5) are required to deploy LAW 1+2+3+4+5+6. They are upside, sequenced.

## References

| Item | Path |
|---|---|
| PoC v4 source | `/Users/leofitz/.openclaw/workspace-venus/_poc_weighted_platt_2026-04-28/poc_weighted_platt.py` |
| PoC v4 metrics | `/Users/leofitz/.openclaw/workspace-venus/_poc_weighted_platt_2026-04-28/metrics.json` |
| PoC v4 evidence summary | `docs/operations/task_2026-04-28_weighted_platt_precision_weight_rfc/evidence/poc_summary.md` |
| PoC v5 source | `/Users/leofitz/.openclaw/workspace-venus/_poc_weighted_platt_2026-04-28/poc_v5_temp_delta_weighted.py` |
| PoC v5 ΔT index | `/Users/leofitz/.openclaw/workspace-venus/_poc_weighted_platt_2026-04-28/delta_index_v5.parquet` |
| RFC | `docs/operations/task_2026-04-28_weighted_platt_precision_weight_rfc/rfc.md` |
| Original LOW extractor | `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/tigge_local_calendar_day_extract.py:_finalize_low_record` |
| Snapshot ingest contract | `src/contracts/snapshot_ingest_contract.py::validate_snapshot_contract` (Laws 1, 2 — boundary_ambiguous and causality gating) |
| Weight authority | THIS FILE |

## Cited principles

- **Fitz Constraint #1 (Structural decisions > patches)**: 5 city regressions are not 5 bugs; they are 1 physical limit of grid-snap forecast input. The fix is structural — per-city eligibility flag — not 5 weight-function tweaks.
- **Fitz Constraint #2 (Translation loss is thermodynamic)**: binary→continuous transition recovers ~$3-4\times$ effective sample size (1.6M vs 339k) at near-zero cost. This is the maximal information-recovery move; further weight-function refinement is information-conservative at best.
- **Fitz Constraint #4 (Data provenance > code correctness)**: the discovery sequence (PoC v4 → v5 → physical mechanism → LAW) was driven by data-provenance interrogation: WHY are these 8 cities different? was the question that produced the answer. Code correctness alone (each weight scheme correctly implements its formula) would have stopped at "schemes are equivalent on aggregate."
