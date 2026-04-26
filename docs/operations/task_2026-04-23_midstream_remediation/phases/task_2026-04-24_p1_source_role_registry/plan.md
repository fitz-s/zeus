# P1.1 Source-Role Registry Ralplan Packet

Date: 2026-04-24
Branch: `data-improve`
Status: planning-only packet for the first P1 provenance-hardening slice

## Task

Freeze the source-role and training-eligibility registry decision before any
writer, schema, DB, settlement, or calibration consumer changes. P1.1 creates
the plan and review gates for a narrow implementation slice that can classify
source roles conservatively without silently promoting legacy or fallback rows.

## Route

- Mainline ralplan: `.omx/plans/post-p1-forensic-mainline-ralplan-2026-04-24.md`
- P0 containment evidence:
  `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p0_data_audit_containment/`
- Post-audit handoff:
  `docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md`
- Forensic package input:
  `docs/archives/packets/zeus_world_data_forensic_audit_package_2026-04-23/`
- P1.1 forensic route rule: the archived path above is canonical for this
  packet. Any local
  `docs/operations/zeus_world_data_forensic_audit_package_2026-04-23/` copy is
  duplicate scratch/input material and is not route authority. Older `.omx`
  plan or handoff references to the pre-archive operations path must be
  resolved to the archived path before use.
