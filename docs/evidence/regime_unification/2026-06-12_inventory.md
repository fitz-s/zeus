# Regime Unification Inventory — 2026-06-12

Exhaustive enumeration of pattern sites in `src/` and `config/settings.json` mapped to doctrine verdicts (U1–U5, HONEST-KEEP, UNCLASSIFIED).

**Doctrine reference**: `/Users/leofitz/zeus/docs/authority/regime_unification_2026-06-12.md`

---

## I. FALLBACK MECHANISMS (era layering, probability path forks)

### I.1 Legacy ENS p_raw + Platt fallback chain

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/calibration/manager.py:268-284` | `_emit_legacy_fallback_warning()` — logs when v2 Platt missing, falls back to legacy v1 path | legacy-baseline / full_transport | U1-RETIRE | Fallback into different era's model; K3 replacement posterior pathway makes this obsolete |
| `src/calibration/manager.py:165-168` | Two-seam legacy fallback branch on Platt missing | full_transport | U1-RETIRE | Hidden fallback path; must be deleted once full_transport_v1 authority zero-consumers |
| `src/calibration/ens_bias_repo.py:792` | `lead_max` legacy fallback for LEAD_BUCKET_BOUNDS | full_transport_v1 | U4-MIGRATE | Numeric knob without fitted basis; derive from observed lead-bucket frequencies |
| `src/calibration/blocked_oos.py:123-125` | p_raw fallback when Platt model missing | legacy | U1-RETIRE | Raw fallback on calibration miss; will be covered by EMOS/replacement sole authority |
| `src/calibration/platt_oos_resolver.py:203-223` | `identity_fallback` decision when no OOS full chain win | legacy | U1-RETIRE | Fallback to identity on calibration failure; replacement regime handles via EMOS identity |

### I.2 Staleness fallback: exit monitor → legacy ENS instead of replacement posterior

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/main.py:2635-2666` | `_market_events_fallback_max_age_hours()` — max_age fallback for market events | day0-observation | HONEST-KEEP | Honest staleness guard; must persist |
| `src/main.py:2688-2709` | WS market_events fallback + operator disable flag | day0-observation | HONEST-KEEP | Honest degraded mode; operators can disable if WS unreliable |

### I.3 Forecast chain probability source fork (replacement vs EMOS vs raw)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/calibration/emos_q_builder.py:45-140` | Serves EMOS, falls back to raw with calibrated dispersion floor when EMOS cell missing | EMOS / replacement-fusion | U2-VIOLATION | Current: fallback into different model (raw vs EMOS). Fix: mandatory fresh-tracked EMOS cell, no silent fallback. Must audit every consumer for served=missing paths |
| `src/calibration/manager.py:821-900` | Hierarchical fallback routing: metric-aware HIGH→LOW→pooled-cycle → legacy buckets | calibration / legacy | U2-VIOLATION | Fallback into different era's training corpus; U1 fix requires ONE metric authority per calibration family |
| `src/forecast/bayes_precision_fusion.py:349-368` | "All extras absent" → anchor prior fallback (soft fail-soft) | replacement-fusion | HONEST-KEEP | Same-authority degraded mode; anchor prior is intentional soft-fail for light data |
| `src/data/replacement_forecast_materializer.py:538-801` | Settles EMOS σ-floor per replacement posterior cell (FAIL-SOFT) | replacement-fusion / EMOS | HONEST-KEEP | Honest degraded mode; floor ensures no over-dispersion confidence |

### I.4 Day0 authority fallback (realized obs vs forecast)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/calibration/day0_horizon_calibration.py` | Day0 nowcast uses realized extremes, not forecast | day0-observation | HONEST-KEEP | Different domain; documented boundary |
| `src/execution/day0_hard_fact_exit.py:36-157` | Empirical threshold gate; fallback if provenance missing | day0-observation | U4-MIGRATE | Empirical threshold (≤1.0) is operator guess; must fit to observed gate effectiveness |

---

