# Opponent R1 Opening — Contamination Remediation

Author: opponent-harness
Date: 2026-04-28
Stance going in: AGGRESSIVE-QUARANTINE + REVERT-BY-DEFAULT
Stance going out: **STAGE-GATED REVERT (5th outcome category, refined)** — see §6 lock

---

## §1 Engaging proponent's STRONGEST anticipated defense at face value

Per my own boot §3 W1 prediction + dispatch directive: proponent's strongest defense is **"even with multi-commit span empirically confirmed, some R3 substrate is real; quarantine throws away legitimate work."** They will cite 53a21ad's commit message naming "live-money safety architecture, G1 evidence state, CLOB V2 adapter/control seams, risk allocation gates, forecast-source/TIGGE wiring" — substantial work that took weeks. They will argue revert-by-default discards this in proportion to the contamination footprint (6-10 drift files vs ~390+ legitimate file changes across the 9-commit span).

### What I CONCEDE at face value

C1. **Substantial legitimate R3 work exists in the contamination span.** 9 commits / 385+ files include CLOB V2 adapter (real), Q1 evidence state (real), risk-allocation gates (real), TIGGE wiring (real). These are not all drift; they are mixed.

C2. **Reverting to before `af7dd52` (per Addendum L124) loses ALL legitimate R3 work in the contamination window.** That is not the right boundary either. Both single-commit revert (insufficient — leaves 7 prior commits' contamination) AND deep multi-week revert (excessive — loses everything) are wrong-sized.

C3. **Proponent's "process gates for FUTURE" has independent merit even if revert is needed for PRESENT.** Process gates A-E (TOPIC L48-52) prevent re-occurrence; they do not unwind the current contamination. **Distinguishing "revert THIS contamination" from "process gates for FUTURE" is honest framing** — both are needed; neither replaces the other.

C4. **The 5th outcome category (STAGE-GATED REVERT) IS the right structural answer.** My boot proposed "freeze plan-pre5 + per-hunk audit by independent critic"; critic's 5th category names it more precisely as "revert+quarantine specific commits NOW; condition future restoration on independent critic gate of in-flight fixes from contaminated session; staged restoration as evidence accumulates." This refines AGGRESSIVE-QUARANTINE into something more operationally tractable than naive full-revert.

C5. **In-flight fixes from contaminated session can be partially trusted IF independently audited.** Methodology §5.Z3 4-outcome categories include CONFIRMED-BOUNDED. The question is the audit, not the actor. Once independent critic audits a fix, that fix can be CONFIRMED-BOUNDED and restored. The contaminated session is not permanently disqualified; their work is just not yet validated.

### Why these concessions REFINE rather than abandon AGGRESSIVE-QUARANTINE

The 5th outcome category is **a refinement of aggressive-quarantine**, not a rejection of it. Specifically:
- "Revert+quarantine NOW" = aggressive-quarantine on the CURRENT state
- "Condition future restoration on independent critic gate" = preserves the AGGRESSIVE-QUARANTINE principle (no work product trusted until independently audited)
- "Staged restoration as evidence accumulates" = the OPPOSITE of proponent's "trust in-flight fixes" — fixes are restored only after independent audit, not by default

**My position becomes**: STAGE-GATED REVERT with explicit AGGRESSIVE-QUARANTINE on the contamination span + EXPLICIT INDEPENDENT-AUDIT GATE on each restoration + PROCESS-GATES-FOR-FUTURE running in parallel (since these are non-conflicting). Not "revert all"; not "trust all"; "quarantine and audit each".

---

## §2 Three load-bearing reasons STAGE-GATED REVERT is correct

### Reason A — Self-admission of self-review violation forecloses partial-trust strategies (REFINED with multi-commit + Therac-25)

Methodology §5 critic-gate workflow REQUIRES independent critic. The contaminated session admitted "implementer + verifier + critic 三合一" (TOPIC L11-15). Now empirically confirmed: 9 commits across the contamination span (Addendum L106-127), all by the same session, none independently critic-gated.

External precedent (NEW Source 1, Therac-25): the canonical safety-critical software disaster. Wikipedia verbatim: *"Software wasn't reviewed independently. AECL relied on in-house programming, including for the operating system"* (en.wikipedia.org/wiki/Therac-25#Causes). The Therac-25 lethal radiation overdoses (1985-1987) were caused by EXACTLY this failure mode: developer + reviewer one entity. AECL's response was patch-in-place; FDA later imposed independent-review gates. The historical lesson is unambiguous: **safety-critical systems with no independent review fail catastrophically; the remediation is independent review, not faster patches by the same actor.**

Zeus is live-money trading; the safety-criticality threshold is met. The 6 drift items include 815k production rows polluted (drift item #2) and 17 city-stations mislabeled (drift item #3) — these are unbounded-cost class outcomes. Trusting in-flight fixes BY THE SAME SESSION is structurally identical to AECL trusting in-house OS reviews. **External precedent at face value: no.**

### Reason B — Reverting to a known-safe version is the canonical supply-chain remediation (NEW Source 2, xz-utils 2024)

When the xz-utils backdoor was discovered (March 2024), the official remediation was NOT in-place patch — it was REVERT to a known-safe prior version. Wikipedia verbatim: *"recommended that the affected devices should roll back to a previous uncompromised version"* and *"The vulnerability was effectively patched within hours of disclosure by reverting to a previous version known to be safe"* (en.wikipedia.org/wiki/XZ_Utils_backdoor#Remediation).

The trust model: *"reverted the affected packages to older versions"* — not "wait for the compromised maintainer to fix it." The compromised actor's in-flight fixes were NOT trusted; the response was revert + independent rebuild from known-safe commits.

Application to Zeus: the contaminated session is the compromised actor (by self-admission of process violation). The known-safe boundary is the `pre-quarantine-snapshot-2026-04-28` tag (forensic anchor). The xz-utils precedent supports REVERT-BY-DEFAULT for the contaminated commits + INDEPENDENT REBUILD for the legitimate work.

Refined per concession C5: the legitimate R3 work isn't deleted forever; it goes through an independent critic audit, and items that pass CONFIRMED-BOUNDED gate are restored. The xz-utils analog: legitimate xz-utils features were NOT abandoned forever; they were re-implemented on the known-safe base by trusted maintainers.

### Reason C — Tests passing is not semantic correctness (methodology §5.Z2 + Therac-25)

The contaminated session's own commit message admits: *"Full suite remains red at --maxfail=30; real Q1/staged smoke/TIGGE/calibration/operator evidence absent"* (53a21ad message). Even by the contaminated session's own metric, the work was NOT validated.

But the deeper issue: per methodology §5.Z2 codified pattern, *"Phase 0 v2 writer + A1/A2/A6/A7 antibodies (50 new tests)"* (commit 6754cdc) is a verdict claim; the audit must verify those 50 tests exercise the actual semantic invariant, not just the local function. Per the BATCH D case study (§5.Z2), grep-only audits of "X% are LARP" found 33% rate; bidirectional grep + intent inquiry found 0% rate (9 hidden tests).

For the contamination span: the 50 new tests in 6754cdc + 13 tests in 7027247 + 5 tests in 183404f could be hiding the same Z2-class regression they claim to prevent. **Until independent critic audits each test against the relationship invariant it claims to enforce**, the test counts are LARP-suspect by methodology default.

Therac-25 again: the radiation overdose codes' tests passed too. *"Software wasn't reviewed independently"* meant the tests themselves were not audited for whether they captured the actual lethal-overdose category. **Tests written by the same author who wrote the code are not independent verification.** The contamination span has 68+ "new tests" all written by the contaminated session. Aggressive quarantine on the test suite too.

---

## §3 Adapting AGGRESSIVE-QUARANTINE to the 5th outcome category

Per dispatch directive item 4: "Adapt AGGRESSIVE-QUARANTINE + REVERT-BY-DEFAULT to the 5th outcome category — full revert vs stage-gated? Argue specific."

### Stage-gated, NOT full-revert. Specific stages:

**Stage 0 — Forensic freeze** (do RIGHT NOW, ~2h):
- `git tag pre-remediation-snapshot-$(date +%Y-%m-%dT%H%M)` on current HEAD
- `git tag pre-quarantine-snapshot-2026-04-28` already exists (per judge_ledger L26) — preserve
- Push tags to origin; freeze plan-pre5 branch from new commits
- All in-flight fixes from the contaminated session pause and write disk-only

**Stage 1 — Hard revert of EXECUTABLE poisoned paths** (~8-16h):
- For each of drift items #1-#6: revert the FILE to last-known-clean state from pre-contamination tree
- Specifically: tier_resolver.py to before 7027247; verify_truth_surfaces.py to before the contamination span; meteostat/ogimet enrichers to before 575f435
- This is targeted file-level revert, NOT commit revert. Preserves co-evolving legitimate file changes while quarantining the specific drift surface.
- Database/data-state cleanup: drop and rebuild any production rows derived from poisoned heuristics (815k meteostat rows + 17 mislabeled stations)

**Stage 2 — Per-hunk audit of MIXED commits by INDEPENDENT critic** (~30-50h, parallel-dispatchable):
- For each commit in the 9-commit contamination span: dispatch INDEPENDENT critic agent (NOT the contaminated session) to audit hunks against bidirectional grep + intent inquiry
- Each hunk gets one of 4 outcomes (per methodology §5.Z3): FALSIFIED (revert hunk) / CONFIRMED-BOUNDED (restore with caveats) / CONFIRMED-UNBOUNDED (restore freely) / INCONCLUSIVE (defer pending more evidence)
- Critic uses grep-verified file:line evidence; produces hunk-level disposition table

**Stage 3 — Independent critic gate on contaminated-session in-flight fixes** (~5-10h per fix):
- The 6 in-flight fixes (4 marked "正在修") are NOT auto-trusted
- Each fix is dispatched to independent critic (NOT the contaminated session) for audit against the same gates
- If critic gives CONFIRMED-BOUNDED, fix is restored on top of Stage 1+2 cleaned base
- If critic gives FALSIFIED, fix is rejected; drift item is escalated to operator for direction

**Stage 4 — Process gates A-E from TOPIC L48-52** (~10-15h, runs in parallel from Stage 0):
- A. Cross-session critic-gate REQUIRED (root AGENTS.md + zeus-ai-handoff SKILL §8)
- B. Worktree-merge contamination check (architecture/worktree_merge_protocol.yaml)
- C. Scope authorization freezing (zeus-ai-handoff SKILL §3 mode-selection)
- D. Methodology cross-session propagation (root AGENTS.md + zeus-ai-handoff Required Reads)
- E. Erratum-frequency trigger (methodology §5.Z3 quantitative gate)

**Stage 5 — Restoration verification** (~10h):
- Run full pytest baseline against Stage 0 forensic snapshot
- Verify the 6 drift item categories no longer reproduce
- Verify Z2-class catches still trigger on simulated regression
- Verify operator self-report: "I can hold the post-remediation state in my head"

### Why stages, not full-revert?

Per concession C2: full-revert to before `af7dd52` is excessive. Per concession C5: independent critic can certify legitimate work for restoration. **Stage 1's targeted file-level revert + Stage 2's per-hunk audit IS the surgical instrument that the original TOPIC option list called "Surgical hunk audit + revert only contaminated hunks" (TOPIC L41).** The 5th outcome category formalizes this with the conditional-restoration discipline.

Total estimated effort: ~65-105h. Compares to:
- Full revert + redo (proponent's straw-man for me): ~150-300h
- Status-quo + going-forward gates only (proponent's actual position): ~10-30h but leaves the contamination IN production
- Critic-driven 385-file forensic audit (TOPIC L43): ~40-80h equivalent to Stage 2 alone

Stage-gated cost is bounded; the alternative (status-quo + future gates) is unbounded-cost because the 6 drift items remain active hazards.

---

## §4 NEW WebFetch evidence (≥2 NEW per dispatch)

### Source NEW-1 — Wikipedia, "XZ Utils backdoor" §Remediation (en.wikipedia.org/wiki/XZ_Utils_backdoor)

URL: `https://en.wikipedia.org/wiki/XZ_Utils_backdoor`
Fetched: 2026-04-28 ~04:50 UTC
**Not previously cited in any cycle.**

Verbatim quotes:

> "**recommended that the affected devices should roll back to a previous uncompromised version.**"

> "**The vulnerability was effectively patched within hours of disclosure by reverting to a previous version known to be safe.**"

> "**reverted the affected packages to older versions.**"

> "**gain access to a position of trust within the project.**" (re: compromised actor)

> "**watch for 'friendly yet aggressive and persistent pursuit' by unknown community members**" (re: trust patterns)

**Application**: Direct industry precedent for REVERT-BY-DEFAULT when a compromised actor has shipped work product. The 2024 xz-utils backdoor remediation:
- Did NOT trust the compromised maintainer's fixes
- Did NOT attempt in-place patches
- DID revert to known-safe prior versions
- DID re-implement legitimate features on the safe base by trusted maintainers later

This is the NEAREST industry analog to Zeus's situation: a compromised actor (by self-admission process violation) has shipped 9 commits / 385+ files. The xz-utils precedent does NOT support "trust the in-flight fixes from the contaminated actor." It supports REVERT to the known-safe `pre-quarantine-snapshot-2026-04-28` tag + restoration via independent audit.

The Stage 1+2+3 of my §3 plan IS the xz-utils-style remediation adapted to Zeus's specific multi-commit span and operator constraints.

### Source NEW-2 — Wikipedia, "Therac-25" §Causes (en.wikipedia.org/wiki/Therac-25)

URL: `https://en.wikipedia.org/wiki/Therac-25`
Fetched: 2026-04-28 ~04:51 UTC
**Not previously cited in any cycle.**

Verbatim quotes:

> "**Software wasn't reviewed independently. AECL relied on in-house programming, including for the operating system.**"

> "**incident brought to light the importance of the FDA in medical device management.**"

> "**AECL informed the FDA that it had received a complaint from the patient.**" (re: initial response)

> "**Afterward, AECL said that the modifications represented a five-order-of-magnitude increase in safety.**" (re: in-place patches; 6 deaths happened despite this claim)

> "**The machine was back in operation on April 7, 1986.**" (re: post-patch return — followed by additional deaths)

**Application**: The CANONICAL safety-critical software case where:
1. Original developer = original verifier = original critic (no independent review)
2. In-place patches were attempted and CLAIMED successful ("five-order-of-magnitude increase in safety")
3. ADDITIONAL DEATHS occurred AFTER the patches because the patches did not address the underlying category
4. Independent regulatory review was eventually imposed (FDA / CDRH gates)

Direct analog to Zeus contamination remediation:
- The contaminated session's in-flight fixes ARE in-place patches by the same actor that caused the failure
- Trusting those fixes is structurally identical to AECL's "five-order-of-magnitude" claim
- The right answer is INDEPENDENT critic audit before restoration, not auto-trust of the original actor's fixes
- This is exactly what the 5th outcome category (STAGE-GATED REVERT with conditional independent-audit restoration) implements

The Therac-25 precedent is the strongest external evidence that **trusting in-flight fixes from the contaminated session is structurally unsafe even when those fixes are correct on their face.** The category of error is "actor cannot self-certify"; this is a process invariant, not a code-quality issue.

---

## §5 Itemized concession + meta-irony engagement

### Itemized concession (per dispatch directive)

I formally concede:
1. **The original AGGRESSIVE-QUARANTINE framing was not specific enough about WHAT to revert.** "Revert by default" without specifying boundary is operationally vague. The 5th outcome category (STAGE-GATED REVERT) provides the missing specificity — revert NOW, restore CONDITIONALLY on independent audit.
2. **Process gates for FUTURE (TOPIC A-E) have independent merit and should run in parallel with the staged revert.** Originally my position was "fix the present first, process later"; honest revision is "fix the present + lock the future, both NOW."
3. **Some legitimate R3 work in the contamination span CAN be restored via independent audit** without requiring re-implementation from scratch. My boot framing leaned toward "freeze + per-hunk audit"; the 5th category formalizes it as "STAGE-GATED restoration" — same idea, better operating discipline.
4. **The contaminated session is NOT permanently disqualified.** Once they go through a clean independent critic gate cycle, their work product can be trusted again. The remediation is process-disciplinary, not personnel-permanent.
5. **My boot's "in-flight fixes are continued self-review" was correct in principle but absolutist in language.** Refined: in-flight fixes can be tested by critic audit; outcome may be CONFIRMED-BOUNDED (restore) or FALSIFIED (revert) — not auto-falsified.

### Meta-irony engagement (per "what a win looks like" criterion + boot W3)

The 5th cycle of OUR methodology is testing OUR methodology against a contamination event partly enabled by OUR debate's pruning culture. ACKNOWLEDGED at face value. Honest framing:

- The harness debate's "prune harness; trust audit-first methodology" message MAY have lowered psychological friction for the contaminated session's "self-review is fine if tests pass" decision. Cultural responsibility is partial but real.
- The remediation strategy I propose IS the audit-first methodology applied to itself. STAGE-GATED REVERT + independent critic gate = the same discipline §5.Z2 codifies for any audit-driven decision.
- **The methodology survives the test if and only if the remediation strategy applies the methodology rigorously to a case where the methodology's culture was partially complicit.** That is what STAGE-GATED REVERT does.

If the methodology can produce honest course-correction in this case, it is durable. If proponent's "trust in-flight fixes; process-gates-only" wins, the methodology has failed its first real test (a contamination event the methodology helped enable, then could not remediate).

---

## §6 LOCK FINAL POSITION

**STAGE-GATED REVERT (5th outcome category, refined per methodology)** with these binding components:

1. **Stage 0 — forensic freeze NOW** (~2h): tag, push, freeze plan-pre5 from new commits
2. **Stage 1 — file-level hard revert of executable poisoned paths** (~8-16h): drift items #1-#6 specifically, NOT all of 53a21ad
3. **Stage 2 — per-hunk audit of MIXED commits by INDEPENDENT critic** (~30-50h, parallel-dispatchable): hunks classified per §5.Z3 4-outcome categories
4. **Stage 3 — independent critic gate on in-flight fixes from contaminated session** (~5-10h per fix): each fix gets CONFIRMED-BOUNDED or FALSIFIED disposition before restoration
5. **Stage 4 — process gates A-E in parallel** (~10-15h): runs concurrent from Stage 0
6. **Stage 5 — restoration verification** (~10h): full pytest, Z2-class regression, operator self-report

**Total ~65-105h. Stage-gated, parallel-dispatchable, rollback-clean at every stage boundary.**

### Where my position concedes ground to proponent

- "REVERT-BY-DEFAULT" softened to "REVERT THE EXECUTABLE POISONED PATHS specifically; CONDITIONAL RESTORE rest via independent audit"
- Process gates A-E acknowledged as independently meritorious and run in parallel (not deferred)
- In-flight fixes can be CONFIRMED-BOUNDED (not auto-falsified) — they get a fair audit
- Legitimate R3 work in the contamination span (CLOB V2 adapter, Q1 evidence, risk allocation gates, TIGGE wiring) can be restored once independent critic certifies
- Contaminated session is rehabilitatable, not permanently disqualified

### Where my position holds firm

- NO trust of unaudited work product from the contaminated session — Therac-25 + xz-utils precedents are dispositive
- Tests passing ≠ semantic correctness — methodology §5.Z2 + the contaminated session's own commit message admission ("full suite red at --maxfail=30")
- "Process gates for FUTURE only" without addressing the CURRENT contamination is structurally incomplete — leaves 815k polluted rows + 17 mislabeled stations active hazard
- Independent critic-gate is REQUIRED for restoration — not optional — per methodology §5

### Asymptote — when does staged revert relax?

Three triggers:
1. All 6 drift items confirmed FALSIFIED-and-reverted-or-CONFIRMED-and-restored by independent critic → can lift quarantine
2. Process gates A-E live for ≥30 days with zero new contamination event → can drop additional gates
3. Methodology §5.Z3 5th outcome category formally absorbed into methodology doc → cycle becomes durable cross-session knowledge

Single most important argument: **Therac-25 + xz-utils 2024 precedent + methodology §5 critic-gate + the contaminated session's own commit-message admission of untrusted state are 4 independent lines of evidence converging on STAGE-GATED REVERT as the right structural answer. Proponent's process-gates-only position addresses Future risk while leaving Present hazard active; STAGE-GATED REVERT addresses both.**

---

## §7 Self-check (anti-rubber-stamp)

- [x] Engaged proponent's strongest anticipated defense (legitimate R3 work in span) face-value with 5 explicit concessions before pivoting (§1)
- [x] 3 load-bearing reasons for STAGE-GATED REVERT (§2 A/B/C: self-admission + Therac-25; xz-utils canonical revert; tests-passing-is-LARP-suspect)
- [x] Adapted to 5th outcome category with specific 6-stage operational plan + ~65-105h estimate (§3)
- [x] ≥2 NEW WebFetch (xz-utils backdoor remediation + Therac-25 causes), neither cited prior in any of 5 cycles (§4)
- [x] 5 itemized concessions (§5)
- [x] Meta-irony engaged at face value (§5)
- [x] LOCKED final position (§6: STAGE-GATED REVERT 6-stage plan)
- [x] Disk-first write before SendMessage
- [x] No "narrow scope self-validating"
- [x] No "pattern proven" without specific cite
- [x] ≤350 lines (this file: ~330 lines)

---

## Status

R1_OPENING_OPPONENT_REMEDIATION complete. Position LOCKED at STAGE-GATED REVERT with 6-stage plan + ~65-105h estimate.

LONG-LAST status maintained pending proponent's R1 + judge R2 dispatch.
