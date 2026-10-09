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

(filled in step 4)

## Storage

(filled in step 4)

## Tests and evidence

(filled in step 5)

## Merge-tree

(filled in step 5)

## Residuals

(filled in step 5)
