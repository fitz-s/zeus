# Opponent ROUND-3 Phase-2 — Critique of Proponent's Harness-First Allocation

Author: opponent-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Critiques: `evidence/proponent/round3_proposal.md` (40/60 HARNESS/EDGE over 6mo, front-loaded)
My round-3-phase-1 anchor: `evidence/opponent/round3_proposal.md` (70/30 EDGE/HARNESS over 16wk, back-loaded)
Convergence noted: 10pp gap (proponent 40% harness, mine 30%); both Tier 1 non-negotiable + EDGE_OBSERVATION first.

**Position going in**: 70/30 EDGE.
**Position going out**: see §6 final lock.

---

## §1 Engaging proponent's STRONGEST element at face value

Proponent's strongest is the **Knight Capital + Flash Crash safety-catastrophe argument** combined with **operator-attention-compounding-tax math** (their §2 Reason A) and **Z2-class-amplification-on-un-pruned-harness** (Reason B). Specifically:

> Quote (proponent §2 Reason A): *"5 EDGE packets × ~40h average = 200h. If each EDGE packet adds 0.5h/month operator-tax... then 5 packets = +2.5h/month operator-tax over the next 12-24 months = +30-60h additional tax. Tier 2/3 pruning REMOVES ~10-15h/month tax. Asymmetric."*

> Quote (proponent §2 Reason C): *"Building edge before the guards are tight produces optimization-of-corrupted-signal — which Zeus's `feedback_inv17_db_over_json.md` memory documents as a real failure mode."*

This is reinforced by the canonical financial-industry catastrophes: Knight Capital ($440M / 45 minutes / 2012) and the 2010 Flash Crash (5-minute event took 5 months to analyze).

### What I CONCEDE at face value

1. **Operator-attention IS the binding constraint** (round-1 retrospective: 7 process faults in 12h). Adding EDGE work without harness pruning DOES tax operator attention with new code that competes for cognition with un-pruned harness debt.

2. **Knight Capital + Flash Crash are real industry-anchored catastrophes.** Proponent's Counter-1 + Counter-2 establish that operations/monitoring infrastructure asymmetry has cost real firms hundreds of millions of dollars.

3. **Money Path causality (proponent Reason C) is correct.** Edge work sits MID-pipeline (calibration → execution → monitoring); upstream corruption (e.g., INV-15 "forecast rows lacking canonical cycle identity" pure-prose) propagates downstream into CALIBRATION_HARDENING. Optimization against bad signal IS worse than no optimization.

4. **DEEP_PLAN §6.2 trigger 5 (process-fault rate >1/12h sustained → emergency harness review) IS already triggered** by the round-1 retrospective's documented 7 faults / 12h. Proponent has fair grounds to argue harness debt is currently ABOVE the safety threshold.

5. **Headlands Tech "Operations/monitoring" framing is correct** — operations IS a co-equal product layer with strategy research, NOT a back-office function. I had implicitly treated harness as office-cleaning subroutine; proponent's Headlands citation correctly elevates it.

### Why this strongest element does NOT win 40/60 (pivot)

**Pivot-A — Knight Capital is the WRONG analogy.** Per my NEW WebFetch (Wikipedia Knight Capital Group, §5 below): the 2012 incident was caused by *"a technician forgot to copy the new Retail Liquidity Program (RLP) code to one of the eight SMARS computer servers"* + *"old Power Peg code still present on that server"*. **Root cause: deployment omission + dormant production code that should have been deleted**, NOT missing observability/harness infrastructure. This is a **production deployment hygiene failure**, not a development-time harness failure. Zeus's analog of Knight Capital risk is `src/main.py` daemon deployment + `state/` runtime — NOT `architecture/*.yaml` manifests. **Proponent's Counter-1 conflates two distinct failure classes.** The right Knight Capital antibody is deployment validation + dead-code-removal CI gate, both of which DEEP_PLAN already covers in Tier 1 (drift-checker + planning-lock hooks). It is not 110-150h of YAML pruning.

