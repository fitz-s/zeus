# P1 Strategy Scoring — 2026-06-14

**Evaluator:** independent scorer (fresh context; all five strategy files read in full before scoring).
**Lens:** correctness = (a) systematic not a 1-order hack; (b) respects the full KEEP-list per the binding constraint doc; (c) settlement-grounded — every claim traceable to VERIFIED settlement outcomes, not asserted.
**Binding constraint (authoritative):** q_lcb≈0 on cheap/non-favorite bins drives ~88% `capital_efficiency` rejection. `capital_efficiency` is the HONEST gate — KEEP untouched. The decisive open question: is q_lcb≈0 a CORRECT belief (honest no-edge) or a BROKEN floor crushing real mass? Five strategies each try to answer it independently.

---

## S0 — Foundation

**Score: 64 / 100**

S0 is mechanically the most specific: it identifies `anchor_sigma_c = 3.0°C` hardcoded as the bootstrap center-width (`replacement_forecast_materializer.py:125`), measures its sensitivity in a seeded simulation (q_lcb goes from 0.0000 at σ=3.0 to 0.0356 at σ=0.4 while q_point stays flat at ~0.10–0.12), and traces the full `fused.sd → anchor_sigma_c → center_sigma_c → _build_fused_q_bounds` chain. The simulation table is the most quantitatively precise evidence in the batch and constitutes a reproducible mechanistic claim.

S0 correctly keeps `capital_efficiency` untouched and proposes all three decisions (A: fit σ_center; B: collapse Wilson authority; C: prefer latest COMPLETE fused posterior) as net-simplify moves with zero new gates or caps.

What costs it points: S0's decisive claim — that 1791 fused posteriors all carry `anchor_sigma_c=3.0` because `fused.sd` pins to the EQUAL_WEIGHT prior τ0 rather than a per-cell Bayesian posterior sd — is stated as an observed fact but without running the calibration join that would confirm these posteriors correspond to VERIFIED settlement winners being rejected. Three sibling strategies (S1, S3, S5) independently ran settlement joins and found q_lcb≈0 on cheap bins is CORRECT (0/72, 0/453, 0/1619 settlement winners among cheap-bin rejected/admitted candidates). S0 does not confront this finding — its 270-winner trace shows 83% of winners have q_lcb≥0.03, but those winners are the buy_no/expensive population already traded; the 17% crush cases are not shown to be VERIFIED settlement-winners the system wrongly rejected. Without that evidence chain, the "ring bin q_lcb is crushed when it should be non-zero" claim is plausible but unconfirmed against the decisive fork. A strategy proposing a real mechanism change without first settling the honest-zero vs broken-floor question loses points under the operator's law-1/law-8 hierarchy.

**Best single idea in S0:** the center-sigma sensitivity simulation (§1.4) — if the A1 diagnostic confirms `fused.sd` is indeed pinned at τ0=3.0 across all cells, this is the highest-precision lever in any strategy. The simulation table alone makes S0 worth reading.

---

## S1 — Minimal

**Score: 78 / 100**

S1 ran the decisive calibration backtest the diagnosis explicitly left open (n=119 settled families / 1309 graded bins, 119/119 win-bin match). Its headline finding is settlement-measured and not asserted: Σ(q_point)=119.0 (the model selects the correct bin — law-8 metadata is intact); Σ(q_lcb)=34.4 vs 119 winners (the lower bound discards 71% of settlement-real mass); Brier(q_lcb)=0.0802 > Brier(q_point)=0.0712; q_lcb R/E ratios are 2.87–3.96 across mid-bands. This is the only strategy that directly quantifies the q_lcb under-coverage bias with a properly-sized settled cohort and a Brier comparison.

The structural diagnosis — every calibration authority in the stack is one-directional downward; nothing can correct a too-low bound — is the cleanest articulation of the architectural blind spot causing the alpha suppression, and it has no analog in the other strategies.

K1 (replace the bootstrap-5th-percentile q_lcb with a settlement-calibrated bidirectional bound, generalizing `_isotonic_realized_rate` to move in both directions) is genuinely minimal: it reuses an existing in-repo function that already computes the right quantity, preserves q_point entirely, and is self-correcting in both directions (over-claiming cohorts shrink; under-claiming cohorts rise). K2 (delete `coverage_unlicensed_tail` + source-string licensing) and K3 (declare cheap buy_no out-of-scope) are pure subtraction. K4 (cycle-summary attribution fix) is observability pre-work.

