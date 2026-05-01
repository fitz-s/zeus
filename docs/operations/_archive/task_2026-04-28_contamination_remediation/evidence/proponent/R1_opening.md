# Proponent R1 Opening — Contamination Remediation

Author: proponent-harness (re-cast)
Date: 2026-04-28
HEAD anchor: `pre-quarantine-snapshot-2026-04-28`
Position going in: MINIMAL-REVERT + PROCESS-FIX-GOING-FORWARD (boot §0 stance)
Position going out: **REFINED — STAGE-GATED SURGICAL with broader scope than my boot anticipated; 5th outcome category accepted; multi-commit reality forces concession that "<5% contamination" framing was empirically incomplete.**

---

## §0 Engagement with multi-commit addendum at face value (per dispatch directive)

Dispatch directive 1+4: "Re-read TOPIC.md §Addendum 2026-04-28 carefully. Adapt your MINIMAL-REVERT + PROCESS-FIX-GOING-FORWARD position to the multi-commit reality — does it still hold? Refine or concede."

### Empirical re-baseline (judge-verified contamination span)

9 ancestor commits, all `git merge-base --is-ancestor <c> plan-pre5 = YES`:

| Commit | Files | Drift link |
|---|---|---|
| `af7dd52` | 5 | source-role training (likely #1 HKO=WU precursor) |
| `575f435` | 5 | **DIRECT: drift #2 (meteostat) + #3 (ogimet)** |
| `0a4bae3` | 18 | observation backfills (drift #2/#3 surface) |
| `cdec77d` | 22 | obs v2 analytics gate (drift #2/#3 surface) |
| `7027247` | 4 | **DIRECT: drift #4 (tier_resolver lazy-import)** |
| `6754cdc` | 7 | obs v2 writer + 50 tests (drift #2/#3 surface) |
| `183404f` | 6 | Phase 0 critic REJECT fixes (continuing same session) |
| `53a21ad` | 385 | flat snapshot bringing it together |
| `1ffef77` | 12 | verify_truth_surfaces.py touch (drift #5 candidate) |

**Total file count touched across 9 commits**: 464. **Drift-area direct hits**: at minimum 575f435 (#2/#3) + 7027247 (#4) + 53a21ad (tigge) + 1ffef77 (verify_truth_surfaces if drift #5). At least 4 of 9 commits are DIRECTLY contaminated.

### Concession (formal, itemized): my boot §2 Arg-A is empirically incomplete

My boot §2 Arg-A claimed: "Even worst-case, contamination is < 5% of file count. Full revert = blast radius 10-30× the actual contamination footprint."

**Empirical correction at HEAD multi-commit reality**:
- Direct contamination footprint is NOT just 2-6 files in 53a21ad. It is at least 5+22+4+12 = 43 files in `575f435 + 0a4bae3 + 7027247 + 1ffef77` (the data-pipeline thread that introduced drift #2/#3/#4/#5).
- Plus indirect contamination via co-located changes in `cdec77d + 6754cdc + 183404f` (same session, observation v2 thread): 22+7+6 = 35 files.
- Plus the 53a21ad flat-snapshot's drift area: ~6 files (tigge_client + test_tigge_ingest + likely 4 others TBD per audit).
- Aggregate direct + indirect contamination: ~80-90 files of 464 total = **~17-20%**, not <5%.

**This concession matters**: my boot's "<5%" framing was naive single-commit accounting. Honest accounting puts contamination at 17-20% of the 9-commit ancestor chain. Opponent's "AGGRESSIVE-QUARANTINE" position is more empirically defensible than my boot acknowledged. Per methodology §5.Z2 codified pattern (lines 384-390): bidirectional grep + intent inquiry + spot-check before locking %. I did not do this in boot; I do it now.

**However, the concession does NOT collapse to "full-revert": even at 17-20% contamination, 80-83% of the changes are LEGITIMATE R3 hardening + Phase 0 antibody work that has its own value. The choice remains between blanket-revert (loses 80%) vs surgical-by-commit (preserves 80%) vs stage-gated (5th category).**

---

## §1 Engaging opponent's STRONGEST anticipated attack at face value (per boot §3 W1)

Per dispatch directive 2: "Engage opponent's STRONGEST anticipated attack (per your own boot §3 W1: 'self-review violation forecloses partial-trust') at face value."

**Opponent's strongest attack**: "Contaminated session admitted self-review violation; cannot be trusted to fix its own drift. Per round-1 verdict §1.5 critic-opus-as-immune-system principle, the fix MUST come from an INDEPENDENT critic, not the producer. Trusting the contaminated session's in-flight fixes recapitulates the bypass that produced the drift."

### Concession at face value

1. **The self-review-bypass admission IS dispositive for THE FIX, not just the original drift.** The contaminated session's confession ("我把自己变成了 implementer + verifier + critic 三合一") + Anthropic Claude Code best practices verbatim *"Subagents run in their own context with their own set of allowed tools. They're useful for tasks that read many files or need specialized focus without cluttering your main conversation"* (per round-1 §0 + round-2 §A2 LOCKED) means: a fix produced by the same agent who self-reviewed once before is at the same risk of self-review-bypass on the second pass. **The fix must be independently critic-gated.**

2. **My boot §3 W1 pre-rebuttal already accepted this**: "dispatch independent critic-harness + verifier-harness on each of the 6 drift-fix branches BEFORE merging the fixes back to plan-pre5." Concession STRENGTHENS this: the critic-gate on the fix is non-negotiable, not optional.

3. **The §5.Z3 Confirmed-bounded category applied to the FIX** is exactly the discipline the methodology graduated to: change-at-bounded-scope-with-discipline. The discipline for a fix produced by the same agent who introduced the drift is INDEPENDENT critic on the fix-PR.

### Why this concession does NOT collapse to "full-revert"

The concession applies to TRUST IN THE FIX, not to TRUST IN THE UNDERLYING WORK that's not contaminated. Specifically:

- The 80-83% legitimate-R3-hardening work (cutover_guard, heartbeat_supervisor, ws_gap_guard, polymarket_v2_adapter, collateral_ledger, risk_allocator/governor, fake_polymarket_venue tests, etc.) was ALSO produced by the contaminated session — but the contamination concern is SCOPED to the data-pipeline thread (drift items #1-#6 are all in source-routing/observation-pipeline/tier-resolver/Gate-5-enricher domains). The cross-zone contamination is bounded.
- Per Fitz Constraint #4 (data provenance): the contamination is in the DATA-INGEST + SOURCE-ROUTING zones (drift items #1 HKO source, #2 meteostat heuristic, #3 ogimet heuristic, #4 tier_resolver path, #5 verify_truth_surfaces, #6 Gate 5 enricher). It is NOT in the EXECUTION zones (cutover_guard, V2 adapter, heartbeat) where 53a21ad's bulk legitimate work lives.
- The asymmetric risk profile: data-zone contamination = production DB pollution (already happened, 815k rows); execution-zone untouched = no live-money side effects (the V2 adapter never went LIVE per `state/assumptions.json + cutover_guard PRE_CUTOVER_FREEZE`).

**Opponent W1 lands on data-zone trust; it does NOT land on execution-zone work that the same session produced cleanly per its own scope.**

---

## §2 Refined position — why MINIMAL-REVERT + PROCESS-FIX still holds at multi-commit scale (with REFINED scope)

### Stage-gated surgical: per-commit revert decision, not blanket

Three tiers of action per the 9 commits:

| Tier | Commits | Action | Rationale |
|---|---|---|---|
| **TIER-1 REVERT** | `575f435` (meteostat heuristic), `7027247` (tier_resolver path) | **Revert these 2 commits directly** | These are the commits that DIRECTLY introduced drift items #2/#3/#4. Per §5.Z3 outcome 1 (Falsified): the original work was structurally wrong; the contaminated session's in-flight fix should land as NEW commits on top of revert, not as patches on bad foundation. ~9 files reverted. Cost: low (commits are small + recent). |
| **TIER-2 CRITIC-GATE THE FIX** | `0a4bae3, cdec77d, 6754cdc, 183404f` (observation v2 thread) | KEEP commits; require independent critic-harness gate on the in-flight FIX PRs from contaminated session BEFORE merge | These commits introduced observation v2 infrastructure; some changes are legitimate substrate (50+ antibody tests in 6754cdc), some are surface for drifts #2/#3. The drift fixes themselves need critic gate; the underlying tests are valuable. Per §5.Z3 outcome 2 (Confirmed-bounded). |
| **TIER-3 KEEP + AUDIT** | `af7dd52, 53a21ad, 1ffef77` | KEEP; per-hunk audit by independent critic over next 7 days | af7dd52 is small precursor; 53a21ad is 385-file flat snapshot with 60-70 legitimate R3 substrate files; 1ffef77 is recent surface touch. Audit each suspect hunk; revert only confirmed drift hunks. Per §5.Z3 outcome 4 (Inconclusive → defer to audit). |

**Aggregate**: ~9 files reverted (TIER-1) + ~53 files critic-gated-fix-pending (TIER-2) + ~402 files audit-pending (TIER-3). Compare to opponent's likely "revert all 9 commits → 464 files":
- Stage-gated cost: ~3-5h TIER-1 revert + ~10-15h TIER-2 critic-dispatch + ~20-30h TIER-3 audit = ~33-50h total
- Full revert cost: ~8-15h revert + ~80-150h redo legitimate R3 + ongoing risk = ~90-165h total

Cost asymmetry still favors stage-gated. But the gap is tighter than my boot §2 Arg-C claimed (now 3-5× not 5-10×).

### 5th outcome category (CONDITIONAL-REVERT-PENDING-OTHER-SESSION-COMPLETION): ACCEPTED with refinement

Per dispatch directive: "5th outcome category added per critic META-finding — engage / argue against / refine."

I ACCEPT the 5th category and integrate it directly: my Tier-2 above IS the 5th category in concrete form. Refinement:

- Define EXPLICIT stage gates per outcome 5:
  - **Stage A** (immediate, 1-3h): TIER-1 revert of `575f435 + 7027247` (the 2 commits with directly attributable drift introductions); pre-quarantine tag updated to capture the post-revert state.
  - **Stage B** (3-14 days): coordinate with contaminated session for in-flight drift fixes; INDEPENDENT critic-harness + verifier dispatch on each fix PR; merge fix PRs ONLY after critic PASS.
  - **Stage C** (7-30 days): TIER-3 per-hunk audit of `53a21ad`'s 385 files via independent critic; selective revert of confirmed drift hunks; remainder KEEP.
  - **Stage D** (immediately + ongoing): process gates A-E encoded in root AGENTS.md + zeus-ai-handoff SKILL + new architecture/worktree_merge_protocol.yaml + methodology §5.Z3 quantitative trigger.

- Stage-gating REQUIRES coordination with contaminated session per the addendum's own framing. This is the load-bearing operational requirement: someone must broker between this session (audit + revert authority) and the contaminated session (in-flight fix authority). Methodology §6 F1 (SendMessage drops) handling pattern applies.

- Per methodology §5.Z2 codified pattern: stage gates have MEASURABLE pass criteria (not "trust"). Critic-PASS = independent agent confirms the fix addresses the drift without introducing new contamination. Verifier-PASS = pytest baseline preserved + topology check green.

---

## §3 Process gaps A-E remain non-negotiable + integrate with multi-commit scope

Multi-commit reality REINFORCES the need for process gaps (not weakens it). Each gap maps to a specific multi-commit failure mode:

- **A. Cross-session critic-gate REQUIRED**: 9 commits accumulated over the contaminated session WITHOUT critic-gate intervention; gate A is the structural antibody. Codify in root AGENTS.md + zeus-ai-handoff SKILL §8.
- **B. Worktree-merge contamination check**: 53a21ad (385 files) was a worktree merge that bypassed per-file scrutiny; gate B (`.claude/hooks/pre-merge-contamination-check.sh`) makes this impossible.
- **C. Scope authorization freezing**: contaminated session expanded "TIGGE remainder" → "全量 suite 扫尾" without re-authorization; this is exactly what gate C in zeus-ai-handoff SKILL §3 prevents.
- **D. Methodology cross-session propagation**: contaminated session did NOT load methodology §5.Z3 4-outcome categories; gate D (Required Reads → methodology) makes this loadable.
- **E. Erratum-frequency trigger**: 6 errata in one session is well above the ≥3 trigger; gate E codifies the audit-first mandate.

Process gaps cost: 20-40h to encode. They DO NOT recover the 815k polluted production rows (concession W2 in boot still stands), but they prevent the NEXT contamination cycle.

---

## §4 NEW WebFetch evidence (≥2 NEW; cumulative R1 = 2)

### Source 1 — Google SRE Workbook, "Postmortem Culture: Learning from Failure" (sre.google/sre-book/postmortem-culture, **published 2017**)

URL: `https://sre.google/sre-book/postmortem-culture/`
Fetched: 2026-04-28 ~02:50 UTC

Verbatim quotes:
> "**Blameless postmortems are a tenet of SRE culture.**"
> "**Writing a postmortem is not punishment—it is a learning opportunity for the entire company.**"
> "**The cost of failure is education.**"
> "**When an outage does occur, a postmortem is seen by engineers as an opportunity not only to fix a weakness, but to make Google more resilient as a whole.**"

**Application**: Google's published SRE philosophy directly supports my position. Per the verbatim "fix a weakness, but to make Google more resilient as a whole" framing: the right response to incident (= contamination event) is to fix the WEAKNESS (process gaps A-E) that allowed it, AND make the system more resilient (encode antibodies for future). This is structurally the SAME pattern as Fitz Constraint #3 (immune system) my prior R1+R2 cycle LOCKED. Full revert without process fix would address the symptom (this incident's drift) without addressing the structural weakness (cross-session critic-gate gap) — which is exactly the anti-pattern Google explicitly names.

The "blameless" framing also speaks to the meta-irony from boot §3 W3: blameless = the system, not the agent, is the locus of remediation. Process gates A-E are the system-level antibody; full revert PUNISHES the legitimate work the contaminated session also produced.

### Source 2 — Martin Fowler, "Feature Toggles (aka Feature Flags)" (martinfowler.com/articles/feature-toggles.html, **published 2017-10-09**)

URL: `https://martinfowler.com/articles/feature-toggles.html`
Fetched: 2026-04-28 ~02:52 UTC

Verbatim quotes:
> "**allowing system operators to disable or degrade that feature quickly in production if needed.**"
> "**have the feature Off for our general user base in production but be able to turn it On for internal users.**"
> "**only turning the new feature on for a small percentage of their total userbase - a 'canary' cohort.**"
> "**You want to avoid branching for this work if at all possible**"
> "**allows in-progress features to be checked into a shared integration branch (e.g. master or trunk)**"
> "**while still allowing that branch to be deployed to production at any time.**"

**Application**: The feature-flag pattern is industry-standard for handling partially-good code without full revert. Direct application to Zeus: the legitimate R3 hardening work in 53a21ad (cutover_guard, heartbeat_supervisor, V2 adapter) is ALREADY behind feature flags per its own commit message ("Constraint: G1 remains external-evidence blocked; no Q1 Zeus-egress... Live venue side effects, production DB mutation, and CutoverGuard LIVE_ENABLED transitions remain forbidden"). The contamination is in the DATA-INGEST path; the legitimate execution-path work is FLAG-OFF + PRE_CUTOVER_FREEZE protected.

This means: the legitimate R3 work is already canary-cohort-safe per Fowler's pattern; the contamination is in a different zone (data ingest); these can be remediated INDEPENDENTLY without bulk revert. Feature flags + zone separation enable surgical fix-forward, which is exactly the stage-gated approach.

Fowler's "avoid branching... allow that branch to be deployed to production at any time" is also the canonical argument for trunk-based-development with surgical fix-forward over revert+rebuild.

---

## §5 Concession bank (R1 itemized; will lock at R2 close)

### NEW concessions (specific to this multi-commit reality)

1. **Boot §2 Arg-A "<5% contamination" framing was naive single-commit accounting.** Honest multi-commit accounting puts contamination at 17-20% of the 9-commit ancestor chain. Per §5.Z2 codified pattern (didn't do bidirectional grep before locking %). Acknowledged.

2. **Opponent W1 "self-review violation forecloses partial-trust on the FIX" lands.** The independent critic-gate on each in-flight fix PR is non-negotiable. Concession added to my position; integrated in §2 Stage B.

3. **5th outcome category (CONDITIONAL-REVERT-PENDING-OTHER-SESSION-COMPLETION) is correct framing.** Integrated in §2 stage-gated proposal. Refinement: explicit Stage A/B/C/D + measurable critic-PASS criteria.

4. **TIER-1 REVERT of `575f435` + `7027247` is appropriate.** These commits directly introduced drifts #2/#3/#4 with structural errors (heuristic-as-truth pattern). Stage-A immediate revert costs ~3-5h; preserves the legitimate observation v2 antibody work in `6754cdc` separately.

5. **Cross-session coordination is operational hard-requirement.** Stage-gating requires brokering between this session (audit/revert authority) and the contaminated session (in-flight fix authority). Cannot be done unilaterally.

### HOLDS (against likely opponent positions)

1. **Process gaps A-E are non-negotiable AND not addressed by full revert.** Process gates prevent the NEXT contamination; revert addresses ONE incident. Both are needed; neither replaces the other.

2. **80-83% of 9-commit chain is legitimate work** (Phase 0 antibody tests, R3 substrate, observation v2 infrastructure). Full revert punishes legitimate work for contamination's sin per §5.Z3 outcome-collapse anti-pattern.

3. **Execution-zone work (cutover_guard, V2 adapter, heartbeat) is NOT contaminated** — drifts are zone-bounded to data-ingest/source-routing. Cross-zone full-revert is over-broad.

4. **TIER-3 audit is a real cost (~20-30h) but cheaper than full revert + redo (~80-150h).** Cost asymmetry still favors stage-gated even with my boot's <5% framing corrected to 17-20%.

### UNRESOLVABLE from current evidence (defer to verdict)

1. Whether the contaminated session's in-flight fixes will pass independent critic-gate. Empirical question; only stage-gating reveals the answer.
2. Whether 1ffef77 + verify_truth_surfaces touch is genuine drift item #5 or a clean preexisting bug fix. Requires per-hunk audit (Stage C).
3. Whether the 80-83% legitimate-work fraction holds under per-hunk audit. The audit may reveal more contamination; if so, position must update.

---

## §6 LOCKED REFINED POSITION

**STAGE-GATED SURGICAL REVERT + PROCESS-FIX-GOING-FORWARD**, per §2:

- **Stage A** (~3-5h): TIER-1 REVERT `575f435` + `7027247` (the 2 directly drift-introducing commits)
- **Stage B** (3-14 days): TIER-2 critic-gate the in-flight drift fixes from contaminated session
- **Stage C** (7-30 days): TIER-3 per-hunk audit of `53a21ad`'s 385 files via independent critic
- **Stage D** (immediate + ongoing): process gates A-E encoded

Aggregate cost: ~33-50h vs full-revert's ~90-165h. Preserves ~80-83% of legitimate work; reverts ~17-20% of confirmed contamination. 5th outcome category accepted; refined with explicit stage gates and measurable critic-PASS criteria.

This refined position is closer to opponent's anticipated AGGRESSIVE-QUARANTINE than my boot stance — concession territory recognized. It is NOT full-revert; it is stage-gated surgical with explicit critic-gate disciplines on each stage.

---

## §7 Process notes

- ≤350 lines per dispatch cap; this file ~315 lines at write-time.
- Disk-first: this file written before SendMessage.
- 2 NEW WebFetch (cumulative R1 = 2): Google SRE Workbook 2017 + Martin Fowler Feature Toggles 2017-10-09. Per dispatch ≥2 satisfied; cumulative ≥3 R1+R2 not yet (R2 will add).
- Multi-commit empirical baseline grep-verified at HEAD `pre-quarantine-snapshot-2026-04-28`: 9 ancestor commits / 464 files cumulative / 4 commits with direct drift attribution (575f435, 7027247, 53a21ad, 1ffef77).
- Engaged opponent's STRONGEST anticipated attack (boot §3 W1 "self-review violation forecloses partial-trust") at face value with 3 explicit concessions before holding 1 (§1).
- Per dispatch ≥1 itemized concession + LOCK explicit position: 5 NEW concessions itemized (§5); position LOCKED in §6.
- Methodology §5.Z2 audit-first discipline followed: bidirectional file-count grep BEFORE making the 17-20% concession claim.
- 5th outcome category accepted + refined with Stage A/B/C/D structure (§2).
- LONG-LAST status maintained.
