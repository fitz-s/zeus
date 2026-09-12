# NOAA settlement page-truth evidence

All measurements below were taken on 2026-09-12 against live surfaces: the
market descriptions from the Gamma API, the page's own script and feed, and
settled `settlements` rows read with a read-only URI. Dates in the replay window
are 2026-08-23 (the provider migration date) through 2026-09-11.

## Defect

Zeus reconstructed the NOAA settlement value from Ogimet's whole-degree METAR
bodies. The market resolves off the table at
`https://www.weather.gov/wrh/timeseries?site=<ICAO>`. Those are different
quantities, and the difference decided real labels:

| Case | Zeus (Ogimet) | Page feed | Chain bin | Old authority |
|---|---|---|---|---|
| Houston 2026-09-11 high | 93 | 94 | 94-95 | DISPUTED |
| Houston 2026-09-10 high | 90 | 89 | 88-89 | DISPUTED |
| Denver 2026-09-11 high | 90 | 89 | 88-89 | DISPUTED |
| Denver 2026-09-10 high | 90 | 89 | 88-89 | DISPUTED |
| NYC 2026-09-10 high | 88 | 87 | 86-87 | DISPUTED |
| NYC 2026-09-09 low | 72 | 71 | 70-71 | DISPUTED |

## The page is a client-side render of the Synoptic feed

The page's script is `https://www.weather.gov/source/wrh/timeseries/obs.js`.
Read directly, it builds

```
https://api.synopticdata.com/v2/stations/timeseries?STID=<ICAO>
    &showemptystations=1[&units=temp|F,speed|kts,english]
    &recent=<minutes> | &start=YYYYMMDDHHMM&end=YYYYMMDDHHMM
    &complete=1&token=<mesoToken>&obtimezone=local
```

with `units=temp|F,speed|kts,english` for the "US Units w/ kts" view and no
units parameter for the metric view. `mesoToken` comes from
`https://www.weather.gov/source/wrh/apiKey.js`, whose body is
`var mesoToken='<hex>';`.

### Render law, quoted from obs.js

The temperature cell is `Math.round(air_temp_set_1[j])`. Row visibility with
`hourly == 'true'` on an ASOS/AWOS station (`SHORTNAME` is `ASOS/AWOS`, or
`GLOBAL-METAR` which the script rewrites to `ASOS/AWOS`) is:

```js
if (DATA.STATION[0].OBSERVATIONS.sea_level_pressure_set_1[j] !== null) {
  tableData += ...                                 // routine METAR row
} else if (... metar_set_1[j].toUpperCase().startsWith(SITE)) {
  tableData += '<tr><td bgcolor="yellow">...'      // SPECI row, highlighted
}                                                  // everything else: hidden
```

With `hourly == 'false'` every row with a timestamp is appended. `Math.round(x)`
equals `floor(x + 0.5)` for every real x, which is exactly the repo's
`WMO_HalfUp` policy, so no new rounding function was introduced.

### Request-shape facts (paced test, 20:49-20:52Z)

One request per 25 seconds, each shape tried once:

| Request | Headers | Result |
|---|---|---|
| `recent=120` | Referer + Origin + browser UA | 200 OK |
| start/end, 1 day | Referer + Origin + browser UA | 200 OK |
| `recent=4320` (3 days) | Referer + Origin + browser UA | 200 OK, 941 rows |
| start/end, 7 days | Referer + Origin + browser UA | 200 OK, 2053 rows |
| any of the above | none | 403 `Invalid request per token rules` |

A separate burst of roughly 17 twenty-two-day history pulls between 20:17Z and
20:24Z tripped a per-IP quota that refused every subsequent request, correctly
headed ones included, for about ten minutes. Both facts are encoded in the
module: the page's headers are always sent, requests are one small `recent=`
window per station per day in the tick and at most seven days per request in the
backfill with two seconds between them, and a 403 raises `WrhTokenRefused`
rather than reading as "no data".

## Which view applies is stated in the market's own description

Census of the 270 active daily-temperature events in the Gamma payload: 66
events carry the literal sentence

> This market will resolve off of the Hourly Data provided using the "Show
> Hourly Data" button.

Those 66 events cover exactly 11 cities and no others: Atlanta, Austin, Chicago,
Dallas, Denver, Houston, Los Angeles, Miami, NYC, San Francisco, Seattle. The
other 40 city labels in the payload carry no such clause. `settlement_page_view`
is set to `hourly` on exactly those 11 cities.

The clause is the only difference between the two prose forms. NYC's and
London's descriptions for 2026-09-12 are otherwise the same document with the
station, unit and units-toggle button swapped; both are quoted verbatim in
`tests/test_market_scanner_provenance.py` so the antibody test cannot drift from
the real text.

## Replay against chain-winning bins, 2026-08-23..2026-09-11

Scoring the page law from the feed against `settlements.pm_bin_lo/hi` for every
settled cell of the 11 degF cities, counting open-shoulder bins (one bound NULL)
as well as closed ones:

| View | Cells in bin | Cells out of bin |
|---|---|---|
| hourly | 434 | 0 |
| all-data | 302 | 132 |

