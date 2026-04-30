# 06 — Patch Blueprint

This is not a patch. It is a likely patch topology for Codex after topology doctor confirms allowed files.

## Authority files

Likely files:

```text
AGENTS.md
architecture/invariants.yaml
architecture/negative_constraints.yaml
architecture/module_manifest.yaml
architecture/source_rationale.yaml
architecture/test_topology.yaml
docs/reference/zeus_math_spec.md
docs/reference/zeus_execution_lifecycle_reference.md
src/strategy/AGENTS.md
src/execution/AGENTS.md
src/state/AGENTS.md
```

Changes:

- Insert three-layer money path.
- Add invariants INV-33..INV-38.
- Add negative constraints NC-20..NC-25.
- Supersede old `P_market` live edge formula.
- Replace implicit maker/passive language with explicit order policy vocabulary.

## New/changed contract files

Likely files:

```text
src/contracts/market_prior_distribution.py
src/contracts/executable_cost_basis.py
src/contracts/executable_trade_hypothesis.py
src/contracts/final_execution_intent.py
src/contracts/order_policy.py
src/contracts/execution_price.py
src/contracts/executable_market_snapshot_v2.py
```

Changes:

- `MarketPriorDistribution` with lineage/validation.
- `ExecutableCostBasis` with snapshot/cost/order policy.
- `ExecutableTradeHypothesis` with full FDR identity.
- `FinalExecutionIntent` immutable final fields.
- `ExecutionPrice` gains origin/cost-basis lineage or is wrapped by cost basis.

## Epistemic files

Likely files:

```text
src/strategy/market_fusion.py
src/strategy/market_analysis.py
src/engine/evaluator.py
src/signal/**
src/calibration/**
```

Changes:

- `compute_posterior(p_cal, market_prior: MarketPriorDistribution | None, mode)`.
- No raw quote/VWMP float as market prior.
- `model_only_v1` corrected baseline.
- `legacy_vwmp_prior_v0` explicit and non-promotion-grade.

## Microstructure files

Likely files:

```text
src/microstructure/clob_sweep.py
src/microstructure/cost_basis_builder.py
src/state/snapshot_repo.py
src/data/market_scanner.py
src/venue/polymarket_v2_adapter.py
```

Changes:

- Production snapshot producer or identified canonical owner.
- CLOB sweep over bids/asks.
- Fee/tick/min-order/freshness/neg-risk validation.
- No legacy-compatible envelope for certified path.

## Execution/risk files

Likely files:

```text
src/strategy/selection_family.py
src/strategy/market_analysis_family_scan.py
src/strategy/kelly.py
src/engine/cycle_runtime.py
src/execution/executor.py
src/execution/exit_triggers.py
src/execution/exit_lifecycle.py
src/engine/monitor_refresh.py
src/riskguard/**
src/risk_allocator/**
```

Changes:

- FDR after cost basis.
- Hypothesis id includes token/snapshot/cost/order policy.
- Runtime no late reprice.
- Executor no recompute.
- Monitor/exit held-token quote basis.

## Persistence/reporting files

Likely files:

```text
src/state/db.py
src/state/venue_command_repo.py
src/state/probability_trace_repo.py
src/reporting/**
scripts/live_readiness_check.py
scripts/verify_truth_surfaces.py
```

Changes:

- Additive fields only.
- Semantics version per trade/position/probability trace.
- Mixed cohort reports hard-fail or segregate.
- Live readiness gates check corrected semantics.

## Test files

Likely files:

```text
tests/test_architecture_contracts.py
tests/test_cross_module_invariants.py
tests/contracts/test_market_prior_distribution.py
tests/contracts/test_executable_cost_basis.py
tests/microstructure/test_clob_sweep.py
tests/strategy/test_posterior_split.py
tests/strategy/test_executable_hypothesis_fdr.py
tests/engine/test_no_late_reprice_corrected.py
tests/execution/test_executor_no_recompute.py
tests/execution/test_exit_quote_basis.py
tests/reporting/test_pricing_semantics_cohorts.py
tests/state/test_pricing_semantics_persistence.py
```

## Semgrep/static rules

Likely rules:

```text
zeus-no-raw-quote-to-posterior
zeus-no-bin-edge-entry-price-to-kelly
zeus-no-executor-reprice-corrected
zeus-no-legacy-envelope-certified-live
zeus-no-mixed-pricing-semantics-report
zeus-no-epistemic-polymarket-import
zeus-no-microstructure-weather-import
```
