# Replacement Forecast Final Form (2026-06-09, operator-ratified)

**Status:** Live replacement probability law. Runtime rows use `forecast_posteriors.runtime_layer='live'`; `LIVE_AUTHORITY`, `trade_authority_status`, and shadow/diagnostic labels are not live execution authority.
**Supersedes:** `BAYES_PRECISION_FUSION_SPEC.md` (deleted).  
**Created:** 2026-06-09  
**Last audited:** 2026-07-15 (source-clock live q retains absolute ENS/provider-center disagreement and its executable band combines finite-member and distribution-free moment ambiguity symmetrically for YES/NO)
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
fully inside the target local day, contribute to the target extrema, have at least
20 finite °C members, and have `source_available_at <= computed_at` and
`source_cycle_time <= carrier source_cycle_time`. Two positively weighted current
providers are the minimum. Missing or invalid current shape blocks the live
posterior; it never falls back to a historical residual, constant width, fitted
floor, or uniform mixture.

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

The older walk-forward residual width remains diagnostic for non-source-clock
carriers; it is not a fallback into the live source-clock probability regime.

**2026-07-17 addendum — anomaly transport for a stale-but-coherent ENS shape.**
Measured cost of the pre-addendum same-cycle-only rule: new scopes waited a mean
14.6h (p50 6.8h) for the slow ENS-baseline leg while every other instrument was
already fresh, costing 0.24–0.41°C of avoidable center error at scope-open
(docs/evidence/upstream_physical_2026_07_17/consult_freshness_decoupling_verdict.txt
§P2-B; docs/operations/current/plans/upstream_data_physical_2026-07-17.md). Reusing
an older but internally coherent ENS cycle is licensed ONLY as a location-shape
transport model, never as same-instant disagreement evidence:

```
shape_lag_hours = carrier_cycle_time - ens_cycle_time
translation_applied = shape_lag_hours > 0

if translation_applied:
    X'_j        = μ* + (X_j - mean(X))     # anomalies recentered on the fresh center
    δ_ens       = 0                         # operational: zeroed, never folded into σ_pred
    δ_ens_raw   = μ* - mean(X)              # provenance-only (research / regime-discordance)
    σ_pred      = sqrt(σ_within² + σ_between²)   # no δ_ens² term -- avoids double-counting μ*
else:
    # shape_lag_hours <= 0 (same ENS cycle as the carrier): §1d above, unchanged.
```

Translation is a pure shift and preserves `σ_within` exactly. The translated
members `X'_j` — not the raw pre-translation members — are the operative sample
for settlement-preimage hit counting and its downstream Clopper-Pearson tail.
`ens_center_delta_raw_c` is carried in `current_evidence_shape` provenance for
research only; it must never re-enter `σ_pred`. A translated row is stamped
`semantics_revision = "ensemble_anomaly_transport_v1"` (module
`src.data.replacement_forecast_cycle_policy`, constant
`ENSEMBLE_ANOMALY_TRANSPORT_SEMANTICS_REVISION`), distinct from the same-cycle
`"ensemble_center_disagreement_v1"` above — so the existing revision-mismatch
convergence machinery (`current_evidence_shape_semantics_mismatch`) applies only
to stale-shape rows, never a universe-wide replay. The 30h source-cycle
staleness bound (§ above, `replacement_source_cycle_max_age_hours`) remains the
outer catastrophic-guard on how old the selected ENS cycle may be; this
addendum does not widen it, and carries no sigma age-inflation term of its own
(a walk-forward-fitted `γ_g · age/6` term, per the consult's EMOS-like scale, is
deferred to a separate calibration slice).

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
therefore depends only on executable cost and the same robust objective, never on
a side-specific probability recipe.

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
into disjoint stress rows of the same coherent simplex carrier consumed by the
global lower-CVaR selector, then derives `q_lcb_json` / `q_ucb_json` from that
carrier. The Normal point `q_json` remains immutable audit provenance. The rule
is symmetric: YES consumes the column, NO consumes its exact pointwise
complement, and both ranking and submit-time bounds see the same current-evidence
world. Current member values stay in memory for settlement-preimage hit counts;
their hash, count, and resulting per-bin bounds are persisted as provenance. The
historical `FAR_TAIL_LCB_FLOOR` is not applied on this source-clock route. Day0
absorbing observation facts dominate the forecast ambiguity band and are not
widened by it. Missing or invalid current members block source-clock bound
construction; it never invents an arbitrary probability cap or historical
fallback.

### 1e-bis. Post-2026-06-12 q corrections (ADDENDUM — fitted artifacts + single authority)

The q chain gained three fitted corrections on 2026-06-12. As of 2026-07-11 they
remain diagnostic for the source-clock route and may still apply to explicitly
non-source-clock carriers; they must not transform a current-evidence live q:

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
| DECORR_GLOBALS | `gfs_global`, `icon_global`, `gem_global`, `jma_seamless`, `ukmo_global_deterministic_10km` | global; gem previous_runs only (K2) |
| GLOBAL_LIKELIHOOD | DECORR_GLOBALS + `icon_eu` + `ncep_nbm_conus` | per-polygon for domain-gated |
| REGIONAL | `icon_d2` (2km, Central-EU, lead≤1), `meteofrance_arome_france_hd` (1.3km, France, lead≤1), `ukmo_uk_deterministic_2km` (2km, UK, lead≤2) | own polygon gates |
| icon_eu | `icon_eu` (7km, lat 29–71N / lon −24–45E, lead≤3) | restored 2026-06-09 (6860f00a21) |