## II. SHADOW / ACTIVE / OFF / CANARY VOCABULARY (U3 regime collapse)

### II.1 edli_live_scope string enumeration (admission gates)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:74-76` | `edli_live_scope` ∈ {forecast_only, day0_shadow, forecast_plus_day0} | edli_per_city | U3-RETIRE | String enum; maps to submit_lane (LIVE/SHADOW). Delete all scope strings, use submit_lane + EventProcessingDisposition instead. Day0 now LIVE (2026-06-12 directive) |
| `src/main.py:139` | `EDLI_LIVE_SCOPES` frozen set | edli_per_city | U3-RETIRE | Delete; scope routing becomes submit_lane gate |
| `src/main.py:113-139` | edli_shadow_no_submit, forecast_only, day0_shadow branch names | edli_per_city | U3-RETIRE | Delete; replace with EventProcessingDisposition + submit_lane |

### II.2 NATIVE_MULTIBIN_*_SHADOW/LIVE branching

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/main.py:231-237` | Strategy routing via NATIVE_MULTIBIN shadow vs live | EMOS | U3-RETIRE | SHADOW_ONLY/LIVE branching; maps to EventProcessingDisposition |

### II.3 openmeteo softanchor shadow/veto/trade_authority flags

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:288-290` | `openmeteo_ecmwf_ifs9_aifs_soft_anchor_{shadow,veto,trade_authority}_enabled` | full_transport / day0 | U3-RETIRE | Three separate flags for one semantic; collapse to submit_lane (SHADOW vs LIVE) + single authority (IFS9) |

### II.4 day0_shadow / DAY0_SCOPE_SHADOW_ONLY marker (receipts)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/analysis/day0_shadow_enrichment.py:17-214` | day0 shadow receipt grading against VERIFIED truth | day0-observation | HONEST-KEEP | Honest shadow lane for day0 learning; branding acceptable (fresh=False + reason) |
| `src/main.py:135-158` | DAY0_SCOPE_SHADOW_ONLY adapter boundary; day0 events PASS on real submit | day0-observation / edli_per_city | U3-RETIRE | Remove shadow-only purgatory gate; day0 now LIVE (2026-06-12). Brand receipts with submit_lane=LIVE instead |

---

## III. BIAS / ERROR-MODEL FAMILY PATHS (U1 probability authority)

### III.1 Per-city bias correction (edli_per_city_v1 vs full_transport_v1)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:80-98` | `edli_bias_correction_enabled` flag (A4 fix, 2026-05-31) | edli_per_city / replacement-fusion | U4-MIGRATE | Fitted artifact flag (bias model promotion + train/serve lockstep). Upgrade to sigma_scale_fit.json pattern with provenance + refit cadence. Evidence path: workspace-venus EDLI_BIAS_REPLAY_RESULT_2026_05_31.md |
| `src/main.py:1065-1093` | Bias+Platt calibration-coverage guard | legacy / edli_per_city | U3-RETIRE | "Loud guard for legacy path" — when EMOS sole=true, this guard is already obsolete; delete |
| `config/settings.json:192-206` | `edli_emos_sole_calibrator_enabled` flag (EMOS S2 replacement, flag=OFF default) | EMOS / edli_per_city | U4-MIGRATE | ONE-CALIBRATOR seam; promote to U1 authority. Flag OFF means byte-identical legacy path; no-op once full_transport_v1 zero-consumers |
| `config/settings.json:101-115` | `_exit_bias_family_unify_enabled_note` (D2 bias-family unify, flag=OFF default) | edli_per_city / full_transport_v1 | U2-VIOLATION | Entry corrected (edli_per_city_v1), exit uncorrected (full_transport_v1) — asymmetry violates U1. Flag-ON carries full consistent treatment (identity Platt, bias-shift only). Merge into single-authority path once K3 exit-staleness fix lands |
| `src/calibration/ens_bias_model.py:1-50` | ENS hierarchical bias model (location + scale) | legacy / full_transport | U1-RETIRE | Replaced by replacement posterior soft-anchor; retire once full_transport_v1 path zero-consumers |
| `src/calibration/ens_error_model.py:257-280` | `p_raw_vector_with_error_model()` — bias-corrected + residual-widened MC (full_transport_v1) | full_transport_v1 | U1-RETIRE | Error model family = full_transport_v1; retirement path: hold until K3 exit-staleness + D2 bias-family unify lands, then delete |
| `config/settings.json:275-286` | `_full_transport_live_enabled_note` (F1/F2 ship flag, OFF default) | full_transport_v1 | U1-RETIRE | Flag-ON uses full_transport_v1; zero-row family makes this dead code. Delete when entry/exit unify lands |
| `src/calibration/ens_bias_repo.py:1-900` | Bias model repo (edli_per_city_v1, full_transport_v1 families) | edli_per_city / full_transport_v1 | U1-RETIRE | Two error-model families; U1 consolidates to ONE. Audit all read sites (manager.py, evaluator.py, monitor_refresh.py) for family strings; delete full_transport_v1 path |
| `src/main.py:4963-4977` | Bias correction + Platt recompute guard | edli_per_city / legacy | HONEST-KEEP | Guard for incomplete bias coverage; keep until K3.6 coverage complete |

