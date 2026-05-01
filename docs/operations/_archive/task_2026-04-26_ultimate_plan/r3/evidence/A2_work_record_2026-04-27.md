# R3 A2 work record — RiskAllocator + PortfolioGovernor

Date: 2026-04-27
Branch: plan-pre5
Task: R3 A2 RiskAllocator + PortfolioGovernor — caps, drawdown governor, kill switch, pre-submit executor gate, cycle summary surface
Status: POST-CLOSE PASS; A2 CLOSEOUT COMPLETE; G1 PHASE ENTRY UNFROZEN

Changed files:

Implementation / config:
- `src/risk_allocator/AGENTS.md`
- `src/risk_allocator/__init__.py`
- `src/risk_allocator/governor.py`
- `src/contracts/execution_intent.py`
- `config/risk_caps.yaml`
- `src/execution/executor.py`
- `src/data/polymarket_client.py`
- `src/engine/cycle_runner.py`
- `src/engine/cycle_runtime.py`

Tests:
- `tests/test_risk_allocator.py`
- `tests/test_digest_profile_matching.py`

Routing / docs / registries:
- `architecture/topology.yaml`
- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`
- `architecture/test_topology.yaml`
- `architecture/AGENTS.md`
- `architecture/docs_registry.yaml`
- `config/AGENTS.md`
- `docs/reference/AGENTS.md`
- `docs/reference/modules/riskguard.md`
- `docs/reference/modules/execution.md`
- `docs/reference/modules/data.md`
- `docs/reference/modules/engine.md`
- `docs/reference/modules/contracts.md`
- `workspace_map.md`
- `docs/AGENTS.md`
- `docs/README.md`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/boot/A2_codex_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/A2.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/receipt.json`

Summary:

- Added `RiskAllocator`, `PortfolioGovernor`, `CapPolicy`, `GovernorState`, `ExposureLot`, `AllocationDecision`, and `AllocationDenied` in a new `src/risk_allocator` package.
- Added read-only canonical exposure helpers for latest append-only `position_lots`, unresolved `SUBMIT_UNKNOWN_SIDE_EFFECT` commands, and unresolved `exchange_reconcile_findings`.
- Preserved NC-NEW-I by separately surfacing confirmed, optimistic, and weighted exposure; optimistic exposure uses a configurable lower capacity weight while confirmed exposure counts at full weight.
- Enforced per-market, per-event, per-resolution-window, correlated exposure, same-market unknown-side-effect, reduce-only, heartbeat-lost, WS-gap, reconciliation finding, drawdown, and manual kill-switch denials with structured reasons.
- Added `config/risk_caps.yaml` engineering defaults while preserving in-code defaults when the file is absent.
- Wired executor pre-submit allocation guard before collateral reservation / command persistence / SDK contact.
- Wired A2 maker/taker order-type selection into executor persistence, heartbeat gating, client submit, and the `PolymarketClient` V2 adapter shim.
- Wired cycle-runner governor refresh from canonical read models and `summary["portfolio_governor"]` operator visibility.
- Added A2 topology profile + digest regression after initial route matched heartbeat rather than the allocator/governor slice.

Verification:

```text
python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 risk allocator governor implementation
python3 -m py_compile src/risk_allocator/governor.py src/risk_allocator/__init__.py src/engine/cycle_runner.py src/execution/executor.py tests/test_risk_allocator.py: PASS
pytest -q -p no:cacheprovider tests/test_risk_allocator.py: 17 passed
pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 17 passed
pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 1 passed
pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_executor.py tests/test_heartbeat_supervisor.py: 33 passed, 5 skipped
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A2: GREEN=12 YELLOW=0 RED=0
python3 scripts/topology_doctor.py --map-maintenance ... --map-maintenance-mode advisory: topology check ok
python3 scripts/topology_doctor.py --planning-lock ... --plan-evidence docs/operations/task_2026-04-26_ultimate_plan/r3/ULTIMATE_PLAN_R3.md: topology check ok
```

Known non-goals / risks:

- A2 does not tune operator caps for production; `risk_caps.yaml` is an engineering default and later operator tuning remains separate.
- A2 does not authorize live venue submit/cancel/redeem, CLOB cutover, credentialed activation, live strategy promotion, or G1 deployment.
- `load_position_lots()` derives event/resolution/correlation from available command/payload metadata with safe defaults when older rows lack those optional fields.
- The process-wide allocator defaults to allow when not configured so isolated tests and utility seams remain inert; cycle startup refresh is the intended live-runtime configuration seam.

Pre-close critic remediation:

- Critic Epicurus the 2nd BLOCKED the initial A2 implementation on three live-path gaps: allocation metadata was test-only/dynamic, kill switch did not guard exit submits, and PortfolioGovernor refresh occurred after monitoring/force-exit work rather than at cycle start.
- Remediation added typed `ExecutionIntent.event_id`, `resolution_window`, and `correlation_key`; `cycle_runtime` now passes candidate event/date/cluster allocation identity through the production entry constructor.
- Entry `SUBMIT_REQUESTED` command events now persist allocation metadata so later `position_lots` reads can reconstruct event/window/correlation capacity from command truth.
- Exit submit now calls an A2 global kill-switch guard before collateral, command persistence, or SDK contact; reduce-only exits remain allowed only when no kill-switch reason is active.
- `cycle_runner.run_cycle()` refreshes the global PortfolioGovernor at cycle start before monitoring, force-exit, or discovery phases.
- Added regressions for typed production intent metadata, exit kill-switch pre-persistence denial, command-event allocation metadata reconstruction, and cycle-start refresh ordering.

Remediation verification:

```text
python3 -m py_compile src/contracts/execution_intent.py src/risk_allocator/governor.py src/risk_allocator/__init__.py src/execution/executor.py src/engine/cycle_runner.py src/engine/cycle_runtime.py tests/test_risk_allocator.py: PASS
pytest -q -p no:cacheprovider tests/test_risk_allocator.py: 20 passed
pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_executor.py tests/test_heartbeat_supervisor.py: 36 passed, 5 skipped
pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 1 passed
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A2: GREEN=12 YELLOW=0 RED=0
python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 risk allocator governor implementation
python3 scripts/topology_doctor.py closeout --changed-files ... --plan-evidence ... --work-record-path ... --receipt-path ... --summary-only: closeout ok, changed_files=33
```

Second pre-close critic remediation:

- Critic Galileo the 2nd confirmed the typed allocation metadata, exit kill-switch, and cycle-start refresh blockers were fixed, then BLOCKED A2 on maker/taker being API-only: executor still persisted/submitted `GTC`, heartbeat checks used `GTC`, and `PolymarketClient.place_limit_order()` did not accept `order_type`.
- Remediation added `select_global_order_type(snapshot)` to the allocator surface. It returns `GTC` for healthy/deep/far-from-close maker conditions, `FOK` for taker-only conditions (shallow book, near resolution, degraded/non-resting heartbeat), and raises `AllocationDenied` for true no-trade states.
- Executor entry and exit paths now select order type from the executable snapshot before command persistence, pass that type to heartbeat gating, persist it in `VenueSubmissionEnvelope`, include it in `SUBMIT_REQUESTED`/`SUBMIT_ACKED` payloads, and pass it to `client.place_limit_order()`.
- `PolymarketClient.place_limit_order()` now preserves the selected `order_type` when delegating to `PolymarketV2Adapter.submit_limit_order()`.
- Added regressions proving shallow-book entry submits/persists `FOK`, degraded-heartbeat reduce-only exit submits/persists `FOK`, PolymarketClient forwards `FOK` to the V2 adapter, and JSON CLOB book depth can select maker mode when healthy/deep.

Second remediation verification:

```text
python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 risk allocator governor implementation
python3 -m py_compile src/contracts/execution_intent.py src/risk_allocator/governor.py src/risk_allocator/__init__.py src/execution/executor.py src/data/polymarket_client.py src/engine/cycle_runner.py src/engine/cycle_runtime.py tests/test_risk_allocator.py tests/test_executor.py: PASS
pytest -q -p no:cacheprovider tests/test_risk_allocator.py: 24 passed
pytest -q -p no:cacheprovider tests/test_risk_allocator.py tests/test_executor.py tests/test_heartbeat_supervisor.py tests/test_k2_slice_e.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_executor_db_target.py: 82 passed, 6 skipped
pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_a2_risk_allocator_routes_to_a2_profile_not_heartbeat: 1 passed
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase A2: GREEN=12 YELLOW=0 RED=0
python3 scripts/topology_doctor.py --map-maintenance --changed-files ... --map-maintenance-mode closeout: topology check ok
python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-04-26_ultimate_plan/r3/ULTIMATE_PLAN_R3.md: topology check ok
python3 scripts/topology_doctor.py closeout --changed-files ... --plan-evidence ... --work-record-path ... --receipt-path ... --summary-only: closeout ok, changed_files=36
python3 scripts/topology_doctor.py closeout --changed-files ... --plan-evidence ... --work-record-path ... --receipt-path ... --summary-only after pre-close artifact/status update: closeout ok, changed_files=37
python3 scripts/topology_doctor.py closeout --changed-files ... --plan-evidence ... --work-record-path ... --receipt-path ... --summary-only after post-close artifact/status update: closeout ok, changed_files=38
```

Next:

- Pre-close critic Euclid the 2nd APPROVE and verifier Ampere the 2nd PASS are recorded in `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/A2_pre_close_2026-04-27.md`.
- Post-close third-party critic Parfit the 2nd APPROVE and verifier Godel the 2nd PASS are recorded in `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/A2_post_close_2026-04-27.md`.
- G1 is unfrozen for phase entry only; live deploy remains blocked by the G1 17/17 readiness gate and explicit operator authorization.
