# Engine Module Authority Book

**Recommended repo path:** `docs/reference/modules/engine.md`
**Current code path:** `src/engine`
**Authority status:** Dense module reference for orchestration, evaluation routing, replay, and runtime sequencing.

## 1. Module purpose
Own the runtime loop that turns loaded state, market context, signal, calibration, risk policy, and execution into a coherent cycle. The engine decides sequencing, not semantic law.

## 2. What this module is not
- Not the owner of contract semantics or settlement truth.
- Not the canonical state writer of lifecycle law by itself.
- Not a place to silently redefine riskguard or execution semantics through orchestration shortcuts.

## 3. Domain model
- Cycle runner and cycle runtime state.
- Evaluation pass over discovered markets/positions.
- Monitor refresh and day0/live observation refresh.
- Replay/time-context/discovery-mode branches that should preserve semantics.

## 4. Runtime role
This is the conductor of the live machine. It binds together data ingestion state, evaluation, riskguard, execution, observability, and state updates into one runtime pass.

## 5. Authority role
Engine code does not outrank law, but because it sequences everything it can destroy semantics by omission, ordering mistakes, or wrong cross-module assumptions. High-blast-radius by structure even when the math is unchanged.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `docs/authority/zeus_current_architecture.md` money path and feed-role matrix
- `src/engine/cycle_runner.py`, `evaluator.py`, `monitor_refresh.py`, `replay.py`
- `architecture/invariants.yaml` lifecycle and risk-actuation rules
- `docs/operations/current_state.md` only for active packet routing, never for runtime semantics

### Non-authority surfaces
- Status summaries or logs that look like runtime truth but are derived
- Archive packet narratives about engine problems unless translated into current law/tests
- Graph blast radius output without semantic boot

## 7. Public interfaces
- `CycleRunner` orchestration entry
- `evaluator.py` evaluation flow and decision synthesis
- `monitor_refresh.py` live-refresh/Day0 runtime hooks
- `replay.py` replay-compatible execution path
- `time_context.py` / `discovery_mode.py` helpers

## 8. Internal seams
- Cycle runner vs evaluator responsibilities
- Live runtime vs replay semantics
- Discovery mode and time-context branching vs economic semantics
- Monitor refresh vs execution/riskguard actuation

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `cycle_runner.py` | Top orchestration hub; one of the highest-blast-radius files in the repo. |
| `evaluator.py` | Turns signal/calibration/market context into action candidates. |
| `monitor_refresh.py` | Day0 and runtime refresh logic; source/monitor semantics are fragile here. |
| `replay.py` | Historical/replay path that must not silently diverge from live semantics. |
| `cycle_runtime.py / lifecycle_events.py` | Runtime state and event sequencing. |
| `time_context.py / discovery_mode.py / process_lock.py` | Mode/time/locking scaffolding that can still break semantics if wrong. |

## 10. Relevant tests
- tests/test_day0_exit_gate.py
- tests/test_day0_runtime_observation_context.py
- tests/test_day0_window.py
- tests/test_cross_module_invariants.py
- tests/test_cross_module_relationships.py
- tests/test_bug100_k1_k2_structural.py

## 11. Invariants
- Engine sequencing must not collapse settlement, monitoring, and execution into one truth plane.
- Exit intent is not economic closure; settlement is not exit.
- Risk and control must alter runtime behavior, not merely annotate logs.
- Replay path may differ in I/O, not in semantic law.

## 12. Negative constraints
- Do not make the engine infer settlement/source law from whichever endpoint currently answers.
- Do not let monitor-refresh data silently become settlement truth.
- Do not patch around state/risk/execution boundaries by adding orchestration shortcuts.
- Do not let replay-only assumptions leak into live runtime.

## 13. Known failure modes
- Wrong sequencing hides or delays actuation even when signal is correct.
- Day0 monitoring uses the wrong source or stale current fact and misses risk events.
- Replay diverges from live semantics and produces false confidence.
- Process-lock or mode branching causes duplicate/partial cycles.

## 14. Historical failures and lessons
- [Archive evidence] `docs/archives/findings/exit_failure_analysis.md` shows that correct signal plus wrong exit/runtime behavior still loses money.
- [Archive evidence] `docs/archives/architecture/zeus_blueprint_v2.md` argued that position/lifecycle reality, not pure signal flow, must anchor runtime design.
- [Archive evidence] `docs/archives/reports/strategy_failure_analysis.md` reinforces that orchestration and exit behavior can dominate raw predictive quality.

## 15. Code graph high-impact nodes
- `src/engine/cycle_runner.py` and `src/engine/evaluator.py` are repeatedly identified as high-blast-radius anchors.
- `src/engine/monitor_refresh.py` is a probable bridge into data, signal, execution, and observability.
- `src/engine/replay.py` likely sits on a separate but semantically coupled branch that must be reviewed with live code.

## 16. Likely modification routes
- Cycle orchestration change: review state, riskguard, execution, observability together.
- Day0/monitor path change: review current source validity, city truth contract, and data module first.
- Replay change: prove semantic parity with live path.

## 17. Planning-lock triggers
- Any change to cycle runner, evaluator, monitor refresh, replay, or cross-zone sequencing.
- Any work touching Day0, hourly, settlement, calibration, or risk semantics from engine code.
- Any edit that changes runtime phase/order of actuation.

## 18. Common false assumptions
- Because the engine is 'just orchestration', small patches are safe.
- Monitor source can be swapped freely as long as temperature values look reasonable.
- Replay and live can tolerate semantic drift if interfaces match.
- A syntax-correct runtime patch is good enough without source/truth review.

## 19. Do-not-change-without-checking list
- `cycle_runner.py` main sequencing
- `evaluator.py` decision flow assumptions
- `monitor_refresh.py` source/monitor hooks
- `replay.py` parity-critical flow without paired proof

## 20. Verification commands
```bash
pytest -q tests/test_day0_exit_gate.py tests/test_day0_runtime_observation_context.py tests/test_day0_window.py
pytest -q tests/test_cross_module_invariants.py tests/test_cross_module_relationships.py tests/test_bug100_k1_k2_structural.py
python -m py_compile src/engine/*.py
python scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <packet-plan> --json
```

## 21. Rollback strategy
Rollback engine packets atomically. If a sequencing change required accompanying state/risk/execution modifications, revert the bundle or keep the feature behind a fail-closed gate.

## 22. Open questions
- Should `monitor_refresh.py` be split into Day0-specific and general runtime-refresh modules to reduce blast radius?
- Which engine assumptions are still only described in packet docs rather than module-level reference?

## 23. Future expansion notes
- Add runtime-sequence diagrams and semantic-call-chain notes extracted from code-review-graph.
- Create explicit engine-to-riskguard and engine-to-execution contract sections in module manifest.

## 24. Rehydration judgement
This book is the dense reference layer for engine. Keep `src/engine/AGENTS.md`
as the launcher, keep `architecture/module_manifest.yaml` as the machine
registry, and do not let orchestration notes become a second authority center.
