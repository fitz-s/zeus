# docs/runbooks AGENTS

Operational runbooks for live operations. Runbooks are procedural guidance; they do not outrank `docs/authority/**`, `architecture/**`, or executable contracts.

## File Registry

| File | Purpose |
|------|---------|
| `live-operation.md` | Day-to-day live daemon operation procedures |
| `live-phase-1-first-boot.md` | First live daemon boot checklist |
| `task_2026-04-15_data_math_operator_runbook.md` | Packet-scoped operator runbook for the 2026-04-15 data/math lane |
| `task_2026-04-19_ai_workflow_bridge.md` | Zeus-specific mapping for AI handoff starter-kit usage |
| `task_2026-04-19_tigge_cloud_download_zeus_wiring.md` | Operational context for cloud TIGGE download handoff and Zeus v2 data wiring |

## Rules

- Keep authority references inside `docs/authority/**`.
- Mark phase-specific assumptions clearly.
- Do not reintroduce paper mode as a peer execution context.
