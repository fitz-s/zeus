# PR workflow + hook failure analysis (2026-05-09)

> **Historical record — superseded fix design.** The "Fix design" section below
> was the first-pass response to the PR #106 failure and was rejected by operator
> as still-blocker-shaped (procedural phases rather than first-principles reasoning).
> The canonical fix lives in `architecture/agent_pr_discipline_2026_05_09.md`,
> which encodes the four-principle framework the operator approved. This document
> is preserved as the failure record (root cause + observed sequence); do not use
> its Fix design section as implementation guidance.

Created: 2026-05-09
Authority basis: operator directive 2026-05-09 — "github 工作流程没有一个按照预期工作"
Trigger: PR #106 merged with 6 unaddressed Copilot/Codex review comments; agent
deferred those to a future bundled PR, violating the operator-of-record workflow
where review comments must be resolved IN the same PR before merge.

## Operator's expected workflow

```
gh pr create  →  bots fire (5-8 min)  →  agent processes ALL comments
                                          →  re-push fixes  →  bots re-fire
                                          →  loop until clean
                                          →  merge
```

Two implicit invariants:

1. **Cost-economics**: minimize PR-opens (one per ≥300 LOC of work, batched).
   Each open = paid fire of all auto-reviewers. PR #106 already encoded this
   in `pr_create_loc_accumulation`.

