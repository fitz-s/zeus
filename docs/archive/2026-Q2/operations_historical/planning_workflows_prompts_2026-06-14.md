# Zeus Plan-Making — 3 Opus Planning-Workflow Prompt Library (2026-06-14)

```
Created: 2026-06-14
Authority basis: operator directive 2026-06-14 — after the broad investigation (docs/evidence/
  investigation_2026-06-13/), build THE implementation plan via 2-3 opus workflows: fresh-context
  strategists, a deep implementation planner, and other (adversarial/verification/architecture) angles.
  Plans must be FULLY WRITTEN (never trimmed). This is the PLAN phase — no implementation, no live changes.
Purpose: these are the comprehensive, full-fidelity agent prompts for the planning workflows.
  They are written to be materialized into 3 Workflow scripts (P1, P2, P3) and run with the
  orchestrator gating between them.
```

## 0. The contract every planning agent inherits (prepend to EVERY brief)

**WHAT "DONE" MEANS (the only success):** stable, continuous, >51% after-cost SETTLEMENT win-rate on **TRADED** markets, proven by real market-chain evidence of alpha profit. NOT "one order filled." A plan whose endpoint is "an order submits" is a FAILURE — the endpoint is *settlement-graded profitable alpha flowing continuously*.

**OPERATOR LAWS (violating any voids your plan):**
1. "No edge / honest no-edge" is presumed OUR DEFECT until proven by settlement — tradeable alpha is assumed to exist; root-cause the suppression, never excuse it.
2. A fix *just to fill one order* is a FAILURE. Only systematically-correct changes count.
3. Collapse N gates to K (K≪N); NEVER add a gate. No caps / allowlists / maker-only / time-bans / q-haircuts / artificial throttles. Default bias: SIMPLIFY.
4. `buy_no ~90%` win-rate and `cost>0.6` favorite-buying are BASE RATE already in the price — NOT alpha. Real edge = `q_lcb > price after cost` on traded markets; the REAL edge lives in the mid band (0.2–0.6) and the cheap tail where the model's q materially disagrees with the market.
5. Settlement is the ONLY truth. Decide on re-probed reality, not memory.
6. Direction law is inviolable (buy_yes⟺bin≈forecast; buy_no⟺bin≠forecast). ARM: system verified-correct + shadow matches mainstream → live.
7. Output FULLY WRITTEN, never trimmed.
8. **Edge lives in selecting the CORRECT BIN.** Every q / q_lcb / licensing / gate / size is downstream *relay and re-computation* of the bin-selection signal — none of it manufactures edge that a wrong bin-belief lacks. CORRECT METADATA is the precondition: bin identity, the contract↔outcome↔settlement-preimage mapping, city/station/date/boundary — if the metadata is wrong, every downstream value is confidently wrong. The plan must FIRST guarantee metadata + bin-selection correctness, THEN treat the licensing/gate layer as the recompute it is. Suspect the foundation before the gates.

**THE INVESTIGATION RESULT YOU PLAN FROM (read these in full first):**
- **`docs/evidence/investigation_2026-06-13/diagnosis_confirmation.md` — AUTHORITATIVE. This SUPERSEDES the synthesis's central claim.** Verification REFUTED "de-licensing / coverage_unlicensed_tail": that gate is 0.6% of rejections and appears in ZERO of 62,874 receipts. The VERIFIED #1 binding constraint is **q_lcb collapsing to ≈0 on cheap bins → honest `capital_efficiency` rejection (~88%)** — a calibration / LCB-floor / bin-belief (law-8 FOUNDATION) problem. Do NOT build the EMOS license file or re-route the tail gate as the headline fix. Plan from THIS document; treat the synthesis below as superseded evidence, not the target.
- `docs/evidence/investigation_2026-06-13/synthesis.md` (the synthesis — its central de-licensing claim is REFUTED; mine it for keep_invariants + the contradictions, NOT for the target)
- `docs/evidence/investigation_2026-06-13/full_lens_analysis.md` (all 152 lens reports — your evidence base)
- `docs/evidence/investigation_2026-06-13/live_state_tracker.md`, `b2_capital_efficiency_audit.md`, `chatgpt_consult_verdict_digest.md`
- worktree reports: `b1_swept_winner_latch_fix.md`, `b2_penalty_adjudication.md` (candidate fixes — INPUT only, parked, NOT to be assumed correct)
- `AGENTS.md`, `docs/authority/replacement_final_form_2026_06_09.md`

