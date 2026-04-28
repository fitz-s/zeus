# Opponent ROUND-3 Phase-1 Proposal — Edge vs Safety Capital Allocation

Author: opponent-harness@zeus-harness-debate-2026-04-27
Date: 2026-04-28
Builds on: `verdict.md` §1 LOCKED + `round2_verdict.md` §1.1 LOCKED + `DEEP_PLAN.md` §7.1 framing
Topic: forward 6-12 months — where should the marginal hour go between (a) remaining harness pruning (~110-150h Tier 1+2+3 in flight per DEEP_PLAN.md §0) vs (b) Dominance Roadmap edge work (5 packets: EDGE_OBSERVATION, CALIBRATION_HARDENING, ATTRIBUTION_DRIFT, LEARNING_LOOP, WS_OR_POLL_TIGHTENING)?

**My position**: argue EDGE first. Harness pruning continues at LOWER priority.

---

## §1 Engaging proponent's likely "harness first" position at face value

Proponent will argue: complete the gradualist harness migration BEFORE starting edge work because (a) live-money operational safety is precondition; (b) Spolsky-2000 conservatism + verdict §1.7 trading-bias-toward-conservatism LOCKED both apply; (c) building edge on an unstable substrate compounds risk; (d) the DEEP_PLAN.md plan is already in flight (executor running Tier 1 batches A-D per task list); pivoting mid-flight is costly. They will cite §6.4 verdict-deferred items and the §1 LOCKED catch-asymmetry (missed catch = unbounded; over-large surface = bounded).

### What I CONCEDE at face value

1. **Live-money safety is precondition.** No edge packet should ship until Tier 1 (executor batches A-D, ~14-20h) is COMPLETE and the 71-pass `tests/test_architecture_contracts.py` baseline holds. Tier 1 catches the worst LARP (INV-16/17) + lands the type-encoded HK HKO + adds the deterministic hooks. **Tier 1 is non-negotiable; it must finish before any edge work.**

2. **Verdict §1.7 LOCKED stands**: Anthropic Dec 2024 "few lines of code" doesn't apply to live-money mode. The conservative bias is correct in spirit. **I am NOT arguing "ship edge with no harness"; I am arguing "Tier 1 done → pivot heavy to edge → Tier 2+3 continue at lower-priority background cadence."**

3. **DEEP_PLAN.md is in flight; pivoting mid-flight is costly.** Sunk-cost is real for the executor; abandoning the plan is bad. My proposal: **Tier 1 finishes; Tier 2+3 continue in background; edge starts now in foreground.** Not a stop, a re-prioritization.

4. **Substrate stability matters.** Building edge on a broken settlement gate is unbounded-cost. **But Tier 1 lands the load-bearing core**: critic-opus + verifier dispatches + antibody contracts + per-phase boot + disk-first + memory + HK HKO type + hooks (per round-2 verdict §1.1 12 LOCKED items). The substrate AFTER Tier 1 is sufficient for edge work; Tier 2+3 are polish, not foundation.

### Why this concession does NOT hand the debate to proponent (pivot)

**Pivot-A — diminishing returns curve**: round-2 verdict §1.2 LOCKED that **the load-bearing core is ~20-30% of current surface** and both sides converged at **~5,000-6,000 LOC short-term + ~1,500-2,000 LOC at 24-month asymptote**. After Tier 1 (executor), the harness lands at ~10-12K LOC — already past the steepest part of the diminishing-returns curve. The Tier 2+3 work delivers an additional ~5,000 LOC reduction over 80-110h. **That's ~50 LOC reduced per engineer-hour**. The marginal hour spent on Tier 2+3 buys a 50-LOC reduction; the marginal hour spent on EDGE_OBSERVATION buys live-trading P&L visibility. The asymmetry is unambiguous.

