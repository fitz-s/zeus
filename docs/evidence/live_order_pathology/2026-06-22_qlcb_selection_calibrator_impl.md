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

| File | Lines | Role |
|---|---|---|
| `src/decision/selection_calibrator.py` | new (~330) | Runtime serving rule. `apply_selection_calibrator(...)` → `CalibratorVerdict`. Cell `(side, lead_bucket, bin_class, raw_prob_bucket)`. Serves Wilson 95% lower bound of realized settlement hit-rate as the admission q_lcb. Helpers: `cell_key`, `lead_bucket`, `raw_prob_bucket`, `beta_lower_bound_95`, `isotonic_nondecreasing`, `load_artifact`. |
| `scripts/fit_selection_calibrator.py` | new (~310) | Walk-forward offline fitter — the artifact's only writer. `SettledDecisionRow`, `build_rows` (reuses `fit_sigma_scale` parse/join + freshest-per-lead dedup), `rows_strictly_before` (no-leak primitive), `fit_cells` (+ isotonic monotone-in-raw-prob), `_project_monotone`. |
| `scripts/selection_calibrator_forward_validation.py` | new (~290) | Forward-validation harness (read-only). 3-layer report + decisive test. |
| `tests/decision/test_selection_calibrator.py` | new (12 tests) | Runtime serving-rule contract. |
| `tests/test_fit_selection_calibrator.py` | new (5 tests) | Fitter no-leak + monotone + adverse-selection recovery. |
| `src/state/db_writer_lock.py` | +2 allowlist lines (688-690 region) | Read-only `?mode=ro` justification for the two new scripts (same posture as `fit_sigma_scale.py`). |

## 3. Method, features, fail-closed logic

- **Features (cell key):** `side` (YES=bin hits / NO=bin misses), `lead_bucket` (L1/L2_3/L4P, mirrors the spine), `bin_class` (modal/nonmodal; harness uses distance d0/d1/d2/d3p/tail), `raw_prob_bucket` (uniform 0.05 grid on the RAW SIDE prob — the admission signal). `admission_margin` (carries price) is accepted for provenance ONLY and never changes `q_safe` (asserted by `test_no_price_anchoring_q_is_single_authority`).
- **Bound:** the artifact persists `(n, hit_rate)`; the runtime serves `beta_lower_bound_95` = the one-sided Wilson 95% lower bound (same math as the OOF guard, single source). Monotone non-decreasing in raw prob via isotonic (PAVA) projection in the fitter.
- **Walk-forward no-leak:** rows accumulate in settlement-time order; `rows_strictly_before(rows, T)` enforces strict `<` (a row settled at-or-after T is a leak and is excluded — `test_walk_forward_no_leak_rejects_at_or_after_boundary`).
- **Fail-closed (NOT inert — unlike the OOF guard):** absent / malformed / stale-posterior-version / under-min-N / missing-cell → `q_safe=0, trade=False, abstained=True`. The live admission path emits NO new entries; it NEVER falls back to the raw center-bootstrap q_lcb. (5 fail-closed tests.)
- **Operator-law compliance:** raw q stays the single probability authority; no price-anchoring/capping; the haircut magnitude is LEARNED from the settled hit-rate, never hard-coded; single-q, no shadow model.

## 4. Test results

```
tests/decision/test_selection_calibrator.py ............ [12 passed]
tests/test_fit_selection_calibrator.py ..... [5 passed]
+ adjacent untouched: tests/decision/test_qlcb_reliability_guard.py, tests/test_fit_sigma_scale.py
+ tests/test_db_writer_lock.py (allowlist antibody): 20 passed
TOTAL new+adjacent: 34 passed in 29.55s
```

The blocking test confirms a buy_no cell graded at realized 0.679 → served q_safe ≈ 0.59 (Wilson LB
over n=104), which fails a 0.70 NO cost → toxic losers blocked. The genuine cheap buy_yes (0.46 over
n=200 → LB ~0.39) clears a 0.30 cost → preserved.

## 5. Forward-validation harness output (real settled data)

Invocation:
```
python3 -m scripts.selection_calibrator_forward_validation \
  --fcst state/zeus-forecasts.db --world state/zeus-world.db --out <report>.json
```

Corpus = 3608 side-decisions (`posterior_method=openmeteo_ecmwf_ifs9_bayes_fusion`). Executed-with-q = 91.

- **Layer 3 admission (end-of-window corpus fit):** old gate (admits all) after-cost EV **−1.95**;
  new selection calibrator after-cost EV **+1.29**. new_admit=76, new_block=15; of 33 losers it blocks 9.
- **Walk-forward (as-of-decision):** new_admit=0 (the recent executed bets have <min_N prior-settled
  corpus rows per cell → fail-closed) — correct fail-closed behavior, but over-blocks early-window.
- **Decisive test:** notional-weighted selection gap **−0.199** → adverse SELECTION confirmed.

## 6. Open methodological fork (consult REQ-20260622-154643, pending)

The corpus fit (population A, robust N) is EV-positive (+1.29 vs −1.95) but does not contain the full
execution-conditioning effect, so it blocks only the worst losers. The admission-conditioned
population (B, `settlement_attribution`) contains the −20pp effect but is thin (n~91) and is a
feedback loop. A frontier consult was dispatched on whether A, B, or a principled hybrid (e.g.
admission-predicate-as-feature / IPW-Heckman selection correction / N-weighted shrinkage) is correct.
**Recommendation deferred to that result** — see §7.

## 7. Verdict

PENDING consult — to be finalized.
