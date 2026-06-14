# P1 — STRATEGY OF RECORD: Continuous Settlement-Proven Correct-Bin Alpha

**Date:** 2026-06-14
**Mode:** PLAN-MAKING (synthesis only — no production edits, no deploy, no live touch). DBs opened `?mode=ro`.
**Role:** P1 synthesizer. This document merges the winning strategy (S3 K-Cut, 82) with the load-bearing grafts from S1 (Minimal, 78), S4 (Edge-Location, 76), S5 (Gate/Licensing, 80), S0 (Foundation, 64), and S2 (Calibration, 55) into ONE coherent strategy-of-record. Conflicts are resolved with fresh source/DB evidence, cited inline.
**Authority spine:** `diagnosis_confirmation.md` (the authoritative target), the five P1 strategies, `P1_scoring.md`, AGENTS.md, operator contract laws 1–8.

---

## 0. THE ONE-PARAGRAPH STRATEGY

The five strategies, approaching from five independent angles, **converge on one picture** and disagree only on which lever to pull first. The convergent picture, re-confirmed here at source and settlement: **the point posterior `q` is honest** (Σq = winners across 1309 bins, S1; near-center rings calibrated at ratio 1.00–1.15, S3's `sigma_scale_fit.json` cut) — the model **selects the correct bin**, so law-8 metadata/bin-identity is intact and this is NOT a wrong-bin defect. The binding constraint (`capital_efficiency` firing on q_lcb≈0, ~88% of rejections — `diagnosis_confirmation.md:18`) is real, and `capital_efficiency` is the **honest gate we KEEP untouched** — but its **input q_lcb is two different things in two different bin populations**, and settlement separates them cleanly: (A) on **far / open-ended tail bins** (`or_higher`/`or_below`, price<0.05), q_lcb≈0 is **HONEST** — those bins settle 0/72 (S4), the point q itself *over*-states them 3.4–7.3× (S0/S3/S4 σ-fit tail ratio), and rejecting them is correct (laws 1/4/5/8 resolved: real no-edge); (B) on **near-center `exact` ring bins** that genuinely won (realized 0.108 vs asserted 0.093 vs market ~0.091, S4; q_lcb R/E 3–4× too low on mid bands, S1), the q_lcb is **mechanically crushed to ~0 by a lower-bound construction tuned to defend against (A)'s sins** — and *this* is the only suppressed correct-bin alpha. The strategy is therefore: **keep the honest spine, collapse the dead gate-mass (S3/S5), fix the q_lcb input so the ring bin's honest lower bound stops lying (S1/S0/S4) — in shadow, graded against settlement at the event level (S2) — and promote to live ONLY if the ring cohort clears >51% after the 1¢ fee in forward settlement.** The honest headline the operator must hear up front: **there is no large suppressed alpha pool behind the gates** — the only durable correct-bin edge is the thin near-center ring (~1.5–3pp of honest market under-pricing), it may not survive the 1¢ fee, and DONE is forward settlement proving it, never a fill.

---

## 1. THE DECISIVE FORK, RESOLVED WITH FRESH EVIDENCE

The diagnosis left ONE decisive question open (`diagnosis_confirmation.md:140`): *is live q_lcb≈0 on cheap/non-favorite bins a CORRECT bin-belief (honest no-edge) or a BROKEN floor crushing real mass?* Five strategies each answered it independently. The scorer flagged the apparent contradiction (T1): S1 says "q_lcb 3–4× too low"; S3/S4/S5 say "cheap bins settle 0%". **Both are true — they grade different bin populations.** I re-confirmed the code mechanism that makes both true at source today (read-only, this session):

### 1.1 The live q_lcb has TWO production paths — both confirmed, both crush the ring

There is not one live q_lcb seam but two, and the two "competing" strategies each correctly described one:

- **Canonical bootstrap path** (`event_reactor_adapter.py:10322` → `_side_q_lcb_from_yes_samples:10148`): `q_lcb = lower_quantile(bin_yes_probability_samples, 0.05)` clamped under the point. **Verified at source: `probability_uncertainty_from_samples(yes_samples)` is called with NO `penalties` and NO `n_eff_override`** (`:10181`). So on the live canonical path: **no δ-penalties, and the C3 N_eff width-correction never fires** (it writes only the `compare=False` shadow field `q_lcb_neff_corrected`, `probability_uncertainty.py:215,283`). James-Stein/EB shrinkage is pinned `authority_on=False` (`event_reactor_adapter.py:2811`). This is S1/S2/S4's path and **vindicates S3's three code facts.**
- **Replacement-bundle path** (`event_reactor_adapter.py:9756 _replacement_yes_lcb_for_bin` → `:9970`): reads the materialized bundle `q_lcb` map (`fused_center_bootstrap_p05`), else a **Wilson-over-AIFS-votes** fallback that **returns `0.0` for any bin absent from the vote map** (`:9788`). The bundle bound's center-jitter width is `center_sigma_c = anchor_sigma_c = float(fused.sd)` (`replacement_forecast_materializer.py:1133,1766`). This is S0's path.

### 1.2 S0's headline claim is TRUE at the database level (not asserted)

`SELECT json_extract(provenance_json,'$.anchor_sigma_c'), COUNT(*) FROM forecast_posteriors GROUP BY 1` → **`3.0 | 3462`** — every posterior carrying the field has `anchor_sigma_c` pinned at exactly **3.0**, never a per-cell fitted value. The default `anchor_sigma_c: float = 3.00` (`replacement_forecast_materializer.py:125`) is the live value universally, i.e. `fused.sd` collapses to the soft-anchor prior τ0. S0's seeded simulation shows the consequence: at center_sigma=3.0 a ring bin has q_point≈0.10 / **q_lcb=0.0000**; at the settlement-honest ~1.0°C q_lcb=0.005; at 0.4°C q_lcb=0.036 — **while q_point stays flat at 0.10–0.12** (S0 §1.4). A 3°C center wobble slides the predictive Normal off the ring bin and zeroes its 5th-percentile mass without moving the belief.

### 1.3 The resolution (the spine of this strategy)

**Both crush mechanisms are real, and both share one signature: a lower-bound construction that zeroes the ring bin's 5th-percentile mass while the point q stays honest.** The canonical path zeroes it via the deep left tail of a 51-member resample percentile (S1); the bundle path zeroes it via the over-wide 3.0°C center jitter and the vote-quantized Wilson-zero (S0). On **far/open-tail bins** this zero is HONEST — the point mass is genuinely ~0.2% (S3's Chengdu `q=[...,0.002,0.002]`, S4's 0/72), so *any* lower bound is ~0 and `capital_efficiency` is correct to reject. On **near-center ring bins** that settle (S4's `exact` bins, realized 0.108) the zero is a **mechanical artifact of the same construction** — the suppressed alpha.

