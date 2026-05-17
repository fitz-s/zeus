# Daily Maintenance Agent — Design

Status: SPEC (no code in this packet; implementation packet listed in
`05_execution_packets/PACKET_INDEX.md`)
Codename: `mw-daemon` (Maintenance Worker daemon — generic name, project-agnostic)

## Goal

A scheduled, autonomous, dry-run-first maintenance worker that prevents
workspace entropy from accumulating between human review cycles. It does
NOT replace the human; it makes human review cheap by surfacing diffs in
manageable batches with full reversal paths.

## Non-Goals

- The agent does not modify `src/`, `architecture/`, `state/`, `config/`,
  hooks, plists, credentials, or any path in `SAFETY_CONTRACT.md`'s
  forbidden list.
- The agent does not open PRs that change application behavior. Its PRs are
  always pure-hygiene and tagged `[maintenance]`.
- The agent does not make policy decisions. Rules live in
  `04_workspace_hygiene/*.md`; the agent only applies rules.
- The agent does not learn / self-modify. Learning is a separate human
  process that updates the rule files; agent picks up rule changes on next
  tick.

## Project-Agnostic Surface

The agent core is project-agnostic. A project adopts it by providing:

```
.maintenance/
  config.yaml         # rule TTLs, allowlists, schedule, evidence dir
  task_catalog.yaml   # which tasks run on which schedule (see TASK_CATALOG.yaml)
  safety_contract.md  # forbidden paths and actions (see SAFETY_CONTRACT.md)
  hygiene_rules/      # PURGE_CATEGORIES, ARCHIVAL_RULES, LORE_EXTRACTION
  bindings/           # project-specific glue (the "Zeus binding" lives here)
```

The agent core (the executable that runs daily) reads these files and
nothing else. No project-specific code paths inside the core.

## Scheduling Surface

The agent supports three scheduling modes; the project picks one or more:

1. **launchd** (macOS): `~/Library/LaunchAgents/<reverse-dns>.maintenance.plist`
   with a `StartCalendarInterval` (e.g., daily 04:30 local). Single-shot per
   tick. Logs go to a managed file under `~/Library/Logs/`.
2. **OpenClaw cron** (existing layer at `~/.openclaw/cron/jobs.json`): a
   job entry calling the agent CLI. Runs in OpenClaw's process namespace,
   so it shares the same Python venv and identity as other agents.
3. **In-process schedule**: a long-running daemon with internal cron loop,
   useful for environments without launchd/cron. The agent self-publishes
   liveness to `<state_dir>/heartbeat.json`.

Pick mode per project. For Zeus, default = launchd (matches existing Zeus
daemon pattern), with OpenClaw cron as fallback.

## Tick Lifecycle

A single agent tick is a state machine:

```
START
  │
  ▼
LOAD_CONFIG  ─►  refuse_to_run if config invalid (see Refusal Modes)
  │
  ▼
CHECK_GUARDS  ─►  refuse_to_run if any guard fails:
  │                - dirty repo on main? (refuse, escalate)
  │                - active migration in flight? (skip this tick)
  │                - on-call window override? (skip this tick)
  │                - kill switch present? (refuse, log, exit)
  │
  ▼
ENUMERATE_CANDIDATES  ─►  per task in TASK_CATALOG.yaml, gather candidates
  │                        applying rules from hygiene_rules/
  │
  ▼
DRY_RUN_PROPOSAL  ─►  emit proposal manifest under evidence_trail/<date>/
  │                    each task gets one section: candidates, rule fired,
  │                    proposed action, reversibility, blast radius
  │
  ▼
APPLY_DECISIONS  ─►  per task, apply ONLY if (live_default_for_task = true)
  │                   OR (a human-acknowledge file exists from previous tick)
  │                  emit per-action evidence row to evidence_trail/<date>/
  │
  ▼
SUMMARY_REPORT  ─►  write SUMMARY.md to evidence_trail/<date>/
  │                  optional: notify human via configured channel
  │
  ▼
END
```

Every transition is logged. A tick that fails any guard exits with reason and
DOES NOT silently skip to "no work today" — the human sees the refusal.

## Refusal Modes

The agent refuses to act and exits non-zero (with explicit log) when:

- Config file is missing or fails schema validation.
- Repo is dirty on the currently-checked-out branch.
- Repo is in the middle of a `git rebase`, `git merge`, or `git cherry-pick`
  (interrupted state files present).
- Active migration flag file present (`<state_dir>/MAINTENANCE_PAUSED`).
- Kill switch file present (`<state_dir>/KILL_SWITCH`).
- On-call window declared (`<state_dir>/ONCALL_QUIET`).
- Disk free below threshold (don't compound a disk-full incident with
  archival writes).
- An open PR labeled `maintenance/in-flight` exists (don't pile maintenance
  PRs).

Each refusal is its own exit code so log monitors can distinguish.

## Evidence Trail

Per tick, a directory `evidence_trail/<YYYY-MM-DD>/` is created under the
agent's `<state_dir>`:

```
evidence_trail/2026-05-15/
  config_snapshot.json        # exact config used
  guards.tsv                  # per-guard pass/fail
  proposals/<task>.md         # per-task dry-run proposal
  applied/<task>.tsv          # per-task actions actually taken
  applied/<task>.commits      # SHA of any commit produced
  applied/<task>.pr           # URL of any PR opened
  errors.tsv                  # any errors during apply
  SUMMARY.md                  # human-facing summary, ≤ 200 lines
  exit_code                   # final exit code
```