**THE VERIFIED TARGETS (from diagnosis_confirmation.md — these supersede the synthesis):**
- **#1 (headline): q_lcb collapses to ≈0 on cheap bins → honest `capital_efficiency_lcb_ev` rejection (~88%, 18,829/cycle-log).** 115 "best" candidates show q_lcb=0.0000, ev=−1.0. `capital_efficiency` is the HONEST gate (q_lcb−price; KEEP it). The defect is UPSTREAM — why the bin-belief LCB collapses to 0 on cheap/non-favorite bins. The decisive question (law 8 + RULE 1): is q_lcb≈0 a CORRECT bin-belief (the bin genuinely won't win → honest no-edge) or a BROKEN calibration/LCB-floor crushing real mass off the correct bin? Resolve this FIRST — it decides whether suppressed alpha exists at all. Suspect: σ-shape, the q_lcb floor + N_eff width correction (#61), far-bin structural-zero (zero ensemble members = honest) vs cheap-near-bin mis-calibration. This is the metadata/bin-belief FOUNDATION (law 8).
- **#2 (submit-path, secondary): admitted candidates (proof_accepted=1) dying at SUBMIT** with `real_order_submit_disabled` / `event_bound_final_intent_no_submit` (454 cheap-tail receipts). A distinct second blocker that surfaces once admission is fixed — confirm the flag/gate disabling submit.
- **EMOS = red herring:** EMOS computes (shadow `served=emos`) but the live override is OFF — flag `edli_emos_ci_live_enabled` defaults False (operator-arm input, `main.py:1029`); the license file was NEVER built. Do NOT build it as the fix; decide whether the EMOS live lane is even wanted.
- **Cert-blackout (separate-but-downstream):** decision_certificates dark since 06-12T17:04, but proof_accepted=0 ⇒ certs are downstream of the admission collapse; likely clears when q_lcb is fixed. Verify, don't assume.
- **Latch (B1): OPEN now** (self-cleared 06-14T01:06). NOT a blocker. KEEP the absorber (self-heals); the B1 worktree fix is moot.
- **Edge reality (law 5):** the only durable mid-band edge is base-rate buy_no; no tradeable cheap-longshot alpha confirmed yet. The plan must LOCATE the real correct-bin edge (where q honestly disagrees with the market AND settlement backs it), never re-enable favorite-buying as "alpha."

**FORCED-DEPTH PROTOCOL (a shallow plan that "finishes in minutes" is a failure):** you must (1) read the full analysis above, not skim; (2) re-derive the problem from the evidence yourself — do not just restate the synthesis; (3) design with 2–3 WEIGHED alternatives per decision and an opinionated pick + why; (4) specify to file:line / contract / artifact level — concrete enough to implement; (5) anticipate the failure modes and the verification that would catch them; (6) write a SUBSTANTIAL document. Brevity, hand-waving, or "see the synthesis" is a failure. Expect this to take real work.

**HARD BOUNDARY:** this is the PLAN phase. You design and write. You do NOT edit production code, do NOT deploy, do NOT touch live. Every agent writes its output to a file under `docs/evidence/planning_2026-06-14/` and returns a prose summary.

---

# WORKFLOW P1 — Fresh-context strategy panel (divergent, uncontaminated)

**Goal:** N independent opus strategists, each with FRESH eyes — explicitly told to ignore the 100 prior patch-attempts, the accreted gate architecture's assumptions, and any "fix the latch" framing — each designs a COMPLETE end-to-end strategy to reach genuine settlement-proven alpha, from a different framing. Then judge + synthesize.

**Shared fresh-planner preamble:** *"You are a fresh strategist. You inherit the operator contract + the investigation EVIDENCE (the facts/measurements), but you must DISCARD all prior solution-framing: ignore the 100 prior fixes, ignore the existing gate gauntlet's design assumptions, ignore 'just reopen the latch'. Treat the current system's architecture as one option among many, not a given. Design the BEST end-to-end path from the current REALITY to continuous settlement-proven alpha. Be willing to conclude 'rebuild this layer' or 'this band is unbeatable, trade that one' — follow the evidence, not the incumbent design."*

