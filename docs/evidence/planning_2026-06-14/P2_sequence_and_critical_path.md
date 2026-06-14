# P2 — SEQUENCE & CRITICAL PATH: The Shortest Settlement-Proven Correct-Bin Alpha Fill

**Date:** 2026-06-14
**Mode:** PLAN-MAKING (sequencing synthesis only — no production edits, no deploy, no live touch). DBs opened `?mode=ro`, `timeout 25` per sqlite3.
**Role:** P2 critical-path owner. This document does NOT re-plan any workstream — it consumes the four authored plans (`P1_strategy_of_record.md`, `P2_W-QLCB.md`, `P2_W-SUBMIT.md`, `P2_W-EDGE-LOCATE.md`) and produces the **dependency DAG, the ordered change sequence, the shadow/canary/ARM gates between steps, and the single shortest path to the FIRST settlement-proven correct-bin alpha fill** — which is NOT the first order, and NOT "submission unblocked" (operator contract: both are FAILURE states).
**Authority spine:** the four sibling plans; `diagnosis_confirmation.md` (authoritative target); operator contract laws 1–8; live `config/settings.json`; `state/zeus-world.db`, `state/zeus-forecasts.db`, `state/zeus_trades.db` (read-only).

---

## 0. THE LOAD-BEARING FACTS, RE-VERIFIED THIS SESSION (the sequence rests on these)

Every gate in the sequence below is grounded in a fact checked at source or DB this session, not inherited:

| Fact | Value | Source (this session) | What it forces in the sequence |
|---|---|---|---|
| Submit master arm | **ON** | `config/settings.json`: `real_order_submit_enabled=true`, `reactor_mode="live"`, `edli_live_operator_authorized=true`, `edli_live_scope="forecast_plus_day0"` | There is NO flag to flip to "unblock submission." The path's first node is NOT a flag. |
| Live q_lcb primary path | **BUNDLE** | `openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled=true` | T3/T4 must intercept the bundle producer (`materializer.py`), not only the canonical fallback. |
| Coverage shrink | **ACTIVE live** | `q_lcb_settlement_coverage_gate_enabled=true` | The bidirectional calibration (T3) replaces a LIVE shrink-only seam, not a shadow one — so the shadow flag must default to byte-identical-shrink-only. |
| EMOS live override | **OFF (default)** | `edli_emos_ci_live_enabled` ABSENT in config | Red herring confirmed. NOT on the path. Do not build the license file. |
| `anchor_sigma_c` | **3.0 on ALL 3498 posteriors** | `SELECT json_extract(provenance_json,'$.anchor_sigma_c'),COUNT(*) FROM forecast_posteriors GROUP BY 1` → `3.0\|3498` | The center-jitter crush (T3a / W-QLCB §0.2) is real and universal — the producer fix is not optional. |
| VERIFIED settlements | **7009, 2024-01-01→2026-06-13** | `SELECT COUNT(*),MIN,MAX FROM settlement_outcomes WHERE authority='VERIFIED'` | The isotonic map (T3) and the E2 harness (T6) have sufficient fit data. The fit step is not data-blocked. |
| Regret substrate graded | **20009 of 260853** | `SELECT COUNT(*),SUM(would_have_won NOT NULL) FROM no_trade_regret_events` | E1/E2 (W-EDGE-LOCATE) can be built and run TODAY; they are not blocked on any sibling. |
| B1/M5 submit latch | **OPEN (0 unresolved)** | `SELECT COUNT(*) FROM exchange_reconcile_findings WHERE resolved_at IS NULL` → `0` | The latch is not on the critical path; it self-heals (keep-list). |

**The single most important sequencing consequence of these facts:** the binding constraint is **upstream at admission (q_lcb), not at submit and not at a flag.** Therefore the critical path is dominated by T3/T4 (the q_lcb-input fix), gated by T6/E2 (settlement grading), and W-SUBMIT (T5) is a *latent* downstream node whose value only materializes after admission produces a real ring candidate. A sequence that puts "unblock submit" first is inverted and is the failure the operator named.

---

## 1. THE NODES (the atomic, separately-shippable changes)

