# US ASOS 5-minute rows as their own WORLD channel (asos5_<icao>)

Branch: `feat/us-asos5-ingest` (from origin/live 1e3db865f). Status: IN PROGRESS.
Rollback point: 16203945f (anchor). Revert the branch commits; no schema, registry or
config change ships, so the revert is code-only.

## Goal and acceptance

The degF settlement page feed (weather.gov/wrh/timeseries via Synoptic) returns
5-minute ASOS rows at HH:00, :05 ... :55 alongside the hourly and SPECI rows. The
`noaa_wrh` route keeps only `is_official_report` rows, so the 5-min rows were
discarded. Write them to WORLD `observation_prints` as their own channel without
changing any q, Day0 fact, settlement value or existing channel content.

Acceptance:
- `asos5_<icao>` rows = minute % 5 == 0 and not `is_official_report`, from the same
  batch payload, station-validated by the same parser, route unit, value verbatim,
  publish = observation clock UTC, fetched = the batch receipt.
- `noaa_wrh_<icao>` output is byte-identical for the same payload.
- G10 `record_page_print_absences` sees only the page channel's clocks.
- The asos5 write is its own SAVEPOINT; any Exception rolls back to it, never the
  page prints.
- No `OBSERVATION_REACTION_TRACE` / `SOURCE_COMMITTED` for asos5 rows.
- No existing reader admits `asos5_*` (inventory below, plus a test).

## Design

Shape: one parser split, one tick block.
- `parse_station_payload` (noaa_wrh branch) also collects the off-report 5-min grid
  rows into a second tuple. The tuple that `parse_station_payload` returns, and so the
  page prints, is unchanged. The asos5 samples come from the same `rows_from_payload`
  call, so station identity, unit label and array integrity are validated once.
- `fetch_station_temperature` keeps its return type. The noaa_wrh branch attaches the
  asos5 samples as an attribute of the returned tuple. Every caller and monkeypatch
  that treats the result as a tuple keeps working. A separate return would have to
  change every caller and every test that patches the seam (6 tests on live, 9 on
  the pending branch).
- `_day0_current_temperature_source_tick` writes the asos5 samples after the page
  prints and after the G10 block, in `SAVEPOINT asos5_prints`. It catches Exception and
  issues `ROLLBACK TO` then `RELEASE`. The rows join the same WORLD commit and are not
  added to `committed_rows`, so no trace is emitted and `inserted` / `advanced` / wake
  are unchanged.

Why the channel is not a registry route (decision 4):
- A route in `config/physical_current_sources.json` is a poll unit. It gets its own
  `_physical_source_next_poll` clock, its own fetch-cache key, its own
  `_day0_current_temperature_source_tick` call and its own wake key. It also enters
  `physical_current_sources_for_city`, which adds its channel to the admitted
  channel set of `_latest_authorized_day0_fact`, `day0_current_temperature_channels`
  (current state) and `valid_station_print` (`CHANNELS`). That is the reader
  admission this change must not make.
- The 5-min rows come from a payload the noaa_wrh route already fetched. A route
  would add a second consumer of the same batch, with no new HTTP request but a
  second poll clock, and it would admit the channel into belief.
- Deriving the rows inside the existing route tick keeps acquisition, receipt clock
  and station validation identical to the page prints, by construction.

Why no trace emission (decision 5): no law consumes asos5 yet, and every
`SOURCE_COMMITTED` line becomes a reaction-chain join candidate. Volume avoided: see
Storage.

## Provenance audit (files modified)

| File | Last change | Verdict | Basis |
|---|---|---|---|
| src/data/station_temperature_adapters.py | c696081f2 2026-10-07 | CURRENT_REUSABLE | noaa_wrh branch reuses `rows_from_payload` (station/array validation, 3b4a3ba3b) and `_sample`; native value unconverted; batch receipt = `_cached_fetch` receipt. Matches G5/G9/G10 law. |
| src/ingest_main.py `_day0_current_temperature_source_tick` | 1e3db865f 2026-10-08 | CURRENT_REUSABLE | WORLD-only lease + mutex (no INV-37 cross-DB write); G10 savepoint pattern current as of 1e3db865f. |
| src/data/noaa_wrh_timeseries.py | 0cb36fc83 2026-10-02 | CURRENT_REUSABLE, not modified | `is_official_report` = routine or station-prefixed SPECI; 5-min rows carry the `METAR ` prefix and no SLP, so they are exactly the off-report rows. |
| config/physical_current_sources.json | not modified | CURRENT_REUSABLE | 48 noaa_wrh routes, 11 degF hourly + 37 degC all. |

## Reader inventory

Method: `rg -n observation_prints src scripts` (26 files), every SQL read traced to the
`source_channel` value it binds, then the named readers read in full. No reader in src/
or scripts/ uses a `LIKE`/`NOT IN`/`!=` channel pattern that could match `asos5_*`; the only
`LIKE` is `ogimet_metar_%` (below). Verdict: **no existing reader admits `asos5_*`**. Three
non-filtering readers carry no channel predicate: (a), (b) and (c) below.

