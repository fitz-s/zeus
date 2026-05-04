# 0. Executive verdict

**PR #42 emergency-patch verdict:** `VALID_EMERGENCY_PATCH`, but only as a stopgap. It correctly identifies a real failure mode: a daemon that is offline through a once-per-day cron can restart with stale daily forecast/solar data because APScheduler coalescing does not replay every missed execution. APScheduler’s own documentation says missed executions can be coalesced into a single execution and bypassed misfires do not emit events, which supports the PR’s outage diagnosis. ([APScheduler][1])

**Important live metadata correction:** the prompt says PR #42 was open “as of latest check,” but GitHub now reports PR #42 as **closed and merged** on branch `data-ingest-boot-resilience-2026-05-02`. The diff and branch facts match the prompt, so I am treating it as the same patch. 

**Current daemon verdict:** the daemon is operationally useful, but it is not yet a live-trading readiness system. It schedules many K2 ingest jobs, source-health probes, hole scans, Open Data/TIGGE jobs, and status rollups, but the inspected code still relies on global or coarse freshness signals in multiple places. The repo has strong local-time primitives in the observation appenders and strong decision-time evidence validation in the evaluator, but those are not yet unified into a machine-readable city/date/metric readiness contract.    

**Time-semantics verdict:** `TIME_SEMANTICS_BLOCKER` for live authorization until Zeus distinguishes source issue time, source release time, source availability, fetch time, capture/import time, valid time, city-local target date, and readiness-computed time. PR #42 uses `captured_at`, `fetched_at`, and `now_utc` as if they can imply trading readiness. They cannot.

**Minimal launch-safe verdict:** keep PR #42’s boot-time “do not wait for the next cron” principle, but wrap it with locks, status-aware queries, release-calendar checks, and a per city/date/metric readiness object. Live trading should be blocked, or at minimum forced to shadow-only for affected scopes, until that readiness object is present and consumed by `cycle_runner`.

**Long-term architecture verdict:** use a hybrid launch path now: one process supervisor is acceptable, but jobs must be typed by data plane, source release calendar, idempotency key, and readiness contract. Later split into plane-separated daemons: forecast, observation, market metadata, settlement/truth, source health, backfill, and separate market-data/quote daemon.

**Biggest time/timezone risk:** confusing a UTC daemon timestamp with a city-local market settlement day. Zeus trades city-specific weather markets; target date is a **local settlement date**, not a UTC date. A fresh UTC fetch can still be stale or irrelevant for a local-day high/low market.

**Biggest false-freshness risk:** global `MAX(captured_at)` over `forecasts` and global `MAX(fetched_at)` over `data_coverage` can make one fresh row, failed row, backfill row, or irrelevant city/source/date row authorize all forecast/solar consumers.

**Biggest live-trading blocker:** no single machine-readable readiness object currently proves, for a candidate trade, that the specific city/date/metric/market topology/quote/source dependencies are fresh, complete, non-partial, and causally available before the decision.

**Final decision sentence:** PR #42 may remain merged as an emergency boot-staleness smoke guard, but it must not become the architecture; Zeus needs a release-calendar-driven, city-local, metric-aware readiness layer that gates live trading before any stale or semantically wrong data can authorize an order.

## 0.1 Hard-question rulings

|  # | Question                                                                      | Ruling                                                                                                                                                                                                    |
| -: | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  1 | Is PR #42 correct as an emergency patch?                                      | Yes, as a minimal boot-time stopgap.                                                                                                                                                                      |
|  2 | Does PR #42 risk false freshness?                                             | Yes. Global max timestamps can falsely green stale scopes.                                                                                                                                                |
|  3 | Is `MAX(captured_at)` for all forecasts valid readiness?                      | No. It is table-level recency, not readiness.                                                                                                                                                             |
|  4 | Is `MAX(fetched_at)` for all solar rows valid readiness?                      | No, especially because `data_coverage.fetched_at` is updated for `FAILED` and `MISSING`, not only `WRITTEN`.                                                                                              |
|  5 | Is 18h correct relative to source release times?                              | No as an architecture. It is an alert heuristic; real thresholds are source/run/track specific.                                                                                                           |
|  6 | Can boot force-fetch run before the forecast source has released new data?    | Yes. Without a release calendar, boot can fetch old, partial, or not-yet-complete data.                                                                                                                   |
|  7 | Does the daemon distinguish source release time from app fetch time?          | Not sufficiently in the PR. Some pipelines have richer fields, but PR #42 does not use them.                                                                                                              |
|  8 | Does it distinguish forecast issue time from `captured_at`?                   | No in PR #42. `forecasts_append` has `forecast_issue_time`, but boot guard ignores it.                                                                                                                    |
|  9 | Does it distinguish `valid_time` from target local date?                      | No in PR #42.                                                                                                                                                                                             |
| 10 | Does it distinguish observation timestamp from fetch time?                    | Some observation code does; PR #42 does not.                                                                                                                                                              |
| 11 | Does it distinguish local day from UTC day?                                   | Some appenders do; current boot freshness does not.                                                                                                                                                       |
| 12 | Does it handle city timezones and DST?                                        | Some observation/solar appenders attempt to; readiness does not consistently enforce it.                                                                                                                  |
| 13 | Does it handle high vs low metric windows separately?                         | Not in PR #42. Long-term readiness must separate high and low.                                                                                                                                            |
| 14 | Does it handle per-city/per-date/per-metric readiness?                        | No. This is the central missing layer.                                                                                                                                                                    |
| 15 | Should hourly observations also have boot-time readiness despite hourly cron? | Yes, for Day0 and local-day settlement readiness.                                                                                                                                                         |
| 16 | Should daily observations be excluded from boot staleness?                    | No, not categorically. They need city-local source-calendar readiness.                                                                                                                                    |
| 17 | Should solar data be live-trading readiness input?                            | Only when a strategy declares solar dependency. Otherwise auxiliary.                                                                                                                                      |
| 18 | Should quote/orderbook snapshots belong in this daemon?                       | No for live quote ownership. Seconds-level quote freshness belongs in a market-data/venue daemon or trading plane.                                                                                        |
| 19 | Should market metadata discovery belong in this daemon?                       | Yes as a slow market-topology plane, with topology readiness. Quote freshness remains separate.                                                                                                           |
| 20 | Should settlement reconstruction belong in this daemon or harvester?          | It should be a settlement/truth harvester plane, possibly supervised by the same process now.                                                                                                             |
| 21 | Best minimal launch-safe readiness object?                                    | A scoped object keyed by global/source/city/date/metric/market/strategy dependency with block/shadow/live decision.                                                                                       |
| 22 | What blocks live trading globally?                                            | Missing readiness table, missing source-health state, stale market topology, duplicate daemon lock, scheduler/job-health failure for required jobs, broken wallet/CLOB/auth/chain, missing city timezone. |
| 23 | What blocks only city/date/metric?                                            | Missing or stale forecast/observation/settlement data for that exact city/local date/metric/strategy dependency.                                                                                          |
| 24 | What forces shadow-only?                                                      | Fresh data with incomplete provenance, release-calendar uncertainty, partial run, degraded but strategy-nondependent source ambiguity, or topology/quote uncertainty not safe for orders.                 |
| 25 | What logs only?                                                               | Stale auxiliary source when no active strategy/market depends on it.                                                                                                                                      |
| 26 | What if source is stale but strategy does not depend on it?                   | Do not block unrelated strategies; log/degrade only that dependency.                                                                                                                                      |
| 27 | What if forecast is fresh but market topology is stale?                       | Block live entries for affected market family.                                                                                                                                                            |
| 28 | What if observation is stale but forecast-only entry is otherwise valid?      | Allow only if strategy dependency explicitly excludes observation and no Day0/settlement-capture logic is invoked.                                                                                        |
| 29 | What if settlement source is degraded near resolution?                        | Block settlement-capture entries and force settlement/truth reconstruction to degraded/manual-review.                                                                                                     |
| 30 | What parts of PR #42 survive long term?                                       | The boot-time readiness check concept and stale/fresh/empty tests. Not global max, not unlocked `daily_tick`, not 18h as readiness law.                                                                   |

# 1. Minimal PR #42 fact reconstruction

**Files changed**

1. `src/ingest_main.py`
2. `tests/test_ingest_main_boot_resilience.py`
3. `.claude/hooks/pre-commit-invariant-test.sh`, which is hook/sentinel support and not core daemon architecture.

**Code behavior reconstructed**

PR #42 adds `_BOOT_FRESHNESS_THRESHOLD_HOURS = 18` and extends `_k2_startup_catch_up()` with a second phase after existing catch-up. Phase 1 still runs `catch_up_missing(conn, days_back=30)` for `daily_obs`, `hourly_instants`, `solar_daily`, and `forecasts`. Phase 2 computes `now_utc = datetime.now(timezone.utc)`, queries `SELECT MAX(captured_at) FROM forecasts`, parses it, and if the table is empty or stale by more than 18 hours, calls `src.data.forecasts_append.daily_tick(conn)`. It then queries `SELECT MAX(fetched_at) FROM data_coverage WHERE data_table = 'solar_daily'`, and if empty/stale by more than 18 hours, calls `src.data.solar_append.daily_tick(conn)`. 

**Cron assumptions**

The patch comments assume:

| Job                    | Claimed cron |
| ---------------------- | -----------: |
| `forecasts_daily_tick` |    07:30 UTC |
| `solar_daily_tick`     |    00:30 UTC |
| `daily_obs_tick`       |       hourly |
| `hourly_instants_tick` |       hourly |

The PR’s own rationale says the outage occurred because the daemon was offline overnight, missed the 07:30 UTC forecast cron, and APScheduler `coalesce=True` did not replay missed daily work on boot. 

**Tables/columns used**

| Plane              | Query                                                                        | Column used as freshness   |
| ------------------ | ---------------------------------------------------------------------------- | -------------------------- |
| Forecasts          | `SELECT MAX(captured_at) FROM forecasts`                                     | `captured_at`              |
| Solar              | `SELECT MAX(fetched_at) FROM data_coverage WHERE data_table = 'solar_daily'` | `data_coverage.fetched_at` |
| Daily observations | Excluded from new guard                                                      | N/A                        |
| Hourly instants    | Excluded from new guard                                                      | N/A                        |

**Tests**

The new test file creates an in-memory DB, initializes schema, patches `daily_tick` and `catch_up_missing`, calls `_k2_startup_catch_up.__wrapped__()`, and checks six cases: forecast stale/fresh/empty and solar stale/fresh/empty. The PR test plan reports the six new tests passed, while 91 pre-existing main-branch failures remained unchanged. 

**Review notes already attached to PR**

Review comments identify two severe structural risks: the staleness probe runs **after** Phase 1 catch-up, so catch-up can make timestamps fresh before the guard checks them; and boot-forced `daily_tick` bypasses the normal per-table advisory lock, allowing concurrent daily ticks near scheduled cron or dual-process startup. Another comment notes solar uses `data_coverage.fetched_at` regardless of status, so a recent `FAILED` or `MISSING` row can look fresh. 

**Classification**

