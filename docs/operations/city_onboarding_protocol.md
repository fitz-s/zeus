# Zeus City Onboarding Protocol
<!-- Created: 2026-05-24 -->
<!-- Last reused/audited: 2026-05-24 -->
<!-- Authority basis: src/config.py (City schema + validation), tests/test_cities_config_authoritative.py (K0 freeze),
     src/data/tier_resolver.py (I1-I4 invariants), docs/reference/zeus_data_and_replay_reference.md,
     docs/reference/zeus_vendor_change_response_registry.md §1 (PM as only settlement authority),
     docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md (FULL_CONTRIBUTOR / 00Z selection),
     architecture/db_table_ownership.yaml (K1 DB split), architecture/city_truth_contract.yaml,
     scripts/backfill_wu_daily_all.py, scripts/backfill_obs_v2.py -->

## Purpose

Step-by-step checklist for adding a new city to Zeus from scratch.
The protocol is sequenced: each step's output is a precondition for the next.
**Do not execute** without operator sign-off on the cities.json entry and
a reviewer-passed evidence plan (see §8).

This protocol supersedes any informal city-addition instructions in archived
packets. When in conflict with an authority doc cited in the header, the
authority doc wins.

---

## §0. Preconditions (before writing a single line)

0.1. **Gamma verification** — confirm the new city has an _open_ (active=True,
     closed=False) Polymarket market via gamma-api, and read the market
     `description` field to extract:
     - Settlement provider (Wunderground, NOAA, HKO, …)
     - Station name and ICAO code
     - Resolution URL
     - Temperature unit (C or F)

     The market description is the **only authority** for settlement routing.
     Never infer station from the city name or nearest airport tables.

0.2. **No existing slug** — confirm the city is not already in `config/cities.json`
     under a different slug or alias. (`grep -r "jinan" config/cities.json`)

0.3. **Current market status note** — if all open markets are for already-past
     target dates (active=True but target_date < today), the city is in
     settlement-pending state. Document this. Polymarket does not guarantee
     future rounds; proceed with onboarding per operator direction.

---

## §1. cities.json Entry (config/cities.json)

**File:** `config/cities.json`
**Schema validated by:** `src/config.py:load_cities()` + `tests/test_cities_config_authoritative.py`

Required fields per `City` dataclass and K0 test:
```json
{
  "name": "<Display name>",
  "aliases": ["<Display name>"],
  "slug_names": ["<polymarket-slug>"],
  "noaa": null,
  "lat": <float, airport lat>,
  "lon": <float, airport lon>,
  "wu_station": "<ICAO 4-char>",
  "wu_pws": null,
  "meteostat_station": null,
  "airport_name": "<Full airport name> Station",
  "country_code": "<ISO-2 uppercase>",
  "settlement_source": "https://www.wunderground.com/history/daily/<CC>/<city>/<ICAO>",
  "settlement_source_type": "wu_icao",
  "timezone": "<IANA tz>",
  "cluster": "<Display name>",
  "unit": "C",
  "historical_peak_hour": <float 10.0–20.0>,
  "diurnal_amplitude_c": <float>,
  "weighted_low_calibration_eligible": true
}
```

Rules enforced at load time (`src/config.py:329–340`) — **hard KeyError/TypeError raises**:
- `cluster` must be present.
- `unit`, `timezone`, `wu_station`, `country_code` must be present (presence check only;
  a null/empty value for `wu_station` is allowed in config for HKO-type cities).
- `weighted_low_calibration_eligible` must be present and an explicit JSON `bool`.
- `lat`/`lon` must be present.

Rules that emit `logger.warning` only (no hard raise) — caught by K0 test assertions:
- `wu_station` must match regex `^[A-Z]{4}$` (warning if violated; test asserts it).
- `historical_peak_hour` should be in `[10.0, 20.0]` (warning if outside; test asserts).
- `settlement_source_type` value is validated by K0 test (`test_all_non_special_cities_are_wu_icao`)
  but is not a load-time hard raise.

**Coordinate rule:** `lat`/`lon` must match the WU settlement ICAO airport
station, not downtown or nearest-grid coordinates. Use the airport's published
ICAO coordinates.

