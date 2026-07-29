<!-- Created: 2026-06-12 | Last reused or audited: 2026-07-28
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
Never run them against the main tree; agents have no bypass.

## 4. Hand off the committed SHA as your LAST step
Do not run a merge-back helper and do not change the live checkout. Report the
committed SHA, branch, focused verification, and any residual risk to the
landing authority. It independently verifies the current `live` tip and lands
the work through exactly one lane: a reviewed hot-pick (`git cherry-pick`) or a
merged PR into `live`.

For a Codex-managed worktree, its completed clean owner archives its own thread
only after landing verification and any PR monitoring obligation end; Codex then
snapshots and reclaims that worktree. No worker raw-removes a Codex path.

## 5. Report = summary + committed SHA only
The landing authority never picks files out of your worktree. Your report is a
short summary plus the committed SHA and evidence. Do not paste diffs or file
contents.
