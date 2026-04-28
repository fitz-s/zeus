# Adversarial Debate for Project Evaluation — Methodology

A reusable, multi-round, evidence-driven debate process for evaluating any non-trivial project decision. Distilled from the harness-debate cycle 2026-04-27 (3 rounds, ~70 min elapsed, 5 longlast teammates, 11 external sources, 50+ grep-verified citations, dramatic mutual convergence in all 3 rounds).

**Purpose**: when the decision is high-stakes, multi-valid-approach, and benefits from genuine adversarial scrutiny — replace single-thread analysis with structured cross-examination that produces operator-actionable synthesis.

**Reusable across topics**: not limited to harness. Applies to architecture decisions, capital allocation, refactoring strategies, vendor selection, deprecation planning, methodology adoption, hiring direction, or any "we have valid arguments on both sides" question.

---

## §0 When to use this methodology

**Use it when** ALL of:

- The decision is non-trivial (≥4h of work or ≥1 multi-day commitment downstream)
- Multiple valid approaches exist (or at least appear to)
- The team has not yet built consensus
- Empirical data alone cannot settle the question (debate is needed because evidence is interpreted differently)
- The cost of a wrong decision is meaningful (engineering hours, revenue, reputation)

**Do NOT use it when**:

- The decision is small / quickly reversible (just decide and iterate)
- One side is obviously correct (use the empirical data directly)
- The issue is purely aesthetic or preference-based
- You don't have time for ~70 min of structured cycle
- Your model setup cannot support 3+ longlast teammates concurrently

**ROI signal**: a 3-round cycle for a 100h+ implementation decision is high-ROI. The same cycle for a 10h decision is overkill — use single-shot critic-opus instead.

---

## §1 The 3-round adversarial debate format

### Round 1 — ROI / direction assessment

Question pattern: "Is X net-positive vs net-negative for goal G?"
- Examples: "Is the harness net-positive ROI on Opus 4.7?", "Is microservices net-positive for our scale?", "Is moving from Postgres to Cassandra net-positive for our access patterns?"

Output: mixed verdict with axis-by-axis analysis; LOCKED concessions both sides agree on; itemized subtraction list both sides agree on (if net-negative tilt); deferred questions for later rounds.

Typical elapsed: ~15-20 min with 2 teammates.

### Round 2 — Alt-system / synthesized middle

Question pattern: "Given Round-1 verdict, what's the right end-state structure?"
- Each side proposes a concrete alt-system honoring Round-1 LOCKED concessions
- Phase-1 (parallel): each side writes their proposal
- Phase-2 (parallel): each side critiques the other's
- Output: synthesized middle (often dramatically convergent under Phase-2 cross-examination)

Typical elapsed: ~20-25 min.

### Round 3 — Capital allocation / sequencing

Question pattern: "Given the synthesized end-state, how do we allocate finite resources to get there?"
- Each side proposes time-phased allocation
- Phase-1 (parallel): proposals
- Phase-2 (parallel): critique + LOCK FINAL POSITION
- Output: operator-actionable schedule with empirical decision triggers

Typical elapsed: ~25-30 min.

### Round 4+ (optional)

If implementation reveals new empirical data, run additional rounds on the most-divergent remaining question. Defer round-4 until implementation data lands — round-N+1 with empirical data is dramatically more valuable than round-N+1 with speculation.

---

## §2 Pre-debate setup checklist

Before spawning teammates:

1. **Write a TOPIC.md** in `docs/operations/<task_dir>/TOPIC.md` containing:
   - Core question (1 sentence)
   - Scope ("the X being evaluated") with quantitative size baselines (LOC, file count, etc.)
   - Three axes (or N axes — typically 3) for structuring the debate
   - Required engagement: (a) repo evidence, (b) external evidence ≥2 WebFetch/round, (c) concession bank lock by R2 close
   - R-format table (boot → R1 → R2 → R3 → verdict)
   - Token discipline rules (≤500 char/A2A turn, ≤200 char converged statement, disk-first, file:line cites grep-verified within 10 min)
   - Anti-rubber-stamp rules (10-attack template; no "narrow scope self-validating"; no "pattern proven" without specific test cite; itemize concessions; engage opponent's STRONGEST point at face value)
   - Judge process (judge does NOT participate substantively; routes cross-questions; writes verdict)
   - "What a win looks like" criteria (see §8)

2. **Write a judge_ledger.md** initialized with:
   - HEAD anchor + branch
   - Active state placeholders
   - Round status table
   - Forbid-rerun list (empty)
   - Concession bank (empty)
   - Cross-questions (empty)
   - Process notes section

3. **Decide framing** (4 critical decisions before spawn):
   - Scope: single broad debate vs N sub-debates
   - External evidence floor: minimum WebFetch sources per side per round (recommend floor=2)
   - Stake: verdict-only / verdict + optional action plan / verdict + mandatory subtraction list
   - Judge role: team-lead / independent third-party critic / dual-judge

4. **Choose teammate identities** (typically 2 longlast opus):
   - `proponent-<topic>` — argues the affirmative
   - `opponent-<topic>` — argues the negative
   - Both spawned via Agent with `team_name` + `name` + `model: opus` + `subagent_type: general-purpose`
   - Both LONG-LAST: persist across rounds; do not shut down without explicit team-lead shutdown_request

---

## §3 Per-round mechanics

### Boot phase (idle-only)

Spawn each teammate with an **idle-only bootstrap**: read context, write boot evidence, SendMessage BOOT_ACK, then idle. Do NOT engage substantively until team-lead dispatches R1.

Boot prompt structure:
- Role identity + position (proponent vs opponent)
- Reading list (TOPIC.md + judge_ledger.md + repo authority docs + relevant manifests)
- Boot evidence file template (≤300 lines, 4 sections):
  - §1: read list + 1-2 line key takeaway per file
  - §2: top 3 strongest arguments anticipated
  - §3: top 3 weakest spots opponent will attack + pre-rebuttal sketch
  - §4: 3 external sources planned for R1 WebFetch
- SendMessage exact format: `BOOT_ACK_<ROLE> path=<abs>`
- Constraint reminders (token / disk-first / file:line grep-verified)

### R1 dispatch (after both BOOT_ACKs)

Each side writes opening at `evidence/<role>/R1_opening.md`:
- Engage opponent's STRONGEST anticipated attack at face value before pivoting (per anti-rubber-stamp rule)
- ≥2 WebFetch external sources cited inline with URL + exact quote + timestamp
- ≥1 concrete tradeoff/concession itemized
- file:line cites grep-verified within 10 min
- SendMessage exact format: `R1_OPENING_DONE_<ROLE> path=<abs>`

Sequential or parallel? Both work for opening statements (no inter-dependency). Parallel saves wall-clock.

### R2 dispatch (after both R1 closed)

Each side reads opponent's R1, writes rebuttal at `evidence/<role>/R2_rebuttal.md`:
- Engage opponent's STRONGEST R1 element at face value
- ≥2 NEW WebFetch (no recycle from R1)
- LOCK formal concession bank (itemized "I CONCEDE / I HOLD / UNRESOLVABLE")
- Explicit verdict direction (net-positive / mixed-positive / mixed-negative / net-negative)

### Round-2 / Round-3 alt-system + critique cycle

For round-2 (alt-system) and round-3 (capital allocation), use **two-phase format**:

- Phase-1 (parallel): each side writes proposal at `evidence/<role>/round<N>_proposal.md`
- Phase-2 (parallel): each side critiques opponent's at `evidence/<role>/round<N>_critique.md`
- Both phases: ≥2 NEW WebFetch each (no recycle from prior rounds)
- Phase-2: LOCK FINAL POSITION explicitly (stands / partial-accept / move-toward-middle / surrender)

### Judge writes verdict

After Phase-2 (or R2 if 1-phase format), judge writes `<round>_verdict.md`:
- TL;DR (1-3 sentences)
- LOCKED concessions (both sides agreed; not re-debatable)
- Remaining bounded disagreements
- Unresolvable from current evidence
- Judge's weighing per "what a win looks like" criteria
- Explicit verdict
- Action plan (per stake set in framing)
- Cumulative debate metrics
- Round-N+1 candidate topics + recommendation

---

## §4 Discipline rules (the load-bearing mechanisms)

### Token economy

- ≤500 char per A2A SendMessage (judge↔teammate)
- ≤200 char per converged statement (LOCKED items)
- Boot evidence ≤300 lines
- R1/R2 openings/rebuttals ≤350 lines
- Round-N proposals/critiques ≤350 lines
- Verdicts ≤500 lines
- Judge dispatch can be slightly longer (700-800 char) if substantive context required

**Why**: token budget compounds across teammates × rounds. Disciplined dispatches keep total cycle ~70 min for 3 rounds.

### Disk-first

Every artifact lands on disk BEFORE SendMessage notification. SendMessage is convenience; disk is canonical record. **SendMessage delivery is asymmetric and can drop silently**; disk is the durable source.

Recovery pattern: if a teammate goes idle without sending notification, disk-poll for their output. If found, treat as delivered.

### file:line grep-verification

Every file:line citation must be grep-verified within 10 minutes before any "lock" event (concession, contract, dispatch). Citations rot fast in active codebases (~20-30% premise mismatch in 1 week per repository memory).

Use symbol-anchored citations where possible: `function_name + sentinel comment` survives line-number drift. Pure line numbers rot in days.

### ≥2 NEW WebFetch per round per side

External evidence requirement. Sources must be NEW (no recycling across rounds). Track cumulative source list in judge_ledger.

When WebFetch blocked:
- Dispatch sub-agent with curl / different UA / alternate route
- Pattern from memory: `feedback_on_chain_eth_call_for_token_identity` — when standard fetch fails, sub-agent with broader toolset often succeeds

### Anti-rubber-stamp template

Per memory `feedback_critic_prompt_adversarial_template`:

10 explicit attacks per critique. Never write:
- "narrow scope self-validating"
- "pattern proven" without citing the specific test that proves it
- "looks fine" or "approve" without articulating WHY

Every concession must be itemized (not "agreed in principle"). Every hold must be specific (not vague "I disagree").

A side that admits NO downside is NOT winning — score them lower for engagement quality. A side that visibly retracts under cross-examination is BUILDING credibility, not losing the debate.

---

## §5 Critic-gate workflow (for execution phases)

When the debate transitions from analysis to execution (e.g., dispatching an executor teammate to implement the verdict), add an independent critic-gate to prevent self-approval.

### Spawn

Spawn `critic-<topic>` (opus, longlast) parallel to executor. Critic reads:
- All verdicts (R1+R2+R3 if multi-round)
- DEEP_PLAN.md or equivalent execution roadmap
- Executor's boot evidence
- Repo authority docs
- Pre-execution baselines (test counts, planning-lock state, etc.)

Critic writes own boot evidence at `evidence/critic-<topic>/_boot_critic.md` with:
- LIVE baselines (independently re-measured; do NOT trust documented baselines)
- Per-batch attack vectors (10 attacks per anticipated batch)
- Pytest baseline plan
- Planning-lock receipt verification plan

### Per-batch review workflow

After executor SendMessages `BATCH_X_DONE`:

1. Team-lead SendMessages critic `REVIEW_BATCH_X` with executor's batch summary
2. Critic independently:
   - git diff to see exact changes
   - Re-run pytest baseline (verify ≥ documented baseline)
   - Re-run topology_doctor planning-lock independently
   - Apply 10-attack template to executor's claims
   - Verify any deprecation/stub strategies still satisfy validators
   - Spot-check pre-existing-vs-new findings
3. Critic writes `evidence/critic-<topic>/batch_X_review_<date>.md` (≤300 lines)
4. Critic SendMessages `BATCH_X_REVIEW <APPROVE|REVISE|BLOCK> path=<abs>`

If APPROVE → team-lead dispatches GO_BATCH_X+1
If REVISE → non-blocking concerns rolled into next batch dispatch
If BLOCK → executor must fix specific defects before next batch

### Why this matters

Per memory `feedback_executor_commit_boundary_gate`: "If executor autocommits before critic review, team-lead MUST run wide-review-before-push." Adding critic-gate prevents executor self-approval drift over multi-batch work.

### §5.X Case study — critic-gate catches verdict-level drift

**Context (zeus harness-debate cycle 2026-04-27)**: round-1 + round-2 + round-3 verdicts all cited opponent's empirical finding "33% of INVs are pure prose-as-law" derived from a single grep: `grep -c "tests:" architecture/invariants.yaml = 20`. Round-2 §6.1 #1 LOCKED "DELETE INV-16 + INV-17" as a both-sides-agreed action. By the time the executor reached BATCH D ("DELETE INV-16/17"), this had survived:
- 3 rounds of debate
- Multi-source external evidence cross-checks
- Both sides' face-value retractions
- Judge's verdict synthesis
- DEEP_PLAN consolidation

It was a LOCKED concession by every methodological standard the debate had.

**The catch**: critic-harness, while reviewing BATCH C (a different batch), ran a cross-batch arithmetic equivalence audit AND ALSO a longlast cross-batch INV-deletion-impact preview for the upcoming BATCH D. The preview discovered:
- `tests/test_phase6_causality_status.py` exists with 3 PASSING tests; file docstring: "Relationship tests for Phase 6 INV-16 causality_status enforcement"
- `tests/test_dt1_commit_ordering.py` exists with 6 PASSING tests; file docstring: "Relationship tests for DT#1 / INV-17: DB authority writes commit BEFORE..."

The 9 tests EXIST in the repo. The opponent's grep audit only counted INVs whose `enforced_by:` block CITES `tests:` field. The hidden tests were never searched for.

**Subsequent grep-first audit on the other 8 "untested" INVs**:
- INV-02 has 2 hidden tests in test_architecture_contracts.py
- INV-14 has 4 hidden tests across test_canonical_position_current_schema_alignment + test_dual_track_law_stubs
- Spot-check on INV-03 + INV-07: also have hidden tests

**True LARP rate**: ~0-7%, not 33%. **Empirical claim was 5x overcount.**

**What the methodology preserved**: BATCH D was RE-SCOPED from DELETE to REVERT-PRUNE-MARKERS + ADD tests: blocks. SIDECAR-2 was RE-SCOPED to grep-first audit. **Zero invariants were lost.** The erratum was added to all 3 verdicts (verdict.md §10, round2_verdict.md §9, round3_verdict.md §9) preserving methodological honesty without invalidating the architectural conclusions.

**Lessons for future debates**:

1. **When a debate cites "X% rate" derived from a single grep on a single file**, ALWAYS extend the audit with a cross-repo search BEFORE the claim becomes a LOCKED concession. Better: at debate-time, run the cross-repo audit; failing that, the critic-gate during execution is the backstop.

2. **Schema-citation gap ≠ enforcement gap**. The YAML field not citing tests does NOT mean tests don't exist. Both sides' grep was "necessary but not sufficient" evidence; the verdict treated it as sufficient.

3. **Critic-gate during execution is HIGH ROI**. In this case it prevented a verdict-blessed DELETE that would have orphaned 9 active tests + lost real invariant law. Without the critic-gate, the executor would have followed the verdict and shipped the bad delete.

4. **Empirical retraction in implementation-stage erratum is healthy**. Methodology §7 "self-discovered errors" applies to debate phase; this case study extends the pattern to implementation phase. Both are signs of healthy adversarial discipline.

5. **Update verdicts with empirical errata**. Don't silently fix; explicitly amend with what the original claim was, what audit found, and what changes (or doesn't change) as a result. Methodological transparency compounds across cycles.

