# Zeus Total-Loss Loop

This loop optimizes the time ordering before a held-side executable bid first
falls below Zeus's absolute live unit-price floor. The floor is an incident
boundary, not a trading rule. It is read from active settings and has no silent
fallback; missing or invalid floor authority stops detection and dispatch.

For each incident it reconstructs `t_source`, `t_probability`, `t_monitor`,
`t_decision`, `t_command`, `t_fill`, and `t_floor`. Success means probability,
decision, actuation, and execution lead remain positive while after-cost
capital growth and valid HOLD behavior are preserved.

## Runtime

All runtime state is under `.total_loss/` and ignored by Git:

- `memory.db`: dedicated episodic memory and queue state.
- `incidents/<id>/evidence.db`: bounded, time-aligned incident evidence.
- `incidents/<id>/codex-*.jsonl`: orchestration event streams.
- `codex-home/`: isolated Codex authentication/configuration surface.
- `worktrees/`: serial incident repair worktrees.
- `benchmarks/` and `logs/`: grading and controller evidence.

The detector reads canonical SQLite databases read-only. The market channel
keeps `execution_feasibility_latest` current and appends every held token's
full-depth WebSocket and held-REST quote to `execution_feasibility_evidence`,
and also appends BBA transitions whose depth is temporarily unavailable, so a
250 ms bounded cursor reader cannot lose an intermediate crossing. Quote
replay is clipped to the canonical economic-exposure interval; pre-entry and
post-exit prices are not incidents. A missing bid is a separate high-priority
no-executable-book incident and never fabricates `t_floor`.

## Commands

```bash
python3 total_loss_loop.py bootstrap
python3 total_loss_loop.py probe
python3 total_loss_loop.py scan-once
python3 total_loss_loop.py daemon
python3 total_loss_loop.py status
```

`bootstrap` creates only `.total_loss/`. `scan-once` performs the seven-day
backfill, detects current floor incidents, and refreshes the top precursor.
`daemon` repeats that operation and dispatches available investigation slots.
The only operator kill switch is `.total_loss/HALT`. A clean daemon stop also
terminates and requeues its active Codex process groups, so HALT cannot leave
orphan investigations or repair commands running.

## Investigation contract

Blind diagnosis receives the incident evidence DB and repository, but no prior
root narrative. Dedicated memory is retrieved only after blind diagnosis.
Every accepted diagnosis must identify an earliest preventable timestamp,
precise seam, and executable capital counterfactual. No-bid incidents remain
open upstream until entry, sizing, and earlier exit opportunity are evaluated.

Codex CLI capabilities are probed from the installed binary and model catalog.
The controller does not assume a context-window size or reasoning effort. The
current CLI cannot upgrade a resumed read-only session to workspace-write, so
blind diagnosis and repair are separate sessions; the repair session persists
across test and fresh-review feedback. The probe executes structured-output,
workspace-write, session-resume, multi-agent, and GitHub-network smoke checks;
profile-content changes invalidate the probe.

## Delivery

Codex owns repair judgment, PR feedback, and merge preparation. It cannot mark
production verified. After an exact GitHub merge receipt, an independent
controller worker proves a clean `live` checkout, performs `ff-only`, invokes
`scripts/deploy_live.py restart all`, checks exact loaded SHA, heartbeat and
every open position's monitor cadence, then observes the configured production
window for same-root recurrence. Only those receipts can complete an incident.
Production grading persists probability/decision/command/fill lead, avoidable
loss, deployment identity, recurrence, and root utility in dedicated memory.

Install the plist only after `bootstrap` has created `.total_loss/logs`:

```bash
python3 scripts/install_launchd_plist.py total_loss_loop.plist
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/total_loss_loop.plist"
```
