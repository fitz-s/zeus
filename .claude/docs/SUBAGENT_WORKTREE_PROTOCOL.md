<!-- Created: 2026-06-12 | Last reused or audited: 2026-06-12
     Authority basis: operator directive 2026-06-12 (subagent worktree lifecycle
     redesign); /tmp/agent_report_worktree_lifecycle.md -->

# Subagent worktree lifecycle protocol

Standing brief for every worktree-isolated subagent in this repo.

## 1. Where you start
`worktree.baseRef` is set to `head` in `.claude/settings.json`. When you are
spawned with `isolation: "worktree"`, the harness branches your worktree from
the **session branch tip** (the orchestrator's current local HEAD) — NOT from
weeks-old `origin/main`. You are on current code. Do not `git fetch` + rebase
onto origin/main; that re-introduces the staleness this setting fixed.

## 2. While you work
- Work ONLY inside your own linked worktree (`.claude/worktrees/agent-*`).
- **Commit in your worktree branch.** Uncommitted work cannot be merged back.
  Commit per phase; the merge-back helper operates on committed history only.

## 3. You MUST NOT touch the main tree's git state
The live Zeus daemons run from the MAIN checkout (`/Users/leofitz/zeus`).
A `PreToolUse` guard (`maintree_git_state_guard`) **BLOCKS** these when the
effective repo dir is the main tree (including `git -C /Users/leofitz/zeus …`):
`git checkout`, `git switch`, `git branch -b/-B/-d/-D/-f/-m`, `git reset --hard`.
Never run them against the main tree. (Deliberate operator override:
`MAINTREE_GIT_BYPASS=1`.)

## 4. Merge back as your LAST step
From inside your worktree, run:

```
python3 scripts/agent_worktree_merge.py
```

It fast-forwards the session branch to your commits **only** when that is a
pure, conflict-free fast-forward and the main tree is clean — daemon-equivalent
to a normal commit landing. Outcomes:

- `MERGE_OK: … merged sha <sha>` — done. Report the sha.
- `MERGE_NOOP` — nothing to merge (already on session tip).
- `MERGE_PENDING: …` — session branch advanced (non-ff) or main tree busy;
  the helper prints the exact orchestrator command. Report MERGE_PENDING and
  that command; the orchestrator finishes the merge.
- `MERGE_REFUSED: …` — you were dirty, not in a worktree, or ran it from the
  main tree. Fix and retry.

## 5. Report = summary + merged sha only
The orchestrator never picks files out of your worktree. Your report is a short
summary plus the merged sha (or MERGE_PENDING + the deferred command). Do not
paste diffs or file contents.
