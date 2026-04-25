# Docs Index

All docs use `lower_snake_case.md` naming unless a date prefix is required.

## Design principle

The docs surface is a tracked active mesh, not "active subdirectories plus
archives."

- active tracked docs live in declared subroots
- visible history is routed through `archive_registry.md`
- raw archive bodies are historical cold storage outside the default read path

## Tracked docs subroots

| Directory | Purpose | Notes |
|-----------|---------|-------|
| `authority/` | Durable architecture and delivery law only | No packet docs, ADRs, or historical governance evidence |
| `reference/` | Canonical durable references only | Concepts, system references, and routing into dense module books |
| `reference/modules/` | Dense module books | Module cognition layer; reference only, not law |
| `operations/` | Live control pointer, current fact surfaces, active packets, package inputs, gap register | Current work routing |
| `runbooks/` | Operator runbooks | Runtime support |
| `reports/` | Generated diagnostic reports | Evidence only |
| `to-do-list/` | Active checklist workbooks and audit queues | Never authority |
| `artifacts/` | Active evidence artifacts and inventories | Never authority |

Historical governance files demoted from authority live under
`reports/authority_history/` as evidence only.

## Active top-level docs

- `../AGENTS.md` - root operating brief
- `archive_registry.md` - visible historical interface and promotion guardrails
- `authority/zeus_current_architecture.md` - current architecture law
- `authority/zeus_current_delivery.md` - current delivery law
- `reference/zeus_domain_model.md` - short domain model
- `reference/modules/AGENTS.md` - router for dense module books
- `reference/zeus_architecture_reference.md` - canonical architecture reference anchor
- `reference/zeus_market_settlement_reference.md` - canonical market/settlement reference anchor
- `reference/zeus_data_and_replay_reference.md` - canonical data/replay reference anchor
- `reference/zeus_failure_modes_reference.md` - canonical failure modes reference anchor
- `operations/current_state.md` - live control pointer
- `operations/current_data_state.md` - current audited data posture
- `operations/current_source_validity.md` - current audited source-validity posture
- `runbooks/live_operation.md` - day-to-day live daemon runbook
- `operations/known_gaps.md` - active operational gap register
- `operations/task_2026-04-25_p0_market_events_preflight/` - closed POST_AUDIT 4.2.C implementation packet
- `operations/task_2026-04-25_p1_daily_observation_writer_provenance/` - closed POST_AUDIT 4.3.B-lite daily observation writer provenance packet
- `operations/task_2026-04-25_p1_obs_v2_provenance_identity/` - closed POST_AUDIT 4.3.B obs_v2 writer/producer provenance identity packet
- `operations/task_2026-04-25_p2_backfill_completeness_guardrails/` - closed POST_AUDIT 4.4.B-lite backfill completeness guardrail packet
- `operations/task_2026-04-25_p2_obs_v2_revision_history/` - active POST_AUDIT 4.4.A1 obs_v2 revision-history packet
- `operations/task_2026-04-25_p3_settlement_metric_linter_closeout/` - closed POST_AUDIT 4.5.A settlement metric-read linter closeout packet
- `operations/task_2026-04-25_p3_usage_path_residual_guards/` - closed P3 residual usage-path guard packet
- `artifacts/tigge_data_training_handoff_2026-04-23.md` - dated TIGGE asset + Zeus training handoff snapshot
- `../workspace_map.md` - repo visibility and routing guide

## Historical interface

Raw historical bodies are not part of the default tracked boot surface.

Use `archive_registry.md` first. Only open archive bodies or bundles when the
task explicitly needs historical evidence, and label archive-derived claims as
`[Archive evidence]`.

## Naming rules

- All `.md` files use `lower_snake_case.md`
- Exceptions: `AGENTS.md`, `README.md`
- New time-bound packet files use `task_YYYY-MM-DD_name.md` or
  `task_YYYY-MM-DD_name/`
- Avoid generic top-level names such as `plan.md` or `progress.md` outside an
  active task folder