Evidence trails older than 90 days move to a quarterly archive (under the
agent's own ARCHIVAL_RULES). The agent self-archives. This is the only
self-targeting action it performs.

## Authority Boundaries

The agent has these authority levels:

- `READ_ANY`: anywhere under repo or system paths in `safety_contract.md`'s
  read allowlist.
- `WRITE_TO_QUARANTINE`: only under `<repo>/.archive/`,
  `~/Library/LaunchAgents/.archive/`, and `<state_dir>/`.
- `GIT_MV_ARCHIVE`: only for closed-packet archival from
  `docs/operations/task_*/` to `docs/operations/archive/<YYYY>-Q<N>/`,
  AND only after all 8 exemption checks pass.
- `OPEN_PR_MAINTENANCE_TAG`: only PRs tagged `[maintenance]` against main,
  containing only paths the agent's WRITE/MV authority covers.
- NEVER: edit any source file, edit any architecture file, edit any
  authority doc, edit any plist, edit any credential, edit any state DB.
- NEVER: merge any PR.
- NEVER: force-push, rewrite history, delete branches.
- NEVER: install / uninstall / reload launchd jobs.

These boundaries are enforced in code (a precondition checker that hard
errors on out-of-bounds path) AND in the SAFETY_CONTRACT.md the human can
audit.

### Dry-run floor enforcement

The validator enforces a mandatory 30-day dry-run floor after first
installation. For any task with `live_default: true`, the validator checks:

```
now - install_metadata.first_run_at >= dry_run_floor.floor_days (30)
```

where `install_metadata` is written once to `${STATE_DIR}/install_metadata.json`
on first run and is thereafter immutable. If the floor has NOT elapsed, the
validator converts the action to `ALLOWED_BUT_DRY_RUN_ONLY` regardless of
`live_default`. This makes the 30-day mandate a code gate, not exhortation.

**Override**: A human may bypass the floor by creating
`${STATE_DIR}/dry_run_floor_override.ack` containing a signed acknowledgement
line. The validator checks for this file before enforcing the floor.

**Exempt tasks**: `zero_byte_state_cleanup` and `agent_self_evidence_archival`
carry `dry_run_floor_exempt: true` in TASK_CATALOG.yaml and are NOT subject
to the floor. Rationale: zero-byte deletion is content-free; self-evidence
archival targets only the agent's own state directory. All other `live_default:
true` flips are blocked by the floor until 30 days have elapsed.

## Identity And Provenance

Every commit by the agent has author `Maintenance Worker
<maintenance@<org>>` and a trailer `Run-Id: <evidence-trail-id>`. Every
file the agent writes carries a `Generated-By: maintenance_worker
<run-id>` header comment. Humans can grep all agent-touched files in 1
command.

## Failure Containment

- A task that errors during apply does NOT block subsequent tasks. Each task
  is independent; a failed task logs to `errors.tsv` and the next task runs.
- A task that errors >3 ticks in a row is auto-paused for that task only,
  and the human is notified. Re-enable is manual.
- The agent does not retry within a tick. Retries happen on the next tick.
- A whole-agent crash leaves the partial `evidence_trail/` directory
  on disk; next tick starts fresh and references the prior incomplete
  trail in its SUMMARY for human investigation.

## Cross-Track Bindings

The agent reads policy from:

- `04_workspace_hygiene/PURGE_CATEGORIES.md` for purge candidates.
- `ARCHIVAL_RULES.md` for packet archival.
- `04_workspace_hygiene/LORE_EXTRACTION_PROTOCOL.md` for lore proposals
  (proposal-only; the agent never auto-writes lore cards).
- `03_authority_drift_remediation/REMEDIATION_PLAN.md` for drift surfacing
  (surface-only; never auto-edits authority docs).

The agent never edits any of these policy files. Policy changes are human
work.

## What The Agent Outputs Each Day

A successful tick produces:
- Zero or one `[maintenance]` PR (if any GIT_MV_ARCHIVE actions applied)
- Zero or more quarantine moves under `~/Library/LaunchAgents/.archive/`
  and `<repo>/.archive/`
- Exactly one evidence trail directory
- Exactly one SUMMARY.md surfaced via configured notification channel
- Exactly one lore-proposal manifest (if any closed packets are
  candidates this tick)
- Zero direct deletions of any non-zero-byte file

If a tick produces no actions, SUMMARY.md still exists and reports "no work
today; guards: <list>; candidates evaluated: <count>; reasons for
no-action: <list>." Silent ticks are not allowed; the human must always be
able to confirm the agent is alive.

## Acceptance For Implementation

The implementation packet is acceptable when:
- The agent passes a test fixture of 10 contrived workspace messes (per
  category) with the right verdict per case.
- The agent refuses to run on a dirty repo.
- The agent refuses to act on a forbidden path with a fatal error,
  not a silent skip.
- The agent's evidence trail can reconstruct any decision after the fact.
- A simulated 30-day uninterrupted run produces zero false positives on
  load-bearing closed packets (using the Zeus current packet inventory as
  the test corpus).
