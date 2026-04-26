# Git State Cleanup Packet

Created: 2026-04-26
Branch: `chore/git-state-cleanup-2026-04-26` (off `main` HEAD `5943f92`)
Worktree: `/Users/leofitz/.openclaw/workspace-venus/zeus`
Status: planning evidence; destructive operations NOT executed; awaiting operator approval per item.

## 0. Scope statement

Consolidate the loose ends in this clone: dormant worktrees, fully-merged
local branches, fully-merged remote branches, and the unpushed local `main`
HEAD. Active worktrees and open PRs are explicitly excluded.

## 1. Active worktrees — DO NOT TOUCH

| Worktree | Branch | HEAD (sampled at packet open) | Why active |
|---|---|---|---|
| `zeus-fix-plan-20260426` | `claude/zeus-full-data-midstream-fix-plan-2026-04-26` | `9890ab8` (P5-3 A3 integration test, ~2026-04-26) | Advanced ≥3 commits during the packet planning window; PR #19 fix-plan still in flight |
| `zeus-pr18-fix-plan-20260426` | `claude/pr18-execution-state-truth-fix-plan-2026-04-26` | `b5fffb6` (P1.S3 critic-followup closeout) | Advanced ≥3 commits; PR #18 fix-plan + venue_commands work still landing |
| `zeus-live-readiness-2026-04-26` | `claude/live-readiness-completion-2026-04-26` | `eb06b74` (G6 packet CLOSED — con-nyx APPROVE) + `dfd5fb0` (G10-scaffold) + `ccc1c6d` (B4 amendments) | Advanced ≥3 commits; live-readiness Wave 1 actively shipping |

Open copilot PRs (`#18`, `#19`) are operator-handled and out of scope.

## 2. Cleanup targets — converged

### 2.1 Worktrees fully synced with merged branches (3)

| Worktree path | Branch | Merge evidence | Working-tree dirt | Action |
|---|---|---|---|---|
| `zeus-packet-runtime` | `p2-packet-runtime` | PR #16 merged 2026-04-25 | clean (only `.code-review-graph/graph.db` autogen) | `git worktree remove zeus-packet-runtime` |
| `.claude/worktrees/amazing-swanson-8aef1d` | `claude/amazing-swanson-8aef1d` | 0 unmerged into `main`; only heartbeat commits, no source content | `.code-review-graph/graph.db` autogen | `git worktree remove .claude/worktrees/amazing-swanson-8aef1d` |
| `.claude/worktrees/happy-yonath-173c16` | `claude/happy-yonath-173c16` | 0 unmerged into `main`; heartbeat-only | `.code-review-graph/graph.db` autogen | `git worktree remove .claude/worktrees/happy-yonath-173c16` |

Worktree removal does NOT delete the branches; that is step 2.2.

### 2.2 Local branches with 0 unmerged commits into `main` (6)

Verified via `git rev-list main..<branch> --count == 0`:

| Branch | Closure path | Action |
|---|---|---|
| `midstream_remediation` | PR #17 merged 2026-04-26 → main absorbed | `git branch -d midstream_remediation` |
| `p2-packet-runtime` | PR #16 merged 2026-04-25 → main absorbed | `git branch -d p2-packet-runtime` (after worktree remove) |
| `post-audit-remediation-mainline` | PR #11 merged 2026-04-25 → main absorbed | `git branch -d post-audit-remediation-mainline` |
| `p2-p2-obs-v2-revision-history` | No unique commits vs main | `git branch -d p2-p2-obs-v2-revision-history` |
| `claude/amazing-swanson-8aef1d` | Heartbeat-only | `git branch -d claude/amazing-swanson-8aef1d` (after worktree remove) |
| `claude/happy-yonath-173c16` | Heartbeat-only | `git branch -d claude/happy-yonath-173c16` (after worktree remove) |

`git branch -d` (lowercase d) refuses unmerged work; safe by default.

### 2.3 Remote branches whose PRs are merged (5)

| Remote branch | PR | Action |
|---|---|---|
| `origin/copilot/complete-implementation-agent-tasks` | #14 merged 2026-04-25 | `git push origin --delete copilot/complete-implementation-agent-tasks` |
| `origin/copilot/understand-repo-and-phase-plans` | #13 merged 2026-04-25 | `git push origin --delete copilot/understand-repo-and-phase-plans` |
| `origin/copilot/deep-evaluation-implementation` | #15 merged 2026-04-25 | `git push origin --delete copilot/deep-evaluation-implementation` |
| `origin/p2-packet-runtime` | #16 merged 2026-04-25 | `git push origin --delete p2-packet-runtime` |
| `origin/midstream_remediation` | #17 merged 2026-04-26 | `git push origin --delete midstream_remediation` |

