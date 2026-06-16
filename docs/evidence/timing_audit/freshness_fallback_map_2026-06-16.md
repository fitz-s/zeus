# Zeus Money-Path Freshness / Fallback / Refuse Map
**Audit date:** 2026-06-16  
**Auditor:** Explorer agent (read-only, branch `live/iteration-2026-06-13`)  
**Scope:** Contract → Source/Forecast → Calibration → Edge → Selection → Execution/Price → Monitor/Exit → Settlement

---

## Master Gate Table

| # | file:line | data_type | threshold (exact value / expression) | classification |
|---|-----------|-----------|---------------------------------------|----------------|
| 1 | `src/control/freshness_gate.py:51-59` | Observation source families (`open_meteo_archive`, `wu_pws`) | 6h = 21600s (FRESHNESS_BUDGETS) | FIXED-NUMBER |
| 2 | `src/control/freshness_gate.py:54-58` | Observation source families (`hko`, `ogimet`, `noaa`) | 36h = 129600s (FRESHNESS_BUDGETS) | FIXED-NUMBER |
| 3 | `src/control/freshness_gate.py:56-58` | Ensemble sources (`ecmwf_open_data`, `tigge_mars`) | 24h = 86400s (FRESHNESS_BUDGETS) | FIXED-NUMBER |
| 4 | `src/control/freshness_gate.py:62-64` | DAY0_CAPTURE mode gate | staleness of any `DAY0_CAPTURE_GATED_SOURCES` member | REFUSE / FAIL-CLOSED |
| 5 | `src/control/freshness_gate.py:63-64` | OPENING_HUNT / ensemble nowcast gate | staleness of any `ENSEMBLE_GATED_SOURCES` member | LOGGED/TAGGED-FALLBACK (degrades, continues with flag) |
| 6 | `src/control/freshness_gate.py:69` | source_health.json mid-run advisory | 5 min = 300s (advisory only; not a gate) | NO-GATE (advisory log only, does not block) |
| 7 | `src/control/freshness_gate.py:298-316` | Mid-run freshness overall | degrade-only; never exit; logs all stale sources | LOGGED/TAGGED-FALLBACK |
| 8 | `src/control/freshness_gate.py:258-274` | Boot-time freshness | 5-min retry loop (30 × 10s), then FATAL | REFUSE / FAIL-CLOSED |
| 9 | `src/engine/cycle_runner.py:606-607` | IMMINENT_OPEN_CAPTURE mode freshness | same as DAY0_CAPTURE (fail-closed, 24h horizon) | REFUSE / FAIL-CLOSED (code comment: "no time to recover from a bad trade on stale signals") |
| 10 | `src/engine/cycle_runner.py:629-631` | OPENING_HUNT freshness degradation | ensemble_disabled → continues with `degraded_data=True` | LOGGED/TAGGED-FALLBACK |
| 11 | `src/contracts/freshness_registry.py:97` | Collateral snapshot | 30 + 150 = 180s = 3min (REFRESH_CADENCE + JITTER_BUDGET) | FIXED-NUMBER |
| 12 | `src/contracts/freshness_registry.py:98` | Day0 executable observation | 1.0h = 3600s | FIXED-NUMBER |
| 13 | `src/contracts/freshness_registry.py:99` | Oracle artifact | 7 × 24h = 604800s (7 days) | FIXED-NUMBER |
| 14 | `src/contracts/freshness_registry.py:100` | Riskguard last check | 300s = 5 min | FIXED-NUMBER |
| 15 | `src/contracts/freshness_registry.py:101` | Heartbeat restart seed | 30s | FIXED-NUMBER |
| 16 | `src/contracts/freshness_registry.py:102` | Strategy health | 300s = 5 min | FIXED-NUMBER |
| 17 | `src/contracts/freshness_registry.py:103` | Venue clearance | 60s | FIXED-NUMBER |
| 18 | `src/contracts/freshness_registry.py:81-87` | All registry sources (three-tier ratio) | DEGRADED = 0.75× stale; EXPIRED = 2.0× stale | SENSITIVITY-DERIVED (ratio of own threshold) |
| 19 | `src/contracts/freshness_registry.py:116,120` | Heartbeat status, executable snapshot | DYNAMIC_THRESHOLD (per-call override required) | SENSITIVITY-DERIVED |
| 20 | `src/contracts/executable_market_snapshot.py:33-35` | Executable market snapshot (SELECTION freshness) | 180s (widened from 30s on 2026-06-15, #122) | FIXED-NUMBER |
| 21 | `src/contracts/executable_market_snapshot.py:272-280,398` | Snapshot freshness deadline gate | `now <= snapshot.freshness_deadline` (deadline computed from `freshness_window_seconds`) | REFUSE / FAIL-CLOSED (raises `executable_snapshot_stale`) |
| 22 | `src/engine/cycle_runtime.py:839-884` | Executable snapshot stale-on-submit | stale + live CLOB client → recapture; stale + no client → raise | LOGGED/TAGGED-FALLBACK (recapture) then REFUSE if recapture fails |
| 23 | `src/engine/cycle_runtime.py:865,878-884` | Executable snapshot recapture failure | any capture exception → raise `executable_snapshot_stale` | REFUSE / FAIL-CLOSED |
| 24 | `src/data/replacement_forecast_cycle_policy.py:65-67` | Replacement forecast source cycle max age | `2 × 12h + 6h = 30h` (default; overrideable via `ZEUS_REPLACEMENT_SOURCE_CYCLE_MAX_AGE_HOURS`) | FIXED-NUMBER (formula documented but value is fixed default) |
| 25 | `src/contracts/time_semantics.py:525-558` | Source cycle staleness horizon | `replacement_source_cycle_max_age_hours()` = 30h default; `fail-closed staleness horizon` | FIXED-NUMBER (ENV-overrideable) |
| 26 | `src/contracts/time_semantics.py:620-628` | Executable price freshness window | 180s (was 30s, widened 2026-06-09 #122; warm interval must be below this) | FIXED-NUMBER |
| 27 | `src/contracts/time_semantics.py:724-776` | Anomaly pause TTL | 24h default (`DEFAULT_PAUSE_TTL_HOURS`); acknowledged as "a guess" in comments | FIXED-NUMBER |
| 28 | `src/contracts/time_semantics.py:768-776` | Day0 oracle DB-miss TTL | 10s (`_DB_MISS_TTL_S`) | FIXED-NUMBER |
| 29 | `src/contracts/time_semantics.py:470-491` | Readiness TTL | `replacement_source_cycle_max_age_hours()` — same as source cycle max age; derived to break twin-clock | SENSITIVITY-DERIVED (derived from source cycle) |
| 30 | `src/data/source_time.py:68-69` | Source release calendar freshness ladder | DEGRADED at 0.8× `max_source_lag_seconds`; EXPIRED at 1.0× | SENSITIVITY-DERIVED (ratio of calendar-owned per-source field) |
| 31 | `src/data/source_time.py:65-69` | ECMWF ladder | `max_source_lag_seconds=108000s=30h` → DEGRADED at 24h, EXPIRED at 30h | SENSITIVITY-DERIVED |
| 32 | `src/data/source_time.py:65` | OpenMeteo ladder | `max_source_lag_seconds=172800s=48h` → DEGRADED at ~38.4h, EXPIRED at 48h | SENSITIVITY-DERIVED |
| 33 | `src/data/source_time.py:65` | TIGGE ladder | `max_source_lag_seconds=604800s=7d` → DEGRADED at ~5.6d, EXPIRED at 7d | SENSITIVITY-DERIVED |
| 34 | `src/engine/evaluator.py:1214-1218` | Day0 executable observation gate | `1.0h` via `FreshnessRegistry("day0_executable_observation")` | REFUSE / FAIL-CLOSED (rejects candidate with SIGNAL_QUALITY) |
| 35 | `src/engine/evaluator.py:1015,1152` | Oracle calibration staleness check | 90 days = `staleness_days × 86400` | FIXED-NUMBER |
| 36 | `src/engine/evaluator.py:4443-4459` | Calibration authority gate | UNVERIFIED rows present for bucket → `rejection_stage="AUTHORITY_GATE"` | REFUSE / FAIL-CLOSED |
| 37 | `src/engine/evaluator.py:4675,4699-4707` | Calibration maturity gate | no viable Platt model → `rejection_stage="CALIBRATION_IMMATURE"` | REFUSE / FAIL-CLOSED |
| 38 | `src/engine/evaluator.py:3848-3859` | Degraded forecast source gate | `degradation_level=="DEGRADED_FORECAST_FALLBACK"` + entry_primary required → REJECT at SIGNAL_QUALITY | REFUSE / FAIL-CLOSED |
| 39 | `src/engine/evaluator.py:1937-1943` | Forecast evidence causality gate | `issue_time > decision_time` or `available_at > decision_time` → error list | LOGGED/TAGGED-FALLBACK (errors collected but not always hard-refuse) |
| 40 | `src/engine/evaluator.py:1164-1172,3742-3840` | Source run completeness / coverage | `WINDOW_INCOMPLETE` → fail-closed rejection | REFUSE / FAIL-CLOSED |
| 41 | `src/engine/monitor_refresh.py:346-359` | Monitor belief staleness tracker | consecutive stale cycles → `BELIEF_AUTHORITY_FAULT` alert | LOGGED/TAGGED-FALLBACK (logs; does not block exit) |
| 42 | `src/engine/monitor_refresh.py:1230-1234` | Monitor calibration authority gate | UNVERIFIED calibration rows → "using stale probability", `authority_gate_blocked` | LOGGED/TAGGED-FALLBACK (uses stale probability, marks not-fresh) |
| 43 | `src/engine/monitor_refresh.py:1238-1243` | Monitor hours_since_open = NaN gate | malformed entered_at → `hours_since_open` is NaN → REFUSE | REFUSE / FAIL-CLOSED |
| 44 | `src/engine/monitor_refresh.py:2270-2290` | Monitor stale replacement belief | stale/missing replacement posterior → `last_monitor_prob_is_fresh=False`; exit organ treats as unavailable | REFUSE / FAIL-CLOSED (for exit decisions) |
| 45 | `src/engine/monitor_refresh.py:2298-2345` | Day0 unsupported / obs-unavailable fallback | `day0_observation_unavailable_forecast_fallback` flag set; emits validation tag | LOGGED/TAGGED-FALLBACK |
| 46 | `src/engine/monitor_refresh.py:629` | Monitor support topology stale | `market_scan_authority` stale → `raise ValueError("support topology stale")` | REFUSE / FAIL-CLOSED |
| 47 | `src/engine/monitor_refresh.py:1160` | Monitor invalid corrected p_raw | → keep stale probability (never trade on garbage) | LOGGED/TAGGED-FALLBACK (keeps stale, does not trade) |
| 48 | `src/strategy/oracle_estimator.py:163-164` | Oracle artifact freshness gate | `age_hours * 3600 >= STALE threshold (7d via registry)` → `OracleStatus.STALE` | REFUSE / FAIL-CLOSED (STALE oracle → city excluded) |
| 49 | `src/strategy/oracle_penalty.py:364` | Oracle evidence age gate | `effective_age_hours > 7d threshold` → blocks oracle record | FIXED-NUMBER (7d) |
| 50 | `src/forecast/debias_authority.py:91,374-383` | Debias artifact freshness | `FRESHNESS_DAYS=3` days from case issue time | FIXED-NUMBER (3 days) |
| 51 | `src/forecast/debias_authority.py:166-171,380` | STALE_REFUSED / MAGNITUDE_REFUSED / OOS_HARM_REFUSED | stale/magnitude/harmful debias artifact → REFUSED; `no_debias` fallback used | REFUSE / FAIL-CLOSED (artifact) + LOGGED/TAGGED-FALLBACK (falls back to no_debias) |
| 52 | `src/forecast/sigma_authority.py:467-524` | Sigma authority soft-anchor fallback | no per-cell realized floor → conservative fallback sigma | LOGGED/TAGGED-FALLBACK (tagged with `basis="soft_anchor_conservative_fallback"`) |
| 53 | `src/forecast/center.py:362-371` | Forecast center REFUSED | no ensemble members → `center_status="REFUSED"`, no μ invented | REFUSE / FAIL-CLOSED |
| 54 | `src/data/replacement_forecast_bundle_reader.py:543-558` | Readiness expires_at gate | `readiness.expires_at <= decision_utc` → staleness brand (`staleness_violations` list), WARNING logged, continues serving freshest row | LOGGED/TAGGED-FALLBACK (brands but does not block) |
| 55 | `src/data/replacement_forecast_bundle_reader.py:631,725-730` | Bundle staleness violations (general) | stamped in `staleness_violations` list; logged, served with brand | LOGGED/TAGGED-FALLBACK |
| 56 | `src/data/bayes_precision_fusion_capture.py:52-76` | BPF `_available_after_decision` guard | FAIL-OPEN: missing/malformed `available_at` → admits | SILENT-FALLBACK (admits when availability evidence missing) |
| 57 | `src/data/executable_forecast_reader.py:473-476,999-1003` | `expires_at` hard filter in reader | `expires_at <= now_utc` → row dropped | REFUSE / FAIL-CLOSED (drops expired rows) |
| 58 | `src/data/executable_forecast_reader.py:1329-1383` | Fallback election in reader | fallback ONLY when enumeration yields no passing candidates | LOGGED/TAGGED-FALLBACK |
| 59 | `src/data/day0_fast_obs.py:560,843-896` | WU stale-cache-after-failure gate | stale_cache_after_failure beyond `staleness_budget_minutes × 60s` → entry blocked; kills are staleness-safe | REFUSE / FAIL-CLOSED (entry); LOGGED/TAGGED-FALLBACK (kills allowed) |
| 60 | `src/data/day0_fast_obs.py:526` | FETCH_STALE_AFTER_FAILURE status | explicit status tag on stale cache hits | LOGGED/TAGGED-FALLBACK |
| 61 | `src/signal/day0_obs_latency.py:40` | Day0 fast obs staleness budget | `DEFAULT_STALENESS_BUDGET_MIN=100.0` min (60min cadence + 40min delay) | FIXED-NUMBER |
| 62 | `src/data/observation_client.py:281,309` | WU stale age / METAR fallback trigger | WU result stale (>1h) or absent → Option-B METAR fast-lane fallback fires | LOGGED/TAGGED-FALLBACK (METAR source tag applied) |
| 63 | `src/calibration/manager.py:920-1064` | Calibration hierarchical fallback | v2 bucket miss → TIGGE rescue → season-only cluster fallback pool | LOGGED/TAGGED-FALLBACK |
| 64 | `src/calibration/manager.py:941,1076` | Stale Platt model skip | `Ignoring stale raw-probability Platt model` → skipped in fallback | LOGGED/TAGGED-FALLBACK |
| 65 | `src/calibration/store.py:385,956` | UNVERIFIED / QUARANTINED calibration rows | `authority='VERIFIED'` required; UNVERIFIED / QUARANTINED filtered out | REFUSE / FAIL-CLOSED |
| 66 | `src/observability/calibration_coverage_guard.py:131,334` | Calibration season-only silent borrow | `SILENT_FALLBACK` label; armed mode raises `CoverageGap` | SILENT-FALLBACK (in non-armed mode); REFUSE in armed mode |
| 67 | `src/runtime/bankroll_provider.py:45-46,581` | Bankroll cache TTL | max_age=30s; fail_closed after 300s of fetch failure → returns None | FIXED-NUMBER (30s TTL, 300s fail_closed) |
| 68 | `src/runtime/bankroll_provider.py:577-590` | Bankroll stale-but-tolerable | live fetch failed + cached_age ≤ 300s → return stale with `staleness_seconds` flag | LOGGED/TAGGED-FALLBACK |
| 69 | `src/runtime/bankroll_provider.py:581-584` | Bankroll fail-closed | cached_age > 300s AND live fetch failed → returns None | REFUSE / FAIL-CLOSED |
| 70 | `src/control/ws_gap_guard.py:32,53-64` | WebSocket message staleness | `DEFAULT_STALE_AFTER_SECONDS=30s` → stale gap blocks new entries | FIXED-NUMBER |
| 71 | `src/contracts/entry_quote_evidence.py:52,162` | Entry quote freshness | `DEFAULT_STALE_THRESHOLD_MS=2500ms` → STALE reliability marker + linear penalty up to 10s | FIXED-NUMBER |
| 72 | `src/events/continuous_redecision.py:66,372` | Stale-quote cancel | resting order quote older than threshold (ms) → cancel + re-quote | FIXED-NUMBER (threshold passed by caller) |
| 73 | `src/ingest/polymarket_user_channel.py:41` | User channel message staleness | `DEFAULT_STALE_AFTER_SECONDS=30s` | FIXED-NUMBER |
| 74 | `src/data/ensemble_client.py:35` | Ensemble client in-process cache | `CACHE_TTL_SECONDS=900s=15min` | FIXED-NUMBER |
| 75 | `src/data/ensemble_client.py:193` | Ensemble client cache hit logic | `age_seconds <= CACHE_TTL_SECONDS AND cached_days >= forecast_days` → serve cache | FIXED-NUMBER |
| 76 | `src/runtime/posture.py:40` | Posture config YAML cache | `_CACHE_TTL_SECONDS=60s` in-process | FIXED-NUMBER |
| 77 | `src/main.py:4626,4649-4689` | Deployment freshness gate at boot | >= 24h → SystemExit fail-closed unless `ZEUS_ACCEPT_STALE_DEPLOY=1` | FIXED-NUMBER (24h) |
| 78 | `src/main.py:2683-2695` | User channel fallback market events | `ZEUS_USER_CHANNEL_WS_MARKET_EVENTS_FALLBACK_MAX_AGE_HOURS=36h` (ENV default) | FIXED-NUMBER (ENV-overrideable) |
| 79 | `src/contracts/decision_provenance.py:425-429` | Decision provenance staleness_violations | collected from bundle reader; observability field, not a hard gate | LOGGED/TAGGED-FALLBACK |
| 80 | `src/contracts/execution_intent.py:913` | Execution intent forecast available_after_decision | `forecast_available_after_decision` error appended; validates causality | LOGGED/TAGGED-FALLBACK |
| 81 | `src/forecast/types.py` (types) | UNVERIFIED provenance tag | rows without re-validation carry `UNVERIFIED` authority | NO-GATE (tag only; callsite decides) |
| 82 | `src/contracts/reality_verifier.py:34-62` | Reality contract blocking staleness | TTL elapsed on blocking contract → `can_trade=False` | REFUSE / FAIL-CLOSED |
| 83 | `src/contracts/reality_verifier.py:79-118` | Reality contract advisory staleness | non-blocking stale contract → log only | LOGGED/TAGGED-FALLBACK |
| 84 | `src/riskguard/riskguard.py:494-583` | Riskguard trailing loss reference | staleness > 2h beyond lookback cutoff → `stale_reference` | FIXED-NUMBER (2h tolerance) |
| 85 | `src/riskguard/riskguard.py:1291-1381` | Riskguard dependency DB locked | writes `riskguard_degraded_reason=dependency_db_locked`; 5-min freshness window governs expiry | LOGGED/TAGGED-FALLBACK |
| 86 | `src/state/portfolio_loader_policy.py:40` | Portfolio partial-stale | `partial_stale` raises — must not silently hide open positions | REFUSE / FAIL-CLOSED |
| 87 | `src/data/replacement_forecast_calibration_quarantine.py` | Calibration quarantine | quarantined forecasts excluded from serving | REFUSE / FAIL-CLOSED |
| 88 | `src/contracts/expiring_assumption.py` | Expiring assumption contract | assumption beyond TTL → gate violation | REFUSE / FAIL-CLOSED |

---

## Question 1: SENSITIVITY-DERIVED vs FIXED-NUMBER gates

### Gates already SENSITIVITY-DERIVED (good examples — generalize these)

| Gate | How it's derived |
|------|-----------------|
| `source_time.py` freshness ladder (rows 30-33) | `_DEGRADED_RATIO=0.8` and `_EXPIRED_RATIO=1.0` applied to calendar-owned `max_source_lag_seconds` — fully derived |
| FreshnessRegistry three-tier ratios (row 18) | DEGRADED=0.75×, EXPIRED=2.0× of the STALE threshold — derived from one number |
| Readiness TTL (row 29) | Derived from `replacement_source_cycle_max_age_hours()`, not a second clock |
| Heartbeat / executable_snapshot (row 19) | DYNAMIC_THRESHOLD — caller must supply per-call, forcing context-sensitivity |
| Executable snapshot freshness_deadline (row 21) | Derived from `freshness_window_seconds` baked into each snapshot object |
| Replacement forecast max age formula (row 24) | `2 × LIVE_CYCLE_REFRESH_INTERVAL_HOURS + MEASURED_P50_PUBLICATION_LAG_HOURS` (formula documented; value derived) |

### FIXED-NUMBER candidates the operator wants made sensitivity-derived

These are hardcoded absolute windows with no derivation from horizon / settlement proximity / volatility:

| Row | Gate | Current fixed value | Sensitivity axis it should track |
|-----|------|---------------------|----------------------------------|
| 1-3 | Observation freshness budgets (open_meteo_archive, wu_pws, hko, ogimet, noaa, ecmwf_open_data, tigge_mars) | 6h / 36h / 24h | Should track `max_source_lag_seconds` from source release calendar (same as rows 30-33 already do) |
| 12 | Day0 executable observation max age | 1.0h | Should track settlement proximity — imminent markets tighten this |
| 20 | Snapshot SELECTION freshness window | 180s | Already referenced in `time_semantics.py` as a function of warm cycle; could derive from expected capture latency |
| 27 | Anomaly pause TTL | 24h (admitted as "a guess") | Should derive from settlement lead (longer lead = longer pause OK) |
| 28 | Day0 oracle DB-miss TTL | 10s | Should derive from cycle interval (must be << cycle cadence) |
| 35 | Oracle calibration staleness | 90 days | Should derive from settlement coverage window / training requirement |
| 50 | Debias artifact freshness | 3 days | Should derive from settlement horizon (imminent markets need fresher debias) |
| 61 | Day0 fast obs staleness budget | 100 min | Should track per-city cadence (60 min) + measured delay (40 min) — city-parameterized, which partially exists |
| 67 | Bankroll cache TTL | 30s / 300s fail-closed | Could be derived from riskguard tick cadence |
| 70 | WS message staleness | 30s | Could relate to expected order lifecycle |
| 71 | Entry quote freshness | 2500ms / 10s window | Should track market volatility or settlement proximity |
| 77 | Deployment freshness gate | 24h | Could track expected deployment cadence |
| 84 | Riskguard reference staleness tolerance | 2h | Could derive from riskguard tick cadence |

---

## Question 2: SILENT-FALLBACK violations (use stale/degraded without log or tag)

These are the dangerous gates where degraded data enters the pipeline without a logged brand or refuse:

| # | file:line | description | why dangerous |
|---|-----------|-------------|---------------|
| A | `src/data/bayes_precision_fusion_capture.py:52-76` | `_available_after_decision` FAIL-OPEN: missing/malformed `available_at` → admits model without logging | A model whose availability evidence is absent is admitted silently; lookahead contamination undetected |
| B | `src/observability/calibration_coverage_guard.py:131,334` | Season-only calibration fallback in non-armed mode | Season-pool borrow fires silently (label `SILENT_FALLBACK` exists in source but guard only raises in armed mode) |
| C | `src/engine/cycle_runtime.py:612-613` | `evaluate_freshness_mid_run` exception → `_freshness_verdict = None` → no freshness check | If the mid-run gate itself crashes, the cycle continues without any freshness verdict |
| D | `src/main.py:5122` (acknowledged comment) | "selection silently reads stale data and falls back" | Acknowledged in source; a pre-existing architectural concern noted at that line |

---

## Question 3: NO-GATE gaps (consumption points with no freshness check)

These are consumption points where possibly-stale data is read without any explicit freshness gate:

| # | file:line | data consumed | gap description |
|---|-----------|---------------|-----------------|
| G1 | `src/engine/cycle_runtime.py:677-693` | Portfolio data with `portfolio_loader_degraded` | Degraded portfolio runs a tick, but the underlying data age is not checked — only the degraded flag is forwarded |
| G2 | `src/data/ensemble_client.py:193,261` | Ensemble member data (15-min in-process cache) | Cache hit path: `age_seconds <= 900` is checked, but no DEGRADED/EXPIRED tier is applied; it is fresh or miss, no graduated stale signal |
| G3 | `src/engine/monitor_refresh.py:2298-2345` | `day0_observation_unavailable_forecast_fallback` path | Fallback fires with flag but no freshness age is gated; the age of the fallback data is not checked |
| G4 | `src/contracts/decision_provenance.py:425-429` | `staleness_violations` field | Collected as observability; no hard gate at consumption — consumer decides whether to act |
| G5 | `src/runtime/posture.py` | Posture YAML (60s in-process cache) | Cache TTL exists but no graduated DEGRADED level; either fresh or re-reads |
| G6 | `src/calibration/manager.py:928-1064` | Calibration hierarchical fallback pool | Cross-cluster season-only fallback pool's own data age is not checked; only the bucket path determines if fallback fires |
| G7 | `src/forecast/center.py` (forecast distribution assembly) | Ensemble members used to build center | No per-member age check before assembly; member freshness is upstream gate at source ingestion only |

---

## Question 4: Correct REFUSE / FAIL-CLOSED examples (generalize these)

These are the model implementations of the fail-closed discipline:

| # | file:line | gate | why it is the right pattern |
|---|-----------|------|---------------------------|
| R1 | `src/control/freshness_gate.py:62-64 + cycle_runner.py:606-607` | DAY0_CAPTURE and IMMINENT_OPEN_CAPTURE fail-closed on stale observation | Mode-aware: the fail-closed is **sensitivity-derived** (24h horizon = no time to recover), not universal |
| R2 | `src/contracts/executable_market_snapshot.py:398 + cycle_runtime.py:865,878-884` | Executable snapshot stale gate with recapture path | Three-branch discipline: fresh → proceed; stale + client → recapture; stale + no client → refuse. No silent fallback |
| R3 | `src/data/executable_forecast_reader.py:473-476,999-1003` | `expires_at <= now_utc` → row hard-dropped | Calendar-derived expiry enforced at read; no stale row enters the serving pipeline |
| R4 | `src/engine/evaluator.py:4443-4459` | Authority gate: UNVERIFIED calibration rows → REFUSE with `rejection_stage="AUTHORITY_GATE"` | Explicit rejection stage; structured audit trail; no silent borrow |
| R5 | `src/engine/evaluator.py:4675,4699` | `CALIBRATION_IMMATURE` → no-trade, explicit stage | Fail-closed on insufficient calibration maturity; never trades with uncalibrated model |
| R6 | `src/engine/monitor_refresh.py:1238-1243,1695-1698` | `hours_since_open = NaN` → REFUSE in monitor probability | Missing/malformed `entered_at` is hard-refused rather than defaulting to 0.0 or some other heuristic |
| R7 | `src/engine/monitor_refresh.py:2270-2290` | Stale replacement posterior → `last_monitor_prob_is_fresh=False`; exit organ treats as unavailable | Never masks stale belief as fresh; the Karachi 2026-06-12 incident drove this |
| R8 | `src/forecast/debias_authority.py:166-171,374-383` | STALE_REFUSED / MAGNITUDE_REFUSED / OOS_HARM_REFUSED on debias artifacts | Six explicit refusal codes; `no_debias` fallback is the LOGGED/TAGGED path, not a silent substitute |
| R9 | `src/runtime/bankroll_provider.py:581-584` | Bankroll fail-closed after 300s fetch failure | Clear two-stage: tolerate stale up to 300s, then return None (caller must gate) |
| R10 | `src/state/portfolio_loader_policy.py:40` | `partial_stale` portfolio raises immediately | Never silently hides positions under a degraded portfolio load |
| R11 | `src/data/day0_fast_obs.py:843-887` | WU stale-after-failure: entry blocked, kills allowed | Asymmetric freshness: kills are staleness-safe; entries are not — correct differentiation |
| R12 | `src/control/freshness_gate.py:258-274` | Boot-time: 5-min retry then FATAL | Fail-closed at boot with retry grace period; does not start trading on stale data |

---

## Architecture Summary

### Freshness decision flow (stage by stage)

```
BOOT
  └── evaluate_freshness_at_boot() [5-min retry, then FATAL]
       └── per-source FRESHNESS_BUDGETS (6h/24h/36h fixed numbers)

CYCLE START
  └── evaluate_freshness_mid_run() [degrade-only, never exit]
       ├── DAY0_CAPTURE / IMMINENT_OPEN_CAPTURE stale → SKIP cycle [REFUSE]
       └── OPENING_HUNT stale → continue with degraded_data=True [TAGGED-FALLBACK]

SOURCE / FORECAST LAYER
  ├── source_time.py: calendar-derived ladder (0.8× / 1.0× max_source_lag) [SENSITIVITY-DERIVED]
  ├── replacement_forecast cycle max age: 30h default formula [FIXED-NUMBER]
  ├── expires_at in executable_forecast_reader: hard drop [REFUSE]
  └── staleness_violations in bundle reader: brand + continue [LOGGED-FALLBACK]

CALIBRATION LAYER
  ├── UNVERIFIED / QUARANTINED rows filtered [REFUSE]
  ├── CALIBRATION_IMMATURE → no-trade [REFUSE]
  ├── Hierarchical fallback (TIGGE rescue / season pool) [LOGGED-FALLBACK]
  └── Silent season-pool borrow in non-armed mode [SILENT-FALLBACK **GAP**]

EDGE / SELECTION LAYER
  ├── Day0 observation: 1h max age via FreshnessRegistry [FIXED-NUMBER / REFUSE]
  ├── Oracle artifact: 7d via FreshnessRegistry [FIXED-NUMBER / REFUSE]
  ├── Debias artifact: 3d FRESHNESS_DAYS [FIXED-NUMBER / REFUSE]
  ├── Forecast causality (available_after_decision): [LOGGED-FALLBACK]
  └── Sigma soft-anchor fallback [LOGGED-TAGGED-FALLBACK]

EXECUTION / PRICE LAYER
  ├── Executable snapshot: 180s freshness_deadline [FIXED-NUMBER]
  │    ├── Fresh → submit
  │    ├── Stale + CLOB client → recapture [LOGGED-FALLBACK]
  │    └── Stale + no client / recapture failed → RAISE [REFUSE]
  ├── Collateral snapshot: 180s [FIXED-NUMBER / REFUSE]
  ├── Entry quote: 2500ms stale threshold [FIXED-NUMBER]
  └── WS channel: 30s stale-after [FIXED-NUMBER]

MONITOR / EXIT LAYER
  ├── Replacement posterior stale → prob_is_fresh=False; exit unavailable [REFUSE]
  ├── Belief stale cycle counter → BELIEF_AUTHORITY_FAULT alert [LOGGED-TAGGED]
  ├── hours_since_open=NaN → REFUSE [REFUSE]
  ├── Support topology stale → raise [REFUSE]
  └── Day0 obs-unavailable → fallback with tag [LOGGED-FALLBACK]

SETTLEMENT LAYER
  ├── Settlement truth authority: VERIFIED required [REFUSE]
  └── Riskguard trailing reference: 2h tolerance [FIXED-NUMBER]
```

---

## Key Findings for Operator

1. **The freshness infrastructure is partially modernized but two systems coexist:** `source_time.py` (sensitivity-derived, calendar-sourced ratios) is the right model and applies to raw data sources. `freshness_gate.py` FRESHNESS_BUDGETS (observation families) are still hardcoded fixed numbers that do not derive from the same calendar — these are the most actionable candidates for harmonization.

2. **The executable snapshot gate (180s) is correctly wired** as a fixed number tracked in `time_semantics.py` with CI-enforced relations to the warm cycle interval; it is not yet sensitivity-derived from settlement proximity but is well-governed.

3. **Anomaly pause TTL (24h) is acknowledged in source as "a guess"** — explicit candidate to make sensitivity-derived from settlement horizon.

4. **The BPF `_available_after_decision` fail-open is a structural gap** — it admits models with missing availability metadata silently, which could allow lookahead contamination in backtest/warm paths to survive to live without detection.

5. **The calibration season-pool silent borrow** is only visible in armed mode; non-armed production could silently borrow cross-cluster calibration without a trace.

6. **Mid-run freshness gate crash → no verdict** (cycle_runner.py:612-613) means a single exception in `evaluate_freshness_mid_run` bypasses the entire freshness discipline for that cycle.

---

_Audit generated from read-only grep scan of `/Users/leofitz/zeus/.claude/worktrees/timing-fixes/src`. No code was modified._
