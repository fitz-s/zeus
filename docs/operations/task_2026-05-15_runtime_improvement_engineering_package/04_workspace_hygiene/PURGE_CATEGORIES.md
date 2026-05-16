# Workspace Purge Categories

Status: SPEC (rules only; no execution in this packet)
Owner of execution: `02_daily_maintenance_agent` (under dry-run-first
discipline)

## Principle

Every purge rule below is a CATEGORY rule, not a one-off cleanup. Each rule
must be: (a) machine-decidable from filesystem + git state, (b) reversible by
git or a quarantine restore, (c) backed by at least one currently-present
example so the rule is grounded in observed entropy, not hypothetical entropy.

A rule that cannot cite a current example must be removed.

## Categories

### Category 1: LaunchAgent Backup Shrapnel

**Rule**: Files under `~/Library/LaunchAgents/` matching the regex
`\.(bak|backup|replaced|locked|before_[a-z_]+)[-._]?[0-9TZ]*(?:\.bak)?$`,
older than `BACKUP_TTL_DAYS` (default 14), are quarantine candidates.

**Action**: Move to `~/Library/LaunchAgents/.archive/<YYYY-MM>/` (NOT delete).
A quarantine retention of 90 days follows; after that, eligible for deletion
under a separate quarterly review by the human, not the agent.

**Currently observed examples** (from `00_evidence/WORKSPACE_MESS_AUDIT.md`):
- `com.zeus.data-ingest.plist.bak-2026-05-15-forecast-live-split`
- `com.zeus.forecast-live.plist.bak-20260515014318`
- `com.zeus.heartbeat-sensor.plist.replaced-2026-05-01.bak`
- `com.zeus.live-trading.plist.bak-2026-04-28-pre-wu-api-key`
- `com.zeus.live-trading.plist.before_proxy_cleanup_20260515T1838Z`
- `com.zeus.live-trading.plist.locked-2026-05-04-cycle-asymmetry-platt-retrain.bak`

Total: 6 backup plists vs 6 active plists — 50% bloat in the LaunchAgents
namespace.

**Forbidden**: Never touch the active plist (file without backup-suffix).
Never `launchctl unload` anything. Never edit plist contents.

**Safety pre-check**: For each candidate, verify the corresponding active
plist exists. If the active plist is missing, the backup is the most recent
authoritative version — refuse to quarantine and escalate to human.

### Category 2: Stale Worktrees

**Rule**: Worktrees under `.claude/worktrees/` and any `../zeus-*` sibling
worktree directory where ALL of:
- No commits in the last `WORKTREE_IDLE_TTL_DAYS` (default 21)
- No uncommitted changes in working tree or index
- Branch has been merged into main OR has zero unique commits ahead of main

are quarantine candidates.

**Action**: Run `git worktree remove --force` AFTER capturing the full diff
against main to `<package_dir>/quarantine/worktrees/<name>_<date>.patch` and
the branch name to a manifest. Patch + manifest enable resurrection.

**Currently observed examples** (from evidence):
- `../zeus-data-daemon-authority-chain-2026-05-14` — 76 commits ahead, NOT
  stale (active development; never touch)
- `../zeus-live-order-e2e-goal-2026-05-15` — 5 commits ahead (recent; check
  TTL before quarantine)
- `../zeus-live-order-e2e-verification-2026-05-15` — 28 commits ahead, the
  current branch base; explicitly EXEMPT from purge

**Forbidden**: Never touch a worktree on the currently-checked-out branch.
Never quarantine a worktree with uncommitted changes. Never quarantine a
worktree whose branch name appears in any open PR.

### Category 3: In-Repo Scratch Directories

**Rule**: Top-level repo paths matching `tmp/`, `scratch/`, `debug_*/`,
`*.scratch.*`, `*.tmp` (file or dir) older than `SCRATCH_TTL_DAYS` (default
7) are quarantine candidates.

**Action**: Move to `<repo>/.archive/scratch/<YYYY-MM>/<original-path>`.
Quarantine retention 30 days, then human review.

**Currently observed examples**: `./tmp` (single dir at repo root, content
unaudited; flag, do not auto-quarantine until first contents-pass confirms it
holds no source-of-truth files).

**Forbidden**: Never touch any path under `src/`, `tests/`, `architecture/`,
`docs/`, `state/`, `config/`, `scripts/`, even if name pattern matches.
Pattern match applies to top-level repo names only.

### Category 4: Closed-Packet Archival Candidates

**Rule**: Packet directories under `docs/operations/task_*/` where ALL of:
- Directory name date (`task_YYYY-MM-DD_*`) is older than
  `PACKET_ARCHIVE_TTL_DAYS` (default 60)
- No file under the packet has been modified in the last 30 days
- No active source/architecture/config/docs file references a path inside the
  packet (grep coverage check)
