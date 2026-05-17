File: .github/workflows/AGENTS.md
Disposition: NEW
Authority basis: docs/authority/zeus_change_control_constitution.md; architecture/maturity_model.yaml; architecture/negative_constraints.yaml; scripts/check_*.py; tests/test_architecture_contracts.py.
Supersedes / harmonizes: implicit CI policy.
Why this file exists now: CI is a law-enforcement surface and must not drift into either theater or excessive friction.
Current-phase or long-lived: Long-lived.

# .github/workflows AGENTS

Workflow files control gate severity and maintenance cost.

## Rules
- blocking only when the signal is reliable and worth ongoing cost
- advisory first for staged checks such as replay parity
- every new gate needs owner, rationale, and sunset/review condition

## Do not
- hard-block on missing external workspace artifacts
- add noisy hooks or redundant gates without net-protection value

## File Registry

| File | Purpose |
|------|---------|
| `architecture_advisory_gates.yml` | Architecture/topology CI lanes: blocking stable gates plus advisory staged checks |
| `pr_identity_collision_check.yml` | Advisory check posting PR comment when identity-bearing class added under src/types/ or src/contracts/ |
| `pr-policy.yml` | Universal cross-agent PR LOC/commit accumulation advisory comment (CI equivalent of pre-push hook) |
| `replay-correctness.yml` | Required merge gate (Gate 4); replay-correctness DB-fixture validation |
| `secrets-scan.yml` | Required cross-agent secrets-scan gate covering Codex, Copilot, human, and raw pushes |
