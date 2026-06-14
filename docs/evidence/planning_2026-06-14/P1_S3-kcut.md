# P1 / S3 — K-Decision Strategy for the Candidate→Belief→Quote→Cost→Decision→Submit Path

**Date:** 2026-06-14
**Mode:** PLAN-MAKING (design + write only; no production edits, no deploy, no live touch).
**Lens:** First-principles K-decision strategist. Treat the current architecture as ONE option, not a given.
**Authority inputs read in full:** `diagnosis_confirmation.md` (authoritative target), `synthesis.md` (central claim REFUTED; mined for keep-invariants + the 5 contradictions), `b2_capital_efficiency_audit.md`, `live_state_tracker.md`, `AGENTS.md`, `docs/authority/replacement_final_form_2026_06_09.md`, and the live decision-path source (`event_reactor_adapter.py` q-construction + EMOS override, `live_admission.py`, `probability_uncertainty.py`, `selection_shrinkage.py`, `market_fusion.py`, `probability_arithmetic.py`, `state/sigma_scale_fit.json`).
**New empirical work done for this plan (cited inline):** settlement-grade win-rate by price band/direction; model-q calibration vs realized; the repo's own settlement-fitted ring calibration; the live admitted-candidate population; live flag state.

---

## 0. Objective (restated against the operator contract)

DONE is a stable, continuous, >51%-after-cost **settlement** win-rate on TRADED markets, proven by real market-chain alpha — where "alpha" means `q_lcb > price` after cost on bins where the model **honestly disagrees with the market AND settlement backs the disagreement**, NOT base-rate favorite-buying (cost>0.6 buy_no, ~90% win already in the price) and NOT cheap-longshot lottery legs.

The single decision this plan must make: **redesign the gauntlet from candidate to submit as K honest decisions (K≪N), admitting every real correct-bin +EV trade and rejecting every base-rate-favorite-masquerading-as-edge — and locate where the real correct-bin edge actually lives.**

The decisive sub-question handed to this plan (from `diagnosis_confirmation.md` §IMPLICATION + law 8 + law 1): is the live `q_lcb≈0` on cheap/non-favorite bins a **CORRECT** bin-belief (genuine no-edge) or a **BROKEN** calibration/LCB-floor crushing real probability mass off the correct bin? **This plan answers it with settlement evidence before designing anything**, because the answer decides targeted-fix-vs-rebuild and whether suppressed alpha exists at all.

---

## 1. Re-derivation from evidence (not a restatement of the diagnosis)

### 1.1 The live q_lcb genesis — read from source, not assumed

The live (non-shadow) `q_lcb_5pct` is produced in `_canonical_probability_and_fdr_proof` → `_side_q_lcb_from_yes_samples` (`event_reactor_adapter.py:10148-10191`). The construction is:

```
q_lcb_yes = lower_quantile(yes_probability_samples, α=0.05)   # 5th percentile of the bin's YES bootstrap samples
          clamped under q_yes_point
q_lcb_no  = lower_quantile(1 - yes_samples) = 1 - q_ucb_yes    # native-NO complement, Hidden #3
```

Three facts the diagnosis did not surface but that are load-bearing:

1. **No penalty term is applied on the live path.** `_side_q_lcb_from_yes_samples` calls `probability_uncertainty_from_samples(yes_samples)` with **no `UncertaintyPenalties`** (`event_reactor_adapter.py:10181`). The δ-penalty machinery in `probability_uncertainty.py` (calibration/boundary/representativeness/...) is **dead on the live path** — it lowers nothing. So q_lcb is the *raw* 5th-percentile of the probability bootstrap, clamped under the point. There is no penalty "floor" crushing cheap bins; the collapse to 0 is a property of the *sample distribution itself*.

2. **`probability_uncertainty.py` is the DEFAULT-OFF / SHADOW contract object** (its own header, lines 17-19: "Importing it changes NO live trading behavior; it is not wired into the live decision path"). The live path uses only its `lower_quantile`/`no_side_samples` primitives via the seam helper — the C3 N_eff width-correction and James-Stein shrink (`#61`) populate **shadow fields only** (`q_lcb_neff_corrected`), never the live `q_lcb`. So the suspects the prompt listed (N_eff=3.71 correction, James-Stein shrink) are **not in the live q_lcb** — they cannot be the cause of a live collapse. Ruled out by code.

