# 08 Provenance and Authority Audit

## Ruling

Provenance is inconsistent across table families. `observation_instants_v2` has strong non-empty provenance coverage; daily `observations` does not. Authority labels are therefore not uniformly meaningful.

## Provenance coverage

| Table | Rows | Provenance status | Ruling |
|---|---:|---|---|
| `observations` | 42,749 | 39,431 empty `provenance_metadata` rows | Unsafe as canonical. |
| `settlements` | 1,561 | `provenance_json` non-empty, but market slug absent | Evidence only. |
| `observation_instants_v2` | 1,813,662 | zero empty `provenance_json` rows | Best evidence lane, still missing role/metric/training fields. |
| `hourly_observations` | 1,813,568 | no provenance column | Compatibility/evidence only. |
| forecast/ensemble/calibration v2 | 0 | schema supports provenance, no rows | Not usable. |

## Authority label defects

- `VERIFIED` is applied to WU daily observation rows without provenance payloads. That violates the principle that `VERIFIED` should mean reproducible.
- `observation_instants_v2` uses `VERIFIED` over mixed source families, including fallback bulk sources. `authority` must be paired with source role.
- `settlements` rows have authority/provenance but lack market identity, so they are not fully verified for replay.

## Required fields to add or enforce

- `source_role`
- `temperature_metric`
- `physical_quantity`
- `observation_field`
- `training_allowed`
- `causality_status`
- `payload_hash`
- `parser_version`
- `source_url_or_file`
- `station_registry_version`
- `settlement_rule_version`
- `finalization_status`

## Metadata needed to reject unsafe rows

The system should expose rejection reasons as row attributes, not only audit outputs. Example values: `MISSING_PROVENANCE`, `FALLBACK_NOT_TRAINING_ELIGIBLE`, `RUNTIME_ONLY_SOURCE`, `NO_MARKET_IDENTITY`, `RECONSTRUCTED_AVAILABLE_AT`, `LOCAL_DAY_INCOMPLETE`, `DST_AMBIGUOUS`, `UNIT_RULE_UNKNOWN`, `ORACLE_TRANSFORM_UNVERIFIED`.