# Midstream Center-Authority Map — Live Forecast Lane
# Created: 2026-06-17
# Last audited: 2026-06-17
# Authority basis: read-only audit of live code + config/settings.json flags

---

## 1. `select_models` per-city behavior

**File:** `src/forecast/model_selection.py`

`select_models` (line 309) is a **pure per-city polygon + provider-family filter**. It never touches a network, a database, or a cell-distance lookup. Selection is driven exclusively by:

- **Polygon gate** (`regional_eligible`, line 219): ray-casting point-in-polygon for the city lat/lon against per-model domain polygons loaded from `config/model_domain_polygons.yaml`.
- **Lead gate** (`max_lead_days` per polygon).
- **Provider-family single-rep contest** (lines 396-405): ICON, NCEP, UKMO, GEM families each contribute at most one member (most-specific-first).
- **Alias dedup** (lines 330-341): `icon_seamless` dropped when corr > 0.995 with `icon_d2`.

**REGIONAL_MODELS** (line 87):
```
"icon_d2", "meteofrance_arome_france_hd", "ukmo_uk_deterministic_2km",
"gfs_hrrr", "gem_hrdps_continental"
```

**Per-city behavior:**

| Region / cities | Regional expert | Globals suppressed |
|---|---|---|
| Central-EU (Paris, etc.) at lead ≤ 1 | `icon_d2` (2km) | `icon_global`, `icon_eu` as ICON-family dups |
| EU-edge (Madrid, Istanbul, Tel Aviv, Warsaw, Helsinki) at lead ≤ 3 | `icon_eu` (7km own polygon) | `icon_global` as ICON-family dup |
| France polygon at lead ≤ 1 | `meteofrance_arome_france_hd` | none (DWD/ICON still carried) |
| UK / London polygon | `ukmo_uk_deterministic_2km` | `ukmo_global_deterministic_10km` as UKMO-family dup |
| CONUS cities at lead ≤ 1 | `gfs_hrrr` (3km) | `ncep_nbm_conus` + `gfs_global` as NCEP-family dups |
| North-America (outside CONUS/HRRR) | `gem_hrdps_continental` (2.5km) | `gem_global` as GEM-family dup |
| All other cities (SE-Asia, S-Asia, MENA, etc.: Lucknow, Kolkata, Mumbai, Delhi, Bangkok, Dubai, etc.) | **No regional expert** — all five `REGIONAL_MODELS` are out-of-polygon → `excluded_regionals` | `gfs_global` + `icon_global` + `gem_global` + `jma_seamless` + `ukmo_global` all ride (5 decorrelated globals; none suppressed unless available) |

Selection is **by polygon, not by cell-distance-to-airport**. The per-city overlay `docs/polyweather_city_source_overlay_verified.csv` is **not yet referenced** anywhere in code (confirmed by no import/reference found in the codebase). The AIFS 0.25° coarse grid therefore remains **un-gated** at the `select_models` level — it enters as the anchor prior on every city.

---

## 2. Materializer center — AIFS prior owns μ* when fusion flag is OFF or capture is absent

**File:** `src/data/replacement_forecast_materializer.py`

The materializer builds the posterior in `_insert_posterior` (line 1518). The center path is:

```
anchor_value_corrected_c                    (line 1560)
  = raw OM9 9km anchor  ±  bias_shift_c (per-city debias artifact, currently absent → 0)

bayes_precision_fusion_override             (line 1561)
  = _replacement_bayes_precision_fusion_override(...)
    → flag: settings["edli"]["replacement_0_1_bayes_precision_fusion_enabled"]
    → LIVE VALUE: true  (settings.json line 121)

result = build_openmeteo_ifs9_aifs_soft_anchor_result(   (line 1564)
    ...
    anchor_value_override_c = bayes_precision_fusion_override.anchor_value_c  (line 1573)
    anchor_sigma_override_c = bayes_precision_fusion_override.anchor_sigma_c  (line 1574)
)
```

**What `build_openmeteo_ifs9_aifs_soft_anchor_result` does** (`src/strategy/ecmwf_aifs_sampled_2t_probabilities.py:341`):
- Its CENTER is the `anchor_value_c` parameter — by default the raw OM9 IFS-9km value.
- When `anchor_value_override_c` is supplied (i.e. `bayes_precision_fusion_override is not None`), the **BPF fused center** replaces the OM9 anchor value. When NOT supplied, the **raw OM9 9km anchor (coarse-snapped)** owns μ*.
- The AIFS 51-member sampled ensemble is used **only as the vote-shape prior**; it is not used for the center when the override path fires.