- Current fact surfaces:
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`,
  `docs/operations/known_gaps.md`

## Semantic Boot

Required profiles:

- `source_routing`: P1.1 must not confuse endpoint availability, ICAO station
  identity, or source tags with settlement correctness. It reuses existing
  source-tier knowledge but does not change settlement source routing.
- `settlement_semantics`: P1.1 must not assign settlement truth,
  market identity, rounding, or high/low settlement provenance. Those belong
  to later P1 slices.
- `hourly_observation_ingest`: P1.1 defines registry behavior only. Writer
  enforcement and row provenance gates are P1.2.
- `docs_authority`: operations packet evidence is routing/control context,
  not durable law.
- `graph_review`: Code Review Graph may guide blast-radius checks, but stale
  or partial graph output never waives source-law proof gates.

Fatal shortcuts explicitly blocked:

- API data availability does not prove settlement source correctness.
- WU website daily summaries are not WU API hourly extrema.
- Daily, Day0, hourly, and forecast sources are not interchangeable.
- Airport station identity is not city settlement identity.
- HKO Hong Kong rows remain cautionary until a fresh audit proves otherwise.

## Decision

Chosen path: extend the existing `src/data/tier_resolver.py` source-tier
surface with explicit source-role and training-eligibility registry helpers,
then lock the behavior with `tests/test_tier_resolver.py`.

Rationale:

- The file already owns city source-tier allow/expect rules and has focused
  tests, so P1.1 can remain narrow.
- Existing registration avoids adding a new source module and avoids
  manifest/source-rationale churn before the taxonomy is stable.
- Writer integration can consume the registry in P1.2 without changing DB
  schema or production rows in P1.1.

Rejected options:

- New `src/contracts/source_role_registry.py`: rejected for P1.1 because it
  adds a new source surface and registry/manifest obligations before the
  taxonomy is proven against existing source-tier tests.
- Change `src/state/**` or v2 schema now: rejected because the schema already
  has `source_role` and `training_allowed` columns, and schema ownership is a
  planning-lock surface outside this slice.
- Wire `observation_instants_v2_writer.py` now: rejected because writer
  migration needs caller, provenance, and fixture review; defer to P1.2.
- Add calibration/replay eligibility adapters now: rejected because consumers
  should not trust registry semantics before writer provenance gates exist.

## Registry Semantics

P1.1 implementation must default to quarantine-first.

Minimum planned source roles:

- `historical_hourly`: eligible only when a row's source tag is the city's
  primary historical-hourly source, provenance exists, and the later writer
  gate supplies metric/quantity/field identity.
- `settlement_truth`: not assigned by P1.1. It remains a future settlement
  identity decision and must not be inferred from city/date/source tag alone.
- `fallback_evidence`: never training-eligible in P1.1.
- `runtime_monitoring`: never training-eligible in P1.1.
- `model_only`: never training-eligible in P1.1.
- `unknown`: never training-eligible.

Exact planned mapping for current `tier_resolver.py` inputs:

- For Tier 1 `WU_ICAO` cities, `expected_source_for_city(city)` /
  `wu_icao_history` maps to `historical_hourly` when all provenance checks pass.
  `ogimet_metar_<icao>` and `meteostat_bulk_<icao>` are allowed fallback source
  tags for continuity, but map to `fallback_evidence` and remain
  training-ineligible in P1.1.
- For Tier 2 `OGIMET_METAR` cities, the city-specific
  `expected_source_for_city(city)` value such as `ogimet_metar_ltfm`,
  `ogimet_metar_uuww`, or `ogimet_metar_llbg` maps to `historical_hourly`
  when provenance checks pass. No secondary fallback source is training
  eligible.
- For Tier 3 `HKO_NATIVE`, `hko_hourly_accumulator` maps to
  `fallback_evidence` until a fresh Hong Kong source-validity audit proves it
  can be promoted. It is never training-eligible in P1.1.
- Source tags that are missing, empty, unrecognized, Open-Meteo/model-derived,
  or not evaluated with both city and source tag map to `unknown` or
  `model_only` and remain training-ineligible.

Legacy empty role strings, missing source tags, HKO caution rows, and rows with
missing provenance must return not training-eligible. P1.1 may expose reasons
as stable strings for tests and later diagnostics, but must not mutate existing
rows or export derived JSON.

## Scope

Planning-only changed files:

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_source_role_registry/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_source_role_registry/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_source_role_registry/receipt.json`

Allowed future implementation code files, after this plan is reviewed,
pushed, and post-close reviewed:

- `src/data/tier_resolver.py`
- `tests/test_tier_resolver.py`

Closeout bookkeeping files, after code verification only:

- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_source_role_registry/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_source_role_registry/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_source_role_registry/receipt.json`

Forbidden files:

- `state/**`
- `.code-review-graph/graph.db`
- `src/state/**`
- `src/execution/**`
- `src/calibration/**`
- `src/data/observation_instants_v2_writer.py`
- `docs/authority/**`
- `architecture/**`
- production DBs and generated runtime JSON

## Phase Topology

Before implementation:

1. Run planning topology gates on this packet's planning-only changed files.
2. Run architect review on the plan, with emphasis on scope, taxonomy, and
   P1.2 handoff boundaries.
3. Run critic review on the plan, with emphasis on false-confidence paths and
   accidental widening into DB/schema/writer work.
4. Run verifier review on the plan and gate evidence.
5. Apply plan-review fixes, then rerun planning gates.
6. Commit and push the plan packet.
7. Run a post-close third-party critic/verifier pass before treating P1.1 as
   frozen for implementation.

During the later implementation Ralph loop:

1. Refresh task context and rerun targeted topology routing for
   `src/data/tier_resolver.py` and `tests/test_tier_resolver.py`.
2. Add regression tests first in `tests/test_tier_resolver.py`.
3. Implement registry helpers in `src/data/tier_resolver.py`.
4. Run targeted verification and topology closeout.
5. Run critic, fix, rerun verification, then verifier.
6. Commit/push code files plus verification-closeout bookkeeping only after
   tests, topology gates, critic fixes, and verifier pass.

## Acceptance For This Planning Packet

- `current_state.md` and `docs/operations/AGENTS.md` point at this P1.1 packet.
- Planning receipt names exactly the planning-only files changed by this commit.
- Planning topology gates pass for the planning-only changed-file set.
- Architect, critic, and verifier agree the plan is narrow enough to start
  P1.1 implementation later.
- After the planning commit is pushed, a third-party critic/verifier pass
  confirms that the committed packet still matches this scope.
- No runtime source, DB, graph DB, state, authority, or architecture files are
  modified by this planning packet.

## Planned Implementation Verification

Future P1.1 implementation must run at minimum:

- `.venv/bin/python -m py_compile src/data/tier_resolver.py`
- `.venv/bin/python -m pytest tests/test_tier_resolver.py -q`
- `.venv/bin/python -m pytest tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_backfill_scripts_match_live_config.py -q`
- Compatibility proof that existing `tier_resolver.py` public APIs and
  constants used by `src/data/observation_instants_v2_writer.py` and
  `scripts/backfill_obs_v2.py` are unchanged; registry helpers must be additive
  unless a later packet widens the scope.
- `python scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_source_role_registry/plan.md`
- `python scripts/topology_doctor.py --work-record --changed-files ... --work-record-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_source_role_registry/work_log.md`
- `python scripts/topology_doctor.py --change-receipts --changed-files ... --receipt-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-24_p1_source_role_registry/receipt.json`
- `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files ...`
- `git diff --check -- <P1.1 changed files>`

## Stop Conditions

- If source-role registry work requires writer/caller behavior, stop and open
  P1.2.
- If the registry cannot distinguish primary source tags from fallback source
  tags using pure `(city, source_tag)` inputs, stop and open P1.2 for writer
  provenance expansion instead of guessing.
- If settlement market identity or settlement-truth assignment is needed, stop
  and open the settlement identity slice.
- If HKO rows need training eligibility, stop for fresh source-validity audit.
- If broad topology/source gates fail on known global registry debt, record the
  known-red status and rely on targeted packet gates for closeout.