| Label                   | Verdict                   |
| ----------------------- | ------------------------- |
| `VALID_EMERGENCY_PATCH` | Yes                       |
| `PARTIAL_FIX`           | Yes                       |
| `TIME_SEMANTICS_RISK`   | Yes                       |
| `GLOBAL_MAX_RISK`       | Yes                       |
| `LOCKING_RISK`          | Yes                       |
| `READINESS_GAP`         | Yes                       |
| `TEST_COVERAGE_GAP`     | Yes                       |
| `REVIEW_REQUIRED`       | Yes, despite being merged |

# 2. PR #42 time-semantics tribunal

| Timestamp / field                | Current PR use                  | Real meaning                                                                                              | Timezone status                                                                 | Risk                                                                            | Required fix / verification                                                      |
| -------------------------------- | ------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `now_utc`                        | Difference anchor for staleness | Daemon process wall-clock at boot                                                                         | Aware UTC                                                                       | Safe only for duration math against aware UTC timestamps                        | Keep, but never treat as source release/readiness time                           |
| `forecasts.captured_at`          | Global forecast freshness       | In `forecasts_append`, effectively app retrieval/capture/import time, not source issue/release/valid time | ISO string; parse behavior depends on stored value                              | Can be fresh from backfill or one city/source/date while target market is stale | Scope by source, city, target local date, metric, lead, status, and source run   |
| `forecasts.forecast_issue_time`  | Ignored by PR                   | Forecast run/issue identity, sometimes derived because source may not expose true issue time              | Should be UTC-aware                                                             | PR can authorize data without proving issue cycle                               | Include in readiness and source-run tables                                       |
| `source_release_time`            | Absent                          | Official or expected upstream release/availability time                                                   | Source-calendar UTC or source-local converted to UTC                            | Boot can fetch before release and mark old/partial data fresh                   | Add release-calendar registry and `safe_fetch_not_before`                        |
| `available_at`                   | Absent in PR                    | Time data was causally available to Zeus/source, separate from fetch                                      | Must be UTC-aware                                                               | Decision can use data that was not available before decision if not proven      | Persist and validate against decision time                                       |
| `valid_time`                     | Absent in PR                    | Forecast-valid instant/window                                                                             | Often UTC; local-day mapping required                                           | Can conflate forecast horizon with settlement date                              | Persist valid start/end and map to city local date                               |
| `target_date`                    | Only incidental in rows/tests   | Market settlement local date                                                                              | Local date, city IANA timezone                                                  | Global freshness ignores target date entirely                                   | Readiness key must include `target_local_date`                                   |
| `solar data_coverage.fetched_at` | Global solar freshness          | Ledger update time; updated for `WRITTEN`, `FAILED`, and `MISSING`                                        | `_now_utc_iso()` emits aware UTC ISO                                            | Recent failure can hide stale/empty solar data                                  | Filter `status='WRITTEN'`; use actual `solar_daily.fetched_at`; key by city/date |
| Cron trigger time                | Assumed UTC                     | Scheduler scheduled-fire time, not data availability                                                      | Comments say UTC; inspected `BlockingScheduler()` does not visibly set timezone | If host timezone differs, cron fires at wrong wall time                         | Explicit scheduler timezone UTC and tests                                        |
| APScheduler missed time          | Implicit motivation             | Scheduled run missed during downtime                                                                      | Scheduler-dependent                                                             | Coalescing can bypass missed runs                                               | Persist `job_run.scheduled_for` and missed-run recovery                          |
| `daily_obs.fetched_at`           | Excluded                        | Provider fetch time for daily obs, not observation time                                                   | Varies by writer                                                                | Exclusion ignores settlement readiness                                          | Add local-day readiness                                                          |
| `hourly_instants.utc_timestamp`  | Excluded                        | Observation instant timestamp                                                                             | UTC plus local timestamp present                                                | Hourly cron does not imply current local-day completeness                       | Add Day0/hourly readiness                                                        |
| City IANA timezone               | Absent in PR                    | Required local-day geometry                                                                               | Present in config/appenders, not PR                                             | UTC day can be mistaken for settlement day                                      | Enforce in readiness and tests                                                   |
| DST flags                        | Absent in PR                    | Local-day duration/ambiguous-hour metadata                                                                | Some appenders store flags                                                      | DST day can have 23/25 hours                                                    | Readiness must verify expected local-hour count                                  |
| High/low metric                  | Absent in PR                    | Different physical/statistical families                                                                   | Metric strings elsewhere                                                        | High and low can be independently stale                                         | Readiness key includes `metric in {high, low}`                                   |
| `readiness_computed_at`          | Absent                          | Time decision was computed from dependencies                                                              | Must be UTC-aware                                                               | Logs are not readiness                                                          | Add durable readiness table/object                                               |

# 3. Current daemon reconstruction

## Entry points and daemon split

The repo’s top-level authority file describes Zeus as a live quantitative trading engine, with the money path running from contract semantics and source truth through forecast signal, calibration, edge, execution, monitoring, settlement, and learning. It explicitly lists `src/main.py`, `src/engine/cycle_runner.py`, `src/engine/evaluator.py`, executor, monitor, and harvester as runtime entry points. 

`src/main.py` states that K2 ingest jobs were removed from the trading daemon and that `src/ingest_main.py` owns K2 ticks, recalibration, ECMWF Open Data, analysis automation, hole scanner, startup catch-up, source-health probe, drift detector, ingest-status rollup, and harvester truth writer. The trading daemon owns market discovery, PnL resolver, venue heartbeat, wallet gate, freshness-gate consumer, and schema validator. 

## Job graph and schedule

From inspected `ingest_main.py` and PR #42 evidence, the ingest scheduler includes:

| Job                        |              Approx schedule | Role                        |
| -------------------------- | ---------------------------: | --------------------------- |
| `_k2_daily_obs_tick`       |             hourly, minute 0 | daily observations          |
| `_k2_hourly_instants_tick` |             hourly, minute 7 | hourly observation instants |
| `_k2_solar_daily_tick`     |                  daily 00:30 | solar future/day rows       |
| `_k2_forecasts_daily_tick` |                  daily 07:30 | forecast-history lane       |
| hole scanner               |           daily around 04:00 | detect coverage holes       |
| harvester truth writer     |      hourly around minute 45 | settlement/truth            |
| ECMWF Open Data high       |                 around 07:30 | mx2t6 high track            |
| ECMWF Open Data low        |                 around 07:35 | mn2t6 low track             |
| TIGGE archive              | around 14:00, target today-2 | archive/backfill lane       |
| source-health probe        |             every 10 minutes | source reachability         |
| ingest-status rollup       |              every 5 minutes | JSON status summary         |

The key architectural issue is not merely the schedule; it is that a scheduled trigger is not the same as source availability, local-day readiness, or strategy dependency readiness.

## Scheduler timezone

The code comments and PR rationale speak in UTC, but the inspected scheduler construction uses `BlockingScheduler()` without an explicit timezone parameter in the visible evidence. APScheduler supports explicit scheduler timezone configuration, and its documentation shows `timezone=utc` / `apscheduler.timezone: UTC` as configuration options. Until the repo explicitly proves UTC scheduler configuration, this is a `TIME_SEMANTICS_BLOCKER`. ([APScheduler][1])

## Coalescing and misfire behavior

The PR’s outage explanation is credible because APScheduler documents that a scheduler restart after a missed execution creates misfires, and coalescing can roll queued executions into one while bypassed runs do not emit misfire events. ([APScheduler][1])

## Locks

Normal K2 ticks are described as acquiring per-table advisory locks via `src.data.dual_run_lock`, but PR #42’s boot-forced `daily_tick(conn)` calls were reviewed as bypassing those normal per-table locks. That creates a duplicate-write/concurrent-fetch risk near the scheduled cron or during dual-daemon startup. 

## Startup catch-up

Startup catch-up currently performs broad `catch_up_missing(conn, days_back=30)` across daily obs, hourly instants, solar, and forecasts, then performs the new PR #42 global freshness checks. The order matters: a catch-up/backfill can refresh timestamps before the staleness guard checks global max.

## Source health

`source_health_probe.py` writes `state/source_health.json` every 10 minutes with `last_success_at`, `last_failure_at`, `consecutive_failures`, `degraded_since`, latency, and error for sources including Open-Meteo Archive, WU, HKO, Ogimet, ECMWF Open Data, NOAA, and TIGGE. This is useful reachability telemetry, but it is not data readiness: a source can be reachable while the relevant city/date/metric row is missing, stale, partial, or not released. 

## Freshness gate

`freshness_gate.py` reads `state/source_health.json`, classifies sources as `FRESH`, `STALE`, or `ABSENT`, and applies coarse budgets such as 6h for Open-Meteo Archive/WU and 24h for ECMWF Open Data/TIGGE. It is a source-health freshness gate, not a city/date/metric market readiness gate. 

## Hole scanner

`hole_scanner.py` is strong infrastructure: it builds expected coverage sets for observations, observation instants, solar, and forecasts; compares against `data_coverage`; writes `MISSING` rows; and has legitimate-gap exception logic. But it is broad historical coverage reconciliation, not live-trade authorization. The scanner itself explicitly does not fetch holes and separates detection from remediation. 

## Heartbeat and ingest status

`ingest_status_writer.py` writes `state/ingest_status.json`, summarizing rows in recent periods, holes by city, quarantine reason, and source-health summary. This is valuable operational telemetry but still JSON-derived status, not a canonical readiness contract. 

## Downstream readiness

`cycle_runner.py` calls `evaluate_freshness_mid_run`. The visible behavior blocks Day0 capture when Day0 sources are disabled, but for ensemble-disabled opening hunt it tags degraded data and continues, which is unsafe unless later gates prove no live entries can be submitted on stale forecast data. 

The evaluator has stronger per-decision evidence checks: it validates forecast source fields, raw payload hash, decision time, issue time, valid time, fetch time, `available_at`, role, degradation level, and authority tier for executable entry evidence. This should be promoted into the readiness layer rather than being left as a late evaluator-only guard. 

# 4. Repo-wide data-flow and time map

