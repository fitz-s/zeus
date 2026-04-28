# Execution Module Authority Book

**Recommended repo path:** `docs/reference/modules/execution.md`
**Current code path:** `src/execution`
**Authority status:** Dense module reference for economic actuation, exits, collateral, fills, and harvester boundaries.

## 1. Module purpose
Turn validated execution intent into actual market actions while preserving the difference between local economic state, market state, settlement state, and lifecycle state.

## 2. What this module is not
- Not a semantic authority on settlement, source routing, or probability math.
- Not a place to infer lifecycle closure from local fills alone.
- Not a report-writer or derived dashboard.

## 3. Domain model
- Order placement and executor behavior.
- Exit triggers and exit lifecycle progression.
- Collateral and fill tracking.
- Harvester/settlement collection and economic-close boundaries.

## 4. Runtime role
This is the live-money edge. It consumes execution intent, prices, collateral policy, and risk constraints; it then talks to market/chain surfaces and updates state through the proper write path.

## 5. Authority role
Execution is downstream of many stronger truth surfaces, but its errors are immediate PnL errors. Module documentation must therefore foreground what execution must *not* infer for itself.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `docs/authority/zeus_current_architecture.md` sections on runtime truth, lifecycle grammar, and money path
- `src/execution/command_bus.py`, `executor.py`, `exchange_reconcile.py`, `settlement_commands.py`, `exit_triggers.py`, `exit_lifecycle.py`, `exit_safety.py`, `fill_tracker.py`, `collateral.py`, `wrap_unwrap_commands.py`, `harvester.py`
- `src/control/cutover_guard.py` and `src/control/heartbeat_supervisor.py` as pre-submit live-money gates consumed by executor
- `src/risk_allocator/governor.py` as the R3 A2 pre-submit capital allocation and kill-switch gate consumed by executor
- `src/execution/executor.py` must persist a U2 pre-submit `VenueSubmissionEnvelope` before SDK contact; no live order may rely on an uncited command row.
- `src/contracts/execution_price.py` and state lifecycle/ledger surfaces
- `architecture/invariants.yaml` on exit-vs-settlement and risk actuation

### Non-authority surfaces
- Outcome-looking report text that is not canonical lifecycle truth
- Archive exit analyses unless translated into tests or law
- Observability summaries that do not prove actual chain/CLOB state

## 7. Public interfaces
- `executor.py` live actuation path
- `command_bus.py` durable venue-command grammar (`CommandState`, `CommandEventType`, `IntentKind`, idempotency keys)
- `exit_triggers.py` threshold/gating logic
- `exit_lifecycle.py` transition management for exits
- `fill_tracker.py`, `collateral.py`, `wrap_unwrap_commands.py`, and `settlement_commands.py` helper APIs
- `harvester.py` for post-trade/settlement collection flows

## 8. Internal seams
- Executor vs exit_lifecycle responsibilities
- Exit trigger semantics vs monitoring/source semantics
- Collateral sizing/availability vs actual order placement
- Harvester vs canonical settlement/result persistence

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `command_bus.py` | Closed command-side grammar and deterministic idempotency contract. M1 adds command states/events while keeping order/trade facts in U2. |
| `executor.py` | Primary live-money actuation entrypoint. M2 maps exceptions after possible venue submit side effects to `OrderResult.status="unknown_side_effect"` and `SUBMIT_UNKNOWN_SIDE_EFFECT`, never semantic rejection. R3 A2 consults the global RiskAllocator before command persistence/SDK contact, persists/submits the selected maker/taker order type, and raises structured `AllocationDenied` when capacity/governor gates deny new risk. |
| `exchange_reconcile.py` | R3 M5 read-only venue-vs-journal sweep. Writes findings and linkable missing trade facts; never creates `venue_commands` for exchange-only state. |
| `settlement_commands.py` | R3 R1 durable settlement/redeem command ledger. `REDEEM_TX_HASHED` is the crash-recovery anchor; Q-FX-1 gates pUSD redemption/accounting. |
| `exit_triggers.py` | Where monitoring and execution semantics meet; extremely failure-prone. |
| `exit_lifecycle.py` | Keeps exit intent and economic closure distinct. |
| `exit_safety.py` | R3 M4 typed cancel outcomes, per-position/token exit mutex, and replacement-sell gating. |
| `fill_tracker.py` | Tracks fill/partial-fill state for downstream lifecycle truth. |
| `collateral.py` | Compatibility facade for sell collateral checks; delegates token-inventory truth to CollateralLedger. |
| `wrap_unwrap_commands.py` | Durable USDC.e↔pUSD command states; Z4 has no live chain-submission authority. |
| `harvester.py` | Collects external result/harvest information without redefining settlement law. |

## 10. Relevant tests
- tests/test_executor.py
- tests/test_execution_price.py
- tests/test_exit_authority.py
- tests/test_entry_exit_symmetry.py
- tests/test_unknown_side_effect.py
- tests/test_day0_exit_gate.py
- tests/test_divergence_exit_counterfactual.py
- tests/test_collateral_ledger.py
- tests/test_command_grammar_amendment.py
- tests/test_command_bus_types.py
- tests/test_exit_safety.py
- tests/test_exchange_reconcile.py
- tests/test_settlement_commands.py
- tests/test_risk_allocator.py

