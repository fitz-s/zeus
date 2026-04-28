# Contracts Module Authority Book

**Recommended repo path:** `docs/reference/modules/contracts.md`
**Current code path:** `src/contracts`
**Authority status:** Dense module reference that explains executable semantic law. It is not constitutional law; root AGENTS, architecture/**, and docs/authority/** still outrank it.

## 1. Module purpose
Own the semantic objects and executable contract rules that make Zeus a discrete-settlement machine instead of a generic temperature dashboard. This package is where rounding, bin geometry, execution-price semantics, evidence objects, provenance contracts, and reality-contract checks become code.

## 2. What this module is not
- Not a persistence layer; it should not write canonical DB truth.
- Not a signal engine; it should not infer forecasts or calibrate probabilities.
- Not a place for packet-scoped doctrine or report text to ossify into law.

## 3. Domain model
- Settlement semantics: how a raw station reading becomes a contract-resolving integer.
- Calibration bins: how discrete market bins are represented and validated.
- Execution intent/evidence: typed objects that separate decision, allocation metadata, price, provenance, and actuation.
- Reality contracts: executable checks that a runtime or integration still matches the semantic promises Zeus thinks it is making.

## 4. Runtime role
Imported broadly by signal, calibration, engine, execution, and state. The role is to make category errors hard to construct: wrong rounding, wrong bin assumptions, wrong semantic labels, wrong provenance object shapes.

## 5. Authority role
This module is the code-adapter for law already declared in `docs/authority/zeus_current_architecture.md`, `docs/reference/zeus_math_spec.md`, and `architecture/invariants.yaml`. It should absorb semantic rules into typed, testable code—not invent them silently.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `docs/authority/zeus_current_architecture.md` sections on contract semantics, source truth, and dual-track identity
- `docs/reference/zeus_math_spec.md` for rounding, bins, Monte Carlo chain, and calibration math
- `src/contracts/settlement_semantics.py` and `src/contracts/calibration_bins.py` as executable semantic anchors
- `architecture/invariants.yaml` and `architecture/negative_constraints.yaml` for non-negotiable behavior

### Non-authority surfaces
- Archive packet notes describing old bugs or old math experiments
- Any report that claims a rounding or bin rule without matching executable tests
- Graph-derived edges that show callers but not semantic correctness

## 7. Public interfaces
- `SettlementSemantics` and helpers such as `round_wmo_half_up_values`
- `Bin` / calibration-bin structures and validators
- Execution-price / decision / evidence dataclasses used across engine and execution
- Reality-contract loaders and verifiers

## 8. Internal seams
- Settlement semantics vs. calibration-bin support geometry
- Decision evidence vs. execution intent vs. provenance registry
- Reality-contract definitions vs. runtime loaders/verifiers
- Tail/vig treatment helpers vs. downstream execution math

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `settlement_semantics.py` | Canonical rounding/unit/settlement semantics. Highest-risk contracts file. |
| `calibration_bins.py` | Discrete-support geometry for training and market bins. |
| `execution_price.py` | Normalizes executable price semantics for downstream execution. |
| `decision_evidence.py` | Typed evidence objects that explain why a decision exists. |
| `execution_intent.py` | Typed expression of what execution may do; critical boundary surface. R3 A2 adds `event_id`, `resolution_window`, and `correlation_key` so allocation caps are carried by production intents rather than dynamic test-only attributes. |
| `reality_contract.py / reality_contracts_loader.py / reality_verifier.py` | Executable assertions that repo/runtime reality matches intended semantics. |
| `semantic_types.py` | Semantic wrappers that reduce stringly-typed misuse. |
| `edge_context.py / epistemic_context.py / alpha_decision.py` | Decision-context objects consumed by engine/strategy. |
| `tail_treatment.py / vig_treatment.py / hold_value.py / exceptions.py` | Specialized semantic subroutines and failure channels. |
| `fx_classification.py` | Z4 enum-only gate for operator-selected pUSD/USDC.e accounting classification. |

## 10. Relevant tests
- tests/test_calibration_bins_canonical.py
- tests/test_execution_price.py
- tests/test_architecture_contracts.py
- tests/test_backtest_settlement_value_outcome.py
- tests/contracts/spec_validation_manifest.py (cross-module contract routing)
- tests/test_collateral_ledger.py
- tests/test_risk_allocator.py

## 11. Invariants
- Settlement rounding must match WMO half-up / HKO-special semantics exactly; Python banker's rounding is forbidden.
- Bin support must preserve exactly-one-bin-wins semantics; outer bins are part of contract reality, not UI sugar.
- Metadata fields must not be mistaken for settlement outcomes.
- High and low tracks cannot be collapsed into one semantic family.
- pUSD/USDC.e redemption accounting classification must be an explicit `FXClassification` enum, never a raw string.
- Allocation cap identity on `ExecutionIntent` must remain explicit and typed; do not make per-event/window/correlation caps infer from labels or test-only monkeypatches.

## 12. Negative constraints
- Never use this package to smuggle new authority claims that are absent from law/tests.
- Never let a contracts helper write canonical truth directly.
- Never encode packet dates or historical bug labels into stable public interfaces.
- Never use nearby-source availability as proof of correct settlement semantics.

## 13. Known failure modes
- Banker's rounding or wrong negative-half handling yields systematic settlement drift.
- Conflating metadata fields with actual settlement outcome columns recreates the settlement-crisis class of bug.
- Bin miscoverage or wrong shoulder interpretation makes probability mass and outcomes disagree.
- Implicit high-track defaults silently poison low-track work.

## 14. Historical failures and lessons
- [Archive evidence] `traces/settlement_crisis_trace.md` showed that semantic metadata was mistaken for lifecycle truth; contracts code must name what is metadata and what is economic outcome.
- [Archive evidence] `governance_doc_restructuring/zeus_discrete_settlement_support_amendment.md` and the archived deep-map family reinforce that discrete support and shoulder semantics are first-class, not optional commentary.
- [Archive evidence] `audits/math_audit_2026_04_06.md` warned that mathematical law scattered across prose and code drifts unless pinned by executable tests.

## 15. Code graph high-impact nodes
- `src/contracts/settlement_semantics.py` — central semantic dependency for source/settlement/calibration correctness.
- `src/contracts/calibration_bins.py` — central because calibration, market mapping, and evaluation all rely on it.
- `src/contracts/execution_price.py` and `decision_evidence.py` — likely bridge nodes from engine into execution/state.
- Exact centrality metrics require local graph queries; online blob presence is confirmed but human-readable graph stats are not.

## 16. Likely modification routes
- New source family or new city/unit behavior: change law first, then settlement_semantics, then tests.
- New contract/bin geometry: change calibration bin support and math spec together.
- New decision/evidence type: ensure engine/execution/state callers are updated in the same packet.

## 17. Planning-lock triggers
- Any edit to `settlement_semantics.py`, `calibration_bins.py`, semantic types, or reality-contract interfaces.
- Any change that touches high/low identity, bin containment, rounding, or contract-support geometry.
- Any cross-zone change from contracts into state, engine, calibration, or execution.

## 18. Common false assumptions
- `round()` is close enough to WMO rounding.
- A market label implies bin geometry without explicit parsing/validation.
- `settlement_semantics_json` or other metadata columns are equivalent to actual outcomes.
- Because this module is 'pure', changing it is low-risk.

## 19. Do-not-change-without-checking list
- `SettlementSemantics.for_city()` dispatch rules
- Any rounding helper used by settlement or Monte Carlo simulation
- Calibration-bin boundary semantics and support coverage
- Reality-contract public interfaces without matching tests and manifest updates
- `ExecutionIntent` allocation metadata consumed by `src/risk_allocator/governor.py`

## 20. Verification commands
```bash
pytest -q tests/test_calibration_bins_canonical.py tests/test_execution_price.py tests/test_architecture_contracts.py
pytest -q -p no:cacheprovider tests/test_risk_allocator.py
pytest -q tests/test_backtest_settlement_value_outcome.py
python scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <packet-plan> --json
```

## 21. Rollback strategy
Revert contracts changes as one packet. Do not leave mixed old/new rounding or bin semantics in place. If any persistence or training data was regenerated from a changed semantic rule, revert or quarantine those artifacts explicitly.

## 22. Open questions
- Should `reality_contract*` remain in contracts or move under a dedicated runtime-integrity package once module manifesting exists?
- Do all current contract-like dataclasses have explicit source-rationale entries, or are some still discoverable only by code search?

## 23. Future expansion notes
- Add explicit module-book sections for each contract family once `architecture/module_manifest.yaml` exists.
- Surface semantic-dependency graph edges in context packs so reviewers know which tests must move with a contracts edit.

## 24. Rehydration judgement
This book is the dense reference layer for contracts. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
