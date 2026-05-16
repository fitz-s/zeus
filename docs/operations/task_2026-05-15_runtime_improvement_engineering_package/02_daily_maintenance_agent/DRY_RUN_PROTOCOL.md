# Daily Maintenance Agent — Dry-Run Protocol

Status: SPEC
Purpose: define how DRY_RUN proposals flow to humans, how they get
acknowledged, and how live execution is gated.

## Core Principle

Every task in `TASK_CATALOG.yaml` defaults to `dry_run: true`. The agent's
first 30 days of operation should be 100% dry-run. Live execution is
unlocked task-by-task by the human after they have confidence in the
proposals.

The agent NEVER auto-promotes a task from dry-run to live based on its own
judgment.

## Proposal Format

Each dry-run task emits a proposal under
`${EVIDENCE_DIR}/<date>/proposals/<task_id>.md`:

```markdown
# Proposal: <task_id> — <date>

## Run Context
- evidence_trail_id: <date>
- agent_version: <semver>
- config_snapshot: ${EVIDENCE_DIR}/<date>/config_snapshot.json
- guards_passed: 7/7

## Rule Applied
- source: ../../04_workspace_hygiene/PURGE_CATEGORIES.md#category-N
- rule_summary: <one paragraph>

## Candidates Evaluated
| Path | Match Reason | TTL Met | Proposed Action | Reversibility | Blast Radius |
|------|--------------|---------|-----------------|---------------|--------------|
| <p1> | <reason>     | yes     | quarantine      | git restore   | LOW          |
| <p2> | <reason>     | no      | skip            | n/a           | n/a          |
...

## Pre-Check Results
| Path | Pre-check | Result | Detail |
|------|-----------|--------|--------|
| <p1> | active_plist_must_exist | PASS | com.zeus.X.plist found |
| <p2> | not_uncommitted_worktree | FAIL | uncommitted: src/foo.py |

## Proposed Manifest (would-be filesystem operations)
1. mv <src1> -> <dst1>
2. mv <src2> -> <dst2>
3. (skip <p3>: pre-check FAIL)

## Cumulative Counts
- Candidates evaluated: <N>
- Would-act: <K>
- Skipped (pre-check or TTL): <N-K>
- Estimated bytes moved: <bytes>
- Estimated PR diff size: <lines>

## Human Acknowledge Path
To approve this exact proposal for live execution on the next tick, run:
  touch ${STATE_DIR}/ack/<task_id>/<proposal_hash>.ack

To approve THIS task as live-default going forward, edit
TASK_CATALOG.yaml and set `live_default: true` for `<task_id>`.

To reject, do nothing. The proposal expires after PROPOSAL_TTL_DAYS (7).
```

## Acknowledge Mechanism

Per-proposal acknowledge:
- `<proposal_hash>` is a SHA256 of the proposal manifest body. Any change
  to candidates between proposal and apply invalidates the ack.
- The agent on the next tick checks for an `.ack` file matching the current
  proposal hash. If present AND not stale, the task runs live ONCE.
- After live execution the `.ack` is moved to
  `${STATE_DIR}/ack/<task_id>/applied/<proposal_hash>_<applied-at>.done`.
- An ack file matching a stale hash (proposal changed since human acked) is
  NOT honored; new dry-run proposal is emitted instead, with a
  `last_ack_was_for_different_proposal: true` flag and a diff between the
  two proposals.

Per-task live-default:
- Setting `live_default: true` in TASK_CATALOG.yaml causes the task to skip
  the ack gate entirely.
- This is a HUMAN edit; the agent never modifies TASK_CATALOG.yaml.
- Recommended for low-blast-radius tasks only after ≥30 days of clean
  dry-run history.

## Promotion Ladder

The human moves tasks from dry-run to live in a deliberate ladder, NOT all
at once. Recommended order:

1. **Week 0–4**: All tasks dry-run. Human reviews proposals daily, looks
   for false positives.
2. **Week 4 (after clean dry-run history)**: Promote ONLY
   `zero_byte_state_cleanup` to live (lowest blast radius — pure deletion
   of zero-byte files).
3. **Week 6**: Promote `agent_self_evidence_archival` (agent maintains its
   own state).
4. **Week 8**: Promote `launchagent_backup_quarantine` (quarantine, not
   deletion; reversible).
5. **Week 10**: Promote `in_repo_scratch_quarantine` (quarantine).
6. **Week 12**: Promote `untracked_top_level_quarantine`.
7. **Week 14**: Promote `stale_worktree_quarantine` (higher blast — touches
   git worktree state).
8. **Week 16+**: `closed_packet_archive_proposal` and
   `lore_proposal_emission` may stay in dry-run perpetually; they involve
   PRs that already require human merge AND archival is the highest-blast
   action in the catalog.

The ladder is a recommendation; humans may go faster or slower per task.

## Notification Channel

Each dry-run proposal triggers:
- Write to `${EVIDENCE_DIR}/<date>/proposals/<task_id>.md`
- Append summary line to `${EVIDENCE_DIR}/<date>/SUMMARY.md`
- Send notification via configured channel (Discord/Slack/email/none)
  with: task id, candidate count, would-act count, link to full proposal

A LIVE execution triggers an additional:
- Append to `${EVIDENCE_DIR}/<date>/applied/<task_id>.tsv`
- Notification with: task id, applied action count, evidence row anchor,
  PR URL if any

## Diff Surface

Every dry-run proposal MUST be paired with a diff surface that lets the
human inspect the actual filesystem changes the agent WOULD make:

```
${EVIDENCE_DIR}/<date>/proposals/<task_id>.diff
```

Format: a unified diff or a `git mv --dry-run`-equivalent textual
representation. Reading this file should be all the human needs to decide.
The proposal `.md` is for context; the `.diff` is the source of truth on
"what changes."

## Rollback Surface

For every LIVE action, the agent records a rollback recipe in
`${EVIDENCE_DIR}/<date>/applied/<task_id>.rollback`:
- Per quarantine move: the inverse `mv` command
- Per `git mv` archival: `git mv <archive_path> <original_path> &&
  git commit -m 'revert maintenance archival of <name>'`
- Per zero-byte deletion: `touch <path>` (the file was empty; the path is
  what mattered)
- Per opened PR: `gh pr close <num> && gh pr delete <num>` plus the branch
  delete

Rollback recipes are append-only. The agent NEVER auto-rollbacks.

## Stale Proposal Cleanup

Proposals not acked within `PROPOSAL_TTL_DAYS` (default 7) are auto-cleared
on the next tick:
- Move to `${EVIDENCE_DIR}/<date>/proposals/.expired/`
- A summary line in SUMMARY.md notes the expiration
- The next tick will emit a fresh proposal with new hash if the underlying
  candidates still match

Stale proposals do NOT block subsequent ticks. The human can ignore the
agent for a week and return to find the queue self-trimmed.

## Bulk Acknowledge (Power User)

For trusted task ids, the human may write:

```
${STATE_DIR}/ack/<task_id>/AUTO_ACK_NEXT_N=<int>
```

The agent honors this for the next N ticks even on changed proposals.
After N ticks the file auto-expires; this is the only auto-modification of
ack state the agent performs.

Recommended for `zero_byte_state_cleanup` only after sufficient operational
history.

## Auditability

Every live action MUST be reconstructible from the evidence trail alone,
without consulting external logs or memory. The triplet (config snapshot +
proposal + applied row + rollback recipe) is the complete audit unit. If a
future investigator cannot reconstruct what happened from those four
artifacts, the evidence emit is incomplete and the implementation is
incorrect.