**slug_names rule:** must include the Polymarket slug exactly as it appears in
the gamma event slug (`highest-temperature-in-<slug>-on-…`). Add all known
aliases.

**settlement_source URL rule (2026-05-24 audit):** use the URL exactly as
quoted in the Polymarket market description. WU city path segments sometimes
differ from the city's common name (e.g. Toronto CYYZ is
`/ca/mississauga/CYYZ`, not `/ca/toronto/CYYZ`). The market description is
authoritative; our `settlement_source` field must match it byte-for-byte
(excluding trailing slash) so scraper path validation and source-contract
monitors don't flag a mismatch.

**China city specifics:** `timezone: "Asia/Shanghai"`, `unit: "C"`.
Peak hour ~15.0, diurnal_amplitude_c per nearest analogous city
(Jinan/Zhengzhou: ~10.0 C, similar to Beijing's 10.5).

After editing `cities.json`, run:
```bash
cd <repo> && source .venv/bin/activate
python -c "from src.config import cities_by_name; print(len(cities_by_name), 'cities loaded')"
```
If it raises, fix the JSON before proceeding.

---

## §2. Test Suite — K0 City Count + Invariants

**Files:**
- `tests/test_cities_config_authoritative.py` — K0 freeze: `test_all_51_cities_present()` pins the count.
- `tests/test_tier_resolver.py` — I1-I4: tier coverage of every city in config.

**Required changes:**
1. `tests/test_cities_config_authoritative.py:27` — update `assert len(cities_by_name) == 51`
   to the new count (+1 or +2 per city added).
2. `tests/test_cities_config_authoritative.py` `test_all_non_special_cities_are_wu_icao()` —
   no change needed for standard WU cities; add to `special_types` dict only if
   the new city is NOAA, HKO, or CWA.

**No other test changes are required** — `CITY_STATIONS` in
`scripts/backfill_wu_daily_all.py` is derived from `cities_by_name` at import
time (`_city_stations_from_config()`), and `TIER_SCHEDULE` in
`src/data/tier_resolver.py` is built from `cities_by_name` at import time
(`_build_tier_schedule()`). Both auto-include the new city as soon as the
cities.json entry is valid.

Verification:
```bash
pytest tests/test_cities_config_authoritative.py tests/test_tier_resolver.py -v
```

---

## §3. Daily Observations Backfill (WU daily history → `observations` table)

**DB:** `zeus-world.db` (WORLD_CLASS)
**Table:** `observations`
**Script:** `scripts/backfill_wu_daily_all.py`

Backfills WU daily high + low for the new city up to the settlement-ready
window (typically start_date = earliest available WU history or
calibration-start date, end_date = today − publication_lag_days).

```bash
cd <repo> && source .venv/bin/activate
python scripts/backfill_wu_daily_all.py \
    --cities "<CityName>" \
    --days 365 \
    --dry-run
# Review output; remove --dry-run to execute
python scripts/backfill_wu_daily_all.py \
    --cities "<CityName>" \
    --days 365
```

Note: `CITY_STATIONS` is auto-derived from `cities_by_name`; no manual edit
is needed in the backfill script.

Verify:
```bash
python -c "
import sqlite3
conn = sqlite3.connect('state/zeus-world.db')
rows = conn.execute(\"SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM observations WHERE city=? AND authority='VERIFIED'\", ('<CityName>',)).fetchone()
print(rows)
"
```

---

## §4. Hourly Observations Backfill (v2 → `observation_instants_v2`)

**DB:** `zeus-world.db` (WORLD_CLASS — confirmed by `architecture/db_table_ownership.yaml` line 725 and `scripts/backfill_obs_v2.py:113` DEFAULT_DB_PATH)
**Table:** `observation_instants_v2`
**Script:** `scripts/backfill_obs_v2.py`
**Source tier:** WU ICAO cities → Tier 1 (wu_icao); auto-assigned by
`src/data/tier_resolver.py:_build_tier_schedule()` from `settlement_source_type`.

Purpose: hourly feature rows used for Day0 nowcast and calibration training.

**IMPORTANT:** Do NOT pass `--db state/zeus-forecasts.db`. Use the script default
(world.db). Passing forecasts.db would misroute rows to the wrong DB.

```bash
python scripts/backfill_obs_v2.py \
    --cities "<CityName>" \
    --start 2024-01-01 \
    --end $(date +%Y-%m-%d) \
    --data-version v1.wu-native \
    --dry-run
# Review; remove --dry-run
python scripts/backfill_obs_v2.py \
    --cities "<CityName>" \
    --start 2024-01-01 \
    --end $(date +%Y-%m-%d) \
    --data-version v1.wu-native
```

Monitor progress:
```bash
tail -f state/obs_v2_backfill_log.jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    r = json.loads(line)
    if r.get('city') == '<CityName>':
        print(r.get('city'), r.get('date_range'), r.get('status'), r.get('written'))
"
```

Verify (world.db):
```bash
python -c "
import sqlite3
conn = sqlite3.connect('state/zeus-world.db')
rows = conn.execute(\"SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM observation_instants_v2 WHERE city=?\", ('<CityName>',)).fetchone()
print(rows)
"
```

**Rollback detection — misrouted obs write:** If you accidentally wrote to
`zeus-forecasts.db`, detect it via:
```bash
python -c "
import sqlite3
# Check WRONG db — should be 0 rows for this city
c = sqlite3.connect('state/zeus-forecasts.db').execute(
    \"SELECT COUNT(*) FROM observation_instants_v2 WHERE city=?\", ('<CityName>',)).fetchone()[0]
print('forecasts.db count (should be 0):', c)
# Check CORRECT db — should have backfilled rows
c = sqlite3.connect('state/zeus-world.db').execute(
    \"SELECT COUNT(*) FROM observation_instants_v2 WHERE city=?\", ('<CityName>',)).fetchone()[0]
print('world.db count (should be >0):', c)
"
```
If forecasts.db count > 0, DELETE those rows and re-run with default DB path.

---

## §5. Ensemble/Forecast Backfill (→ `ensemble_snapshots_v2`)

**DB:** `zeus-forecasts.db` (FORECAST_CLASS)
**Table:** `ensemble_snapshots_v2`

### 5a. Previous-runs forecast history (Open-Meteo)

**NOTE on flags:** `backfill_openmeteo_previous_runs.py` uses `--start-date`/`--end-date`
(not `--start`/`--end`). The sibling script `backfill_obs_v2.py` uses `--start`/`--end`
(short form). Verify each script's flags independently via `--help` before running.

```bash
python scripts/backfill_openmeteo_previous_runs.py \
    --cities "<CityName>" \
    --start-date 2024-01-18 \
    --end-date $(date +%Y-%m-%d) \
    --leads 1,2,3,4,5,6,7 \
    --dry-run
python scripts/backfill_openmeteo_previous_runs.py \
    --cities "<CityName>" \
    --start-date 2024-01-18 \
    --end-date $(date +%Y-%m-%d) \
    --leads 1,2,3,4,5,6,7
```

Note: `openmeteo_previous_runs` retro window starts 2024-01-18 (per
`config/data_availability_exceptions.yaml:model_retro_starts`).

### 5b. ECMWF Open Data backfill (extrema authority rows)

**CRITICAL — 00Z vs 12Z / FULL_CONTRIBUTOR requirement (added 2026-05-24):**

The ECMWF Open Data FULL_CONTRIBUTOR run selection is governed by
`src/data/forecast_extrema_authority.py` and documented in
`docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md`.

**For far-east cities (UTC+7 to UTC+9, including all China / Korea / Japan cities):**
- The local calendar day peaks before UTC noon.
- The **00Z run** is the peak-capturing run (FULL_CONTRIBUTOR,
  `contributes_to_target_extrema=1`, `attribution_status` in POSITIVE_ATTRIBUTION_STATUSES).
- The **12Z run** is post-peak → NON_CONTRIBUTOR and is ranked lower by the reader.
- Backfill MUST include the 00Z run for these cities. A 12Z-only backfill will
  produce cold-biased forecasts.

**Current ECMWF Open Data data_versions** (from
`src/contracts/ensemble_snapshot_provenance.py`):
- HIGH: `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` (`ECMWF_OPENDATA_HIGH_DATA_VERSION`)
- LOW: `ecmwf_opendata_mn2t3_local_calendar_day_min_v1` (`ECMWF_OPENDATA_LOW_DATA_VERSION`)
- LOW contract window: `ecmwf_opendata_mn2t3_local_calendar_day_min_contract_window_v2`

Only these data_versions satisfy `CURRENT_EXTREMA_AUTHORITY_REQUIRED_DATA_VERSIONS`.
Any other version is fail-closed UNKNOWN at the forecast reader.

**Legacy data_versions** (mx2t6/mn2t6) will pass through as
`LEGACY_NULL_PASSTHROUGH` but are NON_CONTRIBUTOR for effective signal quality.
New cities should NOT backfill into legacy versions.

Backfill the ECMWF Open Data rows via the live ingest daemon after city
onboarding is complete (the live daemon will pick up the new city automatically
since it iterates `cities_by_name`). For historical backfill beyond the
Open Data ~10-day rolling window, use `scripts/backfill_ecmwf_*.py` if
available for the target date range, or accept that historical Open Data
ensemble rows will be sparse. Check:
```bash
python -c "
import sqlite3
conn = sqlite3.connect('state/zeus-forecasts.db')
rows = conn.execute('''
    SELECT issue_time, contributes_to_target_extrema, attribution_status, COUNT(*)
    FROM ensemble_snapshots_v2
    WHERE city=?
    GROUP BY issue_time, contributes_to_target_extrema, attribution_status
    ORDER BY issue_time DESC LIMIT 20
''', ('<CityName>',)).fetchall()
for r in rows: print(r)
"
```

---

## §6. Settlement Backfill (→ `settlements` / `settlements_v2`)

If the new city has already had settled Polymarket markets before this
onboarding session, settlements can be bootstrapped from historical gamma data.

**DB:** `zeus-forecasts.db` (FORECAST_CLASS)
**Table:** `settlements_v2` (current canonical path as of K1)

This requires:
1. Observations (§3) to be present for the settled dates.
2. `scripts/backfill_harvester_settlements.py` or the live harvester (if
   `ZEUS_HARVESTER_LIVE_ENABLED=1`) to write settlement rows.

For cities whose markets are in settlement-pending state (active=True,
target_date < today), settlements will be written automatically by the
live harvester once WU finalizes the daily data.

---

## §7. data_coverage LEGITIMATE_GAP Seeds

**File:** `config/data_availability_exceptions.yaml`

For a brand-new city, observation rows before the WU historical archive
coverage start date are genuine gaps. If the hole scanner will flag these,
add a per-city exception:

```yaml
# Under city_onboard_legitimate_gaps: section (create if absent)
city_onboard_legitimate_gaps:
  - city: "<CityName>"
    data_source: "wu_icao_history"
    before_date: "<YYYY-MM-DD>"  # first date WU has data for this station
    reason_code: "city_onboard_pre_archive_coverage"
```

This prevents the hole scanner from continuously retrying pre-archive dates.

---

## §8. Calibration Warmup

**No explicit warmup step is required before a new city can produce signals.**
The Platt model is bucketed by `cluster:season`. A new city whose cluster
name equals its own city name (all current Zeus cities) gets its own
calibration bucket. Until sufficient pairs accumulate (target: ≥30 pairs per
`cluster:season` bucket for a non-degenerate Platt fit), the calibration will
use the bucket's bootstrap interval, which will be wide.

**Trading gate:** new cities are gated by the standard signal quality criteria
(edge floor, Kelly fraction floor, CI floor) that apply to all cities. A
cold-start city with few calibration pairs will produce wide CIs and will
rarely pass the edge floor. This is correct fail-closed behavior.

There is no separate "city warmup" configuration to modify.

---

## §9. Verification Checklist

Run after all backfill steps complete:

```bash
# 0. Argparse smoke — run BEFORE any real backfill; confirm zero "unrecognized arguments" errors
#    (catches flag-name drift: --start vs --start-date, space vs comma for --leads, etc.)
python scripts/backfill_wu_daily_all.py --help > /dev/null && echo "wu_daily_all: OK"
python scripts/backfill_obs_v2.py --help > /dev/null && echo "obs_v2: OK"
python scripts/backfill_openmeteo_previous_runs.py --help > /dev/null && echo "openmeteo_previous_runs: OK"
# Also validate the exact --leads comma form parses cleanly (no exec, just argparse):
python scripts/backfill_openmeteo_previous_runs.py \
    --cities "TestCity" --start-date 2024-01-18 --end-date 2024-01-19 \
    --leads 1,2,3,4,5,6,7 --dry-run 2>&1 | head -5
# Expected: JSON output or "no cities matched" — NOT "unrecognized arguments"

# 1. Config loads cleanly
python -c "from src.config import cities_by_name; assert '<CityName>' in cities_by_name"

# 2. K0 + tier invariants pass
pytest tests/test_cities_config_authoritative.py tests/test_tier_resolver.py -v

# 3. Daily obs present
python -c "
import sqlite3
conn = sqlite3.connect('state/zeus-world.db')
print(conn.execute(\"SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM observations WHERE city='<CityName>' AND authority='VERIFIED'\").fetchone())
"

# 4. Hourly obs present (in zeus-world.db, NOT zeus-forecasts.db)
python -c "
import sqlite3
conn = sqlite3.connect('state/zeus-world.db')
print(conn.execute(\"SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM observation_instants_v2 WHERE city='<CityName>'\").fetchone())
"

# 5. Ensemble snapshots present + FULL_CONTRIBUTOR rows exist
python -c "
import sqlite3
conn = sqlite3.connect('state/zeus-forecasts.db')
r = conn.execute(\"SELECT contributes_to_target_extrema, COUNT(*) FROM ensemble_snapshots_v2 WHERE city='<CityName>' GROUP BY contributes_to_target_extrema\").fetchall()
print(r)
"

# 6. Tier assigned correctly
python -c "
from src.data.tier_resolver import tier_for_city
print(tier_for_city('<CityName>'))
"

# 7. Full test suite (data-related subset)
pytest tests/test_audit_city_data_readiness.py tests/test_cities_config_authoritative.py tests/test_backfill_scripts_match_live_config.py tests/test_tier_resolver.py -v
```

---

## §10. Authority Files That Auto-Update (no manual edit needed)

| Module | Why no edit needed |
|---|---|
| `scripts/backfill_wu_daily_all.py::CITY_STATIONS` | Derived from `cities_by_name` at import time |
| `src/data/tier_resolver.py::TIER_SCHEDULE` | Built from `cities_by_name` at import time via `_build_tier_schedule()` |
| `src/data/forecast_source_registry.py` | Sources registered by product family; new city inherits existing WU ICAO source entry |
| Live ingest daemon city iteration | All daemons iterate `cities_by_name`; new city auto-included on next restart |

---

## §11. Changelog (this document)

| Date | Change | Reason |
|---|---|---|
| 2026-05-24 | Created (first version) | No prior protocol doc existed; assembled from source + test audit |
| 2026-05-24 | §5b: added 00Z/12Z FULL_CONTRIBUTOR rule for far-east cities | P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md — far-east 12Z is NON_CONTRIBUTOR; backfill must include 00Z runs. Prior to this doc, this requirement existed only in the P0 fix code + test_topology.yaml and was not in any onboarding checklist |
| 2026-05-24 | §1: added settlement_source URL city-path rule | SOURCE_VERIFY.md audit (2026-05-24) found 3 cities with city-path mismatches (ankara/sao-paulo/toronto); Polymarket description is authoritative for the URL |
| 2026-05-24 | §4: corrected DB for observation_instants_v2 to zeus-world.db (WORLD_CLASS) | db_table_ownership.yaml line 725 + backfill_obs_v2.py:113 DEFAULT_DB_PATH both confirm world.db. Erroneous draft claimed zeus-forecasts.db — corrected before first release. |
| 2026-05-24 | §5b: noted current data_versions (mx2t3) and LEGACY_NULL_PASSTHROUGH semantics | p0-2-hardening (2026-05-23) made missing/unknown data_version fail-closed; new cities must NOT backfill into legacy mx2t6 data_versions |
