# M2 work record — SUBMIT_UNKNOWN_SIDE_EFFECT semantics

Date: 2026-04-27
Branch: plan-pre5
Task: R3 M2 unknown-side-effect semantics — never treat possible post-submit side effects as semantic rejection

Changed files:
- architecture/AGENTS.md / architecture/topology.yaml / architecture/test_topology.yaml / architecture/module_manifest.yaml / architecture/source_rationale.yaml / workspace_map.md
- docs/reference/AGENTS.md / docs/reference/modules/AGENTS.md / docs/reference/modules/execution.md / docs/reference/modules/state.md / docs/reference/modules/venue.md
- src/execution/executor.py / src/execution/command_recovery.py
- src/data/polymarket_client.py / src/venue/polymarket_v2_adapter.py
- src/state/venue_command_repo.py
- tests/test_unknown_side_effect.py / tests/test_v2_adapter.py / tests/test_executor_command_split.py / tests/test_live_execution.py / tests/test_digest_profile_matching.py
- docs/operations/current_state.md / docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml
- docs/operations/task_2026-04-26_ultimate_plan/r3/boot/M2_codex_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/M2_work_record_2026-04-27.md

Summary:
Implemented the M2 runtime semantics for submit uncertainty. Entry and exit executor paths now map exceptions raised from `place_limit_order` after command persistence and submit boundary crossing to `OrderResult.status="unknown_side_effect"` with command state `SUBMIT_UNKNOWN_SIDE_EFFECT`, appending `SUBMIT_TIMEOUT_UNKNOWN` instead of `SUBMIT_UNKNOWN` or `SUBMIT_REJECTED`. Typed venue rejection responses (`success=False`, missing order ID, returned `None`) remain semantic rejection paths. Pre-post/pre-signing failure stays safe-to-retry through existing preflight/snapshot/cutover/heartbeat/collateral gates.

Duplicate defense:
- Exact idempotency-key retry now returns `unknown_side_effect` for an existing `SUBMIT_UNKNOWN_SIDE_EFFECT` command and does not call the venue again.
- A new economic-intent lookup in `venue_command_repo` blocks same `(intent_kind, token_id, side, price, size)` replacement attempts while a prior unknown-side-effect command is unresolved, even if a different `decision_id` produces a different idempotency key.

Recovery semantics:
- `command_recovery` now actively handles `SUBMIT_UNKNOWN_SIDE_EFFECT` rows.
- If venue lookup finds an order by known `venue_order_id` or idempotency-key capability, recovery transitions to `ACKED`, `PARTIAL`, or `FILLED` depending on venue status.
- If an idempotency-key-capable lookup returns no order after the safe-replay age window, recovery appends `SUBMIT_REJECTED` with `safe_replay_permitted=true` and links the prior unknown command/idempotency key. No new `SAFE_REPLAY_PERMITTED` enum/event was added because current INV-29 law fixes the allowed command event set.
- If lookup capability is unavailable and no venue_order_id is known, recovery fails closed and leaves the row unresolved for future retry/operator handling.

Safety notes:
- No live venue submission/cutover is enabled; executor remains behind cutover, heartbeat, collateral, and executable-snapshot gates.
- No production DB/state artifact was mutated.
- No RESTING/MATCHED/MINED/CONFIRMED command states were added; order/trade finality remains U2 fact-table territory.
- M3 websocket ingest, M4 cancel/replace policy, M5 exchange reconciliation sweep, calibration retrain go-live, and TIGGE activation remain out of scope.

Verification:
- `python3 scripts/topology_doctor.py --navigation --task "R3 M2 SUBMIT_UNKNOWN_SIDE_EFFECT ..." ...` -> navigation ok, profile `r3 unknown side effect implementation`.
- `python3 -m py_compile src/venue/polymarket_v2_adapter.py src/data/polymarket_client.py src/execution/executor.py src/execution/command_recovery.py src/state/venue_command_repo.py tests/test_unknown_side_effect.py tests/test_v2_adapter.py tests/test_digest_profile_matching.py` -> ok.
- `pytest -q -p no:cacheprovider tests/test_unknown_side_effect.py tests/test_v2_adapter.py tests/test_executor_command_split.py tests/test_live_execution.py tests/test_command_recovery.py tests/test_command_bus_types.py tests/test_command_grammar_amendment.py tests/test_venue_command_repo.py tests/test_digest_profile_matching.py::test_r3_m2_unknown_side_effect_routes_to_m2_profile_not_heartbeat` -> 169 passed, 1 skipped, 1 warning (deprecation warning from compatibility wrapper).
- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase M2` -> GREEN=10 YELLOW=0 RED=0.
- `python3 scripts/topology_doctor.py closeout --json ... --receipt-path docs/operations/task_2026-04-26_ultimate_plan/receipt.json` -> PASS; nonblocking warnings only for graph freshness/downstream/context-budget.

Pre-close blocker remediation:
- Critic Fermat BLOCKED on an economic-intent duplicate bypass: price/size comparison used exact Decimal spelling while `IdempotencyKey` canonicalizes floats to 4 decimal places. Remediation: `find_unknown_command_by_economic_intent()` now canonicalizes price and size with the same 4-decimal precision; regression `test_economic_intent_duplicate_uses_idempotency_precision` covers `0.3` vs `0.1 + 0.2`.
- Critic Fermat BLOCKED on generic pre-submit/preflight failures leaving rows in `SUBMITTING`. Remediation: entry and exit executor paths plus the PolymarketClient compatibility wrapper now classify client-init/lazy-adapter/preflight exceptions before actual adapter submit as terminal `SUBMIT_REJECTED` / `OrderResult.status="rejected"`; regressions cover generic preflight exception and exit client-construction exception.
- Verifier Curie BLOCKED because the dirty workspace contains prior M1 `src/execution/command_bus.py` grammar changes. That file is pre-existing M1/INV-29 work, not part of M2; M2 did not add any command enum/event and the exact enum-count tests continue to pass.
- Critic Fermat rerun BLOCKED on lazy PolymarketClient adapter/preflight failures inside `place_limit_order()` being misclassified as unknown side effects. Remediation: `PolymarketClient.place_limit_order()` now converts `_ensure_v2_adapter()`/`adapter.preflight()` exceptions to typed rejection payloads before `submit_limit_order()`; regression `test_exit_lazy_adapter_preflight_exception_safe_to_retry` covers the path.
- Critic Fermat second rerun BLOCKED on `adapter.submit_limit_order()` pre-POST snapshot/signing seams that still raised through the compatibility wrapper. Remediation: `PolymarketV2Adapter` now returns typed `V2_PRE_SUBMIT_EXCEPTION` rejection for preflight/client/snapshot/signing/local unsupported-submit failures before SDK `post_order`, while deliberately letting `post_order`/combined post exceptions bubble as possible side effects; regressions cover adapter direct and executor exit paths.

Known nonblocking context:
- Broad stale `tests/test_discovery_idempotency.py` remains outside the M2 topology gate and currently has pre-existing fixture drift unrelated to this M2 change (`ExecutionIntent.max_slippage` raw float, missing `temperature_metric`). It was not used as M2 closeout evidence.

Next:
Run pre-close critic + verifier review before marking M2 complete; after close, run the mandated third-party critic and verifier pass before freezing M3.
