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
| `operations/known_gaps.md` | Active operational gap register |
| `authority/` | Current architecture and delivery law -> `authority/AGENTS.md` |
| `reference/` | Canonical domain, math, architecture, market/settlement, data/replay, failure-mode, and module references -> `reference/AGENTS.md` |
| `operations/` | Live control pointer, current facts, active packets, and package inputs -> `operations/AGENTS.md` |
| `operations/task_2026-04-25_p0_market_events_preflight/` | Closed POST_AUDIT 4.2.C implementation packet; route through `operations/current_state.md` |
| `operations/task_2026-04-25_p1_daily_observation_writer_provenance/` | Closed POST_AUDIT 4.3.B-lite daily observation writer provenance packet; route through `operations/current_state.md` |
| `operations/task_2026-04-25_p1_obs_v2_provenance_identity/` | Closed POST_AUDIT 4.3.B obs_v2 writer/producer provenance identity packet; route through `operations/current_state.md` |
| `operations/task_2026-04-25_p2_backfill_completeness_guardrails/` | Closed POST_AUDIT 4.4.B-lite backfill completeness guardrail packet; route through `operations/current_state.md` |
| `operations/task_2026-04-25_p2_obs_v2_revision_history/` | Closed POST_AUDIT 4.4.A1 obs_v2 revision-history packet; route through `operations/current_state.md` |
| `operations/task_2026-04-25_p2_daily_observation_revision_history/` | Closed POST_AUDIT 4.4.A2 daily observation backfill revision-history packet; route through `operations/current_state.md` |
| `operations/task_2026-04-25_p3_obs_v2_reader_gate/` | Active POST_AUDIT 4.5.B-lite obs_v2 reader-gate consumer-hardening packet; route through `operations/current_state.md` |
| `operations/task_2026-04-25_p3_settlement_metric_linter_closeout/` | Closed POST_AUDIT 4.5.A settlement metric-read linter closeout packet; route through `operations/current_state.md` |
| `operations/task_2026-04-25_p3_usage_path_residual_guards/` | Closed P3 residual usage-path guard packet; route through `operations/current_state.md` |
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
