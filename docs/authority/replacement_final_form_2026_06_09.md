# Replacement Forecast Final Form (2026-06-09, operator-ratified)

**Status:** Live replacement probability law. Runtime rows use `forecast_posteriors.runtime_layer='live'`; no second row-authority label or alternate runtime layer exists.
**Supersedes:** `BAYES_PRECISION_FUSION_SPEC.md` (deleted).  
**Created:** 2026-06-09  
**Last audited:** 2026-10-06 (§1e approved dual-domain point model and conservative envelope; activation requires complete atomic consumer revision)
**Authority basis:** Commits 140d75ff6d · 6860f00a21 · edc598b440 · 94b584cc3f · 49492f1528 · 2b6936d3b5 · 9c594c9fc3 · df8199ef8e · e80c101c4c · 8541bc93cd · 8f20d39863 · a70436d478 · a1c2163e46 plus June 18 live-runtime cleanup. Historical experiment reports remain evidence only; they do not define the live execution layer.

---

## 1. The Probability Chain

### 1a. Walk-forward de-bias

For each instrument `s` at `(city, metric, target_date)`:

```
residuals_s  = {x_s(d) − Y(d) | d in previous_runs, d < target_date, lead-bucket preferred=1}
r̄_s          = mean(residuals_s),   n_s = len(residuals_s)
λ_s          = n_s / (n_s + κ),     κ = KAPPA = 8.0          # src/forecast/bayes_precision_fusion.py:51
b̂_s          = λ_s · r̄_s + (1−λ_s) · parent                # parent prior = 0.0
z_s          = x_s − b̂_s                                    # de-biased instrument value
```

`n_s < MIN_TRAIN=25` → LOWN_INFLATE=1.5 applied to σ_s (thin instrument).  
Walk-forward is strictly `target_date < decision_date`; settlement residuals only (`endpoint='previous_runs'`). `src/forecast/bayes_precision_fusion.py:67–78`.

### 1b. T2 Bayesian fusion

Anchor: `ecmwf_ifs` as prior N(μ₀, τ₀²), τ₀ = max(anchor_walk_forward_std, TAU0_FLOOR=0.8).  
Likelihood instruments: K de-biased values z = [z₁ … z_K] (globals + in-domain regionals).

Covariance Σ: Ledoit-Wolf shrinkage of the sample covariance toward its diagonal, computed on the **common-target-date residual matrix** (rows = dates present for ALL instruments simultaneously). Requires ≥ COMMON_DATES_MIN=5 common dates; else diagonal C0 = diag(σ²_s). `src/forecast/bayes_precision_fusion.py:82–114`.

```
V* = ( τ₀⁻² + 1ᵀ Σ⁻¹ 1 )⁻¹
μ* = V* · ( τ₀⁻² μ₀  +  1ᵀ Σ⁻¹ z )
```

Hyperparameters fixed a-priori (`src/forecast/bayes_precision_fusion.py:51–57`):

| Param | Value | Meaning |
|---|---|---|
| KAPPA | 8.0 | EB shrink; ~50% trust at n=8 |
| MIN_TRAIN | 25 | minimum rows before an instrument is trusted |
| SIGMA_FLOOR | 0.8 °C | per-instrument residual std floor |
| LOWN_INFLATE | 1.5 | σ multiplier for thin instruments |
| DISAGREE_W | 0.5 | cross-source spread contribution to fusion σ² |
| TAU0_FLOOR | 0.8 °C | prior std floor |
| COMMON_DATES_MIN | 5 | minimum common dates for Ledoit-Wolf vs diagonal fallback |

**THEOREM (inverted-blend experiment, n=4492, `docs/evidence/2026_06_09_final_form/inverted_blend_experiment.md`)**  
Under diagonal Σ the "prior" label is algebraically irrelevant: μ* is the precision-weighted mean of ALL instruments (prior + likelihood). Proof: commutativity of the weighted sum. Consequence verified numerically: concentrating on the single most-precise model WORSENS MAE by 0.1645 °C at lead=1 (ratio=15.4 SE); precision-weighted mean beats equal-weight by 0.0379 °C (ratio=12.0 SE), beating every precision-concentration arm.

### 1c. Thin-anchor retention (commit 49492f1528)

When anchor history `n < MIN_TRAIN`, the anchor has no trusted τ₀. Prior to this fix the anchor center was silently dropped. **Fix (both sides of the boundary):** a finite `anchor_z` without trusted τ₀ joins the fusion as ONE equal-weight member at variance `(TAU0_FLOOR · LOWN_INFLATE)² = (0.8·1.5)² = 1.44 °C²` — demoted from T2 prior, never deleted. Zero-history anchor is the only valid reason for a null anchor center. `src/forecast/bayes_precision_fusion.py` + `src/data/bayes_precision_fusion_capture.py`.

### 1d. Predictive spread — decision-time current evidence

```
σ_within  = population_std(latest causal target-specific ECMWF ENS members)
μ_ens     = mean(latest causal target-specific ECMWF ENS members)
δ_ens     = μ_ens - μ*
σ_between = sqrt(Σ_s w_s · (x_s(current) − μ*)²)
σ_pred    = sqrt(σ_within² + σ_between² + δ_ens²)
```

For the live source-clock route, both components are facts available at the same
decision instant and target. The ENS row must be `VERIFIED`, causal, unambiguous,
fully inside the target local day, contribute to the target extrema (or be an
interval-censored row admitted under §1d-bis), have at least
20 finite °C members, and have `source_available_at <= computed_at` and
`source_cycle_time <= carrier source_cycle_time`. Two positively weighted current
providers are the minimum. Missing or invalid current shape blocks the live
posterior; it never falls back to a historical residual, constant width, fitted
floor, or uniform mixture.

A frozen fixed-weight basket with a physically eligible but absent configured
provider is incomplete as that fixed-weight proposal. If at least two distinct
configured provider families have possessed current values in a coherent cohort,
the existing current precision-fusion proposal may instead serve its own center
and current ENS/provider width. Its typed provenance retains the original
configured, possessed, coherent and missing sources; the executable certificate
identifies the actually weighted and served providers of this distinct proposal.
The city-local lead, source roles and exact rows must be reproducible at decision
time. A one-family cohort, ineligible source or missing current shape blocks it.
The complete fixed-weight proposal continues unchanged.

The configured-basket eligibility cohort, latest center inputs and actual
between-spread cohort are separate evidence roles. Their provider sets and runs
need not coincide. The eligibility cohort carries its exact raw rows and the
posterior's computed-at cutoff, both bound into posterior identity; JIT reproduces
that cohort at the same cutoff and checks the cutoff against the canonical
posterior row. Current center/source supersession and current ENS/between-shape
checks remain independently mandatory. Old partial certificates lacking this
cohort proof are rematerialized through the existing input-revision queue.

The center bootstrap uses only current evidence. The ENS/provider center
displacement is systematic current disagreement and is not divided by member
count:

```
n_eff_provider = 1 / Σ_s w_s²
σ_center = sqrt(σ_within²/n_members + σ_between²/n_eff_provider + δ_ens²)
```

The same absolute ENS members are consumed later for settlement-preimage hit
counts. Therefore their displacement from `μ*` cannot be discarded here as if
the members were a recentered shape-only sample. When `μ_ens = μ*`, this term
is exactly inert and the original within-plus-between decomposition is
preserved. This is current-evidence uncertainty, not a historical residual,
fitted floor, or side-specific probability transform.

The current-evidence semantics revision is serialized inside this shape and
therefore inside its shape/dependency/posterior identity. When this probability
law changes, existing active families are no longer covered: the normal
seed/materialization loop replays them, and entry/monitor readers refuse an
older shaped certificate during convergence. This is semantic identity, not a
deployment-SHA freshness rule.

The older walk-forward residual width is offline historical evidence only and
is absent from runtime probability construction.

**2026-07-25 historical correction — bounded reuse of a stale-but-coherent ENS
shape (superseded for live authority on 2026-08-19).**
Measured cost of the pre-addendum same-cycle-only rule: new scopes waited a mean
14.6h (p50 6.8h) for the slow ENS-baseline leg while every other instrument was
already fresh, costing 0.24–0.41°C of avoidable center error at scope-open
(docs/evidence/upstream_physical_2026_07_17/consult_freshness_decoupling_verdict.txt
§P2-B; docs/operations/current/plans/upstream_data_physical_2026-07-17.md). Reusing
an older but internally coherent ENS cycle is licensed as bounded stale shape,
never as same-instant agreement evidence:

```
shape_lag_hours = carrier_cycle_time - ens_cycle_time
stale_shape_reused = shape_lag_hours > 0

if stale_shape_reused:
    X'_j        = X_j                        # raw absolute members remain finite evidence
    δ_ens       = mean(X) - μ*               # observed cross-clock epistemic disagreement
    δ_ens_raw   = μ* - mean(X)               # signed provenance
    σ_pred      = sqrt(σ_within² + σ_between² + δ_ens²)
else:
    # shape_lag_hours <= 0 (same ENS cycle as the carrier): §1d above, unchanged.
```

The raw members `X'_j` are the operative sample for settlement-preimage hit
counting and its downstream Clopper-Pearson tail.
`ens_center_delta_raw_c` is carried in `current_evidence_shape` provenance for
the signed cross-clock disagreement. A pure location transform does not prove
that the newer deterministic center dominates the older ensemble center; the
absolute disagreement remains in both predictive and center uncertainty.
A bounded stale-shape row is stamped
`semantics_revision = "stale_ensemble_absolute_disagreement_v1"` (module
`src.data.replacement_forecast_cycle_policy`, constant
`STALE_ENSEMBLE_ABSOLUTE_DISAGREEMENT_SEMANTICS_REVISION`), distinct from the same-cycle
`"ensemble_center_disagreement_v1"` above — so the existing revision-mismatch
convergence machinery (`current_evidence_shape_semantics_mismatch`) applies only
to stale-shape rows, never a universe-wide replay. The 30h source-cycle
staleness bound (§ above, `replacement_source_cycle_max_age_hours`) remains the
outer catastrophic-guard on how old the selected ENS cycle may be; this
addendum does not widen it, and carries no sigma age-inflation term of its own
(a walk-forward-fitted `γ_g · age/6` term, per the consult's EMOS-like scale, is
deferred to a separate calibration slice).

**2026-08-19 correction — same-cycle target-specific ENS is required for live
probability authority.** Seven-day decision-time certificates showed that the
cross-clock construction above systematically overstated exact-bin `NO`
probabilities: 13 independent city-date clusters committed $70.91 and realized
-$38.69, while the executable market beat the model by an e-value of 15,585.68.
The defect is structural: `μ* - mean(X_old)` mixes forecast evolution with
uncertainty and is not a sample from the carrier-time settlement distribution.
Inflating `σ_pred` by that term spreads probability away from central discrete
bins and manufactures apparent `NO` edge.

Therefore a shaped certificate has live entry, held-position statistical
redecision, and coverage authority only when all of the following hold:

```
shape_lag_hours == 0
stale_shape_reused in {absent, false}
semantics_revision == CURRENT_EVIDENCE_SEMANTICS_REVISION
translation_applied == false
```

`stale_ensemble_absolute_disagreement_v2` remains an immutable offline and
walk-forward evidence identity. It cannot authorize a live BUY, a statistical
SELL/HOLD decision, no-money admission evidence, or suppress rematerialization
of the missing same-cycle shape. Missing same-cycle ENS is DATA_DEGRADED for the
family and fails closed until the normal materialization loop writes current
evidence.