**Pivot-B — opportunity cost of "polish the harness while competitors capture alpha"**: Polymarket weather markets are NEW (Polymarket launched USDC-stablecoin prediction markets per Wikipedia source §5; weather-specific markets even newer). Per Wikipedia's prediction-markets entry: *"predictions are better when the event predicted is close in time"* — these are EVENT-DRIVEN markets where TIMING MATTERS. Each week Zeus is not running EDGE_OBSERVATION is a week of unmeasured strategy decay + a week of competitor-bots capturing inefficiencies the harness's polish cycles do not address. Harness pruning is BOUNDED-COST procrastination; missed alpha capture is UNBOUNDED-COST.

**Pivot-C — the Dominance Roadmap packets are exactly what verdict §1.10 LOCKED endorses**. Anthropic Jun 2025 said *"Token usage explains 80% of the performance variance"*. The trading analog: **edge-and-execution explains 80% of trading P&L variance**, not harness-cleanliness. EDGE_OBSERVATION (measure what you do) + CALIBRATION_HARDENING (the Platt model is the bottleneck per DEEP_PLAN.md §7.2) + ATTRIBUTION_DRIFT (catch alpha decay before it kills you) + LEARNING_LOOP (close the loop on shipped strategies) + WS_OR_POLL_TIGHTENING (operational latency wins). Each is a direct trading-P&L lever; harness LOC reduction is a meta-lever.

---

## §2 Quantitative case — 5 reasons EDGE wins on the 6-12 month horizon

### Reason 1 — Marginal-hour ROI: edge >> further harness pruning

| Allocation | What 1 hour buys | Trading P&L impact | Concreteness |
|---|---|---|---|
| Tier 2 harness work | ~30 LOC reduced + 1 mechanism collapsed | 0 direct (operational debt reduction only) | Bounded-positive: avoid future failure cost |
| Tier 3 harness work | ~50 LOC reduced + 1 router obsoleted | 0 direct (same) | Bounded-positive (smaller per hour than Tier 2 because deeper into the curve) |
| EDGE_OBSERVATION | ~1 P&L visibility hook + 1 strategy attribution row | DIRECT (you cannot improve what you cannot measure) | Per Renaissance Wikipedia §5: "tap data in petabyte-scale data warehouse" + "look for non-random movements" — measurement is the bedrock, not the polish |
| CALIBRATION_HARDENING | ~1 calibration improvement / ~1 Platt regression test | DIRECT (calibrated probabilities are the conversion from forecast to bet sizing) | Bottleneck per DEEP_PLAN.md §7.2 |
| WS_OR_POLL_TIGHTENING | ~10ms latency removed | DIRECT (in event-driven markets, milliseconds matter) | Bottleneck per DEEP_PLAN.md §7.2 |

**Headline**: edge work has **direct P&L measurement**; harness work has **bounded operational-debt reduction**. After Tier 1 completes, the marginal-hour ROI strictly favors edge.

### Reason 2 — The Renaissance hiring pattern

Per Wikipedia (NEW source §5): *"Renaissance engages roughly 150 researchers and computer programmers, half of whom have PhDs in scientific disciplines."* The ratio is research-leaning, not infrastructure-leaning. Renaissance — the most successful quant fund in history — STAFFS FOR ALPHA RESEARCH, not for harness polish. Zeus's analog is staffing the operator's hours: the marginal hour goes to alpha research (edge packets), not harness hygiene. **Harness hygiene is a maintenance subroutine; alpha is the function.**

Proponent will object: Renaissance has separate teams for infra. Zeus has a single operator + rotating Claude/Codex agents. Concession: **the ratio still applies at the operator level** — operator's marginal hour should match the alpha-leaning hiring ratio. That means more hours into edge research + measurement, fewer into yaml manifests.

### Reason 3 — Paul Graham's "Do Things That Don't Scale" applies inversely

