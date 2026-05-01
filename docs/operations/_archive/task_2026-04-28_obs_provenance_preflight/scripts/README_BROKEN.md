# QUARANTINED: enrich_observation_instants_v2_provenance.py.BROKEN-DO-NOT-RUN

This script is **structurally unsafe** and must NOT be executed.

## Why it was quarantined (2026-04-28T04:30Z)

1. Lazy import path resolution `parents[3]` resolves to `docs/`, not zeus root → tier_resolver `ImportError` swallowed silently.
2. Fallback heuristic returns `historical_hourly` for `meteostat_*` and `ogimet_metar_*` sources WITHOUT zeus authority backing (fabricated by the agent during implementation).
3. Synthetic provenance keys (`payload_hash`, `parser_version`, `source_url`, `source_file` with `legacy://` URLs) are written into `provenance_json`. These violate Fitz Constraint #4 (data provenance) and were removed by `remove_synthetic_provenance.py`.

## What replaced it

`recompute_source_role_canonical.py` — uses `Path(__file__).parents[4]` (verified zeus root), HARD-FAILS on tier_resolver import error (no fallback), and writes ONLY `source_role` + `training_allowed` from canonical tier_resolver. NO synthetic provenance.

## Forensic record

If you need to understand why the OBS provenance preflight gates 5/4/3 fired both before and after this packet's apply: the apply added synthetic markers; the demolition removed them; production now correctly fails those gates. Closing them requires REAL provenance enrichment (e.g., re-fetching authoritative source records), not synthesis.

---

## Also quarantined: backfill_observations_provenance_metadata.py.BROKEN-DO-NOT-RUN

Same class of error: synthesizes `provenance_metadata` JSON from already-existing row fields and tags it `"synthesized_by":"legacy:backfill_obs_prov_2026-04-28"`. This is data fabrication, not real provenance enrichment. The 39,431 rows it touched have been NULL'd back to empty.

## Closing the gates RIGHT requires

- **Gates 3+4** (`observations.{verified_without_provenance, wu_empty_provenance}`): re-fetch from WU API or otherwise restore real provenance metadata; do NOT synthesize.
- **Gate 5** (`payload_identity_missing`): the legacy obs_v2 rows lack real `payload_hash` because they were written before the A1 contract existed. Either re-fetch and re-import through the A1 writer (`backfill_obs_v2.py` exists for this), OR explicitly accept that legacy rows are not training-eligible and quarantine them.