| Reader | file:line | Channel restriction | asos5 |
|---|---|---|---|
| Day0 fact reducer `_latest_authorized_day0_fact` | src/data/replacement_forecast_current_target_plan.py:1482 | `source_channel IN (...)`. The set is `{noaa_wrh_<icao>}` (settlement, :1432) or `{noaa_wrh_<icao>, ogimet_metar_<icao>, aviationweather_metar}` (physical), plus registry routes whose provider is not noaa_wrh (:1457, :1463). | excluded: not a registry route |
| Current state `read_day0_current_temperature_state` / `day0_current_temperature_channels` | src/data/day0_hourly_vectors.py:4442, :4446 | Fixed noaa tuple plus `route.source_channel` of `physical_current_sources_for_city` | excluded: not a route |
| Fast residual `build_fast_station_residual_likelihood` | src/data/day0_fast_obs.py:750, :789 | `IN (noaa_wrh_<icao>, aviationweather_metar)` | excluded |
| `latest_fast_station_extreme_c` | src/data/day0_fast_obs.py:556 | `= FAST_OBS_SOURCE_ID` (`aviationweather_metar`, :99) | excluded |
| `read_noaa_fast_obs_context_from_ledger` | src/data/day0_fast_obs.py:2611 | `= FAST_OBS_SOURCE_ID` | excluded |
| fast-obs per-station read | src/data/day0_fast_obs.py:3077 | `IN (aviationweather_metar, ogimet_metar_<icao>)` | excluded |
| day0_fast_obs city-wide (~3710) | src/data/day0_fast_obs.py:3713, :3835 | `= FAST_OBS_SOURCE_ID` | excluded |
| day0_fast_obs city-wide (~3936) | src/data/day0_fast_obs.py:3923 (cursor), :3940 | `= FAST_OBS_SOURCE_ID` | excluded |
| same-station preliminary survival | src/data/day0_observation_reader.py:1017 | `IN (awc, ogimet_<icao>)` plus a Python branch on those two | excluded |
| hard-fact exit `_durable_fast_tail_hard_fact_evidence` | src/execution/day0_hard_fact_exit.py:1212, ~1238 | `= source.source_id`, which must equal `FAST_OBS_SOURCE_ID` | excluded |
| oracle anomaly `_page_running_extremes_from_ledger` | src/data/day0_oracle_anomaly.py:641, ~657 | `= noaa_wrh_<icao>` | excluded |
| trigger `day0_extreme_updated` | src/events/triggers/day0_extreme_updated.py:573, :1056 | Reads through the fact reducer with `require_settlement_channel=True`. `_source_matches_config` (:1056) admits `ogimet_metar_*` / `noaa_wrh_*` only. | excluded at both |
| finality `day0_evidence_finality` | src/events/day0_authority.py:317 | Prefix `noaa_wrh_` gives MONOTONE; anything else unknown gives UNKNOWN_FINALITY | would not be absorbing even if admitted |
| adapter fast-residual check (read only) | src/engine/event_reactor_adapter.py:37194 | Python `settlement_channel.startswith("noaa_wrh_")` on the residual dict; that dict's channel comes from :750 | excluded upstream |
| adapter legacy current temp | src/engine/event_reactor_adapter.py:~48427 | `IN (ogimet_metar_<icao>, aviationweather_metar)` for noaa | excluded |
| `day0_resolver_terminal_residual` | src/calibration/day0_resolver_terminal_residual.py:697 (`_station_and_channel_source`) | String classifier: hko / `aviationweather_metar` / `ogimet_metar_` prefixes, else None | not a DB read; asos5 returns None |
| its fitter `read_metar_renderings` | scripts/fit_day0_resolver_terminal_residual.py:221 | `= aviationweather_metar OR LIKE 'ogimet_metar_%'`, plus a Python check | excluded |
| harvester / settlement truth | src/execution/harvester.py | no `observation_prints` reference | n/a |
| daily_obs_append | src/data/daily_obs_append.py:2344, :2548 | `print_revisions(match={... source_channel: noaa_wrh_<icao> / hko_rhrread_spot})` | excluded by match |
| hole_scanner | src/data/hole_scanner.py | reads `observation_instants` only | n/a |
| etl_temp_persistence | scripts/etl_temp_persistence.py | no `observation_prints` reference | n/a |
| G10 `record_page_print_absences` | src/state/fact_revocation.py:98 | `= source_channel` argument (the page channel) | excluded; the test also pins `returned_clocks` |
| ingest tick newest-row read | src/ingest_main.py:2231 | `= route.source_channel` | excluded |
| HKO replay | src/ingest_main.py:~2891 | `= 'hko_current_1min_mean'` | excluded |
| `scripts/hko_ingest_tick.py:293` | | `= proof["source"]` (HKO) | excluded |
| `scripts/audit_page_print_absences.py:25` | | Joins prints from `fact_revocations` rows (PAGE_PRINT_ABSENT); asos5 is never tagged | excluded |
| trace `_unique_print_reference` / `_consumed_print_reference` | src/runtime/observation_reaction_trace.py:172, :191 | `= state['source']` / an exact carried rowid | excluded |

