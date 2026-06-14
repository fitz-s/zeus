# P3 — VERIFICATION STRATEGY: Evidence Ladder to Prove the Plan Worked

**Date:** 2026-06-14
**Mode:** PLAN-MAKING (read-only; no production edits, no deploy, no live touch).
**Role:** P3 verification design. This document specifies the complete evidence ladder that gates each node in the P2 critical path from code-landed to live-promoted to DONE. Every gate is testable before the next step. DONE = the operator contract: continuous >51% after-cost settlement win-rate on TRADED markets at n≥30. Settlement is the only truth.
**Authority spine:** `P1_strategy_of_record.md` (Thrusts 1–6, the KEEP-list, the DONE criterion), `P2_W-QLCB.md` (the one causal fix, populations A/B/C, five RED-on-revert tests), `P2_W-SUBMIT.md` (W-S1/W-S2/W-S3 tests), `P2_sequence_and_critical_path.md` (nodes N1–N11, gates G0–G5, the ARM clause), `diagnosis_confirmation.md` (binding constraint: `capital_efficiency_lcb_ev` = 88%, confirmed at source).

---

## 0. THE SINGLE STRUCTURAL INVARIANT

Every gate in this document tests one of three orthogonal properties:

1. **Mechanism-correct** (RED-on-revert unit tests): the code path does exactly what the design says, no more.
2. **Settlement-correct** (shadow harness + the G4 band-verdict): the mechanism's output agrees with the settled record on TRADED markets.
3. **Edge-real** (G5 live promotion): the admitted trades clear >51% after the 1¢ fee at n≥30 forward fills, vs-market.

A gate at layer _i_ can never be skipped by evidence at layer _i+1_. Code that passes unit tests but fails shadow is NOT promoted. Shadow that licenses a band but fails live does NOT stay live. There is no shortcut from "code landed" to DONE.

---

## 1. PER-CHANGE RED-ON-REVERT TESTS

