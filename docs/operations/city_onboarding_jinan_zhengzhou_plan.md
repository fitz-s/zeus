# Execution Plan: Onboard Jinan + Zhengzhou
<!-- Created: 2026-05-24 -->
<!-- Status: PLAN — awaiting reviewer gate before execution -->
<!-- Authority basis: docs/operations/city_onboarding_protocol.md (this session) -->

## §0. Pre-execution state

| Check | Status | Notes |
|---|---|---|
| Gamma markets confirmed | CONFIRMED | 4 open events each (may-20/21, active=True closed=False) |
| Market description parsed | CONFIRMED | SOURCE_VERIFY.md (2026-05-24) |
| Provider / station / URL verified | CONFIRMED | Both WU ICAO |
| Config/test discrepancy noted | CONFIRMED | See §PRE below |

### §PRE Pre-existing test failure to fix

`tests/test_cities_config_authoritative.py:27` currently reads:
```python
assert len(cities_by_name) == 51
```
The live config already has **52** cities (Qingdao was added after this test was
written). This test is **currently failing** on main. Adding 2 new cities brings
the total to **54**. The test assertion must be updated to `54` in the same
commit that adds the two cities.json entries.

---

## §1. Market Evidence

**Full in-repo evidence:** `docs/operations/evidence/jinan_zhengzhou_gamma_evidence.md`
(verbatim description text + byte-for-byte URL comparison, fetched live 2026-05-24)

### Jinan
- **Gamma slug:** `jinan`
- **Open markets:** `highest-temperature-in-jinan-on-may-20-2026` + may-21
  (active=True, closed=False, target dates 4 and 3 days past as of 2026-05-24)
- **Settlement provider:** Wunderground
- **Station:** ZSJN — Jinan Yaoqiang International Airport Station
- **Resolution URL (from gamma description):** `https://www.wunderground.com/history/daily/cn/jinan/ZSJN`
- **Unit:** Celsius
- **URL match vs cities.json proposal:** MATCH (byte-for-byte, no trailing slash)

### Zhengzhou
- **Gamma slug:** `zhengzhou`
- **Open markets:** `highest-temperature-in-zhengzhou-on-may-20-2026` + may-21
  (active=True, closed=False, target dates 4 and 3 days past as of 2026-05-24)
- **Settlement provider:** Wunderground
- **Station:** ZHCC — Zhengzhou Xinzheng International Airport Station
- **Resolution URL (from gamma description):** `https://www.wunderground.com/history/daily/cn/zhengzhou/ZHCC`
- **Unit:** Celsius
- **URL match vs cities.json proposal:** MATCH (byte-for-byte, no trailing slash)

---

## §2. cities.json Entries (EXACT JSON to insert)

Insert both entries into the `"cities"` array in `config/cities.json`, in
alphabetical name order (between "Jeddah" and "Karachi" for both, since J and Z
sit there; actually Jinan after Jakarta/Jeddah/Karachi and Zhengzhou at end or
after Wuhan by alpha — see exact position note below).

**Insertion order in the existing sorted array:**
- "Jinan" → after "Jeddah" (J < K)
- "Zhengzhou" → after "Wuhan" (W < Z)

### Entry: Jinan

Airport coordinates: ZSJN (Jinan Yaoqiang) is at approximately 36.8572°N,
117.0560°E (standard published coordinates; executor must verify against a
fresh airport metadata source — AirportGuide, OurAirports, or ICAO doc —
before commit, since `cities.json` coordinates must match the WU station
exactly per the `_coord_note` header).

```json
{
  "name": "Jinan",
  "aliases": [
    "Jinan"
  ],
  "slug_names": [
    "jinan"
  ],
  "noaa": null,
  "lat": 36.8572,
  "lon": 117.0560,
  "wu_station": "ZSJN",
  "wu_pws": null,
  "meteostat_station": null,
  "airport_name": "Jinan Yaoqiang International Airport",
  "country_code": "CN",
  "settlement_source": "https://www.wunderground.com/history/daily/cn/jinan/ZSJN",
  "settlement_source_type": "wu_icao",
  "timezone": "Asia/Shanghai",
  "cluster": "Jinan",
  "unit": "C",
  "historical_peak_hour": 15.0,
  "diurnal_amplitude_c": 10.0,
  "weighted_low_calibration_eligible": true
}
```

**Reviewer MUST verify lat/lon** against a primary source (AirportGuide ZSJN
or OurAirports) before approving. The values above are indicative; any
discrepancy from the published airport coordinates must be corrected.

### Entry: Zhengzhou

Airport coordinates: ZHCC (Zhengzhou Xinzheng) is at approximately 34.5197°N,
113.8408°E (standard published coordinates; same verification requirement as
Jinan).

