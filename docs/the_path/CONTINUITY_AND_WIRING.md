# CONTINUITY_AND_WIRING — U0R-Bayes live data continuity, built-but-off ledger, contradictions, wiring spec

```
Created: 2026-06-08
Last reused or audited: 2026-06-08
Authority basis: READ-ONLY audit (mode=ro state/*.db) + LIVE /Users/leofitz/zeus (fix/opportunity-book-selector @ e5e5f022ee)
                 + NEW-SYSTEM /Users/leofitz/zeus-thepath-audit (thepath/audit-realign @ ebcefeb6ce, 13 commits off live base)
                 IRON RULE #3 (per-day row counts over 2026-05-23..2026-06-08, today=2026-06-08) + IRON RULE #4 (one-builder)
                 Independently re-probed; lens findings verified against DB and both code trees.
```

## Honest verdict (lead)

The live ingest is still a **single-anchor ECMWF-family pipeline.** Of the ~11 sources U0R-Bayes
requires, only **5 are continuously downloaded daily**, and **2 of those 5 are 2 days old**. The
**four decorrelated globals that are the entire statistical point of U0R-Bayes**
(`gfs_global`, `icon_global`, `gem_global`, `jma_seamless`) plus the regionals (`icon_d2`, AROME-FR)
and `icon_eu` have **ZERO rows ever** in every forecast table. The code that fetches them exists only
on the unmerged `thepath/audit-realign` branch and is **wired to NO scheduler** — even there it is
invoked synchronously inside the materializer, never as a forward-persisting download job. Net:
**U0R-Bayes cannot produce a fused posterior in live today; it would silently degrade to the single
anchor (override=None) or, post-merge-with-no-ingest, to EQUAL_WEIGHT — never the proven T2_BAYES core.**

---

## (1) DAILY-CONTINUITY verdict table

Direct answer to "is everything we continuously need downloading daily, not today-only?":
**NO.** Five of the U0R-needed sources are continuous; six are absent; the U0R anchor + AIFS are 2-day-old shadows.