What costs points: the tension between S1's finding (q_lcb is 3–4× too low on mid bins in the full settled posterior) and S3/S5's finding (admitted candidates and cheap-tail candidates settle at 0%) is real and S1 does not resolve it. Both can be true simultaneously — the settled population S1 uses includes ALL bins with any posterior mass, while S3/S5 join specifically to admitted or cheap-tail candidates (a biased subset). But S1 should explicitly note that the R/E=3–4× under-claim in its table applies to bins that settled YES — it should confirm those bins were actually reaching the decision path and being rejected by capital_efficiency, not just bins the system never considered. Without that link, K1's lift is proven on a calibration basis but not on a "would have admitted a settlement-winner" basis. K2's deletion of the Milan-24C antibody before K1 is settlement-proven is also a sequencing risk (noted already).

**Best single idea in S1:** reusing `_isotonic_realized_rate` as the bidirectional LCB authority — it is already the right function, already in `settlement_backward_coverage.py`, and already computes settlement-realized rates. The change is making it directionally symmetric (bidirectional), not inventing a new estimator. Three vocabularies collapse to one. This is the tightest SIMPLIFY of any proposed change in the batch.

---

## S2 — Calibration & Edge-Quality

**Score: 55 / 100**

S2 earns its score by doing one thing no other strategy did: it de-duplicated `no_trade_regret_events` to the event level (one row per city/date/bin, not per snapshot), catching a ~40× inflation artifact that would corrupt any calibration conclusion drawn from the raw per-snapshot aggregates (e.g. NYC 06-04 had 42 rows for one bin). This is a correct, important methodological contribution.

S2 also independently confirms two key code facts: N_eff=3.71 correction and James-Stein shrink-to-market are shadow-only (`authority_on=False`, `compare=False` fields), so they cannot cause the live q_lcb collapse. This confirmation — reached from source code, not from the diagnosis — is valuable provenance work.

What costs points: S2's primary recommendation is a walk-forward reliability harness that outputs `INSUFFICIENT_DATA, accumulate` as its first deliverable. The operator contract defines DONE as continuous >51% after-cost settlement win-rate, not a harness. By S2's own accounting, the first band license requires N_min≈150–200 events, which at ~20 city-days/week is 1.5–2 months away. The system continues to produce zero fills during that window. This is an epistemically rigorous but operationally blocked plan. S2 also contradicts S1 on the same question — S2 calls the near-center [0.05,0.15] signal "15 events, unlicensable," while S1 uses n=119 families and finds material R/E=3–4×. Both are grading the same model against the same settlement DB; the discrepancy is the unit-of-analysis difference (S2 de-duplicates aggressively, potentially losing signal; S1 may include multi-outcome families that inflate n). S2 does not reconcile this tension.

S2 correctly rules out cheap-tail and favorite-NO as alpha targets, but its positive recommendation (Alternative B: harness + hierarchical EB recalibration) is a more-complex system than what the K<<N law prefers.

**Best single idea in S2:** the event-level de-duplication protocol enforced at the GROUP BY level — any settlement-graded calibration test that does not enforce this will report inflated n and misleading realized rates.

---

## S3 — K-Cut

**Score: 82 / 100**

S3 is the strongest overall strategy by the scoring criteria. It delivers three code facts that no other strategy confirmed independently, and that are load-bearing for every other plan: (1) the live q_lcb path applies NO penalty term — `probability_uncertainty_from_samples` is called on the live path but `UncertaintyPenalties` is never populated, so the δ-penalty machinery is dead on the live path; (2) N_eff width correction and James-Stein corrections populate shadow fields only (`q_lcb_neff_corrected`, `authority_on=False`) — not live q_lcb; (3) `edli_emos_sole_calibrator_enabled=True` is live, meaning cheap-bin q is already the smoothest analytic Normal, and the 5th-pct bootstrap STILL hits 0 because those bins genuinely carry ~0.2% analytic mass (a Chengdu example confirms: `q=[0.002,0.002,...]`, `q_lcb=[0.0,0.0,...]`). These findings refute the "N_eff crush" and "James-Stein crush" suspects from the diagnosis, narrowing the search.

S3 also ran an independent settlement adjudication using the repo's own `sigma_scale_fit.json` calibration table (n=215+ settled Bernoulli, `authority=VERIFIED`): near-center bins (dist 0–3) are well-calibrated (ratios 1.00–1.15); the tail is OVER-predicted by 3.4–7.3×. This independently refutes "cheap-tail suppressed alpha" without requiring the full receipt join.

The K=5 design is the cleanest gate-collapse architecture in the batch: K1 (keep single belief seam, delete dead δ-penalty plumbing and shadow N_eff/JS fields), K2 (collapse two licensing vocabularies to the settlement verdict), K3 (keep direction_law whole), K4 (fix hidden flat-0.01 trade_score covert gate), K5 (make SUBMIT_ABORTED_MODE_FLIPPED a re-price-and-re-admit, not a terminal death). All five are SIMPLIFY, net gate-count decreases, and none adds a new artifact, flag, or cap. K5 is uniquely S3's contribution and addresses the secondary blocker (`diagnosis_confirmation.md:130`) more precisely than any other strategy.

