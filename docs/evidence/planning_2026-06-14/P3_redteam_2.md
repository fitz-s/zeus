# P3 — RED-TEAM #2 (FOUNDATION SKEPTIC): Hostile Attack on W-QLCB

**Date:** 2026-06-14
**Mode:** READ-ONLY adversarial review. No code edited, DBs not written.
**Target:** `docs/evidence/planning_2026-06-14/P2_W-QLCB.md` (the q_lcb collapse fix).
**Authority spine:** operator contract laws 1/4/5/8; `diagnosis_confirmation.md`; the actual source the plan proposes to change (`probability_uncertainty.py`, `settlement_backward_coverage.py`, `replacement_forecast_materializer.py`, `sigma_scale_fit.json`), read this session.
**Stance:** I assume the plan is wrong until each step proves itself. I attacked the mechanism at source, not the prose.

---

## VERDICT: **CONDITIONAL — survives only as the producer fix (σ_center); the bidirectional UP arm is REFUTED as designed and must be DEMOTED.**

The plan is two fused proposals. They do not share a fate:

- **Alt-3a (σ_center producer fix)** — replace `center_sigma_c=3.0` with a settlement-fitted center-uncertainty σ. **SURVIVES** the attack as the mechanically honest, law-compliant half. It makes q_lcb *honest at birth* and is the only part that addresses the proven defect (plan §0.2, confirmed at `replacement_forecast_materializer.py:1399,1425`). It is gated correctly (candidate artifact, operator-gated, settlement-fitted not hand-picked).
- **Alt-3b (post-producer bidirectional isotonic UP arm)** — **REFUTED as specified.** The UP arm cannot discriminate population B from population A on the crushed cohort because the isotonic's domain IS the crushed q_lcb value; on the crush cohort all populations collapse to a single band and the map short-circuits to a *pooled* win-rate (proven below at source). It is LOOSER, not more honest, and the §4.1 backtest the plan itself names as "the gate before the gate" has NOT been run — so the entire UP-arm rationale rests on an unproven premise the plan's own sibling docs (S2, S4) actively contradict.

**The headline answer to the prompt's four questions: the plan is HONEST where it fixes the producer and LOOSER where it adds the UP arm.** Loosening is concentrated in exactly the step the plan markets as "the entire fix" (§1.2: *"The UP arm is the entire fix"*). Strip the UP arm; keep the σ_center repair; demand the §4.1 backtest before anything ships.

---

## Q1 — HONEST, or merely LOOSER? (the core attack)

### 1a. The producer fix is HONEST. Conceded, with one binding caveat.