| Source (U0R role) | Verdict | Daily-cadence numbers (verified, mode=ro) | Action |
|---|---|---|---|
| **ECMWF ENS opendata** (live single-anchor ensemble, 51-member) | **CONTINUOUS** | `ensemble_snapshots` source_id=`ecmwf_open_data`: rows EVERY day 05-23..06-07 (e.g. 05-28=1480, 06-04=1476, 06-06=2228, 06-07=724); 54 cities/day; target_dates forward to lead+6d (06-07 fetch → targets 06-07..06-13). 06-08=0 only because cron fires 07:30 UTC (pre-now). | None — the one healthy ENS source. |
| **AIFS sampled-2t** (U0R AIFS likelihood input) | **SPARSE — 2 days old** | `raw_forecast_artifacts` source_id=`ecmwf_aifs_ens` product `ecmwf_aifs_ens_sampled_2t_6h_v1`: **06-07=10, 06-08=4 artifacts ONLY**. Full history = these 2 days. **0 rows in `ensemble_snapshots` for AIFS** → GRIB downloaded but NOT extracted to usable members; consumed only as soft-anchor in `forecast_posteriors`. | Verify the 5-min shadow cycle keeps firing daily; 2 days ≠ walk-forward-usable. |
| **Open-Meteo ECMWF-IFS 9km anchor** (U0R 0.1/9km prior) | **SPARSE — 2 days old + SPEC MISMATCH** | `deterministic_forecast_anchors` source_id=`openmeteo_ecmwf_ifs_9km`: **06-07=171 (49 cities), 06-08=22 (21 cities)**. Full history = these 2 days. Spec calls for ECMWF-IFS **0.1°/9km ENS** anchor; live uses Open-Meteo **9km deterministic single value**. `ecmwf_ifs_ens_0p1` = 0 rows everywhere. | Confirm 9km-deterministic-vs-0.1°-ENS is intended substitution; 2-day history insufficient. |
| **gfs_global** (decorrelated global) | **ABSENT** | `source_id LIKE '%gfs%'` = **0** in `ensemble_snapshots` AND `raw_forecast_artifacts` AND `deterministic_forecast_anchors`. Defined only in `model_selection.py:41 DECORR_GLOBALS`; fetch in `u0r_multimodel_capture.py:55`; **no scheduler match in live main.py/ingest_main.py**. | Wire `capture_u0r_instruments` into a recurring forward-persisting ingest job. |
| **icon_global** (decorrelated global) | **ABSENT** | `LIKE '%icon%'` = **0** across all three forecast tables. ("icon_previous_runs" in `zeus-world.db forecasts` is the OLD Open-Meteo deterministic broker single-value, NOT native ICON ENS — do not mistake it for the U0R input.) | Schedule capture. Do not conflate broker rows with the decorrelated source. |
| **gem_global + jma_seamless** (decorrelated globals) | **ABSENT** | `LIKE '%gem%'` = 0; `LIKE '%jma%'` = 0 everywhere. No broker analogue either — 100% missing. | Schedule capture (`u0r_multimodel_capture.py:57-58`). |
| **icon_d2 (Central-EU) + AROME-FR + icon_eu** (regional experts) | **ABSENT** | `LIKE '%icon%'`/`'%arome%'` = 0. `REGIONAL_MODELS=('icon_d2','meteofrance_arome_france_hd')` polygon-gated (`model_selection.py:44`, `regional_eligible` :130); all unscheduled. Regional contribution entirely dark in live. | Schedule capture incl. `model_domain_polygons.yaml`; verify polygon gate fires only in-domain. |
| **Per-city observations** (WU + Open-Meteo archive + HKO + Ogimet/METAR + Meteostat) | **CONTINUOUS** | `observation_instants`: ~2358–2588 rows/day, **54 cities EVERY day** 05-25..06-07 (06-08=463 partial, in progress). Families: `wu_icao_history` (984k), `openmeteo_archive_hourly` (886k), `ogimet_metar_*`, `meteostat_bulk_*`. Crons `ingest_k2_hourly_instants`, `ingest_k2_obs_tick`, `ingest_k2_hko_tick`. | None — healthy across all cities. |
| **Intraday market ask/depth** (`executable_market_snapshots`) | **CONTINUOUS (and scaling)** | EVERY day 05-23..06-08; `orderbook_top_ask`+`orderbook_depth_json` on **100% of rows**. Ramping: 06-01=59k, 06-06=397k, 06-07=506k, 06-08=51k(partial). yes/no token, top bid/ask, depth_at_best_ask, tradeability. Fed by `edli_market_channel_ingestor` + `ingest_market_scan`. | None — healthiest live feed. |
| `market_price_history` (legacy price) | **STALE (superseded)** | 05-15=91k, 05-17=118k, 05-18=12k → COLLAPSE → 05-27=4, 05-28=10, **nothing after 05-28**. Superseded by `executable_market_snapshots`. IRON RULE #4: two market-price mechanisms, one dead. | Confirm retired; repoint any consumer to `executable_market_snapshots`. |
| `token_price_log` (per-token bid/ask) | **SPARSE/GAPPED** | Intermittent, partial city coverage (5–14 vs 54 cities). Not reliable for walk-forward intraday. | Prefer `executable_market_snapshots`. |
| **U0R posterior OUTPUT** (`forecast_posteriors` — what the system PRODUCES) | **SPARSE (2 days, ECMWF-family-only fusion)** | 06-07=171, 06-08=22; `posterior_method`=`openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` for **ALL 193 rows**; 100% have aifs+OM anchor, **ZERO u0r_fusion provenance**, `q_lcb_json` **100% NULL**, 100% `SHADOW_ONLY`. | Until decorrelated globals are scheduled + ingested, the "multi-model" posterior fuses two ECMWF-family inputs only — mathematically a single-family model dressed as multi-model. |

**Continuity gap (the data answer): the U0R-needed sources NOT continuously downloaded are
`gfs_global`, `icon_global`, `gem_global`, `jma_seamless` (decorrelated globals — ABSENT), and
`icon_d2`/`AROME-FR`/`icon_eu` (regionals — ABSENT).** AIFS + OM-IFS9 anchor are only 2 days old.
Only ECMWF ENS, obs, and market ask/depth are genuinely continuous-daily.

---

## (2) BUILT-BUT-OFF ledger

