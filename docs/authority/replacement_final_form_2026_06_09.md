# Replacement Forecast Final Form (2026-06-09, operator-ratified)

**Status:** LIVE_AUTHORITY (flag-only, operator directive 2026-06-08).  
**Supersedes:** `U0R_BAYES_SPEC.md` (deleted).  
**Created:** 2026-06-09  
**Last audited:** 2026-06-09  
**Authority basis:** Commits 140d75ff6d · 6860f00a21 · edc598b440 · 94b584cc3f · 49492f1528 · 2b6936d3b5 · 9c594c9fc3 · df8199ef8e · e80c101c4c · 8541bc93cd · 8f20d39863 · a70436d478 + four experiments `docs/evidence/2026_06_09_final_form/aifs_replacement_experiment.md`, `docs/evidence/2026_06_09_final_form/inverted_blend_experiment.md`, `docs/evidence/2026_06_09_final_form/uncovered_cities_regional_report.md`, `docs/evidence/2026_06_09_final_form/universality_sweep.md`.

---

## 1. The Probability Chain

### 1a. Walk-forward de-bias

For each instrument `s` at `(city, metric, target_date)`:

```
residuals_s  = {x_s(d) − Y(d) | d in previous_runs, d < target_date, lead-bucket preferred=1}
r̄_s          = mean(residuals_s),   n_s = len(residuals_s)
λ_s          = n_s / (n_s + κ),     κ = KAPPA = 8.0          # src/forecast/u0r_bayes.py:51
b̂_s          = λ_s · r̄_s + (1−λ_s) · parent                # parent prior = 0.0
z_s          = x_s − b̂_s                                    # de-biased instrument value
```

`n_s < MIN_TRAIN=25` → LOWN_INFLATE=1.5 applied to σ_s (thin instrument).  
Walk-forward is strictly `target_date < decision_date`; settlement residuals only (`endpoint='previous_runs'`). `src/forecast/u0r_bayes.py:67–78`.

### 1b. T2 Bayesian fusion

Anchor: `ecmwf_ifs` as prior N(μ₀, τ₀²), τ₀ = max(anchor_walk_forward_std, TAU0_FLOOR=0.8).  
Likelihood instruments: K de-biased values z = [z₁ … z_K] (globals + in-domain regionals).

Covariance Σ: Ledoit-Wolf shrinkage of the sample covariance toward its diagonal, computed on the **common-target-date residual matrix** (rows = dates present for ALL instruments simultaneously). Requires ≥ COMMON_DATES_MIN=5 common dates; else diagonal C0 = diag(σ²_s). `src/forecast/u0r_bayes.py:82–114`.

```
V* = ( τ₀⁻² + 1ᵀ Σ⁻¹ 1 )⁻¹
μ* = V* · ( τ₀⁻² μ₀  +  1ᵀ Σ⁻¹ z )
```

Hyperparameters fixed a-priori (`src/forecast/u0r_bayes.py:51–57`):

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

When anchor history `n < MIN_TRAIN`, the anchor has no trusted τ₀. Prior to this fix the anchor center was silently dropped. **Fix (both sides of the boundary):** a finite `anchor_z` without trusted τ₀ joins the fusion as ONE equal-weight member at variance `(TAU0_FLOOR · LOWN_INFLATE)² = (0.8·1.5)² = 1.44 °C²` — demoted from T2 prior, never deleted. Zero-history anchor is the only valid reason for a null anchor center. `src/forecast/u0r_bayes.py` + `src/data/u0r_multimodel_capture.py`.

### 1d. Predictive spread

```
σ_resid  = stdev({mean_z(d) − Y(d) | d = common-date fused-center residuals, ≥5 dates})
           else 1.5 °C (conservative default)                # materializer:937,948–950
σ_pred   = max(1.0,  sqrt(fused.sd² + σ_resid²))           # materializer:951
```

Floor 1.0 °C honors the AIFS-experiment lead-0 overconfidence caveat: σ_pred=1.18 °C mean on lead-0 same-day analyses; forward lead-1 prospective spread will be higher.

### 1e. q construction — fused-N-direct (commit 8541bc93cd)

Flag `replacement_0_1_fused_q_shape_enabled = true`. Fail-closed to soft-anchor q on key-set mismatch or any construction error.

```
q[bin] = bin_probability_settlement(mu=μ*, sigma=σ_pred, bin_bounds, half_step=settlement_step_c/2)
         renormalized to sum=1
q_shape provenance = "fused_normal_direct"
```

`src/calibration/emos.bin_probability_settlement` (lines 427–485) is the single settlement integrator — preimage math, Celsius bounds, half_step=0.5 for precision=1. `src/data/replacement_forecast_materializer.py:1040–1062`.

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
- arome: 6 France/Po-valley cities
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
3. `replacement_forecast_shadow_materialization_queue.py:345` — seed checks posterior AND `expires_at > now` (pre-existing, confirmed clean)
4. `_download_u0r_extra_raw_inputs_if_needed` — coverage filter removed; replaced by row-level per-(model, city, target, metric, cycle, endpoint) skip (df8199ef8e)
5. Download gate for replacement anchor — same root (9c594c9fc3, instance 3 in commit)

