# Proponent Round-3 Critique — Opponent's Edge-First Proposal

Author: proponent-harness
Date: 2026-04-28
HEAD: 874e00cc0244135f49708682cab434b4d151d25d
Critiquing: `evidence/opponent/round3_proposal.md` (~330L) — "70/30 EDGE/HARNESS averaged 16 weeks"
My round-3 proposal: `evidence/proponent/round3_proposal.md` (211L) — "40/60 HARNESS/EDGE front-loaded 6 months"

**Convergence statement**: Both sides locked Tier 1 non-negotiable + EDGE_OBSERVATION first. Both agree harness work CONTINUES at lower cadence after Tier 1. Real disagreement: 30pp gap on average allocation (mine 60% harness; opponent's 30% harness) and pacing (mine front-loaded 6mo; opponent's stretched 16wk).

---

## §0 Engaging opponent's STRONGEST element at face value

Opponent's strongest single move is the **Paul Graham + Renaissance + marginal-hour-ROI triple-citation** (their §2 reasons 1-3). Together they form: "After Tier 1, harness work is past the steepest part of the diminishing-returns curve (~50 LOC/h reduction). The marginal hour buys MORE in trading P&L from EDGE_OBSERVATION than from another router pruned. Renaissance staffs research-heavy because alpha IS the firm's function. Paul Graham warns 'perfectionism is procrastination' — automating before the bottleneck is identified."

This is the most authoritative cross-domain citation chain in either side's round-3. I engage face-value first.

### What I CONCEDE from this triple-citation (formal, itemized; ADD to round-1+2 LOCKED)

1. **Diminishing-returns curve IS real after Tier 1.** Opponent's "~50 LOC/hour reduction" estimate for Tier 3 work is consistent with my own §3 sequencing (Tier 3 trickle in months 5-6 = exactly the lower-ROI tail). Concession: the LOC-reduction-per-hour metric strictly favors edge after Tier 1.

2. **Polymarket weather markets are event-driven; timing matters.** Per opponent §2 reason 4 + their Wikipedia cite ("predictions are better when the event predicted is close in time"). Each week without EDGE_OBSERVATION is a week of unmeasured strategy decay. The opportunity cost of delaying measurement is real, not bounded-by-LOC-reduction logic.

3. **Renaissance staffs research-heavy.** Their 150 staff ratio favors research over operations. The general principle ("alpha IS the firm's function; operations is subroutine") is correct, especially at the bootstrap stage where every operator hour is binary-allocated.

4. **Paul Graham's "perfectionism is procrastination" is legitimate.** Polishing the harness past the post-Tier-1 substrate IS the procrastination Graham warns about IF the bottleneck has shifted to edge measurement. Concession: the trigger for "harness done = pivot to edge" should be sized to ACTUAL bottleneck identification, not asymptotic LOC targets.

5. **Anthropic Claude Code best practices ("ruthlessly prune; if Claude already does something correctly without the instruction, delete it") DO apply post-Tier-1.** If post-Tier-1 harness already catches Z2-class regressions, further pruning is "doing-correctly-already" territory. Concession: this is the strongest argument in opponent's pro-edge direction.

### What I HOLD against the triple-citation (formal, itemized)

1. **Renaissance comparison conflates established firm with bootstrap stage** — and opponent's own §2 reason 2 caveat acknowledges this. Renaissance's 150-person staffing pattern was built AFTER they had measurement infrastructure + risk controls + execution validation. Zeus is NOT at the "alpha is the bottleneck" stage yet — it is at the "do we have edge at all?" stage, where the answer comes from EDGE_OBSERVATION (which both sides agree starts week 1) AND from the harness substrate that ensures the measurement isn't corrupted by upstream signal pollution. Citing Renaissance's mature-firm staffing pattern for a bootstrap-stage system is mode-mismatched, the same way Anthropic Dec 2024 "few lines of code" was mode-mismatched per verdict §1.7 LOCKED.

2. **Paul Graham's "automate AFTER bottlenecks" applies to PRODUCT FEATURES, not safety infrastructure.** Graham's essay is about chat support, manual onboarding, hand-built early-stage features — things where premature automation reduces flexibility and learning. SAFETY INFRASTRUCTURE in financial systems is the OPPOSITE asymmetry: failure tail is unbounded, recovery from failure is impossible (lost money is lost). Graham's framework explicitly does not address regulated/financial/safety-critical contexts. Citing it for a live-money trading system trades the framework's strength (flexibility for product iteration) for its weakness (no defense against catastrophic operational failures). The Knight Capital + 2010 Flash Crash citations from my §2.A apply here directly: in trading, "perfectionism is procrastination" is exactly inverted — "skimping on infrastructure to chase edge" has a $440M precedent.

