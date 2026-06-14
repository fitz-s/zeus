# P1·S1 — Minimal-Correct-Change Strategy to Continuous Settlement-Proven Correct-Bin Alpha

**Author lens:** MINIMAL-CORRECT-CHANGE strategist (fresh — prior solution-framing discarded).
**Date:** 2026-06-14. **Phase:** PLAN-MAKING ONLY (no code edits, no deploy, DBs read-only).
**Authority inputs read in full:** `diagnosis_confirmation.md` (authoritative), `synthesis.md` (central claim REFUTED; mined for keep-invariants + the 5 contradictions), `b2_capital_efficiency_audit.md`, `live_state_tracker.md`, `replacement_final_form_2026_06_09.md`, and the q-construction sources (`probability_uncertainty.py`, `selection_shrinkage.py`, `market_fusion.py`, `market_analysis.py`, `event_reactor_adapter.py` q-seam, `live_admission.py`, `settlement_backward_coverage.py`, `sigma_scale_fit.json`).

---

## 0. The new finding that re-frames everything (mine, from settlement data — not in any prior doc)

I ran the settlement-calibration backtest the diagnosis explicitly left as **the decisive open question** ("is q_lcb≈0 a CORRECT bin-belief or a BROKEN floor crushing real probability mass?"). It had never been run end-to-end against `settlement_outcomes`.