The four plans decompose into **11 atomic nodes** across the six thrusts. Each node names its owning plan, its blast radius, and whether it changes live behavior. Nodes are the unit of the DAG.

| Node | Thrust | Owner plan | What it does | Live-behavior change? |
|---|---|---|---|---|
| **N1** | T1 | P1 §3 T1 | Cycle-summary attribution fix: `best=… rejected_by=<actual kill-gate>` (`event_reactor_adapter.py:7149-7206`) | **No** (log text only) |
| **N2** | T2 | P1 §3 T2 | Pure-subtraction gate-mass deletions: collapse the dead source-allow-list to the live coverage verdict; delete the dead C2/C3 selection-shrinkage import (`:2811`); delete the dead δ-penalty / shadow N_eff·JS fields on the live q_lcb seam | **No** (every deleted path is already dead live) |
| **N3** | T6/E1 | W-EDGE-LOCATE §3 | The read-only edge-location query + CLI (`src/analysis/edge_location.py`, `scripts/edge_location_report.py`) — the dated edge map | **No** (read-only analysis) |
| **N4** | T6/E2 | W-EDGE-LOCATE §4 | The grading harness: five INV-CAL contracts + per-cell `EdgeVerdict` + dated-JSON evidence artifact (no DB authority) | **No** (read-only verdict; recomputable) |
| **N5** | T3a | W-QLCB §1.3, §2.2 | Producer fix: replace `center_sigma_c=anchor_sigma_c(3.0)` with a settlement-fitted `σ_center` (`state/sigma_center_fit.json`, candidate); rematerialize bundle q_lcb into a **shadow** column | **No** while `candidate=true` (shadow only) |
| **N6** | T4 | W-QLCB §1, P1 T4 | Promote the σ-shape fit (`state/sigma_scale_fit.json`) into the live primary **point-q** construction after its `_meta.promotion` forward-fill validation | **YES** (moves point-q tail mass → ring) — first live-behavior node |
| **N7** | T3b | W-QLCB §1.2 | The bidirectional settlement-isotonic q_lcb authority (`src/calibration/settlement_calibrated_qlcb.py`) — the UP arm; reuses `_isotonic_realized_rate`; clamped `min(realized−margin, q_point)`. Runs **shadow-first** behind a time-boxed flag | **No** while shadow flag OFF (byte-identical to today's shrink-only) |
| **N8** | T5 | W-SUBMIT §4 (W-S1) | Submit-lane observability: persist-boundary assert that every `edli_no_submit_receipts` row names its `submit_lane`; degrade cause stamped | **No** (telemetry only) |
| **N9** | T5 | W-SUBMIT §2 (W-S2) | Mode-flip terminal-abort → re-decision under `capital_efficiency`: `_resolve_final_order_mode_or_abort` replaces `_validate_…_or_abort` (`event_reactor_adapter.py:4015-4020`) | **YES** (a flipped admitted candidate that re-clears now submits) |
| **N10** | T5 | W-SUBMIT §3 (W-S3) | Locked-opportunity de-dup: replace covert `improve_delta=0.02` with the market tick size (`:4733`) | **YES** (re-quote at ≥1 tick); negligible (5 receipts all-time) |
| **N11** | T3/T5 | W-EDGE-LOCATE §5 (E3) | Candidate-focus: replace `direction_law` hardcoded `k=1.0` with per-cell `k_eff(cell)` from E2 (defaults byte-identical to 1.0; widens ONLY on an E2-LICENSED cell) | **YES** but byte-identical until a cell is LICENSED |

---

## 2. THE DEPENDENCY DAG

### 2.1 Edges (X → Y means "X must be done/proven before Y")

```
N1 (T1 observability) ──────────────┐
                                     ├──► N2 (T2 dead-gate deletion)
                                     │       │
                                     │       ├──► N5 (T3a producer σ_center, shadow)
                                     │       │       │
                                     │       └──► N6 (T4 σ-shape point-q, LIVE) ──┐
                                     │               │                            │
                                     │               └──► N7 (T3b bidirectional ──┤  (N7 validated AGAINST N6's
                                     │                        q_lcb, shadow)      │   corrected point-q ceiling)
                                     │                            │               │
                                     │                            ▼               │
N3 (E1 query) ──► N4 (E2 harness) ───┴───────────────► [E2 GATE] ◄───────────────┘
                    │                                      │
                    │                                      ├──► N7 promote shadow→LIVE
                    │                                      ├──► N5 promote candidate→LIVE
                    │                                      └──► N11 (E3 candidate-focus, LIVE)
                    │
                    └──► (N9, N10 promotion also consult E2)

N8 (W-S1 observability) ── independent, ship anytime ──► (helps read every node's effect)

N9 (W-S2 mode re-decision) ── gated by ──► [admission fix N5+N6+N7 in shadow producing a real ring candidate]
N10 (W-S3 tick de-dup) ── independent, LOW priority, fold into task #64 ──► anytime
```

### 2.2 The DAG as a topological partial order (what is parallelizable)

**Three independent roots can start in parallel on day 0:**
- **Root A (signal-cleaning):** N1 → N2. Pure subtraction; zero risk; cleans the log and the gate-mass so every later effect is legible.
- **Root B (grading instrument):** N3 → N4. Pure read-only analysis on the existing regret+settlement substrate (20009 graded rows, verified this session); has **no code dependency** on any other node.
- **Root C (observability):** N8. Independent; ship anytime; makes every downstream node's effect visible on the receipt.

**The convergence node is `[E2 GATE]` (N4 live as the verdict authority).** Every live-behavior promotion (N5→live, N6, N7→live, N11) passes through it. This is the load-bearing structural fact: **N4 (E2) is the single gate that converts "shadow evidence" into "live promotion," and it is buildable first.** Building it early is the highest-leverage move in the whole sequence — it is the instrument that makes every other promotion settlement-proven rather than declared.

### 2.3 The critical (longest) path through the DAG

The critical path — the chain that determines the minimum time-to-first-proven-alpha-fill — is:

```
N1 → N2 → N5 (σ_center shadow) → N6 (σ-shape point-q LIVE) → N7 (bidirectional q_lcb shadow)
   → [shadow ring cohort accrues settled events] → N4/E2 LICENSES a ring cell
   → N7 promote shadow→LIVE → N9 (W-S2 protects the candidate at submit)
   → live ring fill → SETTLES → E2 grades >51% after-cost at n≥30 → DONE
```

The **rate-limiting segment is NOT code** — it is the **shadow accrual of settled ring events** between N7-shadow and the E2 LICENSE (§5). Code for N1–N7 can land in days; the settled-event cohort to license a ring cell (n≥30 distinct city·date·bin events, walk-forward) is governed by how fast ring markets settle, which the plan can accelerate (backfill historical settled dates) but cannot fabricate (law 5: settlement is the only truth).

---

## 3. WHY THIS ORDER — WHAT EACH NODE UNBLOCKS

Each edge in the DAG exists for a specific, evidence-grounded reason. The order is not arbitrary; reversing any edge breaks a correctness invariant.

- **N1 before everything (T1):** until the cycle-summary stops conflating display-EV with the kill-gate (`event_reactor_adapter.py:7149-7206`), no later q_lcb change can be *observed* to have had its intended effect — the UP arm's admissions would be invisible. N1 unblocks **trustworthy observation of every downstream node.** Zero runtime risk (log text). (W-QLCB §3 names this a hard prerequisite.)
- **N2 after N1, before N5/N6/N7 (T2):** the dead vocabularies (source-allow-list, dead C2/C3 import, shadow penalty fields) add noise to the signal and gate-mass to the surface. Deleting them BEFORE the causal fix means the q_lcb change lands on a clean seam with no dead competitor paths to confound the shadow grade. Every deleted path is *already dead live* (confirmed: `edli_emos_ci_live_enabled` absent; `authority_on=False` at `:2811`) so N2 is byte-identical-live by construction. N2 unblocks **a clean q_lcb seam.** (P1 §3 T2; D6 guard: N2 deletes only provably-dead paths, KEEPS the `coverage_unlicensed_tail` *intent* until N7 reproduces it in shadow.)
- **N3 → N4 (E1 → E2), in parallel from day 0:** E1 is the query; E2 is the verdict authority that wraps it with the five INV-CAL contracts. N4 unblocks **the gate on every live promotion.** It has no dependency on the q_lcb work and MUST be built early because it is what makes N5/N6/N7/N11 promotable. (W-EDGE-LOCATE §6.1: "E1, E2 have NO code dependency on W-QLCB or W-SUBMIT.")
- **N5 (T3a) after N2, shadow:** the `center_sigma_c=3.0` producer bug (verified: 3.0 on all 3498 posteriors) generates a garbage raw q_lcb that the UP arm would otherwise have to lift from ≈0 every cycle. Fixing the producer first makes N7's raw input honest at birth. N5 stays `candidate=true` (shadow) — no live change. N5 unblocks **an honest raw bound for N7 to calibrate.** (W-QLCB §1.3, §2.2; D3 lean: settlement-fitted σ_center, with the "why is it pinned at 3.0" diagnostic as a mandatory prerequisite.)
- **N6 (T4) after N2 — the FIRST live-behavior node:** the σ-shape fit moves provably-wrong tail mass (`or_higher` 5× over-confident) back onto the ring bin in the POINT q, raising the ring point from which N7's clamp ceiling (`min(target, q_point)`) is computed. N6 must clear its OWN `_meta.promotion` forward-fill validation (mode-bin ratio ∈[0.85,1.15], tail ratio 0.30→1.0 on unseen settlements) before it goes live. N6 unblocks **a higher clamp ceiling for N7's UP arm.** N7 must be validated against the N6-corrected point-q where available (W-QLCB §3: "T4 and W-QLCB can be fitted in parallel but W-QLCB's UP arm should be validated AGAINST the T4-corrected point q").
- **N7 (T3b) after N5+N6, shadow → (E2 gate) → live — the ONE causal fix:** the bidirectional isotonic q_lcb authority. Its UP arm is the entire novelty (the current `apply_settlement_coverage` is shrink-only — verified at `settlement_backward_coverage.py:222-224`). It runs shadow-first computing would-be ring admissions, graded by N4/E2. N7 unblocks **the honest ring-bin admission** — the actual suppressed alpha, if it exists. (W-QLCB §1.2; the §4.1 sub-population backtest, D2, decides whether the UP arm ships at all.)
- **N8 (W-S1) independent:** the submit-lane stamp makes "lane was dark (degrade)" distinguishable from "mode-flipped (re-decided)" from "honest no-edge" on the receipt alone. Ship anytime; it unblocks **legible submit-stage telemetry** so N9's effect is measurable.
- **N9 (W-S2) gated by N5+N6+N7-in-shadow:** mode-flip terminal-abort → re-decision under `capital_efficiency`. **Must NOT ship standalone** — with today's admitted population (settlement-dead cheap-tail, base-rate buy_no; C5 confirmed) it would merely speed dead candidates to the venue (FAILURE under laws 2/4). Its value is latent: it ensures that once N7 produces a *real ring candidate*, that candidate does not die to a transient maker/taker book tick. N9 unblocks **a real candidate surviving submit.** (W-SUBMIT §6 sequencing law.)
- **N10 (W-S3) independent, LOW:** tick-size de-dup. 5 receipts all-time, 0 recent. Fold into task #64. Not on the critical path.
- **N11 (E3) last, after E2 trusted + N7 live:** per-cell `k_eff` widens the `direction_law` ring threshold ONLY where E2 LICENSES it. The geometry cut fires *before* the q_lcb gate, so a too-tight ring is an upstream suppressor N7 cannot reach — but widening is only safe once E2 can be trusted. Defaults byte-identical to k=1.0. N11 unblocks **mid-band ring candidates (dist 2–3) where settlement licenses them.** (W-EDGE-LOCATE §5.3: ships LAST.)

---

## 4. THE GATES BETWEEN STEPS (shadow / canary / ARM)

The operator contract forbids "submission unblocked" as a terminus and forbids permanent shadow. The gates below are **time-boxed promotion gates**, each flipping a node from shadow→live on settlement evidence or abandoning it. There is no new permanent flag; every shadow flag is a temporary promotion harness that goes to live-direct or is deleted.

### 4.1 The gate ladder (each promotion crosses exactly these)

| # | Gate | Applies to promotion of | Pass criterion | Fail action |
|---|---|---|---|---|
| **G0 — Unit/RED-on-revert** | code-correctness | every node N1–N11 | the node's RED-on-revert tests pass; full submit/admission suite green | do not merge |
| **G1 — Sub-population backtest (W-QLCB §4.1 / D2)** | N7 (decides if UP arm ships AT ALL) | N7 shadow build | restricted to `FUSED_NORMAL_FULL ∧ q_point>0.05`: R/E ≈3–4× under-coverage **persists** → UP arm is real, ship N7 | R/E collapses to ~1.0 → **no broad ring alpha**; reduce to N5 producer-fix only, NO UP arm; emit dated "market efficient on ring" (law-1 DONE) |
| **G2 — σ-fit forward-fill (`_meta.promotion`)** | N6 (T4) → live; N5 (σ_center) → live | candidate artifact | mode-bin ratio ∈[0.85,1.15] AND tail ratio 0.30→1.0 on settlements the fit did NOT see | keep `candidate=true`; live keeps honest-but-wide raw bound |
| **G3 — Shadow byte-identity** | N7, N5 | shadow flag OFF must == today | with the shadow flag OFF, output is byte-identical to current live (proves the OFF path is a no-op) | the flag is leaking behavior; fix before any promotion |
| **G4 — E2 band-verdict (the LICENSE gate)** | N7 shadow→live; N5 candidate→live; N11 | a (city,metric,season/ring) band reaches `n≥30` distinct settled events AND `realized−price lo95>0` AND `model_brier<market_brier` (INV-CAL-4) | LICENSE the band; promote | INSUFFICIENT_DATA (n<30) → keep accruing; NO_EDGE → stand down on the lane (do NOT loosen `capital_efficiency`) |
| **G5 — Canary live promotion** | N7 live, N9 active | the LICENSED ring band's FIRST live fills clear **>51% after-cost (1¢ fee) at n≥30 forward fills**, model-Brier<market-Brier, event-level walk-forward | **DONE** for the lane | revert to shadow; the ring edge does not survive friction (law-1 honest verdict) |
| **ARM — operator-gated live-table/flag flips** | N6 live point-q; N7 shadow-flag→live; any live-table rename | operator review of the dated evidence artifact (E2 JSON + backtest) | operator authorizes the live flip | stays candidate/shadow |

### 4.2 The ARM clause specifics (what the operator actually authorizes, and what self-arms)

Per operator law (memory: "NO blanket flags-flip-only-on-operator-word law" — the only real flag constraint is the **ARM condition: verified-correct before live"):

- **Self-arming (no operator word needed), because they are byte-identical-live or read-only:** N1, N2, N3, N4, N8 (all no-live-change), and the shadow builds of N5/N7 (candidate/flag-OFF). These pass G0+G3 and proceed.
- **Operator-ARM-gated (a live-behavior flip on settlement evidence):** N6 (point-q goes live — moves real belief), N7 shadow-flag→live (the UP arm starts admitting real money), N9 (mode re-decision starts submitting flipped candidates), the `sigma_center_fit.json` candidate→live, and any live-table rename (W-QLCB §2.4, memory: live-table renames operator-gated). The ARM artifact the operator reviews is the **E2 dated JSON + the G2/G4 evidence** — verified-correct-before-live, not a blanket gate.
- **The B1/M5 latch is NOT an ARM node** — it is the self-healing absorber (verified OPEN, 0 unresolved findings this session); it self-clears (keep-list, #31). N8's telemetry merely *names* it as a degrade cause when closed.

---

## 5. THE SINGLE SHORTEST PATH TO THE FIRST SETTLEMENT-PROVEN CORRECT-BIN ALPHA FILL

This is the operator's actual question. The shortest path is the **minimum chain of nodes+gates** that ends at a fill that (a) is on a near-center ring bin where the model honestly disagrees with the market, (b) settles a winner, and (c) is graded >51% after-cost at the event level — **not** the first order, **not** submission-unblocked.

### 5.1 The shortest chain (with the rate-limiter named)

```
STEP 1  N1 + N2 + N3 + N8   [parallel, day 0]      G0           → clean signal + edge map + telemetry
STEP 2  N4 (E2 harness)      [day 0, parallel]      G0           → the LICENSE gate exists; first run = "all ring cells INSUFFICIENT_DATA (n=5<30)"
STEP 3  N5 (σ_center shadow) + N6 (σ-shape)         G0,G2,G3,ARM → producer honest; point-q tail mass → ring (N6 live after G2+ARM)
STEP 4  N7 (bidirectional q_lcb, SHADOW)            G0,G1,G3     → UP arm computes would-be ring admissions (G1 decides if it ships)
        ┌─────────────────────────────────────────────────────────────────────────────────────┐
        │  RATE-LIMITER: shadow ring cohort accrues settled events until a band reaches n≥30    │
        │  (walk-forward, event-level dedup). This is governed by settlement cadence, NOT code. │
        │  Acceleration lever: backfill historical settled dates into the isotonic + E2 (7009   │
        │  VERIFIED settlements 2024-01-01→2026-06-13 already exist — fit on the full history,   │
        │  do not wait for new settlements to re-accrue what is already settled).                │
        └─────────────────────────────────────────────────────────────────────────────────────┘
STEP 5  N4/E2 LICENSES a ring band                  G4           → n≥30 ∧ realized−price lo95>0 ∧ model_brier<market_brier
STEP 6  N7 promote SHADOW→LIVE  +  N9 (W-S2 active)  ARM,G0       → ring admission goes live; mode-flip protects the candidate
STEP 7  live ring candidate ADMITS (capital_efficiency, honest) → SUBMITS (N9 survives the tick) → FILLS
STEP 8  the fill SETTLES → E2 grades it event-level, walk-forward, vs-market
STEP 9  the LICENSED ring band's live fills clear   G5           → >51% after-cost at n≥30 forward fills → **DONE**
```

### 5.2 The shortest path is shorter than the full DAG — what it deliberately OMITS

To reach the FIRST proven alpha fill as fast as honestly possible, the shortest path omits nodes that are not on the chain to that fill:
- **N10 (W-S3 tick de-dup)** — 5 receipts all-time, 0 recent; not on the path. Fold into task #64 whenever.
- **N11 (E3 candidate-focus)** — only needed if the genuine ring edge lives at dist 2–3 that `k=1.0` clips. **Defer until G4 LICENSES a cell AND the dist-0/1 ring is already proven** — adding it earlier couples two unvalidated fixes (W-EDGE-LOCATE §5.3). It is a *widener*, not a precondition for the first fill at dist 0/1.
- **The N5 producer-fix→LIVE promotion** is NOT required for the first proven fill *if* N7's UP arm carries the lift in shadow (Alternative 1 fallback). The producer fix's live promotion is a robustness improvement; the shortest path can reach a proven fill with N5 shadow-only feeding N7. (But N5 shadow IS on the path — it makes N7's raw input honest.)

### 5.3 The acceleration lever (the only way to compress the rate-limiter)

The rate-limiter (STEP 4 accrual) is settlement cadence, which law 5 forbids fabricating. But the isotonic map and E2 do NOT need to wait for *new* settlements — **7009 VERIFIED settlements already exist back to 2024-01-01** (verified this session). The fit and the walk-forward verdict can run on the **full historical settled population immediately**, so a ring band can reach n≥30 distinct settled events on history the moment N4/N7 are built — *if* enough distinct ring city·date·bin events exist in that history. **The W-EDGE-LOCATE evidence says the distinct ring cohort is currently n≈5** (W-EDGE-LOCATE §1.2: 48 raw rows → 5 distinct events, 3 distinct winning markets). **This is the binding empirical reality of the shortest path:** the code can be done in days, but the LICENSE gate (G4) cannot fire until the distinct ring cohort grows from ~5 to ≥30 — which depends on the market continuing to offer ring mispricings and on N7-shadow accruing them. If it never grows, G4 correctly emits "INSUFFICIENT_DATA / no licensable edge" — the honest law-1 verdict, not a failure.

### 5.4 The honest expected outcome of the shortest path (stated up front, not hidden)

Following P1 §7 and W-EDGE-LOCATE §9: **there is no large suppressed alpha pool.** The shortest path's most probable terminus is **NOT a triumphant fill** — it is one of two honest, settlement-proven verdicts:
1. **G1 collapses to R/E≈1.0** on the `q_point>0.05` sub-population → the UP arm never ships; the path terminates at N5 (producer honesty) + a dated "market is efficient on the ring" verdict. **DONE = the law-1 verdict, proven.**
2. **G1 persists (R/E 3–4×)** → N7 ships shadow; but G4 sits at INSUFFICIENT_DATA because the distinct ring cohort is ~5 → the path waits on accrual, emitting "no licensable edge yet, continue shadow."
3. **The thin-but-real case** → a single ring band reaches n≥30, LICENSES, N7 promotes, a ring fill settles, and G5 clears >51% after the 1¢ fee at n≥30 — **the genuine first proven alpha fill.** This is possible but, per all five settlement joins, **thin** (~1.5–3pp of market under-pricing that may not survive the 1¢ fee).

The path is designed so that **each of these three is a legitimate, dated, numeric output** — the operator gets a settlement-proven answer either way, and a fill is never forced to manufacture a "success."

---

## 6. THE ORDERED CHANGE SEQUENCE (the implementation queue P3 hands to execution)

This is the linearized build order — the partial DAG flattened into a queue, with each item's gate and its parallelism noted. Items in the same **wave** are parallelizable.

### Wave 0 — Signal-cleaning + instrument (parallel; all self-arming, zero live-behavior risk)
1. **N1** (T1 cycle-summary attribution) — G0. *Hard prerequisite for observing all later effects.*
2. **N2** (T2 dead-gate deletions) — G0. *D6 guard: delete only provably-dead paths; keep `coverage_unlicensed_tail` intent.*
3. **N3** (E1 edge-location query) — G0. *Gives the operator the dated edge map showing n≈5 ring / INSUFFICIENT today.*
4. **N8** (W-S1 submit-lane observability) — G0. *Persist-boundary assert; telemetry only.*

### Wave 1 — The grading gate (the highest-leverage single node)
5. **N4** (E2 grading harness + verdict + JSON) — G0. *Depends on N3. This is the convergence node: every later promotion consults it. First run output = "all ring cells INSUFFICIENT_DATA."*

### Wave 2 — The q_lcb input fix (shadow; the one causal lever)
6. **N5** (T3a σ_center producer fix, SHADOW) — G0, G3. *Depends on N2. `sigma_center_fit.json` candidate; rematerialize into shadow column. Prerequisite diagnostic: trace why anchor_sigma_c is pinned at 3.0 (D3).*
7. **N6** (T4 σ-shape point-q) — G0, G2, **ARM**. *Depends on N2. FIRST live-behavior node; promotes only after forward-fill validation + operator ARM.*
8. **N7** (T3b bidirectional q_lcb, SHADOW) — G0, **G1**, G3. *Depends on N5+N6. G1 (the §4.1 sub-population backtest) decides whether the UP arm ships at all — run G1 BEFORE committing N7's primacy.*

### Wave 3 — Promotion (gated by settlement accrual)
9. **[G4 LICENSE]** — E2 licenses a ring band when n≥30 ∧ lo95>0 ∧ model beats market. *Rate-limited by settlement cadence; fit on full 7009-row history to compress.*
10. **N7 shadow→LIVE** — **ARM**, G3. *Only after G4 LICENSES the band.*
11. **N9** (W-S2 mode re-decision) — G0, **ARM**. *Sequenced here, NOT earlier: ships only after N5+N6+N7-shadow produce a real ring candidate (W-SUBMIT §6). Shipped earlier it speeds dead candidates to venue (FAILURE).*

### Wave 4 — Wideners + cleanup (after the dist-0/1 ring is proven)
12. **N11** (E3 candidate-focus) — G0, **ARM**, gated on G4-LICENSED + N7-live. *Defaults k=1.0; widens only on a LICENSED cell.*
13. **N10** (W-S3 tick de-dup) — G0. *LOW; fold into task #64 anytime.*

### Wave 5 — DONE
14. **[G5]** — the LICENSED ring band's live fills clear >51% after-cost at n≥30 forward fills, model-Brier<market-Brier. **This repeating is DONE. A single fill firing is not.**

---

## 7. SEQUENCING RISKS & THE INVARIANTS THAT GUARD THEM

| Risk (a wrong sequence would cause) | The edge that prevents it | Verification |
|---|---|---|
| Ship N9 (submit re-decision) first → speed dead base-rate/cheap-tail to venue | N9 gated by N5+N6+N7-shadow producing a real ring candidate (Wave 2 before Wave 3) | W-SUBMIT §6 sequencing law; G4 must LICENSE before N9 arms |
| Promote N7 to live before E2 exists → unproven admission goes live | N4 (E2) is Wave 1, before any live promotion (Wave 3) | G4 is structurally required for N7→live |
| Land N7 before N5 → UP arm lifts from ≈0 garbage every cycle, fragile | N5 (producer fix) is sequenced before N7 in Wave 2 | W-QLCB §1.4 Alternative 3 (BOTH); N7's raw input is honest first |
| Delete `coverage_unlicensed_tail` effect before N7 reproduces it → far-tail re-admits | N2 deletes only provably-dead paths; KEEPS the antibody intent until G4 | D6 guard; `test_qlcb_far_tail_stays_zero` (Milan-24C) on the new path |
| N7 UP arm lifts a below-market model (population C) over price | the `min(target, q_point)` clamp; G1 backtest excludes pop A | W-QLCB `test_qlcb_never_exceeds_point` RED-on-revert |
| Skip G1 → ship a UP arm that admits noise on a market that is efficient | G1 (the §4.1 backtest) is a hard gate before N7 primacy | "the gate before the gate" (W-QLCB §4.1) |
| Treat a single fill as DONE | G5 requires n≥30 forward fills, repeating, vs-market | operator contract; P1 §5 step 6 |
| N6 moves the point-q before forward-fill validation | G2 (`_meta.promotion`) + ARM before N6→live | σ-fit holdout warning (`sigma_scale_fit.json _meta.promotion`) |

**The single invariant that subsumes all of these:** *no node changes live behavior until it has crossed its specific gate, and the q_lcb causal chain (N5→N6→N7) is built upstream-first so each node's input is honest before the next calibrates it.* Reversing any edge violates either a settlement-proof requirement (laws 1/5) or an upstream-honesty requirement (the raw bound must be honest before the UP arm calibrates it).

---

## 8. THE CRITICAL-PATH SUMMARY (one screen)

**The DAG has three parallel roots** — signal-cleaning (N1→N2), the grading instrument (N3→N4/E2), and observability (N8) — that **converge on the E2 LICENSE gate (N4)**, the single node every live promotion must pass.

**The critical path** (longest chain) is: `N1 → N2 → N5(σ_center shadow) → N6(σ-shape point-q live) → N7(bidirectional q_lcb shadow) → [settlement accrual] → E2 LICENSE → N7 live + N9 → ring fill → settles → G5 >51% → DONE.`

**The rate-limiter is NOT code** (N1–N7 land in days) — it is the **shadow accrual of distinct settled ring events from ~5 toward ≥30**, governed by settlement cadence (law 5, unfabricatable), compressible only by fitting the isotonic + E2 on the full 7009-row settled history that already exists.

**The shortest path to the first proven alpha fill omits N10, N11, and N5→live**, and its most probable honest terminus is one of three dated settlement verdicts: (1) G1 collapses → "market efficient on ring," DONE; (2) G4 stays INSUFFICIENT → "no licensable edge yet"; (3) the thin-but-real case → a ring fill that clears G5 — the genuine first proven correct-bin alpha fill.

**Operator-facing bottom line:** the submit arm is already ON (no flag to flip); the binding constraint is upstream at admission (q_lcb crushed by the 3.0°C center jitter on all 3498 posteriors); the fix is built upstream-first and shadow-graded against 7009 existing settlements; and **the path is engineered so that "the market is efficient, there is no tradeable ring alpha" is a first-class, dated, numeric DONE — not a failure to engineer around.** A fill is the success criterion only if it settles, repeats, and beats the market at n≥30 after the 1¢ fee.

*End of P2 sequence & critical path. Read-only planning; no production code or daemon changed. Every load-bearing fact cited to file:line, artifact, or query+counts run this session.*