These tests FAIL if the specific fix is reverted (they encode the fix's invariant, not just its side-effects). Each carries the provenance header required by the file-header rule. All tests must be GREEN before the node merges; must go RED on revert before they are accepted as binding.

### 1.1 N1 — Cycle-summary attribution (T1 observability)

**File:** `tests/test_cycle_summary_attribution.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_rejected_best_names_actual_gate` | given a candidate with `capital_efficiency` as killer, `rejected_by=capital_efficiency_lcb_ev` appears in the summary output; NOT the display-EV | `event_reactor_adapter.py:7149-7206` cycle-summary builder |
| `test_display_ev_does_not_float_free_of_kill_bucket` | when `direction_law` is the kill-gate, summary shows `rejected_by=direction_law`; test fails if the summary outputs a mismatched bucket | same |

**ARM criterion:** N1 is self-arming (no live-behavior change). G0 passing is sufficient.

---

### 1.2 N2 — Dead-gate deletions (T2 pure subtraction)

**File:** `tests/test_dead_gate_deletion.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_source_allow_list_not_a_gate` | `live_admission.py` G2/G4 gates do NOT consult the static `{EMOS_ANALYTIC, SETTLEMENT_ISOTONIC}` source allow-list; a receipt with `q_lcb_calibration_source=None` is not failed by G2/G4 | re-introduction of the source allow-list at `live_admission.py:141,183` |
| `test_c2_c3_selection_shrinkage_dead_on_live_path` | `_compute_selection_shrinkage(authority_on=False)` (`:2811`) is not called on any live-code path; grep/AST asserts absence | re-import of dead C2/C3 selection authority |
| `test_delta_penalty_never_reaches_live_q_lcb` | `UncertaintyPenalties` is not instantiated on the `_side_q_lcb_from_yes_samples` call site; no penalty fields reach `capital_efficiency` | re-introduction of `UncertaintyPenalties` on live seam |
| `test_coverage_unlicensed_tail_still_rejects_far_tail` | Milan-24C antibody: a bin with `or_higher` ≥24°C, point q≈0.001, receives `capital_efficiency` rejection (NOT a `coverage_unlicensed_tail` licensing path) after N2 | *accidental deletion* of the antibody's effect — this test verifies the intent is preserved even though the vocabulary is collapsed |

**ARM criterion:** N2 is self-arming. G0 (all four tests GREEN + suite green) is sufficient. D6 guard confirmed by `test_coverage_unlicensed_tail_still_rejects_far_tail`.

---

### 1.3 N3 — E1 edge-location query (T6/E1 read-only analysis)

**File:** `tests/test_edge_location.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_e1_query_deduplicates_to_event_level` | the E1 query returns at most one graded row per `(city, target_date, bin)` — INV-CAL-1 (no per-snapshot inflation) | event-level dedup logic |
| `test_e1_uses_only_verified_settlements` | the query joins only `settlement_outcomes WHERE authority='VERIFIED'`; no row from `zeus-world.db.settlements` (verified EMPTY) enters the output | source routing |
| `test_e1_includes_vs_market_brier` | every output row carries both `model_brier` and `market_brier` for the same event set — INV-CAL-4 | vs-market benchmark |

**ARM criterion:** N3 is self-arming (read-only). G0 sufficient.

---

### 1.4 N4 — E2 grading harness (T6 verdict authority)

**File:** `tests/test_e2_grading_harness.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_e2_walk_forward_only` | for any band-verdict at `decision_date=D`, only events with `target_date < D` are included — INV-CAL-3 | walk-forward filter |
| `test_e2_band_verdict_requires_min_n` | a band with `n < 30` never receives `LICENSED`; it receives `INSUFFICIENT_DATA` | the n≥30 floor |
| `test_e2_no_edge_when_model_brier_geq_market_brier` | even if `realized > price`, a band is `NO_EDGE` when `model_brier >= market_brier` on the same events — INV-CAL-4 | vs-market gate |
| `test_e2_ring_distance_bucketed` | verdicts are keyed by `(city, metric, season, dist_from_center)` with dist in `{0,1,2,3,≥4,tail}` — S4's granularity | ring-distance bucketing |
| `test_e2_output_is_dated_json` | the harness writes a JSON artifact with `generated_at` ISO-8601, `settlement_date_range`, and `band_verdicts` array — the dated evidence contract | artifact schema |
| `test_e2_city_date_bin_triple_appears_once` | assertion: in the graded substrate no `(city, target_date, bin)` contributes >1 row to any verdict — INV-CAL-1 | dedup invariant |

**ARM criterion:** N4 is self-arming (read-only verdict, no live gate). G0 sufficient. N4 must be GREEN before any live-behavior promotion is attempted.

---

### 1.5 N5 — σ_center producer fix (T3a shadow)

**File:** `tests/test_sigma_center_fit.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_center_sigma_not_hardcoded_3` | `_build_fused_q_bounds` is called with a fitted `σ_center`, NOT the literal `anchor_sigma_c=3.0` on any `FUSED_NORMAL_FULL` row; AST/grep asserts absence of `center_sigma_c=float(bayes_precision_fusion_override.anchor_sigma_c)` at `materializer.py:1766` | revert to `anchor_sigma_c` hardcode |
| `test_sigma_center_fit_is_settlement_derived` | `state/sigma_center_fit.json` is produced by regressing `|fused_center − settled_value|` on lead-bucket from `settlement_outcomes`; test asserts the artifact's `_meta.source` field is `settlement_residual`, never a hand-picked constant | hand-picking σ_center |
| `test_sigma_center_candidate_true` | `sigma_center_fit.json._meta.status == "candidate"` at merge time; the file does NOT carry `status="live"` until operator ARM | premature live-status |
| `test_shadow_column_does_not_touch_live_table` | the rematerialized bundle q_lcb is written to a shadow column / shadow bundle table, never to the live `forecast_posteriors.q_lcb` column, while `candidate=true` | shadow-isolation requirement |
| `test_diagnostic_trace_3_0_origin` | the commit introducing N5 carries a result from tracing why `anchor_sigma_c` is 3.0; this is enforced by a doc-check test that fails if `docs/evidence/planning_2026-06-14/sigma_center_diagnostic.md` does not exist — the diagnostic is a prerequisite | D3 mandatory prerequisite |

**ARM criterion:** N5 shadow is self-arming (candidate flag, shadow column). G0 + G3 (byte-identical on live path with candidate=false) required. Promotion to live requires G2 (forward-fill validation: shadow ring q_lcb produces bounds in [0.01, 0.15] for `FUSED_NORMAL_FULL ∧ q_point>0.05` cells) + ARM.

---

### 1.6 N6 — σ-shape point-q promotion (T4 first live-behavior node)

**File:** `tests/test_sigma_shape_point_q.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_mode_bin_ratio_in_range` | on the forward-fill validation set (settlements the fit did NOT see): `mode_bin_ratio ∈ [0.85, 1.15]` for every city/season combination — G2 gate | σ-shape fit promotion check |
| `test_tail_ratio_not_worse` | on the same holdout set: `tail_ratio` (or_higher + or_below vs exact) moves from the pre-fit ~5× over-confidence toward ≤1.5× — G2 gate | tail-correction verification |
| `test_sigma_scale_fit_candidate_status_before_arm` | `sigma_scale_fit.json._meta.status == "candidate"` at merge time; test fails if status is "live" before ARM | premature live-status |
| `test_point_q_sum_to_one` | after applying the σ-shape fit, Σq over all bins for a given (city, target_date) ∈ [0.99, 1.01]; the tail-mass redistribution does not break normalization | probability mass conservation |
| `test_ring_bin_mass_increases` | for a synthetic `or_higher=5×overconfident` family, the ring `exact` bin's point q rises after applying the σ-shape scale; the tail bin's mass falls | the intended redistribution |

**ARM criterion:** N6 is operator-gated (first live-behavior change). Evidence artifact: `sigma_scale_fit.json._meta` populated with forward-fill validation stats passing G2 thresholds + dated JSON. Operator reviews the artifact and authorizes the live flip.

---

### 1.7 N7 — Bidirectional q_lcb calibration (T3b shadow → live)

**File:** `tests/test_settlement_calibrated_qlcb.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_qlcb_up_arm_lifts_ring_band` | synthetic cohort: raw q_lcb=0.005 (crushed), realized win-rate=0.11, n=40 → `calibrated_qlcb` rises to ≈0.10 (`realized − Jeffreys_margin`), `source="SETTLEMENT_ISOTONIC"`, `arm="up"` | shrink-only wrapper (returns 0.005 unchanged) |
| `test_qlcb_far_tail_stays_zero` | population A antibody: raw q_lcb=0.0, realized=0.0, n=72 (the 0/72 bin cohort) → calibrated q_lcb ≈ 0, `capital_efficiency` rejects; `arm` never "up" | accidental far-tail UP arm |
| `test_qlcb_never_exceeds_point` | population C antibody: q_point=0.03 (below 0.08 market), realized in band=0.20 → `min(target, q_point)` clamps calibrated q_lcb ≤ 0.03; `(q_lcb−price)/price < 0` still rejects | dropping the q_point clamp |
| `test_qlcb_bidirectional_down_arm_preserved` | over-claimed band (claimed=0.30, realized=0.10, n=40) → shrinks to ≈0.09, `arm="down"` | accidental loss of the down arm |
| `test_cold_start_returns_raw` | `n_obs < MIN_N` → `calibrated_qlcb == q_lcb_raw`, source="FORECAST_BOOTSTRAP", arm="none"; no analytic floor invented | hand-tuned cold-start floor |
| `test_shadow_flag_off_is_byte_identical` | with the shadow flag OFF, `apply_settlement_coverage` output is byte-for-byte identical to pre-N7 behavior on a sample of 200 actual receipts | flag leak |
| `test_isotonic_map_is_walk_forward` | the isotonic realized-rate map used at inference time for cell (city,metric,season) only incorporates settlements with `target_date < inference_date` — INV-CAL-3 | future-peek in the calibration |

**G1 prerequisite (the gate before the gate):** before N7's UP arm is committed, run the sub-population backtest:

```sql
-- join forecast_posteriors (q_lcb_raw, q_point) to settlement_outcomes (VERIFIED, won/lost)
-- RESTRICT: replacement_q_mode = 'FUSED_NORMAL_FULL'  AND  q_point > 0.05
-- compute per claimed-band:  R/E = realized_win_rate / q_lcb_raw
```

- **If R/E ≈ 3–4× under-coverage persists on this sub-population** → population B is real → the UP arm ships (proceed to N7 shadow build).
- **If R/E collapses to ≈1.0** → no broad suppressed ring alpha (the population A + C artifact explains all under-coverage) → the UP arm does NOT ship; N5's producer fix goes to live (honest raw bound), and a dated "market efficient on the ring" law-1 verdict is emitted as DONE.

This single query decides whether N7's UP arm is built at all. It must run and be documented before N7 merges.

**ARM criterion for N7 shadow-flag ON:** G0 (all 7 tests GREEN) + G1 (UP arm warranted) + G3 (shadow OFF byte-identical) — self-arming.

**ARM criterion for N7 live promotion:** G0 + G1 + G3 + **G4 (E2 licenses at least one ring band)** + operator ARM.

---

### 1.8 N8 — Submit-lane observability (W-S1)

**File:** `tests/test_no_submit_receipt_lane_stamp.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_every_persisted_receipt_names_its_lane` | every persisted `edli_no_submit_receipts` row has non-null `submit_lane`; a full-pass on the no-submit lane carries `NO_SUBMIT_ADAPTER_LANE:<cause>` | removal of persist-boundary assert |
| `test_degrade_cause_propagated_to_receipt` | on a cycle where `real_submit_effective` degrades to False (allocator not configured), the receipt carries `submit_lane = "NO_SUBMIT_ADAPTER"` and `reason` names the degrade | degrade-cause threading |

**ARM criterion:** N8 is self-arming (telemetry only).

---

### 1.9 N9 — Mode-flip re-decision (W-S2)

**File:** `tests/test_submit_mode_redecision.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_mode_flip_readmits_when_fresh_mode_clears_capital_efficiency` | admitted candidate proven MAKER; fresh book → TAKER; taker `q_lcb − taker_all_in > 0` → receipt is SUBMITTED in TAKER, NOT `SUBMIT_ABORTED_MODE_FLIPPED` | old terminal-abort validator |
| `test_mode_flip_aborts_when_neither_mode_clears` | fresh book where the flipped mode's `q_lcb − cost ≤ 0` → `SUBMIT_ABORTED_MODE_FLIPPED` (genuine no-edge); guards over-loosening | accidental removal of the abort |
| `test_missing_proof_mode_still_fails_closed` | proof_mode missing/unknown → abort; never a default taker submit | P0 fail-closed invariant |
| `test_proven_maker_fresh_ev_favors_cross_readmits_taker_under_capital_efficiency` | the legacy `:4320-4342` case: proven MAKER, fresh EV favors cross, taker clears `capital_efficiency` → submit TAKER | tripwire deletion |
| `test_readmitted_maker_still_requires_book_agreement` | the downstream maker-book-agreement wall (`:4053-4060`) still fires on the resolved mode | downstream-wall preservation |
| `test_readmitted_taker_still_obeys_spread_guard` | taker spread guard still fires on the resolved mode | spread-guard preservation |

**ARM criterion:** N9 is operator-gated. Sequencing law: N9 ships ONLY after N5+N6+N7-shadow are producing ring-bin admissions (not before Wave 2 is shadow-active). Shipping N9 standalone speeds dead candidates to venue — explicit FAILURE.

---

### 1.10 N10 — Locked-opportunity tick de-dup (W-S3, LOW priority)

**File:** `tests/test_locked_opportunity_tick_improvement.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_requote_allowed_at_one_tick_improvement` | re-quote is allowed when new limit improves by ≥1 tick (market-specific); `improve_delta=0.02` is never consulted | revert to flat constant |
| `test_requote_blocked_below_one_tick` | re-quote blocked when improvement < 1 tick | over-loosening |

**ARM criterion:** self-arming (negligible impact, 5 receipts all-time). Fold into task #64.

---

### 1.11 N11 — E3 candidate-focus per-cell k_eff (W-EDGE-LOCATE)

**File:** `tests/test_e3_candidate_focus.py`

| Test | What it asserts | RED on revert of |
|---|---|---|
| `test_default_k_eff_is_one` | when no cell is E2-LICENSED, `k_eff(cell) = 1.0` for all cells; no ring geometry changes | k=1.0 default |
| `test_k_eff_widens_only_on_licensed_cell` | for an E2-LICENSED cell, `k_eff > 1.0`; for all others, `k_eff = 1.0` | per-cell licensing |
| `test_k_eff_derived_from_e2_verdict` | the `k_eff` value is computed from the E2 JSON artifact, not from an in-memory heuristic | provenance |

**ARM criterion:** N11 ships LAST (Wave 4), after a dist-0/1 ring band is live-proven (G5 passed). Operator-ARM-gated.

---

## 2. SHADOW / CANARY COMPARISON

### 2.1 What the shadow computes

N7 (the bidirectional q_lcb authority) runs in shadow mode: on every reactor cycle, for every candidate that the current live system rejects via `capital_efficiency_lcb_ev`, the shadow computes `calibrated_qlcb` (with the UP arm active) and records the **would-be admission decision** without submitting anything. Each shadow record carries:

- `(city, target_date, bin, dist_from_center, q_lcb_raw, q_lcb_calibrated, arm, source, would_admit)`
- the live decision: `live_rejected_by=capital_efficiency_lcb_ev`
- the matched settlement outcome when the event settles: `settlement_won` (from `settlement_outcomes WHERE authority='VERIFIED'`)

The shadow does NOT submit. It is a forward-looking regret accumulator over the target bin population.

### 2.2 What must match between shadow and live (the correctness invariants)

| Property | Shadow must ≡ Live | Measured by |
|---|---|---|
| Far-tail (population A) | shadow `calibrated_qlcb ≈ 0` for all structural-zero bins (realized rate = 0/72 cohort) → shadow `would_admit = False` | `test_qlcb_far_tail_stays_zero` + runtime metric |
| Below-market model (population C) | shadow `calibrated_qlcb ≤ q_point < price` → `would_admit = False` for ALL population-C bins | `test_qlcb_never_exceeds_point` + runtime metric |
| Live rejections not touched | the shadow's decisions on bins that the live system ADMITS (non-`capital_efficiency` rejections) are byte-identical to live | flag-off = byte-identical assertion |

### 2.3 What must improve (the shadow's positive evidence)

| Metric | Pre-N7 baseline | Shadow target to advance to G4 |
|---|---|---|
| Ring (population B, `dist_from_center ≤ 1`, `q_point > 0.05`) realized win-rate on shadow-would-admit events | Estimated ~0.10–0.11 (S4 `exact` bin realized; P1 §0) | Realized − 1¢ fee > 0.091 (the market price estimate); model-Brier < market-Brier on the same events |
| R/E ratio in `FUSED_NORMAL_FULL ∧ q_point > 0.05` cohort | ~3–4× (G1 threshold that warrants the UP arm) | Decreasing toward 1.0 as the calibration corrects under-coverage |
| Shadow `would_admit` rate on population B bins | ~0% (all crushed today) | ≥1 band with `n ≥ 30` distinct events and `realized > price` on the shadow would-admits |
| Shadow `would_admit` rate on population A + C | ~0% (correctly rejected) | Must remain ≈0% throughout shadow; any increase is a regression |

### 2.4 The shadow comparison runs continuously on the historical settled population

Because 7009 VERIFIED settlements already exist (2024-01-01 → 2026-06-13), the shadow comparison runs on history immediately after N7 is shadow-active — not only on forward settlements. The walk-forward constraint (INV-CAL-3) still applies: the isotonic map used for a band's verdict at `decision_date=D` is fitted only on events with `target_date < D`. The historical backfill compresses the rate-limiter from "wait for new ring markets to settle" to "check if the ring cohort already exists in settled history."

**The specific query to run at shadow launch:**

```sql
-- After fitting the bidirectional calibration on history:
SELECT
    dist_from_center,
    COUNT(*) AS n_events,
    AVG(settlement_won) AS realized,
    AVG(market_price) AS avg_price,
    AVG(settlement_won) - AVG(market_price) AS edge_after_price,
    (AVG(settlement_won) - 0.01) - AVG(market_price) AS edge_after_fee,   -- 1¢ fee
    AVG(model_brier) AS model_brier,
    AVG(market_brier) AS market_brier
FROM shadow_ring_cohort     -- the "would have admitted" events from N7 shadow
WHERE replacement_q_mode = 'FUSED_NORMAL_FULL'
  AND q_point > 0.05
  AND dist_from_center <= 1   -- population B definition
GROUP BY dist_from_center
HAVING n_events >= 5          -- show thin cohorts too; G4 fires at n>=30
```

Expected output at shadow launch (before G4 can fire): `n_events ≈ 5` (current distinct ring cohort). The query re-runs daily as new settlements arrive and as the isotonic map accrues more walk-forward observations.

---

## 3. END-TO-END ACCEPTANCE PROOF

### 3.1 The DONE criterion (the operator contract, stated precisely)

DONE is met when ALL of the following hold simultaneously on TRADED markets (markets where Zeus actually submitted and received a fill — not simulated, not regret-counterfactual):

1. **Settlement win-rate > 51% after the 1¢ fee**, computed as `(wins / n_fills) > 0.51` where `cost_per_fill = 0.01` is deducted from every fill.
2. **n ≥ 30 forward fills** on the licensed ring cohort (`dist_from_center ≤ 1`, `q_point > 0.05`, `replacement_q_mode = FUSED_NORMAL_FULL`), walk-forward only (fills that settled before the measurement date).
3. **Model-Brier < Market-Brier** on the same 30+ events — proving the edge is not base-rate but genuine model-vs-market disagreement.
4. **Continuous**, not a burst: the win-rate is stable over a rolling window of the most recent 30 fills; a single streak followed by reversion is NOT DONE.

**DONE is never triggered by:**
- A single fill settling as a winner.
- "Submission unblocked" (no fills yet).
- n < 30 fills, even at 100% win-rate.
- A win-rate computed on regret-counterfactuals (unrealised would-have-won events).
- Any shadow metric, however favorable.

### 3.2 Power threshold: why n ≥ 30

At the expected ring edge of ~1.5–3pp over market (P1 §7, S4 `exact` bins: realized 0.108 vs market ~0.091 → ~1.7pp), the one-sided binomial test `H0: p ≤ 0.51` at α=0.10 requires approximately:

- At p_true = 0.54 (the thin-edge case): n ≈ 30 for power ≈ 0.80 at a one-sided α = 0.10.
- At p_true = 0.57 (the middle case): n ≈ 15, but we hold n ≥ 30 for stability.
- At p_true = 0.51 + ε (the minimum case): n ≈ 300 — if the observed rate at n=30 is only just above 51%, DONE is NOT declared; the win-rate must be clearly above 51% at n=30 (lower 90%-CI > 0.51).

**The numeric gate:** `binom.cdf(wins, n_fills, 0.51) ≤ 0.10` — the probability of observing at least `wins` successes under the null that p≤0.51 is at most 10%. Equivalently: the lower one-sided 90% confidence bound on the true win-rate (using the Clopper-Pearson interval) exceeds 0.51. This is the `lo90 > 0.51` criterion that licenses DONE. If `lo90 ≤ 0.51` at n=30, continue accruing until either lo90 > 0.51 (DONE) or the observed rate falls so low that it is clearly not >51% (NO_EDGE verdict, stand down).

**The E2 verdict query at evaluation time:**

```sql
SELECT
    COUNT(*) AS n_fills,
    SUM(settlement_won) AS wins,
    1.0 * SUM(settlement_won) / COUNT(*) AS win_rate,
    1.0 * SUM(settlement_won) / COUNT(*) - 0.01 AS win_rate_after_fee,
    -- Clopper-Pearson lo90 computed in Python after this query
    AVG(model_brier) AS model_brier,
    AVG(market_brier) AS market_brier,
    MIN(settlement_date) AS earliest_settle,
    MAX(settlement_date) AS latest_settle
FROM live_ring_fills          -- zeus_trades.db: fills on the licensed ring band
WHERE fill_date >= [N7_live_date]   -- walk-forward from live-promotion date
  AND dist_from_center <= 1
  AND settlement_won IS NOT NULL    -- settled events only
ORDER BY settlement_date
```

If `n_fills ≥ 30` AND `Clopper_Pearson_lo90(wins, n_fills) > 0.51` AND `model_brier < market_brier`: emit DONE verdict with the dated JSON artifact.

### 3.3 The honest alternative termini (also DONE, differently)

Following P1 §5 and P2-sequence §5.4, the shortest path has three legitimate termini, each is a dated settlement-proven verdict:

| Terminus | When | Output |
|---|---|---|
| **Law-1 DONE (market efficient)** | G1 sub-population backtest: R/E collapses to ~1.0 on `FUSED_NORMAL_FULL ∧ q_point>0.05` | Dated `edge_location_report.json`: `ring_band: {verdict: "NO_EDGE", evidence: "R/E=1.0 on n=7009 settled events, market efficient"}`. N7 UP arm never ships. N5 producer fix goes live for honesty. |
| **INSUFFICIENT_DATA stall** | G4: ring cohort stays n<30 after exhausting historical settlements | Dated report: `ring_band: {verdict: "INSUFFICIENT_DATA", n_distinct_events=8, min_required=30}`. Continue shadow; re-evaluate when new ring markets settle. |
| **DONE (edge proven)** | G5: `lo90 > 0.51` at n≥30 forward fills, model beats market | Dated `settlement_proof.json`: win-rate, CI, Brier comparison, fill IDs, settlement IDs. |

None of these is a failure. The engineering failure modes are: (a) promoting to live without a G4 LICENSE, (b) declaring DONE at n<30, (c) loosening `capital_efficiency` when the ring edge fails the fee test.

---

## 4. ARM CRITERIA FOR EACH LIVE FLIP IN THE CRITICAL-PATH SEQUENCE

Each row below specifies the exact query or artifact AND the numeric gate that licenses the step. No step goes live without its specific evidence.

### 4.1 N1 — Cycle-summary attribution (self-arming)

| ARM item | Artifact | Numeric gate |
|---|---|---|
| Merge criterion | `pytest tests/test_cycle_summary_attribution.py` | All tests GREEN |
| No live-behavior change | N/A | Confirmed by code inspection: log text only |

---

### 4.2 N2 — Dead-gate deletions (self-arming)

| ARM item | Artifact | Numeric gate |
|---|---|---|
| Merge criterion | `pytest tests/test_dead_gate_deletion.py` + `grep -rn "coverage_unlicensed_tail" src/` after N2 confirms the reject still fires via `capital_efficiency` | All tests GREEN; Milan-24C antibody: population-A bin still rejected after deletion |
| D6 guard confirmation | `test_coverage_unlicensed_tail_still_rejects_far_tail` GREEN | The intent is preserved |

---

### 4.3 N5 — σ_center producer fix shadow (self-arming for shadow; ARM-gated for live)

**Shadow activation (self-arming):**

| ARM item | Artifact | Numeric gate |
|---|---|---|
| G0 tests pass | `pytest tests/test_sigma_center_fit.py` | All tests GREEN |
| G3 byte-identity | diff live q_lcb on 200 receipts with candidate=false vs today | Zero diff |
| D3 diagnostic | `docs/evidence/planning_2026-06-14/sigma_center_diagnostic.md` exists, names why `anchor_sigma_c` is pinned at 3.0 | File present, non-empty, cites source file:line |

**Live promotion (operator-ARM-gated):**

| ARM item | Artifact | Numeric gate |
|---|---|---|
| G2 forward-fill validation | `sigma_center_fit.json._meta.promotion_stats` | Shadow ring q_lcb in `[0.01, 0.15]` for all `FUSED_NORMAL_FULL ∧ q_point>0.05` cells (no cells producing crushed ≈0 or inflated >q_point) |
| E2 verdict on shadow | `edge_location_report.json` dated, `sigma_center_shadow` band | At least one ring-dist band shows `n_events ≥ 5` (may still be INSUFFICIENT_DATA; confirms the shadow is producing data for G4) |
| Operator sign-off | Operator reviews the two artifacts above | Explicit authorization |

---

### 4.4 N6 — σ-shape point-q live promotion (operator-ARM-gated)

| ARM item | Artifact | Numeric gate |
|---|---|---|
| G0 tests pass | `pytest tests/test_sigma_shape_point_q.py` | All tests GREEN |
| G2 forward-fill validation | `sigma_scale_fit.json._meta.promotion` field | `mode_bin_ratio ∈ [0.85, 1.15]` on holdout settlements; `tail_ratio` improves from ≥5× toward ≤2× on the same holdout |
| Point-q normalization | `test_point_q_sum_to_one` GREEN | Σq ∈ [0.99, 1.01] for all (city, target_date) pairs in the holdout |
| Ring mass increase | `test_ring_bin_mass_increases` GREEN | Quantified mass shift: the `exact` bin gains ≥0.01 in q_point on the synthetic over-confident family |
| Operator sign-off | Operator reviews `sigma_scale_fit.json._meta` stats | Explicit authorization |

**NOTE:** N6 is the first live-behavior node. If N6 is reverted after going live, N7's clamp ceiling drops (the UP arm's maximum rises from `q_point` which would be lower without N6). Revert both together if N6 is pulled.

