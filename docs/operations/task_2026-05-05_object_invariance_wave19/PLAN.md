# Object Meaning Invariance Wave 19

## Boundary

Selected boundary: `ExecutableCostBasis` / `ExecutableTradeHypothesis` / `FinalExecutionIntent` -> legacy `ExecutionIntent` -> venue command / SDK entry submit.

This boundary can directly affect live entry token selection, limit price, submitted shares, order type, snapshot lineage, cost basis lineage, and the FDR hypothesis id that downstream state/report/replay/learning surfaces use.

## Candidate Boundary Selection

| Candidate | Live-money risk | Values crossing | Downstream consumers | Bypass / stale path | Repair scope |
|---|---|---|---|---|---|
| FinalExecutionIntent -> legacy submit | Direct live entry submit risk | selected token, direction, final limit, submitted shares, order type, snapshot hash, cost basis hash, hypothesis id | executor, venue command journal, SDK envelope, fills, portfolio materialization, reports/replay | legacy `ExecutionIntent` envelope can drop corrected identity; final no-recompute marker was only a marker | Safely scoped to admitted contracts/executor/tests, without state repo edit |
| monitor held quote -> exit trigger | Direct live exit risk | current_p_market, p_market_no, posterior, best bid | monitor, exit_triggers, exit_lifecycle | known high-risk buy_no history but active negative constraints/tests exist | Defer unless critic finds current bypass |
| fill/event -> portfolio economics | Direct monitoring/report risk | submitted price, fill avg, cost basis | fill_tracker, portfolio, reports | mostly repaired in earlier waves | Defer |

## Topology Compatibility Notes

- `pricing semantics authority cutover` admitted `src/contracts/execution_intent.py`, `src/execution/executor.py`, and focused tests, but rejected `src/state/venue_command_repo.py` even though the profile's own invariant depends on the single venue-command insertion gate. This is a real route compatibility gap; this wave will avoid editing `venue_command_repo.py`.
- `semantic-bootstrap --task-class execution` failed with `semantic_bootstrap_unknown_task_class`. `--task-boot-profiles` succeeds, but there is no first-class execution/pricing semantic boot class for this boundary.
- The docs packet registration route rejected the new missing Wave19 packet path as unclassified. The packet is still used because the operations router requires a work record for non-trivial repo changes.
- `omx explore` failed because no Rust/cargo or prebuilt `OMX_EXPLORE_BIN` was available. Normal read-only `rg`/file reads were used instead.

## Material Value Lineage

| Value | Real object denoted | Origin | Authority / evidence class | Unit / side / time | Transform | Persistence / consumers | Status |
|---|---|---|---|---|---|---|---|
| `ExecutableMarketSnapshotV2.snapshot_id/hash` | One executable CLOB market snapshot | `src/contracts/executable_market_snapshot_v2.py` | CLOB executable market evidence | token book, tick/min order/fee, captured/freshness window | hash over market microstructure identity | snapshot repo, cost basis, final intent, venue envelope | Must be preserved |
| `ExecutableCostBasis.cost_basis_hash` | Fee/tick/depth-aware executable cost basis | `ExecutableCostBasis.from_snapshot*` | executable economics evidence | native selected-token price, requested size, fee rate, depth proof | canonical hash over cost payload | hypothesis, final intent, submit proof | Must be preserved |
| `ExecutableTradeHypothesis.fdr_hypothesis_id` | Executable economic hypothesis identity | `ExecutableTradeHypothesis.from_cost_basis` | FDR/economic hypothesis evidence | event/bin/direction/token/payoff/snapshot/cost basis | canonical hash over hypothesis payload | final intent, decision id, reporting/replay | Broken if direction can drift from cost basis |
| `FinalExecutionIntent` fields | Immutable submit-ready corrected order object | `FinalExecutionIntent.from_hypothesis_and_cost_basis` | live-submit authority when complete | final limit, submitted shares, order type, snapshot/cost hashes | validation only, no posterior/VWMP recompute | `execute_final_intent`, legacy wrapper | Ambiguous if hidden recompute attrs exist |
| legacy `ExecutionIntent` fields | Legacy executor envelope | `_legacy_entry_intent_from_final` | compatibility transport to live executor | BUY entry only, token/limit/shares timeout | copies corrected values into legacy shape | `_live_order`, risk/collateral, command/event payload | Broken if corrected identity is dropped |
| `venue_commands` / `venue_command_events` | Durable pre-side-effect command journal | `_live_order` -> `insert_command` / `append_event` | canonical venue-command persistence | token, side, price, size, snapshot id, event time | persists command and SUBMIT_REQUESTED | recovery, fills, reports | Ambiguous without final identity payload |

## Findings