3. **Marginal-hour-ROI calculation is correct on the LOC metric and WRONG on the operator-attention metric.** Opponent's own concession §3 NEW item 3: *"Edge packets each carry their own harness needs (~10-30h each)."* 5 edge packets × 10-30h = 50-150h of EDGE-DRIVEN harness work that opponent's plan does NOT count against their 70% edge allocation. This eats 30-50% of the "harness savings" they claim back. The honest arithmetic: opponent's 70/30 is closer to 55/45 or 60/40 once edge-driven harness needs are counted. Concession in §4 below.

---

## §1 Three concrete weaknesses in opponent's 70/30 plan

### Weakness 1 — ~5h/week Tier 2+3 background cadence is unrealistic for the irreversible packets in DEEP_PLAN.md

Opponent §3 NEW concession 4: *"Tier 2+3 work continues in BACKGROUND at ~5h/week vs Tier 1's ~10-15h/week burst. Total Tier 2+3 work still happens; calendar stretches from 4-8 weeks to 4-6 months."*

DEEP_PLAN §2.3 P4 is *"source_rationale.yaml → inline docstrings (irreversible; staged)"*. Opponent's own round-2 proposal §9 NEW concession 3 explicitly admitted this is *"irreversible. Once content moves into source files, reverting requires re-extraction."* Running irreversible migrations at 5h/week cadence — 173 src files needing inline docstring extraction — means each file gets ~2-3 minutes of attention per week, spread across a 4-6 month window. Two failure modes follow:

- **Knowledge loss**: at 2-3 min/file/week, the operator can't hold the current state of the migration in mind across weeks. Files migrated week 3 are forgotten by week 8 when verifier runs. Mid-migration corrections become impossible.
- **Stuck-in-flight state**: irreversible work spread thin spends 4-6 months in `source_rationale.yaml` half-deleted state. The whole point of irreversibility is it's a one-way door; opponent's plan props the door open for 4-6 months.

DEEP_PLAN's original ~80-110h Tier 3 budget over 4-8 weeks is sized for FOCUSED execution. Opponent's plan stretches it to background-cadence — which is wrong for the irreversible items. **Concrete hit**: opponent's plan should EITHER complete irreversible items in burst mode (matching my front-loaded approach) OR defer them entirely. The ~5h/week middle path is the worst-of-both-worlds.

### Weakness 2 — Edge-driven harness needs (their own §3 concession 3) UNCOUNTED in 70/30

Opponent §3 NEW concession 3 verbatim: *"EDGE_OBSERVATION needs decision-log schema; CALIBRATION_HARDENING needs Platt-model relationship tests; ATTRIBUTION_DRIFT needs strategy_key audit. **Each edge packet adds ~10-30h of harness work specific to that packet.** That work does NOT count against my 'edge first' priority because it is direct precondition for the edge value."*

This is honest — but it inverts the accounting. Each edge packet's 10-30h of "edge-driven harness needs" is HARNESS WORK by any definition. Calling it "edge work" because it serves edge value is the same logic by which I could call my Tier 3 hooks "edge enablement" because they unblock edge work. The honest accounting:

| Allocation | Opponent claim | Honest accounting |
|---|---|---|
| Tier 1 fixed cost | 14-20h harness (both agree) | 14-20h harness ✓ |
| Tier 2+3 polish (opponent: backgrounded) | ~110-150h "harness" but at 5h/wk = 30% of weekly hours | 30% as opponent says |
| Edge-driven harness (opponent: counted as edge) | ~50-150h labeled "edge" | THIS IS HARNESS WORK |
| True edge work (P&L logic) | 250-350h "edge" includes the 50-150h above | 100-200h pure edge |

**Re-calculated opponent's actual harness allocation**: (14-20h Tier 1 + 110-150h Tier 2+3 + 50-150h edge-driven) / total = ~175-320h harness / ~500h total = **35-65% harness**. The opponent's "70/30" headline figure is inflated by reclassifying edge-driven harness as edge.

