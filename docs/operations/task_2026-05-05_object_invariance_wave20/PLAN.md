# Object Meaning Invariance Wave 20

## Boundary

Selected boundary: held-position exit intent / executable exit snapshot context -> `ExitOrderIntent` -> `execute_exit_order()` -> venue command/event/envelope -> cancel/replace and retry recovery.

This boundary can directly affect live exit order token selection, sell price, submitted shares, replacement blocking, venue command identity, and whether an exit retry/recovery path treats an old command as the same live-money object.

## Candidate Boundary Selection

| Candidate | Live-money risk | Values crossing | Downstream consumers | Bypass / stale path | Repair scope |
|---|---|---|---|---|---|
| exit snapshot context -> execute_exit_order submit | Direct live exit submit risk | token id, snapshot id/hash, tick/min order, neg-risk, limit, shares, decision id | executor, venue commands/events, pre-submit envelope, cancel/replace safety, fill tracking | active exit lifecycle carries snapshot id without hash; retry paths can return old exit command by idem/economic shape | Safely scoped to M4-admitted executor/exit_lifecycle/test_exit_safety |
| monitor refresh -> exit trigger probability native space | Direct false-exit risk | p_posterior, p_market, forward_edge, direction, Day0 evidence | exit_triggers, monitor, exit_lifecycle | broad route blocks relationship tests; Day0 semantic boot exists but navigation lacks parity | Defer unless Wave20 critic finds current bypass |
| cancel outcome -> replacement sell gate | Direct duplicate exit sell risk | cancel status, command state, venue order id, mutex holder | exit_safety, executor, venue command repo | existing tests cover CANCEL_UNKNOWN and mutex release | Defer after identity path because existing M4 route/test coverage is stronger |

## Topology Compatibility Notes

- Broad position-state -> monitor/exit navigation selected `generic` and marked all candidate files out of scope. This is a real object-audit route mismatch; the route needs typed semantic intent before it can admit a slice.
- `semantic-bootstrap --task-class lifecycle` failed with `semantic_bootstrap_unknown_task_class`. `day0_monitoring` semantic bootstrap exists and answered the monitor/source proof questions, but it is not a navigation profile.
- `omx explore` remains unavailable because Rust/cargo or a prebuilt `OMX_EXPLORE_BIN` is missing.
- `r3 cancel replace exit safety implementation` admitted a safe M4 slice for executor/exit_lifecycle/test_exit_safety. Including `architecture/improvement_backlog.yaml` in that route was rejected, so compatibility notes are recorded here instead of widening the M4 repair.

## Material Value Lineage

| Value | Real object denoted | Origin | Authority / evidence class | Unit / side / time | Transform | Persistence / consumers | Status |
|---|---|---|---|---|---|---|---|
| `Position.trade_id/token_id/no_token_id/direction/shares` | Held outcome-token exposure to reduce | `src/state/portfolio.py` read model consumed by exit lifecycle | portfolio read model derived from canonical state/chain/fill truth | CTF shares, held side, monitor/exit time | `build_exit_intent()` chooses held token and effective shares | `ExitIntent`, `ExitOrderIntent`, sell command | Must be preserved |
| exit snapshot context | Current executable market facts for the held token | `_latest_exit_snapshot_context()` / `_latest_or_capture_exit_snapshot_context()` | CLOB executable market evidence | tick/min order/neg-risk, captured/freshness interval, selected token | context dict passed into `create_exit_order_intent()` | executor pre-submit envelope and command row | Broken if hash is dropped |
| `ExitOrderIntent` fields | Submit-ready sell order compatibility object | `create_exit_order_intent()` | live-exit transport authority when complete | SELL side, limit price, shares, token, snapshot facts | price clamped/quantized in `execute_exit_order()` | `venue_commands`, `venue_command_events`, SDK submit | Ambiguous without snapshot hash |
| existing exit command row | Prior persisted exit submit/retry object | `venue_commands` / `venue_command_events` | canonical command/event persistence | position/token/price/size/side, command state | returned by idempotency/economic-unknown/race paths | recovery, cancel/replace, fills | Broken if old identity is treated as current |

## Findings

- W20-F1 (S0): exit lifecycle cited executable snapshot identity by `snapshot_id` only. If the submit connection resolves the same id to different executable market facts, `execute_exit_order()` can submit against a different market object than the one selected during exit decision/capture.
- W20-F2 (S0/S1): active exit snapshot capture persisted a fresh snapshot but returned only id/tick/min-order/neg-risk. The hash was computable and authoritative, but it was not carried into the exit order object, leaving the corrected exit path indistinguishable from legacy id-only callers.
- W20-F3 (S0/S1): `execute_exit_order()` idempotency, economic-unknown, and `sqlite3.IntegrityError` race fallback paths can return an existing exit command as the same order object without comparing corrected exit snapshot identity.
- W20-F4 (S1): partial exit fills can reduce open local exposure and append nested fill economics while `execution_fact.fill_price` remains null. The partial fill observation changes position economics but downstream report/replay readers see a position-size change without the fill price/share evidence that caused it.

## Repair Plan

- Add optional `executable_snapshot_hash` to `ExitOrderIntent` and `create_exit_order_intent()`.
- Make exit lifecycle latest/capture context include the computed executable snapshot hash.
- Add executor-side `exit_snapshot_identity` capability validation before exit command persistence or SDK contact when a hash is present.
- Persist `exit_snapshot_identity` in the exit `SUBMIT_REQUESTED.execution_capability`.
- Compare existing idempotent/economic-unknown/race fallback commands against persisted `exit_snapshot_identity` before returning them as the same exit object.
- Add relationship tests in `tests/test_exit_safety.py` for snapshot-hash context propagation, submit-connection drift rejection, and existing-command identity mismatch on retry paths.
- Preserve partial exit fill price/shares in `execution_fact` when partial fill observation mutates open exposure.