---

### 4.5 N7 — Bidirectional q_lcb shadow activation (self-arming) and live promotion (ARM-gated)

**G1 gate (must pass before N7 is committed):**

| ARM item | Artifact | Numeric gate |
|---|---|---|
| Sub-population backtest | `docs/evidence/planning_2026-06-14/G1_subpopulation_backtest.md` with the query output | R/E ≥ 2.0 on `FUSED_NORMAL_FULL ∧ q_point>0.05` → UP arm warranted; R/E < 1.5 → UP arm abandoned, emit law-1 DONE |

**Shadow activation (self-arming, given G1 warrants it):**

| ARM item | Artifact | Numeric gate |
|---|---|---|
| G0 tests pass | `pytest tests/test_settlement_calibrated_qlcb.py` (7 tests) | All GREEN |
| G3 byte-identity | diff `apply_settlement_coverage` output on 200 receipts with shadow flag OFF vs today | Zero diff |
| Population A stays zero | Runtime metric logged each cycle: `shadow_would_admit_rate[dist>=tail]` | ≈0% throughout shadow |
| Population C stays zero | `shadow_would_admit_rate[q_point<price]` | ≈0% throughout shadow |

**Live promotion (operator-ARM-gated):**

| ARM item | Artifact | Numeric gate |
|---|---|---|
| G4 E2 band LICENSE | `edge_location_report.json` with at least one ring band entry | `band.verdict == "LICENSED"` AND `band.n_events >= 30` AND `band.lo90_realized_minus_price > 0` AND `band.model_brier < band.market_brier` |
| E2 JSON dated and auditable | The JSON artifact | `generated_at` ISO-8601 present; `settlement_date_range` covers ≥24 months; `n_verified_settlements_used` ≥ 1000 |
| Population A + C regression check | `edge_location_report.json.population_checks` | `A_would_admit_rate ≈ 0` (< 0.01) AND `C_would_admit_rate ≈ 0` (< 0.01) for the shadow period |
| Operator sign-off | Operator reviews G4 artifact | Explicit authorization |

