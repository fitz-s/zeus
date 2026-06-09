# U0R-Bayes — Universal 0.1° Regional Bayesian Settlement Fusion (the forecast core)

> Authority: operator directive 2026-06-08 (full plan pasted in session). This file is the executable condensation; the operator's paste is the governing spec. Supersedes the "Day0-only / fcast_simple_v1 = equal-weight" framing: the forecast core is **settlement-targeted hierarchical Bayesian fusion**, of which equal-weight top-K is the finite-sample special case.
> RULE: prove on VERIFIED settlement (§Proof) BEFORE any production wiring (§Prod). Measure-before-build. Regional/forecast authority is earned by proper-score + after-cost gates, never asserted.

## 0. Target (not MAE)
Per row i=(city,station,target_date,metric,lead,market_family) at decision_time t, output the settlement-bin posterior q_{i,t}(k)=P(Y_i∈bin_k | F_t) + conservative q_lcb. Y_i = VERIFIED WU local-day high/low (unit/station/cadence/rounding/bin semantics). Trade iff edge_k = q_lcb,k − ask_k − cost_k > δ. Forecast skill necessary, not sufficient.

## 1. Observation model (each source = noisy instrument of one latent truth)
z_{i,s} = x_{i,s} − b̂_{s,g(i)}  (bias-corrected);  z_i = Y_i·1 + ε_i,  ε_i ~ N(0, Σ_{g(i)}) [t_ν shadow].
g = city/station × region × metric × lead × season × product/resolution.
**0.1 anchor = prior:** Y_i | x_{0.1} ~ N(μ0_i, τ0²), μ0_i = x_{0.1} − b̂_{0.1}. Other globals/regionals = likelihood terms.

## 2. Theorems (the why)
- **T1 (proper score):** true conditional dist is the unique optimum (log/Brier/CRPS). Judge sources by proper scores + q_lcb coverage + after-cost EV, not MAE.
- **T2 (Bayesian fusion = optimal blend):** V* = (τ0⁻² + 1ᵀΣ⁻¹1)⁻¹; μ* = V*(τ0⁻²μ0 + 1ᵀΣ⁻¹z). Exact posterior — not arbitrary weights. Low-variance regional in-domain → high precision; absent source → simply absent from z; correlated/aliased → Σ⁻¹ down-weights; only-0.1 → posterior = anchor.
- **T3 (equal-weight = special case):** Σ=σ²I, unbiased, indep, no dominant prior ⇒ μ* = mean(z) = equal-weight. The empirical "equal-weight top-3 ≈ softmax, learned overfits" IS the finite-sample robust form. ⇒ production = **Bayesian fusion with strong shrink-to-equal prior**; covariance-aware only when Σ reliably estimated.
- **T4 (regional add helps iff conditional info):** improvement from X_R = I(Y;X_R | X_G). Promote ICON-D2(EU)/AROME(FR) only where out-of-sample ΔproperScore>0, domain-polygon + fixed-lead + block-bootstrap. (HRRR retracted: ΔS≤0 at fair lead.)
- **EB bias (T):** b̂_{s,g} = λ·r̄_{s,g} + (1−λ)·b_parent, λ=n/(n+κ). Thin→shrink to structural prior; large-n→trust local. Bias = grid→station + land/sea + urban/airport + model warm/cold + cadence — a resolution upgrade cannot remove it.

## 3. Source identities (physical)
- Universal anchor: ECMWF IFS 9km/0.1 (`ecmwf_ifs`, OM `previous-runs`) — global prior mean.
- Decorrelated globals (structural-error reducers): GFS, ICON-global, GEM, JMA.
- Regional experts (conditional, in-domain only): ICON-D2 2km EU (`icon_d2`, Central-Europe polygon, ≤48h); AROME / AROME-HD France (`meteofrance_arome_france_hd`, France polygon).
- AIFS: optional diversity/uncertainty feature ONLY (not anchor/support/regional). [E0/E1 ablation decides.]
- Causality: `previous-runs` = fixed-lead (bias/skill train only); `single-runs`/live capture for replay; **run_time ≠ source_available_at** (globals +4-6h).
- DEDUP: `icon_seamless ≡ icon_d2` in EU (bit-identical) → never both (corr>0.995 ∧ mean|Δ|<ε). Use icon_d2 in-EU, icon_global out.

