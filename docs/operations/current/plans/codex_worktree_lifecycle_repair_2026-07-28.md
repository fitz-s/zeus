# Codex Worktree Lifecycle Repair

Status: EXECUTING

## Problem

Codex-managed worktrees accumulated under `$CODEX_HOME/worktrees` despite a
small configured retention count. The project advertised `WorktreeCreate` and
`WorktreeRemove` hooks, but those are not supported Codex hook events; their
advisories never owned the app-managed lifecycle. A repository reaper would be
the wrong repair because it cannot preserve Codex's chat snapshot and may
remove a worktree that Codex still associates with a live chat.

## Decision

Codex owns removal of Codex-managed worktrees. The lifecycle is:

1. Each writing agent holds one disposable, exclusive role worktree for one
   task (`data`, `strategy`, `execution`, `governance`, or the declared
   hot-fix slice). The role is a lease, not a permanent directory or branch
   pool; do not pre-create idle worktrees. At most two code-writing leases may
   exist concurrently.
2. Commit and land changes only through the existing PR or hot-pick lanes.
3. Once the landing is verified and no PR monitoring remains, the worker archives
   its own Codex thread. Codex snapshots the worktree before removing it.
4. Branches and open-PR work remain intact; no hook or cleanup job deletes them.

No lifecycle hook may write an untracked sentinel into a new worktree. Such a
file makes a completed tree dirty, prevents safe closeout, and defeats the
retention policy it claims to support. Role and task identity belong in the
agent/task branch context, not a worktree-local artifact.

The host retention cap is reduced from eight to two completed managed worktrees.
Active, pinned, and permanent worktrees stay protected by Codex.

## Implementation

- Keep `WorktreeCreate` and `WorktreeRemove` protocol-safe and advisory-only;
  raw `git worktree add` in the Claude/OMX Bash path is rejected before it can
  create a Zeus native tree outside the host-managed lifecycle.
- Make the existing manual hygiene/post-merge advisor compare against `live`,
  fail closed when PR status is unavailable, and give the same snapshot-aware
  closeout advice for Codex-managed paths.
- Retire both legacy helpers that could fast-forward a live checkout or force
  remove a worktree. They now fail closed and instruct the worker to hand off
  its committed SHA for the hot-pick/PR lanes.
- Treat no-`+` output from `git cherry live <branch>` as patch-equivalence
  evidence after a hot-pick; do not require the original commit to be an
  ancestor of `live`.
- Add the Codex-specific closeout rule to the global operator contract and the
  Zeus live-branch workflow. It explicitly prohibits raw `git worktree remove`
  for `$CODEX_HOME/worktrees`.
- Add a regression test that rejects reintroducing those unsupported hook events.
- Remove automatic worktree-local sentinel writes and regression-test that the
  advisor uses `live`, never `origin/main`, as its branch baseline.
- Scope the required single-live-semantics PR gate to files changed from the
  GitHub base SHA. SCOPE is the PR/push delta; DRAIN is a separate full-tree
  audit/remediation of base violations; RESET is the next base-relative change.
  Missing base evidence fails closed rather than falling back to an unscoped
  scan that blocks unrelated work.

## Acceptance

- A completed Codex worker can archive itself without changing the `live`
  checkout; its branch and any open PR remain available.
- A new Codex-managed worktree cannot cause more than two completed,
  unpinned, non-permanent worktrees to be retained by the app.
- An agent Bash command cannot create a Zeus worktree with raw `git worktree
  add`, while inspection, native closeout, and other repositories remain usable.
- A pre-existing single-live violation cannot block an unrelated PR, while a
  newly changed file containing that violation remains a required-check failure.
- Active, pinned, permanent, dirty, and open-PR work are never deleted by this
  repair.
- The project hook file parses as JSON and contains no unsupported worktree
  lifecycle event.

## Verification and rollback

Run the focused hook-config test, JSON parse, and planning/changed-surface
checks from this worktree. Roll back with one commit reverting the project
policy/config changes; the global keep-count can be restored to eight without
touching `live` or any worktree.
