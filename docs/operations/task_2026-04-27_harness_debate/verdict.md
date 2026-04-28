# Verdict — Zeus Harness Debate 2026-04-27

Judge: team-lead@zeus-harness-debate-2026-04-27
HEAD: 874e00cc0244135f49708682cab434b4d151d25d (main, branch plan-pre5)
Date: 2026-04-28
Status: **FINAL** for round-1 (pro/con verdict). Round-2 (alt-system proposals) reserved; teammates persist as longlast.

## TL;DR

**MIXED VERDICT WITH NET-NEGATIVE TILT ON MARGINAL SURFACE.**

- Load-bearing core (~20-30% of current surface) earns its cost; both sides agree on what it is.
- Marginal periphery (~60-70%) is unbudgeted under R2 cross-examination.
- The disagreement on exact ratio is unresolvable without an A/B trial that does not exist.
- Both sides proposed concrete subtractions; significant overlap on what to delete first.
- Action plan (per verdict + optional action plan stake): **opponent invited to propose round-2 subtraction list; proponent has dissent rights on each item.**

The harness is doing real work. It is also carrying real entropy. Modern Opus 4.7 / GPT 5.5 do not eat the load-bearing core for breakfast — they DO eat the prophylactic catalog layer (14 anti-drift mechanisms + 7-class boot profile ritual + multi-region parallel debate apparatus) for breakfast.

---

## §1 What both sides explicitly conceded (LOCKED, not re-debatable)

These are concessions that survived R2 cross-examination from both proponent and opponent:

1. **Z2 retro 6 catches are real and at least one is a live-money loss vector** (compatibility-as-live-bypass on V2 cutover). Some discipline is load-bearing.

2. **At least 4-5 specific mechanisms are load-bearing**: critic-opus dispatch, verifier dispatch, antibody contracts (NC-NEW-A..J), per-phase boot evidence files, disk-first artifact discipline, cross-session memory. Without these the Z2 catches do not happen.

3. **TOPIC.md "769 .md non-archive" is wrong; actual 357 tracked non-archive.** Real maintenance failure of the harness on the document framing this debate. Judge-verified.

4. **AGENTS.md count: 41 tracked-non-archive is correct on its definition; 72 includes worktree clones.** Visibility-class manifest (`workspace_map.md:20-28`) is the disambiguating mechanism — load-bearing.

5. **INV-16 and INV-17 are pure prose-as-law on HEAD.** Backed only by `spec_sections:` + `negative_constraints:` (both prose pointers). 6.7% pure-LARP rate (2 of 30 INVs).

6. **7 of 10 untested INVs cite a drifted `migrations/` schema path** (file actually at `architecture/2026_04_02_architecture_kernel.sql`). Proponent's own R2 audit surfaced this — drift exists in the harness's most authoritative document, undetected by `r3_drift_check.py` (coverage gap).

7. **Anthropic's Dec 2024 "few lines of code" guidance does NOT apply directly to live-money trading mode.** Mode-mismatch on that specific quote. Proponent's framing wins on this point.

8. **Cross-session memory + domain encoding ARE necessary** per Fitz Constraint #2 (translation loss thermodynamic). HK HKO truncation, settlement station ≠ airport station, Day0 ≠ historical hourly are not derivable from source-read alone — they must be encoded somewhere.

9. **The R3 multi-region parallel debate apparatus WAS net-negative** for that planning cycle, per `RETROSPECTIVE_2026-04-26.md:14-22`. Operator already started dismantling (lines 61-69 sequential mode). Both sides agree this part of the harness is correctly being pruned.

10. **Anthropic's actual prescriptions** (Jun 13 + Sep 29, 2025 posts) endorse: orchestrator-worker pattern, disk-first artifacts, summary-and-store cross-session memory, intelligent context curation. Zeus implements all of these in its core. **The dispute is about the cathedral-vs-kernel SIZE, not about whether scaffolding helps.**

---

## §2 What each side HELD under cross-examination

### Proponent (mixed-positive, 70-80% load-bearing)