| Plane                         | Producer                           | Source                                      | Timestamp fields found / implied                                                         | Timezone semantics                                                                               | Schema / state                          | Freshness logic                                                        | Provenance                                                           | Consumers                           | Failure behavior                                   |
| ----------------------------- | ---------------------------------- | ------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------- | ---------------------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------- | -------------------------------------------------- |
| Forecast-history lane         | `forecasts_append.py`              | Open-Meteo Previous Runs                    | `forecast_issue_time`, `retrieved_at`, `imported_at`, `captured_at`, `target_date`, lead | `target_date` is date; issue time derived as UTC base; API does not expose every true issue time | `forecasts`, `data_coverage`            | Daily tick and catch-up; PR uses `MAX(captured_at)`                    | Source id, raw payload hash, authority tier, availability provenance | skill/calibration/history consumers | Missing/failed coverage rows; not live readiness   |
| ECMWF Open Data high/low lane | `ecmwf_open_data.py`               | ECMWF Open Data ENS mx2t6/mn2t6             | run date/hour, captured/fetch, local-calendar-day data version                           | Run cycles UTC; target dates local-calendar                                                      | `ensemble_snapshots_v2`                 | Scheduled post-release; status values include failed/extract failed/ok | manifest hash, data version, members unit                            | evaluator/signal path               | Partial/download/extract failure statuses          |
| Ensemble API lane             | `ensemble_client.py`               | Open-Meteo Ensemble API / registered ingest | `issue_time`, `first_valid_time`, `fetch_time`, `captured_at`, optional `available_at`   | Parser treats naive as UTC                                                                       | in-memory/cache and decision evidence   | 15m cache TTL; validation checks member count/finite values            | source id, payload hash, role, authority, n members                  | evaluator                           | reject invalid ensemble; returns `None` on failure |
| Daily observations            | `daily_obs_append.py`              | WU ICAO history, HKO API, Ogimet            | observation local date/window, fetch UTC, local time, DST flags                          | WU/HKO converted to city timezone; HKO Asia/Hong_Kong                                            | `observations`, `data_coverage`         | hourly tick with city-local WU windows; HKO current/prior month        | IngestionGuard atoms, metadata                                       | settlement, calibration, Day0       | failed/gap rows in coverage                        |
| Hourly instants               | `hourly_instants_append.py`        | Open-Meteo Archive                          | `local_timestamp`, `utc_timestamp`, `local_date`, offset, DST flags, imported_at         | requests `timezone=city.timezone`; maps local to UTC                                             | `observation_instants`, `data_coverage` | hourly tick pulls recent completed local days                          | raw response, source, hourly rows                                    | Day0/diurnal/settlement support     | WRITTEN if expected local-hour count met           |
| Solar                         | `solar_append.py`                  | Open-Meteo Archive solar                    | `target_date`, sunrise/sunset local/UTC, `fetched_at`                                    | requests city timezone; current code uses `now_utc.date()` for window start                      | `solar_daily`, `data_coverage`          | daily + PR boot staleness                                              | deterministic lat/lon/timezone/date                                  | auxiliary features                  | currently global freshness in PR is unsafe         |
| Source health                 | `source_health_probe.py`           | all upstream probes                         | `last_success_at`, `last_failure_at`, `degraded_since`, `written_at`                     | UTC ISO                                                                                          | `source_health.json`                    | probe cadence                                                          | latency/error                                                        | freshness gate, ingest status       | source reachability only                           |
| Hole/backfill                 | `hole_scanner.py` and appenders    | physical tables + coverage ledger           | target date, fetched_at, retry_after                                                     | date-based; `date.today()` appears in several paths                                              | `data_coverage`                         | expected-minus-covered                                                 | status/reason/sub_key                                                | fillers and ops                     | not live-authorizing                               |
| Market metadata               | `market_scanner.py`                | Gamma API                                   | fetch/cached time, event dates, created/updated in payload                               | Gamma timestamps ISO UTC-like                                                                    | cache, `market_events_v2`, snapshots    | 5m cache TTL; stale/empty authority                                    | `MarketSnapshot.authority`, source-contract check                    | cycle runner/evaluator              | stale fallback can exist; live must fail closed    |
| CLOB quote/orderbook          | Polymarket client / execution path | CLOB API/WebSocket                          | orderbook timestamp, quote capture time, submitted/fill/cancel time                      | venue timestamps UTC                                                                             | order facts, venue events               | seconds-level, not data-daemon daily freshness                         | raw payload hashes, event facts                                      | executor, monitor                   | should be separate quote-readiness plane           |
| Settlement/truth              | harvester/truth writer             | Polymarket/UMA/source settlement            | market target date, resolved time, settled_at, redeem requested/confirmed                | market-specific; settlement local day needed                                                     | `settlements`, position events          | harvester cadence                                                      | authority verified/unverified/quarantined                            | learning, PnL, settlement capture   | degraded/manual review near resolution             |
| Trading readiness             | currently partial                  | source health + evaluator checks            | source health times, decision evidence                                                   | mixed                                                                                            | JSON + evaluator guards                 | coarse                                                                 | mixed                                                                | `cycle_runner`, evaluator           | no unified readiness object                        |
| Report/backtest/replay        | scripts/reports                    | canonical DB / backtest DB                  | historical times                                                                         | should be point-in-time                                                                          | backtest DB, reports                    | not live                                                               | derived only                                                         | analysis                            | must never authorize live                          |

# 5. External source release-calendar model

## 5.1 Forecast sources

| Source                               | Release cadence                                                                                                                                    | Source timezone                                          | Safe fetch model                                                                          | Partial-run behavior                                                                            | City-local implications                                                                | Readiness impact                                                                               |
| ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| ECMWF Open Data IFS/AIFS             | Four daily cycles 00/06/12/18 UTC; rolling archive last 12 runs                                                                                    | UTC                                                      | Fetch only after the needed cycle, step range, parameter, and member set are available    | ECMWF dissemination is staged by forecast day/step/product; not all steps are available at once | Local high/low windows require mapping UTC forecast-valid intervals to city local date | Must key readiness by source run, metric, step/window, city, local date                        |
| ECMWF Open Data ENS derived high/low | ENS 00Z day0 around 06:40 UTC; later days/derived products later; derived step 0–240 around 07:41 and 246–360 around 08:01 for 00Z in the schedule | UTC                                                      | 07:30 is not universally safe for every derived product/window; use manifest completeness | A fetch before full derived availability can be partial                                         | Local high/low may need steps beyond a naive day0 window                               | Block or shadow if expected members/steps missing ([ECMWF Confluence][2])                      |
| ECMWF mx2t6/mn2t6                    | Parameters are “maximum/minimum temperature at 2 metres in the last 6 hours”                                                                       | UTC-valid periods                                        | Interpret as period aggregates ending at forecast step, not instantaneous temps           | Conflating step with instant corrupts local-day extrema                                         | Need period-window mapping, not point sample mapping                                   | Separate high/low readiness; verify accumulation semantics ([ECMWF][3])                        |
| Open-Meteo Previous Runs             | Model update frequency varies by model, from hourly to six-hourly                                                                                  | API-dependent; requested timezone affects returned times | Use Open-Meteo model update docs per model, not one global 07:30 assumption               | API may expose previous runs but not true source issue time for every model                     | `Day 0`, `Day 1`, etc. are lead concepts, not settlement dates                         | Readiness must store derived/assumed issue basis and flag it                                   |
| Open-Meteo Ensemble API              | Forecast/ensemble model updates vary; API returns hourly arrays                                                                                    | API timestamps; parser treats naive as UTC               | Use model-specific update/release info and member completeness                            | Missing members/non-finite hours possible                                                       | Select hours for city local target date                                                | Existing `validate_ensemble` is good but should feed readiness                                 |
| TIGGE/MARS                           | 48-hour delay after forecast initial time for standard access                                                                                      | UTC                                                      | Never same-day live trading source unless operator has real-time access                   | Archive/backfill only under normal access                                                       | Historical calibration/replay, not live entry                                          | Must be `BACKFILL_ONLY` for live unless credentialed real-time access proven ([ECMWF Apps][4]) |

ECMWF Open Data is not “one file becomes available at 07:30.” ECMWF says IFS data are released at the end of the real-time dissemination schedule, and its schedule shows staged availability across cycles and products. The open data page also states the rolling archive retains the most recent 12 forecast runs from the four daily cycles. ([ECMWF][3])

## 5.2 Observation sources

| Source                                            | Release/update behavior                                                                                                                                                                                                      | Time semantics                                                                              | Revision behavior                                                               | City-local implications                                                        | Readiness impact                                                                                     |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------- |
| Weather Underground / TWC historical observations | On-demand historical/current observations; station observations can vary by device/station operations                                                                                                                        | IBM/TWC docs distinguish GMT and local wall time fields; repo WU path uses `valid_time_gmt` | Daily display high/low may change with late observations or provider processing | Must resolve provider’s displayed settlement local day, not just UTC day       | Store observation instant, provider fetch, provider revision/display time, station timezone          |
| Open-Meteo Archive                                | Historical API supports timezone parameter; hourly variables can be instant or preceding-hour sums; data sources include IFS updated every 6h no delay, ERA5 daily with 5-day delay, IFS assimilation daily with 2-day delay | API can return local timestamps when timezone is set                                        | Reanalysis/archive can revise or lag depending on dataset                       | Good for hourly local-day reconstruction if dataset choice is explicit         | Not all datasets are live-settlement grade; readiness must include dataset/version ([Open Meteo][5]) |
| Meteostat                                         | Hourly observations arrive with about 2–3h offset; some data may be added days or months later; bulk recent hourly data after max 24h                                                                                        | Station time vs API time must be explicit                                                   | Late additions common                                                           | Useful backfill/replay; risky for same-day settlement                          | Treat as delayed/secondary unless market rules explicitly allow it ([Meteostat Developers][6])       |
| OGIMET                                            | METAR/SYNOP public reports; METAR frequency often hourly/half-hourly/special                                                                                                                                                 | Aviation reports in UTC-oriented formats                                                    | Exact provider availability varies; server is public and capacity-limited       | Can support station-level observations, not daily settlement display by itself | Use as fallback/evidence, not silent authority ([Ogimet][7])                                         |
| HKO                                               | HKO climatological data page says data updated every working day before 2 p.m. up to previous day                                                                                                                            | Hong Kong local time                                                                        | Working-day cadence and prior-day window matter                                 | HKO cities must use Asia/Hong_Kong and source-specific calendar                | HKO daily settlement readiness cannot be assumed hourly ([Hong Kong Observatory][8])                 |

## 5.3 Polymarket / Gamma / CLOB

| Source           | Cadence / update                                                                                                                       | Time fields                                                                                                                                              | Readiness impact                                                                                                                                |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Gamma API        | Public market/event metadata; events and markets discovery                                                                             | market response fields include `endDate`, `startDate`, `createdAt`, `updatedAt`, `closed`, `closedTime`, `umaEndDate`, `enableOrderBook`, `clobTokenIds` | Topology freshness required before candidate construction ([Polymarket Documentation][9])                                                       |
| CLOB orderbook   | Public orderbook/pricing endpoints; WebSocket for real-time updates                                                                    | orderbook response includes `timestamp`; WS streams book/price/trade/tick/new/resolved events                                                            | Quote freshness is seconds-level and must block submit if stale ([Polymarket Documentation][10])                                                |
| Resolution / UMA | Market resolves after outcome known; proposal/challenge/dispute flow; undisputed resolution about 2h after proposal, disputed 4–6 days | end date, proposal time, resolved time, redemption time                                                                                                  | Settlement plane must separate target date, eligible resolution time, resolved time, and redeem confirmed time ([Polymarket Documentation][11]) |

# 6. Current patch vs release-calendar reality