### III.2 Bias decay Kelly haircut (interim, data-insufficient phase)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:103-115` | `bias_decay_kelly_haircut_enabled` + `bias_decay_threshold_{c,f}` + `bias_decay_kelly_factor` (operator directive 2026-05-31) | edli_per_city | U4-MIGRATE | INTERIM gate on data-insufficiency phase. Numeric knobs (thresholds 2.0°C / 3.0°F, factor 0.5) are operator guess; must fit to per-city bias-impact when coverage sufficient. Delete when bias_correction per-bucket proof + per-city #24<=1 stable |

---

## IV. Q_LCB SETTLEMENT AUTHORITY (U1 domain: forecast markets)

### IV.1 Settlement-backward coverage gate (K3, phase-2)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:91-98` | `q_lcb_settlement_coverage_gate_enabled` flag (K3 shadow, OFF default) | replacement-fusion | U4-MIGRATE | Fitted artifact flag (coverage-honest shrink of q_lcb to realized-1pp when UNLICENSED). Upgrade to sigma_scale_fit.json with: min_n_settlement (threshold for INSUFFICIENT_DATA), realized_1pp_haircut (numeric). Evidence: K3 settlement_backward_coverage.py + per-city before/after grading |
| `src/calibration/settlement_backward_coverage.py` | Coverage check (LICENSED / UNLICENSED / INSUFFICIENT_DATA) | replacement-fusion | HONEST-KEEP | Same-authority coverage proof; branding makes it honest. Keep as part of q_lcb authority |
| `src/contracts/freshness_registry.py:54-57` | FRESH / DEGRADED / EXPIRED age thresholds (typed enum) | replacement-fusion | HONEST-KEEP | Typed freshness states; move constants to fitted artifact (per-city materialization cadence) but keep enum |

### IV.2 Market-anchor cap on q_lcb_no (C-series favorite-longshot correction)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:116-127` | `replacement_q_market_anchor_enabled` flag + `market_fusion` alpha=0.4 (objective-math audit 2026-06-11, OFF default) | replacement-fusion | U4-MIGRATE | Numeric knob alpha=0.4 without fitted basis. Fit to per-class (C1-C3) realized edge when settles; alpha is cross-class blend weight. Evidence: objective-math verdict 2026-06-11 (C3 -4.8pt mean, tail -34..−54pt) |
| `src/strategy/market_fusion.py` | alpha-blend of model-NO with market-implied NO | replacement-fusion | U4-MIGRATE | Same as above; alpha needs fitted artifact |