```json
{
  "name": "Zhengzhou",
  "aliases": [
    "Zhengzhou"
  ],
  "slug_names": [
    "zhengzhou"
  ],
  "noaa": null,
  "lat": 34.5197,
  "lon": 113.8408,
  "wu_station": "ZHCC",
  "wu_pws": null,
  "meteostat_station": null,
  "airport_name": "Zhengzhou Xinzheng International Airport",
  "country_code": "CN",
  "settlement_source": "https://www.wunderground.com/history/daily/cn/zhengzhou/ZHCC",
  "settlement_source_type": "wu_icao",
  "timezone": "Asia/Shanghai",
  "cluster": "Zhengzhou",
  "unit": "C",
  "historical_peak_hour": 15.0,
  "diurnal_amplitude_c": 10.0,
  "weighted_low_calibration_eligible": true
}
```

**Reviewer MUST verify lat/lon** against a primary source (AirportGuide ZHCC
or OurAirports) before approving.

---

## §3. Test File Change

**File:** `tests/test_cities_config_authoritative.py`
**Line 27:** change `== 51` → `== 54`

```python
# BEFORE (currently failing — pre-existing Qingdao discrepancy):
assert len(cities_by_name) == 51

# AFTER (adds Qingdao + Jinan + Zhengzhou):
assert len(cities_by_name) == 54
```

No other test changes required. `CITY_STATIONS` and `TIER_SCHEDULE` auto-derive
from `cities_by_name`.

---

## §4. Daily Observations Backfill

```bash
cd /path/to/zeus && source .venv/bin/activate

# Dry-run first
python scripts/backfill_wu_daily_all.py \
    --cities "Jinan" "Zhengzhou" \
    --days 365 \
    --dry-run

# Execute (remove --dry-run after reviewing output)
python scripts/backfill_wu_daily_all.py \
    --cities "Jinan" "Zhengzhou" \
    --days 365
```

---

## §5. Hourly Observations Backfill (observation_instants_v2 → zeus-world.db)

**DB is zeus-world.db (WORLD_CLASS), NOT zeus-forecasts.db.** Authority:
`architecture/db_table_ownership.yaml` line 725 + `scripts/backfill_obs_v2.py:113`
DEFAULT_DB_PATH = world.db. Do NOT pass `--db state/zeus-forecasts.db`.

```bash
python scripts/backfill_obs_v2.py \
    --cities "Jinan" "Zhengzhou" \
    --start 2024-01-01 \
    --end $(date +%Y-%m-%d) \
    --data-version v1.wu-native \
    --dry-run

# Execute
python scripts/backfill_obs_v2.py \
    --cities "Jinan" "Zhengzhou" \
    --start 2024-01-01 \
    --end $(date +%Y-%m-%d) \
    --data-version v1.wu-native
```

---

## §6. Forecast History Backfill (Open-Meteo previous runs)

**NOTE:** `backfill_openmeteo_previous_runs.py` uses `--start-date`/`--end-date`
(verified: `scripts/backfill_openmeteo_previous_runs.py:418-419`). Different from
`backfill_obs_v2.py` which uses `--start`/`--end`.

```bash
python scripts/backfill_openmeteo_previous_runs.py \
    --cities "Jinan" "Zhengzhou" \
    --start-date 2024-01-18 \
    --end-date $(date +%Y-%m-%d) \
    --leads 1,2,3,4,5,6,7 \
    --dry-run

# Execute
python scripts/backfill_openmeteo_previous_runs.py \
    --cities "Jinan" "Zhengzhou" \
    --start-date 2024-01-18 \
    --end-date $(date +%Y-%m-%d) \
    --leads 1,2,3,4,5,6,7
```

---

## §7. ECMWF Open Data Ensemble Rows — 00Z run requirement

Jinan (lat=36.9°N) and Zhengzhou (lat=34.5°N) are both UTC+8 cities. Per
`docs/operations/P0_FORECAST_EXTREMA_AUTHORITY_2026-05-22.md`:

- Local calendar day peaks well before UTC noon (daily max around 15:00 local
  = 07:00 UTC).
- The **00Z run** (issued 00:00 UTC, available ~07:00 UTC) covers the full local
  day including the peak window → FULL_CONTRIBUTOR.
- The **12Z run** (issued 12:00 UTC = 20:00 local) is post-peak for the prior
  local day → NON_CONTRIBUTOR.

The live ECMWF Open Data ingest daemon (`src/ingest/forecast_live_daemon.py`)
iterates `cities_by_name` and will automatically pick up Jinan and Zhengzhou
once the config entry is in place. On next daemon cycle after restart, 00Z
snapshots will be written with the correct `data_version` and
`contributes_to_target_extrema=1`.

**Historical 00Z ensemble rows** within the current ~10-day Open Data rolling
window will be auto-fetched by the daemon. Older historical backfill requires
a TIGGE or archived Open Data source, which is not currently automated for new
cities — accept sparse historical ensemble as cold-start behavior.

After first daemon run post-onboarding, verify:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('state/zeus-forecasts.db')
for city in ['Jinan', 'Zhengzhou']:
    r = conn.execute('''
        SELECT contributes_to_target_extrema, attribution_status, COUNT(*)
        FROM ensemble_snapshots_v2
        WHERE city=?
        GROUP BY contributes_to_target_extrema, attribution_status
    ''', (city,)).fetchall()
    print(city, r)
