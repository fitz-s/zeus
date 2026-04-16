# Settlement Validation Workflow

> How to detect, investigate, and resolve discrepancies between our observation data and Polymarket settlement outcomes.

## Overview

Our settlement pipeline depends on observation data matching what Polymarket uses to resolve markets. Polymarket can change settlement sources at any time. This workflow catches those changes before they cause financial losses.

## Step 1: Run the Smoke Test

```bash
cd zeus
source .venv/bin/activate
python scripts/smoke_test_settlements.py
```

**Expected output**: >95% MATCH rate. Any new MISMATCH needs investigation.

Options:
- `--verbose`: Also print NO_DATA rows
- `--city "Hong Kong"`: Filter to a single city

## Step 2: Triage Mismatches

Mismatches fall into 4 categories:

### Category A: ±1°C Rounding (acceptable)
- Same station, same source type
- Delta is exactly ±1°C
- **Action**: No action needed. This is systemic noise from WU/NOAA rounding differences.

### Category B: Source Change (critical)
- Delta >2°C or affects multiple consecutive days for one city
- **Action**: Investigate the source change immediately (Step 3)

### Category C: Bad Observation Data (data quality)
- High == Low, or values clearly wrong (e.g., Chicago high=34°F when neighbors are 60-70°F)
- **Action**: QUARANTINE the observation (`authority='QUARANTINED'`)

### Category D: Date Mapping Bug (code bug)
- Systemic off-by-one affecting ALL cities on certain dates
- **Action**: Fix date extraction logic in the smoke test and settlement code

## Step 3: Investigate Source Changes

When you suspect Polymarket changed a city's settlement source:

```python
import subprocess, json, re, time

def curl_json(url):
    r = subprocess.run(['curl', '-fsk', '--max-time', '30', url],
                       capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None

# Fetch all events (open + closed) for the city
all_events = []
for closed in ['true', 'false']:
    offset = 0
    while True:
        batch = curl_json(
            f'https://gamma-api.polymarket.com/events?tag_id=103040'
            f'&closed={closed}&limit=50&offset={offset}')
        if not batch: break
        all_events.extend(batch)
        if len(batch) < 50: break
        offset += 50
        time.sleep(0.15)

# Filter to target city and extract source from description
target = "Taipei"  # change as needed
for ev in sorted(all_events, key=lambda e: e.get('endDate','')):
    if target not in ev.get('title', ''): continue
    desc = ev.get('description', '')
    # Look for source keywords: NOAA, CWA, Weather Underground, Observatory, Airport Station
    print(f"{ev['endDate'][:10]} | {ev['title'][:60]}")
    print(f"  {desc[:150]}")
```

**Key signals in market descriptions**:
- `"recorded at the X Airport Station"` → WU (Weather Underground)
- `"recorded by NOAA at the X"` → NOAA
- `"recorded by the Hong Kong Observatory"` → HKO
- `"recorded by Taipei's Central Weather Administration"` → CWA

## Step 4: Update Configuration

When a source change is confirmed:

1. **Update `config/cities.json`**:
   - `wu_station` → new ICAO code if changed
   - `airport_name` → new station name
   - `settlement_source` → new URL
   - `settlement_source_type` → `wu_icao`, `noaa`, `hko`, or `cwa_station`
   - Add entry to `change_log` array with timestamp and details

2. **Update `docs/settlement-source-provenance.md`**:
   - Add the transition to the city's history table
   - Note the exact date range affected

3. **Update daemon code** if source type changed (e.g., adding a new fetcher):
   - `src/data/daily_obs_append.py` — live observation pipeline
   - `scripts/backfill_wu_daily_all.py` — backfill script

## Step 5: Handle Quarantined Data

For observations with `authority='QUARANTINED'`:

```sql
-- Find all quarantined rows
SELECT city, target_date, high_temp, provenance_metadata
FROM observations
WHERE authority = 'QUARANTINED';

-- Settlement pipeline MUST skip quarantined rows
-- rebuild_settlements.py already filters: WHERE authority = 'VERIFIED'
```

## Cadence

| Check | Frequency | Trigger |
|-------|-----------|---------|
| Smoke test | Weekly + after any backfill | Cron or manual |
| Source audit (Gamma API descriptions) | Monthly | Scheduled |
| Emergency source check | Immediately | When mismatch spike detected |
| Full provenance doc update | After any source change | Manual |

## Known Permanent Issues

1. **2026-03-08 WU partial data**: 6 US cities have only 2-3 hourly observations. WU API still returns partial data on re-fetch. These rows are permanently QUARANTINED.

2. **±1°C systemic noise**: WU and Polymarket may use slightly different observation windows or rounding. This affects ~1-2% of settlements and cannot be eliminated.

3. **Gamma API endDate ≠ weather date**: Market `endDate` is always 1 day after the actual weather observation date. Always extract the date from the event `title` field.
