# Phase Plans Index

Companion executable plans for the topology-system reform route.
Read `../MAIN_ROUTE_IMPLEMENTATION_PLAN.md` first for the route shape,
gates, and invariants. Then read the per-phase plan for the phase you
are about to execute.

| Phase | File | Repair blueprint | Codex prompt |
|------:|------|------------------|--------------|
| P0 | `phase_p0_plan.md` | `../repair_blueprints/p0_scope_and_lane_repair.md` | `../prompts/codex_p0_execute_topology_lane_repair.md` |
| P1 | `phase_p1_plan.md` | `../repair_blueprints/p1_issue_model_repair.md` | `../prompts/codex_p1_execute_issue_model.md` |
| P2 | `phase_p2_plan.md` | `../repair_blueprints/p2_module_book_rehydration.md` | `../prompts/codex_p2_expand_topology_books.md` |
| P3 | `phase_p3_plan.md` | `../repair_blueprints/p3_manifest_ownership_normalization.md` | `../prompts/codex_p3_normalize_manifest_ownership.md` |
| P4 | `phase_p4_plan.md` | `../repair_blueprints/p4_context_pack_and_graph_extraction.md` | `../prompts/codex_p4_graph_and_context_pack.md` |
| P5 | `phase_p5_plan.md` | `../repair_blueprints/p5_topology_doctor_test_resegmentation.md` | (none — pre-P5 review only) |

## Layering note

These three files per phase are intentionally distinct:

- **Repair blueprint** (`../repair_blueprints/p<N>_*.md`):
  *what* changes, allowed/forbidden files, expected output, rollback.
- **Codex prompt** (`../prompts/codex_p<N>_*.md`):
  the prompt to hand to a coding agent for that phase.
- **Phase plan** (`phase_p<N>_plan.md`, this folder):
  *the ordered atomic checklist* with concrete repo anchor points
  (file:line), pre-decisions resolving relevant Open Questions,
  per-phase risks, and a definition of done. Built from
  direct repo inspection on `data-improve` branch.

If they ever disagree, the route invariants in
`../MAIN_ROUTE_IMPLEMENTATION_PLAN.md` §1 win.