- The packet does not appear in `architecture/reference_replacement.yaml` as
  a load-bearing replacement source
- The packet directory does not contain a `Status: AUTHORITY` or
  `Status: ACTIVE_LAW` field

are archive candidates.

**Action**: Move to `docs/operations/archive/<YYYY-Q[1-4]>/<original-name>/`
with a manifest file at the original path:
```
docs/operations/<original-name>.archived
{archived_to: ..., archived_at: ..., archived_by: maintenance_agent,
 last_modified: ..., reference_grep_count: 0, restore_command: ...}
```
This stub remains discoverable by future grep so closed packets are not
silently invisible.

**Currently observed examples** (from evidence):
- `docs/operations/task_2026-04-26_ultimate_plan` — only packet >30d old by
  filename; do NOT archive without grep-coverage check first (the name
  suggests it may be load-bearing).

**Forbidden**: Never archive a packet referenced by any current authority
doc, by any open PR, by `architecture/docs_registry.yaml`, by any active
`.claude/CLAUDE.md` / `AGENTS.md` chain, or by any topology profile.

### Category 5: Stale Untracked Top-Level Files

**Rule**: Untracked files (`git status --porcelain '^??'`) at the repo root
or one level deep, NOT in any directory under `docs/operations/task_*/`,
older than `UNTRACKED_TTL_DAYS` (default 14) by mtime, are quarantine
candidates.

**Action**: Move to `<repo>/.archive/untracked/<YYYY-MM>/`. Same restore
discipline as Category 3.

**Currently observed examples**: None at this snapshot beyond the active
package directory itself (which is exempt as in-progress).

**Forbidden**: Never touch a file under any `task_*/` directory (those have
their own archival rule). Never touch a file matching `.env*`, `*credential*`,
`*secret*`, `*key*`, even if untracked — escalate to human.

### Category 6: Empty / Zero-Byte Result Files

**Rule**: Files under `state/`, `logs/`, `evidence/`, `proofs/` with size 0
bytes, older than 7 days, are deletion candidates (these are placeholder
artifacts that never received their content).

**Action**: Delete after capturing path + ctime + mtime to a manifest. NOT
quarantined — zero bytes carries no recoverable content.

**Forbidden**: Never delete a non-zero file under this rule. Never delete
under `state/` if the file is referenced by an active SQLite ATTACH or by a
runtime open file handle (check via `lsof` before action).
Explicitly excluded from this rule regardless of file size or `lsof` result:
`*.db`, `*.db-wal`, `*.db-shm`, `*.sqlite`, `*.sqlite3`, `*.sqlite-wal`,
`*.sqlite-shm`. Rationale: SQLite WAL files (`*.db-wal`, `*.db-shm`) can be
0 bytes briefly between checkpoints when no transactions are pending; `lsof`
may not show an open handle during that window, but deletion corrupts recovery
state. These patterns are unconditionally off-limits for the agent; state
database cleanup runs under a separate human-initiated procedure.

**Currently observed examples**: None at this snapshot — no legitimate
zero-byte targets exist in the workspace at assessment time. Category 6 is
therefore preemptive, not driven by current entropy. Future maintenance
should re-evaluate whether this rule earns its place before the first live
execution tick; if no examples emerge during the 30-day dry-run floor, the
rule should be flagged for human review before being kept.

## Cross-Category Discipline

- Every purge runs in DRY_RUN by default. The agent emits a proposed
  manifest; live execution requires either an explicit human acknowledge
  command or a per-category live-default flag flipped by the human.
- Every action writes to `02_daily_maintenance_agent/evidence_trail/<date>/`
  before touching the filesystem. The trail records the rule that fired, the
  inputs evaluated, the decision, and the resulting filesystem operation.
- Quarantine is the default; deletion is the exception. A category that
  defaults to deletion must justify why (Category 6 is the only one).
- A purge rule can be paused per-category by a `pause` field in the
  TASK_CATALOG without disabling the entire agent.

## TTL Configuration Surface

All `*_TTL_DAYS` values are config-knobs surfaced in `TASK_CATALOG.yaml`,
not constants in code. The default values above are starting points; the
agent's first 4 weeks of operation produce p50/p95 metrics on
"quarantine-then-restored" rate per category, and the human tunes TTLs based
on that signal.

## Coverage Gaps Acknowledged

Categories NOT yet covered (out of scope for this iteration; flagged for
follow-up packet):
- `state/` SQLite WAL/SHM stragglers from killed processes
- `logs/` rotation (already handled separately by Zeus)
- `.git/` pack/object cleanup (handled by `git gc`)
- Codex / Claude session transcripts under `~/.claude/projects/**`
- Browser/Computer-Use temporary downloads under `~/Downloads/zeus-*`

These are deferred to a `purge_categories_v2` packet rather than added here
incomplete.
