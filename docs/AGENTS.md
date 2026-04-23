# docs AGENTS

Documentation root for the tracked docs mesh.

This directory is a router, not a co-equal authority plane to source code or
machine manifests. Use it to find the right live docs surface quickly.

## Design principle

Keep the tracked docs surface thin and truthful:

- active tracked docs live in declared subroots
- `docs/reference/` is canonical-only; stale support docs must move to reports
  or operations current-fact surfaces; dense module books live under
  `docs/reference/modules/` and remain reference, not authority
- `docs/authority/` is durable law only; packet docs, ADRs, and historical
  governance evidence must not remain there
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
| `authority/` | Current architecture and delivery law -> `authority/AGENTS.md` |
| `reference/` | Canonical domain, math, architecture, market/settlement, data/replay, failure-mode, and module references -> `reference/AGENTS.md` |
| `operations/` | Live control pointer, current facts, active packets, and package inputs -> `operations/AGENTS.md` |
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
- Do not put current facts, dated audits, or stale support material in
  `docs/reference/`.
- Dense module books may live under `docs/reference/modules/`, but they remain
  descriptive reference surfaces and must not become packet diaries, current
  fact sinks, or duplicate authority kernels.
- Do not put packet-scoped docs, ADRs, rollback notes, or one-off governance
  doctrine in `docs/authority/`; route them to operations evidence, reports, or
  archive interfaces.
- Generated reports are evidence only and must not become authority by
  placement.