## 11. Invariants
- Exit intent is not closure; economic close is not settlement.
- Execution must obey risk/control actuation; advisory-only risk is theater.
- Resting GTC/GTD live orders must pass CutoverGuard, HeartbeatSupervisor, RiskAllocator/PortfolioGovernor, and CollateralLedger before venue-command persistence or SDK contact; missing heartbeat/collateral/allocation health is a hard pre-submit failure.
- Monitoring triggers must be proven against the correct Day0 truth surface.
- Fill tracking must not be mistaken for final outcome truth.
- `RESTING`, `MATCHED`, `MINED`, and `CONFIRMED` are not `CommandState` values; they remain U2 order/trade facts.
- Settlement redemption is durable and crash-recoverable; redeem failure/review states must not mark positions settled.

## 12. Negative constraints
- Do not infer settlement success from local order/fill state.
- Do not wire a convenient observation source into exits without source/date proof.
- Do not bypass state/ledger append-first discipline.
- Do not bypass CutoverGuard, HeartbeatSupervisor, RiskAllocator/PortfolioGovernor, or CollateralLedger for live placement convenience tests; tests that exercise executor mechanics must explicitly opt out with monkeypatches.
- Do not let harvester metadata overwrite canonical outcome semantics.

## 13. Known failure modes
- False-safe or false-breach Day0 trigger due to wrong live observation source.
- Local-close semantics collapse into settlement semantics, corrupting PnL/lifecycle truth.
- Collateral accounting and executor behavior diverge, producing ghost capacity.
- Harvester/settlement paths fail to rejoin state truth cleanly.

## 14. Historical failures and lessons
- [Archive evidence] `docs/archives/findings/exit_failure_analysis.md` is the clearest historic proof that execution and exit logic, not just signal quality, can dominate losses.
- [Archive evidence] `docs/archives/traces/settlement_crisis_trace.md` shows why execution/settlement metadata must not be mistaken for canonical outcomes.
- [Archive evidence] `docs/archives/reports/strategy_failure_analysis.md` reinforces that narrow-edge strategies are especially sensitive to exit and lifecycle mis-semantics.

## 15. Code graph high-impact nodes
- `src/execution/executor.py` — likely bridge from engine, riskguard, state, and supervisor inputs into live market actuation.
- `src/execution/exit_triggers.py` — high risk because it binds monitor truth to economic action.
- `src/execution/harvester.py` — downstream bridge into state/settlement truth.

## 16. Likely modification routes
- Order/executor behavior change: review riskguard, state, and contracts/execution_price together.
- Exit-trigger change: review engine monitor path, current source validity, and day0 tests together.
- Harvester change: prove settlement/lifecycle/state consistency.

## 17. Planning-lock triggers
- Any edit to executor, exit triggers, lifecycle, collateral, or harvester.
- Any change to exit behavior, settlement join-up, or live-money safety rules.
- Any cross-zone change among engine, state, contracts, and execution.

## 18. Common false assumptions
- If an order was sent or filled, the lifecycle is effectively done.
- Exit triggers are merely heuristics and can tolerate semantic fuzziness.
- Harvester state is a harmless report layer.
- Execution modules can 'borrow' monitoring values without source proof.

## 19. Do-not-change-without-checking list
- `executor.py` actuation flow
- `exchange_reconcile.py` findings-vs-command boundary and stale-read absence proof
- `settlement_commands.py` Q-FX-1 gate, payout-asset classification, and `REDEEM_TX_HASHED` recovery semantics
- Heartbeat/order-type submit gates before `_live_order` and `execute_exit_order`
- RiskAllocator/PortfolioGovernor pre-submit gate and structured denial reason propagation
- `exit_triggers.py` thresholds/source bindings
- `exit_lifecycle.py` phase transitions
- `exit_safety.py` cancel/replace gates and mutex release semantics
- `exchange_reconcile.py` unresolved finding idempotence and operator-resolution loop
- `harvester.py` settlement-result handling

## 20. Verification commands
```bash
pytest -q tests/test_executor.py tests/test_execution_price.py tests/test_exit_authority.py
pytest -q -p no:cacheprovider tests/test_risk_allocator.py
pytest -q tests/test_day0_exit_gate.py tests/test_entry_exit_symmetry.py tests/test_divergence_exit_counterfactual.py
python -m py_compile src/execution/*.py
```

## 21. Rollback strategy
Rollback executor/exit packets as a unit. Never leave a new trigger path active if lifecycle or state handling was reverted separately.

## 22. Open questions
- Which exact external market/chain adapters remain embedded here versus abstracted behind client layers?
- Should harvester logic be further split from core execution to reduce live-money blast radius?

## 23. Future expansion notes
- Add explicit execution-state sequence diagrams and failure matrices.
- Add graph-derived impacted-test lists to module book appendices.

## 24. Rehydration judgement
This book is the dense reference layer for execution. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