**Pivot-B — Operator-attention math is asymmetric in proponent's favor only by assertion.** Proponent's §2 Reason A claims "Tier 2/3 pruning REMOVES ~10-15h/month tax" — this is asserted, not measured. Counter-claim: per round-2 verdict §1.3 #4 LOCKED, operator's mental model of the harness is itself the durable artifact; whole-replace damages this. Tier 2/3 pruning that goes too aggressive in months 1-4 disrupts the operator's working mental model precisely when they need it for EDGE work substrate. **The "operator-attention removed by pruning" claim assumes pruning is friction-free; in reality pruning IS attention spend during the migration weeks**. The 90-130h Tier 2/3 work in proponent's months 1-4 is operator-attention COST, not just savings.

**Pivot-C — Proponent's Reason B (Z2-class on un-pruned harness) double-counts the Tier 1 substrate.** Per round-2 verdict §1.1 LOCKED #1-12, Tier 1 LANDS the load-bearing core (critic + verifier + antibody contracts + per-phase boot + disk-first + memory + HK HKO type + hooks). Z2's 6-catch was attributed to **critic + verifier + tests + YAML closeout parser** (4 mechanisms — round-2 §0.3 smoking gun). All 4 of those are IN Tier 1. The Z2-class catch capability is at full strength immediately after Tier 1; Tier 2/3 work removes routers and merges YAML without adding NEW catch capability. Proponent's "EDGE on un-pruned harness is more dangerous" claim is true ONLY if Tier 2/3 work is necessary FOR catches — but it isn't. It's necessary for operator cognitive load only.

This pivot does not destroy proponent's case. It narrows the disagreement to: **are months 1-4 better spent on 90-130h Tier 2/3 work that primarily reduces operator-tax, or on 90-130h of EDGE work that produces direct P&L attribution?** That is a real tradeoff.

---

## §2 Three concrete weaknesses in proponent's 40/60 plan

### Weakness W1 — Front-loaded Tier 2+3 in months 1-4 still costs ~90-130h that delays edge work

Proponent's §3 table: months 1-2 = 60% harness (~30-50h Tier 2 work) + months 3-4 = 35% harness (~35-55h Tier 3 P1-P3) = **65-105h of harness work BEFORE the 5th month** (when CALIBRATION_HARDENING starts in proponent's plan). My round-3 proposal: same Tier 1 to completion, then Tier 2+3 at ~5h/week background = ~80-130h spread over 16 weeks, but the FOREGROUND priority is EDGE.

The honest delta: **proponent delays CALIBRATION_HARDENING by ~12-16 weeks vs my plan**. CALIBRATION_HARDENING is the Platt-model bottleneck (DEEP_PLAN §7.2 framing). 12-16 weeks of running on un-hardened calibration is 12-16 weeks of mis-sized positions in live trading. **The opportunity cost of that delay is unbounded; it is not balanced against the bounded operator-attention savings.**

### Weakness W2 — "Operator-attention compounds" is unmeasured assertion

Proponent §2 Reason A claims 5 EDGE packets add 0.5h/month operator-tax × 12-24 months = 30-60h. **No empirical basis cited.** The Renaissance Wikipedia source I cited in round-3 phase-1 (150 staff, half PhDs) shows research-leaning hiring at scale — but at single-operator scale, the additional 0.5h/month claim is fabricated. Counter-data point: Zeus has been running with the current ~25K LOC harness for months; the 7 process-faults / 12h were specifically attributable to the R3 multi-region parallel debate apparatus (verdict §1.9 LOCKED — operator already retired this), NOT to general harness usage. Proponent's per-EDGE-packet tax claim has no calibration data.

Stronger version: even GRANTING proponent's 30-60h tax estimate, the EDGE packets generate direct P&L. If CALIBRATION_HARDENING improves bet sizing by even 1-2% on average across positions, the trading P&L impact dwarfs 30-60h of operator-attention math. **Proponent's analysis treats engineer-hours as the only unit; the actual unit is dollars of P&L. Edge dominates on the dollar axis.**

### Weakness W3 — Knight/Flash Crash analogy conflates production trading bug with development-time harness debt

