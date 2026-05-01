# Start Here

Status: entrypoint for the reality-semantics refactor package.

This package is the durable starting surface for the pricing/reality semantics
refactor. It exists so the work does not depend on chat memory after
compaction.

## Read First

1. `README.md`
2. `PACKAGE_INTEGRITY.md`
3. `ENGINEERING_ETHIC.md`
4. `WORKFLOW.md`
5. `REFERENCED_FILES.md`
6. `review_apr_30.md`
7. `evidence/source_package/zeus_pricing_semantics_cutover_package/04_multiphase_execution_plan.md`
8. `evidence/source_package/zeus_pricing_semantics_cutover_package/07_verification_matrix.md`

## Current Decision

The work is not a generic refactor. It is a semantic safety repair for a live
quantitative trading engine. The first implementation packet should be
authority/guardrails plus behavior-lock tests, not broad runtime rewiring.

## Startup Commands

Run these before any source edit:

```bash
python3 scripts/topology_doctor.py --navigation \
  --task "pricing semantics authority cutover: physically isolate epistemic probability, microstructure CLOB facts, and execution/risk economics" \
  --files AGENTS.md architecture/invariants.yaml architecture/negative_constraints.yaml docs/reference/zeus_math_spec.md src/strategy/market_fusion.py src/engine/evaluator.py src/engine/cycle_runtime.py src/execution/executor.py src/engine/monitor_refresh.py

python3 scripts/topology_doctor.py --task-boot-profiles
python3 scripts/topology_doctor.py --fatal-misreads --json
```

If topology returns `scope_expansion_required` or `ambiguous`, do not widen the
code diff casually. Freeze a narrower packet and rerun topology with the exact
files.

## Non-Authorization

This package does not authorize live deploy, live venue submission, production
DB mutation, config flips, schema migration apply, source-routing changes, or
strategy promotion.

## Immediate Known Baseline

Preparation found that corrected-semantics contracts already exist in the dirty
worktree, including `MarketPriorDistribution`, `ExecutableCostBasis`,
`ExecutableTradeHypothesis`, and `FinalExecutionIntent`. Legacy runtime seams
remain in executor limit computation, late executable repricing, monitor
quote/probability coupling, exit quote handling, and mixed evidence cohorts.

Preserve unrelated dirty work. Do not revert files just because they are
already modified.