6. **The METHODOLOGY itself is improvable**. This case study was added to §5 after the cycle; the next cycle should bake "extend grep audits with repo-wide search" into the dispatch templates BEFORE the verdict locks. See §12 future extensions.

---

## §6 Common failure modes + recovery

### F1: SendMessage drops (boot ACK or proposal not arriving)

**Symptom**: teammate goes idle without sending notification; ~30+ min pass; no disk activity.

**Diagnostic**: disk-poll the expected output file path. If it exists, the work was done; the notification just dropped.

**Recovery**: read the disk file; treat as delivered; proceed normally. Note in judge_ledger as a process observation.

**Prevention**: TOPIC.md instructs "disk-first; SendMessage is convenience." Reinforce in dispatch prompts.

### F2: Task list pollution (judge meta-tasks visible to teammates)

**Symptom**: teammates send MISROUTE_FLAG refusing tasks they perceive as theirs but are actually judge coordination.

**Cause**: judge created tasks in team task list with subjects like "Dispatch R1" — teammates see these as available work.

**Recovery**: delete the misleading tasks (TaskUpdate status=deleted); re-dispatch via clear SendMessage with explicit "this IS the dispatch" framing.

**Prevention**: keep judge meta-tracking in `judge_ledger.md` only; team task list is for teammate work only.

