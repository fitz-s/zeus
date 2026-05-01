# U2 work record — raw provenance schema

Date: 2026-04-27
Branch: plan-pre5
Task: R3 U2 raw provenance schema — five projection backbone and command envelope gate
Changed files:
- docs/AGENTS.md / docs/README.md / docs/operations/AGENTS.md / docs/operations/current_state.md
- architecture/docs_registry.yaml / architecture/AGENTS.md / workspace_map.md / docs/reference/AGENTS.md
- architecture/topology.yaml / architecture/test_topology.yaml / architecture/source_rationale.yaml / architecture/module_manifest.yaml
- docs/operations/task_2026-04-26_ultimate_plan/r3/boot/U2_codex_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml
- docs/operations/task_2026-04-26_ultimate_plan/r3/learnings/U2_codex_2026-04-27_topology_profile.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/_confusion/U2_schema_ddl_vs_acceptance_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/U2_work_record_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/receipt.json
- docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/U2.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/U2_pre_close_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/U2_post_close_2026-04-27.md
- docs/reference/modules/state.md / docs/reference/modules/execution.md
- src/state/db.py / src/state/venue_command_repo.py / src/execution/executor.py
- tests/test_provenance_5_projections.py / tests/test_executable_market_snapshot_v2.py / tests/test_command_bus_types.py / tests/test_command_recovery.py / tests/test_venue_command_repo.py / tests/test_executor_command_split.py / tests/test_neg_risk_passthrough.py / tests/test_digest_profile_matching.py

Summary:
Implemented U2's raw provenance backbone: append-only venue submission envelopes, order facts, trade facts, position lots, and provenance envelope events; `venue_commands.envelope_id` is required with the U1 `snapshot_id` gate; executor entry/exit flows now persist a pre-submit `VenueSubmissionEnvelope` before SDK contact; the command insert envelope gate validates token+side+price+size so a command cannot cite a different order shape. Added U2 topology routing and documented DDL-vs-acceptance schema decisions.

Verification:
- `r3_drift_check.py --phase U2` -> GREEN.
- `pytest -q -p no:cacheprovider tests/test_provenance_5_projections.py` -> 13 passed.
- `pytest -q -p no:cacheprovider tests/test_executable_market_snapshot_v2.py tests/test_v2_adapter.py` -> 33 passed.
- `pytest -q -p no:cacheprovider tests/test_command_bus_types.py tests/test_command_recovery.py tests/test_venue_command_repo.py` -> 104 passed.
- `pytest -q -p no:cacheprovider tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py` -> 47 passed, 2 skipped.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_u2_raw_provenance_routes_to_u2_profile_not_heartbeat tests/test_neg_risk_passthrough.py tests/test_collateral_ledger.py` -> 36 passed, 4 deprecation warnings.
- Combined reviewer rerun -> 201 passed, 2 skipped.
- `git diff --check` -> clean.
- `topology_doctor --map-maintenance --map-maintenance-mode closeout` -> ok.
- `topology_doctor --planning-lock ... --plan-evidence r3/ULTIMATE_PLAN_R3.md` -> ok.
- Post-close third-party critic Ampere -> PASS; verifier Beauvoir -> PASS; M1 may be unfrozen.
- Post-close verifier focused rerun -> 155 passed.
- Leader post-close recheck: `r3_drift_check.py --phase U2` -> GREEN; `tests/test_provenance_5_projections.py` -> 13 passed.

Next:
U2 is COMPLETE and post-close third-party critic/verifier passed. M1 is now unfrozen as the next ready phase.
