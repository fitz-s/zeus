# P0 Legacy Hourly Evidence View Work Log

Date: 2026-04-25
Branch: `midstream_remediation`
Status: implementation verified; final critic/verifier review passed; ready to
commit.

Task: POST_AUDIT_HANDOFF 4.2.B evidence-only view for the legacy
`hourly_observations` table and proof that canonical bare-table reads remain
linted.

Changed files:
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json`
- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `src/state/db.py`
- `scripts/semantic_linter.py`
- `tests/test_architecture_contracts.py`
- `tests/test_semantic_linter.py`
- `tests/test_truth_surface_health.py`

Summary:
- Freeze a small 4.2.B packet before touching `src/state/db.py`.
- Keep implementation scope to a read-only evidence view and a focused schema
  contract test.
- Reuse the existing semantic-linter bare `hourly_observations` rule, with a
  narrow DDL exception only for creating the explicit evidence view.

Verification:
- Planning-lock, work-record, receipt, current-state receipt, and
  map-maintenance gates will be rerun after packet freeze and after
  implementation.

Next:
- Wait for architect review on the packet boundary.
- Implement only after architect scope review is read.

## Phase Entry

Context rebuilt:

- Reread `AGENTS.md`, `workspace_map.md`,
  `docs/operations/current_state.md`, and `docs/operations/AGENTS.md`.
- Reread POST_AUDIT_HANDOFF 4.2.B.
- Read scoped `src/AGENTS.md`, `src/state/AGENTS.md`, `scripts/AGENTS.md`,
  and `tests/AGENTS.md`.
- Read high-risk overlays:
  `architecture/self_check/zero_context_entry.md` and
  `architecture/self_check/authority_index.md`.
- Read `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`,
  `architecture/task_boot_profiles.yaml`, and
  `architecture/fatal_misreads.yaml`.
- Ran `python3 scripts/topology_doctor.py --task-boot-profiles` -> passed.
- Ran topology navigation for 4.2.B candidate files. It returned `ok: true`
  but matched the broad `modify data ingestion` profile; this packet narrows
  the work to schema-only evidence view + focused schema contract test.
- Ran schema scout and semantic-linter scout:
  - schema scout mapped `hourly_observations` columns and view insertion point
    in `src/state/db.py::init_schema`;
  - linter scout confirmed `scripts/semantic_linter.py` already rejects bare
    `hourly_observations` reads and `tests/test_semantic_linter.py` already
    covers the explicit evidence view allowance.

## Planned Change Set

Expected changed files:

- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/plan.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/work_log.md`
- `docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json`
- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `src/state/db.py`
- `scripts/semantic_linter.py`
- `tests/test_architecture_contracts.py`
- `tests/test_semantic_linter.py`
- `tests/test_truth_surface_health.py`

## Architect Review

Architect verdict: PASS for a narrow schema-and-antibody packet with fresh
planning-lock evidence. The packet must not be treated as linter-development
work because bare `hourly_observations` containment already exists. The missing
piece is the schema-level evidence adapter in `init_schema` plus a trusted
regression test. Architect also directed the implementation to keep exact
legacy column names/order and to avoid row filters that would imply new
time-safety or training-safety semantics.

## Implementation

Implemented:

- `src/state/db.py`
  - Added `v_evidence_hourly_observations` immediately after the legacy
    `hourly_observations` table DDL.
  - Used an explicit column list:
    `id`, `city`, `obs_date`, `obs_hour`, `temp`, `temp_unit`, `source`.
  - Kept the view as a passthrough adapter with no safety-implying filters.
- `tests/test_architecture_contracts.py`
  - Added a trusted schema contract proving `init_schema` creates the view, the
    ordered column list is stable, and one inserted legacy row is mirrored
    exactly through the view.
- `scripts/semantic_linter.py`
  - Added a narrow exemption for the exact evidence-view creation statement
    only: the explicit passthrough columns from `hourly_observations` to
    `v_evidence_hourly_observations`.
  - Rejected filtered or joined variants so future evidence-view edits cannot
    silently add semantics under the DDL exemption.
  - Unsafe `SELECT/FROM hourly_observations` remains blocked.
- `tests/test_semantic_linter.py`
  - Added tests proving the evidence-view DDL is allowed while a later bare
    legacy table read in the same file is still rejected.
  - Added negative tests proving filtered or joined evidence-view DDL is still
    rejected.
- `tests/test_truth_surface_health.py`
  - Corrected `TestGhostPositions` to read the authoritative trade DB via
    `get_trade_connection()` instead of legacy `get_connection()`.
  - Updated lifecycle reuse metadata after auditing the live-DB assumption.

Verification:
- `.venv/bin/python -m py_compile src/state/db.py scripts/semantic_linter.py`
  -> passed.
- `.venv/bin/python -m pytest -q tests/test_architecture_contracts.py -k legacy_hourly`
  -> 1 passed, 93 deselected.
- `.venv/bin/python -m pytest -q tests/test_semantic_linter.py -k hourly_observations`
  -> 21 passed, 20 deselected.
- `.venv/bin/python scripts/semantic_linter.py --check src/state/db.py`
  -> passed.