**Backtest:** `forecast_posteriors.q_json / q_lcb_json` (the model's per-bin point `q` and lower bound `q_lcb`) ⋈ `settlement_outcomes.winning_bin` (authority=VERIFIED), latest posterior per (city, target_date, metric). **n = 119 settled families, 1309 graded bins.** Bin-token matcher validated: a winning bin matched in **119/119 families (100%)**. (Script: `/tmp/bt_clean/bt3.py`; reproduce against `state/zeus-forecasts.db?mode=ro`.)

| Calibration of the POINT `q` | n_bins | mean_pred | realized | R/E |
|---|---|---|---|---|
| q∈[0.10,0.20) | 344 | 0.1458 | 0.1686 | **1.16** |
| q∈[0.20,0.40) | 167 | 0.2453 | 0.3114 | **1.27** |
| Σ(q) over all 1309 bins | — | **119.0** | winners=**119** | **1.00** |

| Calibration of the LOWER BOUND `q_lcb` | n_bins | mean_pred | realized | R/E |
|---|---|---|---|---|
| q_lcb∈[0.01,0.03) | 212 | 0.0191 | 0.0755 | **3.94** |
| q_lcb∈[0.03,0.06) | 215 | 0.0441 | 0.1349 | **3.06** |
| q_lcb∈[0.06,0.10) | 154 | 0.0770 | 0.3052 | **3.96** |
| q_lcb∈[0.10,0.20) | 61 | 0.1255 | 0.3607 | **2.87** |
| Σ(q_lcb) over all 1309 bins | — | **34.4** | winners=**119** | **0.29** |

**Brier(q_point)=0.0712 < Brier(q_lcb)=0.0802** — the lower bound is a *worse* win-predictor than the point, the textbook signature of over-conservatism.

### What this proves (the answer to the operator's decisive fork)

1. **The POINT posterior `q` is honest.** Σ(q)=119.0 ≡ 119 winners across 1309 bins; the mid bands realize at 1.16–1.27× predicted (mild under-confidence, well inside noise). Whatever else is wrong, **the model is selecting and weighting the correct bin** — the foundation (Law 8 metadata/bin-identity) is intact. This is NOT a wrong-bin or wrong-metadata defect.

2. **The far/tail bins genuinely don't win — honest no-edge is REAL there.** Tail ("or higher"/"or below") bins with q_point∈[0,0.02): n=151, realized **0.0000**. Munich-26C+, Milan-38C+, Amsterdam-23C+, Singapore-35C+, NYC-98-99F all carry q_lcb=0.0000 in the live log because the ensemble places ~zero mass there. **`capital_efficiency` rejecting them is CORRECT.** Do not touch that.

3. **The binding defect is that `q_lcb` is systematically ~3–4× too LOW on the actionable mid bins** where the correct bin lives. Σ(q_lcb)=34.4 vs 119 winners ⇒ **the lower bound discards 71% of the true, settlement-proven probability mass.** This is the "BROKEN floor crushing real probability mass" horn of the operator's fork — confirmed by settlement, not asserted. It is exactly where a cheap stake on the correct bin would pay multiples, and it is the mass that `capital_efficiency=(q_lcb−price)/price ≤ 0` then honestly rejects (honest gate, dishonest input).

**The headline is therefore corrected one level deeper than `diagnosis_confirmation.md` left it:** capital_efficiency firing on q_lcb≈0 is the *proximate* mass killer (88% of rejections), but it splits into TWO populations — (a) far/tail bins where q_lcb≈0 is the honest truth (keep rejecting), and (b) **mid bins where q_lcb is a broken under-estimate of a settlement-real win probability (the suppressed alpha).** The whole licensing/EMOS/`coverage_unlicensed_tail` story the synthesis chased is downstream noise; the disease is the **LCB estimator**.

---

## 1. Objective

Make genuine **correct-bin** alpha flow end-to-end to a continuous, settlement-proven >51%-after-cost win-rate on traded markets, by **fixing the one estimator that is provably, settlement-measurably wrong (q_lcb under-coverage) and removing the gate-mass that exists only to protect against the OPPOSITE error.** Smallest systematically-correct change set; K≪N; never a hack, never a new gate/cap.

The deliverable is not "an order fills." It is: the sizing-and-admission lower bound stops throwing away 71% of settlement-real mass, the correct mid-bins clear `capital_efficiency` honestly, and the resulting fills are graded at settlement to confirm the edge is real before scaling.

---

## 2. The mechanism (root cause, file:line)

**q_lcb is the 5th-percentile of a nonparametric bootstrap of a small-sample multinomial cell probability — and that statistic is a known, severe under-estimate of the true confidence lower bound.**

Chain, verified:

- `market_analysis.forecast_yes_probability_samples` (`market_analysis.py:896–964`): for each of `edge.n_bootstrap=500` iterations, resample the 51 ensemble members with replacement (`_bootstrap_p_raw_all`, `:445` `rng.choice(member_maxes, replace=True)`), add MC noise `mc_sigma = hypot(self._sigma, σ_repr)` (`:451`), settle, integrate to a per-bin posterior `p_post[bin_idx]` (`:957`). The result is 500 samples of the bin's probability mass.
- `probability_uncertainty_from_samples` (`probability_uncertainty.py:307`): `q_lcb = clip(percentile(samples, 5) − Σpenalties, 0, 1)`, then `min(q_lcb, q_point)`.
- Consumed verbatim as `q_lcb_5pct` on the proof, threaded to `live_capital_efficiency_rejection_reason` (`live_admission.py:113`): reject iff `(q_lcb − price)/price ≤ 0`.

**Why the statistic is biased low:** for a bin holding true mass ~0.15 from a 51-member ensemble, individual bootstrap resamples frequently land 0–3 fewer members in the bin, driving `p_post[bin]` toward 0 on a large fraction of the 500 draws. The 5th percentile of that left-skewed resample distribution sits near 0 — far below the honest lower confidence bound for a 0.15 proportion. Member resampling of a 51-point sample has resampling SD ≈ sqrt(p(1−p)/51) PLUS the MC σ in quadrature; the percentile then takes the deep left tail of a skewed small-N multinomial. Settlement confirms the magnitude: the 5th-percentile bound under-covers by 3–4× exactly in the mid bands.

**Corroborating fitted evidence:** `sigma_scale_fit.json` independently shows the q *shape* over-peaks the mode and under-weights ring bins at k=1,w=0 (dist-2 ratio 1.79, dist-3 ratio 2.78 at k1w0) — the same family of "the conservative construction is mis-shapen on the non-mode bins." That artifact addresses the POINT shape on the replacement path; it does NOT touch the bootstrap-percentile LCB on the canonical live path, which is where the 3–4× under-coverage measured above lives.

**The architectural blind spot (the reason this survived 100 patches):** every calibration authority in the stack is built to catch q_lcb being too **HIGH** and can only move it **DOWN**:
- `settlement_backward_coverage_check` (`settlement_backward_coverage.py:128`): `realized < claimed − tol` → **shrink**; else unchanged. One-sided downward. It *computes* `_isotonic_realized_rate` (`:97`) — the exact realized win-rate that proves q_lcb should be ~3× higher — and then **refuses to raise it.**
- `market_anchor_no_lcb` (`event_reactor_adapter.py:7472`, default OFF): "only ever LOWERS q_lcb."
- C3 N_eff width correction (`probability_uncertainty.py:328`): widens the interval → **lowers** q_lcb.
- penalties (`UncertaintyPenalties`): "lower ONLY the lower bound."

There is **no authority anywhere that can correct a too-low q_lcb.** The system is exhaustively defended against over-claiming and structurally blind to under-claiming — while settlement says under-claiming is the dominant, alpha-killing error. That asymmetry is the disease.

---

## 3. The fix — 4 minimal-correct changes (K=4), weighed alternatives + pick

### K1 — Replace the bootstrap-percentile q_lcb with a settlement-calibrated lower bound (THE load-bearing change)

**What:** Stop reading q_lcb as the raw 5th-percentile of the bootstrap. Make q_lcb a **settlement-coverage-calibrated** lower bound: bidirectional isotonic map from the *claimed-band* to the *realized win-rate*, the SAME `_isotonic_realized_rate` already in `settlement_backward_coverage.py`, but allowed to move q_lcb in BOTH directions (up where the settled record proves the bound is too conservative, down where it proves it too aggressive). The point `q` stays the honest model posterior (Σq=winners — do not touch it).

**Alternatives weighed:**

- **(a) Analytic LCB instead of bootstrap percentile (Jeffreys/Wilson lower bound on the bin proportion).** Replace `percentile(samples,5)` with a Beta/Wilson lower bound at the bin's effective N. *Pro:* removes the small-N percentile bias at the source, no settlement dependency, cheap. *Con:* still a *forecast-side* prior bound — it corrects the statistical artifact but not any genuine forecast-vs-settlement miscalibration, and it would need its own validation against settlement to prove it lands at R/E≈1. It fixes the symptom's mechanism but doesn't *measure* truth.
- **(b) Settlement-calibrated bidirectional LCB (reuse `_isotonic_realized_rate`).** Calibrate the claimed-LCB→realized map on the settled record per cohort; license the calibrated value as q_lcb. *Pro:* this is the ONLY change that pins q_lcb to **settlement truth** (Law 5), it REUSES an existing, audited function (`_isotonic_realized_rate`, `settlement_backward_coverage.py:97`) — collapses two LCB vocabularies into one authority (K≪N), and it is self-correcting as more settlements arrive. *Con:* needs ≥ min_n settled obs per cohort; thin cohorts fall back.
- **(c) Just lower DEFAULT_ALPHA (use the 10th/15th percentile).** *Pro:* one-line. *Con:* a blind, un-fitted constant nudge — exactly the "unsupported hardcoded value" the project bans; it would over-correct calibrated cohorts and under-correct broken ones. Rejected outright.

**PICK: (b) as the authority, with (a) as the fail-open fallback for INSUFFICIENT_DATA cohorts.** Settlement is the only truth (Law 5), so the licensed q_lcb must be settlement-anchored where data exists; where it doesn't, the analytic Jeffreys bound (a) is a strictly better cold-start than the biased bootstrap percentile (it has no small-N left-tail collapse). This is one unified LCB authority replacing: the raw bootstrap percentile + the one-directional shrink-only coverage check + the dead EMOS/SETTLEMENT_ISOTONIC source-string licensing. **Three licensing/LCB vocabularies → one.**

**Exact change surface:**
- Generalize `settlement_backward_coverage_check` (`settlement_backward_coverage.py:128–201`) to return `q_lcb_out = calibrated_realized − honesty_margin` in BOTH directions when n≥min_n (currently it returns `claimed` unchanged when realized ≥ claimed; change to return the isotonic-calibrated value with the 1pp margin regardless of sign). Keep INSUFFICIENT_DATA → fallback.
- Add the analytic Jeffreys lower bound as the INSUFFICIENT_DATA fallback inside the q-construction seam (`event_reactor_adapter.py:10322–10340`, where `_side_q_lcb_from_yes_samples` feeds `_set_qlcb_provenance(..., source="FORECAST_BOOTSTRAP")`). The new provenance becomes `SETTLEMENT_CALIBRATED` (data exists) or `JEFFREYS_PRIOR_LCB` (cold start), and BOTH are settlement-licensed sources.
- `apply_settlement_coverage` (`:204`) loses its `enabled`-shadow + downward-only restriction: the calibrated value IS the live q_lcb.

**What K1 deliberately does NOT touch:** the point posterior `q` (honest, Σq=winners — untouched); `bin_probability_settlement` and the preimage/time-semantics spine (correct); the member-resampling sampler itself (it feeds the point fine; only the *lower-tail read* of it changes); direction law; the σ-shape fit on the replacement path.

### K2 — Delete `coverage_unlicensed_tail` and the EMOS/source-string licensing lane (gate-mass collapse)

**What:** Remove `coverage_unlicensed_tail_rejection_reason` (`live_admission.py:141–180`), `COVERAGE_LICENSED_LCB_SOURCES`, and the `LIVE_BUY_NO_MATERIAL_ALLOWED_LCB_SOURCES` source-string allow-list. Once q_lcb is settlement-calibrated (K1), the SOURCE-STRING licensing is redundant — the q_lcb itself is now settlement-backed by construction, so a separate "is the source EMOS/ISOTONIC?" test is pure gate-accretion.

**Alternatives weighed:**
- **(a) Re-route `coverage_unlicensed_tail` to the settlement verdict (the synthesis's K1b).** *Con:* the synthesis itself is refuted — `coverage_unlicensed_tail` fires on 0.6% of rejections and 0 receipts; re-routing a 0.6% tail gate is motion, not progress, and it KEEPS a gate the operator law says to collapse.
- **(b) Delete it.** *Pro:* the gate's entire INTENT ("don't trade an unbacked tail q_lcb") is SUBSUMED by K1 — a settlement-calibrated q_lcb on an unbacked tail bin calibrates to ~0 (the tail bins realize 0.0000, §0) and `capital_efficiency` rejects it honestly. The Milan-24C incident it was built for (`b2` ref) is exactly a case where the settlement-calibrated LCB would be ~0 and the honest EV gate handles it — no separate antibody needed.

**PICK: (b) delete.** The fail-closed tail discipline survives as an EMERGENT property of K1 (unbacked tail → calibrated q_lcb≈0 → capital_efficiency rejects), not as a standalone gate. This honors keep-invariant #3's *intent* (don't trade unbacked tails) while obeying the collapse-N-to-K law. Net gate count DROPS.

**Does NOT touch:** `capital_efficiency` (the honest final arbiter — KEEP, keep-invariant #2), direction law, the buy_no native-NO authority.

### K3 — Adjudicate buy_no cheap-tail OUT OF SCOPE explicitly (kill the base-rate mirage)

**What:** The settlement backtest (§0) + Contradiction 5 + operator Law 4 all agree: the only "edge" in buy_no is base-rate favorite-buying (cost>0.6, ~90% in the price). Route `buy_no AND price<0.05` to an explicit `DIRECTION_SCOPE_BUY_NO_TAIL_OUT_OF_SCOPE` reason; remove the `emos_q_lcb_no=0.0` stub (`event_reactor_adapter.py ~12064`) that masquerades as a lane.

**Alternatives weighed:**
- **(a) Build a real NO-side settlement-calibrated LCB.** *Pro:* symmetric with K1. *Con:* §0 shows no tradeable cheap-NO alpha exists; building a calibrated NO-LCB to harvest a non-existent edge is effort against Law 4. The native-NO authority (1−q_ucb_yes) already exists for the cases that DO matter (mid/favorite NO).
- **(b) Declare cheap buy_no out-of-scope, keep the existing native-NO authority for non-cheap NO.** *Pro:* simplest, honest, matches the evidence. SIMPLIFY default.

**PICK: (b).** Cheap buy_no longshots are out of scope by settlement evidence; the mid/favorite native-NO lane (already correct) is untouched.

**Does NOT touch:** the native-NO `1−q_ucb_yes` authority for executable NO, the profitable-era NO eligibility gate (#74, prevents NO-on-winning-ring loss).

### K4 — Make the cycle-summary attribute the gate that actually killed the best candidate (observability, zero runtime risk, FIRST)

**What:** In the reactor cycle-summary builder (`event_reactor_adapter.py:7149–7206`), compute and print `best=... rejected_by=<the actual gate that killed the displayed best>` instead of letting the `best=` (max-display-EV) float free of the bucket label. (This is the synthesis's only surviving rank-1 item — adopt it, because until the log stops conflating display-EV with the kill-gate, K1's effect cannot be trusted.)

**PICK:** adopt as-is; it is pre-work, not a fix. Ship FIRST.

---

## 4. Migration order + the causal chain to a settlement-proven fill

**Order:** K4 (observability, zero risk) → K1 (the estimator fix, behind the existing coverage authority, re-materialize q_lcb) → K2 (delete now-redundant gate-mass) → K3 (scope decision). No table renames, no new flags (go-live-direct per operator law; the calibrated q_lcb IS the live value, not a shadow).

**Causal chain, each link verifiable:**

1. K1 lands → q_lcb on mid bins rises from ~0.044 to the settlement-calibrated ~0.13–0.17 (the realized rates in §0). **Verify:** re-run the §0 backtest on the new q_lcb_json; require Σ(q_lcb_new) → ~119 (R/E→1.0 per band), Brier(q_lcb_new) ≤ Brier(q_point).
2. The correct mid-bin candidates (Manila-35C q_lcb 0.196, KL-34C 0.098, Singapore-33C 0.098, Qingdao-28C 0.063 — all already +EV on the DISPLAY q_lcb in the live log) now pass `capital_efficiency` with a *settlement-backed* q_lcb, not a raw bootstrap one. **Verify:** live cycle log shows `proof_accepted>0` with `q_lcb_calibration_source=SETTLEMENT_CALIBRATED`.
3. K2 means no `coverage_unlicensed_tail` re-blocks them; K3 means buy_no tails don't pollute the buckets. The admitted candidate reaches submit (B1 latch is OPEN, self-cleared 06-14T01:06, keep-invariant #1). **Verify:** first new `decision_certificate` + `venue_command` since 06-12.
4. The fill settles. **Verify (the only DONE criterion):** grade the fill at settlement; require continuous >51%-after-cost win-rate on the traded mid-bin class over a rolling window — and crucially, that the realized rate on TRADED bins matches the calibrated q_lcb (the calibration must hold out-of-sample, not just in the fit). If realized < calibrated on traded bins, K1's calibration is itself over-claiming → the settlement-coverage downward arm (preserved) shrinks it next cycle. **The fix is self-correcting in BOTH directions** — that bidirectionality is the whole point.

---

## 5. Invariants (preserve — from synthesis keep-list, re-validated against my finding)

- **INV-A (point posterior is truth):** Σ(q)=119≡winners. K1 touches ONLY the lower bound; the point `q` is never altered. Any change that moves the point is out of scope.
- **INV-B (capital_efficiency is the honest arbiter):** `(q_lcb−price)/price≤0` reject stays. We fix its *input*, never loosen the *gate*. Keep-invariant #2.
- **INV-C (far/tail honest-no-edge):** tail bins realize 0.0000 (§0); their calibrated q_lcb must stay ≈0. The calibration must not manufacture mass on bins the settled record gives zero. Isotonic-on-realized guarantees this (realized=0 → calibrated≈0).
- **INV-D (settlement is the only truth):** the new q_lcb is anchored to `_isotonic_realized_rate` over VERIFIED settlements only. No in-sample promotion; the calibration cohort excludes the target date (walk-forward, as the replacement-form §4 already mandates).
- **INV-E (no new gate/cap/flag; collapse only):** K2/K3 DELETE gates; K1 UNIFIES three LCB vocabularies into one. Net gate count strictly decreases.
- **INV-F (INV-37 cross-DB, K1 DB split):** calibration reads settlements from `zeus-forecasts.db`, writes nothing cross-DB at decision time; preserved.
- **INV-G (direction law, preimage/time-semantics spine):** untouched.
- **INV-H (CI-honesty, HIGH-only EMOS):** K2 deletes the EMOS source-string lane entirely, so the "EMOS HIGH-only / k_cov never tightens" constraint becomes moot rather than violated.

---

## 6. Failure modes + the verification that catches each

| # | Failure mode | Catch |
|---|---|---|
| F1 | Calibration over-fits thin cohorts → q_lcb manufactured too HIGH on a cohort with few settlements → over-trading a non-edge. | min_n gate (≥30) keeps thin cohorts on the Jeffreys fallback; the **downward** arm of settlement-coverage (preserved) shrinks any cohort whose TRADED realized < calibrated next cycle. Bidirectional = self-healing. |
| F2 | Isotonic map leaks look-ahead (target date in the fit cohort). | Walk-forward cohort construction (target_date < decision_date), asserted in the calibration builder; property test mirroring `test_replacement_download_cycle_currency_gate` style. |
| F3 | Re-materialization changes selection ORDER and silently re-ranks (regression). | Re-run §0 backtest pre/post; require point-`q` ranking byte-identical (point is untouched) and q_lcb monotone-improved (Σ→119). |
| F4 | The mid-bin "edge" is an artifact of the 119-family sample (small N). | n=119 families / 1309 bins is the FIT; the DONE gate is FORWARD settled win-rate on TRADED bins, not the in-sample R/E. Treat §0 as the hypothesis, settlement of real fills as the proof (Law 5). Start sizing at the $5–15 envelope (#18) until forward settlement confirms. |
| F5 | K2 deletion re-opens the Milan-24C unbacked-tail loss class. | INV-C: unbacked tail → isotonic realized ≈0 → calibrated q_lcb≈0 → capital_efficiency rejects. Add the Milan-24C case as a regression test against the NEW path (calibrated q_lcb≈0, not the deleted gate). |
| F6 | buy_no out-of-scope (K3) accidentally drops a real favorite-NO trade. | K3 scopes ONLY `price<0.05` buy_no; the native-NO authority for executable mid/favorite NO is untouched. Test: favorite-NO (price>0.6) still scores. |
| F7 | Calibrated q_lcb still under-covers (the isotonic also too conservative). | The §0-style backtest on the NEW q_lcb is the acceptance gate: R/E must land in [0.9, 1.2] per band, else iterate the honesty-margin. |

---

## 7. KEEP / DELETE ledger

**KEEP (load-bearing, settlement-validated or structurally correct):**
- The POINT posterior `q` chain end-to-end (member resample → MAP-Platt → `compute_posterior` MODEL_ONLY → `bin_probability_settlement`). Σq=winners. **The crown jewel — do not touch.**
- `capital_efficiency` gate (honest arbiter; fix input only).
- `settlement_backward_coverage._isotonic_realized_rate` (REUSE as the new LCB authority — it already computes exactly the right quantity).
- direction law, preimage/time-semantics (#16), the external-close absorber/reconcile (#31, self-heals B1), INV-37, K1 DB split.
- the native-NO `1−q_ucb_yes` authority for executable NO; profitable-era NO gate (#74).
- B1 self-clearing latch path (keep-invariant #1).

**DELETE (gate-mass / dead lanes / one-directional defenses that block the fix):**
- `coverage_unlicensed_tail_rejection_reason` + `COVERAGE_LICENSED_LCB_SOURCES` (`live_admission.py:134–180`) — subsumed by calibrated q_lcb (K2).
- the EMOS source-string licensing lane + `emos_ci_license.json` arming path + `edli_emos_ci_live_enabled` flag (never built, never fired, gate-mass per memory law) — the calibrated q_lcb makes "is the source licensed?" moot (K2).
- `emos_q_lcb_no=0.0` stub + cheap buy_no lane (`event_reactor_adapter.py ~12064`) — replaced by explicit out-of-scope reason (K3).
- the `enabled`-shadow + downward-only restriction in `apply_settlement_coverage` — the calibration is live and bidirectional (K1).
- the C3 N_eff width correction's role as a q_lcb *lowerer* on the canonical path becomes redundant once q_lcb is settlement-calibrated; demote to diagnostics (review under K1, do not pre-delete).

**RECONSIDER (not this slice):** the bootstrap-percentile machinery in `forecast_yes_probability_samples` remains the cheap fallback signal but is no longer the LIVE q_lcb authority; keep it producing samples (the FDR edge engine still consumes them) but stop reading its 5th-percentile as the sizing/admission bound.

---

## 8. Why this is the minimal correct change

Three to five changes, all systematically correct, none a hack:
- **K1 is the ONE causal fix:** it corrects the single estimator that settlement proves wrong (q_lcb under-coverage 3–4×), by REUSING an existing settlement authority, anchored to the only truth (Law 5). It does not invent a source, a cap, or a flag.
- **K2/K3 are pure subtraction:** they delete gate-mass whose intent K1 now satisfies emergently (collapse N→K, Law 3).
- **K4 is observability pre-work** so K1's effect is trustworthy.

It deliberately does NOT: rebuild the forecast spine (the point is honest), re-enable favorite-buying as "alpha" (K3 forbids it), build the EMOS license (red herring), or loosen `capital_efficiency` (we fix its input). The correct bin is already being selected; we stop the lower bound from lying about how often that correct bin wins.

---

## Appendix — reproduction

Backtest script `/tmp/bt_clean/bt3.py` against `state/zeus-forecasts.db?mode=ro`: join `forecast_posteriors` ⋈ `settlement_outcomes` (authority=VERIFIED), latest posterior per family, match `winning_bin` token against parsed q_json keys, bucket by q_point and q_lcb, report realized win-freq + R/E + Brier. n=119 families, 1309 bins, 119/119 win-bin match. Key outputs: Σ(q_point)=119.0, Σ(q_lcb)=34.4, winners=119; q_lcb R/E ∈ [2.87, 3.96] across mid bands; tail bins realized 0.0000.
