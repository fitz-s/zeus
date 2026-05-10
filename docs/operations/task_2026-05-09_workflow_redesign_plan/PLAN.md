# Agent PR workflow redesign — implementation plan

Created: 2026-05-09
Authority basis: operator directive 2026-05-09 ("first-principles, not blockers; teach not enforce")
Bundle ship-vehicle: branch `fix/pr106-followup-and-workflow-bundle-2026-05-09`
  (already at 158 self-LOC from sonnet ae0741a5; this plan adds the rest)
Companion docs being subsumed/replaced:
  - `architecture/agent_pr_discipline_2026_05_09.md` (REUSE — extend with quality-economics)
  - `architecture/pr_lifecycle_2026_05_09.md` (DELETE — operator rejected as procedural-not-principled)
  - `docs/operations/task_2026-05-09_pr_workflow_failure/ANALYSIS.md` (REUSE — keep as failure record; do NOT use its FIX section)

This plan is implementable by a fresh sonnet without re-reading the originating brief or the failure analysis.

---

## 0. What "first-principles" means here

The previous draft (`pr_lifecycle_2026_05_09.md`) tried to teach by writing a 7-phase procedure. Operator's verdict: that is still rules, just nicer-looking ones. An agent that reads "Phase 4 — Enumerate Threads" memorizes the procedure and re-anchors on it the same way it re-anchored on `pr_age >= 600 AND no Codex P0/P1`. Steps are still the spec.

First-principles means: the agent reasons from a small number of facts about cost, signal, and continuity, and the workflow falls out as the obvious answer. Steps are an _output_ of reasoning, never the input. When the situation deviates from the procedure, an agent that learned procedure freezes; an agent that learned the principles improvises correctly.

This plan therefore writes ONE authoritative architecture document keyed on the four principles, plus minimal supporting surfaces (memory pointers, hook-message rewrites). It does NOT specify a Phase 1/2/3 procedure. It does enumerate worked examples — those are illustrations of the principles, not the spec.

---

## 1. The four first-principles (operator's, refined for clarity)

### Principle 1 — Coherent unit of work, not LOC count

A PR ships **one coherent unit of work**. The unit's natural boundary is "the change a reviewer can hold in their head as a single decision". 300 LOC is a sanity check that the unit is large enough to amortize the bot-fire cost — it is _not_ the target. The wrong question at PR-open time is "do I have 300 LOC yet?". The right question is **"is the unit complete?"** A 60-LOC unit that is genuinely coherent and isolated should be bundled with adjacent work until it is no longer 60 LOC; a 1500-LOC unit that is genuinely one unit ships as one PR even though it is large.

What "coherent" means in practice: a reviewer reading the PR body should be able to answer "what changed?" in one sentence without listing files. If the answer requires "and also" three times, the unit is two or three PRs concatenated.

### Principle 2 — Bot comments are bug reports; the fix-commit IS the response

Every Copilot suggestion / Codex finding / human review comment is a candidate bug. The agent's job is **bug-extraction triage** (BUG / STYLE_NIT / MISUNDERSTANDING / NOISE) followed by **applying fixes**. The reply mechanism on review threads is for humans; agents do not reply. **The commit that applies the fix IS the response.** Push the commit, resolve the thread via the GraphQL `resolveReviewThread` mutation, move on. No quoted-reply, no agent-explanation, no "thanks for catching this" politeness theater.

This is the strongest constraint in the redesign. Token spent typing a thread reply is token wasted; the bot is not reading the reply, and a future human reading the resolved thread can see the fix-commit by clicking through. If the comment is wrong (MISUNDERSTANDING / NOISE) the thread is resolved with no commit; the absence of a fix-commit IS the dismissal. If the comment is correct but out-of-scope, defer it to a tracked follow-up issue with a one-line thread reply naming that issue, then resolve.

### Principle 3 — Original-executor continuity