| Flag / capability | Built? | Activation gate | Safe-now vs needs-validation |
|---|---|---|---|
| `replacement_0_1_u0r_fusion_enabled` (the proven ~15% Brier core) | thepath ONLY (`u0r_bayes.py`, `u0r_multimodel_capture.py`, `model_selection.py`); **absent from live**, flag not in live config, read by ZERO live files | (1) merge branch (2) build decorrelated-global ingest (3) shadow ≥30 LIVE forward settled markets | **NOT safe-now** (absent from live + no fusion inputs + no settled cohort) |
| 7 disabled source specs (gfs/icon_global/gem/jma/icon_d2/arome + the_path_u0r_fusion derived) | thepath registry as `disabled` plumbing rows; **not in live registry, not ingested** | flag + a live ingest path that actually fetches them | **NOT safe-now** (capture fail-soft → with NONE present, fusion = byte-identical single-anchor no-op) |
| `replacement_0_1_eb_bias_correction_enabled` (per-city EB bias shift) | thepath ONLY; reuse target EXISTS in live (`model_bias_ens` edli_per_city_v1 VERIFIED=74: 67 HIGH/mx2t3 + 7 LOW/mn2t3) | merge; flip with fusion; fail-closed on no VERIFIED row | NOT safe-now (absent from live; ≥30 settled cohort) |
| `replacement_0_1_member_vote_smoothing_enabled` (α=0.05 Dirichlet, kills zero-prior −inf veto) | thepath ONLY (`MEMBER_VOTE_SMOOTHING_ALPHA=0.05`) | merge; promote WITH fusion (structural fix) | NOT safe-now (absent from live). **Note RED test, §3 Fault A** |
| `replacement_qlcb_settlement_sigma_floor_enabled` (q_lcb settlement-sigma floor) | thepath ONLY | merge; default-ON only after ≥30 settled per-band coverage — but the coverage log only accrues AFTER the evidence gate passes (circular) | NOT safe-now (moves live q_lcb DOWN; circular precondition) |
| soft_anchor authority (shadow/veto/trade_authority/kelly/flip) | **BUILT + WIRED in LIVE**, all 5 flags TRUE (config:294-298) but **INERT**: authority gate is readiness+evidence, not the flag; every posterior lands `SHADOW_ONLY` (193/193) | `promotion_evidence.json` must pass `replacement_live_authority_evidence_gate` (≥5 official days, ≥250 rows, q_lcb_coverage≥0.95, positive after-cost) | NOT safe-now (gate denies); see §3 Fault C (flag-resolved authority vs row-stamped SHADOW_ONLY disagree) |
| FIX-2b OperatorArm (capability token gating every EDLI submit) | BUILT + WIRED in thepath (`main.py:604`); **absent from live** | merge; keep `edli_live_operator_authorized=false` at deploy → no-submit adapter | **Safe to APPLY now** (tightening); do NOT authorize until canary evidence |
| Day0 nowcast lane | code present; lane DORMANT (`day0_horizon_platt_fits`=0, `day0_metric_fact`=0, `day0_nowcast_runs`=0); activator `persist_day0_horizon_identity_fit.py` thepath ONLY | merge, then run activator on LIVE (persists CONSERVATIVE identity fit, ZERO claimed skill, only to start obs clock) | **Safe to RUN now** (no fabricated skill); ROI needs weeks of settled Day0 markets |
| `full_transport_live_enabled` | BUILT+WIRED in LIVE, flag FALSE; **route effectively DEAD** (`full_transport_v1` family 0 rows → flag-ON still falls back to plain p_raw) | superseded by `exit_bias_family_unify_enabled` | NOT safe-now (0-row family = no-op/half-fix) |
| `exit_bias_family_unify_enabled` (D2 entry/exit unify) | BUILT+WIRED in LIVE, flag FALSE | flip to repoint evaluator-FT + monitor to `edli_per_city_v1` w/ bias-shift-only + identity-Platt | NOT safe-now (needs settlement); **the correct IRON-RULE-4 resolution of §3 Fault B** |
| `bias_treatment_v2_enabled` (typed BiasTreatment, corrected XOR haircut) | BUILT+WIRED in LIVE, flag FALSE; type shipped unconditionally | flip to retire the today-live double-penalty on ~20 high-|bias| buckets | NOT urgent; resolves a real double-counting fault |
| `q_lcb_settlement_coverage_gate_enabled` (K3) + `forecast_sharpness_gate_enabled` (K1) | BUILT+WIRED in LIVE, both FALSE | K1 starved (`forecast_skill`=0 → fails CLOSED); K3 needs coverage validation (`settlement_outcomes`=7038 present) | NOT safe-now |
| `tiny_live_notional_cap_enabled` + `tiny_live_daily_order_cap_enabled` | BUILT+WIRED in LIVE, both FALSE; thepath both TRUE (the only net live-money delta of redeploy — a TIGHTENING) | flip TRUE on redeploy | **Safe-now (tightening)** |
| CANONICAL_EXIT_PATH / HOLD_VALUE_EXIT_COSTS / NATIVE_MULTIBIN_* | BUILT+WIRED in LIVE, all FALSE | operator promotion / replay harness | Mostly NOT safe-now |

---

## (3) CONTRADICTIONS (IRON RULE #4) + resolutions (one-builder)

Of 8 suspected violations, 6 resolve to already-one-builder/already-fixed; **two are real standing faults**, plus two data-substrate faults.