**L2 — Row-level only-missing fetches.** Extras download preloads logical keys already persisted for the cycle and skips per-row. Steady-state cost = only-missing; self-healing on re-run regardless of coverage state. Commit df8199ef8e.

**L3 — Anti-silent-sink.** Each materialization runs as a subprocess with `capture_output=True`; warnings reach only the per-request sidecar JSON, never the daemon log. Fix (2b6936d3b5): queue processor re-emits WARNING/ERROR lines at queue level. Two more sinks fixed in universality sweep (8f20d39863): etl_diurnal/etl_temp_persistence stdout WARNINGs, arm_gate_emit producer success-path output.

**L4 — Domain gate == data presence == settlement skill.** Milan/arome: data present and settlement-graded before polygon boundary overrides inclusion. The polygon is the OUTER bound; data-presence-with-skill is the inner bound. Source-identity law: a model's live-inference product must match the product its de-bias history was fit on. Violated cases: EB-bias wrong-set (ENS bias over-corrects 9km IFS anchor → disabled, ff7f33dd5b), gem_seamless rejection (serves HRDPS/RDPS for NA cities, wrong physical product).

**L5 — No in-fusion provider double-count.** PROVIDER_FAMILIES covers all active same-provider pairs: ICON family (d2↔eu↔global), NCEP family (nbm↔gfs), UKMO family (uk2km↔global). ECMWF (ifs + AIFS) is not a U0R concern — AIFS never enters U0R Bayes. Universality sweep P6: CLEAN (8f20d39863).

**L6 — Explicit model-id registration.** Promoted models (`ncep_nbm_conus`, `ukmo_global_deterministic_10km`, `ukmo_uk_deterministic_2km`) have explicit entries in `OPENMETEO_MODEL_IDS` (`src/data/u0r_multimodel_capture.py`) — no identity-fallback implicit mapping. Commit 8f20d39863.

---

## 4. Authority Status and Risk Posture

**LIVE_AUTHORITY flag ladder:** All policy flags true → `LIVE_AUTHORITY` branch in `event_reactor_adapter.py:1745`. Row `trade_authority_status='SHADOW_ONLY'` is a **data-class label** that `bundle_reader.py:76` REQUIRES — flipping it breaks the chain. Authority lives at the policy layer, not the row label.

**q_lcb fallback:** Bundle `q_lcb=None` falls back to baseline q_lcb via Wilson lower-bound over AIFS votes (`event_reactor_adapter.py:6685–6714`) — conservative, not a blocker. The replacement q_lcb is only populated when the fused-q shape is constructed successfully.

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
| `tests/test_u0r_fusion_persisted_read_lead_robust.py` | Lead-calendar mismatch makes fusion fire on 0 cells (140d75ff6d) |
| `tests/test_u0r_fusion_thin_anchor_retained.py` | Anchor center dropped from EQUAL_WEIGHT cells (49492f1528) |
| `tests/test_u0r_gem_current_value_previous_runs_fallback.py` | gem single_runs dead leg silently shrinks ensemble 4→3 (edc598b440) |
| `tests/test_replacement_download_cycle_currency_gate.py` | Coverage-vs-currency conflation freezes anchor indefinitely (94b584cc3f) |
| `tests/test_stale_cycle_download_includes_covered_targets.py` | Covered-target filter starves new-cycle raw inputs (9c594c9fc3) |
| `tests/test_download_row_level_skip_only_missing_fetches.py` | Coverage filter on extras download; instance 5 coverage!=currency (df8199ef8e) |
| `tests/test_queue_surfaces_subprocess_warnings.py` | Subprocess WARNINGs silently discarded in sidecar void (2b6936d3b5) |
| `tests/test_replacement_fused_q_shape.py` | AIFS-vote shape zero-coverage bins; flag-OFF byte-path; fail-closed (8541bc93cd) |
| `tests/test_icon_eu_is_the_dwd_rep_inside_its_own_icon_eu_domain.py` | icon_eu borrowing icon_d2 box drops 7 EU-edge cities (6860f00a21) |
| `tests/test_replacement_0_1_anchor_eb_bias_source_match.py` | ENS bias applied to IFS anchor (wrong-set over-correction) (ff7f33dd5b) |
| `tests/test_forecast_live_opendata_producer_required_for_fsr.py` | FSR starvation when opendata baseline producer disabled (8c6e028066) |
| `tests/test_replacement_live_authority_evidence_gate_wiring_honesty.py` | Dead-but-advertised evidence gate misleads operator (54a53334a9) |
| `tests/test_u0r_candidate_accrual_models.py` | Family coexistence impossibilities; lead fallback; single-fetch-per-target (a70436d478) |
| `tests/test_u0r_bayes_port_fidelity.py` | T2 math port reproduces proof engine (Paris/high/L1 2025-12-26 → μ*=4.3137, sd=0.7259) |

27/27 materializer+fusion+wiring green at ship. 61/61 green post-promotion (a70436d478).