---

### 4.6 N8 — Submit-lane observability (self-arming)

| ARM item | Artifact | Numeric gate |
|---|---|---|
| G0 tests pass | `pytest tests/test_no_submit_receipt_lane_stamp.py` | All GREEN |
| Go-forward receipts | After merge: `SELECT COUNT(*) FROM edli_no_submit_receipts WHERE submit_lane IS NULL AND created_at > [merge_date]` | Result = 0 within one trading cycle |

---

### 4.7 N9 — Mode-flip re-decision live (operator-ARM-gated, sequencing-gated)

**Sequencing pre-condition (hard gate):**

| ARM item | Artifact | Numeric gate |
|---|---|---|
| N7 shadow must be producing ring admissions | E2 shadow report showing ≥1 ring-band row with n_events ≥ 5 | At least one shadow would-admit on a ring bin before N9 ships |
| N7 is at minimum shadow-active | shadow flag ON; population A/C metrics nominal | Verified from runtime logs |

**G0 tests:**

| ARM item | Artifact | Numeric gate |
|---|---|---|
| All W-S2 tests pass | `pytest tests/test_submit_mode_redecision.py` (6 tests) | All GREEN |
| Over-loosening guard | `test_mode_flip_aborts_when_neither_mode_clears` and `test_missing_proof_mode_still_fails_closed` GREEN | Confirmed |

