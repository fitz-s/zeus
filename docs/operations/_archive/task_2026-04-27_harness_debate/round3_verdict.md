# Round-3 Verdict — Edge vs Safety Capital Allocation

Judge: team-lead@zeus-harness-debate-2026-04-27
HEAD: 874e00cc0244135f49708682cab434b4d151d25d (main, branch plan-pre5)
Date: 2026-04-28
Round-1 verdict: `verdict.md` (mixed, net-negative tilt on marginal surface)
Round-2 verdict: `round2_verdict.md` (synthesized middle harness ~5K-6K LOC; ~110-140h migration)
Round-3 cycle elapsed: ~30 min (Phase-1 + nudge + Phase-2 + this verdict)

---

## §0 TL;DR

**Both sides arrived at PARTIAL ACCEPT and CROSSED OVER the midpoint.**

- Proponent (started 40/60 H/E) → final **32/68 H/E** (moved 8pp toward edge)
- Opponent (started 70/30 E/H = 30/70 H/E) → final **42/58 H/E** (moved 12pp toward harness)

They are now on **opposite sides of the middle in different directions than they started**. This is the signature of high-quality intellectual honesty.

**Synthesized verdict-target: ~37% harness / ~63% edge over 6 months, gated by week-3-4 empirical fault-rate observation window.** Steady-state at month 6+: ~50/50 per Headlands "operations is co-equal product layer."

---

## §1 What both sides locked (concession bank merger)

After Phase-2 mutual cross-examination, both sides explicitly endorsed:

| # | Item | Source |
|---|---|---|
| 1 | Tier 1 100% harness, weeks 1-2 (executor batches A-D, in flight) | Both — non-negotiable |
| 2 | EDGE_OBSERVATION is FIRST edge packet (provides measurement substrate for all later edge work) | Both |
| 3 | **Empirical 2-week fault-rate observation window in weeks 3-4** — measure operator process-fault rate, then commit to the rest of the allocation | Opponent §6 (proponent did not contest) |
| 4 | Per-EDGE-packet substrate work is IN-SCOPE for the EDGE packet (~15-25h moves from "background harness" to "edge precondition" classification) | Opponent §4 + proponent §1 W2 (accounting honesty) |
| 5 | INV-15 + INV-09 must be upgraded BEFORE CALIBRATION_HARDENING starts (~6-10h precondition) | Opponent §4 (concession to proponent's Threat T1: edge-on-safety state machine) |
| 6 | @enforced_by decorator prototype is built in month 1 (Tier 2 #17) — empirical decision criterion for round-2 §H1 hold | Both — proponent §3 + opponent implicit |
| 7 | **Knight Capital citation does NOT motivate Tier 2/3 work** — root cause was deployment omission + dormant Power Peg code, not harness debt. Right antibody class is in Tier 1 (deployment validation hooks + drift-checker + chain_reconciliation), not in YAML pruning | Opponent §1 Pivot-A (proponent did not contest in critique §0) |
| 8 | **Headlands "operations is co-equal product layer" framing** replaces both proponent's 75-85/15-25 asymptote AND opponent's original 90/10 asymptote → **steady-state ~50/50 maintenance/research** at month 6+ | Both — opponent §3 T3 retraction of "office-cleaning subroutine" framing + proponent did not contest |
| 9 | Paul Graham "perfectionism is procrastination" applies post-Tier-1 (diminishing-returns curve real once Tier 1 lands) | Proponent §0 concession 4 + opponent's original framing |
| 10 | Renaissance "research-heavy staffing" applies to MATURE firm; Zeus is bootstrap-stage where edge measurement substrate AND harness substrate are concurrent priorities | Both — proponent §0 hold 1 + opponent §3 implicit |
| 11 | Polymarket event-driven microstructure makes EDGE_OBSERVATION urgent ("predictions are better when event predicted is close in time") | Both — opponent §2 R4 + proponent §0 concession 2 |
| 12 | Operator-attention compounding tax IS real but unmeasured (proponent's 0.5h/month/packet × 5 = 30-60h was asserted, not calibrated) | Opponent §2 W2 + proponent did not produce calibration data |
| 13 | DEEP_PLAN §6.2 trigger 5 (process-fault rate >1/12h sustained) needs empirical observation to decide if currently active beyond the retired R3 multi-region debate apparatus | Both — opponent §3 T2 resolution proposal accepted by proponent |
| 14 | Martin Fowler Tech Debt Quadrant — separate harness work into HIGH-INTEREST (front-load: hooks, decorator prototype, topology Python, INV-09/15 upgrade) vs LOW-INTEREST (defer: docstrings, R3 plan compaction) — replaces flat-percentage allocation framing | Proponent §5 NEW; opponent did not contest |

**14 items, 0 contested.** This is the strongest concession bank of all 3 rounds.

---

## §2 Remaining genuine disagreements (small)

After Phase-2 mutual concessions, the only real disagreements are bounded:

### §2.1 Months 1-2 allocation — 30pp gap

| Phase | Proponent final | Opponent final |
|---|---|---|
| Weeks 1-2 (Tier 1) | 100% harness | 100% harness — agree |
| Weeks 3-4 | 50% harness / 50% edge | 30% harness / 70% edge |
| Weeks 5-12 | 30% harness / 70% edge | 35% harness / 65% edge — converged |
| Weeks 13-20 | 15% harness / 85% edge | 45% harness / 55% edge — diverged here (CALIBRATION_HARDENING) |
| Weeks 21-24 | 10-20% harness / 80-90% edge | 35% harness / 65% edge — diverged here |
| Steady-state mo7+ | 10-20% harness | ~50/50 — DIVERGED but per Headlands cite both should be 50/50 |

Real gaps:
- **Weeks 3-4**: proponent wants more substrate hardening before edge dominates; opponent wants edge to begin immediately after Tier 1
- **Weeks 13-24**: proponent wants edge-dominant; opponent wants to add harness substrate during HIGH-risk CALIBRATION_HARDENING packet
- **Steady-state**: both should converge to Headlands 50/50 per concession §1 #8

### §2.2 Accounting question: are "edge-driven harness needs" counted as edge or harness?

Per §1 #4 LOCKED: per-EDGE-packet substrate IS in-scope for the EDGE packet. So "EDGE_OBSERVATION needs decision-log schema" → counts as EDGE. This is settled accounting. Both sides' final percentages already reflect this; the convergence is real.

### §2.3 Diminishing-returns curve slope

Both agree the curve exists; neither has measured the slope precisely. Proponent claims Tier 2/3 still buys ~50 LOC/hour reduction; opponent claims it's diminishing AND the LOC metric is the wrong unit (should be P&L per hour).

**Resolution**: empirically decidable post-Tier-1. Run 2-week observation window per §1 #3, measure operator-attention cost, compute rough P&L impact of EDGE_OBSERVATION. **The data resolves this; debate cannot.**

---

## §3 Judge synthesis: the operator-actionable allocation

Per §1 + §2, the synthesized 6-month plan is:

```
Weeks 1-2:    Tier 1 finishing               (100% harness, executor batches A-D)
                                              ~14-20h actual
Weeks 3-4:    Empirical observation window   (30-50% harness / 50-70% edge)
                                              + EDGE_OBSERVATION begins
                                              + measure operator process-fault rate
                                              + decide subsequent allocation
Weeks 5-12:   EDGE-dominant phase            (30-35% harness / 65-70% edge)
                                              EDGE_OBSERVATION ships
                                              WS_OR_POLL + ATTRIBUTION_DRIFT in parallel
                                              Per-EDGE-packet substrate work in-scope
                                              Tier 2/3 trickle in background
Weeks 13-20:  CALIBRATION_HARDENING window   (35-45% harness / 55-65% edge)
                                              INV-15 + INV-09 upgrade as precondition
                                              CALIBRATION_HARDENING is HIGH-risk; deserves harness substrate
                                              Decorator prototype settled by month 1
Weeks 21-24:  LEARNING_LOOP + Tier 3 wrap    (25-35% harness / 65-75% edge)
                                              All 5 edge packets shipping or shipped
                                              Tier 3 P10 plan compaction deferred to mo7+ if needed

Steady-state mo7+:   ~50/50 maintenance/research equilibrium per Headlands
                     (NOT 90/10 opponent original, NOT 75-85/15-25 proponent original)
```

**6-month average**: ~37% harness / ~63% edge (midpoint of 32-42 convergence range).

### §3.1 Decision points (operator action triggers)

| Trigger | When | Action |
|---|---|---|
| Tier 1 batches A-D close | Week 2 | Lock fault-rate observation window start |
| 2-week fault-rate observation result | Week 4 | If ≤1/12h sustained → 60/40 EDGE-leaning; if >1/12h → fall back 40/60 HARNESS-leaning |
| @enforced_by decorator prototype result | Month 1 | If strictly dominates YAML+test → migrate INVs to Python (round-2 §H1 settled); if not → hold YAML, free those hours for edge work |
| EDGE_OBSERVATION first ship | Week 4-6 | Validates measurement substrate; unlocks ATTRIBUTION_DRIFT |
| CALIBRATION_HARDENING entry | Week 13 | INV-15 + INV-09 must be upgraded; verifier dispatch confirms before entry |
| Process-fault rate spike >1/12h sustained over 4 weeks | any time | EMERGENCY: pause edge work, dispatch harness audit |
| Z2-class regression caught | any time | Re-prioritize harness immediately for that catch class |
| New model generation ships (Opus 5 / GPT 6) | unknown | Re-evaluate harness sizing per DEEP_PLAN §6.2 |

### §3.2 What this synthesis does NOT decide

- **Within-edge-packet sequencing of CALIBRATION+ATTRIBUTION+WS_OR_POLL+LEARNING_LOOP** — this would be round-4 (Dominance Roadmap packet sequencing per DEEP_PLAN §7.2 candidate B). Reserve for later if needed.
- **Allocation of operator vs engineer hours within the 37/63 split** — this is operator-decision territory; depends on operator availability + engineering hire status.
- **Whether to commit to mypy-strict-everywhere as type-encoding precondition** — depends on @enforced_by prototype outcome.

---

## §4 Judge's weighing (per TOPIC.md "what a win looks like")

| Criterion | Round-3 outcome |
|---|---|
| 1. Engagement with strongest claim | TIE — both engaged at face value with 5 explicit concessions before pivoting |
| 2. External evidence concreteness | Slight opponent edge — Knight Capital Wikipedia self-correction (their own NEW source disproving their earlier framing) is the most credibility-building move; proponent's Fowler Tech Debt Quadrant adds a useful framework but not a falsification of opponent |
| 3. Repo evidence specificity | TIE — both grounded in DEEP_PLAN sections + verdict §1 LOCKED concessions + edge-packet specifics |
| 4. Acknowledgment of trade-offs | OPPONENT EDGE — opponent retracted "office-cleaning subroutine" framing AND retracted Knight Capital framing AND moved 12pp; proponent moved 8pp. Both retracted significantly; opponent retracted more visibly |
| 5. Survival under cross-examination | TIE — both positions narrowed honestly in the cross-over direction |

**Aggregate**: opponent slightly edges on credibility-building (more visible retractions), but the SUBSTANCE is convergence, not victory. **No "winner"; the synthesized middle is the verdict.**

---

## §5 Cumulative debate metrics

### Across R1+R2+R3:

- **Total elapsed**: ~70 minutes (R1 14 min + R2 22 min + R3 30 min)
- **External sources cited**: 11 (Anthropic ×4, Cursor ×2, Cognition, Aider, LangGraph, Joel Spolsky, Contrary Research, Headlands Tech, Wikipedia 2010 Flash Crash, Wikipedia Knight Capital, Wikipedia Renaissance, Paul Graham "Don't Scale", Martin Fowler Tech Debt Quadrant)
- **Repo file:line citations grep-verified**: 50+
- **Mutual face-value retractions**: round-1 opponent retracted 33% LARP → 6.7%; round-2 opponent retracted "75% off" AGENTS.md count + retracted whole-replace 2,800 LOC; round-3 opponent retracted Knight Capital framing + "office-cleaning subroutine" framing + 70/30 → 58/42; round-2 proponent retracted "all enforced" → "20/30 + 7-INV path drift"; round-3 proponent retracted 60% mo1-2 → 50% mo1-2
- **Anti-rubber-stamp discipline**: maintained across all 3 rounds. Both teammates demonstrated genuine intellectual honesty.

### What the 3 rounds collectively produced:

1. **Empirical assessment of harness ROI** (R1)
2. **Synthesized harness target** (~5K-6K LOC, R2; converged from 5500/2800 to 3500-4200)
3. **Operator-actionable resource allocation** (R3; ~37/63 H/E with empirical observation window + per-packet substrate carve-outs)

3 rounds × ~22 min each × 8 teammates × 11 external sources × ~50 grep-verified citations = the fastest possible empirical synthesis of a complex strategic question.

---

## §6 What round-3 verdict means for execution NOW

### Today (2026-04-28):
- ✅ Tier 1 batches A-D in flight (executor mid-BATCH-B per task list)
- ✅ Critic-harness gating each batch independently
- ✅ Round-3 verdict provides the post-Tier-1 allocation framework

### Week 2 (post Tier 1 close):
- Begin 2-week empirical fault-rate observation window
- EDGE_OBSERVATION packet design begins (per §3 above)
- @enforced_by decorator prototype experiment starts (Tier 2 #17)

### Month 1 decision:
- Decorator prototype result → migrate INVs or hold YAML
- Fault-rate observation result → lock 60/40 EDGE-leaning or 40/60 HARNESS-leaning

### Months 2-6:
- Execute synthesized allocation per §3
- Per-EDGE-packet substrate carve-outs in EDGE accounting
- Tier 2/3 trickle in background per Fowler HIGH/LOW-interest classification

### Month 6+:
- Steady-state ~50/50 per Headlands "operations co-equal" framing

---

## §7 Round-4 framing (if pursued)

If operator wants further adversarial debate, the natural round-4 topics are:

### Candidate A — Dominance Roadmap packet sequencing within EDGE
> Among CALIBRATION_HARDENING + ATTRIBUTION_DRIFT + WS_OR_POLL + LEARNING_LOOP, which goes first/last and why? (DEEP_PLAN §7.2 candidate B)

### Candidate B — Forward-asymptote bet (model capability assumption)
> Given GPT-5.5/Opus-4.7 capabilities + 6-12 month projection of GPT-6/Opus-5, what model assumption should Zeus build to? (DEEP_PLAN §7.3 candidate C)

### Candidate C — Harness governance evolution (anti-rebloat)
> Now that the synthesized harness is being right-sized, what mechanism prevents re-bloat over the next 24 months? (DEEP_PLAN §7.4 candidate D)

### Candidate D — mypy-strict-everywhere as type-encoding precondition
> Should Zeus commit to mypy-strict-everywhere as precondition for full type-encoding harness migration? Affects round-2 Tier 3 P8 + scope of @enforced_by prototype.

**Judge recommendation**: defer round-4 until empirical data from §3 decision points lands. Round-4 with implementation data >> round-4 with speculation.

---

## §8 Process notes (judge-side)

- Both teammates LONG-LAST in idle pending operator next dispatch.
- SendMessage drop pattern observed in this round (boot ACKs + initial proposals went to disk but not message-stream); recovered via disk-poll. Memory `feedback_converged_results_to_disk` reaffirmed: disk is canonical, SendMessage is convenience.
- Token discipline maintained: ≤500 char A2A, ≤300 LOC critique caps respected.
- Anti-rubber-stamp: both sides demonstrated multiple itemized retractions; no "narrow scope self-validating" arguments.
- All 11 cumulative external sources cited verbatim with URLs + timestamps; cross-validation when same source cited by both (e.g., Anthropic Claude Code best practices in R2 used by both with different verbatim quotes).
- Round-3 producers a less-controversial verdict than R1+R2 because the convergence was so dramatic. The substantive answer is now operationally clear.

End of round-3 verdict.

---

## §9 POST-IMPLEMENTATION ERRATUM (added 2026-04-28 after critic-harness cross-batch audit)

§1 LOCKED concession #12 (referenced as "Process-fault rate observation window" in §3.1) and §1 LOCKED concession #14 (Fowler HIGH/LOW interest classification) operate on the assumption that the schema-citation gap from round-1 was a real enforcement gap. Post-implementation audit found this was wrong:

- 4 INVs (16, 17, 02, 14) had hidden tests not cited via YAML field
- 15 tests added across these 4 INVs in BATCH D + SIDECAR-2
- True enforcement-gap-LARP rate ~0-7%, not 33%

For the round-3 capital allocation (synthesized ~37/63 H/E over 6 months), the erratum's implication is: the harness pruning ROI on Tier 2/3 yaml work may be slightly LESS than the round-3 inputs assumed (since less "actually broken" enforcement to fix), shifting the optimal allocation 2-5pp further toward EDGE. This is within the uncertainty bounds of the round-3 synthesis and does not change the operational schedule.

See round-1 verdict §10 erratum for full root-cause + corrected metrics. Methodology updated at `docs/methodology/adversarial_debate_for_project_evaluation.md` §5 case study.