### F3: Role confusion in dispatch

**Symptom**: teammate processes dispatch but doesn't produce output (idle without write).

**Cause**: dispatch language was unclear about WHO does what.

**Recovery**: re-dispatch with explicit "YOUR ROLE" framing + 3 numbered steps + exact SendMessage format.

**Prevention**: every dispatch has 3 numbered steps + literal SendMessage template.

### F4: Path drift in dispatch

**Symptom**: teammate's boot evidence flags path corrections.

**Cause**: judge's dispatch cited paths that don't exist or have moved.

**Recovery**: acknowledge corrections; adopt teammate's verified paths as canonical.

**Prevention**: `find` / `ls` every referenced path BEFORE writing dispatch (per memory `feedback_verify_paths_before_prompts`). 5s upfront beats 5min per-teammate correction.

### F5: Co-tenant commit absorption

**Symptom**: about to commit, accidentally include other agent/user's pre-staged work.

**Recovery**: `git diff --cached --name-only` BEFORE commit; `git restore --staged <file>` for anything not yours.

**Prevention**: `git add` specific files (never `-A` or `.` with co-tenants active per memory `feedback_no_git_add_all_with_cotenant`); verify with `git diff --cached --name-only`; verify with `git log -1` after heredoc commit.

### F6: Wrong baseline cited