| PR #42 mechanism                                | Reality check                                                                                                    | Verdict                                                              |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Fixed 18h threshold                             | ECMWF/Open-Meteo/HKO/Meteostat/OGIMET release patterns differ by source, product, model, and local day           | Good emergency heuristic, invalid readiness law                      |
| Global `MAX(captured_at)`                       | One fresh forecast row can hide stale city/date/source/lead/metric rows                                          | Invalid for live readiness                                           |
| Global `MAX(fetched_at)` for solar              | `data_coverage.fetched_at` updates on `FAILED`/`MISSING` too; one city/date can hide others                      | Invalid for live readiness                                           |
| Once-per-day forecast force-fetch               | Release can be partial; boot time may be before release                                                          | Must be release-calendar gated                                       |
| Once-per-day solar force-fetch                  | Solar is deterministic/auxiliary; global stale solar should not necessarily block live                           | Dependency-aware only                                                |
| Exclude hourly observations because hourly cron | Hourly cron does not prove local-day completeness, Day0 current observation age, or station settlement readiness | Exclusion unsafe                                                     |
| Exclude daily observations because hourly cron  | Daily observation settlement display can lag/revise and be source-specific                                       | Exclusion unsafe                                                     |
| Run Phase 2 after catch-up                      | Catch-up/backfill can refresh timestamps and mask current-window staleness                                       | Must check relevant current horizon and/or run before broad catch-up |
| Direct call to `daily_tick`                     | Bypasses normal lock path according to PR review                                                                 | Must acquire job/source lock                                         |
| No source release time                          | Cannot distinguish “fetched old data” from “new source run available”                                            | Blocker                                                              |
| No city timezone/DST                            | UTC freshness cannot authorize city local date markets                                                           | Blocker                                                              |
| No high/low separation                          | Different physical quantities and windows                                                                        | Blocker for dual-track correctness                                   |

# 7. Architecture options

| Option                              | Time correctness                  | Live safety                 | Source-release correctness | City-local correctness  |  Complexity | Verdict                                                                            |
| ----------------------------------- | --------------------------------- | --------------------------- | -------------------------- | ----------------------- | ----------: | ---------------------------------------------------------------------------------- |
| 1. Current daemon + PR #42 only     | Low                               | Low                         | Low                        | Low                     |         Low | Reject as final architecture. Accept only as temporary smoke guard.                |
| 2. Current daemon + readiness layer | Medium-high                       | High if consumed by trading | Medium-high                | High if keyed correctly |      Medium | Best minimal launch-safe path.                                                     |
| 3. Supervisor + typed job graph     | High                              | High                        | High                       | High                    | Medium-high | Correct medium-term target.                                                        |
| 4. Plane-separated daemons          | Highest                           | Highest if contracts mature | Highest                    | Highest                 |        High | Long-term operating model, not first step.                                         |
| 5. Hybrid launch path               | High enough now; extensible later | High                        | High                       | High                    |      Medium | Recommended. One supervisor now, typed planes/contracts now, physical split later. |

# 8. Final target data-daemon architecture

## 8.1 Daemon boundary

The data daemon should not decide whether to trade. It should produce **auditable readiness and provenance**. The trading runtime decides whether a strategy can act, but only by consuming the daemon’s machine-readable readiness contract.

Logical planes:

1. Forecast daemon plane.
2. Observation daemon plane.
3. Solar/auxiliary feature plane.
4. Market metadata/topology plane.
5. Settlement/truth harvester plane.
6. Source health plane.
7. Hole/backfill plane.
8. Ingest telemetry/operator-control plane.
9. Separate market-data/quote plane for CLOB orderbook/WS freshness.

## 8.2 Typed job graph

Every job must have:

| Field                   | Required meaning                                                          |
| ----------------------- | ------------------------------------------------------------------------- |
| `job_name`              | Stable job identity                                                       |
| `plane`                 | forecast / observation / market_metadata / settlement / health / backfill |
| `source_id`             | Upstream source                                                           |
| `track`                 | high / low / hourly / daily / solar / topology / quote / settlement       |
| `scheduled_for`         | Scheduler intended fire time                                              |
| `release_calendar_key`  | Source release-calendar entry                                             |
| `safe_fetch_not_before` | Earliest allowed fetch time                                               |
| `lock_key`              | Idempotent lease scope                                                    |
| `expected_scope`        | Cities/dates/metrics/run/steps expected                                   |
| `source_run_id`         | Source cycle/run identity                                                 |
| `job_run_id`            | Attempt identity                                                          |
| `result_status`         | success / partial / failed / skipped_not_released / degraded              |
| `readiness_impacts`     | scopes to recompute after job                                             |

## 8.3 Release-calendar registry

A versioned config/table defines:

```yaml
source_id: ecmwf_open_data
track: mx2t6_high
timezone: UTC
cycle_hours_utc: [0, 6, 12, 18]
product: ENS
parameter: mx2t6
period_semantics: "max temperature at 2m in previous 6h ending at valid step"
expected_members: 51
expected_steps: "per target local date"
safe_fetch_rules:
  - cycle: "00Z"
    not_before_utc: "08:05"   # for full derived/product horizon, not day0 only
partial_policy: BLOCK_LIVE
max_source_lag: "30h"
city_local_mapping: required
```

## 8.4 Timestamp contracts

Forecast contract:

* `source_cycle_time`
* `source_issue_time`
* `source_release_time`
* `source_available_at`
* `fetch_started_at`
* `fetch_finished_at`
* `captured_at`
* `imported_at`
* `valid_time_start`
* `valid_time_end`
* `lead_time_hours`
* `target_local_date`
* `readiness_computed_at`

Observation contract:

* `observation_instant_utc`
* `observation_local_timestamp`
* `provider_fetch_time`
* `provider_revision_time`
* `provider_display_time`
* `station_timezone`
* `target_local_date`
* `local_day_window_start/end`
* `daily_aggregation_window`
* `dst_status`
* `readiness_computed_at`

Settlement contract:

* `market_target_local_date`
* `provider_settlement_local_day`
* `market_close_time`
* `resolution_eligible_time`
* `uma_proposed_at`
* `polymarket_resolved_at`
* `zeus_recorded_settlement_at`
* `redeem_requested_at`
* `redeem_confirmed_at`

Market data contract:

* `gamma_created_at`
* `gamma_updated_at`
* `gamma_captured_at`
* `clob_orderbook_timestamp`
* `orderbook_captured_at`
* `quote_fresh_until`
* `submitted_order_time`
* `fill_time`
* `cancel_requested/confirmed`

Process contract:

* `scheduled_for`
* `missed_cron_time`
* `boot_time`
* `job_start/finish`
* `retry_time`
* `lock_acquired_at`
* `heartbeat_time`
* `source_degraded_since`
* `readiness_computed_at`

## 8.5 Readiness contracts

Scopes:

| Scope               | Key                                                      |
| ------------------- | -------------------------------------------------------- |
| Global              | `global`                                                 |
| Source              | `source_id`, `track`                                     |
| City/date/metric    | `city`, `target_local_date`, `metric`                    |
| Source run          | `source_run_id`                                          |
| Market topology     | `market_family`, `event_id`, `condition_id`, `token_ids` |
| Quote               | `condition_id`, `token_id`, `side`                       |
| Strategy dependency | `strategy_key`, dependencies                             |

Decisions:

* `LIVE_ELIGIBLE`
* `SHADOW_ONLY`
* `BLOCKED`
* `DEGRADED_LOG_ONLY`
* `UNKNOWN_BLOCKED`

## 8.6 Provenance contracts

A row is never just “fresh.” It must carry:

* source id and role;
* source run/cycle identity;
* data version/model version;
* payload hash/manifest hash;
* expected vs observed members/steps/hours;
* completeness status;
* city timezone;
* target local date;
* metric identity;
* upstream status/reason;
* job run id;
* raw payload pointer or hash;
* write transaction id.

## 8.7 Source health

Keep `source_health_probe.py`, but demote it to reachability. Readiness depends on both:

```text
source_reachable AND source_run_available AND required_scope_written AND completeness_ok
```

## 8.8 Hole/backfill

Backfill must never live-authorize data by itself. A backfill write can repair historical coverage and trigger readiness recomputation, but the readiness row must mark:

* `mode = BACKFILL`
* `live_authorization = false`
* `strategy_eligible = false` unless source release and causal availability rules are satisfied.

## 8.9 Retries/backoff

Rules:

* Before release: `SKIPPED_NOT_RELEASED`, retry at release-calendar next safe time.
* During partial run: `PARTIAL`, retry with exponential backoff until completeness or max lag.
* Source unreachable: `SOURCE_DEGRADED`, mark affected dependencies blocked or shadow-only.
* Quota/rate limit: store `retry_after`, do not fake freshness.
* Parse/validation failure: block affected scope and record payload hash.

## 8.10 Locks

Use DB-backed leases:

```text
lock_key = plane/source_id/track/source_cycle_time/target_scope
```

Boot-forced jobs and scheduled jobs must acquire the same lock. Lock records include `acquired_at`, `expires_at`, `owner_pid`, `owner_host`, `heartbeat_at`, and `job_run_id`.

## 8.11 Telemetry

Required operator surfaces:

* last job success/failure per typed job;
* source-run completeness;
* readiness state counts by status;
* blocked live candidates by reason;
* stale source but no dependent strategy counts;
* scheduler misfires/coalesced skips;
* duplicate lock contention;
* city/date/metric hole impact;
* market topology age;
* quote age;
* settlement degradation near resolution.

# 9. Minimal launch-safe daemon design

## 9.1 What PR #42 may temporarily cover

PR #42 may cover only this invariant:

> On daemon boot, do not wait until the next daily cron to notice that forecast/solar daily jobs may have been missed.

It may not authorize live trades.

## 9.2 Required wrapper/fixes

Before live launch, wrap or replace PR #42 with:

1. Acquire the same per-table/job lock as scheduled daily ticks.
2. Check current-horizon scopes before broad catch-up can refresh old timestamps.
3. For solar, require `data_coverage.status='WRITTEN'` or read `solar_daily.fetched_at`, not any `fetched_at`.
4. Replace global max with source/city/date/metric current-market checks.
5. Add release-calendar guard: do not fetch “new” forecast before the new source run is expected.
6. If fetch occurs before release, record `SKIPPED_NOT_RELEASED`, not fresh.
7. If fetched payload is same source run as prior, record `NO_NEW_RUN`, not fresh.
8. If partial, record `PARTIAL_RUN`, block/shadow dependent scopes.
9. Recompute readiness rows after any boot fetch/catch-up.
10. Make `cycle_runner` consume readiness before evaluator/executor.

## 9.3 Minimal readiness object

The smallest launch-safe object is one row/document per active market candidate dependency:

```text
(city, target_local_date, metric, strategy_key, market_family)
```

It must include source status, market topology status, quote status, and decision:

```text
BLOCKED | SHADOW_ONLY | LIVE_ELIGIBLE
```

## 9.4 Live blockers

Global live blockers:

* readiness table absent or stale;
* source-health absent;
* scheduler job-health absent for required plane;
* duplicate daemon lock;
* missing city timezone;
* market topology stale/unknown;
* CLOB auth/heartbeat/wallet/chain unavailable;
* quote/orderbook stale at submit;
* forecast readiness missing for forecast-dependent strategy;
* observation readiness missing for Day0/settlement-capture strategy;
* settlement source degraded near resolution;
* partial forecast run for dependent strategy;
* data source release calendar unknown.

City/date/metric blockers:

* missing row for city/date/metric;
* stale source run;
* incomplete high/low track;
* insufficient ensemble members/steps/hours;
* UTC/local-day mismatch;
* DST expected-hour mismatch;
* source contract mismatch or quarantine;
* hole scanner marks relevant scope missing.

