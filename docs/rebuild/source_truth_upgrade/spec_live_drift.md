# Source-Truth + Day0 Upgrade — spec ↔ live drift ledger

```
# Created: 2026-06-17
# Authority: docs/rebuild/source_truth_upgrade/{e2e_upgrade_plan.md, source_roles_v1.json, city_routing_v1.csv}
#   (operator package zeus_polyweather_upgrade_package_v1). Reconciled against live tree
#   /Users/leofitz/zeus @ live/iteration-2026-06-13 (committed HEAD c62d53b190 + operator WIP).
# Method: read-only. Every spec claim verified against live source/DB before any build.
```

## Confirmed ground-state (verified this session)

| # | spec claim | live reality | resolution |
|---|---|---|---|
| 1 | "Forecast layer already produces `X ~ N(mu*, sigma_pred²)` in Celsius" persisted | `forecast_posteriors` persists **binned** `q_json` / `q_lcb_json` / `q_ucb_json` — NOT a stored continuous (mu*, σ). Table is ALIVE: 7613 rows, latest 2026-06-17T10:29 (materializer actively writing). | **Day0 censoring operates on the persisted q-bins** (zero impossible bins → renormalize), which is exactly the spec's final math step. The continuous N(mu,σ) is the conceptual model only; impl reads `q_json`. Continuous μ/σ, if needed, comes from the materializer in-memory before binning, NOT from a posterior column. |
| 2 | New tables: `city_source_overlay`, `observation_facts`, `source_latency_facts`, `settlement_preimage_facts` | none exist in any split DB | Create in **`state/zeus-forecasts.db`** (owns settlement_outcomes, raw_model_forecasts, ensemble_snapshots, source_run, market_events — the forecast/source-truth lane; 118 tables). Writes via the daemon under **INV-37 (ATTACH + SAVEPOINT, forecasts writer-lock slot)** — never an independent connection. |
| 3 | 8 new module paths (`src/source_truth/*`, `src/data/day0_observation_fetcher.py`, `src/data/observation_fact_repo.py`, `src/forecast/day0_extreme_likelihood.py`, `src/forecast/observation_precision_fusion.py`, `src/engine/day0_revaluation_scheduler.py`) | all 8 **absent** → no path collision. `src/source_truth/` dir does not exist; `src/data/` exists. | Create as specified. BUT siblings exist that must be respected (next rows). |
| 4 | `src/forecast/observation_precision_fusion.py` (new) | `src/forecast/bayes_precision_fusion.py` exists = the T2 forecast precision fusion (live). | NEW module is a **distinct day0-observation** precision layer (σ_eff from sensor+rounding+lag+mismatch+transform). Must NOT duplicate/fork bayes_precision_fusion; it fuses OBS likelihoods, not forecast models. |
| 5 | day0 observation layer (new) | `src/forecast/day0_conditioner.py` exists (live day0 path). `q_engine` mx2t3 coupling: day0 q currently seeds members off `ensemble_snapshots` (the cold-member coupling being decoupled by agent a9878e06a2a4ae67d, in flight). | Day0 layer must **compose with / supersede** day0_conditioner — reconcile against it (see obs-infra locate report). The mx2t3 decouple (clean forecast prior) is a **prerequisite** that lands first. |
| 6 | DB-home: spec is silent on which split DB | K1 law: zeus-world / zeus-forecasts / zeus_trades | resolved → zeus-forecasts.db (row 2). |

## Routing-table facts (city_routing_v1.csv, 54 cities)
- 50 `wu_icao` (settlement_airport_exact) · 1 `hko` (hong-kong, official_city_center_exact) · 3 `noaa` timeseries (istanbul=LTFM, moscow=UUWW, tel-aviv=LLBG).
- Restricted/excluded-from-live families (source_roles_v1.json): amsc_awos, aeroweb, ncm_current, ncm_forecast, ims_observation_api → math_weight 0 in live; shadow residual only.
- Day0 primary obs: AviationWeather METAR (all), MADIS HF-METAR (US: atlanta/austin/chicago/dallas/denver/houston/la/miami/nyc/sf/seattle), HKO realtime (hong-kong).

