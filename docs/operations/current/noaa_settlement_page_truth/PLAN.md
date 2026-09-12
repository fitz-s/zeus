# noaa_settlement_page_truth -- Plan

Date: 2026-09-12
Status: active

## Background

Polymarket's 48 NOAA cities resolve off the table rendered at
`https://www.weather.gov/wrh/timeseries?site=<ICAO>`. Zeus reconstructed that
number from Ogimet's whole-degree METAR bodies instead, which is a different
quantity: for the 11 US degF cities the reconstruction disagreed with the
chain-winning bin on 15-37% of days (Houston 2026-09-11: ours 93 degF, chain
94-95 degF). Labels, disputes and Day0 finality were therefore computed on the
wrong law.

Two facts make the page's value unreachable from Ogimet. First, the page shows
`Math.round(air_temp_set_1[j])` from its own feed, and that feed's SPECI rows
sometimes carry a first-transmission decode that the raw METAR text no longer
reproduces. Second, the market description selects which rows the page shows:
the 11 US degF cities say the market resolves off the "Show Hourly Data" view
(routine METAR plus station-prefixed SPECI rows only) and the other 37 do not
(every row). The two views give different daily extrema, and no METAR
reconstruction can express that distinction because the per-report minute does
not survive Ogimet's hour bucketing.

This packet makes the market's own feed the settlement product for NOAA cities
and records the view per city, without removing the Ogimet lane.

## Scope

_See sibling scope.yaml for machine-readable scope._

## Deliverables

- Read the page's feed through a new client that reproduces the render law and
  returns the raw extremum, leaving rounding to `SettlementSemantics`.
- Carry the contract's view per city as `City.settlement_page_view`, set to
  `hourly` on exactly the 11 clause-carrying cities.
- Reject at the scanner any NOAA market whose description's view clause
  disagrees with the configured view, the same way a station mismatch is
  rejected.
- Write the page product daily as `observations.source='noaa_wrh_<station>'`
  through the same atom-pair sink the Ogimet path uses, on the same shard
  schedule and before the 45-minute settlement cron.
- Prefer the page row over the Ogimet row in both harvester settlement lookups,
  the rebuild script and the dispute drain, stamping the `data_version` of the
  row actually used.
- Backfill past dates with a dry-run-by-default CLI that reports per city and
  date whether the change moves the rounded value out of the chain's bin.
- Keep the Ogimet daily lane as the hourly/history mirror and as the row
  settlement reads when the page feed produced nothing.

## Non-goals

- No change to rounding: `wmo_half_up` is already what `Math.round` computes.
- No change to degC-city values. The existing Ogimet path already matched chain
  bins for them, and the replay confirms the page product agrees.
- No guessing on a station-dark day. The contract resolves a no-data day to the
  lowest bracket; Zeus writes nothing and the market stays DISPUTED.
- No schema field in `architecture/city_truth_contract.yaml`. The existing
  `website_api_product_divergence` caution flag already names this class, and
  the view itself is runtime config, not a contract-schema axis.

## Verification

- `pytest -q tests/test_noaa_wrh_settlement_product.py`
- `pytest -q tests/test_market_scanner_provenance.py tests/test_scanner_slug_pattern.py`
- `pytest -q tests/test_cities_config_authoritative.py tests/test_hourly_clients_parse.py`
- `pytest -q tests/test_k2_live_ingestion_relationships.py tests/test_backfill_scripts_match_live_config.py`
- `python3 scripts/topology_doctor.py --core-claims --json`, `--fatal-misreads --json`,
  `--task-boot-profiles --json`, `--scripts --json`
- Live dry-run of `scripts/backfill_noaa_wrh.py` against a scoped row export.

## Post-landing operator sequence

Run in this order. Each step's output is the precondition for the next. Every
step below acts on `state/zeus-forecasts.db`, which is where `observations`,
`settlements` and `settlement_outcomes` are authoritative after the K1 split; the
`world` copies of those tables are `legacy_archived` ghosts holding zero rows
(verified 2026-09-12). A tool that writes the world copy is a silent no-op.

1. `python3 deploy/deploy_live.py restart all` — the daily tick, both harvester
   copies and the scanner all changed, so a sidecar-only restart would leave
   the mesh writing and reading different laws.
2. `python3 scripts/backfill_noaa_wrh.py --start 2026-08-23 --end <today>` and
   read the dry-run report. Every containment change should be `OUT->in FIXES`;
   an `in->OUT REGRESSES` line means stop and investigate, not proceed.
3. `python3 scripts/backfill_noaa_wrh.py --start 2026-08-23 --end <today> --apply`.
   If it stops with a refused token, resume with a later `--start` once the
   per-IP quota window has passed rather than retrying immediately.
4. `python3 -m scripts.backfill_harvester_settlements --days 30` to re-resolve
   settlement truth from the now-preferred page rows. This runs the ingest truth
   writer (`src/ingest/harvester_truth_writer.py::write_settlement_truth_for_open_markets`)
   on the forecasts DB, which rewrites `settlements`, `settlement_outcomes` and
   `market_events` through `SettlementSemantics`. No new entry point was needed:
   `_stable_settlement_truth_matches` compares `data_version`, so an
   already-VERIFIED row is re-resolved as soon as a higher-ranked `noaa_wrh_`
   observation appears. The live hourly ingest tick does the same work on its own
   30-day window, so this step only shortens the wait.
5. `python3 scripts/drain_settlement_disputes.py` to close any DISPUTED backlog
   the wrong values left behind, grading from venue resolution.
6. Verify in `state/zeus-forecasts.db`: Houston 2026-09-11 high and Denver
   2026-09-11 high are VERIFIED and in-bin, with
   `data_version='noaa_wrh_timeseries_v1'`.

Do **not** use `scripts/rebuild_settlements.py` for step 4. It opens the world DB
and writes the ghost `settlements` table, so it cannot change what the settlement
readers see. That is a pre-existing defect in that script, out of this packet's
scope; it is recorded here so the next operator does not reach for it.