The crush is real and I reproduced its site: `_build_fused_q_bounds` draws 200 centers from `N(μ*, center_sigma_c)` and takes the per-bin 5th percentile (`replacement_forecast_materializer.py:1399,1425`). With `center_sigma_c=3.0` on every posterior (DB-confirmed by S0/P1; the materializer's OWN comment at `:1217-1219` admits "a center-uncertainty bootstrap at that sigma collapses the per-bin 5th-percentile to ~0 on every bin (useless)"), the ring bin's lower tail is zeroed while the point mass stays ~0.10. Replacing 3.0 with the **settlement-measured** center error is not loosening — it is correcting a number that is provably 6–10× too wide against the empirical |fused_center − settled| residual. **Caveat (binding):** "honest" holds ONLY if σ_center is fitted to the settled residual and floored at the empirical error (never below it). The plan says this (§1.4 "forbidden" clause, D3 A2-lean) — but the *failure mode that makes it loosening* is a too-tight σ_center silently admitting far-tail bins. That is F1 in S0 §6 and it is NOT yet bounded by a test in this plan (see Q2). **Producer fix = honest IFF the σ_center fit carries a one-sided "never tighter than measured residual" floor AND a settled-tail no-admit assertion. Plan must add both.**

### 1b. The UP arm is LOOSER. This is the kill.

The plan claims the UP arm "only ever lets the model's HONEST belief reach the gate" because of the `min(target, q_point)` clamp (§1.2, §6). That clamp is real and necessary but it does NOT make the UP arm honest — it only bounds *how* dishonest it can be. Three independent reasons the UP arm re-introduces over-confidence the floors were built to prevent:

**(i) The isotonic cannot see what it claims to discriminate.** `_isotonic_realized_rate` (`settlement_backward_coverage.py:97-125`) fits a monotone map from **claimed q_lcb → realized win-rate**. Its X-axis is the crushed q_lcb itself. On the crush cohort, population A (far tail) and population B (ring) BOTH arrive at q_lcb≈0 — that is the whole premise of the plan ("far-tail and ring share the same crushed raw value", plan §1.4 con). I confirmed at source that when the claimed bands collapse to one distinct value the function **short-circuits to the pooled mean of `won`** (`:115-116`, `if np.unique(xs).size <= 1: return float(np.mean(ys))`). Reproduced this session:

```
claimed q_lcb = 0.005 on 40 obs (all crushed to ~0)  ->  unique bands = 1
-> realized = pooled mean(won) across ALL 40, mixing A and B
```

So the UP arm lifts EVERY crushed bin (A and B alike) to the **same** pooled rate. It does not "select the correct bin" — it averages the ring's wins and the tail's losses and sprays the blend onto both. On a cohort where B is a minority of the crushed mass (S2: the cheap/crushed tail realizes ~4.88%, A dominates), the pooled rate is *low*, so B is under-lifted; but anywhere A is the minority, A is **over-lifted above its honest zero** — the exact population-A fabrication the plan swears it prevents. The `min(q_point)` clamp does not save population A here because a far-tail bin can carry a non-trivial *point* q (the plan's own §0.3 table and S0 §1.5 cite Miami 88-89°F q_point=0.32, Madrid 32°C q=0.23 on bins with ≈0 votes) — so the clamp ceiling is high enough to let the pooled lift through. **The discriminator the plan leans on (isotonic by claimed-band) is structurally blind on precisely the cohort it must discriminate.**

**(ii) "Realized win-rate in the band" is a base-rate, not an edge.** Operator laws 4/5 (memory; S2 §0): buy_no/favorites win at their base rate; a high realized win-rate is *in the price*, not alpha. The UP arm raises q_lcb toward `realized − margin` and then `capital_efficiency` compares it to price. But the market price ALREADY encodes the base rate, so a bin that "realizes 11%" priced at 9% is mostly efficient, not 2pp of edge. The plan's own grading contract (S2 INV-CAL-4, P1 §3 T6) demands a **vs-market Brier** gate before any band is called edge — the UP arm in §1.2 has **no vs-market term**; it compares lifted q_lcb to price directly. **That is the manufacture-edge-from-base-rate trap (law 4/5), dressed as calibration.**

**(iii) It is the FIRST upward authority in a stack deliberately built one-directional.** The plan frames "everything can only lower q_lcb" (§0.4) as a *disease*. Re-read the provenance: every one of those one-directional guards (penalties subtract only — `probability_uncertainty.py:311`; coverage shrink-only — `:222-224`; `q_lcb ≤ q_point` clamp — `:228`) is a deliberate antibody, and the file headers state why (Hidden #2, `probability_uncertainty.py:225-233`: an LCB that can be pushed UP toward a point estimate is "the signature of edge_lcb masquerading as q_lcb"). The asymmetry is not an oversight that "survived 100 patches"; it is the design intent. Adding an UP arm REMOVES an antibody. The burden is on the plan to prove the antibody is wrong — and it has not, because the §4.1 backtest is unrun.

**Q1 verdict: producer fix honest (with two missing guards); UP arm looser, and looser in the law-4/5/8 direction.**

---

## Q2 — Would the new q_lcb pass SETTLEMENT-GRADED calibration (reliability/coverage)?

**No evidence it would; strong sibling evidence it would not.** This is the most damning gap.

- The plan's §4.1 backtest ("RESTRICT to FUSED_NORMAL_FULL AND q_point>0.05; does R/E≈3–4× under-coverage PERSIST once population A is excluded?") is named by the plan AND by P1-D2 as **the gate that decides whether the UP arm ships at all**. It has **not been run** in this plan — §4.1 is written in the future tense ("MUST run first"). So W-QLCB is asking to pre-commit the UP-arm design before its own decisive test exists. That is backwards.
- The plan's sibling S2 (`P1_S2-calibration.md`) ran the *opposite* test on settlement-graded data and reached the *opposite* conclusion: event-level, the cheap/crushed bins realize at **4.88%** matching their price (HONEST zero, "no crushed real mass hiding under the LCB floor", S2 §0/§3). The only positive signal S2 found (near-center [0.05–0.15] under-confidence) is **15 event-level observations** — "it would be malpractice to flip it live." S4 (per b2 audit concordance) found the far tail settles **0/72**. So the settlement record the plan WOULD be graded against currently says: population B is thin-to-absent and population A is honestly dead.
- "Would it admit cheap longshots that then lose?" — On the crush cohort, by mechanism Q1(i), the pooled UP lift admits A-flavored bins wherever A is the minority of a crushed band. Those are exactly the 0/72 losers. So the honest answer to the prompt's Q2: **as designed, the UP arm admits blended A+B mass and the settlement record (4.88% cheap realization, 0/72 tail) predicts the marginal admits LOSE** — it would FAIL settlement-graded coverage on the surface where it is most active.

The producer fix, by contrast, would pass a *coverage* check trivially because it only re-shapes the lower bound toward the point on bins the model already believes — it admits nothing the point q does not already support, and S0 §6 F1 gives the settled-tail assertion that catches over-tightening. **Producer fix: plausibly passes. UP arm: predicted to FAIL by the plan's own sibling settlement evidence.**

---

## Q3 — Is the CORRECT BIN actually selected, or bin-belief wrong underneath?

**Bin SELECTION (identity) is sound; this is the plan's strongest foundation and I do not dispute it.** S0 §7 confirmed q_json↔settlement bin mapping resolves 270/270; the rounding preimage is enforced at the SAME `settlement_preimage_offsets` contract the q-bound bootstrap uses (`replacement_forecast_materializer.py:1407-1409`). Σq = winners (P1, S1). The point q is honest; metadata intact. **Law 8 is satisfied for the producer fix** — it touches only the lower-bound width, never the point belief or the bin identity.

**BUT for the UP arm, "correct bin" is the wrong question — the UP arm operates on a COHORT, not a bin.** Because the isotonic pools by claimed-band (Q1.i), the UP arm's unit of action is "all bins crushed to ≈0," a *mixture of correct and incorrect bins*. So even granting bin selection is correct, the UP arm does not act on the correct bin — it lumps the correct ring bin with structurally-dead tail bins and lifts them together. **"Loosening q_lcb just trades noise" is precisely the realized risk here, and it comes from the pooling, not from wrong metadata.** Right metadata does not rescue an authority whose granularity is coarser than the bin.

The one place bin-belief could still poison the producer fix: the σ_center A2 fit measures `|fused_center − settled|` by lead bucket. If `μ*` itself carries a residual bias (center off, not just its uncertainty), A2 absorbs that bias into a *wider* σ_center → producer fix under-tightens (safe but inert). The dangerous direction (A2 fitting σ_center *too tight* because the residual sample is thin and lucky) is F2 in S0 §6. **Plan must cite the measured residual distribution and its n per lead bucket before trusting A2.**

---

## Q4 — Does q_lcb≈0 reflect genuine no-ensemble-support (HONEST) vs a calibration artifact?

**Both, and the plan's taxonomy is correct in principle but UNQUANTIFIED in practice — fatal for sizing the UP arm.**

- The three populations (A structural-zero / B center-crush ring / C honest-below-market) are a sound conceptual split, matching S0 §1.5 and b2 §3. I accept the taxonomy.
- What the plan does NOT have is **the count of population B.** Every number that would size B is either an example (the §0.3 table is anecdotes, not a distribution) or deferred to the unrun §4.1 backtest. Meanwhile the settlement-graded counts that DO exist point to B being small: S2's event-level cheap cohort (4.88% realized, honest), S4's 0/72 tail, b2's "16-21 of 22 bins per family are population C (honest below-market)." **If B is empty/near-empty, q_lcb≈0 is overwhelmingly HONEST (A+C) and the correct action is the plan's own §4.1 fallback: producer fix for mechanical honesty, NO UP arm, dated "market is efficient on the ring" law-1 verdict.**
- The mechanical proof that *some* B exists (a ring bin with q_point~0.10 gets q_lcb=0 purely from the 3.0° jitter — S0 §1.4 simulation) is solid and justifies the **producer fix**. It does NOT justify the **UP arm**: the producer fix alone lifts that exact bin's q_lcb from 0 to ~0.005–0.036 (S0 §1.4 table) *without any settlement isotonic at all*. **The producer fix already captures the honest-B alpha the simulation demonstrates. The UP arm solves a problem the producer fix has already solved, at the cost of re-opening A.**

This is the deepest finding: **the plan's own evidence for B (the σ-sensitivity simulation) is evidence for the PRODUCER fix; the producer fix makes the UP arm redundant on the honest cases while leaving it dangerous on the dishonest ones.** Alternative 1 (UP-arm-only) was correctly rejected; but the plan did not notice that its own Alternative 2 (producer-only) captures the demonstrated alpha and the UP arm adds only downside.

---

## STEP-BY-STEP DISPOSITION

| Plan step | Disposition | Reason |
|---|---|---|
| Alt-3a: σ_center producer fix | **KEEP** | Honest at birth; fixes the proven crush; captures the demonstrated ring alpha (S0 §1.4) with no UP arm. Add the two missing guards (Q1a/Q3). |
| Alt-3b: bidirectional isotonic UP arm | **DEMOTE / KILL as designed** | Isotonic pools by claimed-band → blind on the crush cohort (Q1.i); no vs-market term → base-rate trap (Q1.ii); removes a deliberate Hidden-#2 antibody without proof; redundant with producer fix on honest B. |
| §4.1 sub-population backtest | **PROMOTE to PREREQUISITE-ZERO** | Plan calls it "the gate before the gate" yet specifies a UP-arm design ahead of it. Nothing about the UP arm may be specified until this returns R/E persistence WITH the A-vs-B split and counts. |
| `min(target, q_point)` clamp | KEEP if UP arm survives | Necessary but insufficient (Q1.iii: q_point on a 0-vote tail bin can be high enough to pass the blended lift). |
| DOWN arm (preserve shrink) | KEEP | Existing, proven, antibody-preserving direction. Not new, not at issue. |
| `MIN_N` cold-start = keep raw q_lcb | KEEP | Correct and law-compliant; the one place the plan refuses to fabricate a floor. |
| Wilson-over-votes fallback delete (D4) | DEFER | Out of W-QLCB's risk surface; decide after producer fix lands. |

---

## ADDITIONAL EVIDENCE THE PLAN MUST PRODUCE BEFORE THIS GOES LIVE

1. **The §4.1 backtest, RUN, with counts.** Restricted to `FUSED_NORMAL_FULL ∧ q_point>0.05`, per claimed-band: `n`, `realized_win_rate`, `claimed q_lcb`, `R/E`, **AND the population-A vs B split inside each crushed band** (dist-from-center bucket, S4 salvage). If B (dist 0–2) is small or R/E→~1.0 once A excluded → **UP arm does not ship**; producer fix only; dated law-1 verdict. This single artifact decides the UP arm and currently does not exist.
2. **Isotonic granularity proof.** As specified (`CoverageObservation(q_lcb, won)`, `settlement_backward_coverage.py:65-75`) the map CANNOT separate A from B — both land at X≈0 and it pools. Re-key the observation with a structural coordinate (dist-from-center / bin-kind) so `unique(xs)>1`, and add a test asserting a far-tail band and a ring band land at *different* X. Then show the re-keyed map lifting a ring band while holding a tail band at 0 on REAL settled data, not synthetic.
3. **vs-market Brier per band** (S2 INV-CAL-4 / P1 T6). UP arm must compare lifted-q to MARKET-q (lower Brier on the same settled events), not to price. Produce the per-band model-Brier vs market-Brier table; no UP-arm admission without model-Brier < market-Brier.
4. **σ_center residual distribution with n per lead bucket.** Measured `|fused_center − settled|` by lead bucket + count, and a demonstration the fitted σ_center is floored at (never below) the empirical residual. Plus the promised A1 diagnostic: *prove* `anchor_sigma_c=3.0` is the EQUAL_WEIGHT/τ0 degrade artifact, not a genuine wide posterior on the unlocked cells.
5. **RED-on-revert antibody, strengthened.** Plan §4.2.2 (`test_qlcb_far_tail_stays_zero`) tests a *clean* far-tail cohort (claimed 0, realized 0) — it does NOT test the dangerous case. Add `test_qlcb_mixed_crush_band_does_not_lift_tail`: a band containing both a 0/72 tail bin and a winning ring bin at the same claimed q_lcb≈0; assert the tail bin's calibrated q_lcb stays ≈0. This test FAILS against the current pooled isotonic — which is the point: it proves the discrimination claim is unmet.
6. **Shadow settlement cohort >51% after the 1¢ fee, model-Brier < market-Brier, event-deduplicated** (P1 §5 DONE). Stated by the plan, but gated on (1)–(3) even being coherent.

---

## NOTE ON CITATION DRIFT (non-fatal but flag)

The plan cites the coverage seam as `src/strategy/settlement_backward_coverage.py`; the actual file is `src/calibration/settlement_backward_coverage.py`. Line numbers (97 `_isotonic_realized_rate`, 204 `apply_settlement_coverage`, 222-224 shrink-only) are correct. The two live flags the plan asserts are confirmed ON: `edli.q_lcb_settlement_coverage_gate_enabled=True` and `feature_flags.openmeteo_ecmwf_ifs9_aifs_soft_anchor_trade_authority_enabled=True`.

---

## SUMMARY (4 lines)

1. **CONDITIONAL: the σ_center producer fix SURVIVES (honest, captures the demonstrated ring alpha); the bidirectional UP arm is REFUTED as designed and must be demoted.**
2. The UP arm's discriminator — isotonic by claimed-band — is structurally BLIND on the crush cohort: it pools A+B at q_lcb≈0 and short-circuits to a single pooled win-rate (confirmed at `settlement_backward_coverage.py:115-116`), lifts honest-zero tail bins, has no vs-market term (base-rate/law-4 trap), and removes a deliberate Hidden-#2 antibody without proof.
3. It is therefore LOOSER, not more honest, in the law-4/5/8 direction; the producer fix already lifts the honest ring bin from 0 to ~0.005–0.036 (S0 §1.4) WITHOUT the UP arm, making the UP arm redundant where it could help and dangerous where it cannot.
4. The plan's own decisive gate (§4.1 sub-population backtest) is UNRUN and its sibling settlement evidence (S2: 4.88% honest cheap realization; S4: 0/72 tail) predicts the UP arm would FAIL settlement-graded coverage — so nothing about the UP arm may be specified until §4.1, a re-keyed bin-distance isotonic, and a vs-market Brier table exist.

*End P3 red-team #2. Read-only; no code or DB changed.*
