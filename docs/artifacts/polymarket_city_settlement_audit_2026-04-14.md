# Polymarket City Settlement Audit 2026-04-14

Status: evidence artifact, not authority.

Purpose: preserve the volatile market-description audit facts that should not
live directly in `config/AGENTS.md`.

## Snapshot

Observed during the 2026-04-14 cross-validation pass:

- Istanbul and Moscow use NOAA-backed settlement sources.
- London moved from EGLL to EGLC and transitioned from Fahrenheit to Celsius.
- Jakarta moved from WIII to WIHH.
- Panama City moved from MPTO to MPMG.
- Taipei moved from Weather Underground to Taiwan CWA station 46692.

The four non-`wu_icao` cities at this snapshot were:

- Hong Kong: HKO.
- Istanbul: NOAA LTFM.
- Moscow: NOAA UUWW.
- Taipei: CWA station 46692.

NOAA and CWA fetchers were not implemented at the time of this snapshot, so
`scripts/backfill_wu_daily_all.py::CITY_STATIONS` intentionally excluded those
four cities.

## Use

This file is historical evidence for why `cities.json` needs routine market
description audits. The current source of truth remains the most recent active
Polymarket market description for each city.
