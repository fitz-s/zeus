# P2 — W-KEEP-SIMPLIFY: The DO-NOT-TOUCH KEEP-list, the K≪N Deletions, and the Parked Worktree Fixes

**Date:** 2026-06-14
**Mode:** PLAN-MAKING (no production edits, no deploy, no live touch). DBs opened `?mode=ro`. Every KEEP / DELETE claim re-verified at source this session and cited file:line.
**Role:** P2 workstream owner for the operator's law-3 spine — *collapse N→K, never add; default SIMPLIFY*. This document is the **safety rail** for every other workstream: it names what must NOT be touched (the load-bearing invariants whose silent breakage destroys the alpha foundation) and what SHOULD be deleted (the dead gate-mass whose removal is pure subtraction), and it parks the two suspended worktree fixes (B1, B2) with reasons.
**Authority spine:** `P1_strategy_of_record.md` §2 (the KEEP-list), §3 Thrust 2 (the deletions), `P1_redteam_1.md` (which **reshapes** both lists — see §0), `diagnosis_confirmation.md`, `P2_W-QLCB.md`, `P2_W-SUBMIT.md`, `P2_W-EDGE-LOCATE.md`, `P2_sequence_and_critical_path.md`, operator contract laws 1–8, the no-shadow / no-gate-accretion / no-caps operator memories.

---

## 0. THE RED-TEAM RESHAPES THIS WORKSTREAM — read this before the lists

This document is NOT a transcription of P1 §2. The hostile red-team (`P1_redteam_1.md`) changed which things are load-bearing, and a KEEP-list that protected the *pre*-red-team picture would be actively dangerous — it would canonize, as "do-not-touch," items that the red-team showed are either (a) the wrong primary lever or (b) a forgotten interlock the headline fix routes under. The three reshapings that propagate into both lists below:

1. **`arm_gate_coverage_blocks` is promoted from invisible to load-bearing KEEP.** The red-team's KILL-3 (`P1_redteam_1.md:96-103`) found — and I re-confirmed at `settlement_backward_coverage.py:228-259` this session — that the coverage `CoverageVerdict` already gates ARMING unconditionally (flag-independent: blocks on UNLICENSED, on `coverage_ratio is None`, on `|ratio-1| ≥ _ARM_RATIO_TOL`). P1 §2 never listed it. It is now **K-ARM** below, a DO-NOT-TOUCH whose over-claim-block semantics any q_lcb change must preserve. This is the single most important addition this document makes to the KEEP-list.

2. **`_isotonic_realized_rate`'s shrink-only wrapper is a KEEP, not a "collapse-into-bidirectional" target.** P1 §3 Thrust 3 wanted to mutate `apply_settlement_coverage` into a bidirectional UP-arm. The red-team's KILL-1 (`P1_redteam_1.md:32-64`) — re-confirmed at the source docstring this session (`settlement_backward_coverage.py:108-110`: *"With observations clustered at a single claimed band (the common live case) this reduces to the pooled realized win-rate in that band"*) — proved the UP arm reads a **bin base rate** (law-4 illusion). So the **shrink-only, one-directional** wrapper (`apply_settlement_coverage:204-225`, *"Never widen: the shrink only ever LOWERS"*) is now itself a **KEEP** (K-COVERAGE-DOWN): its one-sidedness is the antibody, not the disease. The deletion target moves OUT of this module.

3. **The primary causal lever moved from N7 (UP arm) to N5 (σ_center producer fix).** This does not add a KEEP item, but it changes the *framing* of the KEEP-list: the point-q chain (the crown jewel) is protected, AND the red-team's N6-demotion (`P1_redteam_1.md:84-93`) means the σ-shape point-q promotion must NOT be allowed to silently widen the base-rate UP-arm's ceiling. The KEEP-list's "do not touch the point q" bullet now carries an explicit corollary: *N6 may not ship as N7's ceiling-raiser* (§1.2, K-POINT note).

The net effect: this document's KEEP-list is **larger and more defensive** than P1 §2 (it adds the ARM interlock and the shrink-only one-sidedness), and its DELETE-list is **strictly the provably-dead subset** P1 §3 Thrust 2 named, minus anything the red-team re-classified as load-bearing.

---

## 1. THE KEEP-LIST — DO-NOT-TOUCH (load-bearing invariants)