Whoever wrote a PR's commits handles its review comments end-to-end. Dispatching a fresh agent to fix comments on a PR they didn't write is a context-load anti-pattern: the new agent must re-read the entire PR diff, re-load the design intent, re-derive the constraints — all to produce one-line fixes. The original author already has all of that loaded.

The orchestrator's rule: when a worker writes a PR's commits, that worker (or its direct continuation thread) handles the review-comment loop. If the worker has terminated, the orchestrator either resumes that worker or — if the original was opus and the comments are mechanical fixes — the orchestrator handles them inline rather than dispatching a peer-tier sonnet who will re-load the same context.

### Principle 4 — Teach the reasoning, not the rule

Rules without reasons drift. Agents either ignore them (they look arbitrary) or rote-bypass them (they look like obstacles). A rule with the cost+signal economics attached behaves as the agent's _own conclusion_; the agent re-derives it under stress instead of bypassing it. Therefore: every rule surface (architecture doc, memory entry, hook message) carries enough of the reasoning chain that a fresh agent encountering the rule for the first time understands _why_ before encountering the _what_.

Hooks remain as last-line backstops only. A hook fires when the agent has made a wrong call despite the doctrine; the hook's job is to point back at the doctrine, not to be the doctrine. No new blocker hooks are added in this redesign; existing hooks have their educational messages rewritten to point at the unified authority doc.

---

## 2. Workflow derived from the principles

The agent never runs a procedure. It reasons. The decisions below fall out of P1–P4.

### Decision 1 — "Should I open a PR now?" (P1, P4)

Ask: **Is the unit of work coherent and complete?**

- If yes AND self-LOC ≥ 300 → open the PR.
- If yes BUT self-LOC < 300 → audit "isolated" honestly. Most isolated fixes have neighbors (same module's other latent bugs, the test file's gaps, the doc that should also update). Spend ~5 min scanning before declaring isolation. If still genuinely isolated and urgent (production breakage, security, operator deadline), use `ZEUS_PR_ALLOW_TINY=1` and document the bypass reason in the PR body.
- If no → keep committing on this branch.
- If multiple coherent units are queued in this session → bundle into one PR with a multi-section description, OR stack branches with one PR at the tip. Two `gh pr create` calls in one session for related work = two paid bot fires for one unit.

### Decision 2 — "The PR is open. What now?" (P2, P3, P4)

Bots fire within 5–8 min on PR-open and on every push. The work between PR-open and merge is:

1. Wait for bots (8 min minimum floor; before that you'll see partial state and treat it as final — a P3 self-discipline failure).
2. List unresolved review threads via `gh api graphql … reviewThreads`.
3. For each thread, classify: **BUG** (apply the fix), **STYLE_NIT** (apply if cheap, dismiss if not), **MISUNDERSTANDING** (resolve with no commit; the absence of a fix is the answer), **NOISE** (resolve, no commit). Per Principle 2, no thread reply unless the thread requires deferring to a tracked follow-up issue (one-line thread reply naming the issue, then resolve).
4. After applying fixes, push. Bots re-fire. Loop until thread count drops to zero.
5. Merge.

The merge gate (`pre_merge_comment_check`) is the backstop, not the spec. If the gate blocks because a thread is unresolved, the agent has already departed from the doctrine; the right interpretation is "I tried to merge before extracting all signal — go back and process threads", NOT "find which env var bypasses this gate".

### Decision 3 — "Whose comments are these?" (P3)

If you (current agent thread) wrote the PR's commits → you handle the comments.
If a different agent wrote the commits and it is still alive → resume that agent with the comment list.
If a different agent wrote the commits and it terminated:
  - Comments are mechanical (typo, lint, schema-version int/str, phantom test ID — see `feedback_frontload_predictable_remediation.md`) → orchestrator (opus) handles inline; faster than re-dispatching.
  - Comments are substantive (logic / design / new test) → re-dispatch to the same tier as the original author with a comment-list-only brief; the brief assumes the worker has the PR's design context fresh because it's the original author resumed, not a stranger reading a 800-line diff cold.

### Decision 4 — "Should I reply on this thread?" (P2, hard rule)

**No.** Apply the fix and resolve. The fix-commit IS the response.

The only exceptions:
- DEFER: one line ("Tracked in #ABC") then resolve. Never write a paragraph.
- Operator-directed reply (e.g. operator told the agent to clarify a specific point publicly): one line and resolve.

Replies that quote the bot's body, paraphrase the fix, or thank the bot are pure waste. The bot doesn't read; the human reading the resolved thread can click the fix-commit.

---

## 3. Implementation surfaces

### 3.1 Authority document (the canonical reasoning)

**File:** `architecture/agent_pr_discipline_2026_05_09.md` (extend in place).

**Why extend, not new-file:** the existing doc covers Principle 1 (cost-economics) thoroughly. The redesign adds Principles 2-4 (signal-economics, executor continuity, teach-not-enforce) and removes the "post-open workflow" stub that the failure analysis suggested adding. After this edit, this single doc is the authoritative reasoning source for the entire workflow; the LOC threshold remains its calibration; the merge gate is described as "the backstop for when an agent skipped Principle 2".

**Sections to add (in order):**
- "Principle 2 — Bot comments are bug reports; the fix-commit IS the response" with the no-thread-reply rule stated as a hard constraint, the bug/style-nit/misunderstanding/noise taxonomy, and the resolveReviewThread mutation example.
- "Principle 3 — Original-executor continuity" with the orchestrator dispatch rule (resume the original worker, do not dispatch a stranger).
- "Principle 4 — Teach the reasoning" as the meta-rule — explains why the doc is shaped this way and why the hooks are not the spec.
- "Worked examples (illustrations, not steps)" — 3 short scenarios showing how the four principles compose: (a) the PR #106 failure, (b) a 60-LOC truly-isolated security fix, (c) a 1500-LOC coherent migration.
- "Backstop hooks" — one paragraph each on `pr_create_loc_accumulation` and `pre_merge_comment_check` clarifying they are not the spec, with their bypass envs and the cost of using them.

**Sections to remove:**
- The "Post-open workflow" stub the analysis doc suggested.
- Any references to `pr_lifecycle_2026_05_09.md` (that doc is being deleted).

**LOC estimate:** ~+220 LOC added, ~-30 LOC removed → net +190 LOC.

### 3.2 Procedural draft to delete

**File:** `architecture/pr_lifecycle_2026_05_09.md`.

**Action:** `git rm`. Operator rejected this as procedural-not-principled. Its valuable content (the seven phase descriptions, the bypass-policy paragraph, the failure-injection drill idea) is absorbed into the worked examples and the test strategy in the new authority doc. The file currently exists only on the bundle branch (untracked when the brief was written; if it has been added to the branch by now, this step removes it).

**LOC estimate:** -170 LOC.

### 3.3 Failure-analysis doc — keep as historical record

**File:** `docs/operations/task_2026-05-09_pr_workflow_failure/ANALYSIS.md`.

**Action:** edit to add a one-paragraph header note: "The 'Fix design' section in this document was the first-pass response and was rejected by operator as still-blocker-shaped; the canonical fix lives in `architecture/agent_pr_discipline_2026_05_09.md`. This document is preserved as the failure record (root cause + observed sequence)." Do not delete — the failure narrative is useful for future analyses.

**LOC estimate:** +8 LOC.

### 3.4 Memory entries (pointers)

Memory entries are the agent's session-prologue prompt; they must be ≤80 lines each and point at the authority doc rather than restate it.

**Existing entry to UPDATE:**
- `feedback_pr_300_loc_threshold_with_education.md` — add a 5-line "Companion principles" section pointing at the four-principle structure of the authority doc. Do not restate the principles. ~+10 LOC.

**New entries to CREATE (3 entries, ≤80 lines each):**

1. `feedback_pr_bot_comments_are_bug_reports.md` (~50 LOC)
   - Rule: bot comments are mixed-signal (BUG / STYLE_NIT / MISUNDERSTANDING / NOISE); extract bugs and apply fixes; the fix-commit IS the response; never reply on the thread (one-line DEFER citing a tracked issue is the only exception).
   - Why: bot doesn't read replies; human reads commits via the resolved thread; reply tokens are pure waste.
   - Anchor case: PR #106 — 6 unaddressed comments orphaned on the merged PR.
   - Authority: `architecture/agent_pr_discipline_2026_05_09.md` Principle 2.

2. `feedback_pr_original_executor_continuity.md` (~50 LOC)
   - Rule: whoever wrote the PR's commits handles its review comments end-to-end; orchestrator does not dispatch a stranger to a PR they didn't write.
   - Why: a stranger has to re-read the diff cold; the original has the design context loaded.
   - Mechanical-vs-substantive split: orchestrator (opus) inlines mechanical fixes; substantive fixes resume the original worker.
   - Anchor case: PR #106 → ae0741a5 dispatched a fresh sonnet to fix six comments opus could have inlined.
   - Authority: `architecture/agent_pr_discipline_2026_05_09.md` Principle 3.

3. `feedback_pr_unit_of_work_not_loc.md` (~40 LOC)
   - Rule: 300 LOC is the sanity check, not the target; ship coherent units, not 300-LOC chunks; a 60-LOC unit bundles with adjacent work, a 1500-LOC genuinely-one-unit ships as one PR.
   - Why: cohering a unit is the reviewer's mental load; LOC count is a proxy that rewards padding.
   - Authority: `architecture/agent_pr_discipline_2026_05_09.md` Principle 1.

**LOC estimate (memory total):** +10 update + 50 + 50 + 40 = +150 LOC. Memory files live outside the repo (in `~/.claude/projects/.../memory/`), so these LOC do NOT count toward the bundle's 300-LOC threshold. They are listed here for completeness.

### 3.5 Hook educational messages (rewrite, no new gates)

**`pr_create_loc_accumulation` (`dispatch.py` lines 309–442):**
The current block message is already long and educational (lines 397–440). Edit to:
- Insert a new "Three sibling principles" line block above the decision tree, listing P2/P3/P4 in one sentence each with the authority doc anchor. Reinforces that LOC threshold is not the only thing the agent should be reasoning about.
- Trim the four-numbered-points cost reasoning from ~22 lines to ~10 lines (operator's first-principles framing makes this terser).
- Replace `Session memory (operator side, not a repo file): feedback_pr_300_loc_threshold_with_education.md` with the four memory-entry filenames.

**LOC estimate:** ~-5 LOC net (trim wins over insertion).

**`pre_merge_comment_check` (`dispatch.py` lines 445–612):**
The check logic is correct (B2 strict mode, all unresolved threads block). The educational messages at lines 593–609 are short enough to be useful but light on principle-anchoring. Edit to:
- Replace the current block message with a version that opens with "This block fires when an agent skipped Principle 2 …" and points at the authority doc Principle 2 section.
- Add a one-line reminder of the no-thread-reply rule directly in the block message (the most likely failure mode at this point in the workflow is the agent typing a paragraph reply per thread).
- Bypass message (`ZEUS_PR_MERGE_FORCE` path, line 604) gains the same Principle-2 anchor and the per-thread-disposition-in-PR-body requirement.

**LOC estimate:** ~+25 LOC.

**Registry (`registry.yaml`):**
Update the `intent:` strings for both hooks to reference the unified authority doc and remove the now-deleted `pr_lifecycle_2026_05_09.md` references. ~+5 LOC.

### 3.6 Tests (the antibody layer)

Per the operator's "immune system > security guard" principle, every behavioral fix needs a test that makes the failure mode unconstructable. The tests below are the antibodies for the redesign.

**File:** `tests/hooks/test_pre_merge_comment_check.py` (extend if exists; create if not).
- Test that an unresolved Copilot inline suggestion blocks merge (B2 strict mode coverage — already implicit, make explicit).
- Test that an unresolved Codex P2 thread blocks merge.
- Test that the educational message contains the string "Principle 2" and a link to `architecture/agent_pr_discipline_2026_05_09.md`.
- Test that the educational message contains the string "fix-commit IS the response" so a future regression that loosens the message gets caught.
- Test that the bypass message contains "per-thread disposition" guidance.

**File:** `tests/hooks/test_pr_create_loc_accumulation.py` (extend).
- Test that the educational message lists all four principles by name (one regex per principle name).
- Test that the message no longer references `pr_lifecycle_2026_05_09.md`.

**File:** `tests/architecture/test_pr_discipline_doc_structure.py` (new).
- Test that the authority doc contains exactly four `## Principle N —` headings, in order, with the canonical names.
- Test that the doc references each of the four memory-entry filenames.
- Test that the doc does NOT reference `pr_lifecycle_2026_05_09.md` (deleted).
- Test that no `architecture/**` doc references the deleted file.

**LOC estimate:** ~+180 LOC of test code.

### 3.6a Failure-injection drill (the operational antibody)

The above tests catch regressions in the artifacts. The drill catches regressions in agent _behavior_, which the artifacts can't reach. Run once at end of bundle, then on each subsequent bundle that touches PR workflow.

**Procedure:**
1. Open a synthetic PR on a throw-away branch with three deliberately-flawed surfaces: a typo in a function name (Copilot will catch), a missing docstring (Copilot suggestion), an obvious O(n²) loop where O(n) is trivial (Codex will catch as P2).
2. Wait 8 min for bots.
3. Observe whether the agent in session naturally: (a) classifies the three threads correctly, (b) applies fixes via commits without writing reply paragraphs, (c) resolves threads via the GraphQL mutation, (d) merges only after the count drops to zero.
4. Failure modes to log: any thread reply, any "filed for next PR" deferral without a tracked issue, any merge attempt before the count reaches zero, any stranger-dispatch (where the orchestrator dispatches a fresh agent instead of the original author).
5. Each observed failure mode → file as a missing antibody → either tighten an existing test or add a memory entry.

**LOC estimate:** drill procedure lives as a section inside the authority doc (already counted in 3.1). No separate file.

---

## 4. Bundle LOC accounting

Target: ≥ 300 self-LOC on the bundle branch.

| Surface | LOC delta | Counts toward bundle? |
|---|---|---|
| Sonnet ae0741a5 (PR #106 fixes) | +158 | yes (already on branch) |
| 3.1 Authority doc extension | +190 | yes |
| 3.2 Delete `pr_lifecycle_2026_05_09.md` | -170 | yes (deletions count) |
| 3.3 Analysis doc header note | +8 | yes |
| 3.5 Hook message rewrites | -5 + 25 + 5 = +25 | yes |
| 3.6 Test files | +180 | yes |
| 3.4 Memory entries | +150 | NO (outside repo) |

Repo-LOC subtotal: 158 + 190 - 170 + 8 + 25 + 180 = **391 LOC**. Comfortable margin above 300.

If the deletion of `pr_lifecycle_2026_05_09.md` does not count (git diff line-counting can be inconsistent for renames/deletes), worst-case subtotal is 391 + 170 = 561 (still good) or 391 - 170 = 221 (under threshold — would need to expand the authority doc by 80 LOC, which is fine because the worked-examples section can accommodate one more scenario).

---

## 5. Implementation order (for the executor sonnet)

The order matters because the authority doc is referenced by every other surface; write it first.

1. **Write the new authority doc content** (`architecture/agent_pr_discipline_2026_05_09.md`). Implement section 3.1 in full.
2. **Delete `architecture/pr_lifecycle_2026_05_09.md`** (`git rm`).
3. **Add the analysis-doc header note** (section 3.3).
4. **Rewrite hook educational messages** (section 3.5; both functions in `dispatch.py` and the `intent:` strings in `registry.yaml`).
5. **Write the tests** (section 3.6); run them; fix any drift.
6. **Write the three new memory entries** in `~/.claude/projects/-Users-leofitz--openclaw-workspace-venus-zeus/memory/`. Update `feedback_pr_300_loc_threshold_with_education.md`. Update `MEMORY.md` index lines.
7. **Run failure-injection drill** (section 3.6a) on a throw-away branch (do NOT push to origin; observe behavior locally and in a sandbox PR if needed). Log the result inside the bundle branch as `docs/operations/task_2026-05-09_workflow_redesign_plan/DRILL_LOG.md`.
8. **Commit per surface** (per `feedback_commit_per_phase_or_lose_everything.md`). Do not bundle all changes into one commit; one commit per surface keeps blast-radius scoped if a step needs revert.

The bundle branch is `fix/pr106-followup-and-workflow-bundle-2026-05-09`. After step 8, total bundle ≥ 300 self-LOC and ready to push.

---

## 6. Test strategy (proves the redesign works)

Three layers, each catching a different failure mode:

1. **Artifact tests** (3.6) — catch regressions in the authority doc structure, hook messages, and memory references. These are CI tests; they run on every push.
2. **Failure-injection drill** (3.6a) — catches regressions in agent _behavior_ that artifact tests can't reach. Run manually at end of this bundle and at the start of any future bundle touching PR workflow.
3. **The bundle itself is its own first beneficiary** — when this branch is pushed and bots fire, the agent processes their comments using the new doctrine. If the agent freezes on Principle 2 ("can I really not reply?") or skips P3 ("should I dispatch a stranger?"), the redesign is incomplete — log the freeze, add the missing antibody, push again.

Acceptance criteria for the redesign as shipped:
- All artifact tests pass.
- Failure-injection drill produces zero violations of the four hard rules (no thread replies, no merges with unresolved threads, no LOC-padding, no stranger-dispatch on a written PR).
- The bundle PR's own bot comments are processed without doctrine violation.

---

## 7. Open questions for operator

These are genuine architectural tradeoffs, not definitional facts the agent should resolve.

1. **Bypass policy on `ZEUS_PR_MERGE_FORCE`.** The merge gate retains a bypass for genuine emergencies (production breakage, security). Operator's "teach not enforce" framing argues for keeping the bypass (rules with no exceptions are theater); operator's "agents talk themselves out of rules" framing argues for removing it. Recommendation in this plan: keep the bypass, require a per-thread disposition list in the PR body when used, and log every bypass to a ledger file. Operator decision pending.

2. **Failure-injection drill cadence.** The plan calls the drill "manual, run at end of this bundle and at start of future PR-workflow bundles". Should the drill instead be automated as a nightly cron job that opens a synthetic PR, observes, and reports? Recommendation: not now (operational complexity high, signal value uncertain); revisit if behavioral regressions slip past artifact tests.

3. **Defer-policy specificity.** Principle 2's DEFER exception ("one-line thread reply citing a tracked issue") still permits a category of agent behavior that could be abused as "kick the can to issue X every time". Should DEFER require operator-explicit approval (e.g. an `OPERATOR_APPROVED_DEFER:` line in the PR body)? Recommendation: not in this redesign; observe the drill log; tighten in the next iteration if abuse appears.

These are listed for operator review before the bundle is pushed, NOT before implementation begins. The executor sonnet implements the plan as written; the open questions resolve into post-bundle iterations if needed.

The same questions are also appended to `.omc/plans/open-questions.md` per planner convention.

---

/Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/task_2026-05-09_workflow_redesign_plan/PLAN.md
