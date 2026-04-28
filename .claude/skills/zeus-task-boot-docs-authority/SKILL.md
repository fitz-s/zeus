---
name: zeus-task-boot-docs-authority
description: Boot profile for docs/authority/packet/registry/current_state/guidance tasks. Auto-loads when changing guidance, packets, docs registry, or authority routing in Zeus. Preserves authority/context/history layering. Replaces the docs_authority entry from architecture/task_boot_profiles.yaml (Tier 2 Phase 1 #12).
model: inherit
---

# Zeus task boot — docs_authority

Source: round2_verdict.md §1.1 #8 (task_boot_profiles → 7 SKILLs). Replaces `docs_authority` profile in architecture/task_boot_profiles.yaml.

Trigger keywords: docs, authority, packet, registry, current_state, guidance.

## Required reads (in order)

1. AGENTS.md (root)
2. workspace_map.md
3. docs/operations/current_state.md
4. docs/operations/AGENTS.md
5. architecture/docs_registry.yaml
6. architecture/topology.yaml
7. architecture/map_maintenance.yaml
8. architecture/fatal_misreads.yaml

## Current-fact surfaces

- docs/operations/current_state.md

## Required proofs (answer BEFORE editing)

1. **authority_context_history_layering**: Which surfaces are authority, derived context, current fact, or history?
   - Evidence: AGENTS.md, workspace_map.md, architecture/docs_registry.yaml
2. **active_packet_receipt**: Is the active packet tracked and receipt-backed?
   - Evidence: docs/operations/current_state.md, docs/operations/AGENTS.md

## Fatal misreads (load these antibodies)

- code_review_graph_answers_where_not_what_settles

## Forbidden shortcuts

- Do NOT promote archives, generated reports, or .omx scratch into default authority by reference.
- Do NOT leave new docs or architecture files unregistered.
- Do NOT leave current_state pointing at closed work.

## Code Review Graph use

- Stage: not_required (docs work is authority-classification work, not impact analysis)
- NOT for: authority classification, current packet truth

## Verification gates

```
python3 scripts/topology_doctor.py --docs --json
python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode precommit --json
```
