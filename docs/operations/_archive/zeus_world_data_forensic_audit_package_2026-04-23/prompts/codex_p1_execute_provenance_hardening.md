# codex_p1_execute_provenance_hardening.md

Execute after P0 passes.

Goals:
1. Add/require provenance fields: source_role, payload_hash, parser_version, source_url_or_file, station_registry_version, temperature_metric, physical_quantity, observation_field, training_allowed, causality_status, finalization_status where applicable.
2. Implement source-role registry and safe views: canonical settlement observations, training observations, runtime monitoring observations, fallback evidence observations.
3. Quarantine or mark non-training all WU daily rows with empty provenance until retrofitted.
4. Ensure `VERIFIED` cannot be assigned without provenance and source-role eligibility.

Constraints:
- Do not silently promote fallback data.
- Do not infer market identity from city/date only.

Verification:
- Provenance SQL returns zero unsafe canonical rows.
- Negative tests prove fallback/evidence rows cannot enter training by default.
