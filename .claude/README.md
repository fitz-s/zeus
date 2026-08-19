# .claude/ — Agent Governance

This directory configures how coding agents (Claude Code, and by extension
Codex and other GPT-class agents operating under the same repo law) may act
on this codebase. It is architecture — a set of enforced boundaries and
their evidence — not a set of prompting tricks. Nothing here anthropomorphizes
the agents or claims judgment on their behalf; every mechanism described is a
program that runs on a specific event and returns a specific decision.

Five things a cold reader needs, in order:

## 1. The human-authority rule

`docs/authority/zeus_change_control_constitution.md`, CONST-10: LLM output
is never authority; only spec, tests, machine checks, and runtime evidence
are. This governs every mechanism below — a hook, a gate, or an agent can
advise or block a specific action, but none of them can validate a change as
correct. Only tests, invariant checks, and the operator can do that.

## 2. AUTO / PREPARE / NEVER

The autonomous improvement loop's write permissions are tiered by surface,
defined in `loop/prompts/l1.md:74-101`:

- **AUTO** — `loop/`, `docs/`, `tests/`: written directly.
- **PREPARE** — any `src/**` change: written to a diff file plus a red test,
  never applied. The operator applies, reviews, and merges.
- **NEVER** — config, live databases, LaunchAgents, deploy, kelly/risk
  posture, kill-switch: proposal text only, in the journal or ledger.

## 3. The sandbox boundary

`loop/tick.sh` runs the loop's single tick inside a Seatbelt sandbox:
writable only `loop/`, `docs/`, `tests/`, and `$TMPDIR`; `src/`, `scripts/`,
`.git`, and the network are OS-denied, not merely convention. An allowlist
enforcer hard-reverts any tick whose diff exceeds 20 files or 600 lines. The
sandbox is the actual boundary; the AUTO/PREPARE/NEVER tiers above describe
intent within it.

## 4. Three incident-derived hooks

Every hook in `.claude/hooks/registry.yaml` documents the incident that
motivated it, not just its trigger condition. Three representative examples:

- **`maintree_git_state_guard`** (BLOCKING, no bypass) — added 2026-06-12
  after subagent worktree operations were found able to mutate the main
  checkout's own branch/HEAD state (linked worktrees share the repo's
  `refs/` namespace). Blocks checkout/switch/branch-create-delete-force-move/
  `reset --hard` against the main tree from any worktree.
- **`live_tree_write_guard`** (BLOCKING) — blocks any agent file write
  (Edit/Write/MultiEdit/NotebookEdit/apply_patch) whose target resolves
  inside the main checkout. Agents write in their own linked worktree and
  land through a merged PR or a verified `git cherry-pick`.
- **`cotenant_staging_guard`** — re-promoted from advisory to BLOCKING on
  2026-06-12 after commit `30ba237ef5` swept two test deletions staged by a
  concurrent sibling agent; broad `git add` (`-A`/`-u`/`git add .`) is
  blocked in the main worktree. Linked worktrees, which keep an isolated
  index, are exempt.

## 5. Two documented control failures

The governance in this directory has failed at least twice in ways worth
reading in full: the no-edge guard (a `Stop`-hook blocklist that suppressed
the legitimate conclusion "insufficient evidence," deleted rather than
patched — PR #452, merged 2026-07-29) and the shared-ref incident (on
2026-07-29, a worktree agent's `git update-ref refs/heads/live` momentarily
retargeted the main tree's checked-out branch, because linked worktrees
share the repo's ref store and `live_tree_write_guard`/`maintree_git_state_guard`
are organized around specific commands and working trees, not ref mutation
as a class; caught immediately, restored exactly, verified via reflog — a
gap `maintree_git_state_guard`, added 2026-06-12 for the narrower
checkout/switch/branch/reset case, did not close). Both are described with
citations in `AI_ASSISTANCE.md`.