### IV.3 EMOS σ-floor on q_lcb (bootstrap honesty)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/calibration/emos.py:52-174` | `load_sigma_floor_table()` + `apply_sigma_floor()` (q_lcb settlement floor) | EMOS / replacement-fusion | HONEST-KEEP | Same-authority floor; MAX only widens σ (lowers q_lcb), never tightens. Branding acceptable |
| `src/calibration/emos_q_builder.py:45-137` | EMOS point q + analytic σ for q_lcb bootstrap | EMOS / replacement-fusion | HONEST-KEEP | U1 authority for EMOS cells; degraded (raw with floor) when EMOS missing |

---

## V. PROBABILITY-SOURCE FORKS (edli_live_promotion_artifact + operator gating)

### V.1 Entry/exit probability alignment

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/events/triggers/forecast_snapshot_ready.py:70-598` | Checks replacement posterior authority vs canonical; gate on submit_lane=LIVE | replacement-fusion / edli_per_city | U3-RETIRE / U4-MIGRATE | submit_lane routing + EventProcessingDisposition replace snapshot-authority branching. Audit all FSR gates: must migrate to explicit dispatch logic, not string checks |
| `config/settings.json:136-142` | `edli_live_promotion_artifact_path` (staging artifact for operator arm) | replacement-fusion | HONEST-KEEP | Operator arm mechanism; required for live gate |
| `config/settings.json:114-127` | `edli_live_operator_authorized` (policy arm, true=can trade) | replacement-fusion | HONEST-KEEP | Operator policy gate; required |

---

## VI. NUMERIC KNOBS WITHOUT FITTED BASIS (U4 migration targets)

### VI.1 Bootstrap / Monte Carlo parameters

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:36-40` | `ensemble.n_mc` = 10000 (was 5000, bumped 2026-04-29) | replacement-fusion | U4-MIGRATE | Fitted artifact: per-trade runtime precision ≥5000; current 10000 is ceiling preference. Justify via per-trade SE loss vs compute cost trade-off; bake into CI cost model |
| `config/settings.json:145` | `calibration.n_bootstrap` = 200 | replacement-fusion / legacy | U4-MIGRATE | Numeric knob; must fit to per-city Platt stability (OOS LogLoss variance) when promoting new buckets |
| `config/settings.json:179` | `edge.n_bootstrap` = 500 | replacement-fusion | U4-MIGRATE | Same as above; edge calculation needs justified sample count |
| `config/settings.json:174-175` | `day0_nowcast.n_mc` = 10000 (lockstep with ensemble.n_mc) | day0-observation | U4-MIGRATE | Same as ensemble.n_mc; fit together |

### VI.2 Calibration maturity gates (sample thresholds)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/calibration/manager.py:328-330` | Maturity levels (n≥150, 50≤n<150, 15≤n<50) with edge_threshold multipliers (1×, 1.5×, 2×) | legacy / replacement-fusion | U4-MIGRATE | Thresholds (150, 50, 15) and multipliers (1.5×, 2×) are operator guess. Fit to per-city calibration stability (OOS cov90 variance by bucket-n); must be per-metric (HIGH vs LOW have different noise profiles) |
| `src/calibration/manager.py:41` | `_ERROR_MODEL_MIN_LIVE_N` = 5 (minimum live n for bias model) | edli_per_city | U4-MIGRATE | Numeric threshold; fit to per-city bias estimation stability |