**ARM criterion:**

| ARM item | Artifact | Numeric gate |
|---|---|---|
| Operator sign-off on sequencing | Operator confirms N7 is shadow-active AND the admitted population is NOT dominated by settlement-dead cheap-tail | Explicit authorization; verified from the E2 shadow report showing ring-band composition |

---

### 4.8 G5 — Live promotion to DONE

| ARM item | Artifact | Numeric gate |
|---|---|---|
| Settlement proof | `docs/evidence/settlement_proof_[date].json` | `n_fills ≥ 30` AND `lo90_win_rate_after_fee > 0.51` AND `model_brier < market_brier` |
| Continuity check | The win-rate is stable in the rolling-30 window | Rolling win-rate ≥ 0.51 after fee for the most recent 30 fills (not just the first 30) |
| Event-level dedup confirmed | The proof artifact carries `dedup_confirmed: true`, asserting each `(city,target_date,bin)` appears once | INV-CAL-1 |
| Walk-forward confirmed | `earliest_fill_date ≥ N7_live_date` | No pre-live regret rows in the count |
| Operator authorization | Final review of `settlement_proof_[date].json` | Explicit authorization |

If G5 fails (win-rate ≤ 0.51 after fee at n ≥ 30): revert N7 to shadow, emit dated `ring_edge_not_survived_fee.json` with fill IDs + settlement IDs + observed rate + CI. This is a law-1 DONE verdict: the market is efficient at 1¢ friction. Do NOT loosen `capital_efficiency`.