Shadow-only states:

* source reachable but release-calendar uncertain;
* fresh data but missing source issue/release proof;
* partial forecast not used for live;
* market metadata fresh but source contract ambiguous;
* backfill rows available but not causally live;
* source is degraded but strategy claims independence and needs observation only for monitoring.

Log-only:

* stale auxiliary solar when no active strategy uses it;
* delayed archive/backfill source outside active market scope;
* stale historical calibration input not used by current strategy;
* non-critical source health failure outside dependency graph.

## 9.5 Known limitations

* Does not require full daemon split before launch.
* Does not rewrite all ingestion tables first.
* Does not solve every historical backfill inconsistency.
* Does not retire PR #42 immediately.
* Does not give data daemon authority to trade.

## 9.6 Rollback

Rollback path:

1. Set readiness global status to `BLOCKED`.
2. Trading runtime enters no-new-entries / monitor-only.
3. Keep PR #42 boot guard for telemetry.
4. Disable boot forced fetch if duplicate-write risk is observed.
5. Use operator command to mark affected source/city/date/metric `SHADOW_ONLY` or `BLOCKED`.
6. Restore prior scheduler cadence after readiness recomputation passes.

# 10. Readiness object spec

## 10.1 Machine-readable object

```json
{
  "schema_version": 1,
  "readiness_id": "city:New York|date:2026-05-03|metric:high|strategy:center_buy",
  "scope": {
    "global": false,
    "source_id": "ecmwf_open_data",
    "source_track": "mx2t6_high",
    "city": "New York",
    "city_timezone": "America/New_York",
    "target_local_date": "2026-05-03",
    "metric": "high",
    "market_family": "polymarket_weather_temperature",
    "event_id": "gamma-event-id",
    "condition_id": "0x...",
    "token_ids": ["yes-token", "no-token"],
    "strategy_key": "center_buy"
  },
  "decision": {
    "status": "BLOCKED",
    "live_decision": "block",
    "shadow_allowed": true,
    "reason_codes": [
      "FORECAST_SOURCE_RUN_NOT_RELEASED",
      "MARKET_TOPOLOGY_FRESH",
      "QUOTE_NOT_CHECKED"
    ],
    "computed_at": "2026-05-02T12:45:00+00:00",
    "expires_at": "2026-05-02T13:00:00+00:00"
  },
  "source_status": {
    "reachable": true,
    "source_health_status": "FRESH",
    "source_degraded_since": null,
    "source_issue_time": "2026-05-02T00:00:00+00:00",
    "source_cycle_time": "2026-05-02T00:00:00+00:00",
    "source_release_time": "2026-05-02T08:05:00+00:00",
    "source_available_at": null,
    "safe_fetch_not_before": "2026-05-02T08:05:00+00:00",
    "release_calendar_version": "2026-05-02.ecmwf.v1"
  },
  "data_status": {
    "fetched_at": null,
    "captured_at": null,
    "imported_at": null,
    "valid_time_start": null,
    "valid_time_end": null,
    "lead_time_hours": null,
    "target_local_window_start": "2026-05-03T00:00:00-04:00",
    "target_local_window_end": "2026-05-03T23:59:59-04:00",
    "expected_members": 51,
    "observed_members": 0,
    "expected_steps": [],
    "observed_steps": [],
    "completeness_status": "NOT_RELEASED",
    "partial_run": false,
    "payload_hash": null,
    "manifest_hash": null
  },
  "market_status": {
    "gamma_updated_at": "2026-05-02T12:20:00+00:00",
    "gamma_captured_at": "2026-05-02T12:25:00+00:00",
    "topology_status": "FRESH",
    "source_contract_status": "MATCH",
    "orderbook_timestamp": null,
    "orderbook_captured_at": null,
    "quote_fresh_until": null,
    "quote_status": "NOT_CHECKED"
  },
  "strategy_dependency_status": {
    "requires_forecast": true,
    "requires_day0_observation": false,
    "requires_daily_observation": false,
    "requires_solar": false,
    "requires_market_topology": true,
    "requires_quote": true,
    "requires_settlement_truth": false,
    "dependency_status": "BLOCKED"
  },
  "provenance": {
    "job_run_id": "jobrun-...",
    "source_run_id": "sourcerun-...",
    "data_coverage_key": "forecasts/New York/ecmwf_open_data/2026-05-03/high",
    "raw_payload_ref": null,
    "validation_refs": []
  }
}
```

## 10.2 Decision rules

| Inputs                                            | Decision                                       |
| ------------------------------------------------- | ---------------------------------------------- |
| Required dependency missing                       | `BLOCKED`                                      |
| Source not released                               | `BLOCKED`, retry later                         |
| Source released but partial                       | `SHADOW_ONLY` or `BLOCKED` if strategy depends |
| Source stale but strategy independent             | `DEGRADED_LOG_ONLY`                            |
| Forecast fresh but market topology stale          | `BLOCKED`                                      |
| Forecast/topology fresh but quote stale           | `BLOCKED_AT_SUBMIT`                            |
| Backfill-only data                                | `SHADOW_ONLY`                                  |
| All required dependencies fresh, complete, causal | `LIVE_ELIGIBLE`                                |

# 11. Schema and persistence plan

## 11.1 `source_release_calendar`

Config table or versioned YAML:

| Column                     | Meaning                    |
| -------------------------- | -------------------------- |
| `calendar_id`              | versioned calendar key     |
| `source_id`                | upstream                   |
| `track`                    | high/low/hourly/daily/etc. |
| `timezone`                 | source timezone            |
| `cycle_hours_utc_json`     | run cycles                 |
| `safe_fetch_rule_json`     | not-before rules           |
| `expected_member_count`    | ensemble completeness      |
| `expected_step_rule_json`  | required steps by target   |
| `partial_policy`           | block/shadow/retry         |
| `max_lag_seconds`          | stale threshold            |
| `active_from`, `active_to` | version bounds             |

## 11.2 `source_run`

| Column                                       | Meaning                   |
| -------------------------------------------- | ------------------------- |
| `source_run_id`                              | primary key               |
| `source_id`, `track`                         | source identity           |
| `source_cycle_time`                          | source run cycle          |
| `source_issue_time`                          | issue/init time           |
| `source_release_time`                        | expected/official release |
| `source_available_at`                        | observed available time   |
| `fetch_started_at`, `fetch_finished_at`      | app fetch                 |
| `captured_at`, `imported_at`                 | app persistence           |
| `model_version`, `data_version`              | model/data identity       |
| `expected_members`, `observed_members`       | completeness              |
| `expected_steps_json`, `observed_steps_json` | step completeness         |
| `completeness_status`                        | complete/partial/missing  |
| `partial_run`                                | boolean                   |
| `raw_payload_hash`, `manifest_hash`          | provenance                |
| `status`, `reason_code`                      | run result                |

## 11.3 `job_run`

| Column                         | Meaning                          |
| ------------------------------ | -------------------------------- |
| `job_run_id`                   | primary key                      |
| `job_name`, `plane`            | job identity                     |
| `scheduled_for`                | cron/interval intended time      |
| `missed_from`                  | missed cron anchor if applicable |
| `started_at`, `finished_at`    | process times                    |
| `lock_key`, `lock_acquired_at` | lease                            |
| `status`, `reason_code`        | result                           |
| `rows_written`, `rows_failed`  | effect                           |
| `source_run_id`                | source run                       |
| `readiness_recomputed_at`      | readiness update                 |

## 11.4 `data_coverage` upgrade

Existing `data_coverage` is useful and already has table/city/source/date/sub-key/status/reason/retry semantics. Upgrade with:

* `source_run_id`
* `target_local_date`
* `city_timezone`
* `metric`
* `source_issue_time`
* `source_release_time`
* `source_available_at`
* `valid_time_start`
* `valid_time_end`
* `fetched_at`
* `captured_at`
* `status_is_success`
* `completeness_status`
* `expected_count`
* `observed_count`
* `readiness_scope_key`

Do **not** use `fetched_at` alone for freshness because `record_written`, `record_failed`, and `record_missing` all update `fetched_at`. 

## 11.5 `readiness_state`

| Column                                                        | Meaning                                   |
| ------------------------------------------------------------- | ----------------------------------------- |
| `readiness_id`                                                | primary key                               |
| `scope_type`                                                  | global/source/city_metric/market/strategy |
| `city`, `target_local_date`, `metric`                         | local market target                       |
| `source_id`, `track`, `source_run_id`                         | source                                    |
| `market_family`, `event_id`, `condition_id`, `token_ids_json` | market topology                           |
| `strategy_key`                                                | dependency consumer                       |
| `status`                                                      | live/shadow/blocked/degraded              |
| `reason_codes_json`                                           | reasons                                   |
| `computed_at`, `expires_at`                                   | readiness time                            |
| `dependency_json`                                             | dependency graph                          |
| `provenance_json`                                             | pointers                                  |

## 11.6 `source_health`

Move from JSON-only to DB-backed records:

* `source_id`
* `probe_started_at`
* `probe_finished_at`
* `last_success_at`
* `last_failure_at`
* `degraded_since`
* `latency_ms`
* `error`
* `probe_payload_hash`

JSON can remain as a derived export.

## 11.7 `hole_backfill_state`

* `hole_id`
* `data_table`
* `city`
* `target_local_date`
* `metric`
* `source_id`
* `status`
* `detected_at`
* `filled_at`
* `backfill_mode`
* `live_authorization_allowed=false`
* `readiness_recomputed_at`

## 11.8 `market_topology_state`

* `event_id`
* `market_slug`
* `condition_id`
* `question_id`
* `clob_token_ids`
* `outcomes`
* `bin_topology_hash`
* `gamma_updated_at`
* `gamma_captured_at`
* `source_contract_status`
* `topology_status`
* `expires_at`

## 11.9 Migration plan

1. Add tables without changing existing writes.
2. Dual-write `job_run`, `source_run`, and `readiness_state`.
3. Add tests proving old PR #42 staleness cases still pass.
4. Make `cycle_runner` read readiness in shadow-only mode.
5. Flip readiness to block live entries.
6. Retire global max boot guard after readiness boot recomputation passes.

# 12. Tests and CI gates

Required tests:

| Test                                   | Purpose                                                            |
| -------------------------------------- | ------------------------------------------------------------------ |
| PR #42 stale/fresh/empty forecasts     | Preserve emergency behavior                                        |
| PR #42 stale/fresh/empty solar         | Preserve emergency behavior                                        |
| naive timestamp parse                  | Ensure naive strings do not silently miscompute local/source times |
| timezone-aware timestamp parse         | Correct UTC duration math                                          |
| global max hides stale city            | Prove `MAX(captured_at)` insufficient                              |
| global max hides stale metric          | One high row cannot freshen low                                    |
| stale one metric but fresh another     | Metric-scoped readiness                                            |
| city local day crossing UTC            | Target date local vs UTC                                           |
| DST spring-forward                     | Expected 23-hour local day                                         |
| DST fall-back                          | Expected 25-hour or ambiguous-hour handling                        |
| boot before source release             | `SKIPPED_NOT_RELEASED`, not fresh                                  |
| partial forecast run                   | `PARTIAL_RUN`, block dependent live                                |
| missing ensemble members               | readiness blocked                                                  |
| missing forecast valid windows         | readiness blocked                                                  |
| stale source but strategy-independent  | log-only/degraded, no unrelated block                              |
| source fresh but market topology stale | block live entries                                                 |
| market topology fresh but quote stale  | block submit                                                       |
| hourly obs stale despite hourly cron   | Day0 blocked                                                       |
| daily obs stale despite hourly cron    | settlement-capture blocked                                         |
| backfill not live-authorizing          | backfill rows shadow-only                                          |
| duplicate daemon lock                  | second job skipped/blocked                                         |
| failed job stale green readiness       | failure cannot leave old green readiness                           |
| hole scanner blocks relevant market    | active market scope blocked if coverage hole exists                |
| solar auxiliary no dependency          | stale solar does not block unrelated strategy                      |
| source release calendar unknown        | shadow/block, never live                                           |
| scheduler timezone UTC                 | explicit scheduler UTC configuration test                          |

Suggested commands:

```bash
python -m pytest tests/test_ingest_main_boot_resilience.py -q
python -m pytest tests/test_time_semantics.py -q
python -m pytest tests/test_readiness_state.py -q
python -m pytest tests/test_release_calendar.py -q
python -m pytest tests/test_cycle_runner_readiness.py -q
python -m pytest tests/test_hole_scanner.py -q
python -m pytest tests/test_market_scanner.py -q
```

# 13. Hidden branch register

| Branch                                                                 | Risk     | Stage | Decision                 | Test / gate                     | Rollback                 | Validation status        |
| ---------------------------------------------------------------------- | -------- | ----: | ------------------------ | ------------------------------- | ------------------------ | ------------------------ |
| Emergency patch becomes permanent architecture                         | High     |  0–11 | Forbid                   | architecture acceptance gate    | keep PR, add readiness   | Open                     |
| Global `MAX(captured_at)` hides stale city/date/metric                 | Critical |     1 | Replace                  | global-max-hides-city test      | block readiness          | Proven risk              |
| `captured_at` mistaken for issue time                                  | Critical |   1–5 | Separate fields          | issue/capture tests             | shadow-only              | Proven risk              |
| `fetched_at` mistaken for source release time                          | Critical |   1–5 | Separate fields          | release-calendar tests          | block source             | Proven risk              |
| Boot force-fetch before release creates false freshness                | Critical |   2–6 | Guard                    | boot-before-release test        | skipped_not_released     | Open                     |
| Fixed 18h threshold wrong for source cadence                           | High     |     2 | Replace                  | source calendar unit tests      | per-source max lag       | Open                     |
| UTC day mistaken for city local day                                    | Critical |     4 | Block                    | local-day UTC-cross test        | block city scope         | Open                     |
| DST boundary shifts target day                                         | Critical |     4 | Block                    | DST tests                       | block city/date          | Open                     |
| High/low windows conflated                                             | Critical |   4–5 | Separate metrics         | metric readiness tests          | block metric             | Open                     |
| Hourly obs excluded but Day0 needs them                                | High     |     4 | Include                  | stale hourly Day0 test          | block Day0               | Open                     |
| Daily obs excluded but settlement needs them                           | High     |     4 | Include                  | stale daily settlement test     | block settlement-capture | Open                     |
| Solar freshness treated as trading-critical without dependency mapping | Medium   |   3–4 | Dependency-aware         | stale solar nondependent test   | log-only                 | Open                     |
| Source partial run marked fresh                                        | Critical |     5 | Block/shadow             | partial run test                | retry                    | Open                     |
| Table non-empty treated as complete                                    | Critical |   4–5 | Forbid                   | completeness test               | block                    | Open                     |
| One city fresh makes all cities fresh                                  | Critical |     4 | Forbid                   | city-scope test                 | block stale city         | Open                     |
| One metric fresh makes both high/low fresh                             | Critical |     4 | Forbid                   | metric-scope test               | block stale metric       | Open                     |
| Source health green but data stale                                     | High     |     3 | Separate                 | source-health-vs-data test      | block data scope         | Open                     |
| Data fresh but market topology stale                                   | Critical |     8 | Block                    | topology stale test             | block market             | Open                     |
| Market topology fresh but quote stale                                  | Critical |     8 | Block at submit          | quote stale test                | no submit                | Open                     |
| Backfill authorizes live                                               | Critical |     7 | Forbid                   | backfill shadow test            | shadow-only              | Open                     |
| Job failure leaves old readiness green                                 | Critical |     3 | Expire/overwrite         | failed job readiness test       | set blocked              | Open                     |
| Duplicate daemon instances double-write                                | High     |     6 | Lease                    | duplicate lock test             | skip second              | Open                     |
| No lock around boot-forced daily tick                                  | High     |     6 | Fix                      | boot-lock test                  | disable boot tick        | Proven PR risk           |
| Timezone-naive parsed string crashes/miscomputes                       | High     |     1 | Validate                 | naive parse test                | block invalid            | Open                     |
| Tests patch away source release behavior                               | High     |     2 | Add calendar tests       | release fixture tests           | require source calendar  | Open                     |
| Readiness based on logs instead of machine state                       | Critical |     3 | Forbid                   | readiness table presence gate   | no-new-entries           | Open                     |
| Scheduler comments say UTC but scheduler lacks explicit UTC            | High     |     1 | Verify/fix               | scheduler timezone test         | block launch             | Open                     |
| `data_coverage.fetched_at` from FAILED/MISSING looks fresh             | High     |     1 | Status filter            | failed solar freshness test     | block solar scope        | Proven PR risk           |
| Market source contract mismatch ignored                                | Critical |     8 | Block                    | source contract quarantine test | skip market              | Existing partial support |
| Settlement degraded near resolution                                    | High     |     9 | Block settlement capture | settlement degraded test        | manual review            | Open                     |

# 14. Implementation roadmap

## Stage 0 — PR #42 evidence lock and current daemon inventory

* **Objective:** freeze what PR #42 does and document current job graph.
* **Dependencies:** none.
* **Files:** `src/ingest_main.py`, `tests/test_ingest_main_boot_resilience.py`, `src/main.py`, `src/control/freshness_gate.py`.
* **Forbidden files:** execution/order placement.
* **Tasks:** add inventory doc/test fixture for schedules, locks, boot sequence.
* **Tests:** existing boot resilience tests.
* **Commands:** `python -m pytest tests/test_ingest_main_boot_resilience.py -q`
* **Live impact:** none.
* **Rollback:** remove inventory-only files.

## Stage 1 — Time-semantics tests around PR #42

* **Objective:** prove global max and naive timestamp failure modes.
* **Dependencies:** Stage 0.
* **Files:** new `tests/test_ingest_boot_time_semantics.py`.
* **Forbidden files:** schema migrations.
* **Tasks:** add failing tests for global max, failed solar freshness, naive/aware parse, scheduler timezone.
* **Tests:** new test file.
* **Commands:** `python -m pytest tests/test_ingest_boot_time_semantics.py -q`
* **Live impact:** none.
* **Rollback:** remove tests.

## Stage 2 — Release-calendar registry

* **Objective:** encode source safe-fetch and partial-run rules.
* **Dependencies:** Stage 1.
* **Files:** `src/data/release_calendar.py`, `config/source_release_calendar.yaml`, tests.
* **Forbidden files:** evaluator/executor.
* **Tasks:** registry, ECMWF/Open-Meteo/HKO/Meteostat/TIGGE initial calendars, `safe_fetch_not_before`.
* **Tests:** boot before release, unknown source calendar, partial policy.
* **Commands:** `python -m pytest tests/test_release_calendar.py -q`
* **Live impact:** none until consumed.
* **Rollback:** disable calendar consumer.

## Stage 3 — Machine-readable readiness object

* **Objective:** create readiness writer/reader and canonical statuses.
* **Dependencies:** Stage 2.
* **Files:** `src/data/readiness.py`, `src/state/db.py`, tests.
* **Forbidden files:** executor.
* **Tasks:** add `readiness_state`, object builder, expiry rules.
* **Tests:** schema, stale old green expires, reason codes.
* **Commands:** `python -m pytest tests/test_readiness_state.py -q`
* **Live impact:** shadow-only export.
* **Rollback:** ignore readiness reader.

## Stage 4 — City/date/metric readiness

* **Objective:** compute readiness for active market scopes.
* **Dependencies:** Stage 3.
* **Files:** readiness module, forecast/obs adapters, market scanner consumer.
* **Forbidden files:** order placement.
* **Tasks:** key by city IANA timezone, target local date, metric, strategy dependency.
* **Tests:** UTC-local crossing, DST, high/low separation.
* **Commands:** `python -m pytest tests/test_city_metric_readiness.py -q`
* **Live impact:** read-only/shadow.
* **Rollback:** set global readiness disabled.

## Stage 5 — Source-specific timestamp/provenance schema

* **Objective:** persist `source_run` and provenance fields.
* **Dependencies:** Stages 2–4.
* **Files:** `src/state/db.py`, forecast/obs writers.
* **Forbidden files:** portfolio lifecycle.
* **Tasks:** add `source_run`, dual-write from forecast/obs jobs, payload hashes.
* **Tests:** source run identity, issue/release/fetch/capture separation.
* **Commands:** `python -m pytest tests/test_source_run_schema.py -q`
* **Live impact:** no behavior change.
* **Rollback:** stop dual-write.

## Stage 6 — Boot catch-up replacement/wrapper

* **Objective:** wrap PR #42 with locks, status filters, calendar checks.
* **Dependencies:** Stages 2–5.
* **Files:** `src/ingest_main.py`, readiness writer.
* **Forbidden files:** executor.
* **Tasks:** acquire locks; check readiness scopes; run before broad catch-up or scope to current horizon.
* **Tests:** boot lock, failed solar fetched_at, boot before release.
* **Commands:** `python -m pytest tests/test_ingest_main_boot_resilience.py tests/test_ingest_boot_time_semantics.py -q`
* **Live impact:** safer boot.
* **Rollback:** revert to PR #42 but force no-new-entries.

## Stage 7 — Hole/backfill live-authorization gate

* **Objective:** prevent holes/backfills from silently authorizing live.
* **Dependencies:** Stage 4.
* **Files:** `hole_scanner.py`, readiness module, tests.
* **Forbidden files:** executor.
* **Tasks:** active market holes block relevant readiness; backfill mode shadow-only.
* **Tests:** hole scanner blocks market, backfill not live.
* **Commands:** `python -m pytest tests/test_hole_scanner_readiness.py -q`
* **Live impact:** blocks affected scopes.
* **Rollback:** set readiness global blocked.

## Stage 8 — Market metadata/topology readiness

* **Objective:** make Gamma topology a first-class readiness dependency.
* **Dependencies:** Stage 3.
* **Files:** `market_scanner.py`, readiness module, `cycle_runner.py`.
* **Forbidden files:** order signing.
* **Tasks:** persist topology freshness, source contract status, token/bin hash.
* **Tests:** topology stale blocks live, quote stale blocks submit.
* **Commands:** `python -m pytest tests/test_market_topology_readiness.py -q`
* **Live impact:** block stale topology.
* **Rollback:** no-new-entries.