### P1.0 — Metadata & bin-selection FOUNDATION strategist (the root lens; operator law 8)
Premise: edge IS selecting the correct bin; everything else relays/recomputes that, and only correct METADATA yields correct downstream. Audit + design the foundation: is the system selecting the correct bin, and is the bin/contract/settlement-preimage metadata correct end-to-end — city/station/date/boundary identity, YES/NO token mapping, settlement preimage, and the q-mass-on-the-correct-bin belief? Trace ≥2 SETTLED markets end-to-end: did the metadata correctly identify the winning bin, and did q put mass there before settlement? If the diagnosis-confirmation finds the binding constraint is upstream of licensing (q_lcb≤0 / `confidence_band_insufficient` / wrong-bin belief / NULL-source identity), THIS lens owns it and the licensing fixes are secondary. Design how to GUARANTEE metadata + bin-selection correctness as the precondition — a licensing re-route on wrong bin-metadata is worthless. Output: where metadata/bin-selection is provably correct vs broken, and the foundational fix that makes the correct bin win mass and reach the gate honestly.

### P1.1 — Minimal-correct-change strategist
Design the SMALLEST set of *systematically correct* changes (not hacks) that makes genuine alpha flow: which 3–5 changes, why each is correct (not a 1-order hack), the causal chain from each change to a settlement-proven profitable fill, and what it deliberately does NOT touch. Must respect "collapse K≪N."

### P1.2 — Edge-licensing-layer strategist
Premise: the coverage/licensing layer (EMOS license, `coverage_unlicensed_tail`, isotonic min_n) is the disease — it voids edge exactly where it exists. Design the correct edge-licensing architecture: how a cheap-bin candidate with real model-vs-market disagreement gets honestly licensed to trade, what "licensed" should mean, how to make the licensed lane REACHABLE for the tail, and whether `coverage_unlicensed_tail` should be repaired, refit, or deleted. Quantify the edge that is currently de-licensed.

### P1.3 — First-principles decision-path strategist
Redesign the candidate→belief→quote→cost→decision→submit path as K honest decisions (per the K-cut law). Where do the real gates belong, which of the current gauntlet collapse into K, and what is the single admission criterion that admits every real +EV trade and rejects every base-rate-favorite-masquerading-as-edge? Map current gauntlet → target K.

### P1.4 — Edge-quality / calibration strategist
Premise: the issue may be edge QUALITY, not gates — if cheap-tail q_lcb is untrustworthy (FORECAST_BOOTSTRAP inflation, far-bin q_lcb→0, missing settled obs), unblocking it loses money. Design the calibration/edge-evidence fix that makes cheap-bin and mid-band q *trustworthy* OOS (vs market, settlement-graded), so the system trades real edge not noise. State precisely how to PROVE the edge is real before licensing it live.

### P1.5 — Market-structure / opportunity strategist
Premise: maybe the system is fishing the wrong band. The 0.2–0.6 real-edge band collapsed to n=1; only base-rate favorites fill. Design which markets/bands/horizons actually carry exploitable edge for THIS book ($1,162), how to restore the mid-band opportunity flow (cf. the 00Z fusion fanout substrate), and whether the cheap tail is genuine alpha or a longshot trap after friction. Ground every claim in the empirical lens reports.

### P1-JUDGE (×3, diverse lenses) + P1-SYNTH
3 judges score each strategy on: correctness (not a 1-order hack), path-to-settlement-alpha, K-cut compliance, evidence-grounding, risk. Then an opus synthesizer merges the winning strategy + grafts the best ideas from runners-up into ONE coherent strategy-of-record, writes it FULL to `docs/evidence/planning_2026-06-14/P1_strategy_of_record.md`, and states the explicit open decisions for P2.

---

# WORKFLOW P2 — Deep implementation planner (file-level, sequenced, executable)

**Goal:** turn the P1 strategy-of-record + the defect ledger into a concrete, sequenced, *executable* implementation plan. Deep opus, one planner per workstream + a cross-workstream sequencer. Reads P1 output + the full analysis.

**Per-workstream deep-planner brief (run once per workstream):** *"Produce the implementation plan for workstream {W}. FORCED DEPTH: trace the current code path to file:line; specify the exact change (the target contract/function/artifact, before→after), with 2–3 alternatives weighed and your pick; the RED-on-revert test(s) that prove it; the data/migration steps; the dependency on other workstreams; the verification gate (what evidence proves it works — prefer settlement-graded); the risk + the rollback; and an explicit 'is this systematically correct or a 1-order hack?' self-check. Concrete enough to hand to an implementer. Write to docs/evidence/planning_2026-06-14/P2_{W}.md."*

