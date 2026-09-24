# Live-branch workflow (`live`)

Status: ACTIVE — established 2026-07-20; rewritten 2026-09-24 to one truth and task-scoped worktrees. `AGENTS.md` §5 is the binding summary.

## What `live` is

`origin/live` is the live branch: the exact tree the running Zeus engine trades from. The live checkout (`/Users/leofitz/zeus`) is a read-only mirror of it. A commit reaching `origin/live` is a commit the live daemons will act on after the next restart.

## The law

1. **One truth.** Only `origin/live` accepts commits. The live checkout moves by fast-forward to `origin/live` and nothing else; then the daemons restart (`scripts/deploy_live.py restart`, which refuses a checkout that differs from `origin/live`). Nobody edits, commits, amends, resets or switches the checkout.
2. **One task, one worktree.** A task creates its worktree from `origin/live`, works and proves there, lands, then removes the worktree and deletes the branch. Sub-agents share the parent's worktree unless they must edit in parallel; then the parent creates a child worktree, merges the child branch back, and removes it. No worktree is kept per role or across tasks.
3. **The push is the queue.** Landing is `git push origin HEAD:live`, fast-forward only. Two tasks that finish minutes apart cannot collide: the second push is rejected, so that task rebases onto the new tip, re-runs its proof, and pushes again. A milestone that deserves review goes through a PR into `live` instead; the merge on GitHub is the same fast-forward point.
4. **Landed has one meaning:** `git merge-base --is-ancestor <sha> origin/live`. Patch-equivalence, cherry-pick subjects and local-only commits do not count.
5. **Freshness and fail-closed gates are never weakened to land faster.** The alpha-clock and failure-isolation invariants in `docs/operations/current/GOAL.md` bind every money-path change.

## Task lifecycle

```
git fetch origin
git worktree add -b task/<id> .claude/worktrees/<id> origin/live
# work, commit, prove
git fetch origin && git rebase origin/live      # re-prove if the base moved
git push origin HEAD:live                       # rejected -> rebase, re-prove, retry
# live checkout: fast-forward to origin/live, then restart
git worktree remove .claude/worktrees/<id> && git branch -d task/<id>
```

A task ending without landing commits its work to the branch (push it if a PR rides on it) and still removes the worktree. The only reasons to keep a worktree are an open PR under active revision or a process running in it.

## Multi-agent repair

The main thread aligns the work-list (per item: `file:line` and a fix / refute / defer-with-rationale disposition), fans out over disjoint files at the lowest fitting model tier, and lands. Every agent verifies the defect first, makes the minimal change, ships a behavioral antibody that fails before and passes after, and proves zero new regressions by diffing failing-test names pre vs post. Two agents never own the same file.

## Branch hygiene

A branch whose tip is an ancestor of `origin/live` is deleted with its worktree. A branch that never landed is either pushed behind a PR or deleted; stale local branches are not an archive. Deleting the remote branch after a PR merge is part of landing.
