<!--
Created: 2026-06-22
Last audited: 2026-06-22
Authority basis: selection-aware settlement q_lcb calibrator
  (frontier consult REQ-20260622-151741; live_order_pathology 2026-06-22)
-->
# Selection-aware settlement q_lcb calibrator — implementation + forward-validation

## 1. Diagnosis verification (against source + live DBs, read-only)

Every claim in the build brief was confirmed before any code was written.

| Claim (brief) | Source check | Verdict |
|---|---|---|
| q_lcb is a 5th-pct CENTER-uncertainty bootstrap that does not cover post-selection error | `src/data/replacement_forecast_materializer.py:_build_fused_q_bounds` (1633-1762): draws μ_i~N(μ*, center_σ), per-bin 5th-pct after Path-A simplex renorm | CONFIRMED |
| YES→side q_lcb seam in event_reactor_adapter, before edge/FDR/Kelly | `_side_q_lcb_from_yes_samples` (12964), `_replacement_no_lcb_for_bin` (12619), `_replacement_authority_probability_and_fdr_proof` (12650): both build `lcb_by_direction`+`prefilter`(edge_lcb>0)+`p_values`(FDR) | CONFIRMED |
| OOF reliability guard is price-blind (keyed on q_lcb bucket) | `src/decision/qlcb_reliability_guard.py:cell_key` keys `(metric,lead,side,bin_position,q_lcb_bucket)`; wired in `family_decision_engine.py:739`, NOT event_reactor_adapter | CONFIRMED |
| Live σ artifact k=0.671,w=0.149 family C; k≈1.58 comment is stale | `state/sigma_scale_fit.json`: C `{k:0.671,w:0.149,n_cells:614}` | CONFIRMED |
| buy_no slice over-confident ~20pp | `settlement_attribution` buy_no n=104: YES-belief-in-bin **0.126**, realized-in-bin **0.327**, market-in-bin **0.298**, NO-win **0.673**, avg NO fill **0.702** | CONFIRMED (n grew 84→104; identical signature) |

**Decisive selection test** (admission-conditioned vs matched full-corpus, same raw-NO-prob bucket):
admitted NO bins realize **~20pp worse** than corpus bins at the same bucket (notional-weighted gap
**−0.199**). pb16: admitted 0.571 vs corpus 0.808; pb18: admitted 0.600 vs corpus 0.949. This proves
the loss is **adverse SELECTION** (the gate picks the toxic subset), not a uniform q-only defect.

## 2. Files changed / added

