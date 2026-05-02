# docs AGENTS

Documentation root for the tracked docs mesh.

Module book: `docs/reference/modules/docs_system.md`

## System understanding first

Before navigating the docs mesh, understand the trading system:
- **What Zeus does and how it trades**: `reference/zeus_domain_model.md`
- **Binding laws of the trading machine**: `authority/zeus_current_architecture.md`

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
| `to-do-list/known_gaps.md` | Active known-gap worklist; operational task inventory, not authority |
| `to-do-list/known_gaps_archive.md` | Closed known-gap antibody archive |
| `authority/` | Current architecture and delivery law -> `authority/AGENTS.md` |
| `reference/` | Canonical domain, math, architecture, market/settlement, data/replay, failure-mode, and module references -> `reference/AGENTS.md` |
| `reference/modules/ingest.md` | Dense ingest module book for R3 user-channel event-stream routing; reference, not authority |
| `operations/` | Live control pointer, current facts, active packets, and package inputs -> `operations/AGENTS.md` |
| `operations/task_2026-04-26_polymarket_clob_v2_migration/polymarket_live_money_contract.md` | Packet-local R3 Z0 CLOB V2 live-money invariant summary; evidence, not authority |
| `runbooks/` | Operator runbooks -> `runbooks/AGENTS.md` |
| `reports/` | Generated diagnostic reports; evidence only -> `reports/AGENTS.md` |
| `to-do-list/` | Active checklist workbooks; not authority -> `to-do-list/AGENTS.md` |
| `artifacts/` | Active evidence artifacts and inventories; not authority -> `artifacts/AGENTS.md` |
| `artifacts/tigge_data_training_handoff_2026-04-23.md` | Dated TIGGE asset/training handoff for completed raw, extraction, validation, and next Zeus training steps |

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