---

## 5. SUMMARY TABLE: THE COMPLETE GATE LADDER

| Step | Node | Gate type | Artifact | Numeric threshold | Operator ARM? |
|---|---|---|---|---|---|
| Wave 0 | N1, N2, N3, N8 | G0 RED-on-revert | pytest output | All tests GREEN | No |
| Wave 1 | N4 (E2) | G0 | pytest output | All 6 tests GREEN | No |
| Wave 2a | N5 shadow | G0 + G3 + D3 diagnostic | pytest + byte-diff + diagnostic doc | Zero diff; doc present | No |
| Wave 2b | **G1** | Sub-population backtest | `G1_subpopulation_backtest.md` | R/E ≥ 2.0 → UP arm ships; R/E < 1.5 → law-1 DONE | No (automated query) |
| Wave 2c | N6 live | G0 + **G2** | `sigma_scale_fit.json._meta` | mode-bin ∈[0.85,1.15]; tail ≤2× | **YES** |
| Wave 2d | N7 shadow | G0 + G1 + G3 | pytest + runtime metrics | All GREEN; pop A/C ≈0% | No |
| Wave 3a | **G4 LICENSE** | E2 band verdict | `edge_location_report.json` | n≥30 ∧ lo90>0 ∧ model<market | No (automated) |
| Wave 3b | N7 live | G4 + **ARM** | E2 JSON | Band LICENSED | **YES** |
| Wave 3c | N9 live | G0 + sequencing + **ARM** | pytest + shadow report | N7 shadow active; ring admissions present | **YES** |
| Wave 4 | N11 live | G0 + G4 + **ARM** | pytest + E2 | Dist-0/1 band DONE | **YES** |
| Wave 5 | **G5 DONE** | Settlement proof | `settlement_proof_[date].json` | n≥30 ∧ lo90>0.51 after fee ∧ model<market | **YES** |

---

## 6. WHAT CANNOT SUBSTITUTE FOR SETTLEMENT EVIDENCE

For the avoidance of doubt, the following are NOT evidence of DONE, however favorable:

- Fill rate or submission count (a filled bad bet is worse than no fill)
- EDLI cycle EV (display EV, not settlement)
- Regret-event wins (simulated counterfactuals, not money)
- Shadow band metrics alone (shadow is the gate to live, not the gate to DONE)
- A single fill settling as a winner (n < 30)
- Model-back-test win rates (in-sample; law 5: settlement truth only)
- Any metric from `zeus-world.db.settlements` (that table is VERIFIED EMPTY — `diagnosis_confirmation.md:108`)

The only admissible evidence is: a VERIFIED settlement in `zeus-forecasts.db.settlement_outcomes` for a trade that appears in `zeus_trades.db` with a fill date ≥ N7_live_date, de-duplicated to one event per `(city, target_date, bin)`, accumulated to n ≥ 30 such events, with `lo90 > 0.51` after the 1¢ fee and `model_brier < market_brier`.

*End of P3 verification. Read-only planning; no production code or daemon changed. All gates are testable before the next step; no promotion is declared without its named artifact at the specified threshold.*