**Concrete hit**: once we count edge-driven harness honestly, opponent's plan converges toward my 40/60 split. The disagreement is partly accounting, not philosophy.

### Weakness 3 — Renaissance comparison + Polymarket time-to-market are MUTUALLY UNDERMINING

Opponent's §2 reason 2 (Renaissance) argues for research-heavy staffing pattern based on a 70%-annual-return fund. Opponent's §2 reason 4 (Polymarket microstructure) argues edge measurement must run THIS WEEK because event-driven markets shift week-to-week.

These two arguments target different stages of system maturity and pull in different directions:
- Renaissance: "After you've measured + calibrated + validated execution, scale research." (Mature firm, infrastructure assumed.)
- Polymarket-time-to-market: "Measurement infrastructure is the URGENT thing because markets change weekly." (Bootstrap-stage urgency.)

The honest synthesis: at bootstrap stage, the URGENT thing is BOTH "ship measurement infrastructure" (which IS partly harness work — decision-log schema, strategy_key audit, Platt-model relationship tests per opponent's own concession 3) AND "harden the substrate so measurement isn't corrupted by upstream signal pollution." These are not competing priorities; they're concurrent. Opponent's framing of "harness then edge in sequence" or "edge with harness in background" misses that **the first 50-100h of EDGE_OBSERVATION work IS partly harness work** (per their own concession 3).

**Concrete hit**: the Renaissance-style mature-firm pattern doesn't apply UNTIL EDGE_OBSERVATION + CALIBRATION are in place. And those packets pull in harness work the opponent has reclassified as edge. Net: at this stage, the work is more co-equal than 70/30 suggests.

---

## §2 Three strongest threats opponent's proposal poses to my 40/60 position

### Threat 1 — Diminishing-returns curve IS real on LOC reduction

Opponent §2 reason 1 + §1 pivot-A: after Tier 1, harness work yields ~30-50 LOC reduced per engineer-hour. This is genuinely the steepest part of the curve being behind us. My §3 month-5-6 phase ("Tier 3 trickle 20% allocation") implicitly accepts this; opponent makes it explicit and pushes the 20% lower. **This is fair pushback.**

My counter: the 20% in months 5-6 is for COMPLETING the migration, not for asymptotic polish. The curve is steepest pre-Tier-1; flat after Tier 3 P10. The opponent's "drop harness to background after Tier 1" skips the middle of the curve — which still has REAL ROI on operator-attention reduction even if LOC-per-hour is lower. **Hold partial**: I update months 5-6 from 20% to 15% allocation; net 6-month average closer to 35-40% than my original 40-45%.

### Threat 2 — Polymarket event-driven microstructure makes EDGE_OBSERVATION genuinely urgent

Opponent §2 reason 4 + Wikipedia cite: weather markets ARE event-driven; week-by-week measurement matters. My §3 had EDGE_OBSERVATION in months 1-2 (parallel with Tier 1+2) — opponent's plan has it slightly earlier (week 2-4 once Tier 1 done). The gap is small; opponent is right that EDGE_OBSERVATION should be PRIMARY, not parallel-secondary, in the post-Tier-1 window.

**Concession added**: I update my §3 month-1-2 allocation. EDGE_OBSERVATION goes from "parallel 40%" to "primary 50%" once Tier 1 batches complete. Net 6-month average drops from 40/60 H/E toward 35/65 or 30/70 depending on Tier 2 burst speed.

### Threat 3 — Anthropic Claude Code "ruthlessly prune; if Claude already does it correctly, delete the instruction" applied post-Tier-1

Opponent §6 final position cites Anthropic best practices for the post-Tier-1 state: the harness "already catches the catches," so further pruning is "doing-correctly-already" territory. **This is a strong threat to my §K15 retained 28-INV YAML position** — if 28 INVs already do their work, retaining the YAML format is the very pattern Anthropic warns against. Per round-2 §H1, I held YAML pending decorator prototype. If the prototype works, I should pivot.

**Hold conditional**: my round-2 §H1 "INVs as YAML pending decorator prototype" remains correct, but I add a concrete decision criterion: **build the @enforced_by prototype within month 1 of post-Tier-1 work; if it strictly dominates current YAML+test setup, migrate; if not, keep YAML.** This makes the hold testable, not perpetual.

---

## §3 Quantitative — where I am willing to defer / push later

Genuine concession territory per dispatch directive. Walking my §3 phase plan against opponent's pushback:

| Phase | My round-3 plan | After this critique | Why moved |
|---|---|---|---|
| Months 1-2 | 60% harness / 40% edge (T1+T2 + EDGE_OBSERVATION) | **50% harness / 50% edge** (T1 complete + EDGE_OBSERVATION primary + T2 quick wins concurrent) | Threat 2: EDGE_OBSERVATION urgency on event-driven markets |
| Months 3-4 | 35% harness / 65% edge | **30% harness / 70% edge** (T3 P1-P2 hooks + audit script + WS_OR_POLL + ATTRIBUTION_DRIFT) | Threat 1: post-T1 LOC ROI confirmed lower |
| Months 5-6 | 20% harness / 80% edge | **15% harness / 85% edge** (T3 P3-P10 trickle + CALIBRATION_HARDENING focus) | Threat 1 + opponent's diminishing-returns curve |
| Month 7+ | 15-25% harness steady-state | **10-20% harness steady-state + LEARNING_LOOP** | unchanged |
| **6-month total avg** | **40% harness / 60% edge** | **~32% harness / ~68% edge** | net 8pp shift toward edge |

**Specific items I am willing to defer to "background" cadence (matching opponent)**:
- DEEP_PLAN §2.3 P4 (`source_rationale.yaml` → inline docstrings, irreversible, 16-20h) — DEFER until decorator prototype settles + verifier dispatch confirms no regression in P3 module reorganization. This addresses my §1 weakness 1 self-application: don't run irreversible work at 5h/week.
- DEEP_PLAN §2.3 P10 (R3 plan-surface compaction, 24h) — DEFER until 6-month re-audit; only valuable IF future R3-equivalent plan is on the calendar.
- Tier 2 #14 (topology.yaml per-section 90-day audit) — RUN in batched fortnightly cadence (4h every 2 weeks), not continuously.

**Specific items I HOLD as front-loaded (cannot defer per §1 weakness 1 self-application)**:
- Tier 1 batches A-D (already in flight) — non-negotiable.
- Tier 2 #17 (`@enforced_by` decorator prototype) — DO THIS in month 1 to settle the round-2 §H1 hold; concrete decision criterion above.
- Tier 3 P1 (hooks + native agents + native skills) — these are deterministic-gate substrate for ALL subsequent edge work; without hooks, edge packets re-introduce the verdict-§1.5 LARP risks.
- Tier 3 P2 (topology.yaml audit + Python replacement) — needed before EDGE_OBSERVATION decision-log schema work (per opponent's own concession 3, EDGE_OBSERVATION needs schema; the schema must not collide with topology.yaml's table grammar).

---

## §4 LOCK FINAL POSITION

### Position (binding for round-3 close)

**MOVE TOWARD SYNTHESIZED MIDDLE: ~32% harness / ~68% edge over 6 months**, front-loaded on harness in Tier 1 window then ramping toward edge. Specific phases per §3.

NOT a full surrender (opponent's 70/30 still over-counts edge by reclassifying edge-driven harness; honest math closer to 55/45 or 60/40 per §1 weakness 2). NOT my original 40/60 hold (threats 1+2 land too directly).

### Updated 6-month allocation

| Months | Mine post-critique | Opponent's | Gap |
|---|---|---|---|
| 1-2 | 50/50 | 80h / 20% | 30pp |
| 3-4 | 30/70 | 25/75 | 5pp |
| 5-6 | 15/85 | 20/80 | 5pp |
| Avg 6mo | ~32% H | ~30% H (claimed) / ~45% H (honest accounting) | 2-13pp |
| Steady-state mo7+ | 10-20% H | 10% H | 0-10pp |

Real remaining gap: months 1-2 (30pp). I HOLD that the post-Tier-1 substrate hardening + decorator prototype + hooks setup deserve concentrated attention in the first 2 months because they're substrate for everything after; opponent wants edge to dominate from week 2.

### Sequencing (binding)

1. **Tier 1 to completion** (executor batches A-D, ~14-20h) — both sides agree, non-negotiable.
2. **EDGE_OBSERVATION primary in months 1-2** — concession to opponent threat 2.
3. **Tier 3 P1 hooks + native agents + Tier 2 #17 decorator prototype concurrent** — substrate for all later edge packets.
4. **WS_OR_POLL_TIGHTENING + ATTRIBUTION_DRIFT in months 3-4** — parallel different src/ subsystems.
5. **CALIBRATION_HARDENING in months 5-6** — highest-stakes packet, deserves max harness substrate.
6. **LEARNING_LOOP month 7+** — depends on prior 4 packets clean.

### Asymptote shift triggers (additive to round-3 §6 forward triggers)

NEW trigger: if `@enforced_by` decorator prototype (Tier 2 #17) demonstrates strictly stronger enforcement than YAML+tests on the 71-pass baseline within month 1 → migrate INVs to Python in month 2; harness allocation drops further. If prototype fails, hold YAML and move resources to next edge packet.

### What I do NOT concede

- **Knight Capital + 2010 Flash Crash precedents stand.** Skimping infrastructure to chase edge has a $440M precedent. The substrate-stability minimum (Tier 1 + hooks + decorator settlement) is non-negotiable.
- **Edge-driven harness needs are HARNESS WORK** for accounting purposes (§1 weakness 2). Opponent's 70/30 is closer to 55/45 or 60/40 honest accounting; my "moved toward middle" position acknowledges the convergence without accepting the inflated headline number.
- **Months 1-2 substrate hardening cannot run at background cadence** for irreversible items (P4 source_rationale → docstrings) per §1 weakness 1. These either complete in burst mode or defer entirely.

---

## §5 NEW WebFetch evidence (≥1 NEW; cumulative round-3 = 3)

Cumulative round-3 NEW (mine): Headlands Tech 2017-08-03 (round-3 proposal §5), Wikipedia 2010 Flash Crash (round-3 proposal §5), this critique adds Source NEW-3 below.

### Source NEW-3 — Martin Fowler, "Technical Debt Quadrant" (martinfowler.com/bliki/TechnicalDebtQuadrant.html, **published 2009-10-14**)

URL: `https://martinfowler.com/bliki/TechnicalDebtQuadrant.html`
Fetched: 2026-04-28 ~02:40 UTC
**Not previously cited in any prior round.**

Verbatim quotes:

> "**Dividing debt into reckless/prudent and deliberate/inadvertent implies a quadrant**"
> "**The useful distinction isn't between debt or non-debt, but between prudent and reckless debt.**"
> "**The prudent debt to reach a release may not be worth paying down if the interest payments are sufficiently small**"
> "**such as if it were in a rarely touched part of the code-base.**"
> "**The debt yields value sooner, but needs to be paid off as soon as possible.**"
> "**The decision of paying the interest versus paying down the principal still applies, if the payoff for an earlier release is greater than the costs of paying it off.**"

**Application**: Fowler's framework is the missing concept in opponent's framing. Zeus's harness debt is **PRUDENT-INADVERTENT** (the team built infrastructure that was correct at the time; it accreted because of legitimate domain encoding needs; some now exceeds value). Fowler's specific guidance: *"the prudent debt to reach a release may not be worth paying down if the interest payments are sufficiently small such as if it were in a rarely touched part of the code-base."*

The harness Tier 2+3 work falls into TWO Fowler categories:
- **High-interest sections** (rarely-touched but high-blast-radius if wrong, like INV-09 INV-15 prose-as-law that could mis-fire on calibration touch): pay down NOW. Matches my front-loaded position.
- **Low-interest sections** (touched-rarely + bounded blast radius, like docs_registry.yaml regen): defer to background or skip. Matches opponent's background cadence.

**The honest synthesis Fowler provides**: not "60/40 vs 30/70" but **"separate harness work into high-interest (front-load) and low-interest (defer) buckets, then sum."** Re-classifying my round-3 §3 phases:

| Tier | Item | Fowler class | Front-load or defer? |
|---|---|---|---|
| Tier 1 | All batches | Reckless-inadvertent (path drift, INV-16/17 LARP) | Front-load (already in flight) |
| Tier 2 #11/#12 | Auto-gen registries + boot-profile skills | Prudent-deliberate, low-interest | Background |
| Tier 2 #14 | topology.yaml per-section audit | Prudent-deliberate, mixed-interest | Front-load audit script; background section-by-section |
| Tier 2 #17 | @enforced_by prototype | Prudent-deliberate, HIGH-interest (decision criterion for §H1) | Front-load |
| Tier 3 P1 | Hooks + native agents | Prudent-inadvertent, HIGH-interest (substrate for edge) | Front-load |
| Tier 3 P2 | topology.yaml Python replacement | Prudent-deliberate, MEDIUM | Concurrent with EDGE work mo3-4 |
| Tier 3 P4 | source_rationale → docstrings (irreversible) | Prudent-deliberate, low-interest IF rarely-touched | Defer or burst-complete; never trickle |
| Tier 3 P10 | R3 plan-surface compaction | Prudent-deliberate, low-interest | Defer to month 7+ |

**Net via Fowler**: ~60-70h front-loaded HIGH-interest harness + ~40-50h backgrounded LOW-interest = ~100-120h total harness over 6 months (down from my original 110-150h estimate; consistent with my §3 32/68 average).

Opponent's plan implicitly applies Fowler's "rarely touched → defer" but DOESN'T separate HIGH-interest from LOW-interest within Tier 2+3. Treating Tier 3 P1 hooks (HIGH-interest substrate) the same as Tier 3 P10 plan-surface compaction (LOW-interest) is the accounting error.

---

## §6 Summary for judge round-3 grading

**FINAL POSITION: MOVE TOWARD SYNTHESIZED MIDDLE (~32% harness / ~68% edge over 6 months)**, front-loaded on HIGH-INTEREST harness items in months 1-2, ramping to EDGE-DOMINANT in months 3-6, steady-state ~10-20% harness in month 7+.

**5 explicit concessions** to opponent at face value (§0): diminishing-returns curve real, Polymarket urgency real, Renaissance ratio principle correct, Paul Graham perfectionism warning legitimate, Anthropic ruthlessly-prune applies post-Tier-1.

**3 holds against opponent** (§0): Renaissance comparison mode-mismatched (mature firm vs bootstrap), Paul Graham framework doesn't address safety-critical asymmetry, marginal-hour-ROI calculation correct on LOC and wrong on operator-attention.

**3 weaknesses identified in opponent's plan** (§1): 5h/week cadence unrealistic for irreversible items, edge-driven harness needs uncounted in 70/30, Renaissance + Polymarket arguments mutually undermining at bootstrap stage.

**3 threats from opponent absorbed** (§2): diminishing-returns curve real → reduce months 5-6 to 15%; Polymarket urgency → EDGE_OBSERVATION primary not parallel; Anthropic ruthlessly-prune → make decorator-prototype decision in month 1.

**Genuine concession territory** (§3): defer source_rationale → docstrings, defer R3 plan-surface compaction, fortnightly cadence for topology audit. Hold front-loaded for hooks + decorator prototype + topology Python replacement.

**1 NEW WebFetch (cumulative round-3 = 3)**: Martin Fowler Technical Debt Quadrant 2009-10-14 — provides the high-interest vs low-interest distinction that resolves the opponent-vs-mine accounting dispute.

**Distance from opponent**: ~2-13pp on 6-month average (depending on whether opponent's edge-driven harness is honestly counted), 30pp in months 1-2 specifically. Remaining genuine disagreement: substrate hardening urgency in the first 2 months.

---

## §7 Process notes

- 1 NEW WebFetch (Fowler Tech Debt Quadrant 2009-10-14) — cumulative round-3 = 3 (Headlands + Flash Crash + Fowler). Per dispatch ≥1 NEW satisfied; ≥3 cumulative satisfied. No recycle from R1+R2's 8 sources.
- Opponent's R3 fully read (~330L).
- Opponent's STRONGEST element (Paul Graham + Renaissance + marginal-hour ROI triple-citation) engaged at face value with 5 explicit concessions before holding 3 (§0).
- 3 concrete weaknesses in opponent's plan documented (§1: 5h/week irreversible cadence, edge-driven harness uncounted, Renaissance/Polymarket mutually undermining).
- 3 strongest threats from opponent identified + concessions made (§2: diminishing returns, Polymarket urgency, ruthlessly-prune).
- Quantitative concession in §3 (where I defer / where I hold).
- LOCKED FINAL POSITION (§4): MOVE TOWARD SYNTHESIZED MIDDLE ~32% harness / ~68% edge over 6 months; specific phase percentages updated; sequencing 1-6 binding; asymptote triggers extended.
- ≤300 lines per dispatch cap: this file at write-time at 282 lines (tail content + header), under cap.
- LONG-LAST status maintained for any further dispatch.