- All 5 cited semgrep rules exist and are wired (judge-verified). 20 of 30 INVs (66.7%) test-backed.
- Z2 retro 6-catch case is empirical; HK HKO + V2 BUSTED-HIGH + Z2 6-catch are domain knowledge that 1M-context source-read does not produce.
- Cursor (built on Claude, largest-volume Anthropic API customer) uses root + scoped rules + structured workflow + approval gates — same pattern as Zeus.
- Cost asymmetry: 7 process faults / 12h is cheap; one shipped INV-21 violation (Kelly without distribution) is unbounded-cost.
- Subtraction is bounded (10 INV-level fixes), not sweeping cull.

### Opponent (mixed-negative, ~10-20% load-bearing)

- **Smoking gun**: Z2 retro `r3/learnings/Z2_codex_2026-04-27_retro.md` names exactly **4 mechanisms** as catches (critic + verifier + tests + YAML closeout parser). Does NOT name `architecture/invariants.yaml`, `source_rationale.yaml`, `topology.yaml`, `fatal_misreads.yaml`, `task_boot_profiles.yaml`, `code_review_graph_protocol.yaml`, the 14-anti-drift catalog, `topology_doctor.py`, or any of 41 routers. Proponent's strongest case carries 4 mechanisms; the harness ships 50+. **Surface excess of ~46 mechanisms is not credited in the empirical evidence proponent leaned on.**
- Cursor docs explicitly say: "Keep rules under 500 lines"; "Start simple. Add rules only when you notice Agent making the same mistake repeatedly"; "Don't over-optimize before you understand your patterns". Zeus's `topology.yaml` (165KB / ~5000 lines) and pre-emptive 30-INV cataloging directly violate this guidance.
- Anthropic Sonnet 4.5 announcement: "1M context achieves 78.2% but we report 200K as primary; recent inference issues" — long-context is degraded mode on this generation; the right harness is sized for 200-400K, not legacy short-context regimes.
- HK HKO case is structurally better as a TYPE in `src/contracts/settlement_semantics.py` (per Fitz "make the category impossible") than as YAML antibody. The harness's own structure preempts this case's harness justification.
- Z2 retro's "Rules added for future phases" section proves the harness LEARNS via retro, not via prophylactic YAML. **Catalog-as-prevention is the LARP signature.**
- Operator-as-harness saturation: 7 process faults in 12h is evidence of bandwidth ceiling. Adding more harness surface continues a positive feedback loop.

---

## §3 What is UNRESOLVABLE from current evidence

Both sides explicitly listed (and the judge concurs):

1. **Counterfactual quantification.** No experiment exists comparing Zeus-with-current-harness vs Zeus-with-pruned-harness on identical workload. Both sides argued counterfactuals; neither has the data. **This is the fundamental epistemic limit of this round.**

2. **Whether 1-year-old YAML manifests are still load-bearing or have decayed.** Sample-of-2 (INV-16, INV-17 LARP) is suggestive, not dispositive. Proponent did not present systematic re-audit evidence; opponent did not present systematic staleness evidence.

3. **Whether the operator could comfortably run a smaller harness.** Operator's CLAUDE.md reflects deeply internalized harness-thinking; counterfactual cognitive cost of pruning is not assessable from outside.

4. **Whether Opus 4.7 / GPT 5.5 capability growth further compresses the harness's marginal value over time.** Forward-looking; both sides asserted, neither proved. Round-2 alt-system proposals can address.

5. **Whether `topology_doctor.py` (1,630 LOC) is itself optimally sized.** Not audited this round.

---

## §4 Judge's weighing under "what a win looks like" criteria (TOPIC.md L86-93)

| Criterion | Verdict | Notes |
|---|---|---|
| 1. Engagement with strongest claim | TIE | Both engaged at face value with explicit concessions before pivoting |
| 2. External evidence concreteness | Slight opponent edge | Opponent's Cursor "<500 lines" + Sonnet 4.5 long-context degraded admission are more directly actionable; proponent's Anthropic Jun/Sep 2025 posts and Cursor scoped-rules pattern are institutionally weighty but more interpretable |
| 3. Repo evidence specificity | Slight opponent edge | Opponent's Z2 attribution check is the strongest single repo finding (smoking gun); proponent's 7-INV-path-drift discovery (R2 §1 Finding 1) is the strongest counter-finding (also self-attack) |
| 4. Acknowledgment of trade-offs | TIE | Both itemized substantial concessions; opponent narrowed LARP claim 33%→6.7% (credibility-builder); proponent acknowledged R3 parallel-debate failure (credibility-builder) |
| 5. Survival under cross-examination | Slight opponent edge | Opponent's positions narrowed honestly (33%→6.7%, 75%-off retracted); proponent's positions narrowed under R2 (all-enforced→20/30 tests + 7-INV drift). Both narrowed; opponent narrowed more visibly which built more credibility |

