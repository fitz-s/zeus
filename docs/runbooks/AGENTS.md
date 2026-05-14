# docs/runbooks AGENTS

Operator and workflow runbooks for Zeus.

Runbooks are procedural guidance. They do not outrank `docs/authority/**`,
`architecture/**`, tests, or executable contracts. Dated local-machine or
cloud-run snapshots belong in `docs/artifacts/**`, not in durable runbook
routes.

## Runbook Classes

### Durable Operator Runbooks

Load these when the task is about active operations procedure.

| File | Purpose |
|------|---------|
| `forecast-live-daemon.md` | Forecast-live daemon cutover, rollback, and operator verification checklist |
| `live-operation.md` | Day-to-day live daemon operation procedures |
| `live-phase-1-first-boot.md` | First live daemon boot checklist |
| `settlement_mismatch_triage.md` | Procedure for investigating Zeus-vs-Polymarket settlement mismatches |
| `tigge_cloud_download.md` | Durable TIGGE cloud download supervision and Zeus v2 handoff guidance |

### Packet-Scoped Runbooks

Use only for the owning packet or when explicitly routed by current work.

| File | Purpose |
|------|---------|
| `task_2026-04-15_data_math_operator_runbook.md` | Packet-scoped operator runbook for the 2026-04-15 data/math lane |

### Contributor / Workflow Support

Use for agent or contributor workflow mapping, not runtime truth.

| File | Purpose |
|------|---------|
| `task_2026-04-19_ai_workflow_bridge.md` | Zeus-specific mapping for AI handoff starter-kit usage |

### Sensitive / Local Snapshots

Dated local/cloud operational snapshots are evidence-only and route through
`docs/artifacts/AGENTS.md`.

## Rules

- Keep authority references inside `docs/authority/**`.
- Mark phase-specific assumptions clearly.
- Do not put VM names, IP addresses, local absolute paths, account filenames,
  secrets, or dated progress diaries into durable runbooks.
- Preserve local operational evidence as artifacts when it may still matter to
  an active packet.
- Do not reintroduce non-live execution as a peer execution context.