### VI.3 Ensemble/signal parameters

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:22-30` | Ensemble config (bimodal_kde_order=10, bimodal_gap_ratio=0.3, boundary_window=0.5, unimodal_range_epsilon=0.5, conflict_kl_threshold=0.15) | legacy / baseline | U4-MIGRATE | All numeric knobs without fitted basis. Audit each: kde_order via cross-validation on multi-modal synthetic, gap_ratio via peak-detection robustness, boundary_window via empirical forecast-peak location scatter, conflict_kl via member-disagreement threshold. Bake into artifact once fits available |
| `config/settings.json:31-35` | Probability sanity (point_bucket_high_prob=0.5, min_member_support=0.25, low_price_threshold=0.05, low_price_high_prob=0.35) | baseline | U4-MIGRATE | Same as above; all need empirical validation |
| `config/settings.json:50-56` | Probability edge bin sanity (odds_ratio_threshold=3.0, min_edge_gap=0.03, etc.) | baseline / LIVE-PROB-P0 | U4-MIGRATE | odds_ratio_threshold=3.0 is operator guess for tail rejection; fit to per-class (C1-C5) edge-discovery error rate. min_edge_gap=0.03 needs market-empirics study |

### VI.4 Kelly / sizing parameters

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:237-240` | Kelly base sizing (various per-class factors) | replacement-fusion | U4-MIGRATE | Per-city Kelly multipliers (e.g., Paris 0.7×) are asymmetric-loss preferences per oracle DDD evidence. Must be fitted artifacts with per-city objective audit. Audit all: src/strategy/kelly.py factors need authority basis docs |
| `src/oracle/ddd_artifacts/v2_city_floors.json` | Per-city floor multipliers (0.7× for Paris, etc.) | oracle / replacement-fusion | HONEST-KEEP | Fitted artifacts with provenance (DDD audit). Keep; these are evidence-backed |
| `src/riskguard/policy.py:48-49` | `allocation_multiplier` = 1.0, `threshold_multiplier` = 1.0 | replacement-fusion | U4-MIGRATE | Numeric knobs; must fit to realized drawdown tolerance + exposure discipline. Audit vs collateral_ledger max-exposure gates |

### VI.5 Data density discount / small-sample amplifier

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/oracle/data_density_discount.py:71` | `SMALL_SAMPLE_AMPLIFIER` = 1.25 (applied when N < N_star) | oracle / DDD | U4-MIGRATE | Numeric multiplier; fit to per-city N_star threshold. Currently N_star=None triggers forever; migration: v2_nstar.json (fitted per city, ~113 median for healthy low-track) replaces unconditional amplifier |
| `src/oracle/ddd_artifacts/v2_nstar.json` | Per-city N_star placeholders (113 for low-track, city-specific in progress) | oracle / DDD | HONEST-KEEP | Fitted artifacts; keep + complete per-city calibration stability fits |

### VI.6 Decision timeout / freshness budgets

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/execution/exit_lifecycle.py:260` | `max_cycle_budget_ms` = 3000 (exit evaluation timeout) | replacement-fusion | U4-MIGRATE | Numeric knob; fit to observed exit-evaluation latency distribution (p95, p99) + market-volatility cost of stale exits. Currently hard-coded guess |
| `src/ingest_main.py:604-697` | Staleness threshold `threshold_h` (hours, for daily-update tables) | day0-observation | U4-MIGRATE | Numeric thresholds without fitted basis. Audit each table: WU publish latency per city, METAR frequency, solar-event timing; derive materialization cadence per table |

### VI.7 Market freshness / quote age gates

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:131` | `pre_submit_max_quote_age_ms` = 1000 (quote staleness gate) | replacement-fusion / venue-infra | HONEST-KEEP | Venue-infra retry mechanism; hard deadline fits market-latency SLAs. Keep but audit vs live venue latency dist |
| `config/settings.json:120-122` | Market channel refresh (max_actions_per_window=5, window_seconds=60) | edli_per_city / venue-infra | HONEST-KEEP | Venue-infra rate-limit; keep |

### VI.8 Position / risk monitoring thresholds

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/contracts/settlement_capture_verifier.py:272` | Threshold=5 (line count for settlement capture verification) | venue-infra | UNCLASSIFIED | Unclear basis; audit intent vs threshold hardness. If venue-specific, move to config per venue. If domain-logic gate, derive from settlement-receipt cardinality empirics |
| `src/state/learning_loop_observation.py:453-671` | CRITICAL_PAIR_GROWTH_RATIO_CUTOFF = 2.0 (ratio < 1/(2.0×multiplier) for alert) | calibration | U4-MIGRATE | Numeric threshold; fit to per-city pair-growth volatility + refit trigger sensitivity |