Known non-filtering readers:
- (a) `src/ingest/forecast_live_daemon.py:2481` `SELECT COALESCE(MAX(id), 0) FROM
  observation_prints`. This is a component of the discovery revision key (:2561-2563,
  "unchanged_discovery_revision" short-circuit). The coordinator measured that the key
  already changes in 1,346 of 1,440 minutes per day. My read-only check on live WORLD agrees
  on order: by receipt minute, 1,130 (10-07) and 1,192 (10-08) minutes have at least one new
  print, while the degF page prints alone touch only 108/131 minutes. asos5 adds rows in
  minutes where the degF batch delivers a new 5-min record, and those minutes are already
  almost all covered. Also the key embeds the current hour (:2490), so it changes at least
  hourly. **No change needed.**
- (b) `src/runtime/observation_reaction_trace.py:114` `print_revisions` rowid range. Its
  range callers (ingest_main.py:704, :823, daily_obs_append.py:2344, :2548,
  scripts/obs_live_tick.py:557) take the high-water inside their own transaction, and the
  asos5 rows are written only under the WORLD mutex + lease of the physical-current tick,
  so an asos5 row never falls inside another writer's range under the lock. The two
  readers that read the high-water outside the lock (daily_obs_append :2344, :2548) filter by
  `match` on their own channel. The tick itself emits only `committed_rows`, which never
  contains asos5 (item 5). **Handled.**
- (c) Data-owned collector artifacts/fast_obs_audit/round5_closeout.py:836, :910 (no channel
  predicate). Data excludes `asos5_%` on its own branch. **Not touched here.**
- Analysis scripts that copy every row (artifacts/fast_obs_audit/benchmark_observation_index.py:18,
  :35) are offline benchmarks with no live consumer.

Test proof: `tests/data/test_asos5_ingest.py::test_no_reader_admits_asos5_rows` seeds page
prints plus AWC METAR at 20 paired clocks/day for 3 days. It records the fact reducer
(HIGH/LOW x settlement/physical), the current state (+identity), the fast residual
(HIGH/LOW) and the oracle anomaly. Then it inserts >500 asos5 rows at 140 F and -40 F on
every 5-min grid clock of the day and on every paired clock, and asserts the results are
`==`. Mutation check: adding asos5 to the reducer's settlement set, its physical set, the
current-state tuple, the residual `IN` or the oracle predicate each fails this test.

## Storage

Measured on the saved 5-day degF batch (g9_live_verify_payload.json, 9 stations,
2026-09-30..10-05). Each row is inserted into a scratch SQLite with the real schema, then
VACUUM + dbstat:
- rows: 12,186 asos5 rows; full interior days carry 259-288 per station (mean 284.5),
  so ~285 rows/station/day, against a 288 maximum. The batch has 11 degF stations
  (KBKF Denver carried no 5-min rows in the 10-07 capture: 9 rows, all official).
- rows/day: ~285 x 10-11 stations = **~2,900-3,100 rows/day**. Dedup: `append_print`
  suppresses a re-receipt of the same clock and value (the 60 s poll sees each 5-min
  row ~36 times in its 3 h window, and stores it once). A value revision of one clock
  appends one more row. The tick re-poll test pins `inserted == 0` and no new asos5 rows.
- bytes/row: raw_report mean 356 B (no station_reference block; the page rows carry ~3.8 KB
  of it). Table + 2 indexes = **~589 B/row** (dbstat: table 5.56 MB, identity index 1.11 MB,
  city/publish index 0.49 MB for 12,186 rows).
- growth: 3,000 x 589 B ~ **1.8 MB/day, ~0.65 GB/year** in WORLD (now 104.9 GB). For scale,
  WORLD `observation_prints` currently adds ~7.4-8.2k rows/day (all channels).
- degC routes: the 37 `all`-view routes already store every row the page shows. Read-only
  check of live WORLD (last ~3M ids): of 22,823 degC channel-hours, the most clocks seen in one
  hour is 9, and only 1 hour has >=6 five-minute-grid clocks. No 5-minute ASOS cadence
  exists on those pages, and the asos5 split takes only rows the view does not show, so
  `all` routes emit nothing (test `test_metric_all_view_routes_emit_no_asos5`).

Trace volume avoided (decision 5): live `zeus-ingest.log` holds 29,788 SOURCE_COMMITTED lines
since 2026-10-04 16:56 (~5 days), mean 1,136 B/line. Emitting one per asos5 row would add
~3,000 lines/day (~3.4 MB/day of log) and a matching number of reaction-chain join candidates,
roughly half again over today's ~6,000/day.

## Tests and evidence

(filled in step 5)

## Merge-tree

(filled in step 5)

## Residuals

(filled in step 5)