- W19-F1 (S0/S1): `FinalExecutionIntent.assert_no_recompute_inputs()` is an empty marker. A dynamic forbidden field such as `p_posterior`, `p_market`, `vwmp`, `edge`, `BinEdge`, or `entry_price` can be attached after construction and still pass `execute_final_intent()`. Corrected submit authority can therefore carry legacy recompute inputs across the final boundary while the system treats it as immutable corrected economics.
- W19-F2 (S1, potentially S0 with hand-crafted objects): `ExecutableTradeHypothesis.assert_matches_cost_basis()` does not compare `direction`. A directly constructed hypothesis can carry a `buy_no` hypothesis id tied to a `buy_yes` cost-basis hash/selected token, then `FinalExecutionIntent.from_hypothesis_and_cost_basis()` emits a buy-yes order under a buy-no hypothesis id.
- W19-F3 (S0/S1): `_legacy_entry_intent_from_final()` drops snapshot hash, cost-basis id/hash, and pricing semantics version when converting to legacy `ExecutionIntent`. `_live_order()` can then persist/submit using only snapshot id/tick/min-order/neg-risk on its active DB connection. If the submit connection resolves the same snapshot id to a different executable snapshot hash, final cost-basis evidence and submit evidence diverge without a fail-closed check in the admitted executor boundary.

## Repair Plan

- Make `FinalExecutionIntent.assert_no_recompute_inputs()` enforce forbidden dynamic recompute fields and call it from submit readiness.
- Add a direction comparison in `ExecutableTradeHypothesis.assert_matches_cost_basis()`.
- Extend legacy `ExecutionIntent` with optional corrected identity fields and copy them from `FinalExecutionIntent`.
- Add an executor-side corrected identity component that rejects snapshot-hash/cost-basis drift before command persistence or SDK contact.
- Add relationship tests for dynamic recompute-field rejection, hypothesis/cost-basis direction mismatch, and cross-connection snapshot-hash drift.

## Repair Implemented

- `src/contracts/execution_intent.py`
  - Added enforced forbidden recompute-input detection for `FinalExecutionIntent`, including alias/prefix forms such as `p_market_vector` and `edge_context_json`.
  - Added hypothesis-direction vs cost-basis-direction comparison.
  - Added optional corrected identity fields to legacy `ExecutionIntent` so the compatibility envelope can carry snapshot/cost-basis lineage.
- `src/execution/executor.py`
  - `_legacy_entry_intent_from_final()` now copies snapshot hash, cost-basis id/hash, and pricing semantics version.
  - `_live_order()` now evaluates `corrected_execution_identity` before command persistence / SDK contact and rejects snapshot-hash or cost-basis identity drift.
  - Post-critic REVISE: `_live_order()` now also validates existing idempotency and economic-unknown command rows against the current corrected identity before returning them as the same order object.
  - Post-second-critic REVISE: `_live_order()` now also validates the `sqlite3.IntegrityError` idempotency-race fallback before returning an existing command row as the same order object.
- `tests/test_executable_market_snapshot_v2.py`
  - Added relationship tests for hypothesis/cost-basis direction mismatch and dynamic recompute-input rejection.
- `tests/test_executor.py`
  - Added cross-connection snapshot-hash drift test proving no command row or SDK client is created.
- `tests/test_no_bare_float_seams.py`
  - Strengthened the negative-constraint test so the no-recompute method must be executable, not only a field-shape check.

## Downstream Contamination Sweep

- Monitor/exit paths: grep confirmed no `FinalExecutionIntent` submit path is used by monitor/exit; monitor quote/probability split remains a separate boundary with existing tests.
- Settlement paths: no settlement/harvester consumer reads `FinalExecutionIntent`; settlement remains downstream of fill/position economics, not final intent.
- Replay/report/learning paths: existing consumers read corrected `pricing_semantics_version` and `entry_cost_basis_hash` from position/economics projections; this wave adds submit-time identity preservation without promoting old rows.
- Legacy/compatibility path: `_legacy_entry_intent_from_final()` is the active compatibility path and is now guarded. The state insertion seam is still not patched because topology did not admit `src/state/venue_command_repo.py`; see P29.
- Retry/recovery path: existing idempotency, economic-unknown fast paths, and the idempotency-race `IntegrityError` fallback now require the persisted `SUBMIT_REQUESTED.execution_capability.corrected_execution_identity` component to match the current final intent identity exactly. Missing or mismatched prior identity fails closed.

## Verification Plan

- `python3 -m py_compile src/contracts/execution_intent.py src/execution/executor.py tests/test_executor.py tests/test_executable_market_snapshot_v2.py tests/test_no_bare_float_seams.py`
- `pytest -q -p no:cacheprovider tests/test_executable_market_snapshot_v2.py -k 'final_execution_intent or executable_hypothesis'`
- `pytest -q -p no:cacheprovider tests/test_executor.py -k 'execute_final_intent'`
- `pytest -q -p no:cacheprovider tests/test_no_bare_float_seams.py -k 'final_execution_intent'`
- `python3 scripts/topology_doctor.py --planning-lock --changed-files <changed-files> --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave19/PLAN.md`
- Critic review before any next wave.

## Verification Results