Workstreams (the planner confirms/adjusts from P1 + evidence — this is the seed set):
- **W-CERT** — root-cause + fix the decision-certificate blackout (the separate, still-unknown failure that predates the latch). First: locate WHY certs went dark 06-12T17:04 (this needs its own mini-investigation inside the plan).
- **W-LICENSE** — the missing `state/emos_ci_license.json` + the unreachable licensed lane: regenerate/repair the EMOS license artifact (provenance-correct, fitted — NOT a stub to force trades), and make the licensed lane reachable for tail bins, OR refit/replace `coverage_unlicensed_tail` if it is genuinely over-tight. Must distinguish honest tail-protection from edge-suppression with settlement evidence.
- **W-PENALTY** — adjudicate + (if magic) fix the trade_score `penalty=0.01` cheap-bin suppression (build on `b2_penalty_adjudication.md` but re-verify; double-counted cost vs honest friction).
- **W-MIDBAND** — restore the 0.2–0.6 real-edge band flow (fusion coverage / candidate generation for the band that actually carries alpha).
- **W-EDGEPROOF** — the settlement-graded edge-existence proof harness (event-level, vs market benchmark, after-cost, traded-vs-counterfactual) that must GATE going live — so we never re-enable base-rate favorite-buying as "alpha."
- **W-KEEP** — explicitly document the absorber/reconcile/B1 path as KEEP-DO-NOT-TOUCH, and park the B1/B2 worktree fixes with the reason.

**P2-SEQUENCER:** given all W-plans, produce the critical path: dependency DAG, the ordered sequence of changes, what each unblocks, the canary/shadow/ARM gates between steps, and the single shortest path to the FIRST settlement-proven *correct* alpha fill (not the first order). Writes `docs/evidence/planning_2026-06-14/P2_sequence_and_critical_path.md`.

---

# WORKFLOW P3 — Adversarial + verification + architecture angles

**Goal:** pressure-test the assembled plan before it is finalized. Opus.

### P3.1 — Red-team / plan-refuter (×2-3, hostile)
Try to BREAK the P1+P2 plan. For each proposed change ask: would this actually move the system to *settlement-proven profitable alpha*, or just to "an order submits"? Is any step a 1-order hack? Does any step ADD a gate / cap / throttle (operator-law violation)? Does any step license edge that isn't proven real (re-creating the base-rate illusion)? What breaks downstream? What does the plan FORGET? Kill or demote any step that fails.

### P3.2 — Verification / test-strategy planner
Design the evidence ladder that PROVES the plan worked: per-change RED-on-revert tests; the shadow/canary comparison; and the end-to-end acceptance proof = settlement-graded >51% after-cost win-rate on TRADED markets over an adequate event-level n (with the power threshold). Specify exactly what query/artifact proves each gate, and the ARM criteria for each live flip.

### P3.3 — Architecture adjudicator (targeted-fix vs rebuild)
Given the full picture, adjudicate per layer: TARGETED_FIX | PARTIAL_REBUILD | GROUND_UP_REBUILD, with evidence. Define the K≪N kernel each layer should collapse to, the KEEP-list (load-bearing correct invariants/code), and the migration boundary. Resolve the central tension: is this a repair job or does the edge-licensing/decision layer need a rebuild?

### P3.4 — Sequencing / risk / rollback planner
The safe live-deployment order: what is reversible, what needs shadow-first, the rollback per step, the blast radius, and the operator-gated checkpoints. Nothing irreversible without an explicit gate.

### P3-ASSEMBLER (opus) — THE PLAN
Reads P1 + P2 + all P3 angles → writes the FINAL, FULLY-WRITTEN implementation plan to `docs/evidence/planning_2026-06-14/IMPLEMENTATION_PLAN.md`: the strategy-of-record; the ordered workstreams each with file-level changes + tests + verification + rollback; the targeted-fix-vs-rebuild decision per layer; the keep-list; the critical path to the first settlement-proven alpha fill; the acceptance/ARM gates; and the explicit unresolved decisions for the operator. NO trimming, NO "see other doc" — self-contained and complete.

---

## Run order & gating (orchestrator)
P1 (fresh strategy) → read P1_strategy_of_record → P2 (implementation, seeded by P1) → read P2 → P3 (adversarial/verification/architecture, seeded by P1+P2) → P3-ASSEMBLER writes IMPLEMENTATION_PLAN.md → **operator review** → only then implementation. Each workflow opus-tier, forced-depth protocol, all outputs persisted FULL under `docs/evidence/planning_2026-06-14/`. Nothing touches live in any of the three.

Status: PROMPTS READY. Awaiting operator go to materialize P1/P2/P3 as Workflow scripts and run (P1 first; P2/P3 gate on the prior's output).
```