Per my NEW WebFetch §5: Knight Capital's failure was a deployment + dead-code-removal failure. Zeus's analogous risk surface is `src/main.py` + `state/` + deployment scripts + cron jobs — NOT `architecture/*.yaml` manifests. Tier 2/3 pruning of YAML manifests does NOT address Knight-class risks; those risks are addressed by:
- Deployment validation hooks (TIER 1 #6 already covers this)
- Drift-checker extended to architecture/*.yaml (TIER 1 #4 already covers this)
- planning-lock + map-maintenance hooks (TIER 1 #6)

**ALL of these are in Tier 1, which both sides agree finishes first regardless of allocation.** Tier 2/3 work — which is the contested allocation — does NOT address Knight Capital's failure class. Proponent uses the Knight citation rhetorically to motivate work that doesn't actually defend against the cited risk.

Same critique on Flash Crash: the 2010 event required CIRCUIT BREAKERS + CME Stop Logic — runtime safety mechanisms in the trading engine, NOT YAML manifests in `architecture/`. Zeus's analogous defenses are RED-cancel-sweep (INV-05) + risk_level enum (already in source code) + RiskGuard daemon. Tier 2/3 pruning doesn't add or harden any of these.

---

## §3 Three strongest threats proponent's proposal poses to my 70/30 position

### Threat T1 — "Edge sits on safety state machine" (proponent §2 Reason C)

This IS the strongest threat to my position. Money Path causality is real: CALIBRATION_HARDENING optimizing against corrupted forecast signal IS worse than no optimization. INV-15 (forecast row canonical cycle identity, pure prose-as-law) WAS flagged in round-2 as a candidate for either upgrade or deletion. If INV-15 violations propagate silently, CALIBRATION_HARDENING amplifies the error.

**Concession**: there exists a SUBSET of Tier 2/3 work that is **substrate-relevant for specific EDGE packets**. Specifically: any Tier 2/3 work that tightens calibration-input-validity (INV-09, INV-15) IS a precondition for CALIBRATION_HARDENING. Equivalent work for ATTRIBUTION_DRIFT (strategy_key audit, INV-04 enforcement) IS a precondition for that packet.

**This narrows my §3 NEW concession 3 ("edge packets carry their own harness needs") into something more concrete**: per-EDGE-packet, identify the substrate-relevant Tier 2/3 subset and treat it as IN-SCOPE for the EDGE work, not as background harness work. Total impact: ~15-25h of Tier 2/3 work moves from "background" to "EDGE precondition" classification.

### Threat T2 — DEEP_PLAN §6.2 trigger 5 already TRIPPED

Proponent §1 Counter-3: *"Cumulative agent process-fault rate > 1 per 12h sustained — review whether harness is producing failures it then catches."* The round-1 retrospective documented 7 / 12h. Per the trigger's own logic, this should pause new work and review the harness.

**Concession**: this is a real call-the-trigger moment. My 70/30 EDGE position implicitly assumes the 7-faults-/12h was attributable to the R3 multi-region parallel apparatus (now retired). Proponent's read is that the trigger applies to the broader harness, not just the retired component.

**Resolution**: this is empirically decidable. Run a 2-week observation window post-Tier-1 with the proposed EDGE_OBSERVATION packet to MEASURE the process-fault rate on the post-Tier-1 harness. If <1/12h sustained → trigger 5 is not active → 70/30 EDGE is safe. If >1/12h → trigger 5 is active → fall back to proponent's 40/60. **The data resolves this disagreement; debate cannot.**

### Threat T3 — Headlands "operations is co-equal product" framing

Proponent §5 NEW Source 1: *"Most companies are roughly divided into 3 main groups: strategy research, core development, and operations"* — Headlands explicitly lists operations as a co-equal product layer. My round-3 proposal characterized harness pruning as "office-cleaning subroutine"; this was rhetorically convenient but mis-frames operations work.

**Concession**: I retract the "office-cleaning" framing. Harness IS operations infrastructure, not janitorial. Operations work is co-equal with research at industry-grade quant firms.

**But**: Headlands also specifies that ALL THREE layers run continuously and persistently. They do not specify a 60% allocation to operations during weeks 1-16 of a new strategy. The Headlands cite supports STEADY-STATE balance, not FRONT-LOADING. **My §5 §5.1 allocation table actually matches Headlands better**: Tier 1 finishes (operations setup) → ~70% edge for the next 16 weeks → steady-state 50/50 maintenance equilibrium. Proponent's 60% harness in months 1-2 is not industry-aligned — it's a one-time front-load that biases the allocation.

---

## §4 Quantitative — what I concede + where my 30% leaves load-bearing surface uncovered

### My concessions on 70/30 (additions to round-3 LOCKED concession bank)

1. **Per-EDGE-packet substrate work** moves from "background" to "in-scope" classification (~15-25h moves into EDGE accounting). My net: 70% EDGE labeled work covers some Tier 2/3 substrate work. **Reframed allocation: 70% EDGE-or-EDGE-substrate / 30% pure-harness-pruning.**

2. **INV-15 + INV-09 (forecast cycle identity + missing-data first-class) MUST be upgraded BEFORE CALIBRATION_HARDENING starts.** This is per Threat T1. If they remain pure prose-as-law, CALIBRATION_HARDENING runs on un-validated input. Total work: ~6-10h, in-scope for CALIBRATION_HARDENING precondition.

3. **Process-fault-rate observation window** is operationally required before locking 70/30. Without empirical data on the post-Tier-1 fault rate, my 70% EDGE assumes a fact not in evidence (per Threat T2). Cost: 2-week empirical observation = no harm to allocation, just delays the lock.

4. **Headlands "operations is co-equal" reframing** — I retract "office-cleaning subroutine" framing. Operations work is product-tier, not janitorial. Steady-state equilibrium should be ~50/50 maintenance, not 90/10.

### Where proponent's 40/60 catches load-bearing surface my 30/70 leaves uncovered

| Area | Proponent's 40% catches | My 30% may miss | Verdict |
|---|---|---|---|
| INV-15 / INV-09 upgrade for CALIBRATION input validity | YES (Tier 2/3 P3-P4) | NOT IN MY PLAN — gap | **Proponent right; I update by adding ~6-10h to CALIBRATION_HARDENING precondition** |
| Drift-checker monitoring during EDGE work | YES (Tier 1 #4 + Tier 3 P6 polish) | YES (Tier 1 #4 in both plans) | **No delta — both plans cover** |
| Operator-attention recovery from R3-debate apparatus | YES (Tier 2 #14 audit) | NOT EXPLICIT — gap | **Proponent right; ~5-10h to formalize "what's removed, what's clean" doc** |
| Per-EDGE-packet relationship tests | NOT EXPLICIT | NOT EXPLICIT | **Both miss; should be in EDGE packet design** |
| `topology.yaml` deeper cut to ≤500 LOC | YES (Tier 3 P2) | YES BACKGROUND | **No delta on outcome; speed differs** |
| `source_rationale.yaml` → docstrings | YES (Tier 3 P4) | YES BACKGROUND | **No delta on outcome; speed differs** |
| @enforced_by decorator prototype | YES (Tier 2 #17) | YES BACKGROUND | **No delta on outcome** |
| EDGE_OBSERVATION shipping speed | DELAYED 4-8 weeks | SHIPS WEEK 2-3 | **My plan ships measurement substrate sooner** |
| CALIBRATION_HARDENING ship date | Month 5-6 | Month 4-5 | **My plan ships ~4 weeks sooner** |

**Net of concessions**: I move from 70/30 → **~62/38 EDGE/HARNESS** with explicit per-EDGE-packet substrate carve-outs. This is closer to proponent's 40/60 but still EDGE-leaning.

---

## §5 NEW WebFetch (cumulative R3 = 3; not recycled from R1+R2's 8 + R3's 4 already cited)

R3 cumulative recycled list per dispatch + current: Renaissance Wikipedia (mine), Paul Graham Do Things That Don't Scale (mine), Headlands Tech 2017-08-03 (proponent), Wikipedia 2010 flash crash (proponent), plus R1+R2's 8.

### Source NEW R3-#3 — Wikipedia, "Knight Capital Group" §"2012 stock trading disruption" (en.wikipedia.org/wiki/Knight_Capital_Group)

URL: `https://en.wikipedia.org/wiki/Knight_Capital_Group`
Fetched: 2026-04-28 ~02:48 UTC
**Not previously cited in R1, R2, or R3 by either side.**

Verbatim quotes — the actual root cause of the $440M Knight Capital event:

> "**a technician forgot to copy the new Retail Liquidity Program (RLP) code to one of the eight SMARS computer servers**"

> "**old Power Peg code still present on that server**"

> "**RLP code repurposed a flag ... to activate ... 'Power Peg'.**"

> "**code to report back the fulfillment ... had been altered ... resulting in the order never being recorded as completed.**"

> "**As a result, the server would send out orders indefinitely.**"

**Application**: Knight Capital's failure was **deployment omission + dormant production code that was not deleted** — NOT missing harness/observability infrastructure. The right antibody class is:
- Deployment validation hooks (Tier 1 #6 — IN BOTH PLANS)
- Dead-code removal CI gate (Tier 1 #4 drift-checker — IN BOTH PLANS)
- Production-state vs configured-state reconciliation (Zeus's `chain_reconciliation.py`)

NONE of these are in the contested Tier 2/3 work. **Proponent's Knight Capital citation in their §1 Counter-1 motivates work that doesn't actually defend against Knight's failure class.** This is a real correction to proponent's analysis: the cited catastrophe does not justify front-loading 90-130h of YAML manifest pruning. Honest reading.

Stronger version: the actual Zeus-side analog of Knight risk lives in `src/state/chain_reconciliation.py` (verifying chain truth vs local cache) and `src/execution/*` deployment paths. Auditing those is ~5-10h of work and IS already covered by Tier 1 hook setup. Proponent's 40/60 puts 90-130h into work that does not address the cited risk.

---

## §6 LOCK FINAL POSITION

Per dispatch directive: "LOCK FINAL POSITION: 70/30 stands / partial accept of proponent's gradualism / move toward synthesized middle."

### Decision: **PARTIAL ACCEPT — move from 70/30 to ~60/40 EDGE/HARNESS** with explicit per-EDGE-packet substrate carve-outs.

I do NOT fully surrender to proponent's 40/60. I retract my over-confident 70/30 in favor of a synthesized middle that honors:

1. **Per-EDGE-packet substrate work IS in-scope for the EDGE packet** (~15-25h moves from "background harness" to "EDGE precondition" classification per Threat T1).
2. **INV-15 + INV-09 must be upgraded BEFORE CALIBRATION_HARDENING starts** (~6-10h precondition).
3. **2-week empirical fault-rate observation window** post-Tier-1 before LOCKING the allocation — if process-fault rate stays >1/12h, fall back to proponent's 40/60. If <1/12h, my 60/40 stands.
4. **Steady-state equilibrium ~50/50** per Headlands "operations is co-equal" framing — NOT 90/10. Both my round-3 phase-1 §5.1 (90/10 asymptote) and proponent's 75-85/15-25 (their §6) are wrong; the right steady-state is ~50/50 maintenance-and-research equilibrium.

### Final synthesized allocation

| Phase | Calendar | Harness % | Edge % | Notes |
|---|---|---|---|---|
| Weeks 1-2 | Tier 1 finishing | **100%** harness | **0%** | Non-negotiable; both sides agree |
| Weeks 3-4 | Empirical fault-rate window + EDGE_OBSERVATION begins | 30% harness / 70% edge | 70% | If fault-rate ≤1/12h, lock 60/40; if >1/12h, fall back 40/60 |
| Weeks 5-12 | EDGE_OBSERVATION ships + WS_OR_POLL + ATTRIBUTION_DRIFT | 35% harness / 65% edge | 65% | Per-packet substrate work in-scope; Tier 2/3 trickle in background |
| Weeks 13-20 | CALIBRATION_HARDENING (HIGH risk) + INV-15/INV-09 precondition | 45% harness / 55% edge | 55% | Higher harness % during HIGH-risk packet |
| Weeks 21-24 | LEARNING_LOOP + Tier 3 wrap | 35% harness / 65% edge | 65% | All 5 packets shipping or shipped |
| Steady-state (month 6+) | continuous | **~50%** maintenance / **~50%** research | | Per Headlands operations co-equal framing |

**6-month average**: ~42% harness / ~58% edge — meaningfully closer to proponent's 40/60 than my original 30/70.

### What this means for round-3 verdict

The honest verdict at round-3 is **NOT "proponent wins" or "opponent wins" on EDGE-vs-SAFETY framing.** It is:

- **My 70/30 was too aggressive on edge-leaning** (failed to account for per-EDGE substrate work + INV-15/INV-09 calibration precondition + Headlands operations co-equal framing).
- **Proponent's 40/60 was too aggressive on harness-leaning in months 1-2** (60% harness in months 1-2 delays EDGE_OBSERVATION measurement substrate by 4-6 weeks unnecessarily; Knight Capital citation does not motivate Tier 2/3 work; operator-attention compounding tax was unmeasured).
- **Synthesized middle ~58/42 EDGE-LEANING** with empirical 2-week observation window + per-EDGE-packet substrate carve-outs is the correct allocation.
- **Steady-state ~50/50** per Headlands operations-co-equal framing replaces both my 90/10 and proponent's 75-85/15-25 asymptotes.

This is not surrender — it is honest synthesis. Per anti-rubber-stamp rule, "a side that admits no downside is not winning." I admit my 70/30 was over-edge-leaning + my "office-cleaning" framing was wrong; proponent should admit Knight/Flash Crash analogies don't motivate Tier 2/3 + operator-tax claim is unmeasured + 60% harness in months 1-2 is excessive.

---

## §7 Self-check (anti-rubber-stamp)

- [x] Engaged proponent's STRONGEST element (Knight + Flash Crash + operator-tax + Z2-amplification) face-value with 5 explicit concessions before pivoting (§1)
- [x] 3 concrete weaknesses in proponent's 40/60 (§2 W1-W3): 90-130h delay-cost / unmeasured tax claim / Knight analogy conflation
- [x] 3 strongest threats proponent's poses to mine (§3 T1-T3): edge-on-safety state machine / DEEP_PLAN trigger 5 already tripped / Headlands operations co-equal
- [x] Quantitative: which of my 70/30 I concede (§4: 4 concessions; revised to ~62/38 then synthesized ~58/42)
- [x] ≥1 NEW WebFetch (Knight Capital Wikipedia, cumulative R3 = 3)
- [x] LOCKED final position (§6: PARTIAL ACCEPT, ~58/42 EDGE/HARNESS averaged 6 months, with empirical observation window + per-EDGE substrate carve-outs + ~50/50 steady-state per Headlands)
- [x] Disk-first write before SendMessage
- [x] No "narrow scope self-validating"
- [x] No "pattern proven" without specific cite
- [x] ≤300 lines (this file: ~280 lines)

---

## Status

ROUND3_CRITIQUE_OPPONENT complete. Final position LOCKED at PARTIAL ACCEPT — ~58/42 EDGE/HARNESS averaged 6 months (synthesized middle between my round-3-phase-1 30/70 and proponent's 40/60), with empirical 2-week fault-rate observation window before final lock + per-EDGE-packet substrate carve-outs + ~50/50 steady-state per Headlands operations co-equal framing.

Single most important finding from this critique cycle: **the headline gap (40/60 vs 70/30) was inflated by both sides — honest middle is ~58/42 EDGE-leaning, with the asymptote at ~50/50 per industry framing, not at the 90/10 or 80/20 either side originally proposed.** Knight Capital cite does NOT motivate Tier 2/3 work (root cause was deployment + dead-code, not harness debt); Headlands cite supports steady-state co-equal operations not front-loaded harness; Renaissance + Paul Graham support EDGE-leaning during Tier 1+2 transition window.

LONG-LAST status maintained pending judge round-3 grading.
