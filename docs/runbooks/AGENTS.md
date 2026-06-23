# docs/runbooks AGENTS

Runbooks are procedural guidance for operator/workflow tasks. They do not authorize architecture, strategy, probability, lifecycle, settlement, or execution law.

Authority order still runs through code, tests, manifests, DB/runtime receipts, and `docs/authority/**`. Current operational facts require fresh receipts and current-fact pointers.

---

## Runbook Classes

### Durable Operator Runbooks

Load only when the task is about the named operation.

| File | Purpose |
|---|---|
| `live-operation.md` | Day-to-day live daemon operation procedures |
| `live-phase-1-first-boot.md` | First live daemon boot checklist |
| `forecast-live-daemon.md` | Forecast-live daemon startup, cutover verification, and triage |
| `settlement_mismatch_triage.md` | Investigating Zeus-vs-Polymarket settlement mismatches |
| `tigge_cloud_download.md` | TIGGE cloud download supervision and Zeus handoff guidance |

### Packet-Scoped / Historical Runbooks

Use only when the current task explicitly routes to them as evidence/history. They are not default runtime law.

### Contributor / Workflow Support

Use for workflow mapping, not runtime truth.

---

## Rules

- Do not store current PIDs, loaded SHAs, live balances, active positions, account filenames, secrets, or dated progress diaries in durable runbooks.
- Mark phase-specific assumptions clearly and add freshness/expiry when a procedure depends on current state.
- Do not let a runbook override `docs/authority/zeus_current_architecture.md` or `zeus_current_delivery.md`.
- Dated local/cloud operational snapshots belong in evidence/artifacts/history, not durable procedure.
- Non-live/shadow/backtest procedure is not peer authority for live-money execution.