Each item carries: what it is, the file:line proving it is real and current, WHY it is load-bearing (what breaks if touched), and the **provenance verdict** (per the global "legacy is untrusted until audited" rule). All items below are verdict **CURRENT_REUSABLE** — audited at source this session against current law — *unless noted*. Touching any of these is **out of scope for every workstream**; a change that needs to is a re-plan, not an edit.

### K-SPINE — the `capital_efficiency` honest arbiter (the crown of the KEEP-list)

- **What:** `live_capital_efficiency_rejection_reason` — `conservative_ev_per_dollar = (q_lcb − price) / price; reject iff ≤ 0`.
- **Source (verified this session):** `src/strategy/live_inference/live_admission.py:87-126`. The function body is verbatim a single honest inequality: `if conservative_ev_per_dollar <= 0.0: return "ADMISSION_CAPITAL_EFFICIENCY_LCB_EV:..."`. Its own docstring: *"Low maximum payout ROI and low robust EV/$ are ranking/sizing inputs, NOT fixed live blockers"* — i.e. it deliberately carries **no** cap, throttle, ROI-floor, or fill term. There is exactly one inequality and it compares the win-direction q_lcb to the fee-adjusted price.
- **Why load-bearing:** this is THE gate the entire diagnosis identified as honest (`diagnosis_confirmation.md:12,128`) — ~88% of rejections fire here, correctly, on a collapsed q_lcb. Every fix in the program targets the q_lcb that flows IN; **the comparison itself is inviolable.** Loosening it (an ROI floor relaxation, an EV-margin, a "just admit if close") is the single most tempting and most forbidden move — it converts an honest no-edge into a manufactured fill (laws 1, 4, the operator contract's "DONE ≠ a fill").
- **Provenance verdict:** CURRENT_REUSABLE. The `# FIX B (incident 0b5c305e..., Milan-24C)` comment block immediately below it (`live_admission.py:128+`) confirms it was audited as recently as 2026-06-10 under the current settlement-coverage regime.
- **The bright line:** no workstream edits `live_admission.py:87-126`. W-QLCB changes the q_lcb argument; W-SUBMIT re-evaluates the *same* inequality on the fresh book; neither rewrites it.

### K-ARM — the coverage→ARM interlock (the red-team's forgotten KEEP; the operator's verified-correct-before-live backstop)

- **What:** `arm_gate_coverage_blocks(verdict)` — blocks arming when the settled record refuses the claimed LCB.
- **Source (verified this session):** `src/calibration/settlement_backward_coverage.py:228-259`. Reads `CoverageVerdict` **unconditionally** (the docstring: *"read UNCONDITIONALLY (flag-independent)"*). Blocks on: `status == "UNLICENSED"`; `coverage_ratio is None`; `abs(ratio − 1.0) >= _ARM_RATIO_TOL`. Returns `(blocked, reason)`.
- **Why load-bearing:** this is the operator's own "you cannot ARM live on an LCB the settled record refuses" interlock — the law-memory ARM condition (*"the only real flag constraint = ARM condition: verified-correct before live"*) is implemented HERE. The red-team showed (`P1_redteam_1.md:96-103`) that W-QLCB's UP arm, by mutating how the `CoverageVerdict` is computed, could silently relax this gate — converting an over-claim **block** into a base-rate-corroborated under-claim **permit**, routing the base-rate illusion under the operator's safety interlock.
- **Provenance verdict:** CURRENT_REUSABLE, but **upgraded to a HARD CONSTRAINT on W-QLCB**: any q_lcb-producer change MUST hold `arm_gate_coverage_blocks`'s over-claim-block semantics **frozen** — the verdict it reads may gain telemetry but may never become more permissive about arming. The W-QLCB risk table (`P2_W-QLCB.md:246-252`) omitted this; it is a binding addendum (§4 below).
- **The bright line:** the `_ARM_RATIO_TOL` block, the `status == UNLICENSED` block, and the `coverage_ratio is None` block are DO-NOT-RELAX. A bidirectional verdict (if N7 is ever redesigned forecast-conditional) must keep these three blocks firing identically on the over-claim side.

### K-COVERAGE-DOWN — the one-directional shrink-only coverage wrapper (its one-sidedness IS the antibody)

- **What:** `apply_settlement_coverage` — applies the coverage verdict to live q_lcb, but **only ever LOWERS** it.
- **Source (verified this session):** `src/calibration/settlement_backward_coverage.py:204-225`. The down-arm: `if verdict.status == "UNLICENSED": return float(min(float(q_lcb), float(verdict.q_lcb_out)))` with the inline comment *"Never widen: the shrink only ever LOWERS the LCB (one-sided honesty)."* When the shadow flag is OFF it returns q_lcb byte-identical (the SHADOW SAFETY docstring).
- **Why load-bearing — and why this is a RED-TEAM-DRIVEN REVERSAL of P1:** P1 §3 wanted to *replace* this with a bidirectional bound. The red-team's KILL-1 proved the UP direction reads a base rate (because `_isotonic_realized_rate` on the live single-band stream short-circuits to the pooled mean — `settlement_backward_coverage.py:108-117`, verified this session, and `grade_receipt` is forecast-unconditional bin-geometry). The down-arm is **valid** (base-rate evidence soundly shrinks a wild over-claim); the up-arm is **invalid** (base-rate evidence cannot soundly inflate a crushed bound into an admission). Therefore the shrink-only one-sidedness is not a limitation to collapse — it is the **correctness boundary** that keeps the base-rate illusion out. KEEP it shrink-only.
- **Provenance verdict:** CURRENT_REUSABLE. `_isotonic_realized_rate` itself (`:97`) is CURRENT_REUSABLE **for the shrink direction only** — re-using it for an UP arm is the QUARANTINED move (see §2.4, the explicit NON-deletion / NON-mutation note).

### K-POINT — the point posterior `q` chain end-to-end (the crown jewel)

- **What:** member resample → MAP-Platt (#129) → posterior MODEL_ONLY → `bin_probability_settlement`. The point belief; Σq = winners (S1).
- **Source / status:** the chain is the live primary on the bundle path (`event_reactor_adapter.py` `_replacement_authority_probability_and_fdr_proof`, confirmed live in `P2_W-QLCB.md:17`); `anchor_sigma_c=3.0` universal on all posteriors (`forecast_posteriors`, 3464–3504 rows depending on snapshot — the count drifts as posteriors accrue; the *value* is invariantly 3.0).
- **Why load-bearing:** the point q is the law-8 metadata foundation — bin selection is correct because the point is honest. Moving it is moving the belief itself. P1 §2 marks it "do not touch."
- **Provenance verdict:** CURRENT_REUSABLE.
- **RED-TEAM COROLLARY (the new constraint P1 lacked):** the σ-shape point-q promotion (N6 / `sigma_scale_fit.json`) DOES move the point q, and the red-team demoted it (`P1_redteam_1.md:84-93`) precisely because it would raise the `min(target, q_point)` ceiling that the base-rate UP arm exploits. **The corollary:** N6 is OFF the critical path and may NOT ship as "N7's ceiling-raiser." If the σ-shape fit is ever promoted, it must clear its own `_meta.promotion` forward-fill validation **as an independent point-q improvement**, with the UP arm already either redesigned forecast-conditional or dropped — never as the second half of an illusion-amplifying pair. This is a KEEP-the-point-q-sacrosanct refinement, not a separate item.

### K-TAIL-ZERO — the honest q_lcb≈0 on far-OTM / open-tail zero-support bins (population A)

- **What:** the structural-zero lower bound on bins with genuinely ~0 point mass (Munich 26C+, the 0/72 cohort).
- **Source / status:** `b2_capital_efficiency_audit.md` population A; the `coverage_unlicensed_tail` fail-closed antibody at `live_admission.py:141` enforces it on the licensing side.
- **Why load-bearing:** law 4 — do NOT manufacture far-tail alpha. The 0/72 settled record proves these are honest no-edge. Any q_lcb fix that lifts these is the law-4 violation the whole program exists to avoid.
- **Provenance verdict:** CURRENT_REUSABLE. KEEP the *effect* (far-tail stays rejected) even through the Thrust-2 deletion of the dead *source-allow-list vocabulary* — the D6 guard (§2.3).

### K-DIRECTION — the `direction_law` geometry (encodes WHERE settlement-backed edge lives)

- **What:** buy_yes scoped to `distance(bin, μ*) ≤ T = max(1 step, k·σ)`, `k = DIRECTION_LAW_SIGMA_K = 1.0`.
- **Source (verified this session):** `src/strategy/live_inference/direction_law.py:57` (`DIRECTION_LAW_SIGMA_K = 1.0`), `:67` (`_SETTLEMENT_STEP_BY_UNIT = {"C":1.0,"F":2.0}`), `:88` (`bin_forecast_distance`), `:136` (`direction_law_rejection_reason`).
- **Why load-bearing:** it is not a throttle — it ENCODES the near-center ring where settlement-backed edge lives and makes the Milan far-tail-YES loss unconstructable (law 6, direction law inviolable).
- **Provenance verdict:** CURRENT_REUSABLE. **Nuance:** the *constant* `k=1.0` is the ONE thing W-EDGE-LOCATE's E3 may eventually replace with a per-cell fitted `k_eff` (`P2_W-EDGE-LOCATE.md:158-184`), and E3 ships LAST, gated on E2-LICENSE, defaulting byte-identical to 1.0. So the **geometry** (the distance primitive, the step map, the direction semantics) is DO-NOT-TOUCH; the **single tunable** `k` is the deferred, gated, last-in-line exception. The red-team flagged E3 as a base-rate-widener risk (`P1_redteam_1.md:167`) — so even the `k` exception is hard-gated on the Brier antibody.

### K-LCB-CEILING — the `q_lcb ≤ q_point` invariant + native-NO complement

- **What:** penalties/corrections may only LOWER the bound, never raise it above the point; native-NO uses `1 − q_ucb_yes` (Hidden #3).
- **Source:** `_side_q_lcb_from_yes_samples` (`event_reactor_adapter.py:10148`), `probability_uncertainty.py:307,311,315` (clip under point). The `min(q_lcb, q_point)` clamp.
- **Why load-bearing:** the red-team showed (`P1_redteam_1.md:55-57`) this clamp is the **only** thing standing between a (hypothetical, redesigned) UP arm and admitting population C (below-market models). It is load-bearing far beyond P1's framing. KEEP the invariant absolutely; it is the structural guard the W-QLCB tests (`test_qlcb_never_exceeds_point`) bind.
- **Provenance verdict:** CURRENT_REUSABLE.

### K-ABSORBER — the B1/M5 submit-latch absorber + external-close reconcile (self-healing)

- **What:** the settled-class external-close absorber + M5 WS-gap reconcile that freezes/clears the submit latch on swept winners.
- **Source (verified this session):** `src/execution/exchange_reconcile.py:11-18` (the absorber contract comment), `:297-302` (`Run a fresh M5 sweep ... clear the latch only on proof`), `:1061-1062` (`_OPERATOR_EXTERNAL_CLOSE_RESOLUTION = "position_drift_operator_external_close_absorbed"`).
- **Why load-bearing:** it self-clears (`diagnosis_confirmation.md:97`: latch OPEN now, cleared 06-14T01:06 autonomously); the shared-wallet operator co-trading memory makes external closes EXPECTED, and this absorber is what keeps them from latching the daemon. It is on the keep-list precisely because it is NOT a blocker — it is the antibody that keeps a non-blocker from becoming one.
- **Provenance verdict:** CURRENT_REUSABLE (touched 2026-06-14 per the worktree B2 commit `0976a1892f`; see §3). DO-NOT-TOUCH from any other workstream.

### K-NO-ELIGIBILITY — the profitable-era NO eligibility gate + market-anchor cap

- **What:** #74 (closes the NO-on-winning-ring loss class) + the C3 market-anchor cap (closes the phantom-NO loss class).
- **Source:** `event_reactor_adapter.py:7472` (market-anchor cap); #74 lock at `direction_law`/reactor NO gate. The worktree `agent-a7d43465f42dcf1fe` commit `a70b091b10` ("reactor NO loss-class: lock the non-executable-YES buy_no gate") is the live form.
- **Why load-bearing:** these close two settlement-proven loss classes (selling NO on a winning ring bin; phantom NO). Removing them re-opens losses, not edge. KEEP.
- **Provenance verdict:** CURRENT_REUSABLE.

### K-INV37 — cross-DB discipline + the K1 DB split

- **What:** ATTACH+SAVEPOINT only (never independent connections); zeus-world / zeus-forecasts / zeus_trades separation.
- **Source:** `.claude/CLAUDE.md` (the Zeus INV-37 rule); enforced repo-wide.
- **Why load-bearing:** every cross-DB read in W-EDGE-LOCATE (regret ⨝ settlement) and every write must obey it or corrupt the canonical-truth split. KEEP; it is a constraint ON the new workstreams, not a thing they touch.
- **Provenance verdict:** CURRENT_REUSABLE.

### K-SEMANTICS — time-semantics (#16) + per-city settlement-rounding preimage (#24)

- **What:** the law-8 metadata that makes the near-center q honest — μ*, bin identity, rounding preimage exact.
- **Source:** #16 time-semantics contract layer (recent commit `69ad19e902`); #24 HK preimage contract.
- **Why load-bearing:** the ring edge is a few points; it exists ONLY if the preimage and bin identity are exact (law 8). The red-team's KILL-1 reinforces this: W-EDGE-LOCATE must re-grade through `grade_receipt`'s preimage spine (`P2_W-EDGE-LOCATE.md:110-114`) BEFORE any edge claim — so the preimage contract is the precondition for the grading instrument itself.
- **Provenance verdict:** CURRENT_REUSABLE.

### K-SETTLEMENT-TRUTH — settlement is the only truth (no in-sample promotion)

- **What:** law 5; the replacement-form §4 walk-forward discipline (in-sample EV inflated, holdout collapses to +1.2¢…−2.7¢).
- **Source:** `settlement_outcomes` (authority=VERIFIED, 7009 rows); `grade_receipt` as the sole grade.
- **Why load-bearing:** every promotion gate in the program (G1–G5) is settlement-graded. This is the meta-KEEP that makes "DONE = the law-1 dated verdict" a legitimate outcome.
- **Provenance verdict:** CURRENT_REUSABLE.

### K-CI-HONESTY — σ never tightened below MC; `k_cov` never shrinks σ; EMOS licensing HIGH-metric only

- **What:** the CI-floor invariants (synthesis keep #6).
- **Source:** the σ floor at ≥ MC; `sigma_scale_fit.json` may RAISE ring mass via the mixture but the core floor stays.
- **Why load-bearing:** the σ_center producer fix (N5, now PRIMARY per red-team) shrinks the *center-uncertainty* jitter (3.0°C → fitted SEM) — it must NOT be confused with shrinking the *predictive* σ (the weather spread). The CI-honesty KEEP is the guard that keeps N5 from over-tightening the wrong σ. **This is now more load-bearing than in P1**, because N5 is the primary lever.
- **Provenance verdict:** CURRENT_REUSABLE.

---

## 2. THE K≪N DELETIONS — pure subtraction (every deleted path is provably dead live)

The operator law: collapse N→K, never add. These are the deletions P1 §3 Thrust 2 named, **re-verified dead at source this session**, and trimmed by the red-team to exclude anything re-classified as load-bearing. Each carries: what dies, the proof it is dead live, and the test that the deletion is byte-identical-live.

### 2.1 Collapse the two licensing vocabularies to one (the dead EMOS live lane + the source-allow-list)

- **What dies:** (a) the EMOS live-licensing override that stamps `q_lcb_calibration_source=EMOS_ANALYTIC`; (b) the static source allow-list `{EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}` used by `live_admission.py` G2/G4 as a gate input.
- **Proof it is dead live (verified this session):**
  - `edli_emos_ci_live_enabled` defaults **False** (`src/main.py:1016` `if not bool(edli_cfg.get("edli_emos_ci_live_enabled", False))`; `event_reactor_adapter.py:11995` same guard) — absent from config, so the override early-returns.
  - `state/emos_ci_license.json` **does not exist** (verified `ls` → "No such file or directory"); never committed (`git log --all` empty, `diagnosis_confirmation.md:106`); `main.py:1001` explicitly says *"Operator must populate"* — never-built by design.
  - Receipt-level: `EMOS_ANALYTIC` stamped 0× live; `COVERAGE_UNLICENSED_TAIL` in 0 of 62,874 receipts (`diagnosis_confirmation.md:39`).
- **The collapse:** make the live `settlement_backward_coverage` VERDICT the **sole** license authority; `q_lcb_calibration_source` becomes pure telemetry, never a gate input. Net licensing vocabularies: 2 → 1.
- **Byte-identical-live proof / test:** with `edli_emos_ci_live_enabled` absent, removing the override path changes no live receipt (it never fired). A grep-antibody test: `assert "EMOS_ANALYTIC" not in <any live gate-input path>`.
- **DO NOT (the refuted synthesis):** do NOT build `state/emos_ci_license.json` or flip `edli_emos_ci_live_enabled` (`P1_strategy_of_record.md:77`, `diagnosis_confirmation.md:109`). It licenses a settlement-dead class and is exactly the gate-accretion the operator forbids. **This is a DELETE-only item; the temptation to "fix EMOS by building the file" is the trap.**

### 2.2 Delete the dead C2/C3 selection-shrinkage live-path import

- **What dies:** the `_compute_selection_shrinkage(..., authority_on=False)` live-path import that produces only NULL stamps.
- **Proof it is dead live (verified this session):** `event_reactor_adapter.py:2806-2811` calls `_compute_selection_shrinkage(..., authority_on=False)`; the surrounding comment (`:2802-2806`) documents it was pinned `authority_on=False` on 2026-06-13 "in the q-shadow gate-mass collapse." With `authority_on=False`, `authority = "BH_FDR"` (`:2102`) and the EB shrinkage never decides — it writes NULL stamps to receipts, neither gating nor providing telemetry.
- **The collapse:** remove the dead live-path import; leave BH/FDR as the condemned-interim gate with a provenance note; **defer** the BH→C2/C3 promotion until the foundation is settlement-proven.
- **Byte-identical-live proof / test:** `authority_on=False` means the call is already a no-op on the decision; deletion changes no receipt. Test: a receipt-diff over a frozen cycle shows identical decisions with the import removed.

### 2.3 Delete the dead δ-penalty plumbing + shadow N_eff/JS fields on the live q_lcb seam

- **What dies:** the `UncertaintyPenalties` plumbing never populated on `_side_q_lcb_from_yes_samples`; the `q_lcb_neff_corrected` / James-Stein shadow fields that never reach a live decision.
- **Proof it is dead live:** the live canonical call passes **no penalties and no `n_eff_override`** (`event_reactor_adapter.py:10181`, per `P1_strategy_of_record.md:24`); the N_eff correction writes only the `compare=False` shadow field (`probability_uncertainty.py:215,283`); James-Stein pinned `authority_on=False` (`:2811`, the same pin as 2.2).
- **The collapse:** per the no-shadow law (a field that gates nothing is gate-mass), remove the shadow fields from the live seam — OR keep strictly as a settlement-grading diagnostic in the W-EDGE-LOCATE harness (E2), **never as a live input.**
- **The D6 GUARD (the one place subtraction must wait):** do **NOT** delete the `coverage_unlicensed_tail` antibody's *EFFECT* (`live_admission.py:141`, K-TAIL-ZERO) — only its dead *source-allow-list vocabulary*. The Milan-24C regression test moves to the new path (a far-tail bin with q_lcb≈0 → `capital_efficiency` rejects). **Delete the vocabulary, keep the effect, until the corrected q_lcb provably reproduces the far-tail rejection in shadow** (`P1_strategy_of_record.md:131`, sequence §7 D6 row).

### 2.4 EXPLICIT NON-DELETIONS / NON-MUTATIONS (the red-team's reversal of P1's Thrust 3)

These were *deletion or mutation targets* in P1 but are now KEEP per the red-team — recorded here so a future session does not "finish the collapse" P1 started:

- **DO NOT mutate `apply_settlement_coverage` into a bidirectional UP arm** (P1 §3 Thrust 3's headline). The red-team's KILL-1 proved the UP arm reads a bin base rate. The shrink-only wrapper STAYS shrink-only (K-COVERAGE-DOWN). If an UP arm is ever wanted it requires a **forecast-conditional** realized rate (a NEW, larger change joining coverage observations to the per-day posterior), proven on Brier before its own backtest is trusted — NOT a reuse of `_isotonic_realized_rate` (`P1_redteam_1.md:140`). Until then, **no UP arm exists to build.**
- **DO NOT collapse `_isotonic_realized_rate` into "the bidirectional authority."** It is CURRENT_REUSABLE for the shrink direction and QUARANTINED for the UP direction (the pooled-mean short-circuit makes it a base-rate reader on the live single-band stream — `settlement_backward_coverage.py:108-117`).
- **DO NOT delete the Wilson-over-AIFS-votes fallback as a live authority *yet*** (P1 D4). It returns 0.0 for zero-vote bins; once the σ_center producer fix (N5) is the authority it has no live job — but the deletion is gated on confirming no shadow-coverage consumer depends on a non-null bound (`P2_W-QLCB.md:239`). Park it (§3-style) as a **deferred** deletion, not an immediate one.

---

## 3. PARKED WORKTREE FIXES — B1 and B2, with reasons

Two worktree fixes are suspended. The verified worktree state this session (`git worktree list` + per-branch `git log`):

| Worktree | Branch HEAD | Holds |
|---|---|---|
| `agent-a54ad5b8210fa99ee` | `0b553c90fd` | "gate-mass collapse Wave C/D: remove redundant re-checks + banned throttles" (uncommitted: `event_reactor_adapter.py`, a direction-semantics test) |
| `agent-af862bd482b53a2f8` | `0976a1892f` | "reconcile: terminal-chain-closed phantom absorber (unfreeze latch on swept winners)" — the B2/M5 absorber work, on top of the same gate-mass-collapse base |
| `agent-a7d43465f42dcf1fe` | `a70b091b10` | "reactor NO loss-class: lock the non-executable-YES buy_no gate" — this is the K-NO-ELIGIBILITY live form, already merged-adjacent |
| `rename-bpf` | `b994bb7b02` | the version-suffix BPF rename (separate operator-gated track) |

### B1 — PARK as MOOT / self-healing (the submit-latch)

- **Reason to park:** B1 is the M5/submit-latch class. The diagnosis re-confirmed it is **OPEN now** (0 unresolved `exchange_reconcile_findings`, cleared autonomously 06-14T01:06 — `diagnosis_confirmation.md:90-97`). The latch self-heals; it is on the KEEP-list as K-ABSORBER (do not touch). The proof it is not the binding constraint: `proof_accepted=0` on every post-clear cycle, so the constraint is upstream at admission, not at the latch (`diagnosis_confirmation.md:97`).
- **Verdict:** **PARKED — moot/self-heals.** No B1 worktree fix is needed; the absorber already does its job. Do NOT re-open it as a fix target; re-opening risks touching K-ABSORBER. If any B1 worktree commit modified the absorber, it should be reconciled against the live `exchange_reconcile.py` (the B2 worktree `0976a1892f` is the relevant absorber commit — see B2).

### B2 — PARK as PENDING W-QLCB (the phantom-absorber / reconcile work, and the gate-mass-collapse base)

- **Reason to park:** B2 (`agent-af862bd482b53a2f8` `0976a1892f`) is the "terminal-chain-closed phantom absorber (unfreeze latch on swept winners)" — a refinement to K-ABSORBER — sitting on top of the gate-mass-collapse base (`agent-a54ad5b8210fa99ee` `0b553c90fd`, with uncommitted `event_reactor_adapter.py` changes). The gate-mass-collapse base overlaps the Thrust-2 deletions (§2) this program will land on the main branch; merging B2 before the q_lcb foundation is settled risks landing a gate-mass collapse on a seam the W-QLCB fix has not yet cleaned, creating a confounding diff (the exact thing N1→N2 sequencing exists to prevent — sequence §3).
- **Verdict:** **PARKED — pending W-QLCB.** The absorber refinement (`0976a1892f`) is a candidate for K-ABSORBER once the main-branch reconcile path is stable; the gate-mass-collapse base must be reconciled against the §2 deletions (they likely overlap — do not double-delete or delete-then-re-add). Park both until N5 (σ_center, the new primary lever) is at least in shadow, so the gate-mass collapse lands on a clean, post-fix seam rather than confounding the shadow grade.
- **The uncommitted-changes caution:** `agent-a54ad5b8210fa99ee` has uncommitted `event_reactor_adapter.py` + a test. These are unmerged, unaudited (provenance: untrusted until audited per the global rule). Before any merge, they get a provenance audit against current law (do they violate K-SPINE / K-ARM / the §2 D6 guard?). Do NOT fast-merge.

---

## 4. THE BINDING ADDENDA THIS WORKSTREAM IMPOSES ON THE OTHERS (the safety-rail clauses)

This workstream is a rail, so it ends with the explicit constraints it binds onto the sibling plans — each traceable to a KEEP item or a red-team kill:

1. **On W-QLCB (from K-ARM + K-COVERAGE-DOWN):** the q_lcb producer change MUST freeze `arm_gate_coverage_blocks`'s over-claim-block semantics (`settlement_backward_coverage.py:228-259`) and MUST keep `apply_settlement_coverage` shrink-only. The bidirectional UP arm as specified is **not buildable** (base-rate illusion); only the σ_center producer fix (N5) and the down-arm survive. Add the ARM-gate interaction to the W-QLCB risk table (it is currently absent — `P2_W-QLCB.md:246-252`).
2. **On W-QLCB / the σ-shape fit (from K-POINT corollary):** N6 (σ-shape point-q) is OFF the critical path and may not ship as N7's ceiling-raiser. Promote only as an independent, forward-fill-validated point-q improvement.
3. **On W-EDGE-LOCATE (from K-SEMANTICS):** E1/E2 MUST re-grade through `grade_receipt`'s preimage spine before any edge claim (law 8), and the E2 Brier antibody (`model_brier < market_brier`) is a **HARD structural gate on N7-promotion**, not a "consult" — and the "fit on full 7009 history to compress the rate-limiter" lever is **FORBIDDEN** (it fits the would-be UP arm on the base-rate pool while the base-rate antibody is starved — `P1_redteam_1.md:79-81,125`).
4. **On W-SUBMIT (from K-SPINE + the cheap-tail leak):** the mode re-decision re-evaluates the *identical* `capital_efficiency` inequality on the fresh book and stays sequenced last; add the cheap-tail settlement-deadness RED test for the looser TAKER→MAKER direction (`P1_redteam_1.md:108-114`).
5. **On every workstream (from K-INV37 + K-SETTLEMENT-TRUTH):** all cross-DB reads via ATTACH+SAVEPOINT; settlement is the only grade; a dated "market is efficient, no tradeable ring alpha" is a first-class DONE, never a failure to engineer around.

---

## 5. SELF-CHECK — is this KEEP-list defensive in the right direction?

**Yes, and it is the red-team's verdict made into a rail.** The test of this workstream is whether protecting these items prevents the program from (a) loosening the honest gate, (b) manufacturing base-rate alpha, or (c) breaking the metadata foundation. Mapping:

- (a) is guarded by K-SPINE (do not touch the inequality) and K-ARM (do not relax arming).
- (b) is guarded by K-COVERAGE-DOWN (shrink stays one-sided), the §2.4 non-mutation note (no base-rate UP arm), and addendum 3 (Brier as a hard gate, no 7009-fit).
- (c) is guarded by K-POINT (crown jewel untouched), K-SEMANTICS (preimage exact), K-TAIL-ZERO + the D6 guard (far-tail stays rejected through the deletion).

The deletions are strictly the provably-dead subset (EMOS override, source-allow-list, dead C2/C3 import, shadow penalty/N_eff/JS fields) — every one re-verified dead live this session (`edli_emos_ci_live_enabled` absent; license file absent; `authority_on=False`; no-penalties live call). Net gate count strictly decreases (law 3): licensing vocabularies 2→1, dead imports −1, shadow fields −3, with ZERO live-behavior change because every deleted path is already a live no-op.

**The one honest tension surfaced, not hidden:** P1's Thrust 2 framed the shrink-only coverage wrapper as a *collapse target* (fold into a bidirectional authority). This workstream **reverses** that — the red-team proved the one-sidedness is the antibody. A future session reading P1 alone would try to "finish the collapse" and re-introduce the base-rate illusion. The §2.4 explicit-non-deletion block exists precisely to stop that. The KEEP-list is therefore not a static inventory — it is the corrected, red-team-resolved boundary of what subtraction is safe.

*End of P2 W-KEEP-SIMPLIFY. Read-only planning; no production code or daemon changed. Every KEEP / DELETE claim cited to file:line, config flag, artifact, or query+counts re-verified this session; every reshaping traced to `P1_redteam_1.md`.*
