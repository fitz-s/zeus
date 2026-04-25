# P1 Obs V2 Provenance Identity - Work Log

Date: 2026-04-25
Branch: `midstream_remediation`
Task: P1 obs_v2 provenance identity contract
Changed files: architecture/topology.yaml; architecture/docs_registry.yaml; src/data/observation_instants_v2_writer.py; scripts/backfill_obs_v2.py; scripts/fill_obs_v2_dst_gaps.py; scripts/fill_obs_v2_meteostat.py; scripts/hko_ingest_tick.py; scripts/verify_truth_surfaces.py; tests/test_obs_v2_writer.py; tests/test_hk_rejects_vhhh_source.py; tests/test_truth_surface_health.py; tests/test_backfill_scripts_match_live_config.py; docs/AGENTS.md; docs/README.md; docs/operations/current_state.md; docs/operations/AGENTS.md; docs/operations/task_2026-04-25_p1_obs_v2_provenance_identity/plan.md; docs/operations/task_2026-04-25_p1_obs_v2_provenance_identity/work_log.md; docs/operations/task_2026-04-25_p1_obs_v2_provenance_identity/receipt.json
Summary: Hardened the obs_v2 writer provenance contract and the active obs_v2 producer stamps without DB mutation, DDL, upsert redesign, quarantine, or consumer cutover.
Verification: py_compile, focused pytest, topology boot/fatal/tests/scripts/freshness/planning/work-record/change-receipt/current-state/map-maintenance gates, critic review, and git diff check.
Next: Commit and push this packet, then rebuild phase-entry context before selecting the next packet.

## Phase Entry

- Reread root `AGENTS.md`, `docs/operations/current_state.md`,
  `docs/operations/AGENTS.md`, scoped `src/data/AGENTS.md`, `scripts/AGENTS.md`,
  and `tests/AGENTS.md`.
- Read POST_AUDIT_HANDOFF 4.3/4.4 and current data/source fact surfaces.
- Scout recommended batching remaining 4.3.A/B/C, but row quarantine and
  schema/view work are not low-coupled.
- Architect recommended a limited P1 batch on the `observation_instants_v2`
  provenance write seam and explicitly warned not to jump to P2 upsert work.
- A premature P2 upsert packet shell was created and then removed before any
  code edits after architect review returned.
- Topology for the full producer script set reported scope expansion because
  the data-ingestion profile admits the writer and direct writer tests only;
  this packet records the explicit boundary and keeps P2/DDL/DB mutation out.

## Planned Edits

- Require payload/source/parser/station identity in `ObsV2Row.provenance_json`.
- Populate that identity in the active v2 producers:
  `backfill_obs_v2`, `fill_obs_v2_dst_gaps`, `fill_obs_v2_meteostat`, and
  `hko_ingest_tick`.
- Teach `verify_truth_surfaces` to evaluate provenance JSON keys where payload
  identity columns are not present.
- Add focused regression tests.

## Verification

- `.venv/bin/python -m py_compile src/data/observation_instants_v2_writer.py scripts/backfill_obs_v2.py scripts/fill_obs_v2_dst_gaps.py scripts/fill_obs_v2_meteostat.py scripts/hko_ingest_tick.py scripts/verify_truth_surfaces.py tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_truth_surface_health.py tests/test_backfill_scripts_match_live_config.py` passed.
- Initial focused pytest failed because `_missing_payload_identity_keys` was inserted inside `_looks_like_iso_datetime`, making explicit-offset timestamps fail after provenance validation reached the structural check. Fixed by restoring the full datetime parser body.
- Initial focused pytest also exposed a rebuild-preflight fixture row without obs_v2 provenance JSON; fixture now carries the new identity contract.
- `.venv/bin/python -m pytest tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_backfill_scripts_match_live_config.py tests/test_truth_surface_health.py -q` passed after producer behavior tests: 123 passed, 5 skipped.
- Critic returned PASS with two residual non-blockers: packet evidence omitted
  `tests/test_hk_rejects_vhhh_source.py`, and DST-gap/Meteostat producers had
  only source-text assertions. The packet evidence now includes the HK test,
  and `tests/test_backfill_scripts_match_live_config.py` now monkeypatches
  DST-gap and Meteostat fetch/insert seams to parse emitted row provenance.
- `.venv/bin/python -m pytest tests/test_backfill_scripts_match_live_config.py -q` passed after behavior tests: 16 passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1 observation_instants_v2 provenance identity contract for training-eligible rows and active obs_v2 producers" --files ...` reported the expected `scope_expansion_required` for the producer-script batch; this packet keeps the architect-approved boundary documented and does not modify topology admission.

## Implementation Notes

- `ObsV2Row` now rejects rows whose provenance JSON lacks `payload_hash`,
  `parser_version`, source identity (`source_url` or `source_file`), or station
  identity (`station_id`, `station_registry_version`, or
  `station_registry_hash`).
- Active obs_v2 producers stamp deterministic `sha256:` payload identity over
  row/source fields available at write time and label the `payload_scope`
  rather than claiming a raw-provider byte hash where raw response bytes are not
  available.
- Source locators redact WU API keys (`apiKey=REDACTED`) and use non-secret
  Ogimet/Meteostat/HKO source identity.
- `verify_truth_surfaces` remains read-only; it only fails readiness for
  training-allowed obs_v2 rows missing JSON provenance identity when dedicated
  payload columns do not exist.
- The packet also registered current operations pointers and test-topology
  `last_used` dates for directly reused trusted tests.

## Critic

PASS. Non-blocking evidence/test-depth notes were addressed before closeout.
