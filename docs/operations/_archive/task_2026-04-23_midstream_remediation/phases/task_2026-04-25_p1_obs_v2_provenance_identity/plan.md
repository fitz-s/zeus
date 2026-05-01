# P1 Obs V2 Provenance Identity Plan

Date: 2026-04-25
Branch: `midstream_remediation`
Status: in progress

## Scope

This packet finishes the remaining P1 `4.3.B` seam for
`observation_instants_v2`: the writer must reject training-eligible v2
observation rows unless their provenance carries payload identity, source
identity, parser version, and station identity.

In scope:
- `src/data/observation_instants_v2_writer.py`
- `scripts/backfill_obs_v2.py`
- `scripts/fill_obs_v2_dst_gaps.py`
- `scripts/fill_obs_v2_meteostat.py`
- `scripts/hko_ingest_tick.py`
- `scripts/verify_truth_surfaces.py`
- `tests/test_obs_v2_writer.py`
- `tests/test_hk_rejects_vhhh_source.py`
- `tests/test_truth_surface_health.py`
- `tests/test_backfill_scripts_match_live_config.py`
- packet evidence and registry companions required by map-maintenance:
  `docs/operations/**`, `docs/AGENTS.md`, `docs/README.md`,
  `architecture/topology.yaml`, and `architecture/docs_registry.yaml`

Out of scope:
- production DB mutation
- schema/view DDL, including `observation_instants_current`
- 39,431 legacy WU observation row quarantine/backfill
- `INSERT OR REPLACE` / revision-history redesign
- P3 consumer cutover or safe-view migration

## Context

Recent packets already landed source-role registry behavior, writer-side
`source_role` / `training_allowed` / `causality_status` derivation, unsafe-row
readiness diagnostics, and script-side preflights. The remaining weak seam is
that `ObsV2Row` currently accepts any non-empty provenance JSON with `tier`;
the active producers also emit minimal provenance.

Topology navigation for this exact script batch reports
`scope_expansion_required` because the `modify data ingestion` profile admits
only the source writer and direct writer tests. The packet boundary is kept
because the scripts are the producer side of the same writer contract, and an
architect review selected this as the next cohesive P1 batch. This packet does
not widen into P2 upsert/revision work.

## RALPH Loop Plan

1. Add a writer contract helper that requires provenance JSON keys:
   - `payload_hash`
   - `parser_version`
   - one of `source_url` or `source_file`
   - one of `station_id`, `station_registry_version`, or
     `station_registry_hash`
2. Update active `ObsV2Row` producers to populate those keys using available
   fetch/source identity without logging secrets.
3. Tighten `verify_truth_surfaces` so the read-only readiness gate checks JSON
   provenance when dedicated payload columns are absent.
4. Add/adjust tests:
   - writer rejects missing payload identity
   - writer persists payload identity
   - readiness fails when JSON payload identity is missing
   - producer row builders stamp required provenance keys, with source-text
     fallback checks for script surfaces where direct builder isolation is not
     sufficient
5. Run gates and critic review before closeout.

## Stop Conditions

- Stop if implementation requires production DB mutation or DDL.
- Stop if raw response hashes cannot be produced for a source; label the hash
  scope in provenance rather than claiming raw-provider bytes.
- Stop if source-role or training eligibility semantics need to change.
