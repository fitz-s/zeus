# M1 work record — lifecycle grammar + cycle_runner proxy

Date: 2026-04-27
Branch: plan-pre5
Task: R3 M1 lifecycle grammar — command-side grammar amendment and RED durable cancel proxy
Changed files:
- docs/AGENTS.md / docs/README.md / docs/operations/AGENTS.md / docs/operations/current_state.md / architecture/AGENTS.md / workspace_map.md / architecture/docs_registry.yaml / docs/reference/AGENTS.md
- architecture/topology.yaml / tests/test_digest_profile_matching.py / architecture/source_rationale.yaml
- architecture/test_topology.yaml / architecture/module_manifest.yaml
- docs/reference/modules/execution.md / docs/reference/modules/state.md / docs/reference/modules/engine.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M1_codex_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml
- docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_at_gate_edge_M1.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/M1_work_record_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/M1_pre_close_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/receipt.json
- src/execution/command_bus.py / src/state/venue_command_repo.py / src/execution/command_recovery.py / src/engine/cycle_runner.py
- tests/test_command_grammar_amendment.py / tests/test_riskguard_red_durable_cmd.py / tests/test_command_bus_types.py / tests/test_venue_command_repo.py

Summary:
Implemented M1 to the gate edge: added command-side grammar states/events, preserved U2 order/trade fact separation and NC-NEW-E (`RESTING` not a `CommandState`), made unresolved lookup derive from `IN_FLIGHT_STATES`, and added RED force-exit durable CANCEL proxy command emission only inside `cycle_runner._execute_force_exit_sweep`. RiskGuard remains non-writing; no live SDK side effects were added. M1 remains blocked from full completion by the open `INV-29 amendment` operator/governance gate.

Verification:
- `pytest -q -p no:cacheprovider tests/test_command_grammar_amendment.py tests/test_riskguard_red_durable_cmd.py` -> 14 passed.
- `pytest -q -p no:cacheprovider tests/test_command_bus_types.py tests/test_venue_command_repo.py tests/test_command_recovery.py tests/test_executor_command_split.py` -> 123 passed.
- Combined M1 focused suite + digest/neg-risk/dual-track antibody -> 142 passed.
- Executor/U1/Z2 regression suite -> 80 passed, 2 skipped.
- `pytest -q -p no:cacheprovider tests/test_runtime_guards.py -k force_exit` -> 1 passed, 118 deselected.
- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M1` -> GREEN.
- `python3 -m py_compile src/execution/command_bus.py src/state/venue_command_repo.py src/execution/command_recovery.py src/engine/cycle_runner.py` -> ok.
- Pre-close critic Parfit -> PASS; verifier Singer -> PASS for COMPLETE_AT_GATE.
- Verifier fresh subset -> 106 passed.
- Critic docstring drift remediated in `src/execution/command_recovery.py`; escaped unicode markers in adjacent recovery comments/docstrings normalized.
- Post-review focused rerun -> 106 passed; `command_recovery.py` py_compile ok; M1 drift GREEN; `git diff --check` clean.
- Post-review topology closeout -> ok (map-maintenance, planning-lock, closeout JSON).

Next:
M1 is at COMPLETE_AT_GATE. Keep M2 frozen until `INV-29 amendment` governance commit + planning-lock receipt is present; then rerun closeout/review before full COMPLETE.
