# Live-branch workflow (main = live)

Status: ACTIVE — established 2026-07-20. Promote the binding clauses into `AGENTS.md` §5 (Change control) at the operator's discretion; until then this doc is the workflow of record.

## What `main` is now

`main` is the **live** branch: the exact tree the running Zeus engine trades from continuously. It is not a staging or integration branch. A commit reaching `main` is a commit the live daemons will act on.

## The law

1. **Never commit to `main` directly, and never mutate the main checkout's git state.** The live daemons run from `/Users/leofitz/zeus` on the live branch; switching or force-moving that checkout out from under them is the 2026-06-12 hijack incident. The `maintree_git_state_guard` enforces this — deliberate operator moves prefix `MAINTREE_GIT_BYPASS=1`.
2. **All work happens in a worktree.** Branch a linked worktree (`.claude/worktrees/agent-*` or `git worktree add`) off live, make the change there, and prove it there.
3. **Landing on live is cherry-pick or PR only.**
   - Small, isolated, reviewed change → `git cherry-pick` onto live (or `scripts/agent_worktree_merge.py`).
   - Anything larger, or anything that wants review → open a **PR into `main`** and merge after review.
   - Nothing reaches live without passing review. Opening a PR fires paid auto-reviewers; bundle related work into one PR (≥300 self-authored LOC) per `architecture/agent_pr_discipline_2026_05_09.md`.
4. **Freshness and fail-closed gates are never weakened to land faster.** The alpha-clock and failure-isolation invariants in `docs/operations/current/GOAL.md` bind every change that touches the money path.

## Branch hygiene

A branch whose commits are ancestors of live (fully absorbed) is deletable. A branch with commits **not** yet on live is kept — deleting it loses that work. Never delete a branch that backs an open PR. Remote pruning of absorbed branches:

```
git push origin --delete <absorbed-branch>   # only if merged into live and no open PR
```