3. **The EMOS point is LIVE, the EMOS CI-license is DEAD — and these are two different flags.** `edli_emos_sole_calibrator_enabled = True` (live, verified in `config/settings.json`): for a `served=emos` (city,season) cell, the traded point q IS the EMOS predictive `N(μ,σ)` (analytic bin integration), and the bootstrap draws from the SAME `N(μ,σ)`. Separately, `edli_emos_ci_live_enabled` (the CI *override* that would stamp `q_lcb_calibration_source=EMOS_ANALYTIC`) is absent → default False (`event_reactor_adapter.py:11995`), gated additionally behind the never-built `state/emos_ci_license.json`. **Consequence:** the cheap-bin q is already the smooth EMOS analytic Normal, not a ragged ensemble vote — and its 5th-percentile still lands at ~0 on far bins because those bins genuinely carry ~0.2% analytic mass. The collapse survives the *smoothest* belief we have. (Verified directly: a live Chengdu 06-15 posterior reads `q=[0.769,0.185,0.037,0.002,0.002,0.002]`, `q_lcb=[0.756,0.062,0.005,0.0,0.0,0.0]` — q_lcb hits 0 exactly where q itself is ~0.002.)

### 1.2 The decisive settlement test — is cheap-bin q_lcb≈0 correct or broken?