**Symptom**: documented test baseline doesn't match LIVE pytest result.

**Cause**: baseline drifted between documentation and now (other work fixed/broke tests in between).

**Recovery**: critic re-measures LIVE baseline at boot; uses LIVE for all subsequent checks; documents the drift in boot evidence.

**Prevention**: critic boots with explicit "re-measure baseline; do NOT trust documented" instruction.

---

## §7 Convergence pattern recognition

Healthy adversarial debate produces dramatic convergence under cross-examination. Watch for these signals:

### Cross-over (highest convergence signal)

When proponent and opponent move PAST each other toward the middle (proponent ends more on opponent's side than they started, or vice versa), you have witnessed genuine intellectual honesty.

Example from harness debate Round-3:
- Proponent started 40/60 H/E → ended 32/68 H/E (moved 8pp toward opponent)
- Opponent started 30/70 H/E → ended 42/58 H/E (moved 12pp toward proponent)
- They CROSSED OVER — proponent now more edge-leaning than opponent

This is the gold standard. Verdict should celebrate the cross-over and lock the synthesized middle as authoritative.

### Visible retraction

A side that retracts a specific claim with an explicit reason (rather than silently quietly walking it back) is building credibility. Retraction events to track:
- "I retract X" + cite source that disproves it
- "My original framing of Y was wrong; correct framing is Z"
- "My N% claim is empirically too high; correct figure is M%"

Score these as POSITIVE for the retracting side, not negative. They demonstrate the engagement quality TOPIC.md asked for.

### Shared external evidence

When both sides cite the SAME external source with DIFFERENT verbatim quotes for cross-validation, the evidence is doing real work. Watch for:
- Both citing Anthropic Claude Code best practices but quoting different paragraphs
- Both citing the same Wikipedia article but for different sub-sections
- Both citing Cognition's "Don't Build Multi-Agents" but reading different parts (headline vs full body)

This pattern indicates the source is rich enough to support multiple positions, AND both sides are reading carefully.

### Self-discovered errors

When a teammate's own NEW WebFetch disproves their earlier framing (e.g., opponent's Knight Capital Wikipedia citation found root cause = deployment omission, NOT harness debt — disproved their own earlier rhetorical use of Knight Capital), they should:
- Explicitly acknowledge "my earlier framing was wrong"
- Adjust their position immediately
- Score this as MAXIMUM credibility build

