# Agent PR discipline — auto-reviewer cost economics

Created: 2026-05-09
Authority basis: operator directive 2026-05-09 (post PR #105)
Hook: `.claude/hooks/dispatch.py::_run_advisory_check_pr_create_loc_accumulation`
Registry: `.claude/hooks/registry.yaml::pr_create_loc_accumulation`

## TL;DR

Do not open a PR with fewer than **300 self-authored LOC** of accumulated change.
The number is calibrated against the cost curve of the paid auto-reviewers
that fire on every `gh pr create` and every `git push` to an open PR.
The hook enforces this; this document explains the reasoning so future
agents comply by understanding, not by rote.

## Why each PR-open is expensive

Three reviewers run automatically on each PR event:

| Reviewer | Trigger | Tier |
|---|---|---|
| Copilot | `gh pr create`, every push to PR | senior |
| Codex | `gh pr create`, every push to PR | senior |
| ultrareview | manual (`/ultrareview`) | very senior |

The first two are subscription-paid per-fire regardless of diff size.
A 50-line PR pays the same per-event cost as a 1000-line PR. Bundling
related work into one PR is therefore strictly dominant: ten 50-line
PRs cost roughly 10× one 500-line PR for the same total review work
delivered.

## Why senior tier on small diff is waste

The reviewers run on a senior model tier. The marginal value of a
senior pass on a 50-line diff is small — most 50-line diffs contain
at most one or two non-trivial decisions, both of which a junior
model could surface. Spending senior cognition on small diffs has
the same waste shape as spending opus on file-location grep work
(captured in `~/.claude/projects/.../memory/feedback_orchestrator_offload_lookups.md`).
High-tier capacity is fungible across tasks; using it where lower-tier
capacity would do the same work means somewhere else lower-tier work
is starving. The cost is implicit but real.

## Why 300 LOC, not 50 or 1000

- Below 300 LOC the marginal benefit of a senior reviewer pass on the
  diff (cost: paid per fire) is consistently dominated by the marginal
  benefit of waiting and bundling.
- Above 300 LOC the diff has enough independent surfaces (multiple files,
  cross-cutting touch points, distinct subsystems) that the reviewer's
  pattern-recognition adds real signal that a junior model would miss.
- Calibrated against observed PR sizes that produced useful auto-reviewer
  comments in the 2026-04 / 2026-05 cohort: PRs ≥ 300 LOC averaged
  ≥ 2 actionable findings per fire; PRs < 300 LOC averaged < 0.3.

The number is empirical, not arbitrary. The integer threshold is held in
`.claude/hooks/registry.yaml` as the live source of truth for the hook
runtime; this document is the canonical source for the reasoning and
decision tree (and references that integer, but does not restate it as
a separate authority).

## Decision tree for a candidate PR open

When the agent finds itself wanting to call `gh pr create`:

### Case A — there is more related work to do on this initiative
Continue committing on the same branch. Do not open the PR yet.
When self-authored LOC reaches 300, open one PR with a multi-section
description listing each commit's purpose. Reviewers see the full
diff once and amortize their cost across all sections.

### Case B — this is a one-off and seems isolated
First, audit the assumption. Most "isolated" fixes have neighbors:
- the same module's other latent bugs
- the test file's other gaps
- the doc that should also update
- a pattern that recurs in adjacent files

Spend ~5 minutes scanning before declaring isolation. If genuinely
isolated AND the fix is urgent (production breakage, security,
operator deadline), set `ZEUS_PR_ALLOW_TINY=1` and **document the
bypass justification in the PR body**. The bypass is not a code
smell — opening a small PR without justification is.

### Case C — multiple PRs queued in this session
STOP. Combine. Each PR-open is a fresh fire of all auto-reviewers.
Two solutions:
1. One bundled PR with a multi-section description.
2. Stacked branches with one PR at the tip (the reviewers see a
   single combined diff against `main`, not N separate diffs).

### Case D — operator told the agent to ship this NOW
Use the bypass. Cite the operator directive in the PR body so
reviewers (human + bot) understand why the rule was suspended.
The bypass is for cases where the cost is consciously accepted, not
for cases where the agent forgot the rule.

## Author detection — why the LOC count subtracts carry-over commits

When an agent branches off `main` while operator has unpushed local
commits on `main`, those commits ride along into the agent's branch
and get counted in the PR diff. They are not the agent's contribution
and should not count toward the 300 LOC threshold.

Heuristic the hook uses:
- a commit's **trailer block** (last paragraph of the message body, per
  git-interpret-trailers convention) contains a line starting with
  `Co-Authored-By:` that also includes `Claude` → agent contribution
- otherwise → carry-over (operator's local work the branch picked up)

The match is restricted to the trailer block to avoid misclassifying
operator commits that quote the trailer in discussion text.

The hook reports both `total LOC since base` (what reviewers see) and
`self-authored LOC` (what counts toward the threshold). Block decision
uses `self-authored LOC < 300`.

If the agent is genuinely opening a small PR with mostly carry-over
work, that is a signal the branch is poorly scoped — start a fresh
branch from `origin/main` and re-cherry-pick the agent commits onto it.

## Bypass

`ZEUS_PR_ALLOW_TINY=1` degrades the block to advisory-only. The check
still runs and emits the same accumulation numbers; it just lets the
PR open. The bypass should always be paired with a documented reason
in the PR body — otherwise the next critic will flag it.

## What the rule does NOT cover

- Non-PR shipping paths: direct commits to a personal worktree, branch
  pushes that are not PR-opened, force-pushes after rebase. The cost
  driver is the `gh pr create` / `gh pr ready` event specifically.
- Rebases that change LOC count post-open. The rule fires once at PR
  open; subsequent pushes are subject to the merge gate's separate
  age + comment-resolution checks.
- Documentation-only PRs that operators want to ship in isolation. If
  this is a recurring pattern, raise it with operator and update this
  doc; do not bypass per-PR without documenting.

## Reasoning failure mode this rule prevents

> Agent fixes a 50-LOC bug → opens PR #1 → bots fire (cost X).
> Agent notices a related 50-LOC issue 5 minutes later → opens PR #2 → bots fire (cost X).
> Agent notices a third related issue → opens PR #3 → bots fire (cost X).
> ... × N.
>
> Total cost: N · X.
> Same N · 50 LOC bundled into one PR: ~X.
>
> The agent that opens N PRs in a session is not "being responsive" —
> it's burning N − 1 reviewer fires that produced no marginal signal.

The hook's job is to make this failure mode visible at the moment
of the bad call, with enough reasoning attached that the agent
re-evaluates rather than just reads-the-error-and-bypasses.

## Updating this document

If the cost economics change (e.g. bot pricing model changes, threshold
calibration shifts), update this file AND `.claude/hooks/registry.yaml`
AND the hook source together.
This doc is canonical for reasoning and decision tree.
`.claude/hooks/registry.yaml` is canonical for the integer threshold
value (the hook reads it at runtime).
Both must stay consistent: a drift between them is a bug.
The operator session memory entry `feedback_pr_300_loc_threshold_with_education.md`
(not a repo file) should also be updated so future agents reading memory
before hitting the hook see current values.