> **Verdict: q_lcb≈0 is CORRECT on far/tail bins (keep rejecting — laws 1/4/8) and BROKEN on near-center ring bins (the fix target). Settlement is the instrument that tells them apart, and the point q is never the defect.**

This is the law-8 answer: the metadata/bin-selection foundation is intact (point q honest); the defect is the *width* of the belief's lower bound on the one class where the model honestly disagrees with the market.

---

## 2. THE KEEP-LIST (load-bearing invariants — touching any of these is out of scope)

Merged and de-conflicted from all five strategies' KEEP sections; every item is confirmed against the binding-constraint doc or source.

- **K-SPINE — `capital_efficiency` is the honest final arbiter.** `(q_lcb − price)/price ≤ 0 → reject` (`live_admission.py:87-119`). It is a single honest inequality with no cap/throttle/fill term. **Never loosen it; every fix targets the q_lcb that flows IN, never the comparison.** (All five strategies; `diagnosis_confirmation.md`; task #66.)
- **The POINT posterior `q` chain end-to-end** (member resample → MAP-Platt #129 → posterior MODEL_ONLY → `bin_probability_settlement`). Σq=winners (S1). **The crown jewel — do not touch.** Any change that moves the point is out of scope for the q_lcb fix.
- **The honest ZERO q_lcb on far-OTM / open-tail zero-support bins.** Point mass thin → bound thin regardless of construction. Do NOT manufacture far-tail alpha (law 4; B2 audit; S0/S1/S3/S4 all concur).
- **`direction_law`** (buy_yes forecast-adjacent / buy_no forecast-distant). It is not a throttle — it ENCODES where settlement-backed edge lives (the near-center ring) and makes the Milan far-tail-YES loss unconstructable (law 6; S3/K3).
- **The `q_lcb ≤ q_point` invariant** and the native-NO `1 − q_ucb_yes` complement (Hidden #3, `_side_q_lcb_from_yes_samples`); penalties/corrections may only LOWER the bound, never raise it above the point.
- **The B1/M5 submit-latch absorber + external-close reconcile** — self-cleared 06-14T01:06, self-heals (#31; `diagnosis_confirmation.md:97`).
- **The profitable-era NO eligibility gate** (#74, closes the NO-on-winning-ring loss class) and the **market-anchor cap** (closes the C3 phantom-NO loss class, `event_reactor_adapter.py:7472`).
- **INV-37 cross-DB discipline** (ATTACH+SAVEPOINT only) + the K1 DB split (zeus-world / zeus-forecasts / zeus_trades).
- **The time-semantics (#16) and per-city settlement-rounding preimage (#24) contracts** — law-8 metadata that makes the near-center q honest. The ring edge is a few points; it exists ONLY if μ*, bin identity, and the rounding preimage are exact.
- **Settlement is the only truth** (law 5): no in-sample promotion; the replacement-form §4 walk-forward discipline holds (in-sample EV inflated, holdout collapses to +1.2¢…−2.7¢).
- **CI-honesty:** σ never tightened below MC; `k_cov` never shrinks σ; EMOS licensing is HIGH-metric only (synthesis keep #6). A σ-fit may RAISE ring mass via the mixture, but the core σ floor stays ≥ MC.

---

## 3. THE ORDERED THRUSTS (the strategy)

Six thrusts in dependency order. The design principle throughout is the operator's: **collapse N gates to K, never add a gate/cap/flag/lane; default SIMPLIFY; go-live-direct or shadow-then-promote, never a permanent shadow.** Thrusts 1–2 are pure subtraction (zero behavior change, ship first to clean the signal). Thrust 3 is the one causal q_lcb fix. Thrust 4 is the upstream point-q lever. Thrust 5 is the submit-path fix. Thrust 6 is the settlement-grading harness that gates every promotion.

The structure is **S3's K=5 collapse as the skeleton**, with **S1's bidirectional-isotonic q_lcb and S0's center-sigma fix as the q_lcb-input repair** (the piece S3 explicitly deferred and lost points for), **S4's σ-fit promotion as the point-q lever**, **S5's dead-authority deletions folded into the collapse**, and **S2's event-level settlement harness as the grading authority for all of it.** Each thrust names the strategies it integrates.

### THRUST 1 — Observability pre-work (S1/K4; ship FIRST, zero runtime risk)

Fix the cycle-summary attribution so the log stops conflating display-EV with the kill-gate: in the reactor cycle-summary builder (`event_reactor_adapter.py:7149-7206`), print `best=… rejected_by=<the actual gate that killed the displayed best>` instead of letting `best=` float free of the bucket label. **Why first:** until the log stops lying about *why* the best candidate died, no downstream q_lcb change can be trusted to have had the intended effect. This is pre-work, not a fix.

### THRUST 2 — Collapse the dead gate-mass (S3/K2 + S5/A3 + S5/B3; pure subtraction)

All three of these delete dead vocabularies whose intent is subsumed elsewhere; net gate count strictly decreases; zero behavior change because every deleted path is already dead live.

- **Collapse the two licensing vocabularies to one** (S3/K2): the static source allow-list `{EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}` (used by `live_admission.py` G2 `buy_no_conservative_evidence:183` and G4 `coverage_unlicensed_tail:141`) is **dead** — no live code stamps `EMOS_ANALYTIC` (the `edli_emos_ci_live_enabled` override defaults False, the license file was never built — `diagnosis_confirmation.md:50-57,106`). Make the live `settlement_backward_coverage` VERDICT the **sole** license authority; the `q_lcb_calibration_source` string becomes pure telemetry, never a gate input.
- **Delete the dead C2/C3 selection-authority live-path import** (S5/B3): `_compute_selection_shrinkage(..., authority_on=False)` (`:2811`) produces only NULL stamps in receipts — neither deciding nor providing telemetry. Remove the dead live-path import; leave BH/FDR as the condemned-interim gate with a provenance note; **defer** the BH→C2/C3 promotion until the foundation is settlement-proven.
- **Delete the dead δ-penalty plumbing and shadow N_eff/JS fields on the live q_lcb seam** (S3/K1): `UncertaintyPenalties` is never populated on `_side_q_lcb_from_yes_samples`; `q_lcb_neff_corrected`/James-Stein fields never reach a live decision. Per the no-shadow law, a field that gates nothing is gate-mass — remove it (or keep strictly as a settlement-grading diagnostic in Thrust 6, never as a live input).

**Do NOT build `state/emos_ci_license.json` or flip `edli_emos_ci_live_enabled`** (S3/K2(a) rejected, S4, S5/§2.6): never-built by design, licenses a settlement-dead class, and is exactly the gate-accretion the operator forbids.

### THRUST 3 — Fix the q_lcb INPUT: settlement-calibrated bidirectional lower bound (S1/K1 primary, S0 mechanism-confirmed; SHADOW first)

This is the **one causal fix** for the suppressed ring-bin alpha. The architectural blind spot S1 named and this session confirmed: **every authority in the live stack can only move q_lcb DOWN** (the 5th-percentile floor; `settlement_backward_coverage` shrink-only; market-anchor "only ever lowers"; penalties lower only) — **nothing can correct a too-LOW bound**, while settlement says under-claiming is the dominant alpha-killer (S1: Σq_lcb=34.4 vs 119 winners; Brier(q_lcb)=0.080 > Brier(q_point)=0.071; R/E 2.87–3.96 on mid bands). That asymmetry survived 100 patches.

**The fix:** replace the raw-percentile q_lcb read with a **settlement-coverage-calibrated bidirectional lower bound**, REUSING the existing `_isotonic_realized_rate` (`settlement_backward_coverage.py:97`) — which already computes the claimed-band → realized-win-rate map — but allowed to move q_lcb in **BOTH** directions (up where the settled record proves the bound too conservative, down where too aggressive), with a Jeffreys/Wilson analytic lower bound as the cold-start fallback for thin cohorts. **The point `q` is never touched** (S1's INV-A). This collapses three LCB vocabularies (raw bootstrap percentile + one-directional shrink-only coverage + dead source-string licensing) into ONE settlement-anchored authority — a SIMPLIFY, not an addition.

**On the bundle path specifically (S0's mechanism), there is a second, narrower repair that the open decisions (§4) must adjudicate:** the `center_sigma_c=3.0` hardcode and the vote-quantized Wilson-zero are the *bundle-path* analog of the same too-low-bound disease. The settlement-calibrated bidirectional bound (this thrust) subsumes BOTH paths if it becomes the single q_lcb authority feeding `capital_efficiency`; S0's center-sigma fit is the alternative if the bundle path is kept as a distinct producer. **§4-D1 decides which.**

**Shadow discipline (S2/S0/S1 all mandate):** the calibrated q_lcb runs in **shadow first** — it computes the would-be ring-bin admissions and grades them against settlement at the event level (Thrust 6). It goes live ONLY after the shadow ring cohort clears the DONE bar (§5). This is NOT a permanent shadow (operator-forbidden): it is a time-boxed promotion gate that flips to live-direct on settlement evidence or is abandoned.

### THRUST 4 — Fix the point-q tail over-confidence: promote the σ-fit (S4 primary, S0/S3 corroborate; the upstream lever)

S4's bin-KIND cut is the clearest diagnostic in the batch: the model **bleeds mass into the open tail bins** (`or_higher` 5× over-confident, `or_below` ∞ over-confident) and correspondingly **under-fills the `exact` ring bin** where it genuinely beats the market. `sigma_scale_fit.json`'s own tail ratio (0.296 C / 0.138 F) measures the same fact independently. The fix is to **move the provably-wrong tail mass back onto the ring bin in the POINT q** — which is exactly what `sigma_scale_fit.json` already does (it raised the mode-bin calibration ratio 0.514→0.961, `replacement_final_form §1e-bis`), but it is `candidate=true`, operator-gated, and **not wired into the live primary path** (`b2:76`). The lever is built and sitting on the shelf.

**Promote the σ-fit to the live primary q construction**, after its own `_meta.promotion` forward-fill validation (mode-bin ratio in [0.85,1.15] AND tail ratio moving 0.30→1.0 on settlements the fit did not see). This is the *upstream* input to Thrust 3: a tail-corrected point q produces a higher ring-bin point, which raises the ring bin's honest q_lcb regardless of which lower-bound construction Thrust 3 settles on. **The two thrusts compose: T4 fixes the point shape, T3 fixes the lower-bound width; together the ring bin's honest q_lcb clears its ~0.09 price.**

S0's center-sigma fit (`σ_center` measured from |fused_center − settled|) is the **same artifact pattern** as the σ-shape fit and is the bundle-path dual of T4; §4-D1 decides whether it is a separate change or subsumed by T3's single-authority bound.

### THRUST 5 — Make the submit stage a re-decision, not a death (S3/K5; the secondary blocker)

The diagnosis's #2 blocker (`:130`): admitted candidates die at SUBMIT via `SUBMIT_ABORTED_MODE_FLIPPED` (proof priced TAKER, fresh book MAKER, or vice-versa) — currently a **terminal reject** that wastes the admission and forces a full re-traverse next cycle, often after the window closed. **Collapse the submit stage to one authority:** re-price the chosen mode against the fresh book at submit, and if the re-priced mode still clears the SAME `q_lcb>price` criterion (the K-spine), submit in the fresh mode; abort only if the fresh re-price fails that one criterion. Mode-flip becomes a re-decision under the single admission criterion, not a separate vocabulary. **This is relevant only AFTER Thrusts 3–4 produce a ring-bin candidate** (the cheap-tail class that dominated the 454 mode-flip receipts is settlement-dead, so the quantitative unblock is small — but the real ring candidates stop dying to a transient tick).

Also fold in **S3/K4's covert-gate fix**: the flat `0.01` penalty in `_robust_trade_score_from_generated_inputs` (`:13737`) eats the entire edge at price 0.001–0.01 — a hidden cheap-bin ban masquerading as a score. Replace it with a cost-proportional adverse-selection term so the honest gate (`capital_efficiency`), not a constant, rejects cheap bins. Near-zero admission effect (cheap bins are settlement-dead), but it removes a lie.

### THRUST 6 — The settlement-grading harness (S2; the gate on every promotion, NOT a new live gate)

Every promotion above (T3 shadow→live, T4 candidate→live) is gated by ONE thing: a **settlement-graded, event-level, walk-forward reliability monitor**. This is S2's contribution, scoped correctly: it is the *grading instrument*, not the licensing authority and not a new live gate. Its non-negotiable contracts:

- **Event-level de-duplication** (S2 INV-CAL-1): one graded row per (city, target_date, bin) — never per-snapshot (the reactor writes a regret row every cycle; NYC-06-04 had 42 rows for one bin → ~40× inflation). Enforce with `GROUP BY city,target_date,bin` + a test asserting no (city,date,bin) contributes >1 unit.
- **Settlement is the only grade** (INV-CAL-2): `won` derived solely from `settlement_outcomes` (authority=VERIFIED) bin-match — audit the grader's bin-match against the `settlement_semantics` preimage so a boundary/rounding mismatch can't silently mislabel (law 8). Settlement truth lives in `zeus-forecasts.db.settlement_outcomes` (7009 VERIFIED rows, confirmed this session); `zeus-world.db.settlements` is EMPTY — do not read it (S4 §0).
- **Walk-forward only** (INV-CAL-3): a band's verdict uses only events with target_date < decision_date.
- **vs-market benchmark mandatory** (INV-CAL-4): a band is "edge" only if model-q beats market-q (lower Brier) on the same events AND realized−price lower-CI > 0. A tie with the market is no-edge regardless of realized rate.
- **Ring-distance bucketing** (S4 salvage of Alt-B): grade by dist-from-center (0,1,2,3,≥4,tail) — the granularity that catches "tail over-confidence returned" or "calibration over-corrected past the ring".

This harness IS the walk-forward data the shadow thrusts accrue; it is not a separate accumulation phase. It answers, with dates and numbers, whether the ring edge survives the 1¢ fee — and if no band ever clears, that is the honest law-1-compliant verdict (the market is efficient; there is no tradeable alpha to unblock), proven not asserted.

---

## 4. EXPLICIT OPEN DECISIONS FOR IMPLEMENTATION PLANNING

These are the genuine forks the synthesis cannot close from evidence alone; P2/P3 implementation planning must resolve each with the named test. They are listed with my opinionated lean so the planner has a default.

- **D1 — ONE q_lcb authority across both paths, or two repairs?** Thrust 3's settlement-calibrated bidirectional bound *could* become the single q_lcb producer feeding `capital_efficiency` for BOTH the canonical and bundle paths (collapsing S0's center-sigma fix and S1's isotonic fix into one authority), OR the bundle path keeps its own `fused_center_bootstrap_p05` producer with S0's `σ_center` fix applied to it. **Lean: single authority** (operator's 大一统 law; collapses more) — but this requires confirming the bundle path's consumers can route through the seam. **Test:** trace whether `_replacement_yes_lcb_for_bin:9756` can be made to defer to the calibrated seam without losing the live-eligibility (`q_mode`) semantics.

- **D2 — The decisive sub-population backtest (the scorer's T1 resolution, MUST run before any q_lcb change ships).** Re-run S1's settlement backtest **restricted to `replacement_q_mode=FUSED_NORMAL_FULL` cells AND bins with `q_point > 0.05`** (filtering out the structural-zero far bins). If R/E=3–4× under-coverage **persists** on that sub-population, S1's bidirectional isotonic fix (T3) is the right primary lever. If it does NOT persist, S3's "thin ring is the only edge" framing stands and S4's σ-fit promotion (T4) is the primary lever, with T3 secondary. **This single backtest decides T3-vs-T4 primacy.** Lean: run it before committing implementation order.

- **D3 — Center-sigma source for the bundle path (if D1 keeps two producers).** S0/A1 must first trace WHY `fused.sd` is pinned at 3.0 (almost certainly the EQUAL_WEIGHT degrade leaving prior τ0=3.0 as the posterior sd). If the fused posterior sd is genuinely unavailable per-cell (thin history), replace `center_sigma_c` with S0/A2's **settlement-fitted `σ_center`** (measured |fused_center − settled| by lead bucket), operator-gated like the σ-shape fit — never keep the 3.0 prior default and never hand-pick a constant. Lean: A2 (settlement-fitted), with A1 as the mandatory diagnostic first.

- **D4 — Delete the Wilson-over-AIFS-votes live fallback, or keep as shadow?** The vote-quantized Wilson path (`:9788`) returns 0.0 for any zero-vote bin and 20% of settled winners had ≈0 AIFS votes (S0). Once T3/T4 make the analytic bound the authority, the Wilson fallback has no live job. **Lean: delete as a LIVE authority** (S0/B2; fail-closed NULL = non-live-eligible when fusion is absent, rather than a degraded vote bound) — but confirm no shadow-coverage consumer depends on a non-null bound before deleting.

- **D5 — N_min and the DONE cohort size.** S2 proposes N_min≈150–200 events per band before licensing; the scorer's DONE criterion (§5) is n≥30 forward fills clearing 51% after-cost. These are different units (calibration-fit events vs live fills). **Lean:** N_min governs the *shadow* band-verdict (when the harness trusts a band); the n≥30 forward fills governs *live promotion*. Specify both explicitly in P2.

- **D6 — Sequencing risk: when may Thrust 2's deletions land relative to Thrust 3?** S1 was docked for proposing to delete `coverage_unlicensed_tail`'s fail-closed antibody *before* the foundation fix is settlement-proven (a dangerous window). **Lean:** Thrust 2 deletes only the *provably-dead* paths (the source allow-list that never stamps live, the dead C2/C3 import, the shadow penalty fields) — it KEEPS `coverage_unlicensed_tail`'s INTENT folded into the single verdict criterion (S3/K2 "keep the antibody, collapse the vocabulary"). The Milan-24C regression test moves to the NEW path (calibrated q_lcb≈0 on unbacked tail → `capital_efficiency` rejects). Do not delete the antibody's *effect* until the calibrated q_lcb provably reproduces it in shadow.

---

## 5. THE CAUSAL CHAIN TO DONE (and the only DONE criterion)

1. **T1** makes the log honest about kill-gates → **T2** removes dead vocabularies so the signal is clean → **T4** moves over-assigned tail mass back onto the ring bin in the POINT q (σ-fit promoted after forward-fill validation) → **T3** replaces the too-low q_lcb with a settlement-calibrated bidirectional bound so the ring bin's *honest* lower bound rises from ~0 toward its settlement-real ~0.03–0.13.
2. With an honest ring-bin q_lcb (~0.03–0.13) vs a market that under-prices the ring (~0.09), `(q_lcb − price)/price > 0` → **`capital_efficiency` ADMITS** — honestly, no loosening. Far/tail bins keep q_lcb≈0 (point mass thin) and keep being rejected. Cheap buy_no favorites and base-rate lanes are NOT re-enabled (laws 4/5).
3. `direction_law` keeps only the near-ring geometry; horse-race Kelly sizes on the corrected posterior (downstream relay, law 8 — it re-computes correctly *because* the belief is now correct); envelope $5–15 (#18).
4. **T5** ensures a transient mode-tick re-prices-and-re-admits instead of killing the admission; the latch is open (#31, self-heals).
5. The fill **settles**. **T6** grades it at the event level, walk-forward, vs-market.
6. **DONE (the ONLY criterion):** the shadow ring-bin cohort, then the live ring-bin fills, clear a **stable, continuous >51% after-cost settlement win-rate at n≥30 forward fills**, with model-Brier < market-Brier on the same events. **The proof is step 6 repeating, never step 4 firing once.** If the ring cohort does NOT clear after the 1¢ fee, the honest law-1-compliant verdict is that the market is efficient and the ring edge does not survive friction — proven with settlement dates and numbers, not engineering effort, and the correct response is to stand down on that lane (S4 Alternative C), NOT to loosen a gate.

**The bidirectionality is the whole point (S1):** if realized < calibrated on traded bins, the calibration was itself over-claiming → the downward arm (preserved) shrinks it next cycle. The fix is self-correcting in BOTH directions.

---

## 6. WHAT THIS STRATEGY EXPLICITLY DOES NOT DO

- It does NOT loosen `capital_efficiency` (the honest arbiter; the fix is its input).
- It does NOT re-enable cheap-tail longshots (0/72 settled — S4) or base-rate buy_no favorites (net −0.069/share after the 1¢ fee on the admitted subset — S4; slightly negative even at ≥0.8 — S2). Both are settlement-proven dead; chasing them violates laws 1/4/5/8.
- It does NOT build `emos_ci_license.json`, flip `edli_emos_ci_live_enabled`, or re-route `coverage_unlicensed_tail` as a headline fix (the refuted synthesis; 0.6% of rejections, 0 receipts — `diagnosis_confirmation.md`).
- It does NOT add any new gate, cap, throttle, allowlist, or permanent shadow lane. Every thrust is a SIMPLIFY (T1/T2/T5 subtract; T3/T4 replace-with-fitted; T6 is a grading instrument, not a gate). Net gate count strictly decreases (laws 3).
- It does NOT rebuild the q point chain — the point is honest (Σq=winners), the bin selection is correct, the metadata foundation is intact (law 8). The disease is the lower-bound width on one bin class, not the belief.

---

## 7. THE HONEST BOTTOM LINE FOR THE OPERATOR

There is **no large suppressed alpha pool behind the gates.** Five independent settlement joins (S1 n=119 families; S4 430 instruments; S5 0/1619 admitted candidates won; S3 σ-fit ring calibration; S2 event-level) agree: the gate layer is the *only correct actor* — it has been correctly rejecting settlement-losers, and the cheap-tail "alpha" the system chased for 100 patches does not exist (the model's own tail over-confidence, refuted at 0/72). The single durable correct-bin edge is the **thin near-center `exact` ring bin** — ~1.5–3pp of honest market under-pricing where the model is mildly *under*-confident (realized 0.108 vs asserted 0.093 vs market ~0.091). Capturing it continuously is a **metadata-correctness + q_lcb-input-honesty + continuous-executability** problem (Thrusts 3/4/5 on the honest spine), NOT a gate-loosening problem. The primary execution risk is that ~1.5–3pp does not survive the 1¢ fee at Zeus's throughput — and the strategy's discipline is to **prove or refute that in shadow at the event level before a single live promotion**, accepting a dated, numeric "the market is efficient" verdict as a legitimate law-1 outcome rather than forcing a fill cost eats.

*End of P1 strategy-of-record. Read-only synthesis; no production code or daemon changed. Every empirical claim cited to file:line, artifact, or query+counts.*
