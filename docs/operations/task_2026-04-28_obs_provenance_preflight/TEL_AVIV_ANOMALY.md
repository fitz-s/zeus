# Tel Aviv: 10 mis-routed wu_icao_history rows
# Created: 2026-04-28
# Authority basis: live-DB audit 2026-04-28T14:25Z + tier_resolver classification

## Finding

10 rows exist in `observations`:
```
city='Tel Aviv'  source='wu_icao_history'  authority='VERIFIED'  station_id='LLBG'
target_date  ∈  2026-04-10 .. 2026-04-19  (consecutive 10 days)
fetched_at = 2026-04-21T19:30:35Z (single batch, ms apart)
```

These rows are `unknown` per `tier_resolver.source_role_assessment_for_city_source(
'Tel Aviv', 'wu_icao_history')` — Tel Aviv's canonical primary class is
Ogimet/NOAA-proxy per `docs/operations/current_source_validity.md` item 5.

## Evidence: duplicate of canonical ogimet

7 of the 10 dates (04-10..04-16) overlap with `ogimet_metar_llbg` rows for the
same Tel Aviv dates. **Values are byte-identical**:

| date | wu hi/lo | ogimet hi/lo |
|---|---|---|
| 2026-04-10 | 22 / 12 | 22 / 12 |
| 2026-04-11 | 22 / 12 | 22 / 12 |
| 2026-04-12 | 21 / 13 | 21 / 13 |
| 2026-04-13 | 22 / 14 | 22 / 14 |
| 2026-04-14 | 30 / 11 | 30 / 11 |
| 2026-04-15 | 35 / 16 | 35 / 16 |
| 2026-04-16 | 36 / 24 | 36 / 24 |

The 3 remaining dates (04-17..04-19) lack ogimet rows — likely ogimet ingester
hadn't caught up to those yet on 2026-04-21.

## Verdict

**(c) wrong-source-tag bug**: a single backfill run on 2026-04-21 19:30:35Z
fetched WU API for Tel Aviv even though tier_resolver places Tel Aviv on
ogimet. The same station (LLBG) is the underlying truth source — WU's ICAO
endpoint is just a different scrape path on top of the same METAR data — so
values match. The error is the SOURCE TAG (=`wu_icao_history`), not the
values.

## Disposition

**Skip — leave on disk; do not delete autonomously.**

- The Gate 3+4 fill script (`fill_observations_provenance_existing.py`)
  already classifies these as `unknown_city` (Tel Aviv not in `CITY_STATIONS`)
  and skips them with a `quarantine.json` entry.
- They do NOT block training: `rebuild_calibration_pairs_v2._fetch_verified_observation`
  does `ORDER BY source DESC LIMIT 1` per (city, target_date), and `wu_icao_history`
  sorts AFTER `ogimet_metar_llbg` — so for the 7 overlap dates, ogimet wins;
  for the 3 lone dates, wu wins (and values are correct, just mis-tagged).
- Gate 3+4 preflight count after fill will be 0 because these rows have
  empty provenance and would still be 10 rows blocking — UNLESS we fill
  them too. We don't fill them because `CITY_STATIONS` doesn't have Tel Aviv,
  so the script would need an override.

## Actions deferred to operator

| Option | Description |
|---|---|
| 1. Override-fill | Add `--include-cities Tel\ Aviv` flag and a one-off `LLBG/IL` mapping; provenance fill via real WU API; values match (we already verified). |
| 2. Reclassify source | UPDATE source FROM 'wu_icao_history' TO 'ogimet_metar_llbg_via_wu_endpoint' or similar — but creates a non-canonical source name. |
| 3. Delete the 10 rows | Lose the 3 lone dates' coverage; ogimet ingest fills them later. |
| 4. Leave as-is | Gate 3+4 stays at 10 even after fill; fail-closed gate keeps blocking; manual operator approval to proceed. |

**Recommended: Option 1 (override-fill)** because:
- Values are real (verified against ogimet for 7 days; raw WU API for 3)
- Filling preserves all 10 days of coverage
- Audit trail (provenance_metadata) is the only missing piece
- One-off CITY_STATIONS override, not a permanent contract change

But this requires explicit operator approval per Constraint #4 governance —
the source-tag mismatch with tier_resolver is a real audit-trail issue, even
if the values are correct.