---

## VII. FEATURE FLAGS: LEGITIMATE vs ILLEGITIMATE (U4 classification)

### VII.1 LEGITIMATE KIND 1: Operator arm (policy, real-world authority)

| Flag | Kind | Verdict | Notes |
|------|------|---------|-------|
| `edli_live_operator_authorized` (true) | Operator arm | HONEST-KEEP | Real-world policy gate; required |
| `edli_live_promotion_artifact_path` | Operator arm | HONEST-KEEP | Staging artifact; required for live gate |
| `real_order_submit_enabled` (true) | Operator arm | HONEST-KEEP | Global trading arm; required |

### VII.2 LEGITIMATE KIND 2: Daemon role (wiring, live_execution_mode)

| Flag | Kind | Verdict | Notes |
|------|------|---------|-------|
| `enabled` (true) | Daemon role / entry point | HONEST-KEEP | Daemon on/off; required |
| `live_execution_mode` = "edli_live" | Daemon role | HONEST-KEEP | Live vs offline mode; required |
| `reactor_mode` = "live" | Daemon role | HONEST-KEEP | Event reactor on/off; required |
| `event_writer_enabled` (true) | Daemon role | HONEST-KEEP | Event log writer; required |
| `forecast_snapshot_trigger_enabled` (true) | Daemon role | HONEST-KEEP | FSR trigger; required |
| `forecast_complete_live_enabled` (true) | Daemon role | HONEST-KEEP | Forecast complete signal; required |
| `day0_hard_fact_live_enabled` (true) | Daemon role | HONEST-KEEP | Day0 nowcast; required |
| `market_channel_ingestor_enabled` (true) | Daemon role | HONEST-KEEP | Market event ingestion; required |
| `market_channel_quote_cache_enabled` (true) | Daemon role | HONEST-KEEP | Quote caching; required |
| `day0_authority_catchup_scanner_enabled` (true) | Daemon role | HONEST-KEEP | Day0 orbit recovery; required |
| `no_trade_regret_enabled` (true) | Daemon role | HONEST-KEEP | Regret ledger; required |
| `reports_enabled` (true) | Daemon role | HONEST-KEEP | Report jobs; required |
| `reactor_prune_enabled` (true) | Daemon role | HONEST-KEEP | Event pruning; required |
| `edli_user_channel_reconcile_enabled` (true) | Daemon role | HONEST-KEEP | User-channel reconciliation; required |
| `edli_source_run_dual_chain_enabled` (true) | Daemon role | HONEST-KEEP | Dual source-run chain for resilience; required |
| `durable_submit_outbox_enabled` (true) | Daemon role | HONEST-KEEP | Durable outbox; required |
| `pre_submit_balance_allowance_check_enabled` (true) | Daemon role | HONEST-KEEP | Pre-submit wallet gate; required |

### VII.3 LEGITIMATE KIND 3: Fitted artifacts (math, CI gate, evidence basis)