- `.venv/bin/python -m pytest -q tests/test_architecture_contracts.py`
  -> 72 passed, 22 skipped.
- `.venv/bin/python -m pytest -q tests/test_semantic_linter.py`
  -> 41 passed.
- `.venv/bin/python -m pytest -q tests/test_db.py`
  -> 32 passed, 19 skipped.
- `.venv/bin/python -m pytest -q tests/test_truth_surface_health.py`
  -> initially failed because `TestGhostPositions.test_no_ghost_positions`
  queried legacy `zeus.db` through `get_connection()` while `trade_decisions`
  belongs to the trade DB authority surface. After the targeted test fix:
  54 passed, 5 skipped.
- `.venv/bin/python scripts/semantic_linter.py --check src/state/db.py tests/test_architecture_contracts.py scripts/semantic_linter.py tests/test_semantic_linter.py`
  -> failed on pre-existing self/test lint limitations: region-cluster literals
  inside the linter/test fixtures and bad-example SQL in tests. The actionable
  production-source check for this packet is the passing `src/state/db.py`
  semantic-linter gate above.

Next:
- Run topology closeout gates, critic/verifier review, address findings, then
  commit and push only the scoped files.

## Critic Fixes

Initial critic review found two closeout blockers:

- The semantic-linter DDL exemption was too broad and could allow filtered or
  joined `v_evidence_hourly_observations` definitions.
- The required state gate was red because
  `TestGhostPositions.test_no_ghost_positions` queried legacy `zeus.db` through
  `get_connection()` even though `trade_decisions` belongs to the trade DB
  authority surface.

Fixes applied:

- Tightened `_is_evidence_hourly_observations_view_statement()` so it accepts
  only the exact explicit-column passthrough view DDL and rejects filtered or
  joined variants.
- Added negative semantic-linter tests for filtered and joined view DDL.
- Updated `TestGhostPositions` to use `get_trade_connection()` and reran the
  required state gate.

Closeout verification after fixes:

- `.venv/bin/python -m py_compile src/state/db.py scripts/semantic_linter.py tests/test_truth_surface_health.py`
  -> passed.
- `.venv/bin/python -m pytest -q tests/test_architecture_contracts.py`
  -> 72 passed, 22 skipped.
- `.venv/bin/python -m pytest -q tests/test_semantic_linter.py`
  -> 41 passed.
- `.venv/bin/python -m pytest -q tests/test_truth_surface_health.py`
  -> 54 passed, 5 skipped.
- `.venv/bin/python -m pytest -q tests/test_db.py`
  -> 32 passed, 19 skipped.
- `.venv/bin/python -m pytest -q tests/test_architecture_contracts.py tests/test_truth_surface_health.py`
  -> 126 passed, 27 skipped.
- `.venv/bin/python scripts/semantic_linter.py --check src/state/db.py`
  -> passed.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <4.2.B files> --plan-evidence docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/plan.md --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --work-record --changed-files <4.2.B files> --work-record-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/work_log.md --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --change-receipts --changed-files <4.2.B files> --receipt-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --current-state-receipt-bound --receipt-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <4.2.B files> --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files tests/test_architecture_contracts.py tests/test_truth_surface_health.py --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --scripts --json`
  -> `{"ok": true, "issues": []}`.
- `python3 scripts/topology_doctor.py --tests --json`
  -> `{"ok": true, "issues": []}`.
- `git diff --check -- <4.2.B files>`
  -> passed.
- `python3 -m json.tool docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json`
  -> passed.

Final review:

- Verifier: PASS. Receipt-bound files, current-state alignment, schema view,
  linter antibodies, state-gate correction, and topology gates all matched the
  4.2.B packet.
- Critic: PASS. The earlier blockers were resolved; no scope drift was found
  inside the 4.2.B receipt-bound diff.

## Verification Plan

- `.venv/bin/python -m py_compile src/state/db.py`
- `.venv/bin/python -m pytest -q tests/test_architecture_contracts.py -k legacy_hourly`
- `.venv/bin/python -m pytest -q tests/test_semantic_linter.py -k hourly_observations`
- `.venv/bin/python -m pytest -q tests/test_truth_surface_health.py`
- `.venv/bin/python scripts/semantic_linter.py --check src/state/db.py tests/test_architecture_contracts.py`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <4.2.B files> --plan-evidence docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/plan.md --json`
- `python3 scripts/topology_doctor.py --work-record --changed-files <4.2.B files> --work-record-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/work_log.md --json`
- `python3 scripts/topology_doctor.py --change-receipts --changed-files <4.2.B files> --receipt-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json --json`
- `python3 scripts/topology_doctor.py --current-state-receipt-bound --receipt-path docs/operations/task_2026-04-23_midstream_remediation/phases/task_2026-04-25_p0_legacy_hourly_evidence_view/receipt.json --json`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --changed-files <4.2.B files> --json`
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files tests/test_architecture_contracts.py tests/test_truth_surface_health.py --json`
- `git diff --check -- <4.2.B files>`

## Next

- Wait for architect review on the packet boundary.
- Implement only after architect scope review is read.