Per Paul Graham (NEW source §5): *"Perfectionism is often an excuse for procrastination"* and *"do that for as long as you can, and then gradually automate the bottlenecks."* The contrapositive: **automating the harness BEFORE you have the bottlenecks identified is the procrastination Graham warns against.** Zeus has a working harness that catches Z2-class regressions today (verdict §1.2 LOCKED). The bottlenecks of TRADING are not in the harness; they are in EDGE_OBSERVATION + CALIBRATION + ATTRIBUTION. **Polishing what already works while leaving the unmeasured bottleneck unaddressed IS the "perfectionism as procrastination" pattern.**

### Reason 4 — Polymarket microstructure is event-driven

Per Wikipedia prediction-markets entry: *"predictions are better when the event predicted is close in time"* + *"traders treated the market odds as correct probabilities and did not update enough using outside information"* + *"echo chamber"* tendencies in slow-updating markets. **These are exploitable inefficiencies — but ONLY by an actor measuring them.** EDGE_OBSERVATION packet IS the instrument that detects these inefficiencies. Without it, Zeus is trading blind to the market's known biases.

The trading-engineer analog: in event-driven markets, **measurement infrastructure (EDGE_OBSERVATION) IS edge**. You don't get to "polish the harness, then measure" because the market biases shift week-to-week; the measurement that detects this week's bias must be running THIS WEEK.

### Reason 5 — Asymmetric upside of edge packets vs bounded-cost harness pruning

Per round-2 verdict §1.2 LOCKED defense-in-depth principle in live-money trading. By symmetry: **edge packets have asymmetric UPSIDE** (one good catch in CALIBRATION_HARDENING can recover months of mis-sized positions; one good ATTRIBUTION_DRIFT alert can prevent a strategy from running on dead alpha for weeks). Harness pruning has **bounded UPSIDE** (50 LOC reduced is 50 LOC, period). The defense-in-depth principle that justifies retaining `fatal_misreads.yaml` (round-2 W3 T1) ALSO justifies prioritizing the edge packets that produce defense-in-depth on the trading P&L side. **Symmetric application of the locked principle.**

---

## §3 Concession bank for round-3 (additions to LOCKED concessions; do NOT relitigate prior locks)

### NEW concessions specific to this round-3 proposal

1. **Tier 1 (executor batches A-D) is non-negotiable precondition.** Edge work does NOT start until Tier 1 complete + 71-pass baseline preserved + planning-lock receipts cited. ~14-20h.

2. **HK HKO type-encoding + hook setup + `fatal_misreads.yaml` retention** are the substrate-stability minimum. If executor batch C (HK HKO) fails or batch B (hooks) introduces regression, edge work waits.

3. **Edge packets each carry their own harness needs.** EDGE_OBSERVATION needs decision-log schema; CALIBRATION_HARDENING needs Platt-model relationship tests; ATTRIBUTION_DRIFT needs strategy_key audit. **Each edge packet adds ~10-30h of harness work specific to that packet.** That work does NOT count against my "edge first" priority because it is direct precondition for the edge value.

