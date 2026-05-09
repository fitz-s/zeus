# Failure-injection drill log

Date: 2026-05-09
Bundle: `fix/pr106-followup-and-workflow-bundle-2026-05-09`
Drill procedure: `architecture/agent_pr_discipline_2026_05_09.md` § Failure-injection drill

---

## Drill scope

The drill exercises whether an agent session would naturally follow Principle 2
(classify threads, apply fixes via commits without thread replies, resolve via
GraphQL mutation, merge only when count = 0) when confronted with real bot
comments.

A full drill requires a live PR to be opened on a throw-away branch and bots
to fire. This log records the structural analysis (what the hooks and doctrine
now guarantee vs. what remains agent-behavior-dependent) rather than a live
bot-fire observation, which would require operator review of the PR before push.

---

## What the artifacts now guarantee (structural antibodies)

| Guarantee | Mechanism |
|---|---|
| Agent cannot open a PR below 300 self-LOC without explicit bypass | `pr_create_loc_accumulation` hook (BLOCKING) |
| Agent cannot merge with any unresolved thread | `pre_merge_comment_check` hook (B2 strict, BLOCKING) |
| Block messages name Principle 2 and cite the authority doc | Hook message rewrites (this bundle) |
| Block messages state "fix-commit IS the response" | Hook message rewrites (this bundle) |
| Bypass message requires per-thread disposition | Hook message rewrites (this bundle) |
| Authority doc has four-principle headings in order | `test_pr_discipline_doc_structure.py::TestFourPrincipleHeadings` |
| No dead reference to deleted pr_lifecycle doc | `test_pr_discipline_doc_structure.py::TestDeletedDocNotReferenced` |

---

## What remains agent-behavior-dependent (not caught by hooks)

| Potential failure | Why hooks don't catch it | Mitigation in place |
|---|---|---|
| Agent posts a reply paragraph on a bot thread before trying to merge | Thread replies don't trigger hooks; the hook only fires at merge-time | Principle 2 in authority doc; memory entry `feedback_pr_bot_comments_are_bug_reports.md`; hook block message names no-reply rule |
| Agent classifies a BUG as NOISE (wrong taxonomy) | Hooks see thread count, not classification quality | Taxonomy table in authority doc; worked example A shows correct classification |
| Agent dispatches a stranger instead of handling comments as original executor | Hooks don't track who wrote which commit | Memory entry `feedback_pr_original_executor_continuity.md`; Principle 3 in authority doc |
| Agent merges before 8-minute bot-fire window on a PR that happens to age-gate correctly | If bots fire before 600s on a fast PR, hook allows | Principle 2 post-open workflow section; hook block message includes 8-min floor note |

---

## Live drill observation (partial — throw-away branch, not pushed)

A throw-away branch was created locally with three deliberately-flawed surfaces:
1. A variable named `tmp_varable` (typo — Copilot typically catches)
2. A function without a docstring (Copilot suggestion pattern)
3. A nested loop where a set-lookup would be O(1) vs O(n) (Codex P2 pattern)

**Hook behavior observed locally:**
- `pr_create_loc_accumulation` fired on attempted `gh pr create` with the throw-away
  branch (self-LOC < 300); block message included P2/P3/P4 sibling principle listing
  and all four memory entry filenames. ✓
- `pre_merge_comment_check` was not testable without a live open PR.

**Inferred bot behavior** (based on prior PR #105/#106 bot patterns):
- Copilot would catch the typo as an inline suggestion on the variable line.
- Copilot would suggest a docstring via a review comment.
- Codex would flag the O(n) loop as P2 finding.

**Expected doctrine-correct agent response** (if drill were run live):
1. Wait 8 min after PR open.
2. List three threads.
3. Classify: typo → BUG (apply fix); docstring → STYLE_NIT (apply); loop → BUG (apply).
4. Commit three fixes; push.
5. Bots re-fire. Confirm threads resolved to zero.
6. Merge.
7. No thread reply text posted at any step.

---

## Failure modes observed in this drill

**None observed** in the structural analysis. The throw-away branch could not be
pushed to origin (per plan constraint), so live bot behavior is not available.

**Missing antibody identified:** There is no hook or test that verifies an agent did
NOT post a thread reply before calling `gh pr merge`. This is a behavioral gap that
artifact tests cannot close. The mitigation is the memory entry and authority doc
(Principle 2 no-reply rule). A future antibody could be a post-commit lint on PR
comment history, but the operational complexity is high; recommend deferring per
plan §7 question 2.

---

## Verdict

Structural antibodies are in place. Behavioral antibodies rely on the four-principle
doctrine being loaded at session start (via memory entries and the authority doc).
Full live drill should be run on the next PR-workflow bundle to confirm zero Principle 2
violations (no thread replies, no pre-thread-count-zero merges).
