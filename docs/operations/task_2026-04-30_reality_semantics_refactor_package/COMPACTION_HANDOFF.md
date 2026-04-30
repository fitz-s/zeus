# Compaction Handoff

This file is an index, not a substitute for the full package. After any
compaction, use it to recover the exact source material.

## Read Order

1. `README.md`
2. `PACKAGE_INTEGRITY.md`
3. `review_apr_30.md`
4. `evidence/source_package/zeus_pricing_semantics_cutover_package/02_three_layer_architecture.md`
5. `evidence/source_package/zeus_pricing_semantics_cutover_package/03_hidden_branch_register.md`
6. `evidence/source_package/zeus_pricing_semantics_cutover_package/04_multiphase_execution_plan.md`
7. `evidence/source_package/zeus_pricing_semantics_cutover_package/05_codex_execution_strategy.md`
8. `evidence/source_package/zeus_pricing_semantics_cutover_package/07_verification_matrix.md`

## Refactor Thesis

The failure class is semantic aliasing in the money path. Probability belief,
market prior, VWMP, executable ask/bid, all-in cost, submitted limit, fill
average, exit proceeds, and settlement payout must not share ambiguous scalar
fields.

For Zeus as a quantitative trading engine, the best architecture under current
evidence is not a broad cleanup. It is a physically isolated system:

- Epistemic belief computes payoff probability and does not know venue details.
- Microstructure computes executable cost/proceeds and does not know weather models.
- Execution/risk combines the two through typed, immutable trade hypotheses and final intents.

## First Implementation Lane

Do not begin with broad runtime rewiring. Start with a narrow packet:

1. Authority/guardrail admission.
2. Failing or focused invariant tests.
3. Live legacy fail-closed flags.
4. Static gates for forbidden scalar crossings.
5. Only then contracts, microstructure cost basis, FDR, executor, exits, and reporting.

## Must Not Lose

- F-01 through F-10 in `review_apr_30.md`.
- The false symmetry register and semantic aliasing table in `review_apr_30.md`.
- The phased repair plan in `review_apr_30.md` sections 6-14.
- The hidden branch register in the mirrored source package.
- The verification matrix, especially executor no-recompute, buy-NO native
  quote, monitor/exit split, and mixed-cohort report gates.

## Current Preparation Notes

Topology rejected direct one-shot edits across contracts, strategy, engine,
execution, state, and venue as ambiguous. Treat that as correct. Freeze small
phases and use planning lock before changing governed files.

The dirty worktree already contains substantial corrected-semantics work from
another lane. Preserve it. Do not revert unrelated edits. Before changing files,
inspect the current diff and use focused tests to decide whether a change
extends or conflicts with existing work.

## Separate Live-Readiness Blockers

Even if pricing semantics become correct, live remains blocked until separate
evidence proves source truth, calibration readiness, RED/ORANGE actuation,
collateral/allowance, venue command/fill facts, monitor/exit symmetry,
settlement/learning traceability, and explicit operator live-money go.
