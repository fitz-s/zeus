# R3 R1 work record — Settlement / redeem command ledger

Date: 2026-04-27
Branch: plan-pre5
Task: R3 R1 settlement / redeem command ledger — durable command states, Q-FX-1 gate, tx-hash recovery
Status: COMPLETE; pre-close and post-close reviews passed

Changed files:

Implementation:
- `src/execution/settlement_commands.py`
- `src/execution/harvester.py`
- `src/state/db.py`

Tests:
- `tests/test_settlement_commands.py`
- `tests/test_digest_profile_matching.py`

Routing / registries / docs:
- `architecture/topology.yaml`
- `architecture/source_rationale.yaml`
- `architecture/module_manifest.yaml`
- `architecture/test_topology.yaml`
- `src/execution/AGENTS.md`
- `docs/reference/modules/execution.md`
- `docs/reference/modules/state.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/boot/R1_codex_2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/frozen_interfaces/R1.md`
- `docs/operations/task_2026-04-26_ultimate_plan/r3/drift_reports/2026-04-27.md`
- `docs/operations/task_2026-04-26_ultimate_plan/receipt.json`

Summary:

- Added `settlement_commands.py` with R1 `SettlementState`, `SettlementResult`, request/submit/reconcile APIs, savepoint-based transitions, hashed event payloads, and tx-hash receipt recovery.
- Added `settlement_commands` and `settlement_command_events` schema to `src/state/db.py` without importing execution modules from schema boot.
- Wired harvester winning-position redemption to create an R1 command intent instead of direct adapter redeem calls.
- Added Q-FX-1 fail-closed behavior for pUSD request/submit; unset env prevents command creation or adapter contact.
- Classified legacy `USDC_E` payout separately as `REDEEM_REVIEW_REQUIRED`.
- Added topology profile + digest regression after initial R1 navigation misrouted to heartbeat.

Verification:

```text
python3 -m py_compile src/execution/settlement_commands.py src/execution/harvester.py src/state/db.py tests/test_settlement_commands.py tests/test_digest_profile_matching.py: PASS
pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat: 7 passed
pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_collateral_ledger.py tests/test_v2_adapter.py tests/test_exchange_reconcile.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat: 76 passed, 4 known deprecation warnings
pytest -q -p no:cacheprovider tests/test_settlement_commands.py tests/test_digest_profile_matching.py::test_r3_r1_settlement_redeem_routes_to_r1_profile_not_heartbeat tests/test_collateral_ledger.py tests/test_v2_adapter.py tests/test_exchange_reconcile.py tests/test_venue_command_repo.py tests/test_exit_safety.py tests/test_user_channel_ingest.py: 151 passed, 22 known deprecation warnings
python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase R1: GREEN=7 YELLOW=0 RED=0
python3 scripts/topology_doctor.py --navigation ...: navigation ok True, profile r3 settlement redeem command ledger implementation
```

Pre-close review:

- Critic Mencius the 2nd: APPROVE, no blocking issues.
- Verifier Hume the 2nd: PASS.
- Artifact: `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/R1_pre_close_2026-04-27.md`.
- T1 remains frozen until required post-close third-party critic + verifier pass completes.

Post-close review:

- Critic Fermat the 2nd: APPROVE.
- Verifier Zeno the 2nd: PASS.
- Artifact: `docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/R1_post_close_2026-04-27.md`.
- T1 is unfrozen for phase entry only; live venue/prod DB/CLOB cutover remain unauthorized.

Known non-goals / risks:

- Live redeem side effects were not exercised and remain outside default/test authorization.
- Q-FX-1 operator classification remains required for pUSD redemption/accounting; unset gate blocks pUSD request/submit.
- `tests/test_harvester_dr33_live_enablement.py` has a pre-existing stale expectation for `physical_quantity` (`daily_maximum_air_temperature` vs current `mx2t6_local_calendar_day_max`) and was not used as R1 closeout evidence.
- Current real adapter still returns `REDEEM_DEFERRED_TO_R1`; R1 tests use fake adapters to prove ledger semantics without SDK/chain side effects.

Next:

- Proceed to T1 phase-entry boot/navigation before any T1 edits.
- Preserve R1 live-side-effect boundaries: no live redeem, production DB mutation, or CLOB cutover authorization.
