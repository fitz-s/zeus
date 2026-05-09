# Agent PR discipline — four first-principles of workflow quality

Created: 2026-05-09
Last extended: 2026-05-09
Authority basis: operator directive 2026-05-09 (post PR #105/#106 — "first-principles, not blockers; teach not enforce")
Hooks (backstops only): `.claude/hooks/dispatch.py::_run_advisory_check_pr_create_loc_accumulation`,
  `.claude/hooks/dispatch.py::_run_advisory_check_pre_merge_comment_check`
Registry: `.claude/hooks/registry.yaml` (hook metadata and bypass envs)

## TL;DR

This document is the single canonical reasoning source for the full PR
workflow — from when to open a PR to when to merge. Four first-principles
drive every decision. The hooks described at the bottom are backstops for
when an agent skips a principle; they are not the spec.

**Principle 1** — Ship coherent units, not LOC counts. 300 LOC is a sanity
check, not the target.

**Principle 2** — Bot comments are bug reports; the fix-commit IS the
response. No thread replies (one-line DEFER to a tracked issue is the only
exception).

**Principle 3** — Original-executor continuity. Whoever wrote the commits
handles the review loop. Dispatching a stranger wastes context.

**Principle 4** — Teach the reasoning, not the rule. Rules without reasons
drift; this document ensures every hook message carries the "why" so agents
re-derive under stress instead of bypassing.

---

## Principle 1 — Coherent unit of work, not LOC count

What "coherent" means in practice: a reviewer reading the PR body should be
able to answer "what changed?" in one sentence without listing files. If the
answer requires "and also" three times, the unit is two or three PRs
concatenated.

A 60-LOC unit that is genuinely coherent and isolated should be bundled with
adjacent work until it is no longer 60 LOC. A 1500-LOC unit that is
genuinely one unit ships as one PR even though it is large. Padding a PR to
reach 300 LOC is the opposite of this principle.

### Why each PR-open is expensive

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

### Why senior tier on small diff is waste

The reviewers run on a senior model tier. The marginal value of a
senior pass on a 50-line diff is small — most 50-line diffs contain
at most one or two non-trivial decisions, both of which a junior
model could surface. Spending senior cognition on small diffs has
the same waste shape as spending opus on file-location grep work
(captured in `~/.claude/projects/.../memory/feedback_orchestrator_offload_lookups.md`).
High-tier capacity is fungible across tasks; using it where lower-tier
capacity would do the same work means somewhere else lower-tier work
is starving. The cost is implicit but real.

### Why 300 LOC, not 50 or 1000

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

### Decision tree for a candidate PR open (Principle 1)

When the agent finds itself wanting to call `gh pr create`:

#### Case A — there is more related work to do on this initiative
Continue committing on the same branch. Do not open the PR yet.
When self-authored LOC reaches 300, open one PR with a multi-section
description listing each commit's purpose. Reviewers see the full
diff once and amortize their cost across all sections.

#### Case B — this is a one-off and seems isolated
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

#### Case C — multiple PRs queued in this session
STOP. Combine. Each PR-open is a fresh fire of all auto-reviewers.
Two solutions:
1. One bundled PR with a multi-section description.
2. Stacked branches with one PR at the tip (the reviewers see a
   single combined diff against `main`, not N separate diffs).

#### Case D — operator told the agent to ship this NOW
Use the bypass. Cite the operator directive in the PR body so
reviewers (human + bot) understand why the rule was suspended.
The bypass is for cases where the cost is consciously accepted, not
for cases where the agent forgot the rule.

### Author detection — why the LOC count subtracts carry-over commits

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

### Bypass (Principle 1 gate)

`ZEUS_PR_ALLOW_TINY=1` degrades the block to advisory-only. The check
still runs and emits the same accumulation numbers; it just lets the
PR open. The bypass should always be paired with a documented reason
in the PR body — otherwise the next critic will flag it.

### What Principle 1 does NOT cover

- Non-PR shipping paths: direct commits to a personal worktree, branch
  pushes that are not PR-opened, force-pushes after rebase. The cost
  driver is the `gh pr create` / `gh pr ready` event specifically.
- Rebases that change LOC count post-open. The rule fires once at PR
  open; subsequent pushes are subject to the merge gate's separate
  age + comment-resolution checks.
- Documentation-only PRs that operators want to ship in isolation. If
  this is a recurring pattern, raise it with operator and update this
  doc; do not bypass per-PR without documenting.

### Reasoning failure mode Principle 1 prevents

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

### Updating Principle 1 calibration

If the cost economics change (e.g. bot pricing model changes, threshold
calibration shifts), update this file AND `.claude/hooks/registry.yaml`
AND the hook source together.
This doc is canonical for reasoning and decision tree.
`.claude/hooks/registry.yaml` is canonical for the integer threshold
value (the hook reads it at runtime).
Both must stay consistent: a drift between them is a bug.
The operator session memory entries `feedback_pr_300_loc_threshold_with_education.md`
and `feedback_pr_unit_of_work_not_loc.md` (not repo files) should also be
updated so future agents reading memory before hitting the hook see current values.

---

## Principle 2 — Bot comments are bug reports; the fix-commit IS the response

Every Copilot suggestion, Codex finding, and human review comment is a
candidate bug. The agent's job is bug-extraction triage followed by fix
application. The reply mechanism on review threads is for humans; agents
do not reply.

**The commit that applies the fix IS the response.** Push the commit,
resolve the thread via `gh api graphql -f query='mutation{resolveReviewThread(input:{threadId:"..."}){thread{isResolved}}}'`,
move on. No quoted-reply, no agent-explanation, no "thanks for catching
this" politeness theater.

### No-thread-reply rule (hard constraint)

Agents do NOT post reply text on bot review threads. Reasons:

1. The bot doesn't read the reply.
2. The human reading the resolved thread sees the fix-commit by clicking
   through; the reply adds zero information.
3. Every token spent composing a reply is a token not spent fixing the bug.

**The only exceptions:**
- **DEFER**: one line (`"Tracked in #ABC"`) then resolve. Never write a paragraph.
- Operator-directed reply: operator explicitly asked the agent to clarify a
  specific point publicly. One line, then resolve.

Replies that quote the bot's body, paraphrase the fix, or thank the bot
are pure waste.

### Bug-extraction taxonomy

For each unresolved thread, classify before acting:

| Class | Action |
|---|---|
| **BUG** | Apply the fix; commit; resolve the thread |
| **STYLE_NIT** | Apply if cheap; resolve without commit if dismissed |
| **MISUNDERSTANDING** | Resolve with no commit; the absence of a fix IS the dismissal |
| **NOISE** | Resolve; no commit |
| **OUT_OF_SCOPE** | One-line reply citing tracked follow-up issue; resolve |

### Post-open workflow (derived from Principle 2)

1. After `gh pr create`, wait 8 min minimum before inspecting review state.
   Bots fire within 5–8 min after PR-open or each push. Peeking before 8 min
   risks treating partial state as final.
2. List all unresolved threads: `gh api repos/<owner>/<repo>/pulls/N/reviewThreads`
   (or via GraphQL `reviewThreads(first:100) { nodes { isResolved ... } }`).
3. Classify each thread (BUG/STYLE_NIT/MISUNDERSTANDING/NOISE/OUT_OF_SCOPE).
4. Apply fixes; push. Bots re-fire. Repeat until unresolved thread count = 0.
5. Merge.

The `pre_merge_comment_check` hook is the backstop for when this workflow
is skipped. If the hook fires because there are unresolved threads, the
correct interpretation is "I tried to merge before extracting all signal —
go back and process threads", NOT "find which env var bypasses this gate".

### Anchor case: PR #106

PR #106 merged with 6 unaddressed Copilot/Codex comments. The bot fires
were paid; the signal was dropped. The comments orphaned on a closed PR.
This is the failure mode Principle 2 makes impossible when applied.

---

## Principle 3 — Original-executor continuity

Whoever wrote a PR's commits handles its review comments end-to-end.
Dispatching a fresh agent to fix comments on a PR they didn't write is a
context-load anti-pattern: the new agent must re-read the entire PR diff,
re-load the design intent, re-derive the constraints — all to produce
one-line fixes. The original author already has all of that loaded.

### Orchestrator dispatch rule

When a worker writes a PR's commits, that worker (or its direct continuation
thread) handles the review-comment loop.

