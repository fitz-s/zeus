# P0: Forecast Extrema Authority + Day0 Physical Distribution (2026-05-22)

Anti-compaction record of operator's two analyses + reconciliation. Authority for the PR series.

## RECONCILIATION (verified 2026-05-22, live zeus-forecasts.db)
Operator's first analysis → "forecast authority selection picks non-contributing run". CONFIRMED + operative.
Operator's second analysis → emphasized Root A (ECMWF period-valid-time / local-day extraction broken). **DISPROVEN by data**: contributing snapshots carry CORRECT warm peaks:
- Taipei 2026-05-22: all contrib=1 cycles max 32.8–34.1 (obs 34); only 12Z contrib=0 = 27.6 cold.
- Guangzhou: contrib=1 cycles 31–33 (obs 33); 12Z=27.7.
- Seoul: contrib=1 05-22T00=26.0 (obs 26); 12Z=19.7.
=> The external extractor's local-day/period logic WORKS (correct contribution flag + warm maxes). Do NOT rewrite/vendor the extractor as a P0. The bug is purely SELECTION.

## ROOT (operative)
`src/data/executable_forecast_reader.py` selects snapshots `ORDER BY source_cycle_time DESC, available_at DESC, snapshot_id DESC` (latest-first, ~line 433 + world-schema variant ~438). For far-east cities the latest (12Z) run is post-peak → `contributes_to_target_extrema=0`, attribution UNKNOWN — yet selected. The peak-capturing 00Z run (contrib=1, warm) exists in DB (10–12 contributing snapshots/city/date) but is not preferred. NO re-extraction needed.

## Physical law (HIGH)
H_D = settle(max_{t in local day} T(t)); at decision τ: H_j = settle(max(H_obs_so_far, max_{t>τ} T_j(t))). Observation = lower bound only; current_temp must NEVER lower future max.

## PR SERIES (corrected)
- **PR-A (P0, primary):** reader prefers contributes=1 + attribution OK + not boundary-ambiguous, then latest within. Block live (typed reasons) when only NON_CONTRIBUTOR/UNKNOWN. New src/data/forecast_extrema_authority.py classifier. Operates on existing DB data. Reason codes: EXECUTABLE_FORECAST_NON_CONTRIBUTING_EXTREMA / _EXTREMA_AUTHORITY_UNKNOWN / _BOUNDARY_AMBIGUOUS / _PEAK_MISSED_BY_LATEST_RUN.
- **PR-C (P1, Root C):** observation_instants_v2.running_max = hourly bucket max (misnamed, non-monotonic). high_so_far MUST = MAX(running_max) over local-day rows WHERE utc_timestamp<=decision AND authority IN (VERIFIED,ICAO_STATION_NATIVE). New day0_observation_reader.py. View observation_hourly_extrema_v2 aliasing hour_bucket_max/min. Lives in zeus-world.db.
- **PR-B (P1, Root D):** Day0HighNowcastSignal — DELETE current_temp linear blend (`anchored=max(ens_remaining,current_temp); blended=w*anchored+(1-w)*current_temp`). HIGH = settle(max(obs_floor, future_member_max [+ optional calibrated residual on future]). current_temp → diagnostic/freshness/uncertainty only. Day0Signal.day0_blended_highs already ~correct (np.maximum). New src/signal/day0_high_distribution.py.
- **PR-D (defense):** src/signal/probability_sanity.py validate_high_distribution — P_RAW/P_CAL categorical (sum=1), POINT_BUCKET_HIGH_PROB_WITHOUT_MEMBER_SUPPORT (point bin mode>0.5 & support<0.25), EXTREME_MARKET_DISAGREEMENT (px<0.05 & p_cal>0.35). Wire before Kelly in evaluator.
- **Root B (verify-then-maybe):** Day0 remaining-window interval semantics (remaining_member_extrema_for_day0 treats times as instant). VERIFY whether day0 live consumes period-max snapshots for the remaining window; if it uses whole-day period_extrema_members it can't do "remaining hours" correctly. Only implement period-aware remaining if day0 actually consumes period data. (Extraction itself is sound per reconciliation.)
- **PR-E (cleanup):** quarantine opportunity_fact/decision_events/etc rows whose forecast_snapshot was contributes=0/UNKNOWN; revoke day0 calibration fits; day0 stays shadow until corrected replay.

## SAFETY (Phase 0)
day0_nowcast_entry stays shadow / metric_support.high=shadow / REPLAY_PASS until PR-A..D + replay done. min_entry_price stays 0.05 (do NOT lower). IOC phase-gate fix (branch ioc-phase-gate-correct-fix, commit 57a6806219) is separate + live.

## DoD
Taipei/Seoul/Busan/Guangzhou/Shenzhen post-peak 12Z non-contributing snapshot not selected; Amsterdam (latest contributes=1) keeps latest; nowcast current_temp can't pull down future high; obs high_so_far = MAX over day; p_cal categorical; low-price/high-prob blocked; affected history quarantined; day0 replay before LIVE_PILOT_TINY.