What costs S3 the top score: it correctly identifies the near-center dist-1 ring as the only settlement-backed honest-disagreement class, and then assigns the sigma-fit promotion to "the foundation lens" rather than including it in its own K-cut plan. The K=5 collapse leaves q_lcb on ring bins still approximately where it is (the deletions remove dead overhead; they do not move the LCB above price on ring bins). So S3 alone — without S4's sigma-fit promotion — does not produce a fill on near-center ring bins. S3 is the correct structural pre-condition but not the complete path.

**Best single idea in S3:** K5 — convert SUBMIT_ABORTED_MODE_FLIPPED from a terminal abort into a re-price-and-re-admit under the same `q_lcb>price` criterion on the fresh book. This is the secondary-blocker fix, operationally specific, and not present in any other strategy.

---

## S4 — Edge-Location

**Score: 76 / 100**

S4 produced the most granular empirical edge map: 62,874 receipts → 430 settleable instruments, joined to `settlement_outcomes(VERIFIED)`. Its key findings are settlement-proven: cheap buy_yes tail (price<0.05) settled 0/72 (and 0/34 of those that passed the honest q_lcb>price test); the buy_no admitted subset nets −0.069/share after the 1¢ fee; and critically, the bin-KIND cut (§1d) showing `exact` ring bins at realized 0.108 vs model 0.093 vs `or_higher`/`or_below` at 0.020/0.000 is the clearest diagnostic insight in the batch. Three rows explain the entire mass-distribution problem: the model bleeds probability into open tails (5×+ over-confident) and under-fills the `exact` ring bin (where it genuinely beats the market).

S4's "both, in different places" resolution of the decisive fork is the most settlement-grounded and nuanced in the batch: on tail bins, q_lcb≈0 is CORRECT (point q is over-stated; the LCB is being generous relative to settlement); on near-ring `exact` bins, the LCB is zeroed by the conservative stack tuned to protect against the tail's sins. The sigma_scale_fit promotion (Alternative A) is operationally specific, reuses an existing built-and-tested artifact, and has a defined forward-fill validation gate.

What costs points: S4 lists N_eff=3.71 and James-Stein shrink-to-market as bullets 2–3 in the ring-bin LCB crush mechanism (§3), but S3 and S5 independently confirmed from source that these are shadow-only and do not affect live q_lcb. S4 is wrong on this specific point, and it matters because these corrections were wired into the LCB framework specifically to defend against tail over-confidence — if they are NOT live, the crush mechanism is something else (the 5th-percentile of a 51-member thin-vote bootstrap, per S1/S3). This does not invalidate the sigma-fit promotion pick (the artifact still moves mass from tail to ring in the point q, which flows into the bootstrap samples), but the mechanism description in §3 is partially incorrect. S4 also identifies Alternative C (trade the +0.067 NO_price-0.8-0.9 pocket) as the kill-criterion fallback, but that band is the complement of the ring-bin edge seen from the NO side — it is base-rate by law #4 framing and should not be named as a fallback the operator would accept.

**Best single idea in S4:** the bin-KIND cut (`exact` vs `or_higher` vs `or_below`) as the diagnostic frame. Three rows that explain why the system has been fishing the wrong class for 100 patches, and point directly at the sigma-fit promotion as the right lever.

---

## S5 — Gate/Licensing-Implication

**Score: 80 / 100**

S5 ran the most dispositive settlement join of the batch for the gate-layer question: 1,619 admitted buy_yes candidates (`proof_accepted=1`) → 0 settlement winners; 453 cheap-tail → 0 winners; live strategy of record → 8% win rate. This is the cleanest possible evidence that the gate layer is not blocking real alpha — it is reporting a broken foundation. Every gate the synthesis wanted to loosen has blocked only settlement-losers. The conclusion is clean: the gate layer is the only correct actor in the system.

S5 then identifies the gate layer's one real internal defect (not a blocker of alpha but a K<<N violation): three selection authorities coexist (condemned BH/FDR as the live gate; C2/C3 as the operator-ratified replacement, computed but pinned `authority_on=False`; and `capital_efficiency` as the actual arbiter). The C2/C3 live-path import produces only NULL stamps in receipts — neither deciding nor providing telemetry. This is gate-mass in the pure sense. S5's Decision B3 (delete the dead C2/C3 live-path import, leave BH/FDR as condemned-interim with a provenance note, defer the BH→C2/C3 promotion until the foundation is settlement-proven) is the correct minimal move.

