# P0 Legacy Hourly Evidence View Packet

Date: 2026-04-25
Branch: `midstream_remediation`
Status: active phase-entry for POST_AUDIT_HANDOFF 4.2.B.

## Task

Close POST_AUDIT_HANDOFF 4.2.B by adding an explicit evidence-only read view
for the legacy `hourly_observations` table and proving canonical paths remain
guarded by the existing semantic linter.

4.2.B exists because `hourly_observations` is a lossy legacy compatibility
table without UTC/timezone/provenance guarantees. Consumers that need legacy
evidence should be forced to use an explicitly named evidence surface, while
canonical training/replay/live paths remain barred from bare table reads.

## Required Phase Entry

Completed before implementation:

- Reread root `AGENTS.md`.
- Reread `workspace_map.md`, `docs/operations/current_state.md`, and
  `docs/operations/AGENTS.md`.
- Reread POST_AUDIT_HANDOFF 4.2.B.
- Read scoped guidance for `src/`, `src/state/`, `scripts/`, and `tests/`.
- Read high-risk overlays:
  `architecture/self_check/zero_context_entry.md` and
  `architecture/self_check/authority_index.md`.
- Read current-fact companions:
  `docs/operations/current_data_state.md` and
  `docs/operations/current_source_validity.md`.
- Read semantic boot surfaces:
  `architecture/task_boot_profiles.yaml` and
  `architecture/fatal_misreads.yaml`.
- Ran `python3 scripts/topology_doctor.py --task-boot-profiles`.
- Ran topology navigation for the 4.2.B candidate files. The digest matched
  the broad `modify data ingestion` profile and produced data-ingest
  `allowed_files`, while `source_rationale` correctly identified
  `src/state/db.py` as K2 runtime DB schema with
  `tests/test_architecture_contracts.py tests/test_truth_surface_health.py`
  gates. This packet records that mismatch and narrows the implementation to
  a schema-only evidence view plus focused schema contract test.
- Scout subagents mapped:
  - the existing `hourly_observations` table DDL in `src/state/db.py`
  - the existing semantic-linter `hourly_observations` bare-read rule
  - existing semantic-linter tests and acceptance commands

## Scope

Allowed implementation files:

- `src/state/db.py`
- `tests/test_architecture_contracts.py`

Conditional linter implementation files, used only if the new evidence-view DDL
itself proves the existing lint rule needs a narrower adapter-creation
exception:

- `scripts/semantic_linter.py`
- `tests/test_semantic_linter.py`

Conditional gate-remediation file, used only if the required state gate is
blocked by a test-surface mismatch unrelated to the schema-view implementation:

- `tests/test_truth_surface_health.py`

Allowed evidence/control files:

- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-25_p0_legacy_hourly_evidence_view/plan.md`
- `docs/operations/task_2026-04-25_p0_legacy_hourly_evidence_view/work_log.md`
- `docs/operations/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json`

Forbidden files:

- `state/**`
- `.code-review-graph/graph.db`
- `.omx/**`
- production DBs and generated runtime JSON
- `src/contracts/settlement_semantics.py`
- replay/live/runtime consumer rewiring in `src/engine/**` or
  `src/execution/**`
- canonical v2 population or market-event backfill
- legacy settlement promotion

## Plan

1. Add a read-only evidence view in `src/state/db.py::init_schema` immediately
   after the legacy `hourly_observations` table DDL:
   `v_evidence_hourly_observations`.
2. Build the view as a plain projection of legacy columns:
   `id`, `city`, `obs_date`, `obs_hour`, `temp`, `temp_unit`, `source`.
   Do not add filters that imply the legacy rows are time-safe or training-safe.
3. Use `DROP VIEW IF EXISTS` then `CREATE VIEW` so a future view definition
   tightening propagates through `init_schema`.
4. Add a focused architecture contract test that runs `init_schema` against an
   in-memory SQLite DB, asserts the view exists as a view, asserts the exact
   column list, inserts one legacy row, and verifies the view mirrors that row.
5. Do not change semantic-linter logic unless a failing acceptance command shows
   the current rule cannot distinguish the evidence-view creation DDL from an
   unsafe bare `hourly_observations` read.
6. If a required state gate is red for a pre-existing test-surface mismatch,
   make only the smallest test fix needed to exercise the current authority
   surface; do not quarantine or xfail a high-sensitivity test.
7. Update work log, receipt, and current control pointer after verification.

## Acceptance

- `v_evidence_hourly_observations` exists after `init_schema`.
- The view exposes the exact legacy evidence columns and carries no new safety
  claims.
- Existing semantic-linter tests continue to prove bare `FROM/JOIN
  hourly_observations` is rejected and `v_evidence_hourly_observations` is
  allowed.
- No production DB, runtime state, replay/live consumer, v2 population, or
  settlement promotion is touched.
