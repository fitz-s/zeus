File: architecture/AGENTS.md
Disposition: NEW
Authority basis: docs/architecture/zeus_durable_architecture_spec.md; docs/governance/zeus_change_control_constitution.md; architecture/kernel_manifest.yaml; architecture/invariants.yaml; architecture/zones.yaml; architecture/negative_constraints.yaml; architecture/maturity_model.yaml.
Supersedes / harmonizes: informal architecture claims in historical docs.
Why this file exists now: this is the K0/K1 law zone and needs narrower instructions than the repo root.
Current-phase or long-lived: Long-lived.

# architecture AGENTS

This directory contains machine-checkable and constitutional authority surfaces.

## Treat this zone as high sensitivity
Changes here are architecture or governance changes, never “just docs.”

## Required before edit
- approved packet
- explicit invariant references
- list of touched authority surfaces
- statement of what existing surface this change harmonizes or supersedes

## Do
- keep manifests, constitution, and spec mutually consistent
- prefer delta over rewrite
- preserve descriptive vs normative distinction
- record migration drift instead of hiding it

## Do not
- create parallel authority files
- copy historical rationale into active law without saying so
- claim runtime convergence unless current code actually matches
- widen semantics by convenience

## Review rule
At least one independent verifier must read the final diff before acceptance.