- `python3 -m py_compile ...` passed.
- `pytest -q -p no:cacheprovider tests/test_executable_market_snapshot_v2.py -k 'final_execution_intent or executable_hypothesis'` passed: 10 passed, 56 deselected.
- `pytest -q -p no:cacheprovider tests/test_executor.py -k 'execute_final_intent'` passed: 20 passed, 12 deselected.
- `pytest -q -p no:cacheprovider tests/test_no_bare_float_seams.py -k 'final_execution_intent'` passed: 1 passed, 32 deselected.
- Full `tests/test_executable_market_snapshot_v2.py` passed: 66 passed.
- Full `tests/test_executor.py` passed: 31 passed, 1 skipped.
- Full `tests/test_no_bare_float_seams.py` passed: 33 passed.
- Post-self-review alias hardening passed:
  - `pytest -q -p no:cacheprovider tests/test_executable_market_snapshot_v2.py -k 'dynamic_recompute_inputs or executable_hypothesis_direction'`: 2 passed, 64 deselected.
  - `pytest -q -p no:cacheprovider tests/test_no_bare_float_seams.py -k 'final_execution_intent'`: 1 passed, 32 deselected.
- Compatibility tests passed:
  - `tests/test_execution_intent_typed_slippage.py`: 8 passed.
  - `tests/test_v2_adapter.py -k 'execution_intent or place_limit_order or envelope'`: 10 passed, 19 deselected.
  - `tests/test_risk_allocator.py -k 'ExecutionIntent or intent or allocation'`: 2 passed, 23 deselected.
  - `tests/test_cutover_guard.py tests/test_unknown_side_effect.py -k 'intent or live_order or execute'`: 4 passed, 26 deselected.
- Post-critic REVISE fix passed:
  - `pytest -q -p no:cacheprovider tests/test_executor.py -k 'existing_idempotent_command_with_old_corrected_identity or economic_unknown_with_old_corrected_identity or submit_connection_snapshot_hash_drift'`: 3 passed, 31 deselected.
  - `pytest -q -p no:cacheprovider tests/test_executor.py -k 'execute_final_intent'`: 22 passed, 12 deselected.
  - Full `tests/test_executor.py`: 33 passed, 1 skipped.
  - `pytest -q -p no:cacheprovider tests/test_unknown_side_effect.py -k 'economic_intent or unknown or execute'`: 14 passed.
  - `pytest -q -p no:cacheprovider tests/test_command_recovery.py -k 'command'`: 18 passed.
- Post-second-critic REVISE race fallback fix passed:
  - `python3 -m py_compile src/execution/executor.py tests/test_executor.py` passed.
  - `pytest -q -p no:cacheprovider tests/test_executor.py -k 'idempotency_race_with_old_corrected_identity or existing_idempotent_command_with_old_corrected_identity or economic_unknown_with_old_corrected_identity'`: 3 passed, 32 deselected.
  - `pytest -q -p no:cacheprovider tests/test_executor.py -k 'execute_final_intent'`: 23 passed, 12 deselected.
  - Full `tests/test_executor.py`: 34 passed, 1 skipped.
  - Full `tests/test_executable_market_snapshot_v2.py`: 66 passed.
  - Full `tests/test_no_bare_float_seams.py`: 33 passed.
  - `pytest -q -p no:cacheprovider tests/test_unknown_side_effect.py -k 'economic_intent or unknown or execute'`: 14 passed.
  - `pytest -q -p no:cacheprovider tests/test_command_recovery.py -k 'command'`: 18 passed.
  - `python3 scripts/topology_doctor.py --schema`: passed.
  - `python3 scripts/topology_doctor.py --task-boot-profiles`: passed.
  - `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave19/PLAN.md`: passed.
  - `python3 scripts/topology_doctor.py --naming --changed-files ...`: passed.
  - `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...`: passed.
  - `git diff --check -- <Wave19 changed files>`: passed.
- Topology checks passed:
  - `--planning-lock` with this plan evidence.
  - `--schema`.
  - `--naming`.
  - `--map-maintenance`.
  - `--task-boot-profiles`.
- Blocked verification: `pytest -q -p no:cacheprovider tests/test_runtime_guards.py -k 'final_execution_intent or corrected_pricing or pricing_semantics'` failed at collection because `sklearn` is not installed in the active Python environment (`ModuleNotFoundError: No module named 'sklearn'`). No Wave19 code executed in that run.

## Critic Loop

- Initial critic verdict: REVISE.
- Critic finding: existing-command idempotency and economic-unknown fast paths could return an old command with the same economic shape before proving its corrected snapshot/cost-basis/pricing identity matched the current final intent.
- Repair: executor-side existing-command identity comparison using the persisted `SUBMIT_REQUESTED` execution capability component. Missing or mismatched identity rejects before SDK contact and before treating the old command as the same economic object.
- Second critic verdict: REVISE.
- Critic finding: the normal fast paths were repaired, but the `sqlite3.IntegrityError` race fallback still returned an existing command row without the corrected identity comparison.
- Repair: the race fallback now uses the same `_corrected_existing_command_mismatch_reason()` check and rejects mismatched prior command identity before returning `_orderresult_from_existing()`.
- Third critic verdict: APPROVE.
- Critic finding: none blocking. It confirmed normal idempotency lookup, economic-unknown lookup, and `sqlite3.IntegrityError` race fallback all compare corrected identity before treating an existing row as the same order object; it also confirmed P29/P30 are real topology/workflow friction and do not hide an additional Wave19 code repair.