I went to the only authority that can answer it (operator law #5: settlement is the only truth). Two independent cuts, both pointing the same way.

**(a) The repo's own settlement-fitted ring calibration** (`state/sigma_scale_fit.json`, `calibration_at_fit`, graded by `scripts/fit_sigma_scale.py` on settled Bernoulli outcomes, `authority=VERIFIED`, window settled-2026-06-08..06-12). This is the trustworthy cut — no fragile join, the repo's own settlement grading:

| ring (dist from forecast center) | mean_q (C) | realized (C) | ratio realized/expected (C) | ratio (F) |
|---|---|---|---|---|
| 0 (center) | 0.214 | 0.223 | **1.04** | 1.15 |
| 1 | 0.185 | 0.213 | **1.15** | 1.01 |
| 2 | 0.120 | 0.119 | **1.00** | 1.22 |
| 3 | 0.059 | 0.061 | **1.04** | 1.00 |
| ≥4 | 0.012 | 0.017 | 1.42 (n=14, noisy) | 1.19 |
| **tail** | **0.055** | **0.016** | **0.296** | **0.138** |

The verdict is unambiguous: **near-center bins (dist 0-3) are well-calibrated (ratios 1.00-1.15); the TAIL is OVER-predicted by 3.4× (C) to 7.3× (F).** The model assigns *too much* mass to cheap far bins, not too little. A 5th-percentile q_lcb that collapses to ~0 on those bins is the *honest, conservative* response to a class the point estimate already over-states.

**(b) Direct settlement win-rate of the no-submit receipt population**, joining receipts→`(city,target_date)`→`settlement_outcomes.winning_bin`. The buy_yes cheap bands (price<0.2) realize **near-zero settlement win-rate** with **negative W−cost** in every band, despite q_lcb claiming 0.16-0.40. (Caveat, stated honestly: this population is loser-biased per-candidate book entries and my question-string→winning-bin parser is imperfect for some unit/range/truncation forms — so cut (b) is *corroborating*, not primary. Cut (a) is the authority, and the two agree in direction.)

**Answer to the decisive question: q_lcb≈0 on cheap/tail bins is a CORRECT bin-belief — honest no-edge.** It is not a broken floor; if anything the point q over-states the tail and the conservative LCB is *more* right than the point. **There is no suppressed cheap-longshot alpha to unblock.** (This vindicates `b2_capital_efficiency_audit.md`'s HONEST_NO_EDGE verdict and operator-contract law #4/#8 with fresh settlement evidence.)

### 1.3 Where the real correct-bin edge actually is (and is not)

- **NOT the cheap tail.** Settlement over-prediction by 3-7× (1.2). Dead.
- **NOT base-rate buy_no favorites.** The live admitted population since 06-12 is **32 candidates: 31 buy_no in the >0.6 band, 1 in 0.2-0.6, ALL `FORECAST_BOOTSTRAP`** (verified). Their realized win is high but cost is high too — `cost>0.6` buy_no is ~90% in the price (operator law #4). `W−cost ≈ +0.03` at >0.6 is base rate, not alpha. Re-enabling this is explicitly forbidden as "alpha".
- **The one settlement-backed honest-disagreement signal is the dist-1 ring** (ratio 1.15 in C, i.e. realized 0.213 vs predicted 0.185): the model honestly holds *slightly more* correct-bin mass than realized, and the market — priced near the model under MODEL_ONLY fusion — under-prices these near-center bins. This is small (a few points), near-center (not cheap), and is exactly the class the `direction_law` buy_yes-near / buy_no-far doctrine already scopes. **This is the only place a continuous >51% correct-bin edge can plausibly live**, and it is a *thin* edge that depends entirely on metadata correctness (bin identity, rounding preimage, center μ*) being exact.

**Strategic consequence:** the path is NOT "unblock more admissions." It is "make the near-center ring decision correct and continuously executable, and stop spending the engine's entire throughput manufacturing and then rejecting cheap-tail and base-rate-favorite candidates." The edge is small and near-center; everything else is noise the gauntlet currently has to filter.

---

## 2. The current gauntlet (N) — enumerated from live source

Per-candidate, in `_generate_candidate_proofs` order (`event_reactor_adapter.py:7387-7605`), a candidate is killed (`score=0`, `passed_prefilter=False`) by the FIRST of:

| # | gate | site | what it tests | live verdict |
|---|---|---|---|---|
| G1 | **capital_efficiency** | `live_admission.py:87` | `(q_lcb−price)/price ≤ 0` | **HONEST FINAL ARBITER — KEEP.** ~88% of rejects; correct (1.2). |
| G2 | **buy_no_conservative_evidence** | `live_admission.py:183` | material-YES buy_no needs allowed NO LCB source OR settlement-coverage verdict | partially-dead: allow-list `{EMOS_ANALYTIC,SETTLEMENT_ISOTONIC}` never stamped live; verdict-status path is the only live admit |
| G3 | **direction_law** | `direction_law.py` via `:7985` | buy_yes near-center only / buy_no far only | **KEEP — encodes the only real-edge geometry (1.3).** ~3% of rejects. |
| G4 | **coverage_unlicensed_tail** | `live_admission.py:141` | cheap (price<0.05) + q_lcb>2×price + unlicensed source → reject | fail-closed antibody; fires ~0.6%; **its target class is settlement-dead (1.2)** |
| — | FDR prefilter (`passed_prefilter`) | family scan | edge-space MC gate | upstream; structurally redundant with G1 for selection |
| — | mainstream_agreement | receipt fields | ARM clause-3 consistency | advisory at receipt; 91 NULL (not gating live) |
| — | market_anchor cap | `:7472` (flag ON) | one-sided lower of buy_no near-center q_lcb | **KEEP — closes the C3 phantom-NO loss class** |
| — | trade_score 0.01 penalty | `:13737` | `p_fill·(q_lcb−price−0.01)` | **a hidden Kth gate** — at price 0.001-0.01 the flat 0.01 eats all edge (`b2` §5) |

Then at SUBMIT (a *separate* stage, post-admission): `SUBMIT_ABORTED_MODE_FLIPPED` (proof's TAKER/MAKER mode ≠ fresh book at submit — the **dominant live submit-stage killer**, verified), `event_bound_final_intent_no_submit`, `real_order_submit_disabled`, and the (now-cleared) M5 reconcile latch.

**N is effectively ~8 admission gates + ~4 submit gates + an FDR prefilter that duplicates G1.** This is the gate-mass the operator condemns.

---

## 3. The K-cut — design, with weighed alternatives

The minimal honest decision set is **K = 5** (4 admission + 1 submit), built around ONE admission criterion and a clean direction/geometry prior. I give the criterion, then each collapse with alternatives and a pick.

### 3.1 The single admission criterion (the spine of K)

> **Admit iff `q_lcb_side > price_side` after cost, where `q_lcb_side` is the settlement-licensed conservative lower bound on the side's win probability, and the bin is direction-law-legal.** Equivalently: conservative EV/$ `(q_lcb−price)/price > 0` on a bin the doctrine permits, with the q_lcb carrying a settlement-grade provenance.

Everything else is either (a) a *computation* of that criterion's inputs (belief, cost, license) or (b) an *antibody* that makes a known loss-class unconstructable. Nothing else manufactures or destroys edge.

### 3.2 K1 — BELIEF (one q authority, one q_lcb authority)

**Decision:** the live q point is the single replacement-chain q (EMOS analytic for served=emos, fused-normal-direct otherwise; `replacement_final_form` §1e/§1e-bis); the live q_lcb is the single `_side_q_lcb_from_yes_samples` seam (raw 5th-percentile of the same samples, clamped under point). **No second belief, no penalty maze, no shadow N_eff override on the live value.**

*Alternatives weighed:*
- **(a) Keep as-is (single seam, raw 5th-pct, no penalties).** Pro: it is already one authority, settlement-calibrated near-center (ratios ~1.0). Con: the dead penalty machinery + shadow N_eff/James-Stein fields are latent confusion.
- **(b) Wire the `probability_uncertainty.py` δ-penalties live.** Pro: principled forecast-quality haircut. Con: settlement says near-center is *already* calibrated (ratios 1.0-1.15) — adding penalties would push well-calibrated bins below the market and DESTROY the only real edge (the dist-1 ring). **Rejected — it would suppress the alpha we're trying to reach.**
- **(c) Wire the C3 N_eff width-correction live.** Con: it widens the LCB (lowers q_lcb) — same edge-destroying effect on the near-center ring; and it is settlement-unproven. **Rejected.**

**Pick: (a), made explicit — and DELETE the dead machinery** (the unused `UncertaintyPenalties` plumbing on the live seam, the shadow `q_lcb_neff_corrected`/James-Stein fields that never reach a live decision). Per operator's no-shadow law, a shadow field that gates nothing is gate-mass: remove it, don't keep it dark. *This is a SIMPLIFY, not a new gate.* The belief is correct; the surrounding optionality is the disease.

**Why this is the foundation (law 8):** the dist-1 edge is a few points. It only exists if μ* (the fused center), the per-city settlement rounding preimage (#24), and bin identity are exact. K1's real content is *protect the metadata that makes the near-center q honest* — which is why §5 invariants pin the time-semantics / preimage / direction-center contracts as load-bearing, not the LCB arithmetic.

### 3.3 K2 — LICENSE (collapse two licensing vocabularies to one settlement verdict)

**Problem:** there are TWO parallel "is this q_lcb allowed to trade" vocabularies — the static source allow-list `{EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}` (used by G2 and G4) and the `settlement_backward_coverage` VERDICT status `{LICENSED, UNLICENSED}` (used by the cert credential and threaded into G2). The allow-list is **dead** (no live code stamps `EMOS_ANALYTIC`; `SETTLEMENT_ISOTONIC` requires min_n=30 settled obs a near-future tail never has). The verdict authority is **alive** and already the single home in `live_admission.py` (`SETTLEMENT_COVERAGE_LICENSING_STATUSES`).

**Decision:** make the `settlement_backward_coverage` verdict the **sole** license authority. Replace the static source-string membership test in BOTH G2 and G4 with the verdict-status test that already coexists in G2. The brand string (`q_lcb_calibration_source`) becomes pure provenance/telemetry, never a gate input.

*Alternatives weighed:*
- **(a) Materialize `state/emos_ci_license.json` + flip `edli_emos_ci_live_enabled`** (synthesis K1a). Con: builds a NEW operator-armed artifact + flag — exactly the gate-accretion the operator forbids; and `diagnosis_confirmation` GAP 6 proves it was never built (by design). And it would only re-license a class (cheap tail) settlement says is dead (1.2). **Rejected on two independent grounds.**
- **(b) Re-route the licensing test to the settlement verdict** (synthesis K1b). Pro: collapses two vocabularies → one, no new flag, preserves the fail-closed INTENT. Con: GAP-question — does ANY current bin carry a LICENSED verdict? If the cheap-tail class is settlement-dead, this admits ~nothing new — but it also stops the dead allow-list from *falsely* implying a lane exists.
- **(c) Delete G4 (coverage_unlicensed_tail) entirely.** Pro: maximal simplification; its target class is settlement-dead so it "protects" nothing tradeable. Con: it is the fail-CLOSED dual of the Milan-24C fail-open incident (`2026_06_10_milan_24c_first_fill_rootcause.md`); deleting it re-opens the loss class if a future calibration regression resurrects cheap-tail confidence. The operator keeps honest antibodies.

**Pick: (b) for the vocabulary collapse, and KEEP G4's intent but folded into the single verdict criterion.** Concretely: G2 and G4 stop reading the source allow-list; both read the one settlement-coverage verdict. This is a genuine N→K collapse (two licensing tests → one verdict authority) that *removes* a dead vocabulary without adding anything. **Honest expectation, stated up front:** because the cheap-tail class is settlement-dead (1.2), this admits little-to-nothing new — its value is *truth* (the engine stops carrying a dead lane that makes the log lie about why candidates die) and *antibody preservation*, not unblocking alpha. If a verdict-licensed cheap bin ever does appear, it is now reachable through ONE door instead of an unreachable allow-list.

### 3.4 K3 — DOCTRINE (direction_law stays; it is the edge geometry)

**Decision:** keep `direction_law` exactly (buy_yes forecast-adjacent only, buy_no forecast-distant only). It is not a throttle — it is the *encoding of where settlement-backed edge lives* (1.3: the dist-1 near-center ring is the only honest-disagreement class). It also makes the Milan far-tail-YES loss unconstructable.

*Alternative:* subsume direction_law into the ΔU ranker (let a far YES simply score negative). Con: the ranker subsumes the *cheap-NO-overconfidence* class (S4 removal, already done), but a far-tail YES with a phantom q_lcb could still rank positive *before* the LCB is grounded — direction_law is a deterministic pre-ranking antibody for the exact incident class (`0b5c305e`). **Keep it as defense-in-depth; do not fold.**

### 3.5 K4 — COST/SIZING (one ΔU, fix the hidden trade_score floor)

**Decision:** the chosen-execution-mode EV (`_mode_consistent_ev_for_proof`) is the single score; the q_lcb leg and the executable cost curve are its inputs; horse-race Kelly (#63) sizes. **Fix the one hidden Kth gate:** the flat `0.01` penalty in `_robust_trade_score_from_generated_inputs` (`:13737`) eats the entire edge at price 0.001-0.01 (`b2` §5) — at those prices it is an implicit cheap-bin ban masquerading as a score. Replace the flat additive 0.01 with a **cost-proportional** adverse-selection term (already the maker path's logic) so a near-center cheap bin with a genuine LCB edge is scored honestly rather than zeroed by a constant.

*Alternative:* leave the 0.01 (it conveniently kills cheap bins). **Rejected — that's a covert gate doing direction_law/capital_efficiency's job dishonestly; if cheap bins are dead, let capital_efficiency reject them transparently, not a hidden constant. Operator law: honest gates only.** Net effect is near-zero on admissions (cheap bins are settlement-dead anyway) but it removes a lie.

### 3.6 K5 — SUBMIT (one mode-coherent submit authority)

**Decision:** the dominant live submit-stage failure is `SUBMIT_ABORTED_MODE_FLIPPED` (proof priced as TAKER, fresh book is MAKER, or vice-versa). This is correct *safety* (don't submit a TAKER order into a book that moved to MAKER economics) but it is currently a **terminal reject**, wasting the admission. **Collapse the submit stage to one authority:** re-price the chosen mode against the fresh book at submit (the maker/taker EV is already computable there), and if the *re-priced* mode still clears `q_lcb>price` after cost, submit in the fresh mode; only abort if the fresh re-price fails the SAME single admission criterion (3.1). Mode-flip becomes a *re-decision*, not a death.

*Alternatives:*
- **(a) Keep terminal abort.** Con: throws away admitted +EV because the book ticked; the candidate must re-traverse the whole gauntlet next cycle, often after the window closed.
- **(b) Submit in the proof's original mode regardless.** Con: submits stale-mode economics — the exact adverse-selection the abort protects against. **Rejected.**

**Pick: (a-fixed) — re-price-and-re-admit at submit under the one criterion.** This is the second-blocker fix `diagnosis_confirmation` §2 names (454 cheap-tail receipts died at submit), made principled: the submit gate becomes the SAME `q_lcb>price` decision evaluated on the fresh book, not a separate vocabulary. (Note: the cheap-tail class that dominated those 454 is settlement-dead, so the *quantitative* unblock is small — but the near-center ring candidates that DO carry edge will stop dying to a transient mode tick.)

### 3.7 The K=5 result

| K | decision | one authority | collapses / deletes |
|---|---|---|---|
| K1 | BELIEF | single q + single q_lcb seam (raw 5th-pct, no live penalties) | deletes dead δ-penalty plumbing + shadow N_eff/JS fields on live seam |
| K2 | LICENSE | one settlement-coverage verdict | deletes the dead source allow-list vocabulary in G2+G4; no new flag/file |
| K3 | DOCTRINE | direction_law (edge geometry) | kept whole (it IS the edge prior) |
| K4 | COST/SIZE | one mode-consistent ΔU + horse-race Kelly | fixes the hidden flat-0.01 covert cheap-bin gate |
| K5 | SUBMIT | re-price-and-re-admit under the K1 criterion | collapses mode-flip-abort into the single admission decision |

**The single admission criterion (3.1) is the spine; capital_efficiency IS that criterion and stays untouched.** K2/K3/K4 are its inputs and antibodies; K5 is the same criterion re-evaluated at submit. **N≈12 → K=5.** Nothing added. Two dead vocabularies and one covert constant gate removed.

---

## 4. The causal chain to a settlement-proven fill

1. Family generated → replacement-chain q (EMOS analytic / fused-normal-direct), settlement-calibrated near-center (ratios ~1.0).
2. K1: per-bin q_lcb = raw 5th-pct of the same samples, clamped under point. Near-center bins carry honest q_lcb a few points above/below market; cheap-tail bins carry q_lcb≈0 (correct).
3. K3: direction_law keeps only buy_yes-near and buy_no-far candidates — the geometry where the dist-1 honest-disagreement edge lives.
4. K2: license = settlement-coverage verdict. Near-center bins with a LICENSED/UNLICENSED-shrunk verdict carry settlement-grade q_lcb; unbacked tails fail.
5. K4: ΔU = mode-consistent EV with honest (cost-proportional) adverse-selection; horse-race Kelly sizes.
6. K1-criterion (= capital_efficiency): admit iff `q_lcb>price` after cost. The near-center ring where realized 0.213 > predicted 0.185 > market-price clears; base-rate favorites clear only when truly +EV after their high cost; cheap tails reject honestly.
7. K5: at submit, re-price the chosen mode on the fresh book; if it still clears the criterion, submit in the fresh mode; else re-queue (not die).
8. Fill → position → **settlement grades it** → `fit_sigma_scale` / coverage verdict ingest the realized outcome → the ring calibration updates → loop. **The proof of DONE is step 8 repeating with realized win-rate >51% after cost on the admitted near-center class, NOT step 7 firing once.**

---

## 5. Invariants (KEEP — these make the edge honest; do not touch)

- **INV-A (capital_efficiency is the honest arbiter).** `(q_lcb−price)/price≤0 → reject`. Never loosen. It is the K-spine, not the defect (`b2`, keep-invariant #2).
- **INV-B (settlement is the only truth).** Edge is proven only by forward settled fills (`replacement_final_form` §4: in-sample EV inflated, holdout collapses to +1.2¢..−2.7¢). No in-sample promotion.
- **INV-C (metadata before math — law 8).** The near-center edge is a few points; it exists only if bin identity, per-city rounding preimage (#24), time-semantics contract (#16), and μ* center are exact. These are the foundation; the LCB arithmetic is downstream relay.
- **INV-D (direction law inviolable).** buy_yes near / buy_no far. The edge geometry and the Milan-24C antibody.
- **INV-E (EMOS HIGH-metric only; k_cov never tightens σ).** CI-honesty law (synthesis keep #6). No licensing LOW-metric, no σ below MC.
- **INV-F (fail-closed antibodies survive collapse).** coverage_unlicensed_tail's INTENT (block unbacked cheap tails) survives folded into the single verdict criterion; the market-anchor cap (closes C3 phantom-NO) stays. Collapsing vocabularies ≠ deleting antibodies.
- **INV-G (INV-37 cross-DB discipline; K1 DB split).** ATTACH+SAVEPOINT only; zeus-world/forecasts/trades ownership intact.
- **INV-H (no shadow, no gate-mass).** A shadow field/flag/lane that gates nothing is the disease; go-live-direct or delete. K1/K2 *remove* dead optionality, never add.

---

## 6. What to DELETE (gate-mass / dead vocabulary, per the collapse law)

- Dead δ-penalty plumbing on the live q_lcb seam (`UncertaintyPenalties` is never populated live).
- Shadow `q_lcb_neff_corrected` / `neff_correction_source` / James-Stein fields that never reach a live decision (#61 shadow).
- The static source allow-list `{EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}` as a *gate input* in G2/G4 (keep the string as telemetry; the verdict is the authority).
- The flat `0.01` trade_score penalty as a covert cheap-bin gate (replace with cost-proportional; let capital_efficiency reject cheap bins transparently).
- The `emos_ci_license.json` lane / `edli_emos_ci_live_enabled` path SHOULD NOT be built (red herring; never-built by design; licenses a settlement-dead class).

---

## 7. Failure modes + the verification that catches each

| failure mode | how it manifests | verification that catches it |
|---|---|---|
| **F1 — we "unblock" and re-enable base-rate favorite-buying as "alpha".** | admissions spike, all buy_no cost>0.6; realized win high but W−cost≈base rate. | per-fill settlement grading split by (direction, price band); reject the run if the only profitable band is cost>0.6 buy_no (law #4). |
| **F2 — folding G4 into the verdict accidentally admits an unbacked cheap tail.** | a price<0.05 q_lcb>2×price fill with INSUFFICIENT_DATA verdict reaches submit. | property test: cheap-tail bin with non-LICENSED verdict must reject (Milan-24C antibody); settlement-replay the admitted cheap class for negative realized edge. |
| **F3 — removing the flat-0.01 floods cheap near-center bins that don't actually clear.** | cheap-bin admissions rise, settlement win <51%. | the K1-criterion (capital_efficiency) still gates on q_lcb>price; F3 is caught by realized win-rate per band — if cheap near-center is unprofitable, capital_efficiency already rejects it (cheap-bin q_lcb≈0). |
| **F4 — K5 re-price-at-submit introduces a submit-time edge that admission never saw.** | fill economics differ from the admitted proof. | the submit re-price MUST evaluate the SAME `q_lcb>price` criterion (3.1); assert proof-criterion == submit-criterion (one authority). |
| **F5 — the dist-1 "edge" is in-sample noise (n per ring is small).** | live near-center ring fills realize ≤ predicted at settlement. | INV-B: forward holdout only; require the ring ratio to hold on out-of-window settled fills before sizing up; start at the $5-15 envelope (#18). |
| **F6 — metadata regression silently moves the center/preimage** (law 8). | q honest near-center becomes wrong-bin; realized win collapses with no gate firing. | INV-C contracts (time-semantics #16, preimage #24) have property tests; add a settlement-vs-bin-identity reconciliation check to the harvester loop. |

---

## 8. Targeted-fix vs rebuild verdict

**TARGETED_FIX — but NOT the synthesis's fix.** The synthesis targeted the licensing lane (0.6% of rejections, settlement-dead class) and was REFUTED. The correct targeted fix is a **K=5 collapse that (a) leaves the honest spine (capital_efficiency, q, direction_law, reconcile/absorber, INV-37) untouched, (b) deletes two dead vocabularies + one covert constant gate, and (c) makes the submit stage a re-evaluation of the single admission criterion instead of a separate death.** No rebuild of the q chain — it is settlement-calibrated near-center. No new flags, files, caps, or lanes.

**The honest headline the operator must hear:** there is **no large suppressed alpha pool** waiting behind the gates. The settlement record says cheap-tail is over-predicted (dead) and the only filled class is base-rate favorites (not alpha). The single durable correct-bin edge is the **thin near-center dist-1 ring** (~3 points of honest market under-pricing), and capturing it continuously is a *metadata-correctness + continuous-executability* problem (K1 belief integrity, K5 submit re-decision, INV-C contracts), **not** a gate-loosening problem. The K-cut's job is to stop the engine wasting its throughput on dead classes and to let the thin real edge reach a fill and be settlement-graded — then prove >51% on THAT class, forward, before sizing up.

---

*End of S3 K-cut strategy. Written under PLAN-MAKING boundary — no code, no deploy, no live touch. Every empirical claim cited to file:line, artifact, or query+counts above.*