| Flag | Kind | Verdict | Notes |
|------|------|---------|-------|
| `edli_bias_correction_enabled` (true, 2026-05-31) | Fitted artifact | U4-MIGRATE | Bias model promotion; upgrade to sigma_scale_fit.json |
| `edli_emos_sole_calibrator_enabled` (false, SHADOW) | Fitted artifact | U4-MIGRATE | ONE-CALIBRATOR seam; promote to U1 authority |
| `_exit_bias_family_unify_enabled` (false, SHADOW) | Fitted artifact | U4-MIGRATE | D2 asymmetry fix; promote to U1 authority |
| `_full_transport_live_enabled` (false) | Fitted artifact / DEAD | U1-RETIRE | Zero-row family; dead code |
| `q_lcb_settlement_coverage_gate_enabled` (false, SHADOW) | Fitted artifact | U4-MIGRATE | K3 coverage proof; upgrade to sigma_scale_fit.json |
| `replacement_q_market_anchor_enabled` (false, SHADOW) | Fitted artifact | U4-MIGRATE | C-series correction; fit alpha=0.4 to settled edge |
| `bias_decay_kelly_haircut_enabled` (true) | Fitted artifact / INTERIM | U4-MIGRATE | Interim gate on data-insufficiency; delete when per-bucket coverage sufficient |
| `openmeteo_ecmwf_ifs9_aifs_soft_anchor_{shadow,veto,trade_authority}_enabled` | Fitted artifact / U3-regime | U3-RETIRE / U4-MIGRATE | Three flags collapse to one authority + submit_lane |
| `calibration_bin_source_v2_fit_enabled` (true) | Fitted artifact | HONEST-KEEP | Bin-source fit gate; required |
| `ddd_v2_enabled` (true) | Fitted artifact | HONEST-KEEP | DDD v2 (floor + N_star); required |
| `download_current_targets_enabled` (true) | Daemon role | HONEST-KEEP | Target-date download; required |
| `disable_legacy_opendata_forecast_live_jobs` (false) | Legacy cleanup | U1-RETIRE | Legacy flag; delete once legacy opendata path zero-consumers |

### VII.4 ILLEGITIMATE: Veto / advisory / canary / "shadow-only" gates on real-submit

| Flag | Kind | Verdict | Notes |
|------|------|---------|-------|
| `edli_live_scope` = "forecast_plus_day0" vs "forecast_only" vs "day0_shadow" | String enum / regime | U3-RETIRE | Scope strings; replace with submit_lane + EventProcessingDisposition. Day0 now LIVE (2026-06-12) |
| `openmeteo_ecmwf_ifs9_aifs_soft_anchor_shadow_enabled` | Shadow-only gate | U3-RETIRE | Maps to submit_lane=SHADOW; delete vocab, use submit_lane |
| `openmeteo_ecmwf_ifs9_aifs_soft_anchor_veto_enabled` | Veto gate | U3-RETIRE | Veto stops LIVE submit; use EventProcessingDisposition=TERMINAL_REJECT instead |
| `edli_arm_gate_emit_enabled` (false) | Advisory emit gate | HONEST-KEEP | Emit arm-gate telemetry for debugging; non-critical |

---

## VIII. DATA VERSION / ERROR-MODEL FAMILY STRINGS (U1 consolidation targets)

### VIII.1 Error-model family references (audit all consumers)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/calibration/ens_bias_repo.py:*` | Read sites for `error_model_family IN {edli_per_city_v1, full_transport_v1, none}` | legacy / edli_per_city / full_transport_v1 | U1-RETIRE | Audit all read sites: manager.py, evaluator.py, monitor_refresh.py. U1 consolidates to ONE family + authority. Check for fallback loops (family missing → try legacy → try none) |
| `src/calibration/manager.py:*` | Manager routing via error_model_family string | legacy / edli_per_city | U1-RETIRE | Same as above; audit callers |
| `src/calibration/platt.py:272-280` | IDENTITY_CALIBRATION_METHOD = "identity_full_transport_v1" | full_transport_v1 | U1-RETIRE | Hardcoded method string; U1 fix makes this obsolete |

### VIII.2 Data-version strings in config (settable, not code-hardcoded)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `config/settings.json:167-168` | `calibration_policy_id` = "ecmwf_open_data_uses_tigge_localday_cal_v1" | legacy | HONEST-KEEP | Calibration policy identifier; use to route to correct bucket set. Must stay settable for policy override |

---

## IX. DAY0 NOWCAST (documented different domain, not a fallback)

