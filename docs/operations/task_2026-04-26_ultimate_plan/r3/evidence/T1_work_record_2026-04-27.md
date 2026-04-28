# R3 T1 work record — FakePolymarketVenue paper/live parity

Date: 2026-04-27
Branch: plan-pre5
Task: R3 T1 FakePolymarketVenue — same adapter protocol, deterministic failure injection, schema-identical paper/live event shapes
Status: POST-CLOSE PASS; A1 unfrozen for phase entry

Changed files:

Implementation / fake infrastructure:
- `src/venue/polymarket_v2_adapter.py`
- `tests/fakes/__init__.py`
- `tests/fakes/polymarket_v2.py`
- `tests/conftest.py`

Tests:
- `tests/test_fake_polymarket_venue.py`
- `tests/integration/test_p0_live_money_safety.py`
- `tests/test_digest_profile_matching.py`

Routing / registries / docs:
- `architecture/topology.yaml`
- `architecture/test_topology.yaml`
- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`
- `tests/AGENTS.md`
- `src/venue/AGENTS.md`
- `docs/reference/modules/venue.md`
- `workspace_map.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/boot/T1_codex_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/T1.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/receipt.json`

Summary:

- Added `PolymarketV2AdapterProtocol` as the shared live/paper adapter contract.
- Added test-only `FakePolymarketVenue` with deterministic in-memory order state, collateral balance checks, heartbeat/open-order/cancel failure modes, partial fills, and MATCHED→FAILED rollback simulation.
- Added T1 P0 integration tests with the phase-card acceptance names, including duplicate submit idempotency, partial fills, heartbeat miss handling, cutover wipe simulation, pUSD/token insufficiency blocks, MATCHED→FAILED rollback, and paper/live schema parity against a mock live adapter.
- Added topology profile + digest regression after initial T1 navigation misrouted to heartbeat.

Verification:

```text
python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 fake polymarket venue parity implementation
pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat: 1 passed
python3 -m py_compile tests/fakes/polymarket_v2.py tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/conftest.py src/venue/polymarket_v2_adapter.py: PASS
pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py: 15 passed
pytest -q -p no:cacheprovider tests/integration/test_p0_live_money_safety.py::test_restart_mid_cycle_preserves_orders_and_records_recovery_boundary tests/integration/test_p0_live_money_safety.py::test_paper_and_live_produce_identical_journal_event_shapes: 2 passed
pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/test_v2_adapter.py tests/test_venue_command_repo.py tests/test_digest_profile_matching.py::test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat: 88 passed
pytest -q -p no:cacheprovider tests/test_fake_polymarket_venue.py tests/integration/test_p0_live_money_safety.py tests/test_v2_adapter.py tests/test_venue_command_repo.py tests/test_exchange_reconcile.py tests/test_exit_safety.py tests/test_user_channel_ingest.py tests/test_heartbeat_supervisor.py tests/test_cutover_guard.py tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_t1_fake_venue_routes_to_t1_profile_not_heartbeat: 155 passed, 6 skipped, 18 known deprecation warnings
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase T1: GREEN=11 YELLOW=0 RED=0
```

Pre-close critic remediation:

- Critic Lagrange the 2nd BLOCKED the initial T1 implementation because `FailureMode.RESTART_MID_CYCLE` existed only as an enum value with no deterministic behavior.
- Remediation: `FakePolymarketVenue` now records a restart/recovery boundary on the next read/recovery surface, preserves in-memory venue-side orders and raw-request idempotency state across the simulated restart, and exposes `restart_events()` for assertions.
- Added `tests/integration/test_p0_live_money_safety.py::test_restart_mid_cycle_preserves_orders_and_records_recovery_boundary`.

Pre-close review:

- Initial critic Lagrange the 2nd: BLOCK on inert `FailureMode.RESTART_MID_CYCLE`.
- Remediation added restart/recovery boundary semantics and regression coverage.
- Final critic Hegel the 2nd: APPROVE.
- Verifier Hubble the 2nd: PASS.
- Artifact: `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/T1_pre_close_2026-04-27.md`.
- A1 unfrozen after post-close critic Wegener the 2nd APPROVE and verifier Jason the 2nd PASS; A2/G1 remain dependency-gated.

Known non-goals / risks:

- Fake venue is test-only and does not authorize live venue submit/cancel/redeem, production DB mutation, credentialed activation, or CLOB cutover.
- T1 proves adapter protocol/envelope/result parity and deterministic P0 scenario simulation; later G1 still owns 17/17 live readiness and staged smoke gates.
- The fake uses test-only imports and in-memory state; production paper/live mode wiring remains outside T1 unless separately authorized.

Next:

- Run required post-close third-party critic + verifier before unfreezing A1/A2/G1.

Post-close gate opened:

- Opened post-close artifact: `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/T1_post_close_2026-04-27.md`.
- A1/A2/G1 remain frozen until third-party critic APPROVE + verifier PASS are recorded and closeout reruns.

Post-close critic/verifier progress:

- Critic Wegener the 2nd: APPROVE.
- Verifier Peirce the 2nd: initial procedural FAIL after its closeout command reported `map_maintenance_companion_missing`; leader reran receipt/full T1 map-maintenance and closeout successfully (`ok=true`, `blocking_issues=[]`).
- A verifier re-run remains required before downstream unfreeze.

Post-close final result:

- Critic Wegener the 2nd: APPROVE.
- Verifier Jason the 2nd: PASS after leader reran receipt/full T1 closeout cleanly.
- A1 may enter phase; A2/G1 remain dependency-gated and no live venue/prod DB/cutover authorization is implied.