**If the original worker has terminated:**
- Comments are **mechanical** (typo, lint, schema-version int/str, phantom
  test ID — see `feedback_frontload_predictable_remediation.md`) → the
  orchestrator (opus) handles them inline; faster than re-dispatching.
- Comments are **substantive** (logic / design / new test) → re-dispatch to
  the same tier as the original author with a comment-list-only brief; the
  brief assumes the worker has PR design context fresh because it IS the
  original author resumed, not a stranger reading a 800-line diff cold.

### Anchor case: PR #106 → ae0741a5

Orchestrator dispatched a fresh sonnet (`ae0741a5`) to fix six PR #106
comments that opus could have inlined. The sonnet had to re-read the full
diff to understand the context it was dropped into. Principle 3 prevents this:
mechanical comments → opus inline; substantive → original worker resumed.

---

## Principle 4 — Teach the reasoning, not the rule

Rules without reasons drift. Agents either ignore them (they look arbitrary)
or rote-bypass them (they look like obstacles). A rule with the cost+signal
economics attached behaves as the agent's _own conclusion_; the agent
re-derives it under stress instead of bypassing it.

Therefore: every rule surface (this doc, memory entries, hook messages)
carries enough of the reasoning chain that a fresh agent encountering the
rule for the first time understands _why_ before encountering the _what_.

