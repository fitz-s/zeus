# docs AGENTS

Documentation root for the tracked docs mesh.

This directory is a router, not a co-equal authority plane to source code or
machine manifests. Use it to find the right live docs surface quickly.

## Design principle

Keep the tracked docs surface thin and truthful:

- active tracked docs live in declared subroots
- visible historical protocol lives in `archive_registry.md`
- raw archive bodies stay outside the default read path

## Navigation

Read `README.md` here for the tracked docs index.

For historical needs, read `archive_registry.md` before opening any archive
body or bundle.

## File registry

| Item | Purpose |
|------|---------|
| `README.md` | Tracked docs index and visibility guide |
| `archive_registry.md` | Visible historical interface; archive access and promotion guardrails |
| `operations/known_gaps.md` | Active operational gap register |
| `zeus-architecture-deep-map.md` | Generated deep refactor/reference map; evidence only |
| `settlement-source-provenance.md` | Settlement provenance evidence; not authority by itself |
| `settlement-validation-workflow.md` | Settlement validation notes; procedural evidence |
| `zeus-pathology-registry.md` | Refactor pathology registry; evidence/routing surface |
| `zeus-refactor-plan.md` | Incremental refactor plan; planning evidence |
| `zeus-system-constitution.md` | Refactor constitution artifact subordinate to active authority |
| `authority/` | Current architecture and delivery law -> `authority/AGENTS.md` |
| `reference/` | Domain, math, architecture, market/settlement, data/replay, and failure-mode references -> `reference/AGENTS.md` |
| `operations/` | Live control pointer, active packets, and package inputs -> `operations/AGENTS.md` |
| `runbooks/` | Operator runbooks -> `runbooks/AGENTS.md` |
| `reports/` | Generated diagnostic reports; evidence only -> `reports/AGENTS.md` |
| `to-do-list/` | Active checklist workbooks; not authority -> `to-do-list/AGENTS.md` |
| `artifacts/` | Active evidence artifacts and inventories; not authority -> `artifacts/AGENTS.md` |

## Rules

- New active docs belong in declared tracked subroots, not directly under
  `docs/`, except for approved root files such as `README.md`,
  `archive_registry.md`; active gaps belong under `operations/`.
- Historical needs route through `archive_registry.md`, not archive-subtree
  routers or raw archive bodies.
- Generated reports are evidence only and must not become authority by
  placement.
