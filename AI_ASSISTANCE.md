# AI Assistance in Zeus

This repository is built with substantial coding-agent participation
(Claude Code, Codex, and other GPT-class agents) under a written change-control
regime. This page states what that participation measures, what it does not,
and where the controls around it have failed.

## The 22% figure

An internal repository audit found `Co-Authored-By` trailers on roughly 22%
of historical commits. Read that as a **commit-level indicator only**: it
counts commits carrying the trailer, not lines of code, files touched, or
decision weight. There is no defensible way to turn commit counts into a
line-share estimate — a one-line config fix and a 400-line refactor are the
same "1 commit," and many commits mix human and agent edits with no
attribution boundary inside the diff.

It also under-measures current practice: the trailer convention has since
been dropped by operator instruction (`type(scope): subject`, no AI
trailers) — of the last 500 commits on `live`, zero carry one. Recent
agent-authored work is invisible in commit metadata; it shows up in the file
tree, `.claude/orchestrator/runs/*/state/agent_registry.jsonl`, and PR
history instead. 22% is a snapshot of an older convention, not a current
usage rate.

## What gets delegated

Coding agents are used for implementation (features, bug fixes, refactors),
test authorship, targeted audits (provenance, security, invariant
compliance), documentation, and first-pass review triage on pull requests.

## What stays human

Architecture decisions, research assumptions (what a model change is
supposed to prove), acceptance of any change before it reaches `live`,
deployment (`scripts/deploy_live.py` is operator-invoked only), and all
capital-risk decisions (position sizing law, kill-switch, risk posture) are
never delegated. See `loop/prompts/l1.md:91` for the explicit NEVER list
enforced on the autonomous loop.

## How generated work is checked

Agent work lands through an isolated worktree, never a direct edit to the
main checkout (`live_tree_write_guard`, BLOCKING, `.claude/hooks/registry.yaml`).
Money-path changes route through CI checks keyed to the diff's semantic
class (`architecture/money_path_ci.yaml`), invariant tests
(`architecture/invariants.yaml`), and adversarial multi-agent review for
larger or higher-risk work before a PR merges. None of this proves the work
is correct; it proves the work was checked by something other than the
agent that wrote it.

## Two control failures

**The no-edge guard.** A `Stop`-event hook (`no_edge_rule1_guard`) blocked
the conclusion "no edge" / "market efficient" via a ~35-phrase blocklist —
directly contradicting this repo's own rule that "insufficient evidence" is
a legitimate, required conclusion. On 2026-07-28 it fired on a session's own
summary of the plan to fix it, proving the trigger was lexical, not
structural. The guard was deleted, not patched, and replaced with a
non-blocking advisory. Corrected in public: PR #452, merged 2026-07-29.

**The shared-ref incident(s).** Two incidents, over a month apart. On
2026-06-12, subagent worktree operations were found able to reach the main
checkout's own branch state via ordinary checkout/switch/branch/reset
commands run against the main tree's path from a worktree. Closed with a
BLOCKING, no-bypass guard (`maintree_git_state_guard`,
`.claude/hooks/registry.yaml`; `.claude/docs/SUBAGENT_WORKTREE_PROTOCOL.md`
§3) that denies those specific verbs against the main tree.

The guard has a gap: it enumerates commands, not the general hazard of ref
mutation. On 2026-07-29, during production of this showcase, an agent in an
isolated worktree ran `git update-ref refs/heads/live <sha>` to refresh what
it believed was a stale local ref — not realizing linked worktrees share the
repository's common ref store. `update-ref` isn't a checkout/switch/branch
verb, so the guard didn't see it; it momentarily pointed the MAIN tree's
checked-out branch — where the live daemons run — at a different commit.
The agent caught it immediately and restored the exact prior SHA
(`9c5d4b10d`); the main thread verified via `git reflog` that the
restoration was exact and the daemons were unaffected. The exposed gap is
not yet closed.

## Responsibility

Generated output is never authority. The operator owns research
assumptions, acceptance decisions, deployment, and capital risk.