Hooks remain as last-line backstops only. A hook fires when the agent has
made a wrong call despite the doctrine; the hook's job is to point back at
this doc, not to be the doctrine. No new blocker hooks are added when
doctrine is missing — instead, the doctrine is updated to fill the gap.

---

## Worked examples (illustrations of the four principles, not steps)

These scenarios show how P1–P4 compose in practice.

### Example A — PR #106 failure (what went wrong and why)

**Situation**: PR #106 opened at 17:21Z. CI green at 17:23Z. Agent merges
at 17:32Z (PR age 644s, past the 600s gate). Six Copilot/Codex comments
sit on a closed PR.

**Where each principle was violated:**
- P2: The agent did not classify the six threads or apply any fixes. The
  merge was the merge gate's green light, not "all signal extracted".
- P3: A fresh sonnet was dispatched post-merge to fix the orphaned comments;
  that is the context-load anti-pattern.
- P4: The hook message at the time did not explain P2; the agent treated the
  gate's literal conditions as the full spec.

**What P1–P4 would have produced instead:**
- Agent waits 8 min (P2). Lists six threads at 17:30Z. Classifies: three
  STYLE_NIT (apply), two BUG (apply), one MISUNDERSTANDING (resolve without
  commit). Pushes fixes at 17:38Z. Bots re-fire. Resolves all six threads.
  Merges at ~17:50Z. Total cost: one extra push + 12 minutes. Benefit:
  all signal extracted, no orphaned comments, no follow-up PR needed.

### Example B — 60-LOC security fix, genuinely isolated

**Situation**: agent finds a path traversal bug. Fix is 60 LOC. Nothing
adjacent is broken.

**P1 says:** self-LOC = 60, below 300. Audit the "isolated" assumption —
are there other traversal patterns in the same module? If yes, bundle. If
genuinely isolated AND it's a security issue (operator deadline), use
`ZEUS_PR_ALLOW_TINY=1` and document: "Security fix — traversal in
`path_util.py`; no adjacent pattern; urgency justifies small PR."

**P2 says:** after opening, wait 8 min, process bot comments inline, no
thread replies.

**P3 says:** whoever wrote the 60-LOC fix handles the bot comments.

### Example C — 1500-LOC migration, genuinely one unit

**Situation**: agent rewrites the data ingestion pipeline. 1500 LOC across
12 files. All files are logically part of one migration: the old schema
cannot coexist with the new one.