Aggregate: **slight opponent edge across criteria** — not a sweep, not close to a tie either.

---

## §5 Verdict

**MIXED VERDICT WITH NET-NEGATIVE TILT ON MARGINAL SURFACE.**

By axis:

- **Axis 1 (file cleanliness): NET-NEGATIVE.** Proven by: TOPIC count error, 7-INV citation drift to non-existent path, 9-lines-per-file `source_rationale.yaml` ratio, 165KB single-file `topology.yaml`, retrospective's 7 process faults / 12h. Recoverable but currently in deficit.

- **Axis 2 (agent runtime helpfulness): SPLIT.**
  - Net-positive on core: critic-opus + verifier dispatch + antibody contracts + per-phase boot evidence + disk-first + memory (~5 mechanisms). Z2 retro empirical proof.
  - Net-negative on marginal: 14-anti-drift catalog, 7-class `task_boot_profiles.yaml` ritual, `code_review_graph_protocol.yaml` 62-line YAML governing "derived context not authority" (two abstraction layers above the actual problem), R3 multi-region parallel debate apparatus (operator already retiring).

- **Axis 3 (topology system): SPLIT.**
  - Net-positive on partial subset: 5 semgrep rules verified present, 12+ schema-backed INVs, scripts that exist. Topology IS partially enforced law.
  - Net-negative on marginal: INV-16 + INV-17 pure prose-as-law, 7 INVs with drifted schema path, "tests verify law REGISTRATION not law ENFORCEMENT" critique stands for several test-backed INVs.

The honest verdict is **neither "all good" nor "all bad"** — it is **load-bearing core (~20-30% of current surface, both sides agree on what it is) + prunable periphery (~60-70%)**. Opponent's empirical evidence for surface excess is concretely demonstrated; proponent's defense of the SIZE of the surface (vs a smaller version) is not. The user's hypothesis ("modern Opus 4.7 / GPT 5.5 eat much of this scaffolding for breakfast — too many constraints become attention drift") is **partially correct**: they DO eat the prophylactic catalog layer for breakfast; they do NOT eat the load-bearing core.

---

## §6 Action plan (per "verdict + optional action plan" stake — TOPIC.md L80-82)

Verdict is net-negative-tilted. Per the stake set in framing: **opponent (winner) is invited to propose round-2 subtraction list; proponent has dissent rights on each item.**

### §6.1 Subtractions both sides ALREADY agree on (no further debate needed)

These are concrete actions the operator can take without additional debate cycles:

1. **DELETE or REWRITE INV-16, INV-17.** Pure prose-as-law on HEAD. Both sides agree.
2. **FIX `migrations/` → `architecture/` path drift in 7 INVs** (INV-02, INV-03, INV-07, INV-08, INV-09, INV-10, INV-14, INV-15). Proponent surfaced; opponent agrees.
3. **EXTEND `r3_drift_check.py` coverage to `architecture/*.yaml` citation blocks.** Both sides agree the gap exists.
4. **FIX TOPIC.md `.md` count** (357, not 769). Factual correction.
5. **R3 multi-region parallel debate harness retired** in favor of sequential mode. Already started by operator per `RETROSPECTIVE_2026-04-26.md:61-69`.
6. **Add `tests:` block to INV-02 and INV-14** (or upgrade existing semgrep+schema with relationship test). Both sides see value; proponent committed in R2.

### §6.2 Subtractions proposed by opponent (proponent dissent reserved)

These are pending round-2 debate:

1. **HK HKO encoded as TYPE in `src/contracts/settlement_semantics.py`** (HKO_Truncation + WMO_HalfUp subclasses) RATHER THAN as YAML antibody in `fatal_misreads.yaml`. Per Fitz "make the category impossible". Estimated: 30 LOC; replaces 17 lines of YAML antibody with TypeError.

2. **Consolidate the 14-anti-drift-mechanism catalog + 7-class boot profile system into a single ~100-line operating heuristic.** Per Anthropic Jun 2025 "good heuristics rather than rigid rules" + Cursor "<500 lines" + "add only after observed mistake repetition" guidance.

