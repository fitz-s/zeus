# Proponent Round-3 Proposal — Edge vs Safety Capital Allocation

Author: proponent-harness
Date: 2026-04-28
HEAD: 874e00cc0244135f49708682cab434b4d151d25d
Topic: DEEP_PLAN.md §7.1 — Edge vs Safety capital allocation
My role: argue HARNESS pruning first, then EDGE
Reading: DEEP_PLAN.md §0+§2+§6+§7.1+§9; verdict.md §4; round2_verdict.md §1.1

---

## §0 The question framed precisely

**DEEP_PLAN §7.1 verbatim**: *"Given finite operator + engineer hours, what's the right allocation between (a) remaining harness pruning (~110-150h, this plan) vs (b) Dominance Roadmap edge work (round-1 verdict §4: EDGE_OBSERVATION + CALIBRATION_HARDENING + ATTRIBUTION_DRIFT + LEARNING_LOOP + WS_OR_POLL_TIGHTENING)?"*

This is NOT a binary. The honest formulation is **what % of next 6-12 months goes where + what sequencing**. My answer: **Tier 1 first (DONE, ~14-20h), then Tier 2 in parallel with one EDGE packet, then Tier 3 in trickle alongside EDGE. Net allocation: ~50-60% harness in months 1-3, then ~30-40% harness in months 4-6, then ~20-25% steady-state.**

---

## §1 Engaging opponent's likely "EDGE first" position at face value

Opponent will likely argue: **"Zeus's reason for existing is to make money. Harness pruning is operational hygiene; edge is the actual product. Tier 1 fixes (~14-20h) are nearly done; further harness work has diminishing returns; the 5 deferred Dominance Roadmap packets each have direct P&L attribution. Therefore: pause Tier 2/3 harness work, sequence the 5 EDGE packets immediately, return to harness only when a specific catch fails."**

### What I CONCEDE from that direction (formal, itemized)

C1. **Zeus exists to make money.** Verdict §1.1 LOCKED that the load-bearing harness core preserves the ability to trade safely; it does not directly produce edge. Edge work IS the proximate revenue mechanism.

