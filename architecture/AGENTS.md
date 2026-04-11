File: architecture/AGENTS.md
Disposition: NEW
Authority basis: docs/authority/zeus_current_architecture.md; docs/authority/zeus_current_delivery.md; docs/authority/zeus_change_control_constitution.md; architecture/kernel_manifest.yaml; architecture/invariants.yaml; architecture/zones.yaml; architecture/negative_constraints.yaml; architecture/maturity_model.yaml.
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

## File registry

| File | Purpose |
|------|---------|
| `kernel_manifest.yaml` | Kernel file ownership and protection rules |
| `invariants.yaml` | 10 invariant definitions (INV-01 through INV-10) |
| `zones.yaml` | Zone definitions with import rules (K0-K4) |
| `negative_constraints.yaml` | 10 negative constraint definitions |
| `maturity_model.yaml` | Maturity model definitions |
| `lifecycle_grammar.md` | Lifecycle grammar specification |
| `2026_04_02_architecture_kernel.sql` | Canonical event/projection schema — position_events, position_current, strategy_health, risk_actions, control_overrides, fact tables |
| `self_check/zero_context_entry.md` | Zero-context agent entry checklist |
| `ast_rules/semgrep_zeus.yml` | Semgrep rules for code enforcement |
| `ast_rules/forbidden_patterns.md` | Forbidden code patterns |
| `packet_templates/*.md` | Work packet templates (bugfix, feature, refactor, schema) |

## Review rule
At least one independent verifier must read the final diff before acceptance.
