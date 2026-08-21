# AI-assisted engineering: authority, boundaries, and failures

Coding agents (Claude Code, Codex, and other GPT-class agents) contribute
implementation, tests, audits, documentation, and review triage to this
repository. They do not own architecture, research assumptions, merge
acceptance, deployment, risk posture, or capital decisions.

Generated output is proposal material, never authority. Acceptance requires
repository law, tests, machine checks, independent review, and operator
approval. Interactive agents work in isolated workspaces; the unattended lane
is additionally restricted by operating-system write and network boundaries.
These controls reduce the blast radius of agent error. They do not prove that
generated work is correct.

## Authority and delegated work

Delegated: implementation (features, bug fixes, refactors), test authorship,
targeted audits (provenance, security, invariant compliance), documentation,
and first-pass review triage on pull requests.

Never delegated: architecture decisions, research assumptions (what a model
change is supposed to prove), acceptance of any change before it reaches
`live`, deployment (`scripts/deploy_live.py` is operator-invoked only), and
all capital-risk decisions — position-sizing law, kill-switch, risk posture.
`loop/prompts/l1.md` carries the explicit NEVER list enforced on the
autonomous loop.

## How generated work is checked

Agent work lands through an isolated worktree, never a direct edit to the main
checkout (`live_tree_write_guard`, BLOCKING, `.claude/hooks/registry.yaml`).
Money-path changes route through CI checks keyed to the diff's semantic class
(`architecture/money_path_ci.yaml`), invariant tests
(`architecture/invariants.yaml`), and adversarial multi-agent review for
larger or higher-risk work before a PR merges. None of this proves the work is
correct; it proves the work was checked by something other than the agent that
wrote it.

## Control failures

**The no-edge guard.** A `Stop`-event hook blocked the conclusion "no edge" /
"market efficient" via a phrase blocklist — directly contradicting this repo's
own rule that "insufficient evidence" is a legitimate, required conclusion. On
2026-07-28 it fired on a session's own summary of the plan to fix it, proving
the trigger was lexical, not structural. The guard was deleted, not patched,
and replaced with a non-blocking advisory. Corrected in public: PR #452,
merged 2026-07-29.

**The shared-ref boundary (open).** Linked worktrees isolate working files and
indexes, but they share the repository's ref namespace. A 2026-06-12 incident
showed worktree agents could reach the main checkout's branch state through
ordinary git verbs; that was closed with a blocking command guard — no
operator bypass for the commands it classifies (`maintree_git_state_guard`).
A July 2026 incident then showed the guard's
limit: it enumerates commands, and an agent reached the same state through a
ref-mutation verb the guard did not classify, momentarily retargeting the
branch used by the production checkout. The exact prior ref was restored and
verified, the daemons were unaffected, and the incident is retained as
evidence. This snapshot does not claim the shared-ref mutation class is
closed. Linked worktrees are therefore treated as workflow isolation, not as a
security boundary.

## Responsibility

Generated output is never authority. The operator owns research assumptions,
acceptance decisions, deployment, and capital risk.

<details>
<summary>Historical commit-trailer attribution note</summary>

An internal audit found `Co-Authored-By` trailers on roughly 22% of historical
commits. That is a commit-level indicator only: it counts commits carrying the
trailer, not lines, files, or decision weight — a one-line config fix and a
400-line refactor are the same "1 commit", and many commits mix human and
agent edits with no attribution boundary inside the diff. It also
under-measures current practice: the trailer convention was dropped by
operator instruction, so recent agent work is invisible in commit metadata and
shows up instead in the file tree, orchestrator run records, and PR history.
22% is a snapshot of an older convention, not a current usage rate.

</details>
