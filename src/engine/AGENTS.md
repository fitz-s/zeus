File: src/engine/AGENTS.md
Disposition: NEW
Authority basis: docs/architecture/zeus_durable_architecture_spec.md; architecture/invariants.yaml; architecture/negative_constraints.yaml; architecture/zones.yaml.
Supersedes / harmonizes: orchestration-local lifecycle shortcuts.
Why this file exists now: orchestration code is where lifecycle and truth drift most often re-enter.
Current-phase or long-lived: Long-lived.

# src/engine AGENTS

Engine/orchestration code may coordinate work.
It may not redefine truth.

## Must preserve
- exit is not local close
- settlement is not exit
- no direct lifecycle terminalization from orchestration
- no ad hoc phase reassignment
- no silent write-path bypass around canonical truth evolution

## Do not
- let monitor/executor code act as the lifecycle law
- depend on deprecated portfolio authority as if it were final-state canonical
- patch around missing kernel work by inventing new local state