"
```

---

## §8. Settlement Harvest

The existing markets for may-20/21 are in settlement-pending state
(active=True, closed=False, target dates past). The live harvester will
attempt to harvest them once WU finalizes the daily data.

No manual intervention needed. The live harvester reads `cities_by_name`
and will include the new cities once onboarded. Monitor:
```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('state/zeus-forecasts.db')
for city in ['Jinan', 'Zhengzhou']:
    r = conn.execute(\"SELECT city, target_date, winning_bin, settlement_value FROM settlements_v2 WHERE city=? ORDER BY target_date DESC LIMIT 5\", (city,)).fetchall()
    print(city, r)
"
```

---

## §9. data_coverage LEGITIMATE_GAP (optional)

If the hole scanner flags pre-2024-01-01 dates as failures for these cities,
add to `config/data_availability_exceptions.yaml`:

```yaml
city_onboard_legitimate_gaps:
  - city: "Jinan"
    data_source: "wu_icao_history"
    before_date: "2024-01-01"
    reason_code: "city_onboard_pre_archive_coverage"
  - city: "Zhengzhou"
    data_source: "wu_icao_history"
    before_date: "2024-01-01"
    reason_code: "city_onboard_pre_archive_coverage"
```

Only add this if the scanner actually triggers; don't pre-seed if not needed.

---

## §10. Full Verification Run

```bash
# 0. Argparse smoke — run BEFORE any real backfill; confirms zero "unrecognized arguments"
python scripts/backfill_wu_daily_all.py --help > /dev/null && echo "wu_daily_all: OK"
python scripts/backfill_obs_v2.py --help > /dev/null && echo "obs_v2: OK"
python scripts/backfill_openmeteo_previous_runs.py --help > /dev/null && echo "openmeteo_previous_runs: OK"
# Validate exact §6 flags parse cleanly (no network call, just argparse):
python scripts/backfill_openmeteo_previous_runs.py \
    --cities "Jinan" --start-date 2024-01-18 --end-date 2024-01-19 \
    --leads 1,2,3,4,5,6,7 --dry-run 2>&1 | head -5
# Expected: JSON output or "no cities matched" — NOT "unrecognized arguments"

# 1. Config loads
python -c "
from src.config import cities_by_name
assert 'Jinan' in cities_by_name
assert 'Zhengzhou' in cities_by_name
assert cities_by_name['Jinan'].wu_station == 'ZSJN'
assert cities_by_name['Zhengzhou'].wu_station == 'ZHCC'
print('OK:', len(cities_by_name), 'cities')
"

# 2. K0 + invariants
pytest tests/test_cities_config_authoritative.py tests/test_tier_resolver.py -v

# 3. Tier assignment
python -c "
from src.data.tier_resolver import tier_for_city, Tier
assert tier_for_city('Jinan') == Tier.WU_ICAO
assert tier_for_city('Zhengzhou') == Tier.WU_ICAO
print('Tier OK')
"

# 4. Daily obs row counts
python3 -c "
import sqlite3
conn = sqlite3.connect('state/zeus-world.db')
for city in ['Jinan', 'Zhengzhou']:
    r = conn.execute(\"SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM observations WHERE city=? AND authority='VERIFIED'\", (city,)).fetchone()
    print(city, r)
"

# 5. Hourly obs row counts (world.db — NOT forecasts.db)
python3 -c "
import sqlite3
conn = sqlite3.connect('state/zeus-world.db')
for city in ['Jinan', 'Zhengzhou']:
    r = conn.execute('SELECT COUNT(*), MIN(target_date), MAX(target_date) FROM observation_instants_v2 WHERE city=?', (city,)).fetchone()
    print(city, r)
"

# 6. Backfill-scripts match (CITY_STATIONS auto-derives)
pytest tests/test_backfill_scripts_match_live_config.py -v

# 7. Full data-path subset
pytest tests/test_audit_city_data_readiness.py tests/test_cities_config_authoritative.py tests/test_backfill_scripts_match_live_config.py tests/test_tier_resolver.py -v
```

---

## §11. Commit Scope

All changes for this onboarding belong in a single coherent commit on branch
`feat/onboard-jinan-zhengzhou`:

- `config/cities.json` — 2 new city entries
- `tests/test_cities_config_authoritative.py:27` — count 51 → 54
  (closes pre-existing Qingdao discrepancy + new cities)
- `docs/operations/city_onboarding_protocol.md` — new protocol doc (this session)
- `docs/operations/city_onboarding_jinan_zhengzhou_plan.md` — this plan

Backfill commands (§4–§6) are run locally against the live DB and do not
produce git-tracked changes.

---

## §12. Market Expiry Note

As of 2026-05-24, all 8 open markets for Jinan/Zhengzhou have target dates in
the past (may-20 and may-21). Polymarket has not yet opened may-22+ rounds for
these cities. Onboarding is correct regardless: the protocol does not gate on
future market availability. The config + backfill + test changes are durable
and will be active if/when Polymarket opens new rounds.
