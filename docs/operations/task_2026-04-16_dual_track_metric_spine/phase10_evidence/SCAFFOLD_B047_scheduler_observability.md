# Scaffold — B047 Scheduler Observability

Created: 2026-04-21
Authority basis: Phase 10 DT-close bug100 K2 tail — `src/main.py` scheduler wrapper observability gap

## Section 1 — Assumption Discovery

| Value | Current | Source | Volatility | Silent failure impact |
|---|---|---|---|---|
| APScheduler `BlockingScheduler` error semantics | job exceptions propagate to scheduler; logs emit then scheduler continues | apscheduler docs | YEARLY | If behavior changes (e.g., scheduler exits on exception), wrapper pattern may need adjustment |
| `status_summary.write_status(dict)` semantics | atomic replace of `state/status_summary.json`; treats `dict` as cycle_summary slot | `src/observability/status_summary.py:98` | NEVER (internal) | — |
| OpenClaw supervisor staleness window | unknown (external daemon manager) | OpenClaw side | UNKNOWABLE | Daemon process-exit on job failure triggers supervisor restart livelock (docs: `27bedbd` commit msg) |

## Section 2 — Provenance Interrogation

| Artifact | Created by / when | Assumptions | Still valid? | Recompute |
|---|---|---|---|---|
| `_run_mode` `write_status({failed, failure_reason})` pattern | P9A `7081634` (2026-04-18) | "write_status is sufficient observability for daemon job failures" | Valid but scoped to 1/11 wrappers | Every scheduler.add_job target should emit health on failure — currently only `_run_mode` does |
| `27bedbd` scheduler fail-open design (log-and-continue) | K2 `27bedbd` (2026-04-13) | "Daemon must keep running; job exception must not process-exit" | Valid — OpenClaw supervisor relies on heartbeat | First-principles: fail-observable, not fail-close. Health file records status; daemon continues |

## Section 3 — Cross-Module Relationships

| Relationship | Why must hold | Silent violation | Enforcement |
|---|---|---|---|
| Every callable registered via `scheduler.add_job(fn, ...)` → fn must emit observable failure state (log + status file) | Without status write, operator has no observability of persistent per-job failure; daemon looks healthy because heartbeat continues | Job throws hourly for days; logs grow; status JSON shows last-good state; operator sees green dashboard | **Target**: AST antibody walks `main.py`, finds `scheduler.add_job(X, ...)` sites, asserts X routes through `@_scheduler_job` decorator (`_write_heartbeat` exempt). Makes "register job without observability wrapper" structurally impossible |

## Section 4 — What I Don't Know

- **Q**: Where should per-job health live? `status_summary.json` (single file, overwrite) OR new `scheduler_jobs_health.json` (dict keyed by job_name)? → **A**: new file. Overwriting status_summary would stomp between concurrent jobs.
- **Q**: Heartbeat frequency — 60s file writes OK? → Skip decorator on heartbeat. Heartbeat IS the observability mechanism; no self-observability needed.
- **Q**: Does `_run_mode` need the decorator (already writes status via status_summary)? → Yes — decorator writes to `scheduler_jobs_health.json`, independent of `status_summary.json`. Non-conflicting, dual-signal.
- **Q**: Auto-pause on N consecutive failures? → Out of scope. This fix is observability-only. Follow-up ticket if needed.

## Completion criteria

- [x] Section 1: all external values cited (APScheduler docs, OpenClaw supervisor behavior)
- [x] Section 2: predecessor work (P9A, K2 design) verified current
- [x] Section 3: relationship enforced by AST antibody
- [x] Section 4: design choices made explicit (separate health file, skip heartbeat, double-wrap _run_mode)
