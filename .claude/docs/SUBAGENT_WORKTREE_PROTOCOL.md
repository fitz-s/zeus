<!-- Created: 2026-06-12 | Last reused or audited: 2026-09-24
     Authority basis: AGENTS.md §5; docs/operations/current/plans/live_branch_workflow_2026-07-20.md -->

# Sub-agent worktree protocol

1. **Default: no worktree of your own.** You work in the parent task's worktree on the files you were assigned. You get a child worktree only when the parent must run editors in parallel.
2. **Never touch the live checkout** (`/Users/leofitz/zeus`). A `PreToolUse` guard blocks git mutations there.
3. **Commit on your branch.** Uncommitted work is lost when the worktree is removed.
4. **Finish by handing back, not by landing.** Report your branch, the committed SHA, the proof you ran, and any residual risk. The parent merges your branch into the task branch and removes your child worktree. Only the task owner pushes to `origin/live`.
5. **Report = summary + SHA + evidence.** Do not paste diffs or file contents.