3. **Audit `architecture/topology.yaml` (165KB / ~5000 lines)** for sections that exceed Cursor's "<500 lines" guidance. Sections that have not produced a catch in last 90 days are subtraction candidates.

4. **Audit `architecture/source_rationale.yaml` (60KB / 1573 lines for 173 src files = 9 lines/file)** for sections that duplicate what AGENTS.md routers + module_manifest.yaml already cover.

### §6.3 Subtractions proposed by proponent (opponent already concedes value)

1. **REVIEW the `code_review_graph_protocol.yaml` 62-line YAML** that governs a tool labeled "derived context not authority" — protocol-on-derived-context is two abstraction layers above the actual problem.

2. **Single-pass cull of `feedback_*` memory entries** that document harness-on-harness drift (e.g. `feedback_zeus_plan_citations_rot_fast`, `feedback_grep_gate_before_contract_lock`). These are antibodies for harness-self-failures; if subtraction §6.1+§6.2 lands, some of these become obsolete.

### §6.4 Items deferred to round-2 (genuine debate)

1. **The CORRECT size of the load-bearing core.** Both sides agree there IS one; they disagree on size (proponent: 70-80% of current; opponent: 10-20%). Round-2 is where structured proposals (proponent's "in-place harness reform" vs opponent's "whole-system replace") get evaluated against shared criteria.

2. **Forward-looking question**: at what model capability point does the harness's marginal value approach zero? Both sides asserted; neither proved. Worth a structured round-2 debate.

---

## §7 Process notes (judge-side, not part of substantive verdict)

- Both teammates sent BOOT_ACK at 2026-04-28T01:00Z; R1 dispatch had a process bug (judge-coordination tasks polluted team task list, both teammates correctly refused with MISROUTE_FLAG); cleanup + redispatch succeeded; R1 closed at 2026-04-28T01:08Z; R2 closed at 2026-04-28T01:14Z. Total elapsed: ~14 minutes for full debate cycle.

- Token discipline: both teammates respected ≤500 char/A2A. Boot evidence files 130-215 lines; R1 openings 135-215 lines; R2 rebuttals 210-265 lines. All disk-first.

- ≥2 WebFetch per round honored; opponent did 3 in R1 + 2 NEW in R2; proponent did 2 in R1 + 1 bonus + 2 NEW in R2.

- All file:line citations grep-verified by teammates within 10 min; judge spot-verified the most load-bearing claims (5/5 semgrep rules, 41 tracked AGENTS.md, 357 tracked .md non-archive, 30 INVs / 20 with `tests:`, drifted `migrations/` schema path).

- Anti-rubber-stamp rules (TOPIC.md L72-75) honored by both: face-value engagement, no "narrow scope self-validating", itemized concessions.

- Both teammates LONG-LAST; persist for round-2 alt-system proposal cycle.

---

## §8 Round-2 framing for next dispatch

When operator is ready, round-2 dispatch should:

1. Build on this verdict's §1 LOCKED concessions (cannot be reopened)
2. Use §6.4 deferred items as the genuine debate territory
3. Each side proposes an alt-system per the original framing (proponent: in-place evolution; opponent: whole-system replace)
4. Both proposals graded against the load-bearing-core agreed in §1 concession 2

Teammates `proponent-harness@zeus-harness-debate-2026-04-27` and `opponent-harness@zeus-harness-debate-2026-04-27` remain alive in idle pending round-2 dispatch.

---

## §9 The single most important finding of this debate

Independent of the proponent-vs-opponent verdict: **the harness is producing, undetected, citation drift in its most authoritative document** (`architecture/invariants.yaml` — 7 of 10 untested INVs cite a non-existent `migrations/` schema path). This was discovered DURING the debate, by the proponent honoring a R1 commitment, using the harness's own audit pattern.

This is simultaneously:
- **Strongest evidence FOR opponent**: harness is in entropy-exceeds-maintenance mode on Axis 1.
- **Strongest evidence FOR proponent**: the harness's audit pattern (per-INV `enforced_by:` block + grep-verify) actually FOUND the drift; the immune system per Fitz Constraint #3 is operating; the next antibody is to extend `r3_drift_check.py` coverage.

The same finding can be cited by both sides. That is the honest texture of this debate: a complex system whose value depends on whether you weight what it currently catches or what it currently misses.

---

End of verdict.