Score this VERY positively — it's the strongest possible epistemic honesty.

---

## §8 Verdict structure

Every round-N verdict follows this template:

```markdown
# Round-N Verdict — <topic>

Judge: team-lead@<team-name>
HEAD: <commit hash>
Date: <date>
Round-N cycle elapsed: <minutes>

## §0 TL;DR
[1-3 sentences. The synthesized answer.]

## §1 What both sides explicitly LOCKED
[Itemized concessions both sides endorsed in cross-examination. Cannot be reopened.]

## §2 Remaining bounded disagreements
[Small, often empirically decidable. Judge proposes resolution path.]

## §3 What stands UNRESOLVABLE from current evidence
[Honest acknowledgment of epistemic limits.]

## §4 Judge's weighing (per "what a win looks like" criteria)
[5 criteria from TOPIC.md, scored across both sides.]

## §5 Verdict
[Explicit direction. Net-positive / mixed / net-negative / synthesized middle / etc.]

## §6 Action plan (per stake set in framing)
[Operator-actionable items in priority order.]

## §7 Cumulative debate metrics
[Total elapsed, sources cited, retractions counted.]

## §8 Round-N+1 framing (if pursued)
[Candidate topics + judge recommendation on whether to pursue.]
```

### "What a win looks like" criteria

The judge weighs 5 criteria when synthesizing the verdict:

1. **Engagement quality** with opponent's strongest claim (not the strawman)
2. **External evidence concreteness** (cite quotes + URLs, not vague "industry consensus")
3. **Repo evidence specificity** (file:line + grep-verified, not vague "the architecture feels bloated")
4. **Acknowledgment of trade-offs** (a side that admits no downside is not winning)
5. **Survival under cross-examination** (R2/Phase-2 — does the position still hold after attack?)

Judge scores each criterion as: TIE / Slight X edge / X EDGE / X SWEEP. Aggregate determines the verdict tilt direction.

---

## §9 Reusability — applying to non-harness topics

### Topic translation patterns

The 3-round structure adapts to many topics:

| Domain | R1 question | R2 question | R3 question |
|---|---|---|---|
| Harness / scaffolding | Net-positive on current model? | Synthesized end-state target? | Capital allocation Edge vs Safety? |
| Database migration | Net-positive to migrate? | Target schema / data layout? | Migration phase plan? |
| Service decomposition | Should we split monolith? | Target service boundaries? | Migration sequencing? |
| Vendor selection | Is incumbent net-positive? | Target alternative architecture? | Contract / pricing / ramp plan? |
| Refactoring | Is X worth refactoring? | Target code shape? | Resource allocation new vs refactor? |
| Process / methodology | Is process X net-positive? | Synthesized minimal viable process? | Adoption sequencing? |
| Hiring direction | Should we hire role X? | What does X actually look like? | Hiring sequence + budget? |

### What stays the same

- TOPIC.md framing
- Boot → R1 → R2 → R3 → verdict format
- Discipline rules (token, disk-first, ≥2 NEW WebFetch, anti-rubber-stamp)
- Critic-gate when transitioning to execution
- Convergence pattern recognition

### What adapts per topic

- Axes (3 for harness; might be 4 for service decomposition; etc.)
- External evidence sources (Anthropic + Cognition for harness; AWS / GCP / Postgres docs for database; etc.)
- Verdict structure emphasis (architectural for refactoring; financial for vendor; calendar for migration)

### Naming conventions

- Packet directory: `docs/operations/task_<YYYY-MM-DD>_<topic>/`
- Team name: `zeus-<topic>-debate-<date>` (or `<project>-<topic>-debate-<date>`)
- Teammate names: `proponent-<topic>` and `opponent-<topic>`
- Critic name (when used): `critic-<topic>`
- Executor name (when used): `executor-<topic>-fixes`

---

## §10 Templates appendix

### Template 1: TOPIC.md skeleton

See `docs/operations/task_2026-04-27_harness_debate/TOPIC.md` for the canonical example. Adapt core question + scope table + axes + sources to your topic.

### Template 2: Boot dispatch prompt

```
You are <role>-<topic> in team <team-name>. Judge: team-lead.

ROLE: <Defend | Attack | Argue X> as <position>.

Complete topic, scope, format, constraints in:
<absolute path to TOPIC.md>

This is BOOT-ONLY. Do NOT engage R1, do NOT WebFetch yet.

Sequence:
1. Read TOPIC.md and judge_ledger.md from packet dir
2. Read <repo authority docs>
3. Sample-read <N> manifests of YOUR choice
4. Read <relevant retrospective / review docs>
5. Write to evidence/<role>/_boot_<role>.md (≤300 lines, 4 sections)
6. SendMessage me exactly "BOOT_ACK_<ROLE> path=<abs>". Then idle.

DO NOT engage R1 substantively. DO NOT WebFetch in boot.

Constraints: ≤500 char/A2A; ≤200 char converged; ≥2 WebFetch per debate round; disk-first; file:line cites grep-verified within 10 min; sub-agent dispatch if WebFetch blocked.

You are LONG-LAST. Persist for follow-up rounds.
```

### Template 3: R-round dispatch prompt

```
ROUND-N PHASE-X DISPATCH (<intent>).

Build on <prior verdict> §X LOCKED concessions (CANNOT reopen). Use <specific TOPIC sections> as framing.

Your task:
- <specific items per role>
- Quantitative target where applicable
- ≥2 NEW WebFetch (no recycle from cumulative <N>): <suggest sources>
- LOCK FINAL POSITION at end (stands / partial-accept / move-toward-middle)

Engage opponent's STRONGEST direction at face value before pivoting.

Disk: evidence/<role>/round<N>_<phase>.md (≤350 lines). SendMessage me "ROUND<N>_<EVENT>_DONE_<ROLE> path=<abs>".
```

### Template 4: Critic gate dispatch

```
REVIEW_BATCH_X. Executor BATCH_X_DONE: <N> files (paths in their notification + git diff):
[list]

Tests: <X passed Y skipped Z failed>.
planning_lock: <result>.
<any bonus findings>

Per your boot §<N> attack vectors for BATCH X + caveat-fix carryovers from prior batch:
- <attack vector 1>
- <attack vector 2>
- ...

Write evidence/critic-<topic>/batch_X_review_<date>.md. SendMessage me "BATCH_X_REVIEW <APPROVE|REVISE|BLOCK> path=<abs>".
```

