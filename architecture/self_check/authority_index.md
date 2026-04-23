# Authority Index

Role: zero-context authority read index.

Entry overlay: `architecture/self_check/zero_context_entry.md` explains when to
use this order and how to refuse unsafe starts. The index below is the direct
authority spine for high-risk zero-context work.

## Canonical Order

1. `AGENTS.md`
2. `workspace_map.md`
3. `architecture/kernel_manifest.yaml`
4. `architecture/invariants.yaml`
5. `architecture/zones.yaml`
6. `architecture/negative_constraints.yaml`
7. `docs/authority/zeus_current_architecture.md`
8. `docs/authority/zeus_current_delivery.md`
9. `docs/authority/zeus_change_control_constitution.md` when deep governance applies

## Rule

Reference docs and reports explain domain, rationale, and history. They do not
override the authority order above unless a packet explicitly promotes a rule
into machine-checkable law.

## Current Topology Tool

Use `python scripts/topology_doctor.py digest --task "<task>" --files <paths>` for bounded task context. Use `python scripts/topology_doctor.py --docs` for Packet 3 docs-mesh checks.
