File: tests/AGENTS.md
Disposition: NEW
Authority basis: architecture/invariants.yaml; architecture/negative_constraints.yaml; docs/architecture/zeus_durable_architecture_spec.md; docs/governance/zeus_change_control_constitution.md.
Supersedes / harmonizes: ad hoc test intent.
Why this file exists now: architecture tests must preserve law, not convenience.
Current-phase or long-lived: Long-lived.

# tests AGENTS

Tests here defend kernel law and delivery guarantees.

## Do
- map tests to invariants or constraints
- keep architecture tests small and legible
- note when a test is transitional or advisory
- prefer one failure meaning per test

## Do not
- encode historical doc claims as active law
- xfail high-sensitivity architecture tests without a written sunset
- treat missing runtime convergence as if the target state already exists