**PROVIDER_FAMILIES** — one rep per physical family, most-specific-first (`model_selection.py:82–91`):
- `ICON_FAMILY = (icon_d2, icon_eu, icon_global)` — icon_d2 in Central-EU, icon_eu in ICON-EU polygon, icon_global elsewhere
- `NCEP_FAMILY = (ncep_nbm_conus, gfs_global)` — NBM replaces gfs in-CONUS (lead≤3); gfs_global elsewhere. NBM blends NCEP/GFS → never coexist
- `UKMO_FAMILY = (ukmo_uk_deterministic_2km, ukmo_global_deterministic_10km)` — UKV in UK, global elsewhere

**Coordinate authority:** `config/cities.json` is the SINGLE coordinate source. 5 coarse coordinates re-pinned to verified WU/METAR station ARPs 2026-06-09 (e80c101c4c): Amsterdam EHAM (52.3086/4.7639), Chengdu ZUUU, Istanbul LTFM, Shanghai ZSPD, Zhengzhou ZHCC. Amsterdam was the material case (1.9 km off, inside icon_d2 2km domain).

**gem_global current-value rule (K2, commit edc598b440):** open-meteo single-runs API does NOT serve `cmc_gem_gdps_15km` (curl-verified; modelRunUnavailable even at 00z). gem is served from its `previous_runs` row at the SAME natural key `(city, metric, target_date, source_cycle_time)` — source-identical to its de-bias history, zero bridge mismatch. Any other model missing single_runs remains LOUD (no masking).

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

**Live runtime semantics:** All required replacement policy flags true -> rows may be materialized/read as `runtime_layer='live'`. Execution and monitoring must consume the live row set only. Historical row labels such as `LIVE_AUTHORITY`, `trade_authority_status`, `SHADOW_ONLY`, `SHADOW_VETO_ONLY`, and `DIAGNOSTIC_ONLY` are not live authority and must not be joined into the execution or monitor decision path.

**q_lcb requirement:** The live replacement path requires fused-q certified bootstrap `q_lcb_json` / `q_ucb_json`. On the source-clock route the bootstrap consumes `σ_center`, `σ_pred`, and `member_count` from the same current-evidence shape; the coherent carrier includes the finite-member tail limit in §1f. A missing current ENS carrier or bound blocks live materialization/readiness; it must not fall back through historical residual calibration, baseline, or retired experiment provenance.

**FSR dependency (commit 8c6e028066):** The replacement forecast is an OVERLAY authority — it writes posteriors and readiness that depend on `baseline_b0 (ecmwf_open_data)` source_run. It emits no source_run or ensemble_snapshots of its own. The opendata baseline producer (mx2t6_high / mn2t6_low) MUST remain enabled; disabling it starves FSR.

**Statistical vs absolute honesty:**
- Forecast superiority (bin-hit, MAE, bias) is settlement-graded, temporal holdout (TRAIN≤2026-05-10 / TEST 05-11..06-08), no look-ahead.
- BACKTEST RESULT (wszeibgi0): de-bias is the dominant lever. Non-regional 39 cities: bin-hit +4.6 pt, MAE 1.435→1.305, bias −0.515→+0.071. Regional 12 cities: bin-hit +11.6 pt, MAE 1.101→0.850. Optimal = per-model de-bias then equal-weight.
- Trading EV is proven ONLY by forward real fills. The strategy-selection settlement-2026-06-09 analysis showed in-sample EV was inflated; forward temporal holdout collapses to +1.2¢..−2.7¢ with large day-variance. **No promotion to live capital before forward fills license it.**

**Iron rules:** (1) coverage != currency — five instances fixed today, zero tolerance for recurrence. (2) source-identity — a model's live product must match its de-bias product. (3) no in-sample promotion. (4) buy_no derives from forecast YES bin — cold-bias corrupts the family. (5) operator promotion requires settlement-graded evidence at the same evidence class as icon_eu.

---

## 5. Test / Antibody Index

| Test file | Category killed |
|---|---|
| `tests/test_bayes_precision_fusion_persisted_read_lead_robust.py` | Lead-calendar mismatch makes fusion fire on 0 cells (140d75ff6d) |
| `tests/test_bayes_precision_fusion_thin_anchor_retained.py` | Anchor center dropped from EQUAL_WEIGHT cells (49492f1528) |
| `tests/test_bayes_precision_fusion_gem_current_value_previous_runs_fallback.py` | gem single_runs dead leg silently shrinks ensemble 4→3 (edc598b440) |
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

27/27 materializer+fusion+wiring green at ship. 61/61 green post-promotion (a70436d478).
