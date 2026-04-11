File: scripts/AGENTS.md
Disposition: NEW
Authority basis: architecture/zones.yaml; architecture/negative_constraints.yaml; docs/authority/zeus_autonomous_delivery_constitution.md; current repo scripts.
Supersedes / harmonizes: informal script scope.
Why this file exists now: enforcement and audit scripts can quietly overreach or encode stale assumptions.
Current-phase or long-lived: Long-lived.

# scripts AGENTS

Scripts here are enforcement, audit, runtime support, or operator tools.

## Do
- keep script purpose narrow
- distinguish blocking gates from advisory checks
- make external-workspace assumptions explicit
- fail loudly on stale authority where appropriate

## Do not
- let scripts become hidden authority centers
- write directly to canonical truth except explicitly approved migration/support tooling
- block CI on external files that are outside repo control unless policy says so