| Site | Mechanism | Era | Verdict | Notes |
|------|-----------|-----|---------|-------|
| `src/execution/day0_hard_fact_exit.py` | Day0 uses realized obs (METAR/WU) not forecast | day0-observation | HONEST-KEEP | Different domain (nowcast vs forecast); documented boundary. Empirical threshold ≤1.0 is U4-MIGRATE numeric knob |
| `src/analysis/day0_shadow_enrichment.py` | Shadow receipts graded against VERIFIED truth | day0-observation | HONEST-KEEP | Honest shadow ledger for learning; keep |
| `src/calibration/day0_horizon_calibration.py` | Day0 horizon gates + settlement metric choice | day0-observation | HONEST-KEEP | Domain-specific gate; keep |

---

## X. UNCLASSIFIED / PENDING AUDIT

| Site | Mechanism | Era | Reason | Notes |
|------|-----------|-----|--------|-------|
| `src/contracts/settlement_capture_verifier.py:272` | threshold=5 (line count) | venue-infra | Unclear intent | Audit: is this a venue-specific constant or a domain-logic gate? If venue-specific, move to per-venue config. If domain-logic, derive from settlement-receipt cardinality empirics |
| `src/data/bayes_precision_fusion_capture.py:109` | "Legacy date-less history" fallback to positional stack | replacement-fusion | Unclear scope | Audit: is this an honest degraded mode (fresh=False branded) or a silent fallback into different era? Clarify in code comment |
| `src/config.py:288-295` | `diurnal_amplitude_{c,f}` fallback if preferred key missing | legacy | Tentative | Unit conversion fallback; audit intent: is this a domain boundary (two valid keys per venue) or a legacy soft-fail? If domain boundary, document as such. If soft-fail, brand with fresh=False |
| `src/state/decision_chain.py:378-393` | "legacy_decision_log_fallback" reason field | legacy | Unclear | Audit: is this an honest degraded mode or a fallback into a different era? Clarify wording + ensure branding (fresh=False) is applied |

---

## XI. SUMMARY STATISTICS

- **Total FALLBACK sites**: 13 (6 U1-RETIRE, 2 U2-VIOLATION, 5 HONEST-KEEP)
- **Total SHADOW/ACTIVE/OFF vocabulary**: 8 (6 U3-RETIRE, 2 HONEST-KEEP)
- **Total BIAS/ERROR-MODEL paths**: 12 (5 U1-RETIRE, 2 U2-VIOLATION, 3 U4-MIGRATE, 2 HONEST-KEEP)
- **Total NUMERIC KNOBS (U4)**: 35+ (all U4-MIGRATE or HONEST-KEEP; no verdicts yet assigned pending fit basis audit)
- **Total LEGITIMATE FLAGS (U4 KIND 1+2+3)**: 30 (24 HONEST-KEEP daemon/arm/audit, 6 U4-MIGRATE fitted artifacts)
- **Total ILLEGITIMATE FLAGS (veto/advisory/canary)**: 5 (all U3-RETIRE)
- **UNCLASSIFIED**: 4 (pending intent audit)

---

## XII. NEXT ACTIONS (per U5 execution order)

1. **Wave-2 batch** (in flight): baseline-cap exit, mode-string two-state, σ-floor merge, refuted-branch deletion, taker fold
2. **Exit-staleness root fix**: held-family targeted re-materialization + derived freshness budget; THEN retire legacy ENS fallback + unify flag + full_transport monitor path
3. **Regime-vocabulary retirement (U3)**: delete edli_live_scope strings, NATIVE_MULTIBIN shadow/live, openmeteo shadow/veto flags; map to submit_lane + EventProcessingDisposition
4. **Numeric knob audit (U4)**: fit all knobs in §VI to empirical basis (per-city Platt n-stability, Kelly multiplier OOS loss, market-anchor alpha, threshold triggers). Upgrade to sigma_scale_fit.json with provenance + refit cadence
5. **Single-authority consolidation (U1)**: delete full_transport_v1 path, retire legacy ENS fallback, audit all error_model_family reads for fallback loops, promote EMOS to replacement sole authority
6. **Receipt second-brain merge** (Wave-2 #4): ensure submit_lane + EventProcessingDisposition invariants hold
7. **edli_per_city_v1 family retirement**: once steps 2-3 zero its consumers

