# P1.4 Legacy Settlement Evidence Policy - Work Log

Date: 2026-04-24
Branch: `post-audit-remediation-mainline`
Task: P1.4 legacy settlement evidence-only / finalization policy planning

Changed files:
- `docs/AGENTS.md`
- `docs/README.md`
- `architecture/topology.yaml`
- `architecture/docs_registry.yaml`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/plan.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/work_log.md`
- `docs/operations/task_2026-04-24_p1_legacy_settlement_evidence_policy/receipt.json`

Summary:
- Renamed the working branch from `p1-unsafe-observation-quarantine` to
  `post-audit-remediation-mainline`, pushed the renamed branch, and deleted
  the old remote branch.
- Reopened context under current `AGENTS.md`, `workspace_map.md`, operations
  router, current-state, current data/source fact companions, and P1.2/P1.3
  packet boundaries.
- Created the P1.4 planning packet for legacy settlement evidence-only /
  finalization policy. This freezes a read-only diagnostic path and explicitly
  excludes production DB mutation, schema/view DDL, `settlements_v2`
  population, market-identity backfill, eligibility views/adapters, and
  calibration/replay/live consumer rewiring.
- Scout mapped settlement/finality anchors across P1.3, forensic package,
  v2 schema expectations, and readiness tests. The key conclusion is that P1.4
  must make legacy `settlements` evidence-only status explicit, not promote or
  rewrite settlement truth.
- Updated docs/topology registry companions required by map-maintenance for
  the new active operations packet.

Verification:
- Reread `AGENTS.md`.
- Reread `workspace_map.md`.
- Read `docs/AGENTS.md` and `architecture/AGENTS.md` for registry companion
  updates.
- Read `docs/operations/current_state.md`, `docs/operations/AGENTS.md`,
  `docs/operations/current_data_state.md`,
  `docs/operations/current_source_validity.md`, and
  `docs/operations/known_gaps.md`.
- Read P1.2 and P1.3 packet boundaries.
- Read forensic settlement/data-readiness surfaces:
  `07_settlement_alignment_audit.md`, `11_data_readiness_ruling.md`,
  `17_apply_order.md`, `03_table_by_table_truth_audit.md`,
  `08_provenance_and_authority_audit.md`, and
  `validation/required_db_queries.md`.
- `python3 scripts/topology_doctor.py --task-boot-profiles --json` passed.
- `python3 scripts/topology_doctor.py --fatal-misreads --json` passed.
- `python3 scripts/topology_doctor.py --navigation --task "P1.4 legacy settlement evidence-only finalization policy planning" --files docs/operations/current_state.md docs/operations/task_2026-04-24_p1_unsafe_observation_quarantine/plan.md docs/operations/task_2026-04-23_midstream_remediation/POST_AUDIT_HANDOFF_2026-04-24.md docs/operations/known_gaps.md --json`
  returned known global docs/source/history-lore red issues. Those are derived
  routing debt and do not authorize skipping scoped gates.
- First map-maintenance run reported required companion updates for
  `docs/AGENTS.md`, `docs/README.md`, `architecture/topology.yaml`, and
  `architecture/docs_registry.yaml`; the planning scope was widened only for
  those registry companions.
- After registry companion updates, reran JSON validation, planning-lock,
  work-record, change-receipts, current-state receipt binding,
  map-maintenance precommit, freshness metadata, and `git diff --check`; all
  passed for the expanded changed-file set.
- `python3 scripts/topology_doctor.py impact --files <expanded changed-file set>`
  reported no source zones, write routes, hazards, or required tests for this
  planning-only docs/registry packet.
- `.venv/bin/python scripts/semantic_linter.py --check <expanded changed-file set>`
  passed with zero AST files verified, as expected for docs/registry-only
  changes. System `python3` cannot import this linter due local interpreter
  syntax support, so `.venv/bin/python` is the valid command surface.
- Closeout navigation still reports known global docs/source/history-lore red
  issues outside this packet. The P1.4 scoped gates above remain green.
- Critic review returned PROCEED. Non-blocking watch item: future
  implementation must define an accepted finalization-policy evidence
  column/alias contract in script/tests before adding blockers.

Next:
- Commit and push the planning packet.
- Future P1.4 implementation starts only after post-close review and a fresh
  phase entry.