| File | Role |
|---|---|
| `src/decision/selection_calibrator.py` | Runtime serving rule + EB math + seam helper. `apply_selection_calibrator(...)` → `CalibratorVerdict` (serves persisted EB `q_safe_lb` for v2 cells / Wilson LB for v1). `eb_lower_bound` (BetaInvCDF, pure-Python), `beta_inv_cdf`/`betainc_regularized`, `isotonic_nondecreasing_weighted` (weighted PAVA), `cell_key`/`raw_prob_bucket`/`lead_bucket`, `selection_calibrated_side_lcb` (flag-gated seam, default OFF). |
| `scripts/fit_selection_calibrator.py` | Walk-forward offline fitter (artifact's only writer). `SettledDecisionRow`, `build_rows`, `rows_strictly_before` (no-leak), `fit_cells` (v1), `fit_eb_cells` (v2 hybrid), `learn_tau` (rolling prequential), `_corpus_prior_rates`, `_selected_counts`, weighted `_project_monotone`. |
| `scripts/selection_calibrator_forward_validation.py` | Forward-validation harness (read-only). 3-layer report + decisive test + STEP-4 3-way walk-forward replay (`threeway_walk_forward`); `target_date+1` no-leak boundary. |
| `tests/decision/test_selection_calibrator.py` | Runtime serving-rule + seam-helper contract (16 tests). |
| `tests/decision/test_selection_calibrator_eb.py` | Weighted PAVA + EB beta-binomial lower bound + v2 serving (7 tests). |
| `tests/test_fit_selection_calibrator.py` | Fitter no-leak + monotone + adverse-selection recovery + EB fit + learn_tau (11 tests). |
| `src/state/db_writer_lock.py` | +2 allowlist lines (read-only `?mode=ro`, same posture as `fit_sigma_scale.py`). |
| `docs/evidence/live_order_pathology/2026-06-22_qlcb_selection_forward_validation.json` | STEP-4 3-way walk-forward report (real shared data). |

## 3. Method, features, fail-closed logic

- **Features (cell key):** `side` (YES=bin hits / NO=bin misses), `lead_bucket` (L1/L2_3/L4P, mirrors the spine), `bin_class` (modal/nonmodal; harness uses distance d0/d1/d2/d3p/tail), `raw_prob_bucket` (uniform 0.05 grid on the RAW SIDE prob — the admission signal). `admission_margin` (carries price) is accepted for provenance ONLY and never changes `q_safe` (asserted by `test_no_price_anchoring_q_is_single_authority`).
- **Bound:** the artifact persists `(n, hit_rate)`; the runtime serves `beta_lower_bound_95` = the one-sided Wilson 95% lower bound (same math as the OOF guard, single source). Monotone non-decreasing in raw prob via isotonic (PAVA) projection in the fitter.
- **Walk-forward no-leak:** rows accumulate in settlement-time order; `rows_strictly_before(rows, T)` enforces strict `<` (a row settled at-or-after T is a leak and is excluded — `test_walk_forward_no_leak_rejects_at_or_after_boundary`).
- **Fail-closed (NOT inert — unlike the OOF guard):** absent / malformed / stale-posterior-version / under-min-N / missing-cell → `q_safe=0, trade=False, abstained=True`. The live admission path emits NO new entries; it NEVER falls back to the raw center-bootstrap q_lcb. (5 fail-closed tests.)
- **Operator-law compliance:** raw q stays the single probability authority; no price-anchoring/capping; the haircut magnitude is LEARNED from the settled hit-rate, never hard-coded; single-q, no shadow model.

## 4. Test results

```
tests/decision/test_selection_calibrator.py ............ [16 passed: serving rule + seam helper]
tests/decision/test_selection_calibrator_eb.py ......... [ 7 passed: weighted PAVA + EB beta-binomial]
tests/test_fit_selection_calibrator.py ................. [11 passed: no-leak + EB fit + learn_tau]
tests/test_db_writer_lock.py (allowlist antibody) ...... [20 passed]
TOTAL new + antibody: 52 passed in 16.12s   (adjacent test_qlcb_reliability_guard / test_fit_sigma_scale also green)
```

Unit-level confirmations: a buy_no cell graded at realized 0.679 → served q_safe ≈ 0.59 (fails a 0.70
NO cost → toxic blocked); the EB lower bound is conservative + monotone in evidence; weighted PAVA
prevents an n=1 cell dragging an n=400 cell; the [BLOCKER] version strings agree; all fail-closed
paths (absent/malformed/stale/thin-selected/missing-cell) emit no-trade, never a raw-bootstrap fallback.

## 5. Forward-validation harness output (real settled data)

Invocation:
```
python3 -m scripts.selection_calibrator_forward_validation \
  --fcst state/zeus-forecasts.db --world state/zeus-world.db --out <report>.json
```

Corpus = 3608 side-decisions (`posterior_method=openmeteo_ecmwf_ifs9_bayes_fusion`). Executed-with-q = 91.

- **Decisive test (end-of-window):** notional-weighted selection gap **−0.21** → adverse SELECTION
  confirmed (admitted bets realize ~21pp worse than matched corpus bins at the same raw-prob bucket;
  NOT a uniform q-only defect).
- **No-leak boundary fix:** `settlement_outcomes.settled_at` is the GRADING BATCH timestamp (clusters
  on backfill dates), not when the outcome became known. The harness uses `target_date + 1 day` as
  the as-of boundary so a decision at T consumes only markets RESOLVED before T.

## 6. STEP-1 reconstructability (gating) + the EB hybrid (consult REQ-20260622-154643)

The consult verdict: a **selection-conditioned hierarchical empirical-Bayes hybrid** (full corpus =
prior `p0_b`; executed/would-admit selected rows = likelihood `(w_s, n_s)`; beta-binomial shrinkage
with a LEARNED `tau` → `q_safe_lb = BetaInvCDF(0.05, tau·p0+w_s, tau·(1-p0)+n_s-w_s)`).

**STEP-1 result (CONCLUSIVE):** full would-admit EB is **NOT available** on the current regime.
`edli_no_submit_receipts` has 62,874 rich counterfactual rows (q_lcb_5pct, own-side cost, posterior_id,
direction…) — but ALL are the STALE regime: 62,789 `probability_authority=None` (old canonical, ends
06-10), 85 `replacement_0_1` (06-10..06-12), and every sampled posterior is
`openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` (SOFT-ANCHOR, not bayes_fusion). **ZERO no-submit
receipts reference a bayes_fusion posterior.** So the current-regime selected population = the
executed trades only (`settlement_attribution`, n=91 with q, 06-13..06-22). This forces the consult's
**executed-EB fallback + shadow-log**.

**EB implementation** (all consult findings addressed):
- `fit_eb_cells` (corpus prior + executed selected likelihood) + `learn_tau` (rolling-origin
  prequential NLL over the tau grid, learned not hard-coded) + `eb_lower_bound` (pure-Python
  BetaInvCDF, **no SciPy at runtime** — the fitter persists `q_safe_lb`).
- **[BLOCKER] version string** unified: runtime `DEFAULT_POSTERIOR_VERSION` == fitter `POSTERIOR_VERSION`
  == `openmeteo_ecmwf_ifs9_bayes_fusion` (+ regression test).
- **[MEDIUM] weighted PAVA** (`isotonic_nondecreasing_weighted`): a thin cell cannot drag a deep
  neighbour (+ test).
- **Fail-closed:** corpus alone NEVER licenses an admit cell; a v2 cell licenses only when
  `n_selected >= min_n` (`EB_THIN_SELECTED` no-trade otherwise).
- Anti-memorization: no city/date/condition_id/token features; `selection_policy_version` frozen.

## 7. STEP-4 walk-forward 3-way replay (THE GATE — no look-ahead)

As-of-decision, artifact rebuilt from rows resolved strictly before each bet (`target_date+1` boundary).
`tau` learned = 100; min_n swept 5–30 (consult: min_n is a forward-validated hyperparameter).

| Population | admit | toxic-NO blocked | genuine-YES admit | EV_admitted | prequential nll/obs |
|---|---|---|---|---|---|
| pure-A corpus | 3 | 81/84 | 0/4 | **−1.09** | 0.747 |
| pure-B executed | 0 | 84/84 | 0/4 | 0.0 | 3.405 |
| **selected-EB** | 0 | 84/84 | 0/4 | 0.0 | **0.492** |

(min_n=5 best case: pure-A admits 4 incl. 1 genuine-YES, EV −1.10; selected-EB admits 3 toxic NO, EV −1.09.)

**Honest finding:** on the current-regime data (only 91 executed bets, corpus `known_at` spanning just
06-20..06-22), **no population produces a positive walk-forward admitted EV**, and none admits a
genuine cheap-YES, because the selected support per cell never reaches a trustworthy depth as-of-T.
The EB hybrid is the structurally-correct estimator — its prequential log-score (0.49/obs) dominates
pure-B (3.41) and beats pure-A (0.75), and it BLOCKS the toxic NO (84/84) — but it correctly
**fails closed** (near trade-nothing) rather than licensing on thin selected evidence. The corpus-fit
"EV +1.29" from the earlier end-of-window run was a LOOK-AHEAD artifact and does NOT count.

## 8. Verdict

The selection-aware EB calibrator is **CORRECT and SAFE, but NOT yet promotable to a positive-EV live
admit policy** on the current data. Specifically:
- It **blocks the toxic buy_no losers** (84/84 toxic-NO blocked walk-forward) and is fail-closed.
- It **does not over-trade**: it admits nothing it cannot license on settled selected evidence.
- It **cannot yet preserve genuine cheap-YES nor demonstrate forward EV>0**, because the current
  bayes_fusion regime has only ~91 executed bets and ~3 days of resolved corpus — too little selected
  support to license any admit walk-forward at an honest min_n.

**Recommendation:** SAFE TO DEPLOY IN BLOCK-ONLY / SHADOW MODE (flag `ZEUS_SELECTION_CALIBRATOR_LIVE`
default OFF). When ON it will deflate/abstain the toxic NO admissions (stopping the −23% bleed) while
fail-closing the rest — i.e. it stops the loss class without yet claiming new alpha. Concurrently
**shadow-log all current candidates** (would-admit population on the live regime) so a full would-admit
EB can be refit once ≥ a few hundred labelled selected rows accrue; re-run STEP-4 then to promote a
positive-EV admit policy. Do NOT promote an admit policy on the present thin data. The orchestrator
owns the flag flip and the artifact promotion.
