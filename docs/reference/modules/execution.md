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
- `src/execution/executor.py`, `exit_triggers.py`, `exit_lifecycle.py`, `fill_tracker.py`, `collateral.py`, `harvester.py`
- `src/contracts/execution_price.py` and state lifecycle/ledger surfaces
- `architecture/invariants.yaml` on exit-vs-settlement and risk actuation

### Non-authority surfaces
- Outcome-looking report text that is not canonical lifecycle truth
- Archive exit analyses unless translated into tests or law
- Observability summaries that do not prove actual chain/CLOB state

## 7. Public interfaces
- `executor.py` live actuation path
- `exit_triggers.py` threshold/gating logic
- `exit_lifecycle.py` transition management for exits
- `fill_tracker.py` and `collateral.py` helper APIs
- `harvester.py` for post-trade/settlement collection flows

## 8. Internal seams
- Executor vs exit_lifecycle responsibilities
- Exit trigger semantics vs monitoring/source semantics
- Collateral sizing/availability vs actual order placement
- Harvester vs canonical settlement/result persistence

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `executor.py` | Primary live-money actuation entrypoint. |
| `exit_triggers.py` | Where monitoring and execution semantics meet; extremely failure-prone. |
| `exit_lifecycle.py` | Keeps exit intent and economic closure distinct. |
| `fill_tracker.py` | Tracks fill/partial-fill state for downstream lifecycle truth. |
| `collateral.py` | Controls what capital is actually deployable. |
| `harvester.py` | Collects external result/harvest information without redefining settlement law. |

## 10. Relevant tests
- tests/test_executor.py
- tests/test_execution_price.py
- tests/test_exit_authority.py
- tests/test_entry_exit_symmetry.py
- tests/test_day0_exit_gate.py
- tests/test_divergence_exit_counterfactual.py

## 11. Invariants
- Exit intent is not closure; economic close is not settlement.
- Execution must obey risk/control actuation; advisory-only risk is theater.
- Monitoring triggers must be proven against the correct Day0 truth surface.
- Fill tracking must not be mistaken for final outcome truth.

## 12. Negative constraints
- Do not infer settlement success from local order/fill state.
- Do not wire a convenient observation source into exits without source/date proof.
- Do not bypass state/ledger append-first discipline.
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
- `exit_triggers.py` thresholds/source bindings
- `exit_lifecycle.py` phase transitions
- `harvester.py` settlement-result handling

## 20. Verification commands
```bash
pytest -q tests/test_executor.py tests/test_execution_price.py tests/test_exit_authority.py
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