**Source geometry is part of current-evidence authority.** A land-station ENS
sample uses the nearest eligible land cell among the four surrounding cells,
with land fraction greater than 0.5, using [ECMWF's land-cell selection
rule](https://confluence.ecmwf.int/spaces/FUG/pages/673551105/Section%2B8.1.4.1%2BSelection%2Bof%2Bgrid%2Bpoints%2Bfor%2Bmeteograms).
Unlike its display-product sea fallback when no surrounding land cell exists,
live station-probability authority requires land evidence and degrades that
family when none is available. Its official static mask must have the same grid identity as
the extracted temperature fields. Preserve mask source cycle, content hash,
selected index and coordinates, station identity and the selection witness;
missing or mismatched evidence cannot fall back to an unqualified nearest cell.

Provider precision likewise requires the actual response grid, its matching
model surface evidence and the real station ground elevation. Requested
coordinates are not observed grid coordinates; target DEM, raw model height,
effective downscaling height and station height are distinct quantities.
Invented zero elevations, land labels or station-verification labels do not
constitute proof. Bind the certificate to exact response bytes and validate it
again at materialization, including the anchor derived from those bytes.

The stable source-geometry witness participates in current shape identity and
its semantics revision. Transport fetch times and unrelated registry edits
must not create a different geometry identity. Old immutable certificates
remain historical evidence, not live entry or held statistical authority and
not current coverage. The ordinary acquisition/materialization loop must
regenerate missing valid witnesses for the affected family. This requirement
does not change the predictive uncertainty formula, manufacture hard-fact
observation authority, or suspend monitoring and independently lawful exits.

### 1d-bis. Interval-censored ENS members (ADDENDUM 2026-09-25 — operator-approved)

ECMWF Open Data mn2t3/mx2t3 are 3-hour aggregates on a 3-hour step grid. A
city's local day rarely aligns with it, so the first and last windows straddle
local midnight. For such a member, the local-day extreme is only known to lie in
an interval `[l_i, u_i]`:

- LOW: `[boundary_min, inner_min]` when the boundary window is strictly colder,
  else the exact `inner_min`.
- HIGH: `[inner_max, max(inner_max, boundary_max)]`.

Excluding the whole row when every member's interval is recoverable is the
biased fallback. It dropped 27 venue families across 2026-09-25..27 into
DATA_DEGRADED, and expired others. This addendum extends
`statistical_calibration_addendum_2026-06-13.md` **D2** (preferred treatment of
an ambiguity set A_i = CAR interval-widening to `[min A_i, max A_i]`; exclusion
only when A_i is unrecoverable or directional) **from observations to forecast
members**: the ambiguity set of each member is its interval, and conservatism
comes from the interval width, with no tuning knob.

**Admission (the single writer is `scripts/ingest_grib_to_snapshots.py`).** A row
is interval-censored, with `forecast_window_attribution_status =
'INTERVAL_CENSORED_TARGET_LOCAL_DAY'`, only if its payload causality is OK and:

- HIGH: the native boundary certificate's only failure reason is
  `boundary_can_exceed_inner`. All 51 members then have validated native windows
  whose clipped union covers the whole local day.
- LOW (Open Data window identity): the row is majority-ambiguous; every member's
  interval evidence is EXACT or INTERVAL, never INVALID; and every member's
  native windows cover the whole local day.

Rows failing with `native_interval_gap` (issued after local-day start: the
elapsed part of the day is invisible, so the upper bound is unbounded) stay
excluded. Unbounded is D2's "unrecoverable" case. The family serves the newest
cycle issued before local-day start. The row persists 51 native-unit bounds in
`provenance_json.member_interval_bounds` (revision
`ens_member_interval_bounds_v1`). Older rows without bounds keep their prior
status and fail closed exactly as before.

**Leakage law.** An interval row keeps `contributes_to_target_extrema = 0`. Every
point-extreme reader requires contributes = 1, so no endpoint, midpoint or
boundary value is ever read as a point daily extreme. The bounds are read only
by the current-evidence shape. The admission predicate is one shared function
(`src/data/forecast_extrema_authority.py`): exact row OR interval row. Every
replacement-chain admission site calls it. The interval class is a new status,
not a relaxation of `FULLY_INSIDE` for point rows.

**Spread.** With members `x_i ∈ [l_i, u_i]` and provider center `μ*`,
`σ_within² + δ_ens² = mean_i (x_i − μ*)²` is separable. Therefore

```
sup_x σ_pred² = σ_between² + mean_i max((l_i − μ*)², (u_i − μ*)²)
```

This is attained by the consistent assignment `x*_i` = the endpoint farther from
`μ*`, but it is a confidence bound, never the served point assignment. The
point assignment must come from the explicit same-member, same-grid native
forecast approximation below, retaining its physical original and possession
identity. A missing point entity is typed UNKNOWN, not an endpoint or midpoint
fallback. In the collapsed single-Normal full-Y representation the point
variance includes the between-provider term exactly once; in an equal-center
mixture that term is already endogenous and is not added to component noise.

`σ_center` is replaced by the upper bound

```
S_max/n + (1 − 1/n)·max((m_l − μ*)², (m_u − μ*)²) + σ_between²/n_eff
```

where m are the means of the lower/upper bounds. This bound dominates every
consistent assignment's value.

The former farthest-endpoint point law and its fitted serving ladder belong to
the old `ensemble_center_scenarios_v6` contract. The new point/confidence split
has no instrument, latency, settlement or fitted width floor and no city mix.
The owning revision is `native_role_point_interval_confidence_v7`.
Activation requires an atomic owning semantics revision across normal seed,
materializer, serialized reader, ENTRY, held redecision and submit replay. Old
certificates retain their original identities and drain through normal
recomputation; they are not restamped. Interval shapes bind both point-model
identity and the independently retained member bounds.

**Bounds (non-Day0 route).** The served interval `q_ucb` of each bin dominates the
served `q_ucb` of every consistent point assignment, each served through its own
shape. The mechanisms are:

- **plausibility hit counts:** #members whose interval meets the settlement
  preimage. This is ≥ every assignment's hits, and Clopper-Pearson is increasing;
- **the Cantelli term** at `σ_sup`;
- **the ENS scenario term:** a supremum over the feasible ENS-center range and a
  certified spread range, with the center at the clamped bin midpoint and the
  closed-form spread optimum;
- **bootstrap center draws:** each seeded draw's mass is bounded over its
  feasible center segment and spread range, and its 95th percentile joins the
  floor;
- **the stress step:** it gives every positive floor its own rows on interval
  evidence, and refuses the band if any floor fails to hold.

**Not claimed.**

- Dominance of the Day0 (observation-absorbed) bootstrap-draw term.
- Conservativeness of `q_lcb`.
- Predictive coverage: `q_ucb` is a band edge, not a coverage guarantee.
- LOW minority-ambiguous rows (1..25 interval members) keep the §1d drop-member
  treatment.

**Predeclared live check and rollback.**

- Interval-backed posteriors are those with
  `current_evidence_shape.interval_censored_member_count > 0`.
- Grade them on settled outcomes, per metric, under the served `N(μ*, σ)`.
- Once n ≥ 30 families per metric, the check passes if the Wilson 95% lower bound
  of 90%-central coverage is ≥ 0.80.
- Failure → report to the operator; no automatic knob.
- Rollback: revert the admission commits. Rows already written with the interval
  status are inert under the prior code, because every prior reader requires
  `FULLY_INSIDE`/contributes = 1.
- Design, derivations and validation:
  `docs/operations/current/plans/ens_boundary_interval_2026-09-25.md`.

### 1e. q construction — fused-N-direct (commit 8541bc93cd)

Flag `replacement_0_1_fused_q_shape_enabled = true`. Fail-closed to soft-anchor q on key-set mismatch or any construction error.

```
q[bin] = bin_probability_settlement(mu=μ*, sigma=σ_pred, bin_bounds, half_step=settlement_step_c/2)
         renormalized to sum=1
q_shape provenance = "fused_normal_direct"
```

`src/calibration/emos.bin_probability_settlement` (lines 427–485) is the single settlement integrator — preimage math, Celsius bounds, half_step=0.5 for precision=1. `src/data/replacement_forecast_materializer.py:1040–1062`.

The Normal is the maximum-entropy distribution determined by the observable
current center and variance; it does not add a fitted tail or a market anchor.
YES and NO are exact complements of this one probability world. Side selection
therefore depends only on executable cost and the same posterior-mean expected
log-wealth objective, never on a side-specific probability recipe.

#### Day0 conditional remaining-path operator

The shared-carrier operator for remaining-hourly paths is
`extreme_observed_then_noisy_future_analytic_gaussian_mixture_v2`. Its point q
integrates the existing Gaussian mixture through the physical max/min and the
canonical settlement preimages, including the observed-boundary atom. It changes
neither the physical distribution nor its fitted parameters. Confidence draws
retain the V1 seed and samples for identical inputs; the V2 content identity binds
the operator and that legacy confidence-draw identity. The legacy `n_point`
parameter affects the confidence seed, but not the analytic point expectation.

Non-shared Day0 remaining-path point probabilities use that same exact mixture
integral. Provisional-report survival is a weighted mixture of the censored
and uncensored future distributions, not an additional observation fact.
Native-unit settlement rounding and the HIGH/LOW boundary atom are preserved.
The numerical operator is recorded in the decision probability provenance;
confidence sampling retains its existing law. Monte Carlo sample count must
not change this point expectation or erase representable Gaussian tail mass
merely because no sample reached it. Full-day forecast distributions are unchanged.

Historical terminal-bin fitted center shifts `b_fit(h)`, including their MLE,
shrinkage and interpolation, are **offline diagnostics only**. The existing
`scripts/fit_day0_remaining_center_bias.py` fitter and
`src/calibration/day0_remaining_bias.py` artifact loader may support offline
attribution and comparison. Evidence may pool per (metric, 2-hour local band),
excluding fast-residual-transported rows; city-day-clustered uncertainty,
N(0, tau2) shrinkage, Paule-Mandel spread and station deviations remain offline
model evidence. Daily or boot refits do not promote an artifact to live authority.

Live remaining members receive no fitted historical additive/affine center
shift: `m'_j = m_j`. Live materialization, reactor rebuilding, public probability
consumption and JIT must not read or apply the fitted artifact. The current
carrier policy is `day0_remaining_center_policy='unshifted_live_v1'`, with
explicit finite numeric zero bias (not bool/string; signed zero is canonicalized
to `0.0` by the writer). A declared carrier with absent policy/bias,
a nonnumeric/nonfinite bias or a nonzero bias is not current authority; normal
seed/materialization must rebuild it. Ordinary non-carriers do not acquire a
new carrier-only gate. Old certificates retain their original attribution:
no restamping, source-age renewal or fallback fitted probability regime.

This prohibition does not remove lawful instantaneous current-state
conditioning: compare the current temperature with the same issued member at
the same observation time and decay that current innovation into future hours.
It is not a historical terminal-bin fit and does not substitute a running
extreme for current temperature. Physical observed boundaries, typed final-daily
provider centers and the same-station native
settlement-product-minus-FAST measurement likelihood retain their source roles.
The new role contract below replaces the historical shared-width/instrument
composition; no old instrument floor survives into that current revision.

The unshifted policy is retained in the current joint identity defined below.
New current certificates are produced by the ordinary rebuild loop; an identity
change does not rewrite immutable historical evidence or authorize deployment.

#### Historical Day0 diurnal-residual mixture: offline only

Historical station/city residual distributions `pi` and fitted mixture weights
`w`, including likelihood fits, shrinkage, local-hour pooling and refit artifacts,
are offline attribution and comparison evidence only. The pure operator in
`src/calibration/day0_diurnal_residual.py` may reproduce an immutable historical
certificate's original transformation; replay does not promote that certificate
or its fitted artifact to current live authority.

Live materialization, reactor rebuilding, public probability consumption and JIT
must not read fitted `pi`/`w` or mix them into either the point simplex or any
confidence draw. The historical transformation
`r'[live] = (1 - w) r[live] + w (1 - dead_mass) pi[live]` is not a live probability
regime, even when causal, fresh, trained only on settled outcomes, or fitted with
`w = 0`. Missing an artifact is not the policy: live does not consult it.
Known declarations of the retired fitted-mixture mechanism require ordinary
seed/materialization rebuilding under the current no-mixture policy. Preserve
the old certificate and its attribution; do not restamp it or renew source age.

This prohibition leaves the source-clock current-evidence Gaussian/ENS spread,
legal same-instant current-temperature conditioning, observed-boundary max/min
and native settlement preimages, typed final centers, city instrument variance,
and same-station native settlement-product-minus-FAST measurement likelihood
unchanged. A current physical observation is not a historical fitted city mixture.
The current additional policy is
`day0_probability_mixture_policy='unmixed_live_v1'` (with the same prefixed EDLI
field); it does not replace `day0_remaining_center_policy='unshifted_live_v1'`
or its explicit numeric zero bias. Construction, replay, coverage and normal
queue RESET bind both policies. The predecessor joint identity was survival
`day0_settlement_channel_revision_model_v34_unmixed_unshifted_remaining_observation_clock_city_instrument_native_boundary_v1`
and historical resolver
`day0_resolver_terminal_composition_v33_unmixed_unshifted_remaining_observation_clock_city_instrument_native_boundary_v1`.
Old zero-weight declarations are not silently given the new policy. A docs
change alone is not implementation or deployment evidence.
The new native X/Y point/confidence carrier uses
`day0_native_domain_roles_v35_point_interval_confidence_v1` throughout normal
construction, persisted replay, ENTRY, held and JIT. Its native role proof,
independent confidence bounds and current revision are mandatory. The fitted
resolver switch cannot select a second current density; its old namespace is
historical replay only. No existing certificate is relabeled by this transition.

2026-10-04 revert record: the v35/v36 conditional measurement-domain law
(15b264df7, 0b5ee41a3) was reverted as a broken deploy, and the v34/v33 law
above was restored at that rollback cut. Its ENS acquisition assumed the Ensemble API accepts
`run`. The provider refuses `run` for every run and model (HTTP 400 "requested
model run is not available"; 200 without `run`), so no post-revision Day0
carrier could materialize. Its midnight coverage domain also refused every
post-midnight run for non-HKO cities. A re-land must acquire ENS on the
provider's actual physical product contract: a latest-run API body requires
exact run binding, while a native instantaneous product requires original
quantity/member/run/grid and causal possession proof. Neither route may invent
issuance or unresolved-past coverage. Deployment must preserve lawful held
monitoring; no empty revised carrier may replace its serving belief.

The shared-carrier V1 is retained only for explicit, immutable historical replay. Current ENTRY and
held-position belief require a complete V2 or typed V3 carrier declaration; ordinary
non-carrier forecasts are unaffected. Old, partial or unknown carrier versions
are uncovered in the existing family coverage/seed loop and are rebuilt from
causal inputs. Valid current materialization clears this family-scoped condition.
Unavailable inputs preserve DATA_DEGRADED/read-only monitoring; version migration
does not authorize liquidation or rewriting historical receipts. Posterior
identity binds both carrier operator and content, even when numerical q coincides.

WU plus same-station fast-tail evidence (`wu_api+same_station_fast_tail`) is
provisional current-snapshot evidence for every supported city and both metrics.
On an open local day, its current temperature conditions the existing remaining-hourly future path;
the uncertain past extreme is then mapped to the settlement channel by the
existing fast-residual transport exactly once, on both point q and confidence
draws. Construct that base carrier with the no-boundary scenario; never make the
fast extreme an absorbing floor/ceiling or invent NOAA/HKO boundary-survival
probability for this source. HIGH and LOW use their respective X/Y domains and
the independent point-width/confidence construction below.

The producer and ENTRY/held/submit readers bind the same source, station,
metric, local day, observation clocks, current-state value, base-carrier identity
and residual-likelihood identity. They must reproduce the transported q and
draws, not just the base-carrier hash. A legacy WU fast-residual posterior missing
its consumed current-state carrier is uncovered for open-local-day decisions. The existing
observation/fusion seed loop rebuilds that exact family from current inputs;
successful complete materialization resets it. Ordinary non-Day0 forecasts and
other source authorities do not acquire this restriction. Same-extreme updates
to a newer causal current temperature must also re-seed an already consumed
carrier, independently of the city name.
The historical combined land-grid/current-state construction was identified by
`day0_settlement_channel_revision_model_v26_land_grid_v3`, or
`day0_resolver_terminal_composition_v25_land_grid_v3` when that existing
composition is selected. Older cohorts retain their original attribution.

The following dual-domain construction is approved for a complete atomic
consumer revision, not a docs-only or empty-carrier activation. The current
joint revisions specified above remain the migration baseline until the
producer, materializer, ENTRY, held redecision and submit replay implement and
verify the new content together. Final revision values are the frozen owning
constants, never a guessed version or a relabeled old probability.

X is the unresolved target-day extreme; Y is the independently typed full-day
extreme. X includes the unobserved interval from the last qualified coverage
clock through decision time and then through local-day end. A current instant
does not prove a cumulative prefix. When that prefix is unknown, keep the
unresolved past domain or fail with typed UNKNOWN; do not start X at run init,
latest spot, capture time or decision merely to obtain coverage.

Native 3h/6h 2m instantaneous knots support only an explicit piecewise-linear
forecast approximation, not an hourly observation or a native interval extreme.
For each member, an interval wholly contained in X uses its original mx2t6 or
mn2t6 extreme. A straddling interval uses that same member's actual 2t
piecewise-linear forecast point, constrained by the actual interval bounds.
For a straddling window, both original mn and mx messages must bind the same
run/grid/member/exact interval and have immutable causal possession evidence.
HIGH uses the original mn as its lower bound and original mx as its upper
bound; LOW uses the same ordered physical bounds. Actual instantaneous knots
inside the half-open subdomain may tighten HIGH's lower or LOW's upper bound.
Interpolated boundary values cannot tighten a physical bound. Missing paired
originals or unknown possession blocks only the affected role until normal
capture and seed redecision supplies the same scoped proof.
Serialize both the chosen point and its actual admissible [l_m,u_m] ambiguity;
do not fabricate exact extrema from interpolation. Left/right support must
exist in the actual product's control/perturbed horizon intersection. The
next-local-midnight knot is interpolation support only, not a next-day point
inside the half-open target. HIGH applies max and LOW min on their own native
units and settlement preimages.

Condition the remaining-X provider paths on the qualified current-state value
and clock. ENS role member points and bounds retain their raw same-run model
values: do not apply a spot innovation to a contained native extreme whose peak
time is unobserved. Bind this distinction explicitly in role identity. The
raw-ENS role center minus current-provider center contributes delta exactly
once; it is not evidence that ENS was spot-conditioned. Full-Y retains its independent whole-target-day
domain and source frontier; it does not borrow X's current-state innovation.
An explicitly qualified bound/proxy report may condition Y only under the
`COARSENED_BOUND_ONLY` forecast approximation, with `prefix_information_kind`,
source coverage and unknown past bound into identity. This is not the complete
prefix posterior and grants no absorbing authority. Apply that coarse scenario
by truncation, never by inventing a boundary atom. If the retained witness proves
a complete same-quantity prefix statistic (including an exact point limit), Y
requires a reproducible joint-prefix likelihood certificate. Without one, reject
only this Day0 Y role as `Y_PREFIX_LIKELIHOOD_UNIDENTIFIED`; retain eligible X
providers subject to the whole-proposal physical-family rule. Unknown prefix
information is not silently relabeled bound-only. Non-Day0 full-Y priors are
unaffected. Normal source qualification/materialization can RESET this role;
the next seed binds the actual revised prefix dependency, never a retagged q.
Compute each role's member points and provider center before this scenario
pushforward. For each role R in {X,Y}:

```
mu_R       = mean_s(role_provider_center_s)
W_R²       = population_variance_m(role_member_point_m)
delta_R    = mean_m(role_member_point_m) - mu_R
sigma_R_point² = mean_m((role_member_point_m - mu_R)²)
               = W_R² + delta_R²
component_R_s  = Normal(role_provider_center_s, sigma_R_point²)
```

Physical-family centers are equally weighted within their role; a regional
path supersedes its own global family. Their between-spread B_R is already
inside this center mixture and must not be added to component noise again.
No instrument, latency, constant or fitted floor augments sigma_R_point. A
proven measurement quantization is separately typed source/settlement
likelihood, not an invented forecast-error variance. A singleton role has B_R=0;
it does not waive the whole proposal's at-least-two-provider requirement.

X and Y have separate provider cohorts, ENS physical quantities, eligible
frontiers and immutable dependency clocks. A legal independent full-Y run need
not equal the newest X run. Fifty-one members of either role bind one actual
product/run/grid and decision-time-past possession; newer usable role evidence
is scoped refresh debt, not permission to rewrite historical membership or
clocks. Publisher issuance may remain UNKNOWN when original possession is
proven. A new current consumer binding may refer to a verified audit-origin
entity without changing that entity's historical origin/role/first clock.
All consumed temperature and static dependencies must pass their own PIT
readback. Initialization, server Date, mtime and writer time never substitute
for issuance or first possession. Unknown sensor AGL, datum, representativeness
or precision forbids height correction and sensor/settlement equivalence, not
honestly labeled model-grid forecast approximation.

For each role's actual member intervals, retain a conservative sigma-squared
domain separately from the point model:

```
sigma_R_lower² = mean_m(distance(mu_R, [l_m,u_m])²)
sigma_R_upper² = mean_m(max((l_m-mu_R)², (u_m-mu_R)²))
```

These bounds do not select point sigma or define posterior mean q/expected log
wealth. Conservative confidence envelopes use the existing coherent carrier:
Gaussian settlement mass must consider interior stationary values, not only
variance endpoints (including LOW). For a truncated Y component, bound its
numerator and normalization separately: [N_min/D_max, N_max/D_min], clipped
to [0,1]; D_min=0 gives [0,1]. This is an explicitly conservative envelope,
not an exact joint optimum or a replacement mean probability. Point q,
confidence draws and immutable point replay share the same chosen point world.
No variance-bound endpoint may be relabeled a mean-EV or log-wealth proposal.

A station product predicting the **final daily extreme** is not a remaining
hourly path. Its center remains separately typed in
`day0_remaining_carrier_final_extremes_c`. When such a component is present,
the shared operator is `typed_remaining_and_final_extreme_gaussian_v3`.
Within each surviving observed-boundary scenario, a final-HIGH Gaussian is
conditioned on being at least that boundary; a final-LOW Gaussian is conditioned
on being at most it. Integrate the truncated, normalized Gaussian over the same
settlement preimages. Do not clamp the center or censor its below/above-boundary
mass into an atom. A no-boundary scenario retains its unconditioned Gaussian.
A zero-variance component contradicting the surviving boundary is unavailable,
not a fabricated point mass at the boundary.

Typed V3 final-daily components use their own Y point width/cohort/frontier,
while remaining-path components use their own X width. Retain source-appropriate
scenario weights and do not borrow X variance for Y or full-Y variance for X.
HKO reported since-midnight 1-minute-mean extrema are provisional reported-product
evidence: incomplete status and uncertain-past scenarios remain explicit, with
settlement equivalence UNPROVEN and no absorbing authority. Do not convert a
reported prefix or an uncertain scenario into irreversible final support.
Point q and draws use the same typed point distributions; confidence envelope
provenance is separate. Identity binds both roles, centers, widths, intervals,
original dependencies, conditioning and scenario/frontier evidence. Historical
replay preserves its original law. Current construction and ENTRY/held/JIT
reproduction must consume the same complete new carrier, not an unused kernel.

The historical combined land-grid-proof, conditional-HIGH and all-city current-state
mechanism used Day0 survival revision
`day0_settlement_channel_revision_model_v26_land_grid_v3` and resolver revision
`day0_resolver_terminal_composition_v25_land_grid_v3`. Predecessor revisions,
including survival v25 and resolver v24, retain their historical attribution.
The predecessor v34/v33 joint policy is not silently relabeled by this record.
The native dual-domain revision is frozen from the owning constants above and
consumed atomically; never change a prefix on an old probability to migrate it.

For either approved role, its original-role ENS within-spread and displacement
from its own simultaneous provider center account for component model error.
The raw ENS role is not spot-conditioned. The X provider conditioning witness
and its discrepancy from that raw role remain explicit; Y retains its independent
full-day provider and ENS domains. No unproven instrument/latency floor or
fitted residual may fill missing current role evidence. The producer, held/ENTRY rebuild,
and immutable submit replay bind the original-role shape's member/run and provider-conditioning
witness and semantics revision. Older certificates must be rematerialized for
new action, while their original realized-fill attribution remains historical.
SCOPE is the consumed family/local date/metric and exact role dependencies.
DRAIN is its ordinary observation/source seed and materialization loop; RESET
is a complete new certificate reproduced by ENTRY, held redecision and JIT.
Before replacement, lawful old belief may continue monitoring only under the
approved migration rule; it cannot waive new-action revision requirements.
Unavailable inputs retain DATA_DEGRADED monitoring, not a global pause,
liquidation command, old-q restamp or alternate live regime. Loading requires
complete private consumption/replay antibodies and current normal required
availability proof; a source-only rollout or docs amendment is insufficient.

The source-clock posterior, finite-member/moment band, topology, and causal
identity remain bound into the Day0 witness and are reproduced at submit. A
missing or invalid source-clock carrier still fails closed; this separation is
not permission to fall back to historical residual width, an unbound path set,
or a market-price anchor. Any change to this conditional operator increments
`DAY0_PROBABILITY_SEMANTICS_REVISION`, so settlement attribution never pools the
new law with an older realized-capital record.

The possession-bound provider-run cutover is such a change. It prevents local
capture clocks or mixed provider runs from entering one remaining-path witness,
and is identified as the v10 Day0 probability cohort. Realized v9 fills remain
historical evidence; they cannot reject or validate v10. RiskGuard may reject
v10 only from later decision-time v10 q/book witnesses joined to verified
settlements under the current global selector.

The source-clock observation clock is supporting provenance when the global
action q is rebuilt from the current remaining-path random variable. Its age
alone must not suppress statistical ENTRY between normal provider publications:
the remaining-path builder prices the unresolved interval from the latest causal
current-state ledger through decision time. This exception exists only when that
builder produces a complete current simplex. Missing carrier identity, current
observation, hourly trajectories, topology, or submit-time equality still fails
closed. Any route that uses the source-clock posterior itself as action q retains
the strict fast-observation ENTRY freshness contract.

That exception is atomic across both conditioning seams: when the named current
source and supporting carrier clocks differ while preserving the same physical
extreme, the global remaining-path route binds current action state and records
the carrier-clock lag instead of rejecting ENTRY. The values, unit, named source,
station identity, decision-time causality, and current action-q content remain
exact; a changed extreme or a carrier clock that cannot be reconciled to its
named current source still blocks. Direct source-clock action routes do not
receive this clock-advance permission.

WU changed-payload revision history is a statistical modifier, not settlement
truth. Only a revision the observation writer applied to canonical state enters
the Jeffreys-Beta transition denominator. A quarantined payload mismatch did
not move current truth and therefore cannot be relabeled as a boundary
retraction inside q. When one city has zero applied changed-payload transitions
in the bounded causal lookback, new capital remains ineligible: ENTRY may not
substitute an empirical likelihood. Existing capital must remain re-decidable,
so HELD_MONITOR and REDUCE_ONLY_EXIT may use the same Jeffreys-Beta model at its
zero-observation prior. The applied-revision or prior-only basis is serialized
into the probability content and a new Day0 semantics revision; it never
creates deterministic payoff support and cannot pass the ENTRY/HELD equality
gate for a history-free BUY.

For a Day0 BUY that has already passed current ENTRY authority, the immediate
HELD_MONITOR gate compares the facts that can change that fixed action's
economics: witness type, Day0 semantics revision, family/token bindings,
resolution/topology, probability band, sample matrix, and point simplex. Both
witnesses retain their own immutable source/posterior/certificate identities;
those lane-specific provenance hashes may differ only when all action-q content
above is exact. Any sample, point, topology, band, binding, witness-type, or
semantics-revision change remains a candidate-local fail-closed divergence.
This exception cannot legalize the history-free WU prior: ENTRY still fails
before equality is evaluated, while the prior remains held/reduce-only.

### 1f. Finite current-evidence tail limit

The Normal point vector is a model expectation, not permission to claim
arbitrary certainty. For each bin, count the `h` current ENS members inside its
settlement preimage. Its one-sided `(1-alpha)` exact Clopper-Pearson upper bound
is:

```
q_ucb_sample(h, N, alpha) = BetaInv(1-alpha; h+1, N-h)
q_ucb_min(N, alpha) = q_ucb_sample(0, N, alpha) = 1 - alpha^(1/N)
alpha = 0.05
N and h = current target-specific ENS evidence
```

For `N=51`, the finite-member term is `q_ucb_min≈0.05705`; therefore even before
other uncertainty the executable complement satisfies
`q_lcb(NO)=1-q_ucb(YES)≤0.94295`. This is optimistic because it treats all
members as independent; member dependence cannot justify a narrower interval.

The Normal tail is also an assumption, not current evidence. For a settlement
bin wholly above or below the current center, let `d` be the distance from the
center to the nearest settlement-preimage boundary. Trusting only the current
predictive mean and variance gives the exact one-sided Cantelli ambiguity mass:

```
q_ucb_moment = sigma_pred^2 / (sigma_pred^2 + d^2)
q_ucb_required = max(q_ucb_sample, q_ucb_moment)
```

This makes the executable band distribution-robust while leaving the Normal
point estimate intact. For example, a current center `36.5151°C`, predictive
sigma `0.527789°C`, and `39°C` WMO point bin (`[38.5,39.5)`) imply
`q_ucb_required≈0.0661`, hence executable NO confidence below `0.934`, even if
the Normal point complement displays near `0.9999`.

`replacement_forecast_materializer._build_fused_q_bounds` writes this ambiguity
into disjoint stress rows of one coherent simplex carrier, then derives
`q_lcb_json` / `q_ucb_json` from that carrier. The Normal point `q_json` is the
fixed-action posterior predictive mean; the bounds remain confidence evidence,
not a second payoff distribution or admission objective. The rule is symmetric:
YES consumes the point column, NO consumes its exact complement, and submit-time
evidence rebinds both the current point and the same current-evidence bounds.
Current member values stay in memory for settlement-preimage hit counts;
their hash, count, and resulting per-bin bounds are persisted as provenance. The
historical `FAR_TAIL_LCB_FLOOR` is not applied on this source-clock route. Day0
absorbing observation facts dominate the forecast ambiguity band and are not
widened by it. Local midnight without a causal target-day observation is not an
absorbing fact: held-position redecision continues to use the fresh replacement
carrier until the first observation arrives, while entry remains coverage-gated.
Missing or invalid current members block source-clock bound
construction; it never invents an arbitrary probability cap or historical
fallback.

### 1e-bis. Post-2026-06-12 q corrections (ADDENDUM — fitted artifacts + single authority)

The q chain gained three fitted corrections on 2026-06-12. As of 2026-07-11 they
are offline historical comparisons only; they must not transform a live q:

1. **Fitted σ-scale + uniform mixture** (`state/sigma_scale_fit.json`, sole writer
   `scripts/fit_sigma_scale.py`, weekly refit): `q_adj = (1−w)·N(σ_impl·k) + w·(1/n_bins)`,
   joint MLE on settled Bernoulli outcomes. First fit (provenance 20c6040cb39dc327):
   C k=1.5833 [1.32,1.88], w=0.2811 [0.17,0.41], n=215; families with n<60 refuse
   (F refused at n=47, stays identity). Provenance fields `sigma_scale_k_applied`,
   `uniform_mixture_w_applied` on every posterior. Cure for the C3 ring-bin
   over-peaking (mode-bin calibration ratio 0.514→0.961).
2. **Settlement σ-shape floor = per-cell data availability** (Wave-2 item 6, commit
   479cb34446): the floor applies whenever the fitted floor cell exists, inert when
   absent. No flags. A missing cell no longer degrades q-mode.
3. **Market-anchor q_lcb cap = permanent constraint** (verdict INTERNALIZE,
   `docs/evidence/sigma_scale/2026-06-12_anchor_cap_overlap.md`): orthogonal to the
   σ fit (bind rate rises post-fit); one-sided, only lowers q_lcb_no against the
   α=0.4 model/market blend. OPEN: α is hardcoded; fitted basis is a registered
   follow-up.

**Single q authority (U1, `docs/authority/regime_unification_2026-06-12.md`):** the
legacy baseline LCB cap on the live path is DELETED (commit 479cb34446) — the
former `min(baseline, replacement)` joins are gone; the baseline is receipt
comparison provenance (`comparison_q_lcb_reference`). The honest no-replacement-data →
baseline fallback for genuinely-baseline strategies remains. The settlement-refuted
EB bias correction and the bias_treatment_v2 branches are deleted with their code.

**Evidence for shape replacement** (`docs/evidence/2026_06_09_final_form/aifs_replacement_experiment.md`, n=39, target 2026-06-08):

| Arm | LogLoss | Brier | Top-bin hit |
|---|---|---|---|
| A: live AIFS-vote shape | 11.07 | 0.997 | 25.6% |
| B: fused-N-direct (this form) | **1.51** | 0.712 | **46.2%** |
| C: live shape + μ* (center-only) | 1.71 | **0.695** | 46.2% |

11/39 cells (28%) had AIFS shape assign exactly zero probability to the winning bin (vote-support truncation). Full-support Normal makes zero-coverage UNCONSTRUCTABLE. Center correction (μ*) delivers ~97% of gain; shape swap eliminates the catastrophic-zero category. Note: experiment used lead-0 same-day analyses → μ* accuracy benefit is slightly inflated; shape comparison (B vs C) is unaffected.

---

## 2. Instrument Universe and Selection Law

### 2a. Full instrument set (`src/forecast/model_selection.py:58–91`)

| Role | Model(s) | Domain / lead cap |
|---|---|---|
| Anchor (prior) | `ecmwf_ifs` | global, all leads |
| DECORR_GLOBALS | `gfs_global`, `icon_global`, `gem_global`, `jma_seamless`, `ukmo_global_deterministic_10km` | global; current values may use the eligible same-product `previous_runs` serving law below (K2) |
| GLOBAL_LIKELIHOOD | DECORR_GLOBALS + `icon_eu` + `ncep_nbm_conus` | per-polygon for domain-gated |
| REGIONAL | `icon_d2` (2km, Central-EU, lead≤1), `meteofrance_arome_france_hd` (1.3km, France, lead≤1), `ukmo_uk_deterministic_2km` (2km, UK, lead≤2) | own polygon gates |
| icon_eu | `icon_eu` (7km, lat 29–71N / lon −24–45E, lead≤3) | restored 2026-06-09 (6860f00a21) |

**PROVIDER_FAMILIES** — one rep per physical family, most-specific-first (`model_selection.py:82–91`):
- `ICON_FAMILY = (icon_d2, icon_eu, icon_global)` — icon_d2 in Central-EU, icon_eu in ICON-EU polygon, icon_global elsewhere
- `NCEP_FAMILY = (ncep_nbm_conus, gfs_global)` — NBM replaces gfs in-CONUS (lead≤3); gfs_global elsewhere. NBM blends NCEP/GFS → never coexist
- `UKMO_FAMILY = (ukmo_uk_deterministic_2km, ukmo_global_deterministic_10km)` — UKV in UK, global elsewhere

**Coordinate authority:** `config/cities.json` is the SINGLE coordinate source. 5 coarse coordinates re-pinned to verified WU/METAR station ARPs 2026-06-09 (e80c101c4c): Amsterdam EHAM (52.3086/4.7639), Chengdu ZUUU, Istanbul LTFM, Shanghai ZSPD, Zhengzhou ZHCC. Amsterdam was the material case (1.9 km off, inside icon_d2 2km domain).

**Generalized current-value serving rule (K2; GEM was the first instance):** the single serving builder admits only the same model/product, city, metric, and target date.  A carrier-bound read prefers selected-cycle `single_runs`, then selected-cycle `previous_runs`, then the newest eligible prior-cycle row no later than the carrier.  A source-clock live read instead serves each provider's newest row possessed by decision time; the ECMWF ENS cycle remains the shape carrier, not a ceiling on deterministic provider values.  Missing, future, over-age, or product-mismatched rows stay dropped; in particular, ECMWF `ifs025` history cannot stand in for the 9 km live center.  Every admitted row preserves its real `served_via` and `served_cycle`, so substitution is explicit rather than silent.

**Supersession follows the complete consumed evidence, not numeric equality (2026-09-30).** Equality of `(forecast_value_c, lead_days)` does not prove that a new provider row is a harmless cycle relabel. Provider cohort, source/possession clocks, native geometry, physical proof dependencies and carrier identity can change the current center/spread or its authority even when those two scalars agree. Missing consumed evidence or missing proof cannot establish equivalence. The existing scoped raw-input HWM therefore preserves its complete source/proof/cohort checks; a new unconsumed row triggers normal seed/materialization redecision rather than borrowing the old certificate. A repeat of the same exact current evidence remains zero-cost and does not renew source clocks. Historical rebuild-pair statistics do not authorize a numeric-only alias or establish that an older cycle is always conservative. SCOPE is the used provider and its exact city/day/metric family; DRAIN is the existing input-HWM and seed/materialization loop; RESET is a newly consumed, currently valid certificate, with original rows and capture clocks preserved.

**K3 provider completeness:** 5 declared providers — NOAA/gfs|nbm, DWD-ICON one-of-{d2,eu,global}, CMC/gem, JMA/jma_seamless, UKMO one-of-{global,uk2km}. Family-aware check; ≥ 4/5 required or WARNING emitted. Commit 2b6936d3b5 surfaces subprocess WARNINGs to daemon log.

### 2b. Precision city coverage

24/54 cities have regional expert coverage:
- icon_eu: 7 EU-edge cities (Madrid, Moscow, Istanbul, Ankara, Helsinki, Tel Aviv, Warsaw) + 5 Central-EU shared with icon_d2
- icon_d2: 5 Central-EU cities (Munich, Milan, Paris, Amsterdam, London partial)
- arome: 2 cities (Milan, Paris — polygon lat_max=51.10 covers only these two; London/Amsterdam/Madrid/Munich are outside)
- ukmo_uk_2km: London
- nbm_conus: 12 CONUS + Toronto

30 cities globals-only. **Refuted candidates** (settlement-graded, not added): HRRR (fixed-lead MAE worst in CONUS, lead-confound in prior audit), jma_msm (Tokyo MAE +36% vs icon_global, −1.2 °C cold bias), cma_grapes (pooled +30% vs ECMWF, −0.4..−2.9 °C cold), knmi_arome_netherlands (Amsterdam +2.34 °C warm bias), dmi/metno (Helsinki/Warsaw worse than incumbents), bom_access_global (all-null previous-runs archive), gem_hrdps (tied NBM for Toronto, large warm biases for US border cities), kma_seamless/kma_ldps (archives discontinued 2026-04-12/04-02), arpae-icon-2i (Milan MAE 0.974 vs icon_d2 0.846 — resolution ≠ skill, refuted third time). **Open:** KMA forecast-latest mode for Seoul/Busan (LDPS showed MAE 1.412 vs ECMWF 1.802 on n=42 overlap before archive cutoff).

**Milan/arome verdict (commit b15253599b, logged in model_domain_polygons.yaml):** geometric speculation refuted by data. Fixed lead-1 HIGH n=76: arome MAE 0.868 / bias +0.18 = 3rd-best instrument, beats ecmwf_ifs (1.304). Milan STAYS.

---

## 3. Structural Laws (Antibodies)

These make error categories **unconstructable**, not merely less likely.

**L1 — Coverage never implies cycle-currency.** `covered` (posterior exists) says nothing about which cycle it was built on. Five instances fixed 2026-06-09:
1. `replacement_forecast_current_target_plan.py:36` — download gate (94b584cc3f): requires downloaded HWM ≥ available cycle
2. `download_replacement_forecast_current_targets.py:177` — `include_covered=True` when cycle stale (9c594c9fc3)
3. `replacement_forecast_live_materialization_queue.py` — seed checks posterior AND `expires_at > now` (pre-existing, confirmed clean)
4. `_download_bayes_precision_fusion_extra_raw_inputs_if_needed` — coverage filter removed; replaced by row-level per-(model, city, target, metric, cycle, endpoint) skip (df8199ef8e)
5. Download gate for replacement anchor — same root (9c594c9fc3, instance 3 in commit)

**L2 — Row-level only-missing fetches.** Extras download preloads logical keys already persisted for the cycle and skips per-row. Steady-state cost = only-missing; self-healing on re-run regardless of coverage state. Commit df8199ef8e.

**L3 — Anti-silent-sink.** Each materialization runs as a subprocess with `capture_output=True`; warnings reach only the per-request sidecar JSON, never the daemon log. Fix (2b6936d3b5): queue processor re-emits WARNING/ERROR lines at queue level. Two more sinks fixed in universality sweep (8f20d39863): etl_diurnal/etl_temp_persistence stdout WARNINGs, arm_gate_emit producer success-path output.

**L4 — Domain gate == data presence == settlement skill.** Milan/arome: data present and settlement-graded before polygon boundary overrides inclusion. The polygon is the OUTER bound; data-presence-with-skill is the inner bound. Source-identity law: a model's live-inference product must match the product its de-bias history was fit on. Violated cases: EB-bias wrong-set (ENS bias over-corrects 9km IFS anchor → disabled, ff7f33dd5b), gem_seamless rejection (serves HRDPS/RDPS for NA cities, wrong physical product).

**L5 — No in-fusion provider double-count.** PROVIDER_FAMILIES covers all active same-provider pairs: ICON family (d2↔eu↔global), NCEP family (nbm↔gfs), UKMO family (uk2km↔global). ECMWF (ifs + AIFS) is not a BAYES_PRECISION_FUSION concern — AIFS never enters BAYES_PRECISION_FUSION Bayes. Universality sweep P6: CLEAN (8f20d39863).

**L6 — Explicit model-id registration.** Promoted models (`ncep_nbm_conus`, `ukmo_global_deterministic_10km`, `ukmo_uk_deterministic_2km`) have explicit entries in `OPENMETEO_MODEL_IDS` (`src/data/bayes_precision_fusion_capture.py`) — no identity-fallback implicit mapping. Commit 8f20d39863.

---

## 4. Authority Status and Risk Posture

**Live runtime semantics:** Materialization writes only `runtime_layer='live'` rows that satisfy the complete current-evidence contract. Execution and monitoring consume that row set only. Incomplete rows are refused rather than persisted under a second authority class.

**q_lcb requirement:** The live replacement path requires fused-q certified bootstrap `q_lcb_json` / `q_ucb_json`. On the source-clock route the bootstrap consumes `σ_center`, `σ_pred`, and `member_count` from the same current-evidence shape; the coherent carrier includes the finite-member tail limit in §1f. A missing current ENS carrier or bound blocks live materialization/readiness; it must not fall back through historical residual calibration, baseline, or retired experiment provenance.

**Action probability:** For a fixed binary BUY or statistical SELL, posterior
expected log terminal wealth is evaluated at the current posterior predictive
mean `q_json`. `q_lcb_json` / `q_ucb_json` certify uncertainty and freshness but
do not replace the action probability or impose an additional ambiguity utility.
Every JIT submit certificate must reproduce the current point and confidence
carrier; a changed point forces re-decision.

**FSR dependency (commit 8c6e028066):** The replacement forecast is an OVERLAY authority — it writes posteriors and readiness that depend on `baseline_b0 (ecmwf_open_data)` source_run. It emits no source_run or ensemble_snapshots of its own. The opendata baseline producer (mx2t6_high / mn2t6_low) MUST remain enabled; disabling it starves FSR.

**Statistical vs absolute honesty:**
- Forecast superiority (bin-hit, MAE, bias) is settlement-graded, temporal holdout (TRAIN≤2026-05-10 / TEST 05-11..06-08), no look-ahead.
- BACKTEST RESULT (wszeibgi0): de-bias is the dominant lever. Non-regional 39 cities: bin-hit +4.6 pt, MAE 1.435→1.305, bias −0.515→+0.071. Regional 12 cities: bin-hit +11.6 pt, MAE 1.101→0.850. Optimal = per-model de-bias then equal-weight.
- Realized trading EV is measured only from forward real fills. The
  strategy-selection settlement-2026-06-09 analysis showed in-sample EV was
  inflated; forward temporal holdout collapses to +1.2¢..−2.7¢ with large
  day-variance. That result is learning and falsification evidence, not a
  circular admission prerequisite: a current causal probability witness that
  passes the live content, freshness, JIT book, portfolio, expected-log-growth,
  and Fractional Kelly contracts may enter the capital auction before its first
  fill. Forward fills then update walk-forward attribution and may reduce or
  remove future authority; their absence cannot itself block every action that
  could produce them.

**Iron rules:** (1) coverage != currency — five instances fixed today, zero tolerance for recurrence. (2) source-identity — a model's live product must match its de-bias product. (3) no in-sample promotion. (4) buy_no derives from forecast YES bin — cold-bias corrupts the family. (5) operator promotion requires settlement-graded evidence at the same evidence class as icon_eu.

### 4a. Staleness degrade ladder (2026-07-17 addendum)

Operator go 2026-07-17 ("除了需要累积的内容都执行 使用数学和统计就能证明一切") ratified
executing the previously-pending consult (d) ladder. It REPLACES the binary
fresh / fail-closed posterior-staleness regime with a GRADED ladder keyed on the
served posterior's AGE at a decision, `age = decision_time − source_cycle_time`.
Every boundary is DERIVED from settled live history, not guessed — full numbers,
n, and p-values in
`docs/evidence/upstream_physical_2026_07_17/staleness_ladder_derivation.md`.

```
GREEN   age ≤ 18h : full trading, UNCHANGED.
AMBER   18h < age ≤ 24h : trading continues with the qualified current-evidence
        predictive width unchanged; no historical fitted age variance is added.
RED     24h < age < 30h, OR a newer live-eligible cycle detected-not-active :
        NO new entries for the family; resting makers cancel; held-position
        monitor/exit lanes stay FULLY ACTIVE (isolation of ENTRY, never monitoring).
EXPIRED age ≥ 30h : existing fail-closed law, UNCHANGED. The ladder never
        weakens replacement_source_cycle_max_age_hours.
UNKNOWN unparseable/absent source_cycle_time : caller keeps its binary law
        (the ladder never turns a classification failure into a NEW block).
```

**Derivation basis (paired, target-fixed, walk-forward; n=1479 settled targets).**
Fresh serving age (`computed_at − source_cycle_time`) is p50 7.19h / p99 15.75h,
so `GREEN ≤ 18h` covers a genuinely-fresh cycle with margin. The paired
center-error variance increment is significant from one cycle-generation of
staleness onward — fitted AMBER-band inflation **high v = 0.362 degC² (p=6.3e-8,
n=1009), low v = 0.244 degC² (n=156)**, 13–19% of the base predictive variance
(p50 1.86 degC²). These historical increments are offline diagnostic evidence,
not a live predictive-variance component or permission to modify current q/draws.
Past 24h the systematic center BIAS (|drift| ≥ 0.47°C) becomes a large,
UNCORRECTABLE-by-variance fraction of the error and only ~6h remain to the
EXPIRED wall — hence RED stops entry rather than pretending to price it.

**Mechanism (current width, independent age eligibility).**
- Classification: `src/data/staleness_degrade_ladder.classify_posterior_staleness`
  (pure; reads the EXPIRED horizon from `replacement_forecast_cycle_policy` so the
  two can never drift on that one number). Boundaries are module constants, not
  new config knobs.
- Offline age-inflation artifact: `scripts/fit_posterior_age_inflation.py` (walk-forward,
  deterministic, RO over the forecasts DB) → `state/posterior_age_inflation/`
  ACTIVE.json + sha256, inspectable by `src/forecast/posterior_age_inflation.v_for`
  only for offline diagnostics. Live consumers do not read this fitted artifact
  or add v: both ordinary NOAA and shared-carrier paths preserve canonical current
  predictive width. Presence or absence of an age-fit artifact cannot change the
  same current consumer's point vector or coherent draw matrix; full-prior and
  remaining-window consumers retain their distinct legal physical quantities.
  Consumer-route
  cache identities force normal re-preparation instead of reusing an older
  polluted witness. JIT independently rejects that stale witness and a normally
  prepared current witness restores authority; source rows/clocks are not restamped.
- RED entry isolation: `read_replacement_forecast_bundle` returns BLOCKED
  `REPLACEMENT_STALENESS_RED_ENTRY_ISOLATED` (entry-decision authority). The
  held-position monitor/exit read paths (`position_belief`, `portfolio.Position`)
  are independent and untouched — same isolation-of-entry contract as
  `FAMILY_ENTRY_BLOCKED`.

**Role distinction.** The per-model explicitly declared precision/center operator
is a separate source role and is not changed by this consumer repair. Historical
posterior-age v is not a live variance input, whether an offline artifact exists
or not. The 18/24/30-hour eligibility boundaries, newer-cycle isolation and all
source completeness/native/ground gates remain unchanged.

**DEVIATION from the consult (d) sketch:** the "two-provider requirement relaxes
to 1 provider + ENS on AMBER" clause is NOT implemented. Relaxing the source-clock
completeness gate is a gate WEAKENING and no settlement-accuracy proof that a
1-provider AMBER posterior settles as well as a 2-provider one was produced; per
"never weaken a gate" + "使用数学和统计就能证明一切" it is deferred to its own
walk-forward provider-count-vs-settlement derivation. AMBER keeps the existing
provider-completeness requirement.

---

## 5. Test / Antibody Index

| Test file | Category killed |
|---|---|
| `tests/test_bayes_precision_fusion_persisted_read_lead_robust.py` | Lead-calendar mismatch makes fusion fire on 0 cells (140d75ff6d) |
| `tests/test_bayes_precision_fusion_thin_anchor_retained.py` | Anchor center dropped from EQUAL_WEIGHT cells (49492f1528) |
| `tests/test_bayes_precision_fusion_gem_current_value_previous_runs_fallback.py`, `tests/data/test_previous_runs_substitution.py`, `tests/data/test_fusion_upgrade_trigger.py` | Generalized same-product current-value serving: `single_runs` preference, eligible prior `previous_runs`, source-clock newest-possession selection, explicit provenance, and rejection of future or product-mismatched ECMWF `ifs025` rows |
| `tests/test_replacement_download_cycle_currency_gate.py` | Coverage-vs-currency conflation freezes anchor indefinitely (94b584cc3f) |
| `tests/test_stale_cycle_download_includes_covered_targets.py` | Covered-target filter starves new-cycle raw inputs (9c594c9fc3) |
| `tests/test_download_row_level_skip_only_missing_fetches.py` | Coverage filter on extras download; instance 5 coverage!=currency (df8199ef8e) |
| `tests/test_queue_surfaces_subprocess_warnings.py` | Subprocess WARNINGs silently discarded in sidecar void (2b6936d3b5) |
| `tests/test_replacement_fused_q_shape.py` | Current-evidence Wellington no-edge counterfactual; YES/NO complement symmetry; historical shape transforms cannot enter source-clock live q |
| `tests/test_icon_eu_is_the_dwd_rep_inside_its_own_icon_eu_domain.py` | icon_eu borrowing icon_d2 box drops 7 EU-edge cities (6860f00a21) |
| `tests/test_replacement_0_1_anchor_eb_bias_source_match.py` | ENS bias applied to IFS anchor (wrong-set over-correction) (ff7f33dd5b) |
| `tests/test_forecast_live_opendata_producer_required_for_fsr.py` | FSR starvation when opendata baseline producer disabled (8c6e028066) |
| `tests/test_replacement_live_authority_evidence_gate_wiring_honesty.py` | Dead-but-advertised evidence gate misleads operator (54a53334a9) |
| `tests/test_bayes_precision_fusion_candidate_accrual_models.py` | Family coexistence impossibilities; lead fallback; single-fetch-per-target (a70436d478) |
| `tests/test_bayes_precision_fusion_port_fidelity.py` | T2 math port reproduces proof engine (Paris/high/L1 2025-12-26 → μ*=4.3137, sd=0.7259) |
| `tests/test_staleness_degrade_ladder.py` | §4a unchanged band edges (18/24/30h), newer-cycle→RED, UNKNOWN binary law; live current sigma ignores fitted age variance and missing current proof is not supplied by fit; monitor/exit isolation preserved |
| `tests/test_fit_posterior_age_inflation.py` | §4a offline-only age diagnostic fitter: positive v, walk-forward exclusion, monotone-in-age and byte-determinism; no live promotion |
| `tests/test_replacement_forecast_bundle_reader_staleness.py` | §4a RED entry-isolation BLOCKED + AMBER-still-binds at the single bundle-reader gate (2026-07-17) |

27/27 materializer+fusion+wiring green at ship. 61/61 green post-promotion (a70436d478).
