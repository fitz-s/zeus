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
| `authority/` | Current architecture and delivery law | Present-tense authority docs |
| `reference/` | Domain, math, and repo reference material | Facts and orientation, not authority by themselves |
| `operations/` | Live control pointer, active packets, package inputs, gap register | Current work routing |
| `runbooks/` | Operator runbooks | Runtime support |
| `reports/` | Generated diagnostic reports | Evidence only |
| `to-do-list/` | Active checklist workbooks and audit queues | Never authority |
| `artifacts/` | Active evidence artifacts and inventories | Never authority |

## Active top-level docs

- `../AGENTS.md` - root operating brief
- `archive_registry.md` - visible historical interface and promotion guardrails
- `authority/zeus_current_architecture.md` - current architecture law
- `authority/zeus_current_delivery.md` - current delivery law
- `reference/zeus_domain_model.md` - short domain model
- `reference/zeus_architecture_reference.md` - canonical architecture reference anchor
- `reference/zeus_market_settlement_reference.md` - canonical market/settlement reference anchor
- `reference/settlement_source_provenance.md` - settlement source/station provenance evidence
- `reference/zeus_data_and_replay_reference.md` - canonical data/replay reference anchor
- `reference/zeus_failure_modes_reference.md` - canonical failure modes reference anchor
- `operations/current_state.md` - live control pointer
- `runbooks/live_operation.md` - day-to-day live daemon runbook
- `operations/known_gaps.md` - active operational gap register
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