Both views are scored over the same 434 cells, so the hourly view's perfect
score is not a coverage artifact. The 132 all-data misses reach 10 of the 11
cities rather than concentrating in one station; the per-city miss rate runs from
San Francisco at 20 of 40 cells down to Chicago at 7 of 38. Denver is the one
city with zero all-data misses, which is why Denver alone cannot be used to
discriminate the two views.

The degC cities were scored under the all-data view across London, Toronto,
Seoul, Shenzhen, Mexico City and Panama City: 231 of 238 cells in bin. No
degC-city value changes as a result of this packet, since the existing Ogimet
path already matched those bins. The seven exceptions are two distinct classes:

- Shenzhen 2026-08-23 high: page 32, chain 33. One cell, cause not established.
- Panama City 2026-08-30, 08-31 and 09-01 (high and low, six cells): every one
  of these settled to an open-shoulder *lowest* bracket (`pm_bin_hi` 19 to 24
  degC against page values of 24 to 32), which is the contract's no-data clause
  firing, not a temperature disagreement. Note that the page feed *does* carry
  rows for those days now (5, 22 and 25 rows respectively), so a replay today
  cannot reproduce what the station had published at resolution time. That class
  must stay DISPUTED and must never be forced to match; `daily_extreme` returning
  None on a genuinely dark day is what keeps Zeus from reproducing the bracket
  guess, but it is not a defence against a day that was dark only at resolution
  time. This is a known limitation of the product, recorded here rather than
  papered over.

## Why the feed's value cannot be re-derived from the METAR text

Synoptic's `air_temp_set_1` on a SPECI row is sometimes the body integer even
when the report text carries a tenths-precision T-group. Measured over the
degF-city window: of 563 shown SPECI rows, 70 carry a T-group that disagrees
with the feed's own `air_temp_set_1` by more than 0.2 degF, spread across 10
stations. It reads like a first-transmission decode that later text revisions do
not correct. The page shows the feed value and the chain resolves it, so the
product stores `air_temp_set_1` verbatim and keeps `metar_set_1` in provenance
only. A re-parse would be wrong on those 70 rows by construction.

KATL 2026-09-05 low is the pinned case, chosen because it is one of only four
extremum rows in the window where a T-group re-parse would change the settled
value. The extremum is the SPECI at 19:35 local. The feed says 73.4 degF,
settling to 73, and the chain's bin for that cell is 72-73. That same report's
text carries `T02390211`, i.e. 23.9 degC = 75.02 degF, which would settle 75 and
fall outside the bin. The other three such rows are Miami 2026-09-02 high (feed
90, re-parse 89, chain bin 90-91), San Francisco 2026-08-30 low and Seattle
2026-08-28 low. In every case the feed value is the one the chain resolved.

KATL 2026-08-27 low, the case originally proposed for this test, turns out not to
discriminate: the feed says 71.6 degF and the report's `T02220206` group gives
71.96 degF, and both settle to 72. It is kept as a second fixture day, but the
discriminating assertion rests on 2026-09-05.

The same property cuts the other way for transient reports. Lucknow (VILK)
2026-09-06: the page feed carries 45 rows with max 31 degC and min 26 degC, and
the chain resolved `31°C`. Zeus's stored Ogimet row says high 37 degC from a
report that is no longer present in Ogimet's own raw feed, and that row's
settlement sits DISPUTED against the 31-31 bin. Reading the market's feed does
not carry such a phantom.

## Live backfill dry-run

Run from the packet worktree against a scoped row export of `observations` and
`settlements` for NYC, Houston and Denver (nine days, built by copying the rows
themselves, not the database):