## Stage 9 — Observation/settlement time split

* **Objective:** separate observation instant/fetch/revision and settlement resolved/redeemed times.
* **Dependencies:** Stage 5.
* **Files:** obs writers, harvester/truth writer, DB schema.
* **Forbidden files:** strategy math.
* **Tasks:** add provider revision/display times, settlement proposal/resolution/redeem fields.
* **Tests:** settlement degraded near resolution, daily obs source lag.
* **Commands:** `python -m pytest tests/test_observation_settlement_time_semantics.py -q`
* **Live impact:** safer settlement capture.
* **Rollback:** block settlement capture.

## Stage 10 — Telemetry/operator controls

* **Objective:** expose readiness and override controls.
* **Dependencies:** Stages 3–9.
* **Files:** ingest status writer, ops CLI, control plane.
* **Forbidden files:** executor without explicit approval.
* **Tasks:** readiness summaries, operator block/shadow overrides, alerts.
* **Tests:** override precedence, telemetry JSON.
* **Commands:** `python -m pytest tests/test_readiness_operator_controls.py -q`
* **Live impact:** operator visibility.
* **Rollback:** remove override consumer.

## Stage 11 — Emergency patch retirement

* **Objective:** replace global max guard with readiness recomputation and typed boot jobs.
* **Dependencies:** all prior stages.
* **Files:** `src/ingest_main.py`, tests.
* **Forbidden files:** source writers unless needed.
* **Tasks:** retire `_BOOT_FRESHNESS_THRESHOLD_HOURS` as readiness logic; keep alert threshold if desired.
* **Tests:** all readiness and boot tests.
* **Commands:** `python -m pytest tests/test_ingest_main_boot_resilience.py tests/test_readiness_state.py tests/test_cycle_runner_readiness.py -q`
* **Live impact:** final architecture cutover.
* **Rollback:** restore PR #42 guard but keep readiness blocking live.

# 15. Codex/local-agent prompts

## Stage 0 prompt

```text
Role: Zeus data-plane evidence auditor.

Read first:
- AGENTS.md
- src/ingest_main.py
- src/main.py
- src/control/freshness_gate.py
- tests/test_ingest_main_boot_resilience.py

Allowed files:
- docs/operations/current_daemon_inventory.md
- tests/test_ingest_main_boot_resilience.py only if non-behavioral assertion names need clarification

Forbidden files:
- src/execution/**
- src/engine/evaluator.py
- schema migrations

Invariants:
- Do not change runtime behavior.
- Treat PR #42 as emergency patch, not architecture.
- Preserve all current boot-resilience tests.

Tasks:
1. Reconstruct boot sequence, job graph, cron schedule, coalesce/misfire settings, lock path, and current startup catch-up behavior.
2. Record all timestamp fields used by boot freshness checks.
3. Mark scheduler timezone as VERIFIED only if explicit UTC config is found.

Tests:
- python -m pytest tests/test_ingest_main_boot_resilience.py -q

Expected failures before fix:
- None; inventory only.

Closeout evidence:
- Inventory doc with exact file/line references and unresolved blockers.

Rollback:
- Delete inventory doc.

Not-now constraints:
- No schema change.
- No readiness implementation.
- No trading logic edits.
```

## Stage 1 prompt

```text
Role: Zeus time-semantics test author.

Read first:
- src/ingest_main.py
- src/state/data_coverage.py
- tests/test_ingest_main_boot_resilience.py
- src/data/forecasts_append.py
- src/data/solar_append.py

Allowed files:
- tests/test_ingest_boot_time_semantics.py
- tests/conftest.py only if fixture reuse is needed

Forbidden files:
- src/**
- migrations
- execution/trading files

Invariants:
- Tests must fail against global MAX logic where expected.
- Do not patch away source-release behavior in new tests.
- Use timezone-aware UTC timestamps unless specifically testing naive input.

Tasks:
1. Add test proving fresh row for City A hides stale City B under global MAX.
2. Add test proving fresh high metric does not freshen low metric.
3. Add test proving recent FAILED/MISSING solar data_coverage fetched_at must not count fresh.
4. Add naive timestamp and timezone-aware parse cases.
5. Add scheduler timezone assertion if scheduler object is inspectable.

Tests:
- python -m pytest tests/test_ingest_boot_time_semantics.py -q

Expected failures before fix:
- Global max and solar FAILED/MISSING tests should fail.

Closeout evidence:
- List each failing test and the architectural risk it proves.

Rollback:
- Remove new test file.

Not-now constraints:
- Do not implement fixes in this stage.
```

## Stage 2 prompt

```text
Role: Zeus source release-calendar implementer.

Read first:
- src/data/ecmwf_open_data.py
- src/data/forecasts_append.py
- src/data/hourly_instants_append.py
- src/data/daily_obs_append.py
- src/data/solar_append.py
- docs/operations/current_source_validity.md if present
- AGENTS.md

Allowed files:
- src/data/release_calendar.py
- config/source_release_calendar.yaml
- tests/test_release_calendar.py

Forbidden files:
- src/execution/**
- src/engine/**
- destructive migrations

Invariants:
- Release calendar is advisory until consumed.
- Unknown calendar means not live-eligible.
- Fetch time is not release time.

Tasks:
1. Define SourceReleaseCalendarEntry dataclass.
2. Implement safe_fetch_not_before(source_id, track, cycle_time, target_scope).
3. Add ECMWF Open Data high/low, Open-Meteo previous-runs, HKO, Meteostat, TIGGE entries.
4. Encode partial-run policy.
5. Add tests for boot before release and TIGGE backfill-only behavior.

Tests:
- python -m pytest tests/test_release_calendar.py -q

Expected failures before fix:
- New tests fail because registry absent.

Closeout evidence:
- Calendar entries and tests passed.

Rollback:
- Remove calendar file and config.
```

## Stage 3 prompt

```text
Role: Zeus readiness-contract implementer.

Read first:
- src/state/db.py
- src/control/freshness_gate.py
- src/data/ingest_status_writer.py
- src/data/source_health_probe.py
- AGENTS.md

Allowed files:
- src/data/readiness.py
- src/state/db.py
- tests/test_readiness_state.py

Forbidden files:
- src/execution/**
- order placement
- portfolio lifecycle

Invariants:
- Readiness is machine-readable state, not logs.
- Old green readiness must expire.
- Missing readiness blocks live.

Tasks:
1. Add readiness_status enum.
2. Add readiness_state schema idempotently.
3. Implement write_readiness/read_readiness.
4. Implement expired readiness behavior.
5. Add reason-code validation.

Tests:
- python -m pytest tests/test_readiness_state.py -q

Expected failures before fix:
- Schema/table absent.

Closeout evidence:
- DDL summary, reason-code list, tests passed.

Rollback:
- Stop using readiness reader; table can remain dormant.
```

## Stage 4 prompt

```text
Role: Zeus city-local readiness implementer.

Read first:
- src/config.py
- src/data/daily_obs_append.py
- src/data/hourly_instants_append.py
- src/data/forecasts_append.py
- src/types/metric_identity.py
- src/data/readiness.py

Allowed files:
- src/data/readiness.py
- tests/test_city_metric_readiness.py
- minimal helper modules under src/data/

Forbidden files:
- src/execution/**
- strategy math changes

Invariants:
- Target date is city-local settlement date.
- High and low never share readiness.
- DST expected-hour counts must be explicit.

Tasks:
1. Implement city/date/metric readiness key builder.
2. Convert target local date to UTC window using IANA timezone.
3. Add DST spring/fall tests.
4. Add high/low separation tests.
5. Add one-city-fresh-does-not-freshen-all test.

Tests:
- python -m pytest tests/test_city_metric_readiness.py -q

Expected failures before fix:
- Tests fail because readiness not scoped.

Closeout evidence:
- Examples for New York, London, Hong Kong DST/local-day cases.

Rollback:
- Disable city-metric readiness consumer.
```

## Stage 5 prompt

```text
Role: Zeus provenance schema implementer.

Read first:
- src/state/db.py
- src/data/ecmwf_open_data.py
- src/data/forecasts_append.py
- src/data/ensemble_client.py
- src/data/daily_obs_append.py
- src/data/hourly_instants_append.py

Allowed files:
- src/state/db.py
- src/data/source_run.py
- focused writer dual-write edits
- tests/test_source_run_schema.py

Forbidden files:
- execution/order files
- portfolio lifecycle

Invariants:
- Dual-write only; no destructive migration.
- Source issue/release/fetch/capture/valid times remain separate.
- Backfill rows must not imply live causality.

Tasks:
1. Add source_run schema.
2. Add helper to create/update source_run.
3. Dual-write source_run_id from forecast and observation jobs where available.
4. Add payload/manifest hash tests.

Tests:
- python -m pytest tests/test_source_run_schema.py -q

Expected failures before fix:
- source_run table absent.

Closeout evidence:
- Source run rows for representative forecast and observation fixtures.

Rollback:
- Disable source_run dual-write; table remains inert.
```

## Stage 6 prompt

```text
Role: Zeus boot-catch-up safety implementer.

Read first:
- src/ingest_main.py
- src/data/dual_run_lock.py
- src/data/release_calendar.py
- src/data/readiness.py
- tests/test_ingest_main_boot_resilience.py
- tests/test_ingest_boot_time_semantics.py

Allowed files:
- src/ingest_main.py
- tests/test_ingest_main_boot_resilience.py
- tests/test_ingest_boot_time_semantics.py

Forbidden files:
- src/execution/**
- broad schema changes

Invariants:
- Preserve stale/fresh/empty emergency behavior.
- Boot-forced daily_tick must acquire same lock as scheduled job.
- Status FAILED/MISSING must not count fresh.
- Boot before source release must not mark data fresh.

Tasks:
1. Add lock wrapper around boot forced jobs.
2. Evaluate relevant current-horizon readiness before broad catch-up or scope after catch-up.
3. Replace solar MAX fetched_at with WRITTEN-only scoped check.
4. Use release calendar to skip not-yet-released runs.
5. Recompute readiness after boot work.

Tests:
- python -m pytest tests/test_ingest_main_boot_resilience.py tests/test_ingest_boot_time_semantics.py -q

Expected failures before fix:
- Lock and status-filter tests fail.

Closeout evidence:
- Test output and explanation of any intentionally unchanged emergency behavior.

Rollback:
- Revert src/ingest_main.py; set readiness global BLOCKED.
```

## Stage 7 prompt

```text
Role: Zeus hole/backfill live-gate implementer.

Read first:
- src/data/hole_scanner.py
- src/state/data_coverage.py
- src/data/readiness.py
- src/data/forecasts_append.py
- src/data/hourly_instants_append.py

Allowed files:
- src/data/hole_scanner.py
- src/data/readiness.py
- tests/test_hole_scanner_readiness.py

Forbidden files:
- executor/order placement
- unrelated source clients

Invariants:
- Backfill never live-authorizes by itself.
- Relevant active market holes block only affected city/date/metric/strategy scopes.
- Legitimate gaps do not block unless strategy requires impossible source.

Tasks:
1. Add readiness impact computation for holes.
2. Mark backfill-origin rows shadow-only until causal/live checks pass.
3. Add tests for active market hole blocking.
4. Add tests for irrelevant hole log-only.

Tests:
- python -m pytest tests/test_hole_scanner_readiness.py -q

Expected failures before fix:
- No readiness effect from holes.

Closeout evidence:
- Before/after readiness rows for a hole fixture.

Rollback:
- Disable hole readiness recomputation.
```

