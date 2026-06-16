# Deep-dive verdicts — operator's 4 priority timing questions (2026-06-15)

```
Created: 2026-06-15
Authority basis: operator-named priority questions; evidence-mandated (real recorded timestamps).
Full result: tasks/w2u7i8bkh.output (4 structured verdicts).
```

## Q1 — fusion multi-source simultaneity → PARTIAL
- **Fetch IS coherent:** at 18Z all 10 providers share `source_cycle_time` (spread 0h), ONE `captured_at`, one fetch pass. So "同时获取" = **yes, no skew.** (My earlier "6h spread" was the soft-anchor *baseline_b0* de-bias role, not the fusion providers.)
- **Defects:** (P2) T2 weights come from residual variance only — **no freshness term**; a not-yet-disseminated provider (ECMWF real lag 8.42h > 7.55h fetch offset) is fused at equal weight. (P2) `source_available_at == captured_at` 5000/5000 → true dissemination time never recorded → contemporaneity structurally unverifiable. (P3) no cross-provider contemporaneity assertion.
- Loc: `src/forecast/bayes_precision_fusion.py:130,277`; `src/data/bayes_precision_fusion_download.py:889,894`.

## Q2 — appear→Day0→exit countdown/transitions → ALIGNED
- Day0 boundary = **station-local midnight** (`market_phase.py:99-126 settlement_day_entry_utc` via `ZoneInfo(city_timezone)`), uses frozen `decision_time_utc` not live `now()`. Verified: Tokyo/KL/Amsterdam/Chengdu day0_capture all fire +3.3h..+9.5h AFTER local midnight, **zero pre-midnight cases**, across 49/53 non-Central cities (92%). My ~90%-blast-radius fear is **refuted.**
- Defects: (P2) `shoulder_strategy_vnext.py:118` `date.today()` host-Chicago fallback for target_date — only when DB target_date absent, mis-dates Asian markets in the 5-9h pre-UTC-midnight window. (P3) `market_topology_state.gamma_captured_at/expires_at` all NULL, stale since 05-28.

## Q3 — per-provider release delay → PARTIAL
- **ECMWF (only LIVE-authorized provider):** configured gate 485 min, measured min 497 min / avg 650 min → **gate accurate, never early. Correct.**
- **19 other providers (ICON/GFS/UKMO/NCEP/JMA/MeteoFrance/gem…) = SHADOW_ONLY, no calendar release-lag config** — probe-based detection only. Measured lags span 6-13h, no per-provider basis. `openmeteo_previous_runs` 720min = RECONSTRUCTED guess. 06Z/18Z 285min profile parsed but never applied (dead).
- Defect (P2): release framework covers **1 of 20** providers; no live-money risk today (only ECMWF live) but any shadow→live promotion exposes unconfigured timing.
- Loc: `config/source_release_calendar.yaml`; `src/data/source_time.py:218-224`.

## Q4 — Day0 fast-lane keeps up minute-by-minute → MISALIGNED
- **The Day0 metric/decision lane is DEAD — a new silent cascade:** `day0_metric_fact`=0 rows, `day0_nowcast_runs`=0 rows everywhere. Cause: nowcast writer (`day0_nowcast_store.py:249`) is hard-gated at `monitor_refresh.py:1827-1833` on `read_latest_platt_fit()`; **`day0_horizon_platt_fits`=0 rows → fit None on every call → every write returns early.** (Same disease as decision_events: empty upstream dependency silently disables a whole decision surface.) Plus `monitor_refresh.py:1814 if hours_remaining>6: return`.
- **No minute data exists:** obs grid is strictly HOURLY (60-min gaps, 0 sub-hourly). The live `day0_hourly_vectors` lane recomputes every ~20-35 min (over-samples the hourly obs, can't miss a reading) — that part is fine.
- **Ingest lag:** new hourly obs reaches stored obs avg 56.3 min (p95 129.8, max 5775) after event time.
- **WP-18 confirmed:** fast-lane entry-staleness gate (`day0_fast_obs.py:723,845`) uses `time.monotonic()` cache-age (time since WE fetched) NOT obs **event-time** age → a stale-upstream-but-recently-refetched report reads fresh; the gate can't detect the obs hasn't advanced. **Wrong clock.**
- Net: minute-by-minute decision tracking is structurally absent — the decision surface writes nothing, and the freshness gate is on the wrong clock.

## Cross-question reality (doc-vs-live)
The documented "T2 precision fusion over 5 decorrelated providers" is, live, **ECMWF-authorized + aifs/openmeteo soft-anchor**; ICON/GFS/UKMO/JMA/etc are SHADOW_ONLY (Q3). The "5 decorrelated providers" is aspirational/shadow, not the live trading forecast.

## Severity tally
Q4 MISALIGNED (dead decision lane + wrong-clock gate) is the worst; Q1 PARTIAL (no freshness weight); Q3 PARTIAL (1/20 providers configured); Q2 ALIGNED.