## Resolved — existing live infra (obs_infra_locate.md) → targeted-extend scope (operator chose extend)
Zeus ALREADY has a sophisticated LIVE day0 lane. The spec's 8 "new modules" mostly re-specify it.
Build ONLY the genuinely-missing authority; wire the rest as substrate.

| spec module | live equivalent (KEEP, wire as substrate) | action |
|---|---|---|
| day0_observation_fetcher | day0_fast_obs.py (METAR + high/low-so-far + plausibility quarantine + split memo) + daily_obs_append.py (WU/HKO) + observation_client.py | KEEP. (gap: WU-path plausibility/faithfulness — EXTEND) |
| observation_fact_repo | `observations` table + ObservationAtom (sole validated write path) + observation_instants | KEEP. |
| day0_extreme_likelihood | day0_conditioner.py: `probability_high/low_day0_bin` == spec censoring; `condition_day0` == Y=max(H_t,X) | KEEP / extend to consume obs-precision fusion. |
| market_rule_reconciler | settlement_semantics.for_city() (wu_icao/hko/cwa/noaa; HKO oracle_truncate) | KEEP. (persist preimage facts — optional) |
| proof-of-possession | observation_available_at = publication clock (receiptTime), MANDATORY for live | KEEP. |
| **source_truth_loader / overlay_manifest** | scattered config only (cities.json + wu_obs_latency.json + wu_metar_divergence.json + ad-hoc fast_obs_source_for_city) — **NO first-class loader** | **BUILD (new authority).** |
| **observation_precision_fusion** | single-source faithfulness gating only — **NO multi-source σ_eff fusion** | **BUILD (new authority).** |

## Build progress
- **Module 1 — SourceTruthOverlay: DONE + tested 5/5** (`src/source_truth/overlay_manifest.py` + `tests/test_polyweather_source_truth_overlay.py`, percity-source-data worktree). Loads 54/54, reconciles vs cities.json (0 shadow), settlement truth from cities.json (NOT the table), restricted families excluded from live primary. **Design: spec's `source_truth_loader.py` MERGED into `SourceTruthOverlay`** (for_city/live_cities/role/is_restricted) — separate file = redundant indirection (collapse-don't-add). NEEDS fresh verify+critic (writer≠reviewer).
- Next: (2) observation_precision_fusion (the genuinely-new multi-source σ_eff obs fusion), (3) WU-path plausibility/faithfulness gap, (4) city_source_overlay materializer (RO default, --commit gated), (opt) persisted preimage facts.

## Prerequisite status
- **mx2t3 decouple (agent a9878e06a2a4ae67d): VERIFIED GREEN** — 3 couplings patched (day0 seed, σ-fallback→raw_model_forecasts lead-1 member-mean, carrier cert/pin); imports clean; carrier test 5/5; hard-gate 378/378 (money_path+live_inference+architecture). Applies clean on the operator WIP (touches none of the WIP-edited functions). LAND on live tree when agent reports its full diff.

## Hard laws carried from the package (enforce in every module)
1. Active Polymarket resolution rules = single final truth; table is shadow-only on conflict.
2. Exact settlement station/source = truth target, NOT a weighted opinion.
3. No network fetch in the q path — fetchers persist artifacts + proof-of-possession; q reads persisted facts only.
4. Weights LEARNED from walk-forward residual covariance + latency variance — no operator-picked weights.
5. Day0 obs = likelihood + censoring on the daily high/low; never replaces the forecast posterior before the day completes.
6. Rollout: Phase 0 shadow → Phase 1 replay last 90 settled (lower Brier/logloss, no semantic violation) → Phase 2 canary Kelly≤0.05 exact-public cities → Phase 3 live exact airport/HKO/NOAA classes → Phase 4 monthly drift audit.
