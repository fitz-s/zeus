# Referenced Files

Status: required file reference map for starting the refactor.

This file is the durable answer to "what files is this work referring to?"
Open these files from disk. Do not rely on chat memory.

## Package Sources

| File | Why it matters |
|---|---|
| `review_apr_30.md:15` | F-01: Kelly accepts relabeled probability/VWMP as executable cost. |
| `review_apr_30.md:39` | F-02: executor can recompute final limit from posterior/VWMP. |
| `review_apr_30.md:63` | F-03: buy-NO exit can use market/probability vector as sell proceeds. |
| `review_apr_30.md:270` | False symmetry register. |
| `review_apr_30.md:573` | Finding-to-repair map confirming F-01/F-02/F-03. |
| `review_apr_30.md:1302` | Phase 0 live freeze / semantic guardrails. |
| `evidence/source_package/zeus_pricing_semantics_cutover_package/02_three_layer_architecture.md` | Physical isolation architecture for epistemic, microstructure, execution/risk layers. |
| `evidence/source_package/zeus_pricing_semantics_cutover_package/03_hidden_branch_register.md` | Completeness contract for hidden branches and deferred decisions. |
| `evidence/source_package/zeus_pricing_semantics_cutover_package/04_multiphase_execution_plan.md:5` | Phase 0 authority admission and scope lock. |
| `evidence/source_package/zeus_pricing_semantics_cutover_package/04_multiphase_execution_plan.md:31` | Phase A safety freeze and invariant tests first. |
| `evidence/source_package/zeus_pricing_semantics_cutover_package/04_multiphase_execution_plan.md:60` | Phase B contracts and physical import fences. |
| `evidence/source_package/zeus_pricing_semantics_cutover_package/05_codex_execution_strategy.md:79` | Packet 1: authority plus failing tests. |
| `evidence/source_package/zeus_pricing_semantics_cutover_package/07_verification_matrix.md:1` | Verification matrix for layer isolation, posterior, microstructure, edge/Kelly, FDR, executor, monitor/exit, reporting. |

## Root Authority and Operations Routing

| File | Why it matters |
|---|---|
| `AGENTS.md:7` | Money path mental model. |
| `AGENTS.md:11` | Probability chain. |
| `AGENTS.md:15` | Topology navigation requirement. |
| `AGENTS.md:71` | Risk levels and advisory-only risk prohibition. |
| `AGENTS.md:87` | Position lifecycle state law. |
| `AGENTS.md:100` | Chain reconciliation hierarchy. |
| `AGENTS.md:128` | Durable trading rules. |
| `AGENTS.md:196` | Topology digest is not optional. |
| `AGENTS.md:337` | Planning lock triggers. |
| `docs/authority/zeus_current_architecture.md:281` | Risk levels must change behavior. |
| `docs/operations/AGENTS.md` | Operations package registry and non-authorizations. |
| `architecture/docs_registry.yaml` | Docs mesh classification for package discoverability. |

## Core Source Surfaces

| File | Symbol | Why it matters |
|---|---|---|
| `src/strategy/market_fusion.py:74` | `MarketPriorDistribution` | Named market-prior contract; raw quote vectors must not be corrected prior authority. |
| `src/strategy/market_fusion.py:262` | `compute_posterior` | Posterior boundary for model-only, legacy VWMP, and named prior modes. |
| `src/strategy/market_analysis.py:124` | `MarketAnalysis` | Edge scan currently bridges posterior, market prices, and buy-YES/buy-NO candidate economics. |
| `src/engine/evaluator.py:500` | `_size_at_execution_price_boundary` | Current evaluator-to-Kelly boundary; must stop accepting relabeled quote/probability as cost. |
| `src/contracts/execution_price.py:24` | `ExecutionPrice` | Existing typed price contract; insufficient alone if origin is wrong. |
| `src/strategy/kelly.py:31` | `kelly_size` | Kelly sizing boundary. |
| `src/contracts/execution_intent.py:442` | `ExecutableCostBasis` | Corrected cost-basis contract. |
| `src/contracts/execution_intent.py:877` | `FinalExecutionIntent` | Corrected immutable submit intent. |
| `src/execution/executor.py:679` | `create_execution_intent` | Legacy executor path still computes limit from posterior/VWMP context. |
| `src/engine/cycle_runtime.py:195` | `_reprice_decision_from_executable_snapshot` | Runtime late-reprice seam that must be invalidated or replaced in corrected mode. |
| `src/engine/monitor_refresh.py:902` | `refresh_position` | Monitor quote/probability coupling seam. |
| `src/execution/exit_triggers.py:182` | `_evaluate_buy_no_exit` | Buy-NO exit sell-proceeds seam. |

## Tests and Checks To Keep Close

| File/command | Why it matters |
|---|---|
| `tests/test_market_analysis.py` | Posterior/prior and market-analysis semantics. |
| `tests/test_executable_market_snapshot_v2.py` | Snapshot/cost-basis/final-intent semantics. |
| `tests/test_execution_intent_typed_slippage.py` | Slippage and intent typing. |
| `tests/test_executor.py` | Executor limit and submit behavior. |
| `tests/test_runtime_guards.py` | Runtime reprice, monitoring, risk, and guard behavior. |
| `tests/test_architecture_contracts.py` | Cross-module architecture contracts; currently has relevant discovery harness failures in this dirty tree. |
| `python3 scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <plan>` | Required for governed/cross-zone files. |

## Current Baseline Evidence From Preparation

- `tests/test_market_analysis.py tests/test_executable_market_snapshot_v2.py tests/test_execution_intent_typed_slippage.py`: 97 passed.
- `tests/test_executor.py tests/test_lifecycle.py tests/test_runtime_guards.py`: 203 passed, 1 skipped.
- `tests/test_no_bare_float_seams.py tests/test_architecture_contracts.py`: 2 failures in architecture-contract discovery harness expectations.
- `python3 -m py_compile` on core source surfaces passed.
