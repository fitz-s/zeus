# M1 at-gate edge — INV-29 amendment

Date: 2026-04-27
Phase: M1 — Lifecycle grammar + cycle_runner-as-proxy
Status: COMPLETE_AT_GATE candidate; blocked from full COMPLETE by operator gate `INV-29 amendment`.

## Completed engineering work

- Added an explicit M1 topology profile and digest regression so lifecycle grammar work no longer routes to Z3 heartbeat.
- Added grammar-additive command states/events in `src/execution/command_bus.py` without adding `RESTING` to `CommandState`.
- Updated `src/state/venue_command_repo.py` transitions to admit M1 pre-side-effect grammar, timeout/closed-market unknown events, and cancel failure/block events.
- Kept U2 order/trade facts separate from command grammar; `RESTING`, `MATCHED`, `MINED`, and `CONFIRMED` remain out of `CommandState`.
- Updated unresolved command lookup to derive from `IN_FLIGHT_STATES` rather than a stale string literal.
- Updated terminal release handling to derive from `TERMINAL_STATES` so new terminal-like command states stay aligned.
- Added RED force-exit durable CANCEL proxy emission inside `cycle_runner._execute_force_exit_sweep` only; RiskGuard does not write venue commands.
- Preserved no-live-side-effect behavior: RED proxy writes durable command journal + provenance envelope and appends `CANCEL_REQUESTED`; it does not call SDK `cancel_order()` or `place_limit_order()`.

## Gate still open

`docs/operations/task_2026-04-26_ultimate_plan/r3/operator_decisions/INDEX.md` says:

- Gate ID: `INV-29 amendment`
- Phase blocked: M1
- Status: OPEN
- Default: M1 PR fails CI on missing planning-lock receipt.

Therefore M1 must remain `COMPLETE_AT_GATE` / not mergeable until the governance amendment commit + planning-lock receipt exists and is cited.

## Evidence at gate edge

- `pytest -q -p no:cacheprovider tests/test_command_grammar_amendment.py tests/test_riskguard_red_durable_cmd.py` -> `14 passed`.
- `pytest -q -p no:cacheprovider tests/test_command_bus_types.py tests/test_venue_command_repo.py tests/test_command_recovery.py tests/test_executor_command_split.py` -> `123 passed`.
- `pytest -q -p no:cacheprovider tests/test_command_grammar_amendment.py tests/test_riskguard_red_durable_cmd.py tests/test_command_bus_types.py tests/test_venue_command_repo.py tests/test_command_recovery.py tests/test_executor_command_split.py tests/test_digest_profile_matching.py::test_r3_m1_lifecycle_grammar_routes_to_m1_profile_not_heartbeat tests/test_neg_risk_passthrough.py tests/test_dual_track_law_stubs.py::test_red_triggers_active_position_sweep` -> `142 passed`.
- `pytest -q -p no:cacheprovider tests/test_executor.py tests/test_live_execution.py tests/test_executor_command_split.py tests/test_executor_db_target.py tests/test_executor_typed_boundary.py tests/test_executable_market_snapshot_v2.py tests/test_v2_adapter.py` -> `80 passed, 2 skipped`.
- `pytest -q -p no:cacheprovider tests/test_runtime_guards.py -k force_exit` -> `1 passed, 118 deselected`.
- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M1` -> GREEN (`GREEN=15 YELLOW=0 RED=0`).
- `python3 -m py_compile src/execution/command_bus.py src/state/venue_command_repo.py src/execution/command_recovery.py src/engine/cycle_runner.py` -> ok.

## Next action

Operator/governance must close `INV-29 amendment` with a planning-lock receipt. After that, rerun M1 closeout and the required critic/verifier gates before marking COMPLETE and freezing M2.