```
$ python3 scripts/backfill_noaa_wrh.py --city NYC --city Houston --city Denver \
    --start 2026-09-09 --end 2026-09-11 --db <scoped-copy>.db
=== noaa_wrh backfill 2026-09-09..2026-09-11 apply=False ===
Denver 2026-09-09 view=hourly n=24/24: HIGH ogimet=82 page=82 (raw 81.86 @ 2026-09-09T16:58:00-0600) bin[82.0,83.0] unchanged (in) | LOW ogimet=57 page=57 (raw 57.20 @ 2026-09-09T06:58:00-0600) bin[56.0,57.0] unchanged (in)
Denver 2026-09-10 view=hourly n=23/23: HIGH ogimet=90 page=89 (raw 88.88 @ 2026-09-10T16:58:00-0600) bin[88.0,89.0] OUT->in FIXES | LOW ogimet=55 page=55 (raw 54.86 @ 2026-09-10T02:58:00-0600) bin[54.0,55.0] unchanged (in)
Denver 2026-09-11 view=hourly n=24/24: HIGH ogimet=90 page=89 (raw 89.42 @ 2026-09-11T15:58:00-0600) bin[88.0,89.0] OUT->in FIXES | LOW ogimet=63 page=62 (raw 62.42 @ 2026-09-11T03:58:00-0600) bin[62.0,63.0] unchanged (in)
Houston 2026-09-09 view=hourly n=27/27: HIGH ogimet=91 page=91 (raw 91.04 @ 2026-09-09T15:53:00-0500) bin[90.0,91.0] unchanged (in) | LOW ogimet=79 page=79 (raw 78.98 @ 2026-09-09T05:53:00-0500) bin[78.0,79.0] unchanged (in)
Houston 2026-09-10 view=hourly n=27/27: HIGH ogimet=90 page=89 (raw 89.06 @ 2026-09-10T11:53:00-0500) bin[88.0,89.0] OUT->in FIXES | LOW ogimet=79 page=78 (raw 78.08 @ 2026-09-10T06:53:00-0500) bin[78.0,79.0] unchanged (in)
Houston 2026-09-11 view=hourly n=29/29: HIGH ogimet=93 page=94 (raw 93.92 @ 2026-09-11T14:53:00-0500) bin[94.0,95.0] OUT->in FIXES | LOW ogimet=79 page=78 (raw 78.08 @ 2026-09-11T00:53:00-0500) bin[78.0,79.0] unchanged (in)
NYC 2026-09-09 view=hourly n=24/24: HIGH ogimet=82 page=83 (raw 82.94 @ 2026-09-09T13:51:00-0400) bin[82.0,83.0] unchanged (in) | LOW ogimet=72 page=71 (raw 71.06 @ 2026-09-09T05:51:00-0400) bin[70.0,71.0] OUT->in FIXES
NYC 2026-09-10 view=hourly n=24/24: HIGH ogimet=88 page=87 (raw 87.08 @ 2026-09-10T14:51:00-0400) bin[86.0,87.0] OUT->in FIXES | LOW ogimet=73 page=73 (raw 73.04 @ 2026-09-10T04:51:00-0400) bin[72.0,73.0] unchanged (in)
NYC 2026-09-11 view=hourly n=24/24: HIGH ogimet=81 page=80 (raw 80.06 @ 2026-09-11T15:51:00-0400) bin[80.0,81.0] unchanged (in) | LOW ogimet=72 page=72 (raw 71.96 @ 2026-09-11T06:51:00-0400) bin[72.0,73.0] unchanged (in)
{
  "apply": false,
  "cities": 3,
  "days_changed_containment": 6,
  "days_changed_value": 7,
  "days_no_rows": 0,
  "days_seen": 9,
  "days_written": 0,
  "end": "2026-09-11",
  "refused_at": null,
  "start": "2026-09-09"
}
```

Every containment change is `OUT->in FIXES`; there is no `in->OUT REGRESSES`
line. The expected values hold: NYC 2026-09-11 reads high 80 and low 72, Houston
2026-09-11 high reads 94, Denver 2026-09-11 high reads 89.

NYC 2026-09-11's high deserves a note because it is the one value that reads
lower than the brief's expectation. The hourly view gives 80 (raw 80.06 at the
15:51 routine METAR) and the all-data view gives 81 (raw 80.6 at a 14:55
five-minute AUTO row). The chain's bin for that cell is 80-81, so both land
in-bin and the day does not discriminate between the views. The 434/434 replay
and the Houston 2026-09-10 case do.

## Tests

`pytest -q -p no:cacheprovider tests/test_hourly_clients_parse.py
tests/test_market_scanner_provenance.py tests/test_scanner_slug_pattern.py
tests/test_cities_config_authoritative.py
tests/test_k2_live_ingestion_relationships.py
tests/test_backfill_scripts_match_live_config.py
tests/test_noaa_wrh_settlement_product.py`

Result: `7 failed, 303 passed`. The same seven fail on the unmodified checkout
at this packet's base commit, with identical causes:

- `tests/test_scanner_slug_pattern.py::test_slug_path_clob_check_rejects_archived`
- `tests/test_k2_live_ingestion_relationships.py::test_R5_wu_observation_upsert_preserves_row_identity`
- `tests/test_k2_live_ingestion_relationships.py::test_R11_forecasts_model_source_map_matches_backfill`
- `tests/test_k2_live_ingestion_relationships.py::test_R12_main_py_defines_all_k2_functions`
- `tests/test_k2_live_ingestion_relationships.py::test_R12_main_py_references_k2_job_ids`
- `tests/test_k2_live_ingestion_relationships.py::test_R13_write_atom_coverage_atomicity_happy_path`
- `tests/test_k2_live_ingestion_relationships.py::test_R13_obs_count_equals_written_count`

The baseline run before any edit was `7 failed, 276 passed` over the same files
minus the new one; no new failure was introduced.

Topology gates: `--task-boot-profiles` is clean, `--scripts` reports 268 issues
both before and after with none against `scripts/backfill_noaa_wrh.py`, and
`--core-claims` and `--fatal-misreads` fail identically before and after (a
missing `_locator_exists` attribute and an unknown `docs_taxonomy` task class,
both pre-existing in the checkout).

## What this evidence does not prove

It proves the settlement product now reproduces the surface the markets name,
and that six of nine recent city-days move from out-of-bin to in-bin. It does
not prove expected value, fills, or realized capital gain. The DISPUTED backlog
closes only after the operator sequence in PLAN.md runs, and the corrected
labels feed calibration only once the walk-forward lanes re-read them.