### Template 5: Verdict skeleton

See `docs/operations/task_2026-04-27_harness_debate/{verdict,round2_verdict,round3_verdict}.md` for canonical examples. Reuse §0-§8 structure verbatim.

---

## §11 ROI / when NOT to use this methodology

### High-ROI signals (use the methodology)

- Decision affects 100h+ downstream work
- Decision is hard to reverse
- Multiple competent engineers genuinely disagree on approach
- The "obvious" answer feels wrong but you can't articulate why
- A previous attempt to decide via single-thread analysis produced unsatisfying conclusions

### Low-ROI signals (skip — use single-shot critic instead)

- Decision is small or easily reversible
- Empirical data unambiguously settles the question
- Time pressure prevents structured cycle (~70 min minimum)
- Only one engineer involved (no "sides" to debate)
- Topic is purely aesthetic / style preference

### Single-shot alternative

If full debate is overkill, use:
1. Write the proposal in own context
2. Dispatch ONE critic-opus subagent with 10-attack template
3. Read critic's response
4. Decide based on critic verdict + own judgment

This is ~5 min vs ~70 min and works for ~80% of decisions.

---

## §12 Future extensions (when you have data + time)

After running this methodology N times, consider:

### E1: Empirical convergence speed metrics

Track: how many phases / how much elapsed before mutual cross-over occurs. Different topic types may have different convergence speeds. Build a calibration table.

### E2: Source database

Build a shared `docs/methodology/external_sources.yaml` cataloging external sources used across debates with: URL, date fetched, key quotes, applicable topics. Future debates can reference without re-fetching.

### E3: Topic-specific axis catalogs

For recurring topic types (architecture, vendor selection, migration), build a per-type axis-template so framing decisions are faster.

### E4: Promotion to OMC skill

The methodology can become a `/oh-my-claudecode:adversarial-debate` skill that auto-dispatches the full cycle. Trigger keywords: "evaluate", "should we", "is it worth", "prioritize", "synthesize", "settle".

### E5: Cross-project portability

The methodology is project-agnostic. Promote to `~/.claude/skills/adversarial-debate/SKILL.md` for cross-project use. Other Zeus / Mars / Neptune projects can invoke the same playbook.

---

## §13 Lineage + provenance

This methodology was distilled from the harness-debate cycle 2026-04-27 in this repository. The full debate artifacts at `docs/operations/task_2026-04-27_harness_debate/` serve as the canonical worked example. Key empirical observations that shaped the methodology:

- 3 rounds × ~22 min each = ~70 min total — the format scales well
- Mutual cross-over in BOTH R2 and R3 — convergence is achievable when discipline holds
- 11 cumulative external sources across 3 rounds — sustainable WebFetch cadence
- 2 SendMessage drops + 1 task-list pollution + 1 path drift — failure modes are recoverable
- 0 anti-rubber-stamp violations — discipline scales when baked into prompts
- Critic-gate added during execution phase caught 2 non-blocking caveats — gate is load-bearing

This methodology is itself a candidate for future debate ("is this methodology net-positive vs single-shot critic?") — but only after it has been used N≥3 times on different topics, so empirical data exists.

---

## §14 Maintenance + governance

- This doc is **living**: update after each debate cycle with new lessons
- Cite this doc in future debate TOPIC.md files: `Methodology source: docs/methodology/adversarial_debate_for_project_evaluation.md`
- When adding new failure modes (§6) or convergence patterns (§7), append rather than rewrite
- Quarterly review: are the discipline rules (§4) still load-bearing or has any become ritual?
- When promoting to OMC skill (§12 E4), maintain the skill version of this doc as the authoritative source

---

End of methodology.