## Stage 8 prompt

```text
Role: Zeus market-topology readiness implementer.

Read first:
- src/data/market_scanner.py
- src/data/polymarket_client.py
- src/engine/cycle_runner.py
- src/contracts/executable_market_snapshot_v2.py
- src/data/readiness.py

Allowed files:
- src/data/market_scanner.py
- src/data/readiness.py
- src/engine/cycle_runner.py
- tests/test_market_topology_readiness.py

Forbidden files:
- src/execution/executor.py unless explicitly instructed
- order signing logic

Invariants:
- Gamma topology stale blocks live candidate entry.
- Quote/orderbook freshness is not satisfied by Gamma metadata.
- Source-contract mismatch/quarantine blocks market.

Tasks:
1. Persist topology freshness status.
2. Add readiness dependency for market_family/event/condition/tokens.
3. Make cycle_runner skip live entry when topology readiness blocked.
4. Add quote-not-checked status for submit-stage blocking.

Tests:
- python -m pytest tests/test_market_topology_readiness.py tests/test_cycle_runner_readiness.py -q

Expected failures before fix:
- cycle_runner does not consume topology readiness.

Closeout evidence:
- Candidate blocked by stale topology fixture.

Rollback:
- Force no-new-entries via global readiness.
```

## Stage 9 prompt

```text
Role: Zeus observation/settlement time-split implementer.

Read first:
- src/data/daily_obs_append.py
- src/data/hourly_instants_append.py
- src/execution/harvester.py
- src/state/db.py
- src/contracts/settlement_semantics.py

Allowed files:
- observation writer files
- harvester/truth writer files
- src/state/db.py
- tests/test_observation_settlement_time_semantics.py

Forbidden files:
- strategy probability math
- executor order submission

Invariants:
- Observation instant, provider fetch, provider revision/display, local day, and settlement recorded/resolved/redeemed are distinct.
- Settlement capture cannot trade when settlement source degraded.
- High/low observation fields remain separate.

Tasks:
1. Add provider revision/display fields where available.
2. Add settlement proposal/resolved/redeem timing fields.
3. Add readiness mapping for settlement-capture dependencies.
4. Add tests for source degraded near resolution.

Tests:
- python -m pytest tests/test_observation_settlement_time_semantics.py -q

Expected failures before fix:
- Missing separate settlement timing fields.

Closeout evidence:
- Fixture showing target local date vs resolved/redeemed times.

Rollback:
- Block settlement-capture strategy.
```

## Stage 10 prompt

```text
Role: Zeus telemetry/operator-control implementer.

Read first:
- src/data/ingest_status_writer.py
- src/data/source_health_probe.py
- src/data/readiness.py
- src/control/control_plane.py
- src/main.py

Allowed files:
- src/data/ingest_status_writer.py
- src/data/readiness.py
- src/control/*
- tests/test_readiness_operator_controls.py

Forbidden files:
- executor/order placement unless only reading block state

Invariants:
- Operator override cannot make incomplete data live.
- Override can only block or downgrade to shadow.
- Logs are not readiness.

Tasks:
1. Add readiness summary export.
2. Add operator block/shadow overrides.
3. Add alert reason aggregation.
4. Add tests for override precedence.

Tests:
- python -m pytest tests/test_readiness_operator_controls.py -q

Expected failures before fix:
- No override consumer.

Closeout evidence:
- Example readiness summary JSON and override test output.

Rollback:
- Remove override reader; keep readiness base state.
```

## Stage 11 prompt

```text
Role: Zeus emergency-patch retirement implementer.

Read first:
- src/ingest_main.py
- src/data/readiness.py
- src/data/release_calendar.py
- tests/test_ingest_main_boot_resilience.py
- tests/test_readiness_state.py
- tests/test_cycle_runner_readiness.py

Allowed files:
- src/ingest_main.py
- tests/*readiness*
- docs/operations/current_daemon_inventory.md

Forbidden files:
- source clients unless a failing test requires a minimal bug fix
- executor/order signing

Invariants:
- Boot must still evaluate data freshness immediately.
- No global table MAX may authorize readiness.
- 18h may remain only as alert threshold, not live gate.
- Do not remove PR #42 behavior before replacement tests pass.

Tasks:
1. Replace global max boot guard with readiness recomputation and typed boot jobs.
2. Keep emergency stale/fresh/empty tests adapted to readiness.
3. Add acceptance evidence that live trading consumes readiness.
4. Remove or demote _BOOT_FRESHNESS_THRESHOLD_HOURS.

Tests:
- python -m pytest tests/test_ingest_main_boot_resilience.py tests/test_readiness_state.py tests/test_cycle_runner_readiness.py -q

Expected failures before fix:
- Old tests expect direct daily_tick; adapt to readiness-safe behavior.

Closeout evidence:
- Before/after boot flow and passing gates.

Rollback:
- Restore PR #42 guard but force global readiness BLOCKED until repaired.
```

# 16. Acceptance gates

## PR #42 temporary acceptance

Accept only if:

* six boot-resilience tests pass;
* boot forced daily jobs are not treated as live readiness;
* reviewers acknowledge `GLOBAL_MAX_RISK`, `TIME_SEMANTICS_RISK`, and `LOCKING_RISK`;
* live trading is not authorized by PR #42 timestamps.

## Minimal launch-safe daemon

Accept only if:

* readiness table/object exists;
* scheduler timezone explicitly UTC or proven correct;
* city/date/metric readiness exists for active markets;
* release calendar blocks pre-release fetches;
* source health and data readiness are separate;
* `cycle_runner` blocks live entries when readiness is blocked;
* market topology readiness blocks stale Gamma/topology;
* quote freshness blocks submit.

## Live trading consuming readiness

Accept only if:

* every candidate trade resolves a readiness object before evaluator/executor;
* missing readiness fails closed;
* stale required dependency blocks;
* strategy-independent stale source logs only;
* blocked reason codes are persisted.

## City/date/metric live eligibility

Accept only if:

* city IANA timezone present;
* target local date window computed;
* DST expected hours verified;
* metric high/low separated;
* source run complete for required valid windows;
* market topology and quote fresh.

## Forecast pipeline accepted

Accept only if:

* issue/release/available/fetch/capture/valid/target times are separate;
* source run identity stored;
* expected vs observed members/steps stored;
* partial run blocks/shadows;
* mx2t6/mn2t6 period semantics documented and tested.

## Observation pipeline accepted

Accept only if:

* observation instant separate from fetch;
* provider revision/display time stored when available;
* local day window explicit;
* DST tested;
* station/source timezone explicit.

## Settlement pipeline accepted

Accept only if:

* target date, provider settlement day, market close, UMA proposal/resolution, Zeus recorded settlement, redeem requested/confirmed are separate;
* degraded settlement source blocks settlement-capture;
* resolved market prevents new entries.

## Backfill accepted

Accept only if:

* backfill rows cannot live-authorize;
* backfill provenance stored;
* readiness recomputation marks backfill `SHADOW_ONLY` unless causal live proof exists.

## Long-term daemon accepted

Accept only if:

* typed job graph;
* release calendar registry;
* DB-backed job/source/readiness state;
* operator controls;
* telemetry;
* duplicate lock protection;
* plane boundaries documented.

## Emergency patch retired

Accept only if:

* no global `MAX(captured_at)` or `MAX(fetched_at)` is used for live readiness;
* boot still recomputes readiness immediately;
* all PR #42 outage scenarios are covered by readiness tests;
* 18h threshold is alert-only or removed.

# 17. Not-now list

* No full distributed rewrite before inventory.
* No destructive schema migration first.
* No daemon-owned trading decisions.
* No quote/orderbook ownership decision without repo evidence.
* No live-authorizing backfill until readiness/provenance gates exist.
* No treating global table freshness as full readiness.
* No treating fetch time as source issue/release/valid time.
* No treating logs or JSON status rollups as readiness.
* No removing PR #42 before replacement passes tests.
* No conflating high and low tracks.
* No using UTC date as target settlement date.
* No source-calendar-free boot fetches.
* No ignoring scheduler timezone.
* No assuming source health green means data complete.
* No allowing stale market topology or quote freshness to pass because weather data is fresh.

# 18. Final verification loop

1. **Did you inspect PR #42?** Yes. I inspected the live PR metadata/diff and noted it is now closed/merged, contrary to the prompt’s stale “open” status. 
2. **Did you reconstruct the exact current patch?** Yes: files, threshold, queries, boot sequence, tests, exclusions, and review risks are reconstructed above.
3. **Did you treat it as emergency patch, not architecture?** Yes.
4. **Did you make time/timezone the primary axis?** Yes.
5. **Did you distinguish issue/release/fetch/capture/valid/local target/readiness time?** Yes.
6. **Did you handle city timezone and DST?** Yes; readiness requires IANA timezone, local window, and DST tests.
7. **Did you reject global table MAX as sufficient if unsupported?** Yes.
8. **Did you define machine-readable readiness?** Yes.
9. **Did you prevent stale/partial data from authorizing live trades?** Yes, via blocked/shadow-only readiness decisions.
10. **Did you include schema, tests, hidden branches, roadmap, and Codex prompts?** Yes.

[1]: https://apscheduler.readthedocs.io/en/stable/userguide.html?highlight=misfire_grace_time "User guide — APScheduler 0.0.post50 documentation"
[2]: https://confluence.ecmwf.int/display/DAC/Dissemination%20schedule "Dissemination schedule - Data and Charts - ECMWF Confluence Wiki"
[3]: https://www.ecmwf.int/en/forecasts/datasets/open-data "Open data | ECMWF"
[4]: https://apps.ecmwf.int/datasets/licences/tigge/?utm_source=chatgpt.com "ECMWF | Use of data accessed via this service"
[5]: https://open-meteo.com/en/docs/historical-weather-api?utm_source=chatgpt.com "🏛️ Historical Weather API | Open-Meteo.com"
[6]: https://dev.meteostat.net/api/stations/hourly?utm_source=chatgpt.com "Hourly Data | Weather Station | JSON API | Meteostat Developers"
[7]: https://www.ogimet.com/?utm_source=chatgpt.com "Entrada de Ogimet"
[8]: https://www.weather.gov.hk/en/cis/climat.htm?utm_source=chatgpt.com "Climatological Information Services｜Hong Kong Observatory(HKO)｜Climate"
[9]: https://docs.polymarket.com/developers/gamma-markets-api/overview?dub_id=Pu93SRzVLZfFlDx3 "Overview - Polymarket Documentation"
[10]: https://docs.polymarket.com/trading/orderbook "Orderbook - Polymarket Documentation"
[11]: https://docs.polymarket.com/developers/resolution/UMA "Resolution - Polymarket Documentation"
