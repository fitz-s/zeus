# Daily Maintenance Agent — Safety Contract

Status: SPEC (binding for the implementation packet)
Version: 1
Authority: this contract is enforced both as code (precondition checks
inside the agent core) AND as audit doc (humans audit the boundary
without needing to read code).

## Purpose

The agent moves files, opens hygiene PRs, and quarantines artifacts. Each of
those is reversible. But a single untrapped path bug — touching `src/` or
`state/` or a credential file — converts the agent from useful into
catastrophic. This contract enumerates exactly what the agent may and may
not touch, and the failure mode if a boundary is crossed.

Failure mode for any boundary crossing: agent FATAL ERROR, exit non-zero,
write to `errors.tsv`, send alert via configured channel. The agent does
NOT continue with subsequent tasks after a contract violation.

## Forbidden Targets (Hard Block)

The agent must NEVER read-write or move any file matching:

### Source code and tests
- `src/**`
- `tests/**`
- `scripts/**` (except agent's own self-evidence under `state/maintenance_evidence/**`)
- `bin/**`
- Any `*.py`, `*.ts`, `*.rs`, `*.go`, `*.c`, `*.cpp`, `*.swift` outside
  `docs/operations/archive/**` and `${STATE_DIR}/**`

### Authority surfaces
- `architecture/**`
- `docs/reference/**`
- `AGENTS.md` (any file at any depth named exactly `AGENTS.md`)
- `CLAUDE.md` (any file named exactly `CLAUDE.md`)
- `.claude/CLAUDE.md`, `.claude/settings.json`, `.claude/agents/**`,
  `.claude/skills/**`, `.claude/hooks/**`
- `.codex/hooks.json`, `.codex/hooks/**`
- `.openclaw/**` outside the agent's own `.openclaw/cron/jobs.json` job
  entry (which the agent reads but never writes)

### Runtime / state
- `state/*.db`, `state/*.db-wal`, `state/*.db-shm`, `state/*.sqlite*`
- `state/calibration/**`, `state/forecasts/**`, `state/world/**`
- `~/Library/LaunchAgents/com.zeus.*.plist` (active plist; backups are
  the ONLY allowed action surface, see PURGE_CATEGORIES Category 1)

### Secrets and credentials
- Any path matching `*.env`, `.env*`, `*credential*`, `*secret*`, `*key*`,
  `*token*`, `*.pem`, `*.p12`, `*.pfx`, `*authn*`, `*oauth*`
- Anything under `~/.aws/`, `~/.gcloud/`, `~/.ssh/`, `~/.config/gcloud/`,
  `~/.config/op/`, `~/.openclaw/agents/*/agent/auth-profiles.json`
- Any file containing `BEGIN .* PRIVATE KEY` in its first 200 bytes

### Git plumbing
- `.git/**` (entirely — never)
- `.gitmodules`, `.gitattributes`, `.gitignore` (read-only)
- Any file outside the agent's own commits / branches /
  PRs (history modifications forbidden)

### External system surfaces
- `~/Library/LaunchAgents/*.plist` (active) — never `launchctl load/unload`
- `~/Library/LaunchDaemons/**`
- `/etc/**`, `/usr/local/etc/**`
- The `crontab -e` table (never edit user crontab)
- Any GitHub mutation other than opening `[maintenance]`-tagged PRs and
  posting comments on those PRs

## Forbidden Actions (Hard Block)

Even on permitted paths, the agent must NEVER:

- `rm` any non-zero-byte file (use quarantine move instead)
- `git push --force` (any push, force or not, is restricted to maintenance
  branches with the `[maintenance]` PR tag)
- `git rebase` or `git reset --hard` for any reason
- Merge any PR (its own or otherwise)
- Approve any PR
- Comment on a non-maintenance PR
- Close or open issues
- Run `pip install` / `npm install` / `cargo add` / any package
  install/uninstall
- Run any pytest / shell command that mutates state outside the agent's
  evidence dir, beyond the explicit Bash invocations in PURGE_CATEGORIES /
  ARCHIVAL_RULES procedures
- Modify `.claude/scheduled_tasks.json` (the agent uses cron/launchd, not
  Claude scheduled tasks)
- Trigger another agent (no Task / SendMessage / Agent tool use; the agent
  is leaf, not orchestrator)
- Change file permissions (`chmod`) or ownership (`chown`)
- Symlink across the safety boundary (no `ln -s` from quarantine into
  forbidden-path set)
- Network requests beyond `gh pr create / gh pr view / gh api repos/.../labels`
  for the maintenance-PR flow; no fetch from arbitrary URLs

## Allowed Targets (Whitelist)

The agent's WRITE/MV/MKDIR authority is exhaustively limited to:

### Read + write + create
- `${STATE_DIR}/**` (its own state)
- `${EVIDENCE_DIR}/**` (its own evidence trail)
- `${REPO}/.archive/**` (in-repo quarantine root)
- `~/Library/LaunchAgents/.archive/**` (LaunchAgents quarantine root)

### Move only (git mv from source to destination)
- Source: `docs/operations/task_*/` matching ALL exemption checks
- Destination: `docs/operations/archive/<YYYY>-Q<N>/<original-name>/`

### Create stub only
- `docs/operations/<archived-packet-name>.archived` (single 12-line manifest)

### Open PR only (against main)
- Branch name prefix: `maintenance/`
- Required label: `maintenance`
- Allowed paths in PR diff: union of all allowed-write paths above
- Forbidden paths in PR diff: any path in the Forbidden Targets section
- The agent NEVER merges its own PR

### Read-only authority
- The entire repo (read everything for grep/inspection)
- `~/Library/LaunchAgents/*` (read all plists for active-vs-backup status)
- `git log`, `git diff`, `git worktree list`, `git status` (any ref, any path)
- `gh pr list`, `gh pr view`, `gh api` for maintenance flow only

## Pre-Action Validator

Before every filesystem mutation, the agent's `validate_action(path,
operation)` function MUST be called. It returns:
- `ALLOWED` → proceed
- `FORBIDDEN_PATH` → fatal error, log path + matched forbidden rule
- `FORBIDDEN_OPERATION` → fatal error, log operation + matched forbidden rule
- `MISSING_PRECHECK` → fatal error, log which pre-check the rule required
- `ALLOWED_BUT_DRY_RUN_ONLY` → emit proposal, do NOT mutate

The validator is the LAST line of defense; rule files are the first. If the
validator says `FORBIDDEN_*`, the rule that proposed the action is
mis-written and the agent halts.

## Validator Semantics

The `validate_action(path, operation)` function MUST implement all of the
following guarantees before the implementation packet P5 is acceptable:

(a) **Read on forbidden path = FORBIDDEN_PATH.** Bare reads of credential
files, `state/*.db*`, authority surfaces, and any other path in the Forbidden
Targets section are FORBIDDEN_PATH, not silently allowed. READ is not exempt.

(b) **Canonicalize via `realpath` before match.** Every path is resolved to
its canonical absolute form (symlink-expanded, `..`-collapsed) using
`os.path.realpath()` or equivalent BEFORE any glob/pattern match is applied.
A path like `~/Library/LaunchAgents/.archive/../com.zeus.live-trading.plist`
must expand to the active plist and trigger FORBIDDEN_PATH.

(c) **Symlink and hardlink resolution policy.** Pre-existing symlinks in
the filesystem are resolved (via `realpath`) before pattern matching;
the agent never follows a symlink whose resolved target escapes the
allowed-write set. Hardlinks are treated identically to their target:
if the inode resolves to a forbidden path, the operation is FORBIDDEN_PATH
regardless of the path label presented.

(d) **Per-leaf decomposition for directory operations.** Operations that
act on a directory (e.g., `git mv` on a directory subtree) are decomposed
into per-file leaf checks. Every file under the directory subtree is
individually validated before the directory operation proceeds. A single
leaf failure aborts the entire directory operation.

(e) **Git remote URL allowlist before any push.** Before any `git push`
(maintenance-branch or otherwise), `validate_action` checks the remote URL
against an allowlist captured at install time into
`${STATE_DIR}/install_metadata.json` (field `allowed_remote_urls`). Only
the project's main origin URL — discovered at first run and pinned in
install metadata — is permitted. Any redirect, rewrite, or `git remote
set-url` that changes the remote URL flips all subsequent pushes to
FORBIDDEN_OPERATION until the install metadata is updated by a human.

These five guarantees are the contract P5 must satisfy; the implementation
is not acceptable until all five are verifiable by an automated test.

## Audit-by-Grep Discipline

A human must be able to enumerate every agent action in the repo with one
command:

```
git log --author='Maintenance Worker' --pretty='%h %ai %s'
```

Every commit message contains the run-id; every run-id maps to an evidence
trail directory. Reverse lookup is one grep.

## Kill Switch

The human can stop the agent permanently with:

```
touch ${STATE_DIR}/KILL_SWITCH
```

The agent reads this on every tick before doing anything else. The kill
switch is sticky — the agent never auto-removes it. Removal is explicit
human action.

A second softer pause:

```
touch ${STATE_DIR}/MAINTENANCE_PAUSED
```

This skips ticks but does not require a re-acknowledge to resume. Useful
for short-duration freezes (deploys, incidents).

## Allowed Drift Of This Contract

Updates to this contract are themselves human work. The agent never
proposes contract changes; the agent reads the contract and obeys. If the
agent encounters a path it cannot classify, it errors with the path and
halts; the human then either updates the rule or the contract.

## Accidental-Trigger Containment

If the agent is invoked outside its scheduler (e.g., a human runs the CLI
manually), it MUST detect this (no scheduler env var, no expected parent
process) and refuse to mutate, falling back to a one-shot DRY_RUN-only
mode regardless of `live_default` flags.

## What If A Forbidden Mutation Already Happened

If the precondition validator misses a violation and a forbidden mutation
lands on disk:
1. The agent self-quarantines: writes a `${STATE_DIR}/SELF_QUARANTINE`
   file, exits non-zero, sends an URGENT alert.
2. All future ticks refuse to run until the human removes the file.
3. The PR (if any) is left unmerged for human inspection.
4. The mutation is reverted by the human, NOT by the agent (the agent's
   credibility is compromised; let humans reconcile).
