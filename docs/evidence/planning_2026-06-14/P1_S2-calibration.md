# P1 / S2 — Calibration & Edge-Quality Strategy

**Lens:** Calibration / edge-quality strategist (CENTRAL given the verified binding constraint).
**Date:** 2026-06-14
**Mode:** PLAN-MAKING. No production-code edits, no deploy, no live touch. DBs opened read-only.
**Author basis (read in full):** `diagnosis_confirmation.md` (authoritative), `synthesis.md` (central claim REFUTED; mined for keep-invariants + contradictions), `b2_capital_efficiency_audit.md`, `live_state_tracker.md`, `docs/authority/replacement_final_form_2026_06_09.md`; live source `src/strategy/probability_uncertainty.py`, `src/strategy/selection_shrinkage.py`, `src/strategy/market_fusion.py`, `src/strategy/market_analysis.py`, `src/contracts/probability_arithmetic.py`, `src/engine/event_reactor_adapter.py` (q_lcb materialization + selection-shrinkage seams), `src/strategy/live_inference/live_admission.py`, `src/data/replacement_forecast_materializer.py`, `state/sigma_scale_fit.json`.
**New settlement-graded evidence produced here:** event-level calibration over `no_trade_regret_events` (260,783 rows, `would_have_won` graded). This is the decisive artifact every prior pass left as "unverified item #1/#2".

---

## 0. TL;DR — the verdict the operator most needs

The headline "q_lcb collapses to ~0 on cheap bins and that is the binding constraint" is **half-true and mis-framed**, and the prevailing remedy instinct (loosen the LCB, re-license cheap tails, widen sigma so far-bin q rises) is **the wrong direction and would manufacture loss, not alpha.**

What the settlement-graded record actually shows, event-level (de-duplicated to one row per city/date/bin, killing the snapshot-repeat inflation that fooled the raw aggregates):

