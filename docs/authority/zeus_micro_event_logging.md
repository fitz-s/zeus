# Zeus Micro-Event Logging Rules

Status: Active governance law
Source: Extracted from root `AGENTS.md` §2 (committed version, 2026-04-09)
Referenced by: `AGENTS.md` §7 (working discipline)

---

## 1. Separation of concerns

| Surface | Purpose | Who may write |
|---------|---------|---------------|
| `architects_progress.md` | Packet-level durable state only | Leader only |
| `architects_task.md` | Active control state only | Leader only |
| `.omx/context/<packet>-worklog.md` | Micro-events, retries, scout findings, experiment breadcrumbs | Any agent |

### What goes where

**Do not** dump every small attempt into `architects_progress.md`.

Small events, retries, scout findings, timeout notes, and experiment breadcrumbs belong in `.omx/context/<packet>-worklog.md`.

### Spark scout rules

- Spark scouts **may** draft or append micro-event worklog entries.
- Spark scouts **must not** directly edit `architects_progress.md` or `architects_task.md`.
- The leader is responsible for **promoting** a worklog fact into `architects_progress.md` only when it becomes a real packet state transition, blocker, or accepted evidence item.

---

## 2. Preferred micro-event format

```md
## [timestamp local] <packet> <slice-or-event>
- Author:
- Lane:
- Type: scout | retry | timeout | evidence | blocker | note
- Files:
- Finding:
- Evidence:
- Suggested next slice:
- Promote to architects_progress/task?: yes | no
```

### Field definitions

| Field | Required | Description |
|-------|----------|-------------|
| `Author` | Yes | Agent identity or model name |
| `Lane` | Yes | Which execution lane (main, scout-1, critic, etc.) |
| `Type` | Yes | One of: `scout`, `retry`, `timeout`, `evidence`, `blocker`, `note` |
| `Files` | If applicable | Files read or modified |
| `Finding` | Yes | What was discovered or attempted |
| `Evidence` | If applicable | Test output, grep result, error message |
| `Suggested next slice` | Optional | What should happen next based on this finding |
| `Promote to architects_progress/task?` | Yes | Leader's call — default `no` unless packet-level state change |

---

## Related documents

- `docs/authority/zeus_packet_discipline.md` — Packet closure and evidence rules
- `AGENTS.md` §7 — Working discipline
- `AGENTS.md` §8 — File placement rules (`.omx/context/` location)
