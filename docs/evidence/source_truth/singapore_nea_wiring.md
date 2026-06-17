# Singapore NEA day0 SHADOW observation source — wiring report

- Created: 2026-06-17
- Authority basis: operator v4 problematic-20 best-sources audit
  (`zeus_problematic20_best_sources_v4.csv`; reconciliation
  `docs/evidence/source_truth/problematic20_v4_reconciliation.md`). NEA is the
  one net-new free public source from the v4 audit.
- Status: NEA is FETCHABLE + RESOLVABLE for Singapore with correct
  provenance; tests PASS. Full fusion wiring is a documented later step.
  No commit. No daemon restart.

## What was built

| Artifact | Path |
|---|---|
| NEA source module | `src/data/nea_sg_obs.py` |
| Tests | `tests/test_nea_sg_obs.py` (14 tests, all pass) |

Module mirrors `src/data/day0_fast_obs.py` (the existing free-METAR fast-lane
pattern): canonical constants, a typed obs record with full station-identity
provenance, a tolerant parser, a fail-soft fetcher, a nearest-station haversine
resolver, and a per-city resolver `nea_shadow_source_for_city(city)`.

Key symbols:
- `NEA_ENDPOINT` = `https://api-open.data.gov.sg/v2/real-time/api/air-temperature`
  (+ `NEA_ENDPOINT_LEGACY` v1 fail-over).
- `NEA_SOURCE_ID = "nea_sg_air_temperature"`.
- `NEA_SOURCE_ROLE = "shadow_covariate"` — a NEW role, deliberately distinct
  from the tier_resolver settlement roles.
- `NeaObsReading` — typed record: `source_id, station_id, station_name,
  distance_km, value_c, timestamp, is_settlement_faithful` (always False).
- `fetch_nea_reading(...)`, `parse_nea_payload(...)`, `nearest_station(...)`,
  `nea_shadow_source_for_city(city)`, `nea_obs_to_fusion_reading(reading)`.

## Live API shape (verified 2026-06-17, no key)

v2 path responds. JSON:
`data.stations[]` = `{id, deviceId, name, location.{latitude,longitude}}` (16
stations); `data.readings[]` = one element `{timestamp, data[] of {stationId,
value}}`; `readingUnit` = `"deg C"`. Legacy v1 also responds with a different
shape (`metadata.stations[]` / `items[].readings[] of {station_id, value}`); the
parser accepts BOTH shapes and the fetcher fails over v2 → v1.

## Chosen station + distance (the deliverable result)

Nearest NEA station to WSSS Changi settlement coords (Singapore lat 1.368, lon
103.982 from `config/cities.json`), by haversine:

| station_id | name | distance_km |
|---|---|---|
| **S24** | **Upper Changi Road North** | **0.040** |
| S106 | Pulau Ubin | 5.667 |
| S107 | East Coast Parkway | 6.476 |
| S06 | Paya Lebar Airport | 8.757 |

S24 is essentially co-located with WSSS (~40 m). Live end-to-end resolve at
report time returned: `NeaShadowSource(station_id='S24', distance_km=0.040,
is_settlement_faithful=False)`; live reading `value_c=26.4`, shadow flag False.

## SHADOW semantics (enforced in code, not just doc)

NEA is a Changi-AREA sensor network, NOT the WSSS settlement instrument — even
S24 at 40 m is a DIFFERENT physical sensor. So NEA is wired strictly as a
high-frequency day0 SHADOW / observation-likelihood covariate:

1. `NeaObsReading.is_settlement_faithful` is hard-False; the constructor RAISES
   `ValueError` if any caller tries `is_settlement_faithful=True`
   (`test_constructing_a_faithful_nea_reading_is_rejected`).
2. NEA is NOT registered in `src/data/tier_resolver.py` (the settlement /
   historical / fallback registry). Verified: `allowed_sources_for_city
   ('Singapore')` is unchanged = `{wu_icao_history, ogimet_metar_wsss,
   meteostat_bulk_wsss}`; `'nea_sg_air_temperature' not in` it. So NEA can never
   be selected as a settlement or backfill source.
3. The fusion adapter `nea_obs_to_fusion_reading` stamps
   `is_settlement_faithful=False`, so when fusion is wired NEA enters as a
   distinct, down-weighted shadow station.

The existing Singapore wu_icao/METAR settlement path is untouched: 82 existing
tests (`test_day0_fast_obs_lane.py` + `test_tier_resolver.py`) still pass;
Singapore still resolves `wu_icao_history` primary.

## Provenance law

Every NEA datum carries `source_id` + `station_id` + `station_name` +
`distance_km` (the AREA-vs-settlement gap), reproducibly resolved by haversine
against the configured WSSS coords. Fail-soft: any network/parse error → source
ABSENT (None / empty), never a crash (`test_fetch_failure_returns_none`,
`test_garbage_payload_returns_none`).

## Integration point (NOT wired this pass — hook documented)

`src/forecast/observation_precision_fusion.fuse_day0_observations` consumes
`ObsSourceReading` records. `nea_obs_to_fusion_reading()` adapts a
`NeaObsReading` into those kwargs with `is_settlement_faithful=False`. When the
multi-source day0 observation fusion is wired for Singapore, NEA enters there as
a correlated-but-distinct shadow station (its `station_id` ≠ WSSS → treated as
an independent station, down-weighted via station-mismatch σ). The immediate
deliverable — NEA fetchable + registered with correct provenance — is complete;
full fusion wiring is the next step.

## Test result

```
.venv/bin/python -m pytest tests/test_nea_sg_obs.py -q
14 passed
```

Covers: nearest-station selection (S24, <0.5 km), v2 + legacy parse, value +
provenance, SHADOW invariant + the faithful-rejection guard, non-Singapore
returns None, fusion-adapter shadow flag, and fail-soft on transport error /
garbage payload.