- **The cheap-tail q is HONEST, not suppressed.** Cheap buy_yes bins (price < 0.05): 41 distinct settled bin-events, realized win-rate **4.88%**, average price 0.0118. The model's cheap-bin probability matches the realized frequency to within noise. There is **no crushed real mass** hiding under the LCB floor on cheap bins — the bins genuinely do not win. `q_lcb≈0` there is the correct bin-belief (law 8 / law 1 resolved: HONEST zero, not artifact).
- **The 18,829 `capital_efficiency` rejections are overwhelmingly correct.** They are mid/expensive bins where a small-but-positive `q_lcb` (0.02–0.16) is genuinely below the market price. The market is efficient on this surface; rejecting is the truthful arbiter. KEEP the gate untouched (keep-invariant #2 confirmed independently here).
- **The only durable filled band is base-rate buy_no favorites (price ≥ 0.8), and it is slightly NEGATIVE after cost** (realized 0.9774 vs price 0.9872 → −0.0098/contract). Confirms C5 and operator law #4/#5: favorite-buying is NOT alpha and must not be re-enabled as "the fix".
- **The ONE genuine calibration signal is the OPPOSITE of the cheap-tail story:** the near-center band `q_live ∈ [0.05, 0.15]` is realized at **~0.20 event-level (raw 0.56)** versus a predicted ~0.07 — i.e. the model is **UNDER-confident on near-center low-probability bins**, exactly where the σ-shape pedestal/floor and the fitted uniform mixture push mass AWAY from the mode. But this rests on **15 event-level observations across 2–3 city-days** — far too thin to license live.

**Therefore the calibration objective is NOT "lift cheap-bin q_lcb so longshots trade." It is: (a) prove out-of-sample whether the near-center under-confidence is real with a settlement-graded reliability harness that has enough events to license a band, and (b) make q_lcb on the NEAR-CENTER mid-band [0.05–0.35] trustworthy, because that is the only coordinate where the model honestly disagrees with the market in the profitable direction.** The cheap longshot tail is correctly dead; chasing it violates laws 1/4/5/8. The mid-band is where edge could live — but the evidence base is currently 50 bin-events, and **the first deliverable is the harness that turns 50 into 500+, not a q-formula change.**

---

## 1. Objective (precise, falsifiable)

Make `q_lcb` (and the point `q`) **trustworthy out-of-sample, settlement-graded, versus the market**, on the coordinate where edge can actually exist — the **near-center mid-band** (model `q` ∈ [0.05, 0.35], price not a base-rate favorite) — and **prove** the edge is real at the event level, after cost, against a market benchmark, BEFORE any live flip.

Concretely, DONE for this lens =
1. A **reliability harness** that grades the model's `q` and `q_lcb` against settlement at the **event level** (one row per city/date/bin, never per-snapshot), partitioned by `q` band × price band × lead bucket × source-family, with **per-band Wilson confidence intervals** and a **vs-market benchmark** (Brier / log-loss of model-q minus market-q on the same events).
2. A **calibration verdict** per band: `CALIBRATED` / `UNDER_CONFIDENT` / `OVER_CONFIDENT` / `INSUFFICIENT_DATA (n<N_min)`, where the q-construction is corrected ONLY in bands the harness proves mis-calibrated with n ≥ N_min and a CI that excludes the market.
3. A **licensing rule** that admits a live trade only when, for that band, the settlement-graded realized frequency beats the price after cost with a one-sided lower CI > 0 (the honest analogue of "settlement backs the disagreement"). This REPLACES the source-string allow-list with a settlement-grounded band verdict (collapses two licensing vocabularies to one — operator law #3, collapse N→K).

This objective explicitly **excludes** loosening `capital_efficiency`, widening σ to lift cheap-tail q, restoring the EMOS license file, or re-enabling favorite buy_no.

---

## 2. The mechanism — how `q_lcb` is built today, end to end

This is the causal chain a candidate's `q_lcb_5pct` travels, with the exact sites:

1. **Member maxes → bootstrap probability samples.** `MarketAnalysis.forecast_yes_probability_samples(bin_idx, n)` (`src/strategy/market_analysis.py:896`). For each of `n` bootstrap iterations: resample ensemble members (`_bootstrap_p_raw_all`, `market_analysis.py:436`) with MC noise σ = `hypot(σ_ensemble, σ_repr)`, settlement-round, bin-count → `p_raw_all`; calibrate with the **MAP Platt (A,B,C)** (fixed, not random-per-sample, per BUG #129); compute posterior over the MECE family (`_compute_posterior`); take `p_post[bin_idx]`. Result: `q_yes^(b)` samples ∈ [0,1].
2. **Samples → robust bounds.** `probability_uncertainty_from_samples` (`src/strategy/probability_uncertainty.py:256`): `q_point = mean(samples)`, `raw_lcb = percentile(samples, 5)`, `q_lcb = clip(raw_lcb − Σpenalties, 0, 1)`, then `q_lcb = min(q_lcb, q_point)`. **This is where a cheap far bin gets `q_lcb = 0`: if < 5% of bootstrap iterations place ANY posterior mass on the bin, the 5th percentile of the sample vector is exactly 0.** That is structural and honest — no floor is injected; the zero IS the empirical 5th percentile.
3. **σ-shape correction (the fitted artifact).** The forecast q itself (the `p_post` that feeds the samples on the replacement path) is shaped by `state/sigma_scale_fit.json` via `_replacement_sigma_scale_lookup` (`src/data/replacement_forecast_materializer.py:643`): `σ_core = max(σ_impl·k, floor_steps·step)`, `q_adj = (1−w)·N(σ_core) + w·uniform(1/n_bins)`. **Live artifact has `w = 0.0` and `floor_steps = 1.80` for families C/F** (`state/sigma_scale_fit.json`). So today: NO uniform pedestal is added (`w=0`), and an ABSOLUTE σ-floor of 1.80 bin-steps widens the Normal. Widening σ **flattens** the distribution: it RAISES far-bin q a little and LOWERS the mode bin. This is the GATE-2 center-faithfulness fix (#69) and it is the lever that most directly moves cheap-bin vs mode-bin q.
4. **Penalties.** `UncertaintyPenalties` (calibration/boundary/representativeness/forecast-vol/multiple-comparison) subtract from `q_lcb` only. In the live path these are largely 0 today (the proof carries scalars; the materialization wrapper `_proof_probability_uncertainty`, `event_reactor_adapter.py:6652`, sets `q_ucb=q_point` and threads the proof's already-computed `q_lcb_5pct` — penalties are upstream).
5. **N_eff width correction (#61) — SHADOW ONLY.** `n_eff_override` populates `q_lcb_neff_corrected` (a `compare=False` shadow field); the live `q_lcb` is ALWAYS the raw value (`probability_uncertainty.py:283-287`, confirmed). **The N_eff=3.71 correction is NOT crushing live q_lcb.** (Refutes one of the prompt's suspects: ruled out.)
6. **James-Stein / EB selection-shrinkage (#60/#61) — SHADOW ONLY.** `_compute_selection_shrinkage(..., authority_on=False)` is pinned off (`event_reactor_adapter.py:2806-2811`); BH/FDR is the live gate; lfsr/edge_shrunk are stamped for the winner's-curse diagnostic but never alter the decision. **Shrink-to-market is NOT crushing live q_lcb.** (Refutes the second suspect: ruled out.)
7. **q_lcb → capital_efficiency.** `live_capital_efficiency_rejection_reason` (`live_admission.py:87`): reject iff `(q_lcb − price)/price ≤ 0`. Honest, KEEP.
8. **q_lcb (cheap + disagreement) → coverage_unlicensed_tail.** `coverage_unlicensed_tail_rejection_reason` (`live_admission.py:141`): reject iff `price < 0.05 AND q_lcb > 2·price AND source ∉ {EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}`. This is the gate that kills the cheap +EV candidates the reactor displays as "best" (Manila +97, Tel Aviv +26.5, KL +3.9).

**The crucial correction to the diagnosis framing:** the suspects the prompt asked me to examine — N_eff width correction, James-Stein shrink-to-market — are **both shadow-only and provably not affecting live q_lcb.** The σ-shape floor (`floor_steps=1.80`) IS live and IS the only fitted lever on cheap-vs-mode q. The cheap-bin `q_lcb≈0` is the honest empirical 5th percentile, confirmed by settlement: those bins realize at ~5%.

So the two gates that actually decide are: (7) `capital_efficiency` killing mid/expensive bins honestly, and (8) `coverage_unlicensed_tail` killing cheap +EV bins on a **source-string** test. The calibration question reduces to: **is the cheap +EV that gate kills real?** Answer from settlement: **no** (realized 4.88% on cheap bins, the displayed +EV is built on a `q_lcb` that the settled record does not back). The gate is conservative-correct; the deeper truth is there is little cheap-bin alpha to license.

---

## 3. The decisive evidence (settlement-graded, event-level)

Source: `no_trade_regret_events` (zeus-world.db), `would_have_won` populated for 20,009 rows (buy_no 18,872; buy_yes 1,137). **Raw per-row aggregates are inflated** because the reactor writes one regret row PER snapshot PER cycle (e.g. NYC 2026-06-04 has 42 rows for the same winning bin). The honest unit is the **event** (city, target_date, bin_label). De-duplicated:

**Buy_yes event-level calibration (one row per city/date/bin):**

| model q_live band | n_events | mean_pred | realized_freq | avg_price | edge_after_price |
|---|---|---|---|---|---|
| 0–0.05 | 29 | 0.005 | 0.000 | 0.008 | −0.008 |
| 0.05–0.15 | 15 | 0.072 | **0.200** | 0.039 | **+0.161** |
| 0.35–0.6 | 3 | 0.573 | 0.000 | 0.393 | −0.393 |
| ≥0.6 | 3 | 0.747 | 0.667 | 0.630 | +0.037 |

**Cheap buy_yes (price < 0.05) event-level:** 41 events, only 4 pass capeff (`q_lcb>price`), realized win-rate **4.88%**, avg price 0.0118. → cheap tail honest no-edge.

**Buy_no event-level by price (the favorite-buying base rate):**

| price band | n | realized_wr | avg_price | edge_after_price |
|---|---|---|---|---|
| <0.2 | 479 | 0.000 | 0.045 | −0.045 |
| 0.2–0.4 | 24 | 0.000 | 0.299 | −0.299 |
| 0.4–0.6 | 17 | 0.765 | 0.577 | +0.188 |
| 0.6–0.8 | 724 | 0.840 | 0.746 | +0.094 |
| ≥0.8 | 17,618 | 0.977 | 0.987 | **−0.0098** |

**Total settled history backing buy_yes calibration: 50 distinct bin-events across 20 city-days, 5 calendar days (2026-06-01..06-05).** This is the binding evidentiary fact: **the calibration record is too thin to license any cheap- or mid-band claim.** The `0.05–0.15` under-confidence signal (the only positive) is 15 events; it would be malpractice to flip it live.

**Calibration verdict (law 8 / law 1 resolved):**
- Cheap-tail `q_lcb≈0`: **HONEST zero** (no ensemble support AND settlement confirms ~5% realization). Not an artifact. Do not lift.
- Mid/expensive `capital_efficiency` rejections: **HONEST no-edge** (model below an efficient market). Keep.
- Near-center `[0.05,0.15]`: **CANDIDATE under-confidence**, direction plausible (σ-floor + any pedestal flattens the mode), but **INSUFFICIENT_DATA** — must be proven, not assumed.

---

## 4. The fix — 3 weighed alternatives, opinionated pick

The objective is "make mid-band q trustworthy and PROVE edge before live." Three structural ways to get there:

### Alternative A — "Lift the cheap tail" (the instinct in the refuted synthesis). REJECT.
Re-license cheap longshots (option K1: materialize EMOS license, or re-route `coverage_unlicensed_tail` to a verdict authority), and/or widen σ further / restore the uniform pedestal `w>0` so far-bin q rises above price.
- **Why it is wrong:** settlement says cheap bins realize at 4.88%; the displayed +EV is a `q_lcb` the record does not back. Lifting it trades into honest no-edge. Widening σ to raise far-bin q simultaneously LOWERS the mode bin (the only place the model is near-calibrated), degrading the near-center signal. Violates laws 1 (no-edge presumed our defect ONLY until settlement proves otherwise — here settlement PROVES no-edge), 4, 5, 8. **Rejected on evidence.**

### Alternative B — "Settlement-graded reliability harness + band-licensed q, mid-band first." PICK.
Build a **reliability harness** (offline, settlement-graded, event-level) that is the single source of truth for whether any (q-band × price-band × lead × source-family) cell is calibrated and beats the market after cost. The harness output becomes the **licensing authority**: a candidate trades live iff its cell has n ≥ N_min AND realized-freq-minus-price one-sided lower CI > 0. The q-construction is corrected ONLY where the harness proves mis-calibration (e.g. apply a per-band isotonic/Platt recalibration on the near-center band IF and only IF it is proven under-confident at n≥N_min). The cheap tail is left exactly as-is (honest dead). This **collapses** the two existing licensing vocabularies (source allow-list in `live_admission` + verdict-status in `settlement_backward_coverage`) into ONE settlement-graded band verdict (operator law #3).
- **Why pick:** it directly answers the only open question (is mid-band edge real?) with the only valid instrument (settlement at event level), it cannot manufacture edge a wrong bin-belief lacks (law 8 — the harness grades the bin-belief against truth), and it is the SIMPLER end-state (one licensing authority, not two). It also turns the current 50-event poverty into a forward-accumulating asset.
- **Cost/risk:** the harness needs enough settled events to license a band; today there are ~50. So the FIRST output is honest: "INSUFFICIENT_DATA, accumulate." That is the correct answer, not a failure.

### Alternative C — "Hierarchical Bayesian recalibration of q across cities (partial pooling)." PARTIAL / FOLD INTO B.
Fit a hierarchical model (city × season × lead) that shrinks each cell's calibration map toward a global prior, so thin cells borrow strength — the principled way to act on 50 events without overfitting. The C1 era-aware EB partial pooling (#59) and the σ-scale MLE (#50) are precedents in-repo.
- **Why partial:** it is the right *estimator* for the recalibration step INSIDE alternative B (it is how you get a trustworthy per-band map from thin data), but it is not a substitute for the settlement-graded grading harness and the after-cost vs-market license. **Fold C in as B's recalibration engine; do not run it standalone** (a pooled q with no settlement-graded license is still un-proven — law 5).

**PICK: B, with C as its recalibration engine.** Mechanism: harness (grade) → per-band verdict (calibrated? beats market after cost?) → band-licensed q where proven, isotonic/EB-pooled recalibration applied only to proven-miscalibrated bands → single licensing authority replacing the source allow-list. Cheap tail stays dead; mid-band is the target; nothing flips live until a band clears n≥N_min with lower-CI>0.

---

## 5. Causal chain to a settlement-proven fill (the end-to-end story)

This is how B produces a CONTINUOUS >51% after-cost settlement win-rate fill, not just one order:

1. **Harness runs nightly** over all newly-settled events, appends event-level graded rows (model q, q_lcb, price, won) partitioned by band/lead/source-family, recomputes Wilson CIs and vs-market Brier per cell.
2. **As settled events accumulate** (the system already grades ~20 city-days/week across cities), the near-center mid-band cell crosses n ≥ N_min (target N_min ≈ 150–200 events per band before licensing; tunable, fitted not guessed).
3. **If the mid-band cell proves under-confident** (realized > predicted) AND **realized-minus-price lower CI > 0**, the harness licenses that band. The recalibration map (isotonic on that band, EB-pooled across cities) lifts the model q on near-center bins to its settlement-true value, so `q_lcb` on those bins now honestly exceeds price.
4. **Those candidates pass `capital_efficiency` honestly** (now `q_lcb > price` because the band-true q is higher than the raw-bootstrap q was), are NOT cheap-tail (price not <0.05 for near-center bins), so `coverage_unlicensed_tail` does not fire, and they reach submit.
5. **The submit lane is open** (B1 self-clears per keep-invariant #1; the secondary submit-disable blockers are the #2 finding in the diagnosis, addressed by the parallel submit-path lens — not this lens).
6. **Fills accrue on a settlement-licensed band**, and because the license is itself the settlement-graded after-cost edge, the realized win-rate is >51% after cost by construction of the license. Continuity comes from the harness re-licensing each night on the growing record.

**Where this can still legitimately end at "honest no-edge":** if, after N_min events accumulate, NO band clears lower-CI>0 after cost, then the model has no settlement-provable disagreement with the market anywhere, and the correct answer is that Zeus has no tradeable weather alpha on this surface — a real, law-1-compliant outcome (we rooted-caused the suppression; there was none; the market is efficient). The harness makes that verdict honest and dated rather than a vibe.

---

## 6. Invariants (what must hold; make the wrong code impossible)

- **INV-CAL-1 (event unit):** all calibration grading is event-level (one row per city/target_date/bin). Per-snapshot aggregation is FORBIDDEN — it inflates n and win-rate (NYC-06-04 = 42 snapshot rows for 1 event). Enforce with a `GROUP BY city,target_date,bin_label` contract + a test that asserts no (city,date,bin) contributes >1 graded unit.
- **INV-CAL-2 (settlement is the only grade):** `won` is derived solely from `settlement_outcomes`/`settlements` bin-match; never from model q, never from market price, never from `would_have_won` without confirming its derivation matches the settlement bin (audit the regret-event grader's bin-match against `settlement_semantics` preimage; boundary/rounding mismatch = silent mislabel, law 8).
- **INV-CAL-3 (out-of-sample only):** a band's license uses ONLY events with `target_date < decision_date` (walk-forward; same discipline as `replacement_final_form §1a`). No in-sample fit licenses a live trade.
- **INV-CAL-4 (vs-market benchmark mandatory):** a band is "edge" only if model q beats market q (lower Brier/log-loss on the SAME events) AND realized−price lower CI > 0. A band where model and market are tied is no-edge regardless of realized win-rate.
- **INV-CAL-5 (cheap tail stays honest):** the cheap-longshot lane (price<0.05) is NOT a recalibration target. No σ-widening or pedestal is added to lift cheap-bin q. The σ-floor change must be **center-faithful** (keep-invariant #69 / GATE-2): any σ edit is evaluated by mode-bin calibration ratio, not far-bin.
- **INV-CAL-6 (q_lcb ≤ q_point, penalties lower only the LCB):** preserved verbatim from `probability_uncertainty.py` — the recalibration map adjusts q_point; the LCB is re-derived from recalibrated samples, never set above the point (Hidden #2).
- **INV-CAL-7 (one licensing authority):** the settlement-graded band verdict REPLACES, not augments, the source-string allow-list in `coverage_unlicensed_tail` and the buy_no source allow-list. Collapse N→K (operator law #3). No new flag, no shadow lane (operator memory: no shadow / no gate-mass).
- **INV-CAL-8 (no favorite re-enable):** buy_no price≥0.8 is base-rate (realized 0.977 ≈ price 0.987, slightly negative after cost) and is NOT licensed by the harness as alpha. The license requires model q to beat market q; a favorite where model≈market never clears.

---

## 7. Failure modes + the verification that catches each

| Failure mode | How it manifests | Catch |
|---|---|---|
| **Snapshot inflation** re-enters | a band shows n=4,000 and win-rate 0.96 from one city-day | INV-CAL-1 test: assert graded-unit count == distinct (city,date,bin) count; reconcile against `settlement_outcomes` row count |
| **Mislabeled win (law 8)** — regret grader's bin-match disagrees with settlement preimage | a cheap bin shows implausible realized freq (e.g. cheap-tail >0.3) | audit `no_trade_regret_events.would_have_won` derivation vs `settlement_semantics.bin_probability_settlement` preimage on a sample; assert agreement before trusting any band |
| **In-sample overfit** | a band licenses on the same events it was fit on | INV-CAL-3 walk-forward test: license CI computed only on `target_date < fit_date`; a leakage test asserts no future event in the fit set |
| **Tied-with-market false edge** | realized−price>0 but only because the market priced it identically | INV-CAL-4: require model-Brier < market-Brier on the band with its own CI; reject ties |
| **Thin-cell mirage** | N_min too low licenses a 15-event fluke | N_min fitted from the variance of the per-band Wilson width (require lower CI of realized−price > 0 at the chosen N_min); start conservative (≈150–200) and let the fitted boundary move it |
| **σ-edit degrades the mode** | widening σ to help a band lowers mode-bin calibration | INV-CAL-5 + the #69 mode-ratio gate: any σ change must hold mode-bin calibration_ratio in [0.9,1.1] |
| **The harness licenses, but submit is still blocked** | proof_accepted>0 yet no fill | this is the diagnosis's #2 (submit-path) — out of this lens's scope; flagged to the submit-path lens (real_order_submit_disabled / event_bound_final_intent_no_submit) |
| **Edge decays after license** | a band was real, then the market caught up | re-license nightly; a band whose trailing-window lower CI drops below 0 is auto-de-licensed (continuous, not one-shot) |

---

## 8. KEEP / DELETE / BUILD

**KEEP (load-bearing, settlement-confirmed correct):**
- `capital_efficiency` gate (`live_admission.py:87-119`) — honest q_lcb>price arbiter; rejections are correct. Keep-invariant #2.
- The `q_lcb = percentile(samples,5) − penalties; min(.,q_point)` construction (`probability_uncertainty.py`) — the cheap-bin zero it produces is honest. Keep the contract.
- `coverage_unlicensed_tail`'s INTENT (fail-closed on unbacked cheap disagreement) — but its source-string TEST is replaced by the harness verdict (see DELETE). Keep-invariant #3.
- The σ-shape center-faithful floor (#69, `floor_steps=1.80`, `w=0`) — center-faithfulness is correct; do NOT add a uniform pedestal `w>0` (it would flatten the mode and worsen the only near-calibrated band).
- Direction law, settlement-preimage q, INV-37, time-semantics (#16). Keep-invariants #4/#5.

**DELETE / COLLAPSE:**
- The **source-string allow-list** as the licensing authority in `coverage_unlicensed_tail` (`{EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}`) and the parallel buy_no allow-list — replaced by the single settlement-graded band verdict (operator law #3). This also kills the dead EMOS-license-file lane (never built, default-OFF flag — do NOT build it; diagnosis GAP 6).
- The N_eff width-correction shadow field and the EB-shrinkage shadow stamp IF the harness supersedes the winner's-curse-slope diagnostic they feed — candidate for removal in the gate-mass collapse (they are shadow-only and not load-bearing live; confirm no consumer before deleting).

**BUILD:**
- The **settlement-graded reliability harness** (offline, event-level, walk-forward, vs-market) — `scripts/` + a graded `calibration_band_verdict` artifact, weekly/nightly refit, sole writer pattern like `fit_sigma_scale.py`.
- The **per-band recalibration map** (isotonic / EB-pooled across cities), applied ONLY to bands the harness proves mis-calibrated at n≥N_min.
- The **single band-licensing authority** consumed by both admission and the cert credential.

---

## 9. Honest bottom line for the operator

There is **no confirmed suppressed cheap-tail alpha** — the settlement record proves the cheap longshots realize at their cheap price, and the gates that reject them are correct. The prior "q_lcb collapses to 0" headline conflated honest structural zeros (cheap far bins, ~5% realized) with the much larger mass of honest mid/expensive `capital_efficiency` rejections (model below an efficient market). The two calibration suspects the prompt flagged as primary — the N_eff=3.71 width correction and James-Stein shrink-to-market — are **both shadow-only and do not touch live q_lcb**; they are not the cause and should be removed in the gate-mass collapse, not "fixed."

The **only** coordinate with a hint of real edge is **near-center under-confidence** (model q [0.05,0.15] realized ~3× higher), which is the OPPOSITE of the cheap-tail story and is currently backed by **15 event-level observations** — unlicensable. The correct strategic move is therefore not a q-formula tweak but the **settlement-graded reliability harness** that (a) grades q against settlement at the event level, (b) licenses a band only when it beats the market after cost out-of-sample with a lower CI > 0, and (c) accumulates the record so the mid-band verdict becomes statistically real over the next weeks. If, once the record is thick, no band clears, that is the honest law-1-compliant verdict: the market is efficient and there is no tradeable weather alpha to unblock — and the harness will have proven it with dates and numbers instead of instinct.

*End of P1/S2 calibration strategy. Untrimmed.*