- **RESOLVED — Conflict 1 (riskguard RED vs daemon GREEN):** K4 fix made `risk.level` orthogonal to `infrastructure_level` (`status_summary.py:414-417`, :1518-1551). Antibody = consumers must read BOTH fields; if any consumer collapses them, split-brain returns.
- **RESOLVED — Conflict 2 (EMOS 3-key vs 7-key):** two keys serve two DISJOINT products. Live trade seam `build_emos_q` (3-key); the 7-key (`replacement_forecast_emos_identity.py:118`) is the shadow-only U0R refit verifier. **Pre-promotion check:** ensure the U0R product routes serving through the 7-key cell, not `build_emos_q`'s 3-key, before U0R gets authority.
- **RESOLVED — Conflict 3 (legacy hook vs replacement_0_1 single-owner):** commit `aeff1cd24b` enforced single-owner; YES/NO authority made independent (live HEAD `16c35e7445`).
- **RESOLVED — Conflict 4 (fusion / smoothing / EB layering):** compose cleanly in `_insert_posterior` (audit `materializer:647-682`): authority → EB center → smoothing veto-lift → U0R center/spread → q_lcb sigma floor → EMOS; all flag-gated default-OFF, all fail-soft → byte-identical. Settings note: "NO PARALLEL FUSION (ONE-BUILDER, iron rule #4)".
- **REAL FAULT A — Conflict 5 (Dirichlet floor test RED on BOTH branches):** `tests/test_aifs_prior_uses_dirichlet_floor.py` asserts an UNCONDITIONAL zero-prior floor (`assert 0.0 > 0.0` fails) but the shipped mechanism is a flag-gated **default-OFF** smoothing (`MEMBER_VOTE_SMOOTHING_ALPHA=0.05`) the test never exercises (calls `build_soft_anchor_posterior` with no alpha). Root: `openmeteo_ecmwf_ifs9_aifs_soft_anchor.py:197-198` sets `log_term=-inf` for prior≤0. Introduced `2d05e81f69`, never passed. **Two designs for one invariant.** **Resolution (preferred, Fitz #4):** make the floor UNCONDITIONAL inside `build_soft_anchor_posterior` (epsilon floor replacing the −inf branch) so the category is structurally impossible and the test passes regardless of flags. Do NOT ship U0R with this red — at flag-OFF a 0-vote bin stays un-hittable → manufactured impossible buy_no that loses.
- **REAL FAULT B (data) — IRON RULE #3:** the U0R continuous multi-source ingest does not exist in LIVE (decorrelated globals/regionals = 0 rows ever; capture writes nothing; `_history_provider` never assigned → empty history → fusion = EQUAL_WEIGHT, never T2_BAYES). **Resolution:** §4 wiring spec. Gate U0R authority on a positive multimodel-coverage assertion, not just the flag (else a U0R-LABELED posterior is actually pure single-anchor).
- **REAL FAULT C (live-state risk, incidental):** all 5 soft_anchor authority flags = TRUE (config:294-298) → `resolve_replacement_forecast_runtime_policy` maps to LIVE_AUTHORITY (incl. direction-flip + kelly) for the SINGLE-ANCHOR path with only 2 days of history; yet the row hard-stamps `SHADOW_ONLY` (193/193). Flag-resolution and row-stamp **disagree on the same posterior's authority.** Operator must reconcile which is truth before relying on the runtime policy.
- **RESOLVED — Conflicts 6/7/8:** `full_transport_v1` reads are behind a default-OFF flag, fail to plain p_raw (inert, candidate dead-code removal once unify ships); q_lcb has ONE seam (EMOS bootstrap live, Wilson raw fallback, edge bootstrap CI — not parallel authorities); `posterior_method` column stays `soft_anchor` by schema-compat design while U0R truth lives in `posterior_config` (cosmetic until U0R has authority; add reader-side assertion to resolve method from `posterior_config`).

---

## (4) WIRING SPEC — live continuous U0R multi-model download

**Current reality (verified):** the "no-op seam" is two-layered, and only the FETCH half is real.
`_default_live_fetch` (`u0r_multimodel_capture.py:117-168`) already calls the real Open-Meteo
single-runs fetcher per model. But (a) it runs ONLY synchronously inside
`_replacement_u0r_fusion_override` (`materializer:599`) gated by `replacement_0_1_u0r_fusion_enabled`
(FALSE); (b) the capture performs **ZERO DB writes** — nothing persisted/walk-forward-usable;
(c) the recurring scheduler `replacement_forecast_shadow_materialize` (`main.py:4857`) downloads
ONLY anchor+AIFS (`download_current_target_raw_inputs`, only `skip_aifs`/`skip_openmeteo` flags —
no gfs/icon/gem/jma/arome/d2 call); (d) the walk-forward HISTORY provider is the genuine no-op
(`_empty_history_provider` → {}; `_history_provider` is `getattr(...,None)` at `materializer:596`,
**never assigned**) → every extra gets bias=0 and fusion degrades to EQUAL_WEIGHT, never T2_BAYES.
**IRON RULE #4 fault:** spec §6 F1 mandates a `raw_model_forecasts` table that **does NOT EXIST** in
any schema (grep = 0 hits); the only persistence target is `raw_forecast_artifacts` (no extras, no
retention/prune).

**Build steps (one flag, no parallel switch — IRON RULE #4):**

1. **Fetcher (exists, reuse + extend).** CAPTURE (live decision): reuse `_default_live_fetch` per
   model → single-runs URL (`SINGLE_RUNS_FORECAST_URL`) with the 8 OM model ids
   (gfs_global, icon_global, gem_global, jma_seamless, icon_eu, icon_d2, meteofrance_arome_france_hd,
   +icon_seamless for alias-dedup). Params: lat, lon, hourly=temperature_2m, models=<OM id>,
   run=<00/06/12/18>, forecast_hours=120, temperature_unit=celsius, timezone=<city tz>.
   WALK-FORWARD HISTORY (new): add a SECOND fetch path against the **previous-runs API** (fixed-lead
   history). Registry already maps gfs→gfs_previous_runs, icon→icon_previous_runs,
   ecmwf→ecmwf_previous_runs (`forecast_source_registry.py:154-160`); add gem/jma/icon_d2/arome
   previous-runs entries. Required per spec §3 (previous-runs = fixed-lead bias/skill train only;
   single-runs/live capture for replay).

2. **Register on the LIVE recurring scheduler (forward, daily, not one-shot).** Extend
   `download_current_target_raw_inputs` (or add `download_u0r_extra_raw_inputs`) to loop the
   current-target (city, metric, target_date, cycle) plan × the 8 extra models, fetch single-runs
   (forward, fixed cycle) AND previous-runs (fixed-lead), gate by `replacement_0_1_u0r_fusion_enabled`,
   persist each. Drive it from `_replacement_forecast_shadow_materialize_cycle` (`main.py:4858`,
   parallel to the anchor/AIFS download at :4867) so it persists FORWARD daily.

3. **Persistence target + retention (build it).** Add the spec-named `raw_model_forecasts` table to
   `v2_schema.ensure_replacement_forecast_shadow_schema` (model, city, target_date, metric,
   source_cycle_time, source_available_at, captured_at, lead_days, forecast_value_c,
   endpoint{single_runs|previous_runs}, trade_authority_status='SHADOW_ONLY', training_allowed=0,
   UNIQUE(model,city,target_date,metric,source_cycle_time,endpoint)). Prefer this over overloading
   `raw_forecast_artifacts`/`deterministic_forecast_anchors`. Retention ~6mo (spec §5) via a prune
   step in the same job (DELETE WHERE captured_at < now−180d).

4. **Walk-forward provider (implement + wire).** After single-runs (forward) AND previous-runs
   (fixed-lead) extras persist daily, implement a real `U0RHistoryProvider` reading persisted
   previous-runs forecasts JOINed to VERIFIED settlement, ordered strictly target_date<decision_date
   (no leak), returning `ModelHistory` per model (`capture.py:67-87`). **Wire it by ASSIGNING**
   `_replacement_u0r_fusion_override._history_provider` (replace the `None` getattr at
   `materializer:596`). Only then does `n_train` cross `MIN_TRAIN=25` (`u0r_bayes.py:52`) and the
   fusion reach **T2_BAYES** with EB bias-correction (until then: EQUAL_WEIGHT, `u0r_bayes.py:279-288`).

5. **Activation order (single flag).** (a) merge thepath U0R modules to LIVE; (b) build
   `raw_model_forecasts` + the forward recurring multi-model download (steps 2-3); (c) implement +
   wire the real `U0RHistoryProvider` (step 4); (d) run flag-OFF accruing ≥25 days history per
   (city,metric,lead); (e) flip `replacement_0_1_u0r_fusion_enabled` ON so `fuse_u0r_posterior`
   reaches T2_BAYES and `the_path_u0r_fusion` provenance appears (today=0). **Until (b)-(d), flipping
   the flag yields only EQUAL_WEIGHT — not the proven core — or a single-anchor byte-identical no-op.**
   Gate U0R trade authority on a positive multimodel-coverage assertion, not the flag alone.
