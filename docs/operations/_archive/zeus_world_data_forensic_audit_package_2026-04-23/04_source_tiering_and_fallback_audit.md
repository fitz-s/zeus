# 04 Source Tiering and Fallback Audit

## Ruling

The source-tier architecture is directionally rational but not yet safe enough for Zeus's downstream needs. The code distinguishes WU/HKO primary settlement-like sources, Ogimet/Meteostat fallback station evidence, Open-Meteo gridded/model fallbacks, and TIGGE forecast signal. The uploaded DB does not encode source role and downstream eligibility strongly enough to prevent a fallback row from being treated as canonical.

## Confirmed source families in DB

### Daily `observations`

- Populated sources include `wu_icao_history`, `hko_daily_api`, and Ogimet METAR-derived sources.
- WU dominates daily observations and is stamped mostly `VERIFIED`, but WU rows have empty provenance.
- HKO/Ogimet daily rows generally have provenance and represent distinct semantics.

### Hourly `observation_instants_v2`

- 1,813,658 rows have authority `VERIFIED` and data_version `v1.wu-native`.
- Current view includes 68 sources and 1,813,662 rows.
- Source set includes WU, Ogimet fallback, Meteostat bulk fallback, and a small HKO/current lane. That is acceptable as evidence if each row has source-role eligibility. It is unsafe if consumers treat all rows under `v1.wu-native` as a single canonical family.

## Source classes

| Source class | Appropriate role | Zeus current risk |
|---|---|---|
| WU station history | Settlement evidence when exact market station/source matches | Private/public endpoint behavior and empty provenance make `VERIFIED` overconfident. |
| HKO | Hong Kong settlement/evidence lane | Decimal observations and integer oracle settlement semantics can be conflated. |
| Ogimet METAR/SYNOP | Station fallback/evidence; DST/gap repair | Cadence may miss extrema; not settlement authority unless explicitly market-specified. |
| Meteostat bulk | Historical station-evidence gap filler | Aggregated/lagged; may be unsuitable for final settlement or live calibration without source-role flag. |
| Open-Meteo Archive/Previous Runs | Gridded/model fallback and forecast signal | Not station authority; previous-runs issue/availability must be captured, not guessed. |
| ECMWF/TIGGE | Ensemble forecast signal | Correct direction for modeling, but no populated v2 snapshots in DB. |
| Polymarket CLOB/rules | Market truth and settlement rule registry | Market event tables empty; settlement rows lack market slug. |

## Fallback contamination risks

1. `observation_instants_current` uses a data-version filter, not a source-role/eligibility filter.
2. `hourly_observations` collapses v2 source/time/provenance fields into a local-hour compatibility table.
3. Daily `observations` has no source role, training flag, or causality status.
4. Calibration rebuild code can search latest verified observations by source ordering; without source-role hardening, fallback can become label truth.
5. WU/HKO/Ogimet/Meteostat rows can all be `VERIFIED` in different ways; authority alone is insufficient.

## Required source-tier redesign

- Introduce a `source_registry` or materialized policy view with fields: `source_code`, `provider`, `product`, `station_id`, `source_role`, `settlement_eligible`, `training_eligible`, `live_eligible`, `fallback_rank`, `valid_from`, `valid_to`, `known_caveats`.
- Create views: `canonical_settlement_observations`, `training_observations`, `runtime_monitoring_observations`, `fallback_evidence_observations`.
- Make consumers read these views, not raw tables.
- Require fallback rows to be preserved but excluded by default from calibration/settlement labels.