2. **Quality-economics**: extract maximum signal from each fire. Each fire
   already paid for; merging without addressing the review comments wastes
   the fire AND degrades the trust line ("agent merged but my comment is
   on a closed PR, did anyone read it?").

PR #106 redesigned only #1. #2 remained lax.

## What actually happened on PR #106

- Open: 2026-05-09 17:21:46Z
- CI green: 17:23:00Z (~75s after open)
- Bot comments lands (Copilot 4 + Codex 1 P2 + 1 review): around 17:25-17:29Z
- Agent merge attempt: 17:32:32Z (PR age 644s, post-600s gate)
- Merge: success — `state=MERGED mergedAt=2026-05-09T16:32:32Z`
- 6 comments now sit on a merged PR; agent files them as "next bundled PR"

## Where the merge-gate hook failed (`pre_merge_comment_check`)

Registry says it blocks on:
- `pr_age_seconds < 600` ✓ (gate was 644s — passed)
- `unresolved_codex_p0_or_p1_thread == true` ✓ (only P2 — not blocked)
- `changes_requested_review_not_dismissed == true` ✓ (no CHANGES_REQUESTED)

The implementation matches the registry. The DESIGN was wrong:

| Bot output | Severity tag | Was blocked? | Should it have been? |
|---|---|---|---|
| Copilot inline suggestion | (none) | no | **yes** — most actionable findings come this way |
| Copilot review summary | COMMENTED | no | yes if it contains substantive findings |
| Codex P0 | P0 | yes | yes |
| Codex P1 | P1 | yes | yes |
| Codex P2 | P2 | **no** | **yes** — P2 is "should fix" not "may dismiss" |
| Codex review summary | COMMENTED | no | yes |

The hook was scoped to the LOUDEST-only patterns (P0/P1, CHANGES_REQUESTED).
Everything quieter (P2, suggestions, COMMENTED-with-substance) leaked
through. Operator's expectation is that those quieter signals are still
the cost we paid for and must be processed.

## Where the workflow doctrine failed

There was no canonical doc telling agents:
- "Wait for bots before any merge attempt"
- "Address every Copilot suggestion + every Codex P-tag, or document why dismissed"
- "If the merge gate doesn't block but you have unaddressed comments — STOP"

The `feedback_accumulate_changes_before_pr_open.md` memory covered cost-economics
(don't open tiny PRs). No memory covered quality-economics (don't merge with
unresolved review comments). The agent (opus) read the cost memory, treated
it as the only constraint, and skipped past the missing quality constraint.

This is the same failure mode the previous PR #91 follow-up flagged: agents
that learn rules by stumbling over hooks treat the hook as the spec. Where
the hook is silent, the agent is silent. The doc + memory exist to fill
those silences.

## Root cause

Two-axis issue, captured separately:

### 1. Hook scope was too narrow

`pre_merge_comment_check` was designed when the bots tagged severities
clearly (Codex P0/P1 era). Copilot doesn't tag — every suggestion is
"COMMENTED" + body. Codex P2 was added later but the hook never expanded.
The gate's blocking-list became a sample of the bot output space, not the
full set.

### 2. Agent's mental model was hook-anchored, not principle-anchored

I (opus) treated "PR age >= 600 + no P0/P1 + no CHANGES_REQUESTED" as the
green-light condition. That's the hook's literal logic. It is NOT the
workflow's logic. The workflow's logic is "all bot signal extracted".
The hook is a partial encoder of the workflow; treating the encoder as
the spec is the same fallacy as treating tests as the spec.

This is captured in `feedback_redesign_self_discoverable.md` — if agents
need teaching about the new flow in chat, the redesign isn't done.

## Fix design (bundled with PR #106 follow-ups)

A separate sonnet (`ae0741a5`) is fixing the 6 PR #106 findings on a
non-pushed branch. Orchestrator (this session) adds the workflow fix on
top and pushes one PR when ≥300 LOC AND every comment cleared.

### Hook layer

`pre_merge_comment_check` upgraded to block on ANY unresolved review thread,
regardless of severity tag:

```yaml
blocked_when:
  - pr_age_seconds < 600
  - unresolved_review_thread_count > 0     # ANY bot, ANY severity
  - changes_requested_review_not_dismissed == true
```

Bypass `ZEUS_PR_MERGE_FORCE=1` retained for genuine operator override; bypass
must be paired with a documented per-comment dismissal in the PR body.

Implementation note: "unresolved review thread" = the GraphQL
`reviewThread.isResolved == false` field. The hook queries
`gh api graphql` with the PR's reviewThreads, filters bot authors, counts
non-resolved. Threads marked resolved (via `resolveReviewThread` mutation)
no longer count — that gives the agent an explicit close-loop action per
thread.

Educational message:

```
BLOCKED: gh pr merge declined — N unresolved bot review thread(s).

Each Copilot suggestion + Codex finding is signal you already paid for.
Merging now wastes the review fire AND closes the thread without telling
the bot whether its suggestion was applied / dismissed / disputed.

Per-thread resolution (preferred):
  for each unresolved thread:
    1. Read the comment.
    2. Apply the fix on this branch and push, OR
    3. Disagree → reply on the thread with the reason, then call
       `gh api graphql -f query='mutation{resolveReviewThread(input:{...})}'`
       to mark resolved.

Bypass: ZEUS_PR_MERGE_FORCE=1 (document each unresolved thread in PR body
with disposition: APPLIED in commit X / DISMISSED reason / DEFERRED to issue Y).

Reasoning failure mode this prevents:
  Bot fires (cost X). Bot finds 5 issues, files 5 threads. Agent merges
  before reading. Cost X paid for 0 issues addressed. The 5 issues either
  re-emerge in production or get filed as follow-up tasks that violate
  the bundle rule. Net: paid for review you didn't use.
```

### Memory layer

New entry: `feedback_pr_close_loop_with_bots_before_merge.md`

```
Rule: do not call `gh pr merge` until every bot review thread is resolved
(applied as a fix-commit OR dismissed via `resolveReviewThread` with reason
in the thread reply).

Why: each bot fire is paid; the value extracted is comments addressed,
not comments collected. Merging closes the PR and orphans unaddressed
threads — the cost is paid but the signal is dropped.

How to apply: after `gh pr create`, wait 5-8 min for bots; poll
`gh api repos/.../pulls/N/reviewThreads`; for each unresolved thread,
take action; only call `gh pr merge` when count drops to 0.
```

### Doc layer

`architecture/agent_pr_discipline_2026_05_09.md` extended with a
"Post-open workflow" section that codifies the close-loop expectation
and references the merge-gate hook.

## Verification

The bundled PR (orchestrator + sonnet `ae0741a5`) lands as ONE PR after
all six PR #106 findings are applied AND the new merge-gate hook is in
place AND its tests pass. Sequence:

1. Sonnet fixes 6 findings, commits, does NOT push.
2. Orchestrator adds hook redesign + tests + memory + doc updates on
   the same branch.
3. Combined LOC ≥ 300 (rule satisfied).
4. Push, open PR.
5. Bots fire.
6. **Apply the new workflow**: address every bot comment IN THE PR
   before any merge attempt — this PR is its own first beneficiary
   (same shape as PR #106 was for the cost-economics rule).
7. Merge once threads are clean.

If step 6 takes more iterations than expected, that is the rule
working as intended — not a defect.

## Open question for operator

Bypass policy: should `ZEUS_PR_MERGE_FORCE=1` be allowed at all, given
operator just argued the rule must teach not just enforce? Argument
for keeping bypass: emergencies (production breakage, security) where
the cost of waiting outweighs the cost of re-opening for follow-up.
Argument for removing bypass: every "emergency" rationalization is the
agent talking itself out of the rule. Recommend: keep bypass, require
a per-thread disposition note in PR body, log every bypass to a ledger
file the operator can review weekly.

Awaiting operator direction.
