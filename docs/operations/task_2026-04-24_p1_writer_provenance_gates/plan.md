# P1.2 Writer Provenance Gates Ralplan Packet

Date: 2026-04-24
Branch: `data-improve`
Status: planning-only packet for observation_instants_v2 writer provenance gates

## Task

Plan the narrow implementation slice that wires P1.1 source-role registry
semantics into the `observation_instants_v2` typed writer. P1.2 must prevent
future rows from relying on schema defaults for `source_role`,
`training_allowed`, and `causality_status`, without mutating existing DB rows,
changing schema ownership, or expanding the `ObsV2Row` constructor contract.

Full row metric identity (`temperature_metric`, `physical_quantity`,
`observation_field`) is deliberately not solved by this narrow slice because
the current row contract carries both `running_max` and `running_min` on one
row and has no single-track metric identity input. If that identity must be
written now, this packet stops and a wider metric-identity/caller packet opens.

## Route

- P1.1 source-role registry evidence:
  `docs/operations/task_2026-04-24_p1_source_role_registry/`
- P1.1 implementation commit:
  `af7dd5289748b2cb6256df56f97f421490bf1925`
- Forensic package input:
  `docs/archives/packets/zeus_world_data_forensic_audit_package_2026-04-23/`
- Current fact surfaces:
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`,
  `docs/operations/known_gaps.md`

## Semantic Boot

Required profiles:

- `hourly_observation_ingest`: writer construction must fail closed before a
  row reaches INSERT. Provenance is row-level, not batch-level.
- `source_routing`: source tags must keep the P1.1 split between primary
  historical-hourly sources and fallback evidence.
- `settlement_semantics`: P1.2 does not assign settlement truth or market
  identity; `settlement_truth` remains out of scope.
- `docs_authority`: this packet routes implementation scope; it does not
  create durable architecture law.
- `graph_review`: Code Review Graph is derived context only. Current graph
  red state remains non-authority fallback if targeted gates pass.

Fatal shortcuts explicitly blocked:

- Do not rely on SQLite ALTER defaults (`training_allowed DEFAULT 1`,
  `causality_status DEFAULT 'OK'`) as semantic truth for new writer rows.
- Do not promote fallback WU Ogimet/Meteostat rows to training-eligible.
- Do not promote HKO rows without a fresh source-validity audit.
- Do not infer `settlement_truth` from city/date/source tag.
- Do not mutate existing `state/**` DBs as part of this packet.

## Decision

Chosen implementation direction for the later P1.2 Ralph loop:

- Keep the `ObsV2Row` public constructor stable.
- Derive writer-side `source_role`, `training_allowed`, and
  `causality_status` from existing validated fields: `city`, `source`, and
  non-empty parsed `provenance_json`.
- Use the P1.1 `source_role_assessment_for_city_source` helper as the sole
  registry source for `source_role` and `training_allowed`.
- Extend `_INSERT_SQL` and `_row_to_tuple` so new writes carry explicit
  `source_role`, `training_allowed`, and `causality_status` values rather
  than relying on SQLite defaults.
- Leave caller scripts untouched unless plan review finds a blocker. Existing
  call-site tests and py_compile runs become compatibility proof.

Rejected options:

- Constructor expansion in P1.2: rejected because every production writer
  builds `ObsV2Row` directly; adding required constructor fields would widen
  the packet into all caller scripts.
- Writer-only implementation with optional semantic defaults: rejected because
  it would reproduce the forensic failure where new rows silently inherit
  permissive DB defaults. Writer-local derived values are acceptable only when
  they come from the P1.1 registry and validated provenance.
- Schema/DB migration in P1.2: rejected because columns already exist and DB
  mutation belongs to a separate packet.
- Writing `temperature_metric`, `physical_quantity`, and `observation_field`
  in P1.2: rejected for the narrow slice because current `ObsV2Row` rows do
  not carry one unambiguous metric identity. A later metric-identity packet
  must decide whether to split rows, add constructor fields, or add caller
  context.
- Calibration/replay reader changes in P1.2: rejected because P1.2 is the
  write boundary only; consumer safe views/adapters belong to later P1 slices.
- Editing `src/data/tier_resolver.py`: rejected unless P1.2 plan review finds
  a blocker in P1.1 helpers. The registry was frozen in P1.1.

## Future Implementation Scope

Allowed code/test files after this plan is reviewed, pushed, and post-close
reviewed:

- `src/data/observation_instants_v2_writer.py`
- `tests/test_obs_v2_writer.py`
- `tests/test_hk_rejects_vhhh_source.py`

Closeout bookkeeping files after verification only:

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/work_log.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/receipt.json`

Forbidden files:

- `state/**`
- `.code-review-graph/graph.db`
- `src/state/**`
- `src/calibration/**`
- `src/execution/**`
- `src/data/tier_resolver.py`
- `docs/authority/**`
- `architecture/**`
- production DBs and generated runtime JSON

## Planned Writer Semantics

Minimum validation rules for future implementation:

- `source_role` must equal the P1.1 registry assessment for `(city, source)`.
- `training_allowed` must equal the P1.1 assessment result and must never be
  caller-overridden to `True` for fallback, HKO, unknown, model-only, or
  missing-provenance rows.
- `causality_status` must be explicit in the INSERT tuple. P1.2 should derive
  a small initial set from rows that already pass the writer's A2 allowlist,
  for example `OK` for training-eligible primary rows,
  `RUNTIME_ONLY_FALLBACK` for allowed fallback evidence, and
  `REQUIRES_SOURCE_REAUDIT` for HKO. Unknown, model-only, missing, or
  unrecognized source tags remain hard rejects before INSERT under existing A2
  rules; P1.2 must not relax source allowlists to store those rows.
- `provenance_json` must remain non-empty JSON with the current required
  `tier` key. Stronger provenance-key requirements are a separate widening
  decision; if P1.2 needs them, stop and widen the packet.

Explicit non-goals for this narrow P1.2:

- No new required `ObsV2Row` constructor fields.
- No `payload_hash`, `source_url_or_file`, `parser_version`, or
  `station_registry_version` contract.
- No metric identity writes for `temperature_metric`, `physical_quantity`, or
  `observation_field`.
- No live-row retrofit/backfill and no `INSERT OR REPLACE` redesign.

## Planning-Only Scope

Planning commit changed files:

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/plan.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/work_log.md`
- `docs/operations/task_2026-04-24_p1_writer_provenance_gates/receipt.json`

## Phase Topology

Before implementation:

1. Run planning topology gates on the planning-only changed-file set.
2. Run architect review on scope, constructor/call-site coupling, and
   validation semantics.
3. Run critic review on false-confidence paths, especially defaulted
   `training_allowed` and fallback promotion.
4. Run verifier review on route, receipt, and gate evidence.
5. Apply plan-review fixes, rerun planning gates, commit, and push.
6. Run post-close third-party critic/verifier before starting implementation.

During the later implementation Ralph loop:

1. Refresh topology for writer plus all `ObsV2Row` call sites.
2. Add tests first for required identity fields, registry mismatch rejection,
   fallback/HKO training ineligibility, and SQL persistence of the new
   writer-derived fields.
3. Implement writer-local derivation and INSERT tuple updates without changing
   caller constructors.
4. Run targeted writer/caller tests and topology closeout.
5. Run critic, fix, rerun verification, then verifier.
6. Commit/push only scoped implementation files.

## Planned Verification

Future P1.2 implementation must run at minimum:

- `.venv/bin/python -m py_compile src/data/observation_instants_v2_writer.py scripts/backfill_obs_v2.py scripts/fill_obs_v2_dst_gaps.py scripts/fill_obs_v2_meteostat.py scripts/hko_ingest_tick.py`
- `.venv/bin/python -m pytest tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_tier_resolver.py -q`
- `.venv/bin/python -m pytest tests/test_backfill_scripts_match_live_config.py -q`
- Targeted topology gates: `planning-lock`, `work-record`,
  `change-receipts`, `current-state-receipt-bound`, `map-maintenance`,
  `freshness-metadata`, and `git diff --check`.

## Stop Conditions

- If implementation requires `src/state/**` schema changes, stop and open a
  schema packet.
- If implementation requires existing DB row migration or backfill, stop and
  open a DB/data packet.
- If implementation requires new required `ObsV2Row` constructor fields or
  caller-provided metric identity, stop and widen the packet to include every
  production writer caller.
- If implementation requires unknown/model-only rows to reach INSERT, stop and
  open a wider packet. Narrow P1.2 keeps existing A2 hard rejects.
- If implementation requires stronger provenance keys beyond the current
  non-empty JSON object with `tier`, stop and widen the packet to caller
  provenance-shape work.
- If writer integration needs calibration/replay reader changes, stop and open
  a consumer safe-view packet.
- If HKO training eligibility is requested, stop for fresh source-validity
  audit.
- If source-role registry semantics need changes, stop and create a P1.1
  follow-up instead of editing `tier_resolver.py` inside P1.2.