## Repair Implemented

- `src/execution/executor.py`
  - Added optional `ExitOrderIntent.executable_snapshot_hash` and `create_exit_order_intent(..., executable_snapshot_hash=...)`.
  - Added `exit_snapshot_identity` validation before exit command persistence / SDK contact when a hash is present.
  - Persisted `exit_snapshot_identity` in `SUBMIT_REQUESTED.execution_capability`.
  - Added identity comparison for idempotency, economic-unknown, and `sqlite3.IntegrityError` existing-command return paths.
- `src/execution/exit_lifecycle.py`
  - `_latest_exit_snapshot_context()` and `_latest_or_capture_exit_snapshot_context()` now return the executable snapshot hash with the snapshot id.
  - Partial exit fill observation now writes fill price and filled shares to `execution_fact` after reducing open exposure.
- `tests/test_exit_safety.py`
  - Added relationship tests for submit-connection hash drift, idempotency/economic-unknown/race existing-command identity mismatch, and exit lifecycle hash propagation.
  - Reused the existing partial-fill execution-fact test as a regression for W20-F4.

## Verification Plan

- `python3 -m py_compile src/execution/executor.py src/execution/exit_lifecycle.py tests/test_exit_safety.py`
- `pytest -q -p no:cacheprovider tests/test_exit_safety.py`
- focused Wave20 relationship tests in `tests/test_exit_safety.py`
- M4 topology/planning-lock/map-maintenance checks with this plan evidence.
- Critic review before any next wave.

## Verification Results

- `python3 -m py_compile src/execution/executor.py src/execution/exit_lifecycle.py tests/test_exit_safety.py`: passed.
- Focused Wave20 tests: `pytest -q -p no:cacheprovider tests/test_exit_safety.py -k 'snapshot_hash_drift or old_exit_snapshot_identity or idempotency_race_with_old_exit_snapshot_identity or partial_fill_reduces_open_position_exposure'`: 5 passed, 38 deselected.
- Full `tests/test_exit_safety.py`: 43 passed.
- Full `tests/test_executor.py`: 34 passed, 1 skipped.
- `pytest -q -p no:cacheprovider tests/test_executor_command_split.py -k 'exit or sell or snapshot'`: 8 passed, 25 deselected.
- `pytest -q -p no:cacheprovider tests/test_unknown_side_effect.py -k 'exit or unknown or economic_intent'`: 14 passed.
- `pytest -q -p no:cacheprovider tests/test_final_sdk_envelope_persistence.py -k 'exit or sell or envelope'`: 3 passed.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_r3_m4_cancel_replace_routes_to_m4_profile_not_heartbeat`: 1 passed.
- `python3 scripts/topology_doctor.py --schema`: passed.
- `python3 scripts/topology_doctor.py --task-boot-profiles`: passed.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave20/PLAN.md`: passed.
- `python3 scripts/topology_doctor.py --naming --changed-files ...`: passed.
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...`: passed.
- `git diff --check -- <Wave20 changed files>`: passed.

## Downstream Contamination Sweep

- Cancel/replace paths: replacement sell guard still runs before new submit and now receives identity-preserved existing-command behavior on idempotency/economic-unknown/race paths.
- Fill tracking/report/replay paths: partial exit fill now carries fill price and filled shares into `execution_fact` instead of only nested position state.
- Legacy compatibility: exit callers without `executable_snapshot_hash` still flow as legacy exit intents; active lifecycle latest/capture path now provides the corrected hash.

## Critic Loop

- Initial critic verdict: REVISE.
- Critic finding: when a status payload is both a void/retry status (`CANCELLED`/`CANCELED`/`EXPIRED`/`REJECTED`) and contains partial-fill evidence (`matched_size`/`remaining_size`/`avgPrice`), `check_pending_exits()` reduced open exposure and wrote partial fill economics, then retry telemetry cleared the same `execution_fact` fill fields.
- Repair: partial-fill execution facts are now written through a helper and re-applied after void/retry telemetry for partial-cancel outcomes, preserving fill price and filled shares while leaving the command/order status as cancelled/retry-owned.
- Post-critic verification:
  - `python3 -m py_compile src/execution/exit_lifecycle.py tests/test_exit_safety.py`: passed.
  - `pytest -q -p no:cacheprovider tests/test_exit_safety.py::test_exit_lifecycle_cancel_after_partial_only_retries_remaining_exposure`: 1 passed.
  - `pytest -q -p no:cacheprovider tests/test_exit_safety.py -k 'partial_fill_reduces_open_position_exposure or cancel_after_partial_only_retries_remaining_exposure'`: 2 passed, 41 deselected.
  - Full `tests/test_exit_safety.py`: 43 passed.
  - Full `tests/test_executor.py`: 34 passed, 1 skipped.
  - `pytest -q -p no:cacheprovider tests/test_unknown_side_effect.py -k 'exit or unknown or economic_intent'`: 14 passed.
  - `pytest -q -p no:cacheprovider tests/test_executor_command_split.py -k 'exit or sell or snapshot'`: 8 passed, 25 deselected.
  - `pytest -q -p no:cacheprovider tests/test_final_sdk_envelope_persistence.py -k 'exit or sell or envelope'`: 3 passed.
  - `python3 scripts/topology_doctor.py --schema`: passed.
  - `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave20/PLAN.md`: passed.
  - `python3 scripts/topology_doctor.py --naming --changed-files ...`: passed.
  - `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...`: passed.
  - `git diff --check -- <Wave20 changed files>`: passed.
- Critic re-review verdict: APPROVE.
