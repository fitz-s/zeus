# Live-branch workflow (`live`)

Status: ACTIVE — established 2026-07-20. Promote the binding clauses into `AGENTS.md` §5 (Change control) at the operator's discretion; until then this doc is the workflow of record.

## What `live` is now

`live` is the **live** branch: the exact tree the running Zeus engine trades from continuously. It is not a staging or integration branch. A commit reaching `live` is a commit the live daemons will act on.

## The law

1. **`live` accepts commits by exactly two lanes — hot-fix `git cherry-pick` or merged PR — and no third lane. A direct commit, amend, or in-place edit to the live checkout is forbidden.** The live daemons run from `/Users/leofitz/zeus` on the live branch; directly committing to it or force-moving that checkout out from under them is the 2026-06-12 hijack incident. `maintree_git_state_guard` enforces the no-git-state-mutation half — deliberate operator moves prefix `MAINTREE_GIT_BYPASS=1`.
2. **All work happens in a worktree.** Branch a linked worktree (`.claude/worktrees/agent-*` or `git worktree add`) off live, make the change there, and prove it there.
3. **Landing on live is cherry-pick or PR only.**
   - Small, isolated, reviewed change → `git cherry-pick` onto live (or `scripts/agent_worktree_merge.py`).
   - Anything larger, or anything that wants review → open a **PR into `live`** and merge after review.
   - Nothing reaches live without passing review. Opening a PR fires paid auto-reviewers; bundle related work into one PR (≥300 self-authored LOC) per `architecture/agent_pr_discipline_2026_05_09.md`.
4. **Freshness and fail-closed gates are never weakened to land faster.** The alpha-clock and failure-isolation invariants in `docs/operations/current/GOAL.md` bind every change that touches the money path.

## Branch hygiene

A branch whose commits are ancestors of live (fully absorbed) is deletable. A branch with commits **not** yet on live is kept — deleting it loses that work. Never delete a branch that backs an open PR. Remote pruning of absorbed branches:

```
git push origin --delete <absorbed-branch>   # only if merged into live and no open PR
```

## Multi-agent live-repair protocol

`live` runs 24/7 as a mesh of scheduled jobs; a mechanism (the improvement loop, a failing gate, a code review, a monitor alert, or an operator ask) wakes work. Many agents may repair concurrently. The **main thread is the integrator and landing authority** — it holds full context and owns alignment, integration, final verification, and the landing decision; agents own bounded slices and prove them in isolation.

1. **Wake → align.** Reconcile the trigger into a precise work-list: per item, the exact `file:line` and the disposition to prove — *fix* / *refute* / *defer-with-rationale*. Alignment precedes fan-out; a vague brief buys well-argued irrelevance.

2. **Fan out — one owner per file/slice.** Dispatch worktree-isolated agents over **disjoint** files, at the lowest model tier that fits the slice (reserve the top tier for outcome-deciding money-path logic). Every agent, without exception:
   - **verifies the defect is real first** — locate by symbol, not the reported line (review line numbers drift); refute false positives with evidence rather than fabricating a fix;
   - makes the **minimal** correct change — no architecture rewrite for a case the runtime already covers (Occam; the scheduler's separate pools + separate processes already isolate across jobs);
   - ships a **behavioral antibody** that fails on the pre-fix tree and passes after;
   - proves **zero new regressions** by diffing the failing-test-name set pre-vs-post, not by trusting a count.

3. **Adversarially disposition the rest.** Before anything lands, read-only investigators verify every remaining or uncertain finding to a verdict — SATISFIED / MITIGATED / REFUTED / DEFERRED-with-rationale — each backed by `file:line`. Completeness without over-fixing; a deferred item carries the reasoning for *why the obvious fix doesn't hold here*, written down.

4. **Integrate by disjoint cherry-pick.** Cherry-pick each agent's commit onto the **current** live tip in one integration branch (disjoint files → clean). Prove the composite adds zero failures with a **base-vs-integrated failing-set diff**, not by trust. Verify antibodies pass on the integrated branch together.

5. **Land by lane; never clobber live-ops.** Hot-fix (a live money-path defect) → `cherry-pick` onto live + reload. PR (functional/milestone) → gates + review. The live branch moves under you as other agents/operators commit — rebase onto its current tip before landing; a **dirty live checkout is a coordination point, never a force** (preserve unrelated uncommitted work; overlapping governance files merge, they do not overwrite).

**Balance point:** the main thread integrates and verifies; agents prove bounded slices in isolation; land small and often. Two agents never own the same file; parallel editors are worktree-isolated; the low tiers enumerate and build, the top tier only untangles the genuinely complex.