**AIFS-as-center path** (`REPLACEMENT_Q_MODE_BAYES_PRECISION_FUSION_CAPTURE_MISSING`):
- `bayes_precision_fusion_override` returns `None` when: (a) the flag is OFF, (b) persisted current single_runs capture is absent for the cell at materialization time (line 1011–1020), or (c) all extras drop to zero (line 1063–1079).
- In those cases, `anchor_value_override_c=None` → `build_openmeteo_ifs9_aifs_soft_anchor_result` uses the raw OM9-IFS9km anchor as center, which is **the 0.1° OpenMeteo point** — distinct from the coarse AIFS 0.25° sampled prior.
- However, the **q SHAPE** is still from the AIFS member votes (soft-anchor q `aifs_member_votes_soft_anchor`). The q SHAPE is AIFS-driven unless `replacement_0_1_fused_q_shape_enabled=true` AND `bayes_precision_fusion_override` is available.

**LIVE FLAGS (config/settings.json):**

| Flag | Location | Value |
|---|---|---|
| `replacement_0_1_bayes_precision_fusion_enabled` | `edli` (line 121) | **true** |
| `replacement_0_1_member_vote_smoothing_enabled` | `edli` (line 122) | **true** |
| `replacement_0_1_fused_q_shape_enabled` | `edli` (line 129) | **true** |
| `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled` | `feature_flags` (line 287) | **true** |
| `qkernel_spine_enabled` | `feature_flags` (line 270) | **true** |

**Conclusion on AIFS center ownership in the materializer:**
When `replacement_0_1_bayes_precision_fusion_enabled=true` AND the persisted current capture is present AND has_extras, `bayes_precision_fusion_override` provides the BPF-fused center. The AIFS-sampled-2t 0.25° prior becomes the **vote shape**, not the center. The OM9 IFS-9km 0.1° anchor is the BPF *prior* center (line 1559), fed into `fuse_bayes_precision_posterior` as `anchor_z`. For cities where the current single_runs capture is MISSING at materialization time (returns None), the center falls back to the **raw OM9-IFS9km anchor** (not raw AIFS center) with q-shape from AIFS votes — this path produces `REPLACEMENT_Q_MODE_BAYES_PRECISION_FUSION_CAPTURE_MISSING` and is NOT live-eligible.

---

## 3. Spine: blind-fuse-vs-selected verdict — BLIND FUSE (no `select_models` applied)

**File:** `src/engine/event_reactor_adapter.py`, function `_spine_multimodel_members_for_event` (line 11304)

**Verdict: BLIND FUSE. `select_models` is NOT called.**

The spine member fetch is a raw SQL query against `raw_model_forecasts` (lines 11374–11402):

```sql
SELECT model, source_cycle_time, forecast_value_c
FROM raw_model_forecasts
WHERE city = ? AND metric = ? AND target_date = ? AND date(source_cycle_time) = ?
  AND source_available_at <= ?
ORDER BY model, source_cycle_time
```

The result is reduced to `best: dict[str, float]` (latest cycle per model) **without any polygon gate, provider-family filter, alias dedup, or call to `select_models`**. Every model present in `raw_model_forecasts` for the city on the causal cycle date enters the fused envelope (lines 11395–11402). Only the `< 3 members` guard is applied as a fail-closed floor (line 11403).

**Implication for cities like Lucknow:** `gfs_global` (0.25°, MAE 6.71 per-city), `icon_global` (0.25°), `jma_seamless` (0.25°, offshore-snap bias ~−2.10°C), and `gem_global` (~15km) all enter the spine member set alongside `ecmwf_ifs` (0.1°) if all are present in `raw_model_forecasts`. The coarse globally-biased models are **not suppressed** at the spine level as they would be in `select_models` (where the NCEP, ICON, GEM families would reduce to a single best rep, and out-of-polygon regionals would be excluded). No city-overlay or distance-to-airport weighting applies.

The Stage-0 producer stashes these raw members at lines 7777–7796:
```python
_spine_multimodel = _spine_multimodel_members_for_event(...)
if _spine_multimodel is not None:
    payload["_edli_spine_raw_members_native"] = _spine_lst
    payload["_edli_spine_debiased_members_native"] = _spine_lst  # raw == debiased (de-bias OFF)
    payload["_edli_spine_mu_native"] = float(_spine_mean)  # empirical mean of ALL models
    payload["_edli_spine_sigma_native"] = ...               # empirical std of ALL models
```

The empirical mean of a blind-fused set (including multiple ICON/NCEP/GEM-family members) is not a decorrelated BPF posterior μ*. It is a raw equal-weight average that double-counts correlated provider families and admits all coarse-grid outliers.

**The cold-center root cause is partially mitigated** (2026-06-16 fix replaced `ensemble_snapshots` 51-member ECMWF path with `raw_model_forecasts`), but the new path still admits every model stored in that table without `select_models` filtering. The qkernel docs themselves note this (bridge.py line 56–57): "They are RAW, NOT chain-of-record-debiased" and the settlement-residual debias flag `ZEUS_SPINE_SETTLE_RESID_DEBIAS` is **currently OFF** (default).

---

## 4. LIVE center authority on the forecast lane — which q reaches the receipt?

**Decision path for `FORECAST_SNAPSHOT_READY` / `EDLI_REDECISION_PENDING`:**