**P1 says:** coherence check — can a reviewer summarize this in one
sentence? "Migrate ingestion pipeline from v1 schema to v2 schema with
backward-compatible fallback." Yes. Ship as one PR. The LOC threshold
(300) is already cleared; splitting this into three PRs would require
shipping partial migrations that break CI on each intermediate commit.

**P2 says:** 1500-LOC PR will generate more bot comments. Budget 20 min
for post-open comment processing, not 8. The taxonomy and resolution
procedure are the same regardless of PR size.

---

## Backstop hooks (last-line enforcement, not the spec)

These hooks enforce at runtime what the principles enforce by reasoning.
They fire when an agent has already departed from the doctrine.

### `pr_create_loc_accumulation` (Principle 1 backstop)

Triggers on `gh pr create` / `gh pr ready` when self-authored LOC since
merge-base is below 300.

**Block message points to:** this document, Principle 1.
**Bypass:** `ZEUS_PR_ALLOW_TINY=1` (degrades to advisory; requires bypass
justification in PR body).
**Reading the hook firing:** "I tried to open a PR before the unit was
large enough — either bundle more work onto this branch or document why
the isolation is genuine and urgent."

### `pre_merge_comment_check` (Principle 2 backstop)

Triggers on `gh pr merge <N>` when:
- PR age < 600s (bots haven't had time to fire)
- ANY unresolved review thread exists (B2 strict mode: all threads)
- Any review state == CHANGES_REQUESTED (non-dismissed)

**Block message points to:** this document, Principle 2.
**Bypass:** `ZEUS_PR_MERGE_FORCE=1` (emits warning; requires per-thread
disposition documented in PR body for each bypassed thread).
**Reading the hook firing:** "I tried to merge before all signal was
extracted — go back, classify each thread (BUG/STYLE_NIT/MISUNDERSTANDING/
NOISE/OUT_OF_SCOPE), apply fixes, resolve threads, THEN merge."

The bypass is for genuine emergencies (production breakage, security) where
the cost of waiting outweighs the cost of re-opening for follow-up. It is
NOT for cases where the agent wants to avoid thread processing.

---

## Failure-injection drill (operational antibody)

The above tests catch regressions in the artifacts. The drill catches
regressions in agent _behavior_, which artifact tests can't reach.

**Procedure** (run at end of each PR-workflow bundle, then at start of the
next bundle that touches PR workflow):

1. Open a synthetic PR on a throw-away branch with three deliberately-flawed
   surfaces: a typo in a function name (Copilot will catch), a missing
   docstring (Copilot suggestion), an obvious O(n²) loop where O(n) is
   trivial (Codex will catch as P2).
2. Wait 8 min for bots.
3. Observe whether the agent in session naturally: (a) classifies the three
   threads correctly, (b) applies fixes via commits without writing reply
   paragraphs, (c) resolves threads via the GraphQL mutation, (d) merges
   only after the count drops to zero.
4. Log observed failure modes: any thread reply, any "filed for next PR"
   deferral without a tracked issue, any merge attempt before thread count
   reaches zero, any stranger-dispatch.
5. Each failure mode → file as a missing antibody → either tighten an
   existing test or add a memory entry.

Log the drill results in `docs/operations/task_2026-05-09_workflow_redesign_plan/DRILL_LOG.md`.

---

## Memory entry pointers

The following operator-side memory entries (outside the repo) anchor the
four principles for fresh-session agent loading:

- `feedback_pr_300_loc_threshold_with_education.md` — Principle 1 (LOC
  threshold, cost economics, decision tree)
- `feedback_pr_unit_of_work_not_loc.md` — Principle 1 (coherence over
  LOC count)
- `feedback_pr_bot_comments_are_bug_reports.md` — Principle 2 (bug
  taxonomy, no-thread-reply rule, fix-commit IS the response)
- `feedback_pr_original_executor_continuity.md` — Principle 3
  (orchestrator dispatch rule, mechanical vs. substantive split)