S5 also correctly identifies the EMOS-CI override lane (§2.6) as ~100 lines of dead code behind a default-off flag and a never-built artifact, proposes Decision A3 (collapse licensing vocabularies A+B into the single settlement-VERDICT), and explicitly does NOT try to loosen any gate on the grounds that doing so would admit settlement-losers.

What costs S5 the top score: its mandate is "prove the gate layer is not the blocker; collapse dead mass." By its own honest accounting, S5 alone does not move the system toward a fill. All of S5's improvements (A3, B3) are orthogonal to the foundation fix — they produce a cleaner, simpler gate layer, but if the foundation still emits q_lcb≈0 on ring bins, the gate still correctly rejects. S5 is a necessary parallel workstream, not the primary path. The correct sequencing is: foundation fix first (S1/S4), gate collapse second (S5). S5 scores high because it is systematically correct and settlement-grounded within its mandate, but it cannot claim a path to DONE on its own.

**Best single idea in S5:** the 0/1619 admitted-candidate settlement join — the decisive proof that the gate layer is not the blocker. This is the single most important piece of evidence for operator decision-making about where to direct engineering effort, and it is unique to S5.

---

## Scores and rankings

| Strategy | Score | Best single idea |
|---|---|---|
| **S3 — K-Cut** | **82** | K5: re-price-and-re-admit on mode-flip, not terminal abort |
| **S5 — Gate/Licensing** | **80** | 0/1619 admitted-candidate settlement join proves gate layer is not the blocker |
| **S1 — Minimal** | **78** | Reuse `_isotonic_realized_rate` bidirectionally as the single LCB authority |
| **S4 — Edge-Location** | **76** | bin-KIND cut revealing open-tail over-confidence steals mass from ring bin |
| **S0 — Foundation** | **64** | center-sigma sensitivity simulation (§1.4) — highest-precision mechanistic lever if A1 validates |
| **S2 — Calibration** | **55** | Event-level de-duplication (GROUP BY city/date/bin, not per-snapshot) |

---

## Cross-cutting tensions (evaluator note, not a new strategy)

Three empirical tensions across the strategies must be resolved before any plan is implemented:

**T1 (the decisive fork):** S1 shows q_lcb is 3–4× too low on mid bins in the full settled posterior (R/E=3–4×). S3/S5 show admitted candidates and cheap-tail receipts settle at 0%. Both can be true simultaneously: the full posterior has mid-band bins with honest settlement-winning q values that the bootstrap under-estimates; but the admitted candidates are systematically wrong-bin candidates (cheap tail that the model over-states). Resolution: run S1's backtest restricted to `replacement_q_mode=FUSED_NORMAL_FULL` cells and to bins whose `q_point > 0.05` (filtering out the structural-zero far bins). If R/E=3–4× persists on THAT sub-population, the bidirectional isotonic fix (S1/K1) is the right primary lever. If not, S3's "thin ring is the only edge" framing stands and the sigma-fit promotion (S4) is the primary lever.

**T2 (N_eff/JS live vs shadow):** S3 and S5 confirm from source that N_eff and James-Stein are shadow-only. S4 lists them as live causes. S4 is incorrect on this specific point. This does not affect any strategy's primary recommendation but should be noted before any investigation wastes time tracing these paths.

**T3 (ring edge vs 1¢ fee):** S3, S4, S5 all identify the thin near-center dist-1 ring as the only settlement-backed honest-disagreement class. None proves it clears the 1¢ fee at Zeus's throughput. This is the primary execution risk and must be verified in shadow before DONE is claimed.

---

## Recommended integration

The strategies are more complementary than competing. The highest-confidence path integrates them in sequence:

1. **S5/A3 + S5/B3** immediately (pure subtraction, zero behavior change, reduces dead code noise in future debugging).
2. **S3/K1 + S3/K2 + S3/K4** (gate collapse, one selection authority, fix hidden trade_score floor — all pure simplify, prerequisite for clean signal).
3. **S1/K1** in shadow (settlement-calibrated bidirectional q_lcb; shadow so the forward evidence accumulates before live).
4. **S4 sigma-fit promotion** after forward-fill validation passes its own `_meta.promotion` criterion (moves mass from tail to ring in the point q, S1/K1's upstream input).
5. **S3/K5** (re-price-and-re-admit on mode-flip) once admission produces a ring-bin candidate — the secondary blocker fix becomes relevant only then.
6. **S2's event-level harness** as the ongoing settlement monitor for all of the above (not the gating authority; the shadow output from steps 3–4 IS the walk-forward data).

DONE criterion: the shadow ring-bin cohort admitted by steps 3–4 must clear >51% after-cost in forward settlement at n≥30 fills before any live promotion. If it does not, the honest answer is law-1-compliant: the market is efficient and the ring edge does not survive friction. That determination is proven with settlement dates and numbers, not engineering effort.