## 4. Algorithm (U0R-Bayes, runtime)
(1) eligible(s): source_available_at≤t ∧ domain∋station ∧ lead≤horizon ∧ localday-operator valid ∧ unit known ∧ bias substrate/bridge exists ∧ not alias-dup. (2) provider representatives (ECMWF/NOAA/DWD[icon_d2 in-domain else icon_global]/CMC/JMA/Météo-France[arome in-domain]). (3) alias dedup. (4) EB bias-correct each (sparse → bias=0 + widen σ). (5) fusion: covariance-aware Bayesian if Σ reliable ELSE equal-weight K=3 corrected reps + source-disagreement σ. (6) σ² = wᵀΣw + σ²_bias + σ²_bridge + σ²_station + σ²_lowN + σ²_disagree. (7) Gaussian v1 (conservative σ inflation) / Student-t v2 shadow. (8) integrate over canonical market bins → q_f, q_lcb_f, q_ucb_f.
Resolution bridge: Δb_{R→R'}=E[x_{R'}−x_R], ρσ=σ_{R'}/σ_R; ported σ += σ²_bridge_uncertainty; ρσ<1 NOT guaranteed (coast/terrain) → widen-only default.

## 5. PROOF PACKAGE (do FIRST — §12 of the plan)
Data: B0_multilead_dataset.json (50 cities, 2025-12→2026-06, leads 1/2/3/5/7, 8 models, WU-joined) AUGMENTED with ICON-D2 EU + AROME FR (OM previous-runs fixed-lead, ~6mo retention). Walk-forward (train bias/var/selection on dates<d only; no same-day leak).
Ablations (per metric×lead×region): A0 OM9-raw · A1 OM9-EB · B0 globals-equal-raw · B1 globals-equal-EB · C0 U0R-Bayes diagonal · C1 U0R covariance-shrink · D0 regional-off · D1 regional-on · E0 AIFS-off · E1 AIFS-feature · F0 Gaussian · F1 Student-t.
Metrics: Brier, log-loss, CRPS, MAE/RMSE, bias, top1/top3 bin-hit, q_lcb coverage, 80/90% interval coverage, PIT/rank-hist.
Paired: identical rows, block-bootstrap by target_date, cluster by city, per metric/lead/region. Domain tests: in-EU-polygon ICON-D2 gain>0; out-of-polygon ABSENT. Calibration: reliability deciles, q_lcb coverage, tail log-loss, floor/ceiling bins, LOW separate.
**5 proof targets:** (1) 0.1 anchor necessary (remove→degrade, esp. no-regional cities); (2) regional conditionally valuable (in-domain gain, no out-domain leak); (3) decorrelated globals reduce structural/tail error; (4) learned weights not worth it (softmax not >2pp majority); (5) Bayesian shrink ≥ equal-weight, never catastrophic.
Acceptance (forecast authority): U0R beats OM9-only AND globals-equal baseline on proper scores; regional improves in-domain; no out-domain leak; q_lcb coverage passes; LOW passes; no unresolved regional regression cluster. (trading authority: after-cost EV+ on same-CLOB replay.)

## 6. PRODUCTION (build AFTER proof passes; gated, flag-OFF default, one-builder)
F0 source registry + model_domain_polygons.yaml + source_release_calendar.yaml (add openmeteo_{ecmwf_ifs_9km,gfs_global,icon_global,icon_d2_eu,gem_global,jma_global,arome_fr,arome_fr_hd}, the_path_fcast_simple_v1). F1 raw capture (previous_runs + single_runs + raw_model_forecast_repo; raw_model_forecasts table). F2 localday operator (hourly_2t_localday_max/min). F3 bias_substrate + resolution_bridge. F4 model_alias + model_domain + model_selection. F5 u0r_bayes + simple_blend + posterior_to_bins. F6 EMOS key += product, resolution_mix_hash, model_set_hash, lead_bucket.
Integration into live `replacement_0_1` soft-anchor: U0R replaces the single-9km-anchor with the fused posterior; EU in-polygon uses icon_d2 expert; falls back to universal 0.1 elsewhere. Flag-gated, shadow→veto→size-down→promote on settlement evidence (the existing evidence gate + q_lcb floor compose).

## 7. Hidden branches (forecast layer) — antibodies
top-K-uses-target-truth (walk-forward only) · regional-outside-domain (polygon) · icon_seamless≡icon_d2 (alias dedup) · OM hourly-interp-as-native · best_match hidden mixture · elevation/cell_selection default drift (product identity) · LOW-uses-high-max · C/F unit mix (settlement-unit residual) · regional lead>horizon · covariance from too-little-data (shrink-to-equal) · source-disagreement-ignored (σ widen) · q_lcb undercover (haircut) · seasonal bias pooled · station-city mismatch · land/sea cell flip · model-release-after-decision (source_available_at) · previous-runs-for-live-decision (single-run for replay) · no-regional-history (shadow only).