`_live_yes_probabilities` (line 9787) runs first:
```python
replacement = _replacement_authority_probability_and_fdr_proof(...)  # line 9815
if replacement is not None:
    return replacement   # line 9826 — REPLACEMENT LANE WINS
return _canonical_probability_and_fdr_proof(...)   # fallback only
```

`_replacement_authority_probability_and_fdr_proof` (line 10572):
1. Checks `_replacement_authority_enabled()` → reads `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled` → **true** (settings.json line 287). Flag is ON → proceeds.
2. Reads the replacement readiness and `read_replacement_forecast_bundle` — the materialized posterior from `forecast_posteriors`.
3. Gate: `_replacement_q_mode_live_eligibility` — only `FUSED_NORMAL_FULL` / `FUSED_NORMAL_PARTIAL` are live-eligible. `BAYES_PRECISION_FUSION_CAPTURE_MISSING`, `SOFT_ANCHOR_FALLBACK`, `FUSED_Q_BUILD_FAILED`, `FUSED_NORMAL_BOUNDS_MISSING` all raise `REPLACEMENT_Q_MODE_NOT_LIVE_ELIGIBLE` → no-submit receipt.
4. When eligible: `q_by_condition[condition_id] = q_yes` taken from `replacement_bundle.q` (the fused-Normal shape over bins), with LCB from the certified bootstrap (`q_lcb_basis == "fused_center_bootstrap_p05"`).

**The q on the live receipt comes from the materializer's fused-Normal posterior** (`forecast_posteriors.q_json`), built via `build_openmeteo_ifs9_aifs_soft_anchor_result` with `bayes_precision_fusion_override.anchor_value_c` as the center — i.e., the **BPF multi-model fused μ*** (when flag ON and capture present). This is NOT the AIFS 0.25° center; it is the Bayesian posterior of the OM9-IFS9km prior updated by the decorrelated model likelihood set selected by `select_models` via `capture_bayes_precision_instruments`.

**The `qkernel_spine_enabled=true` flag** means the spine bridge (`decide_family_via_spine`) now owns the **DECISION** (candidate selection, sizing, direction law, edge LCB gate). However, the spine bridge is inserted **after** the `_live_yes_probabilities` call and the existing `_CandidateProof` materialization: the spine's `decide()` receives the already-built candidate proofs (which carry the replacement-lane q/q_lcb from the materializer) and the RAW multi-model member envelope (from the blind `raw_model_forecasts` fuse). The spine builds its own N(μ*, σ*) from the raw member mean/std (NOT the replacement q) for the `FamilyDecisionEngine`. The **q that sizes the receipt** depends on which path the bridge surfaces: when the spine selects a proof, it overlays the spine's `edge_lcb` / `optimal_delta_u` onto the existing `_CandidateProof` (which carries the materializer's BPF q). The `q_yes` value in the receipt therefore comes from the materializer's BPF fused-Normal shape, while the candidate **selection** comes from the spine's `FamilyDecisionEngine.decide()` over the blind-fused raw member distribution.

---

## Summary table

| Layer | Center source | select_models applied? | AIFS coarse grid owns μ*? |
|---|---|---|---|
| **Materializer** (`replacement_forecast_materializer.py:1564`) | BPF multi-model fused μ* via `capture_bayes_precision_instruments` → `select_models` → `fuse_bayes_precision_posterior` | **YES** — polygons + provider-family dedup applied | No — AIFS is vote-shape only; OM9 IFS-9km is the prior; fused μ* is the center |
| **Spine member fetch** (`_spine_multimodel_members_for_event`, line 11304) | Raw empirical mean of ALL models in `raw_model_forecasts` for city/date/causal-cycle | **NO** — blind SQL, no `select_models`, no polygon gate | Coarse globals (gfs_global 0.25°, icon_global, gem_global, jma_seamless 0.25°) all enter; no provider-family dedup |
| **Live q on receipt** (FORECAST_SNAPSHOT_READY / EDLI_REDECISION_PENDING) | **Materializer's BPF fused-Normal q** (when mode=FUSED_NORMAL_FULL/PARTIAL + flag ON) | YES (via materializer) | No, when capture is present |
| **Spine decision** (qkernel_spine_enabled=true) | Spine's N(μ*_raw, σ*_raw) over the BLIND multi-model envelope | NO | Yes, partially — coarse global bias enters μ*_raw |

---

## Key gap: the per-city overlay CSV is not wired

`docs/polyweather_city_source_overlay_verified.csv` is referenced in no import in the live code path. The `select_models` polygon gate does resolve regionals for covered cities (EU, CONUS, N-America, UK), but for uncovered cities (Lucknow, Kolkata, Bangkok, Dubai, etc.) the global likelihood set is the five coarse-grid globals — all 0.25°/0.1° — with no nearest-airport fine-resolution selection. The overlay's per-city best-near-airport source is unused.

The spine blind-fuse additionally admits coarse globals for ALL cities including covered ones (e.g. Paris would receive `icon_global` as a spine member alongside `icon_d2`, where `select_models` would suppress it as an ICON-family dup). This is a secondary cold-bias risk on the spine's N(μ*) that the materializer's BPF path does not share.