Open copilot branches (`origin/copilot/full-review-of-upstream-data-storage` PR #19,
`origin/copilot/task-model-implementation-plan` PR #18) are NOT in scope.

### 2.4 Local-only branch with unmerged commits (1)

`pr18-head` carries 3 commits not present on `main`:
```
76a2f42 Fix execution-state package receipt evidence
687ae4a Add receipt for execution-state truth package
59dc265 Refresh execution-state truth operations package
```

These three commits ARE present on `origin/copilot/task-model-implementation-plan`
(PR #18 head). `pr18-head` is a local convenience copy created during PR #18
review.

**Decision required**:
- Option A: keep `pr18-head` until PR #18 is closed (preserves local review state).
- Option B: delete now (remote copy under `copilot/task-model-implementation-plan`
  is canonical).

Recommendation: Option A (defer until PR #18 disposition is known).

### 2.5 Unpushed `main`

Local `main` is ahead `origin/main` by 6 commits — all from this session:

```
5943f92 Drop stale archived-packet rows from operations registry
50f8b27 Route .agents/ from workspace_map directory router
626879c Split known_gaps.md: active surface + antibody archive
f432a9e Remove redundant .github/copilot-instructions.md
6f8c74a Add cloud session operating notes + active worktree table
fd57370 Open live-readiness-completion packet + archive 5 closed workbooks
```

(Note: `fd57370` predates this session; was already on local `main` at session start.)

Action: `git push origin main`. Requires operator authorization (push to default branch).

## 3. Cleanup execution order (after operator approval)

1. **Worktrees first** — `git worktree remove` for the 3 in §2.1. This unbinds
   the branches so step 2 can delete them.
2. **Local branches** — `git branch -d` for the 6 in §2.2.
3. **Remote branches** — `git push origin --delete` for the 5 in §2.3. After
   `git fetch --prune` the local stale tracking refs disappear automatically.
4. **`main` push** — `git push origin main`.
5. **Optional** — `git remote prune origin` if any tracking refs linger.

This packet does NOT execute any of the above. Each step is operator-gated
because:
- Worktree removal is irreversible without re-creation.
- Remote branch deletion affects shared state.
- `git push origin main` to the default branch needs explicit consent.

## 4. Verification gates

After each step:
- `git worktree list` should show only the 4 remaining (this clone + 3 active).
- `git branch -a` should show only `main`, the 3 active claude/* branches, the
  current cleanup branch, and `pr18-head` (if Option A).
- `git status -sb` clean except autogen `.code-review-graph/graph.db`.
- `gh pr list --state open` still shows #18 and #19.

## 5. Rollback posture

- Worktree removal: re-add via `git worktree add <path> <branch>`.
- Local branch delete (`-d`): branch tip preserved in reflog for ~30 days; restore
  via `git branch <name> <sha>`.
- Remote branch delete: only recoverable from local cache or a contributor's
  copy. Verified that all 5 remote-deletion targets correspond to merged PRs;
  the merge commit on `main` is the durable record.
- `main` push: reversible only by force-push (avoid).

## 6. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Active worktree branch silently absorbed by mistake | Low | §1 list + explicit branch names in commands |
| Heartbeat worktree had legit user content I missed | Very low | `git diff main...claude/amazing-swanson-8aef1d` returns only heartbeat patterns |
| Remote branch was the only home of unmerged work | Low | All 5 are tied to merged PRs (verified `gh pr list --state merged`) |
| Cleanup branch itself becomes orphan | Low | This branch is `chore/git-state-cleanup-2026-04-26`; merge into main when packet closes |

## 7. Out-of-scope

- Active claude/* branches (§1).
- Open copilot PRs #18 and #19 (operator-handled).
- `.code-review-graph/graph.db` runtime artifact handling.
- The new untracked `docs/operations/task_2026-04-26_polymarket_clob_v2_migration/`
  directory that appeared mid-session (likely another worktree's output;
  not this packet's concern).
- Any source/state mutation.