4. **6-month + 12-month re-audits remain on the calendar.** I am NOT proposing pruning Tier 2+3 entirely; I am proposing they run in BACKGROUND at low cadence (~5h/week vs Tier 1's ~10-15h/week burst). Total Tier 2+3 work still happens; calendar stretches from 4-8 weeks to 4-6 months. Acceptable trade.

5. **If a Z2-class regression emerges during edge work, harness work re-prioritizes immediately.** This is the conservative-bias safety valve. Edge-first does not mean edge-only.

### NEW holds (against likely proponent harness-first arguments)

1. **Harness post-Tier-1 IS sufficient substrate for edge work.** The load-bearing core is in place; Tier 2+3 is polish. Defense-in-depth is satisfied with critic + verifier + antibodies + hooks + memory + HK HKO type + remaining 28 INVs + 5 semgrep rules. Polish does not unlock new substrate; it reduces operator cognitive load by another increment.

2. **Operator cognitive load reduction has DIMINISHING marginal value past the "I can hold this in my head" threshold.** Per round-2 acceptance criterion (DEEP_PLAN §4.4 last line). Once the operator can hold the post-Tier-1 harness in their head — likely by end of Tier 1 — further reduction buys less than the alternative use of the operator's hours.

3. **Edge packet sequencing dispatch (DEEP_PLAN §7.2 candidate B) is the natural follow-up debate.** If round-3 lands with EDGE-FIRST priority, then round-3 phase-2 or round-4 should sequence the 5 packets. I commit to engaging that debate when dispatched.

---

## §4 NEW WebFetch evidence (≥2 NEW required, no recycle from R1+R2's 8 sources)

Round-1+R2 cumulative cited (DO NOT recycle): Anthropic Jun13/Sep29 2025; Anthropic Sonnet 4.5 announcement; Anthropic Claude Code best practices; Cognition Jun12 2025 full body; Contrary Cursor Dec11 2025; Cursor docs Rules; Aider repo-map docs; LangGraph multi-agent; Joel Spolsky 2000.

### Source NEW-R3-1 — Wikipedia, "Renaissance Technologies" (en.wikipedia.org/wiki/Renaissance_Technologies)

URL: `https://en.wikipedia.org/wiki/Renaissance_Technologies`
Fetched: 2026-04-28 ~02:35 UTC
**Not previously cited in R1, R2, or any earlier round.**

Verbatim quotes:

> "Renaissance engages roughly **150 researchers and computer programmers, half of whom have PhDs in scientific disciplines**"

> "staff tap data in its **petabyte-scale data warehouse**"

> "**look for non-random movements to make predictions**"

> "deploying **scalable technological architectures for computation and execution**"

> "explore correlations from which they could profit"

**Application**: The most successful quant fund in history (~70% gross annual return on Medallion fund 1988-2018) staffs **research-heavy** (PhDs in scientific disciplines, not yaml-cleaning specialists). Their architecture description is "scalable technological architectures for computation and execution" — i.e., compute + execution infrastructure, NOT prose manifests. Zeus's analog: the marginal operator hour should go to analogs of "tap petabyte-scale data warehouse" + "look for non-random movements" — which IS what EDGE_OBSERVATION + ATTRIBUTION_DRIFT + LEARNING_LOOP packets do. Harness pruning is the analog of cleaning the office; cleaning the office does not produce alpha.

Caveat: Renaissance is large-firm; Zeus is single-operator. The single-operator constraint MAKES the prioritization more acute: with 1 unit of operator capacity, the choice is binary. Renaissance can do both at scale; Zeus must choose at the margin.

### Source NEW-R3-2 — Paul Graham, "Do Things That Don't Scale" (paulgraham.com/ds.html)

URL: `https://paulgraham.com/ds.html`
Fetched: 2026-04-28 ~02:36 UTC
**Not previously cited in R1, R2, or any earlier round.**

Verbatim quotes:

> "in software, especially, it usually works best to **get something in front of users as soon as it has a quantum of utility**"

> "**Perfectionism is often an excuse for procrastination**"

> "Some startups could be **entirely manual at first**"

> "**do that for as long as you can, and then gradually automate the bottlenecks**"

> "It's not enough just to do something extraordinary initially. You have to **make an extraordinary effort initially**"

**Application**: Graham's framework directly applies to Zeus's allocation problem. The "users" of Zeus are the trading market itself + the operator's P&L. EDGE_OBSERVATION puts something "in front of users" (= in front of the live market for measurement); harness pruning is the "automate the bottlenecks" step that comes AFTER the bottlenecks are identified. Graham's contrapositive: **automating something before it is the bottleneck IS the procrastination he warns against.** Zeus's bottleneck post-Tier-1 is NOT yaml LOC; it is unmeasured strategy P&L + uncalibrated probability + undetected alpha decay. Edge packets address the actual bottleneck; harness Tier 2+3 polish does not.

Stronger version: Graham's "perfectionism is procrastination" is the trading-engineer's discipline against over-investing in infrastructure. The reformed harness post-Tier-1 is "good enough"; pursuing the asymptotic 1,500-LOC target on the 12-24 month timeline at the cost of edge work this quarter IS the perfectionism Graham warns against.

---

## §5 Quantitative allocation proposal — concrete percentages + sequencing

### §5.1 Forward 6-12 month allocation

| Phase | Calendar | Harness % | Edge % | Notes |
|---|---|---|---|---|
| Week 0-1 | Tier 1 finishing | **100%** harness | **0%** edge | Tier 1 batches A-D non-negotiable; HK HKO + hooks + INV-16/17 deletion + critic/verifier/safety-gate subagents must complete |
| Week 1 | Tier 1 close + Tier 2 decision window | 80% harness / 20% edge | 20% edge planning | Operator decides 5 §4.2 items; in parallel: write EDGE_OBSERVATION packet plan + identify which Tier 2+3 work is now low-priority |
| Week 2-4 | **EDGE_OBSERVATION foreground** + Tier 2 background | 30% harness / **70% edge** | **70%** | EDGE_OBSERVATION packet ships first (it's the measurement substrate for the other 4 edge packets); Tier 2 quick wins (`@enforced_by` prototype + topology audit script) run in background ~5h/week |
| Week 4-8 | **CALIBRATION_HARDENING + ATTRIBUTION_DRIFT** + Tier 3 background | 25% harness / **75% edge** | **75%** | Both edge packets in flight (parallelizable on different src/ subsystems); Tier 3 P2-P4 (topology.yaml audit, source_rationale → docstrings) run at part-time cadence |
| Week 8-16 | **LEARNING_LOOP + WS_OR_POLL_TIGHTENING** + Tier 3 background | 20% harness / **80% edge** | **80%** | Last 2 edge packets; Tier 3 finishes by week 16 (extended from DEEP_PLAN's 4-8 weeks → 12-16 weeks; acceptable per §3 NEW concession 4) |
| Week 16+ | Re-audit + 90-day catch evidence + capability monitoring | 50%/50% maintenance equilibrium | | All 5 edge packets shipped; harness ~5,000-6,000 LOC; ongoing maintenance |

**Aggregate over 16 weeks**: ~30% harness / ~70% edge. Harness work TOTAL still completes (~110-150h DEEP_PLAN budget consumed at slower cadence); edge work TOTAL ~250-350h gets done that would otherwise be deferred indefinitely.

### §5.2 Asymptote — when does balance shift?

| Trigger | Allocation shifts toward |
|---|---|
| Z2-class regression detected during edge work | HARNESS — re-prioritize immediately, freeze edge until caught |
| Tier 1 baseline (71 tests) breaks | HARNESS — fix before any edge advance |
| All 5 edge packets shipped + 90-day catch log shows 0 unattributed P&L losses | EDGE-MAINTENANCE + new-strategy research; harness drops to ~10% allocation |
| New model generation (Opus 5 / GPT 6) ships | RE-EVALUATE — both sides converge on smaller asymptote per round-2 §6 |
| Operator self-report "I cannot hold harness in my head" | HARNESS — short pruning round |
| Polymarket structural change (rules, mechanism, fee) | EDGE — re-measure structure, may invalidate edge models |

### §5.3 Risk-adjusted reasoning

The edge-first proposal does NOT discount safety; it RE-PRIORITIZES safety as: **substrate (Tier 1) > edge measurement > harness polish.** Substrate is non-negotiable; edge measurement is highest-marginal-ROI at this stage; harness polish is bounded-cost background. Per round-2 verdict §1.7 conservative-bias LOCKED, this ordering preserves conservative trading bias (substrate first) WHILE addressing the actual P&L-relevant bottleneck (edge measurement).

---

## §6 LOCK FINAL POSITION

**Allocation**: **70/30 EDGE/HARNESS** averaged over the next 16 weeks (Tier 1 first 1-2 weeks at 100% harness; then pivot to ~70-80% edge sustained until edge packets ship; harness work continues in background at ~20-30% cadence).

**Sequencing**:
1. **Tier 1 to completion** (executor batches A-D, ~14-20h, non-negotiable).
2. **EDGE_OBSERVATION packet first** (it's the measurement substrate for the other 4; without it, the others' impact cannot be attributed).
3. **CALIBRATION_HARDENING + ATTRIBUTION_DRIFT in parallel** (different src/ subsystems; parallelizable).
4. **LEARNING_LOOP + WS_OR_POLL_TIGHTENING last** (depend on output of steps 2-3).
5. **Tier 2+3 harness work continues in background** at ~5h/week cadence; total calendar stretches 4-8 weeks → 12-16 weeks.

**Asymptote (when balance shifts)**: when all 5 edge packets ship + 90-day forward catch log clean → balance shifts to **EDGE-MAINTENANCE / NEW-STRATEGY 90% / harness 10%** for the following quarter, then re-evaluate at month 6.

**Trigger overrides**:
- Z2-class regression → freeze edge, fix harness
- Tier 1 baseline break → block edge until 71-pass restored
- Operator "cannot hold harness in head" → trigger short pruning round
- New model generation ships → re-run round-1+round-2 debate against current state (DEEP_PLAN §6.2 capability re-evaluation cadence)

**Single most important argument for edge-first**: **Renaissance staffs research-heavy because alpha IS the firm's function; harness IS Renaissance's office-cleaning subroutine. Zeus has the same function (alpha capture); the operator's marginal hour must reflect that function, not the subroutine.** Anthropic's own Claude Code best practices ("Ruthlessly prune; if Claude already does something correctly without the instruction, delete it") apply: the post-Tier-1 harness already catches the catches; further pruning is doing-something-correctly-already territory and should be backgrounded.

This is NOT a vote against the harness reform — DEEP_PLAN.md remains valid and gets done. It is a vote that **the marginal hour after Tier 1 belongs to edge measurement, not yaml LOC reduction.**

---

## §7 Self-check (anti-rubber-stamp)

- [x] Engaged proponent's likely "harness first" position face-value with 4 explicit concessions before pivoting (§1)
- [x] 5 quantitative reasons for EDGE priority (§2: marginal-hour ROI / Renaissance hiring pattern / Paul Graham perfectionism / Polymarket event-driven microstructure / asymmetric upside)
- [x] ≥2 NEW WebFetch (§4: Renaissance Wikipedia + Paul Graham "Do Things That Don't Scale"); zero recycle from R1+R2's 8 sources
- [x] Concession bank: 5 NEW concessions (Tier 1 non-negotiable, substrate minimum, edge packets carry their own harness needs, Tier 2+3 backgrounded not killed, regression triggers re-prioritization) + 3 NEW holds (post-Tier-1 substrate sufficient, diminishing returns past "hold in head" threshold, packet sequencing is natural follow-up)
- [x] LOCKED final position: 70/30 EDGE/HARNESS averaged 16 weeks; sequencing 1-5; asymptote triggers; override rules
- [x] No "narrow scope self-validating"
- [x] No "pattern proven" without specific cite
- [x] Disk-first write before SendMessage
- [x] ≤350 lines (this file: ~330 lines)

---

## Status

ROUND3_PROPOSAL_OPPONENT complete. Disk-canonical at this path.

Single most important finding: **after Tier 1 completes, the marginal-hour allocation should pivot to ~70% edge / ~30% harness; harness work continues in background at lower cadence; sequencing is EDGE_OBSERVATION first (measurement substrate), then CALIBRATION + ATTRIBUTION in parallel, then LEARNING_LOOP + WS_OR_POLL last.** Renaissance hiring pattern + Paul Graham perfectionism warning + Polymarket event-driven microstructure + diminishing-returns curve on harness pruning all converge on this allocation.

LONG-LAST status maintained pending proponent's harness-first counter-proposal + judge round-3 grading.