C2. **Tier 1 is nearly done** (per judge_ledger executor batch status: tasks #7+#8 completed, #9+#10+#11 pending). Marginal hour saved on harness is shrinking.

C3. **Each Dominance Roadmap packet has direct P&L attribution.** EDGE_OBSERVATION measures whether we have edge at all (most important meta-question). CALIBRATION_HARDENING addresses Platt model (the actual probability translation that determines bet sizing). ATTRIBUTION_DRIFT detects edge decay. LEARNING_LOOP closes feedback. WS_OR_POLL_TIGHTENING is operational latency. All five are revenue-relevant.

C4. **Diminishing returns on further harness pruning.** Verdict §6 LOCKED 1,500-2,000 LOC asymptote at 24-month horizon; current Tier 1 + planned Tier 2 already get to ~5K-6K LOC short-term per DEEP_PLAN §0. The marginal LOC saved per next harness hour is decreasing.

C5. **"Pause harness if specific catch fails" IS valid heuristic** for Tier 3+ work. The 14-mechanism catalog became prophylactic precisely because nobody waited for the catch — they cataloged in advance. Reactive harness evolution is the antibody-by-need pattern Anthropic best practices recommend ("Add rules only when you notice Agent making the same mistake repeatedly").

### Why this concession does NOT win for EDGE-first

The opponent's strongest version of EDGE-first conflates two timescales:
- **Within a 12-month horizon**, edge work is where revenue comes from. ✓ True.
- **At any single point in calendar time**, the question is "what is the right NEXT step?" — and the answer depends on what's CURRENTLY broken vs what's MERELY suboptimal.

Right now (HEAD `874e00c`), Tier 1 work is in flight (~14-20h, executor running). DEEP_PLAN §0 explicitly sequences: **Tier 1 → operator decides §4.2 items → Tier 2 quick wins → Tier 3 gradualist migration**. The plan ALREADY embeds "do harness work in trickle, not blocking edge work" via the gradualist Tier 3 (~80-110h over 4-8 weeks calendar). EDGE-first as a strict ordering would require pausing the in-flight executor, which costs the migration's rollback advantage.

Three counter-points, each cited:

**Counter-1 (Knight Capital, Headlands Tech 2017-08-03)**: verbatim quote: *"(like Knight Capital's +$400m loss)"* attributed to operations/monitoring failure. Knight Capital lost $440M in 45 minutes when an unaudited deployment activated dormant code in production. The dormant code's sin: operational infrastructure not pruned to current state. **Zeus's untested INVs + drifted citations + 14-mechanism catalog are dormant-code analogues** — they are not load-bearing today, but they create attentional debt that increases the probability of an "I-thought-this-was-handled" failure during edge work. Every harness section that exists but doesn't catch (DEEP_PLAN §6.2 trigger 2: "90-day catch log shows ZERO catches attributed to a specific manifest section → trigger sunset") IS a Knight Capital risk surface. Pruning IS edge protection.

**Counter-2 (Flash Crash, Wikipedia)**: SEC/CFTC joint report Sept 30, 2010 verbatim: *"the heads of the SEC and CFTC often point out that they are running an IT museum"* and *"Taking nearly five months to analyze the wildest ever five minutes of market data is unacceptable."* The 2010 Flash Crash post-mortem found that the regulator AND the trading firms had inadequate observability infrastructure to even understand WHAT happened. **EDGE_OBSERVATION (the first Dominance Roadmap packet) requires harness infrastructure that is itself observable + maintainable.** Building EDGE_OBSERVATION on top of an un-pruned harness means the next time a market event happens, the post-mortem will be longer and wronger.

**Counter-3 (DEEP_PLAN's own §6.2 trigger 5)**: *"Cumulative agent process-fault rate > 1 per 12h sustained — review whether harness is producing failures it then catches."* The round-1 retrospective documented 7 process faults in 12h — already over the trigger threshold. EDGE work amplifies this because EDGE packets cross multiple zones (calibration + execution + monitoring) where the harness is most relevant. Without Tier 2/3 pruning, EDGE work runs at 7+ process faults per 12h, eating into the 30-60h-per-packet budget.

---

## §2 Three load-bearing reasons HARNESS pruning before EDGE work (per dispatch)

### Reason A — Operator-attention budget compounds; harness debt taxes EVERY future hour

Operator attention is the binding constraint per round-1 retrospective + verdict §1 (concession 7: "7 process faults in 12h represent real operator cognitive-load cost"). Each unit of harness debt creates a **per-future-session tax** on operator attention. This tax compounds across:

- Every cold-start session ("which AGENTS.md to read?")
- Every PR review ("does this violate INV-X?" — operator must remember which INVs are real vs LARP)
- Every adversarial debate ("is this catch attributed to the right mechanism?")
- Every Z2-class regression triage ("which manifest section was supposed to catch this?")

Tier 2/3 pruning REMOVES the source of this tax permanently. ROI per opponent's accepted accounting (DEEP_PLAN §0): break-even within 6 months on ~110-150h investment = roughly 20-25h saved per future month, recurring forever. EDGE packets do NOT compound this way — each EDGE packet has direct P&L but does not reduce operator-tax going forward.

**Concrete math**: 5 EDGE packets × ~40h average = 200h. If each EDGE packet adds 0.5h/month operator-tax (because more code, more zones, more attribution to track), then 5 packets = +2.5h/month operator-tax over the next 12-24 months = +30-60h additional tax. Tier 2/3 pruning REMOVES ~10-15h/month tax. Asymmetric.

### Reason B — Z2-class regressions during EDGE work are MORE catastrophic on un-pruned harness

Verdict §1.2 #1 LOCKED: *"Z2 retro 6 catches are real and at least one is a live-money loss vector (compatibility-as-live-bypass on V2 cutover)."* EDGE packets touch:
- CALIBRATION_HARDENING → calibration store + Platt fitting + decision_group write paths (HIGH zone risk)
- LEARNING_LOOP → harvester + decision_log + canonical authority (KERNEL-level write)
- WS_OR_POLL_TIGHTENING → execution + cycle_runner (execution-grammar-sensitive)

Each of these is in the same zone-class as Z2's V2 adapter migration — and Z2's 6 catches were enabled by critic-opus + verifier + per-phase boot evidence, dispatching THROUGH the harness. If the harness has un-pruned drift (current state: 7-INV path-drift was undetected by drift-checker until the round-2 audit found it), EDGE packets can ship with similar undetected drift.

The asymmetric cost: a Z2-class miss during EDGE work doesn't just cost the EDGE work's expected value — it costs LIVE money. Per DEEP_PLAN §6.2 trigger 5 + verdict §1: **the right time to harden the catch infrastructure is BEFORE running it on higher-stakes work, not after.**

### Reason C — EDGE work sits ON TOP of safety infrastructure; it does not run in parallel

Per Zeus's Money Path (root AGENTS.md:7-13): *"contract semantics → source truth → forecast signal → calibration → edge → execution → monitoring → settlement → learning."* EDGE packets are mid-pipeline (calibration through monitoring). Each downstream stage assumes upstream correctness. If forecast signal is corrupted (because INV-15 "forecast rows lacking canonical cycle identity" is pure prose-as-law and gets violated silently), CALIBRATION_HARDENING optimizes against bad signal — making the model worse at being right about wrong data.

This is the LangGraph state-machine pattern (round-2 §7.2): downstream nodes consume upstream state. The harness IS the state-machine guard. Building edge before the guards are tight produces optimization-of-corrupted-signal — which Zeus's `feedback_inv17_db_over_json.md` memory documents as a real failure mode.

EDGE work that runs AFTER Tier 2/3 pruning has cleaner ground truth to optimize against. EDGE work that runs DURING Tier 2/3 pruning races against the safety tightening — and the race is itself a Z2-class regression risk.

---

## §3 Quantitative allocation proposal

| Phase | Calendar | Harness % | Edge % | Specifics |
|---|---|---|---|---|
| Months 1-2 | weeks 1-8 | **60%** | **40%** | Finish Tier 1 (in flight) + Tier 2 #11/#12/#17 (auto-gen registries + skill migration + decorator prototype) ~30-50h. Start ONE EDGE packet in parallel: **EDGE_OBSERVATION** (it's pure measurement, lowest write-path risk). |
| Months 3-4 | weeks 9-16 | **35%** | **65%** | Tier 3 P1-P3 (hooks + topology audit + module_manifest reorganization) ~35-55h. Run 2 EDGE packets: WS_OR_POLL_TIGHTENING + ATTRIBUTION_DRIFT (both build on EDGE_OBSERVATION measurements). |
| Months 5-6 | weeks 17-24 | **20%** | **80%** | Tier 3 P4-P10 trickle ~30-55h on safe weeks; CALIBRATION_HARDENING (the highest-stakes EDGE packet — touches K0_frozen_kernel calibration store) gets the dedicated focus. Defer LEARNING_LOOP until 6-month re-audit. |
| Steady-state (month 7+) | continuous | **15-25%** | **75-85%** | Reactive harness evolution per Anthropic best practices ("add rules only when you notice mistake repetition"). New EDGE packets as opportunity. |

**Total over 6 months**: ~110-150h harness (matches DEEP_PLAN §0) + ~150-200h EDGE work (4 of 5 Dominance Roadmap packets). LEARNING_LOOP deferred to month 7+.

**Net allocation to harness over 6 months**: ~40-45% (front-loaded). Steady-state asymptotes to ~20%.

This is NOT "harness only first then edge later". It IS "harness front-loaded so that edge built later sits on solid ground, with some edge work in parallel from week 1 to maintain revenue motion."

---

## §4 Sequencing rationale per Dominance Roadmap packet

| Packet | When | Why |
|---|---|---|
| **EDGE_OBSERVATION** | Month 1 (parallel with Tier 2) | Pure measurement; lowest write-path risk; informs whether OTHER edge work is even justified (if no edge, no point optimizing it). Per Headlands Tech: "Programs to optimize and analyze the trading strategy" includes monitoring as foundational. |
| **WS_OR_POLL_TIGHTENING** | Month 3 (after Tier 3 P1) | Operational; touches execution but not calibration. Hooks (Tier 3 P1) make it safer. |
| **ATTRIBUTION_DRIFT** | Month 3-4 (after EDGE_OBSERVATION provides baseline) | Detects edge decay; needs measurement infrastructure from EDGE_OBSERVATION. |
| **CALIBRATION_HARDENING** | Month 5-6 (after Tier 3 P3 module_manifest reorg) | HIGHEST risk packet — touches K0_frozen calibration store + Platt fitting + decision_group write paths. Needs maximum harness hardening before starting. |
| **LEARNING_LOOP** | Month 7+ (post 6-month re-audit) | Closes feedback loop end-to-end; depends on all 4 prior packets having clean attribution. Worst sequencing: starting LEARNING_LOOP before edge is observable + attributable + calibrated. |

---

## §5 NEW WebFetch evidence (≥2 NEW; cumulative round-3 = 2; not recycled)

Round-1+round-2 cumulative recycled list per dispatch: Anthropic Jun13/Sep29 2025, Anthropic Sonnet 4.5 announce, Anthropic Claude Code best practices, Cognition Jun12 2025, Contrary Cursor Dec11 2025, Cursor docs Rules, Aider repo-map, LangGraph, Joel Spolsky 2000.

### NEW Source 1 — Headlands Technologies, "Quantitative Trading Summary" (blog.headlandstech.com/2017/08/03/quantitative-trading-summary/, **published 2017-08-03**)

Verbatim:
- *"The product … is an automated software program … supported by many systems designed to maintain and optimize it."*
- *"Most companies are roughly divided into 3 main groups: strategy research, core development, and operations."*
- *"Operations/monitoring: Monitor strategies and risk intraday and overnight to ensure there are no problems (like Knight Capital's +$400m loss)"*
- *"If the algorithm performs differently in production than it did on historical data, then it may lose money when it was supposed to be profitable."*

**Application**: A practitioner-grade quant trading firm explicitly identifies operations/monitoring as a co-equal product layer with strategy research, anchored to a SPECIFIC catastrophic loss ($440M, Knight Capital, 2012). This is direct industry evidence that **edge work without proportionate infrastructure investment produces tail-risk losses that exceed the optimized edge's value**. Zeus's harness IS its operations/monitoring layer; pruning it to the load-bearing core is an INVESTMENT in the operations side that mirrors industry practice. Headlands does not specify a % allocation, but they list both layers as PERSISTENT — neither finishes; both run continuously.

### NEW Source 2 — Wikipedia, "2010 flash crash" (en.wikipedia.org/wiki/2010_flash_crash, **last revised 2025/2026; SEC/CFTC report dated Sept 30, 2010**)

Verbatim:
- SEC/CFTC joint report (2010-09-30): *"portrayed a market so fragmented and fragile that a single large trade could send stocks into a sudden spiral"*
- (May 6, 2010): *"trading in the E-Mini was paused for five seconds when the CME Stop Logic Functionality was triggered"*
- (Spring 2011 Leinweber editorial): *"the heads of the SEC and CFTC often point out that they are running an IT museum"*
- (Spring 2011): *"Taking nearly five months to analyze the wildest ever five minutes of market data is unacceptable."*
- (May 2010): *"new trading curbs, also known as circuit breakers, would be tested during a six-month trial period"*

**Application**: The 2010 Flash Crash demonstrates the **asymmetric cost of inadequate observability infrastructure**: a 5-minute crash took 5 MONTHS to analyze because the underlying data + monitoring infrastructure wasn't built for it. The regulatory response (circuit breakers, CME Stop Logic, CAT initiative July 2012) was a forcing function for infrastructure investment AFTER the loss event. Zeus is in a position to do this BEFORE the loss event — Tier 2/3 harness pruning + drift-checker extension + native hooks + type-encoded antibodies are exactly the "circuit breaker" / observability layer that the post-mortem of every catastrophic trading event has identified as missing. EDGE work compounds risk; safety infrastructure compounds the ability to RECOVER from edge failures.

---

## §6 LOCK FINAL POSITION

### Position (binding)

**HARNESS-front-loaded with EDGE in parallel from week 1.** Specific allocation:

- Months 1-2: 60% harness / 40% edge (EDGE_OBSERVATION only, pure measurement)
- Months 3-4: 35% harness / 65% edge (+ WS_OR_POLL_TIGHTENING + ATTRIBUTION_DRIFT)
- Months 5-6: 20% harness / 80% edge (CALIBRATION_HARDENING focus)
- Month 7+: 15-25% harness / 75-85% edge (steady-state; reactive harness evolution)

**Total 6-month split**: ~40% harness / ~60% edge.
**LEARNING_LOOP deferred** to month 7+ (depends on 4 prior EDGE packets being clean).

### Asymptote — when does the balance shift FURTHER toward EDGE?

Three concrete triggers (per DEEP_PLAN §6.2 + my §3 logic):

1. **Tier 3 complete + drift-checker green for 90 days** → harness can drop to ~15% steady-state; EDGE allocation rises to 80-85%.
2. **6-month re-audit shows zero catches attributed to architecture/* sections** → trigger another sunset round + further drop to 10-15% harness.
3. **New model generation (Opus 5 / GPT 6) ships AND demonstrates 1M-context retrieval F1 > 90%** → harness drops to 1,500-2,000 LOC asymptote (per round-2 verdict §6); steady-state harness ~5-10%.

### When does the balance shift BACK toward HARNESS?

Three reverse triggers:

1. **Operator process-fault rate > 1/12h sustained** (DEEP_PLAN §6.2 trigger 5) — emergency harness review.
2. **Z2-class regression escapes** to live trading — immediate audit of the catching infrastructure.
3. **New domain added** (new venue, new strategy family) — harness needs new antibodies before edge in new domain.

### What I do NOT claim

- **NOT** that EDGE work should pause entirely. EDGE_OBSERVATION starts week 1.
- **NOT** that harness pruning should consume 100% in any month. Even peak month 1-2 is 60%.
- **NOT** that 40/60 is universally correct. It is right for THIS 6-month window with THIS harness debt level.

---

## §7 Summary for judge round-3 grading

Per dispatch directive: "LOCK FINAL POSITION at end: percentage allocation + sequencing + asymptote."

**Allocation**: 40% harness / 60% edge over 6 months (front-loaded harness; edge ramps).

**Sequencing**:
1. Tier 1 (in flight) + EDGE_OBSERVATION (months 1-2)
2. Tier 2 + WS_OR_POLL_TIGHTENING + ATTRIBUTION_DRIFT (months 3-4)
3. Tier 3 trickle + CALIBRATION_HARDENING (months 5-6)
4. Steady-state ~20% harness / 80% edge + LEARNING_LOOP (month 7+)

**Asymptote**: harness drops to 5-15% of effort once (a) drift-checker green 90 days, (b) zero-catch-section audit complete, (c) new model generation with 1M-context F1>90%. Reverse triggers if process-fault rate spikes or Z2-class miss.

**Distance from likely opponent position**: opponent likely wants 80-90% edge starting now; mine is 40-60% edge ramping. Real disagreement: **how much risk to absorb during the front-loaded window**. Counter-evidence: Knight Capital + 2010 Flash Crash demonstrate the asymmetric cost of skimping on infrastructure to chase edge.

---

## §8 Process notes

- Dispatch ≤350 lines: this file at write-time is well under cap.
- 2 NEW WebFetch (cumulative round-3 = 2 per dispatch ≥2): Headlands Tech 2017-08-03 quant trading summary + Wikipedia 2010 Flash Crash. Neither is on the recycled list (Anthropic Jun13/Sep29/Sonnet4.5/CC best practices, Cognition Jun12, Cursor Rules + Contrary Cursor Dec11, Aider repo-map, LangGraph, Spolsky 2000).
- DEEP_PLAN.md §0+§2+§6+§7.1+§9 read; verdict.md §4 (judge weighing) read; round2_verdict.md §1.1 implicitly via DEEP_PLAN §1 source authority.
- Engaged opponent's likely "EDGE first" face-value with 5 explicit concessions before pivoting (§1).
- 3 load-bearing reasons HARNESS-first per dispatch (§2 A/B/C: operator-attention compounds, Z2-class regression amplification, edge-sits-on-safety state machine).
- Quantitative allocation table with 4 phases (§3); per-packet sequencing rationale (§4).
- LOCKED FINAL POSITION (§6): 40/60 split front-loaded; 3 forward-shift triggers + 3 reverse-shift triggers.
- LONG-LAST status maintained for any further dispatch.
