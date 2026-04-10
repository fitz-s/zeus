File: src/riskguard/AGENTS.md
Disposition: NEW
Authority basis: docs/architecture/zeus_durable_architecture_spec.md; architecture/invariants.yaml; architecture/negative_constraints.yaml; current risk contracts.
Supersedes / harmonizes: portfolio-level-only protection assumptions.
Why this file exists now: protective logic is a separate spine and must not drift into theater.
Current-phase or long-lived: Long-lived.

# src/riskguard AGENTS

This directory owns the protective spine.

## Rules
- risk must change behavior, not just record warnings
- protection must remain strategy-aware when strategy information exists
- control and risk surfaces may tighten or pause; they may not silently rewrite truth

## Do not
- invent new governance keys
- hide portfolio-level heuristics as if they were strategy policy
- couple protective logic back into experimental math layers
