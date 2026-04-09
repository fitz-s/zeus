# Agent Edit Loss Investigation

**Date:** 2026-04-07  
**Investigator:** isolation-researcher  
**Trigger:** Multiple agent edits lost during session — Phase 1 connection factory, SD-1 harvester fix, scripts migration, cycle_runner migration

---

## Findings

### Finding 1: All changes are UNCOMMITTED (root cause #1)

`git status` shows 50+ modified files — ALL unstaged, none committed.

**Impact:** Any of these actions destroys all in-session work:
- `git checkout .` or `git checkout <file>`
- `git restore .` or `git restore <file>`
- `git reset --hard`
- `git stash` (pushes current changes to stash, restores HEAD)
- Another agent cloning or checking out the branch fresh

An agent that "cleans up" before starting work, or any process that resets to HEAD, silently discards hours of edits.

**Evidence:** `git log --oneline -1` shows last commit is `6ea7abb` (math docs) — none of today's code changes are in it.

---

### Finding 2: A dangerous stash exists (root cause #2)

```
stash@{0}: On pre-live: temp-clean-workspace
```

**Impact:** If any agent or process runs `git stash pop` or `git stash apply`, it will attempt to merge a "clean workspace" state on top of current edits. Depending on conflict resolution, this can:
- Silently revert changed files to the pre-live state
- Overwrite agent edits with the stashed version
- Leave the repo in a partial state with some files reverted

**Risk:** HIGH. The stash name "temp-clean-workspace" suggests it was created intentionally to wipe state. Any agent that sees this and "helpfully" applies it will undo the session's work.

---

### Finding 3: Concurrent agent writes to same files (root cause #3)

The session had multiple agents (isolation-architect, market-verifier, isolation-researcher) writing to overlapping files. The write pattern:

1. Agent A reads `harvester.py`
2. Agent B reads `harvester.py`
3. Agent A writes new version of `harvester.py`
4. Agent B writes its version of `harvester.py` (overwrites A's changes)

This is a classic TOCTOU (time-of-check to time-of-use) race. Both agents see the same pre-edit state, both produce their own new version, the last write wins and the other's changes are gone.

**Evidence:** isolation-architect reported "externally reverted" on `cycle_runner.py` — this is the fingerprint of a concurrent write overwriting an earlier edit.

---

### Finding 4: No pre-commit hook (RULED OUT)

`ls .git/hooks/` shows only `.sample` files. No active pre-commit hook exists. Hooks are not reverting changes.

---

### Finding 5: No auto-format hooks in settings.local.json (RULED OUT)

`.claude/settings.local.json` contains only permission rules:
```json
{"permissions": {"allow": ["Bash(claude:*)", ...]}}
```

No PostToolUse hooks, no formatters, no linters that could rewrite files.

---

### Finding 6: No daemon process overwriting files (RULED OUT)

The daemon runs from `state/` files (positions-paper.json, positions-live.json), not from `src/` code. A running daemon does not overwrite Python source files. Daemon is not the cause.

---

## Summary: Why Edits Get Lost

| Cause | Probability | Mechanism |
|-------|-------------|----------|
| Concurrent agent TOCTOU writes | **HIGH** | Two agents read-then-write same file; last write wins |
| `git checkout`/`restore` by an agent | **HIGH** | Unstaged changes disappear instantly |
| Stash pop/apply | **MEDIUM** | `stash@{0}` is a trap waiting to fire |
| Pre-commit hook | NONE | No active hooks |
| Auto-formatter hook | NONE | settings.local.json has no write hooks |
| Daemon overwrite | NONE | Daemon doesn't touch src/ |

---

## Antibodies (Structural Fixes)

### Antibody 1: COMMIT after every verified batch

**Rule:** After every batch of edits that passes tests, immediately commit:
```bash
git add -p   # review what's staged
git commit -m "feat: migrate get_connection() batch 1 -- src/ files"
```

Uncommitted edits are one `git checkout` away from being lost. Commits are durable.

### Antibody 2: Delete the dangerous stash

```bash
git stash drop stash@{0}  # drop "temp-clean-workspace"
```

Or inspect it first:
```bash
git stash show stash@{0}
```

This stash is a loaded gun. Any agent or human that applies it reverts the workspace.

### Antibody 3: File ownership protocol for multi-agent sessions

When multiple agents must edit the same files:
- Assign file ownership at session start ("harvester.py owned by isolation-architect")
- Other agents read but do not write owned files
- Or serialize: all agents on a file finish before the next agent starts

Or: use git worktrees per agent so concurrent edits happen in isolated branches.

### Antibody 4: Agents must never run destructive git commands without confirmation

Commands that destroy uncommitted work:
- `git checkout .` / `git restore .`
- `git reset --hard`
- `git stash` (without `--keep-index`)
- `git clean -f`

No agent should run these without explicit human approval.

---

## Immediate Actions Required

1. **Commit current state** — 50+ modified files are unsaved. One wrong git command loses everything.
2. **Drop the stash** — `git stash drop stash@{0}`
3. **Do not run any git reset/checkout/restore** until changes are committed
