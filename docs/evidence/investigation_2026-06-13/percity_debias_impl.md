# Per-City Representativeness De-Bias — Implementation (law-8 foundation fix)

**Date:** 2026-06-14
**Mode:** Implementation in an ISOLATED worktree. NOT deployed (no merge, no restart). Belief
correction only — no trading-logic change beyond the anchor center de-bias.
**Roots:** `cold_bias_metadata_root.md` (ROOT = per-city 9km grid-cell-vs-settlement-station
representativeness offset, Tokyo −2.18°C … Karachi +2.48°C, two-sign, **lead-stable**,
**raw-anchor-resident**), `percity_corrected_oos.md` (per-city δ is the right SHAPE but
**OVERFITS** when fit on the thin live `single_runs` anchor — n_prior 1–4/city → net-worse; the
decisive missing ingredient is **history depth**, not a better estimator).

---

## 1. THE FIX IN ONE LINE

Restore the per-city center de-bias that was deleted at
`replacement_forecast_materializer.py:1455` (settlement-refuted in its naive form), but make it
**SAFE on thin data**: fit δ_city on the **full `previous_runs` anchor history** (deep, n=23..890
settled rows/city) instead of the thin live `single_runs` anchor (~6 dates), and gate it with an
**activation threshold + empirical-Bayes shrink + a do-no-harm walk-forward check** so it never
overfits.

## 2. WHY THE PRIOR ATTEMPT FAILED, AND WHY THIS ONE DOES NOT

`percity_corrected_oos.md` fit δ_city on the live `single_runs` anchor: only 6 settled dates →
n_prior 1–4/city → a 1–4-point median is high-variance noise → pooled exact-bin hit
per-city 0.221 < orig 0.240 (net-WORSE). The structure was real; the **estimator was starved**.

Decisive data fact (this session, `state/zeus-forecasts.db`, read-only):

| endpoint | distinct target dates | per-city settled n (high) |
|---|---:|---|
| `single_runs` (live anchor) | **9** | 1–6 |
| `previous_runs` (fixed-lead train) | **198** (2025-12-01 → 2026-06-16) | **23 … 890**, median 384 |

The representativeness offset is a **spatial** property (9km cell vs station), so it is
**lead-stable** (verified: Seoul −1.10/−1.20/−1.10 at leads 1/2/3; Tokyo −0.70/−0.90/−0.75;
Karachi +1.00/+0.80/+0.50). That licenses **lead-pooling** the `previous_runs` history → an
order-of-magnitude more samples per city, which is exactly the depth the prior attempt lacked.
The recovered δ_city match the root doc's offsets (Tokyo −0.69, Seoul −1.19, Singapore −1.39,
Hong Kong −1.29, Karachi +0.69).

## 3. WHERE IT APPLIES IN THE PIPELINE

Single insertion point: `src/data/replacement_forecast_materializer.py:1455` — the `bias_shift_c`
binding inside `_insert_posterior`. This is the **existing** per-city center-correction hook
(contract `corrected = raw − bias_shift_c`, `ecmwf_aifs_sampled_2t_probabilities.py:377-403`),
which was set to `None` when the naive version was deleted.

Setting `bias_shift_c = δ_city`:
- subtracts δ_city from the raw OM9 anchor center →
  `anchor_value_corrected_c = raw − δ_city` (line 1465), which is the **prior center fed into the
  BAYES_PRECISION_FUSION fusion** (`anchor_z_corrected`, capture →
  `eb_bias(resids, parent_bias)`), so the de-bias **propagates into the fused posterior μ\***; and
- corrects the AIFS member votes consistently (the same shift), even on the fusion-OFF path.

Sign: δ_city = `anchor − settlement`. A COLD anchor (δ<0) → `raw − δ = raw + |δ|` (warms); a HOT
anchor (δ>0) cools. This matches both the `bias_shift_c` contract and the root doc's bias sign.

Layering is unchanged downstream: q_lcb settlement floor, EMOS, σ-scale, bin integration all run
on the corrected center exactly as before.

## 4. N_min, THE EB SHRINK, AND THE ACTIVATION GUARD

- **δ_city = λ · median(anchor_c − settlement_c)**, robust median over the city's full lead-pooled
  `previous_runs` VERIFIED history.
- **EB shrink toward 0 by the offset's own SE:** `λ = τ² / (τ² + SE²)`, where τ² is the
  between-city dispersion of the true offset (variance of the per-city medians) and
  SE = sd/√n. Thin/noisy cities (large SE) shrink gently toward 0; well-sampled cities (SE→0)
  get λ→1 → full correction. (Measured τ ≈ 0.72°C; well-sampled cities land at λ ≈ 0.97–0.99.)
- **Activation guard — N_min = 30** (operator-overridable `--n-min`). Chosen from the data: at the
  observed per-city residual sd (~1.3–3.1°C), n=30 gives SE ≈ 0.24–0.57°C, i.e. the offset is
  resolved to **well under one 1°C settlement bin**. A city is `activated` ONLY when n ≥ N_min;
  below N_min the loader returns None → the materializer leaves `bias_shift_c = None` → the
  current family-level de-bias (**do no harm**). On the current DB this catches exactly one city
  (Ankara, n=23) and activates 50/51.
- **do-no-harm walk-forward gate (family level):** the fitter does a 70/30 expanding-window split,
  fits δ on the train fold, scores the held-out tail's |anchor − settlement| with vs without the
  activated correction, and records `walk_forward.do_no_harm = corrected_MAE ≤ raw_MAE`. The
  loader applies δ_city ONLY when this is True. (HIGH passes: corr 1.6319 ≤ raw 1.7269 on 13,100
  test cells; LOW fails by 0.003°C on a sparse 9-city history → gated off, mirroring
  `grid_representativeness.get_offset` which also fails closed on non-high.)

## 5. ARTIFACT SCHEMA (`state/anchor_representativeness_debias.json`)

Fitted, auditable, re-fittable (operator law: no magic constants). Gitignored generated state
(`/state/*`), like `sigma_scale_fit.json` / `bias_scale_fit.json`.

```
{ "_meta": { schema:"anchor_representativeness_debias",
             authority:"anchor_grid_representativeness_eb_shrunk_v1",
             anchor_model:"ecmwf_ifs", endpoint:"previous_runs",
             residual:"anchor_forecast_value_c - settlement_value_in_C (VERIFIED)",
             estimator:"EB-shrunk robust per-city median; lambda=tau^2/(tau^2+SE^2); lead-pooled",
             activation:"activated iff n>=n_min(30); else family-level fallback",
             sign:"delta_c = anchor - settlement; corrected = raw - delta_c" },
  "families": {
    "high": { fitted:true, tau_between_city:0.72, n_min:30, n_cities:51, n_activated:50,
              walk_forward:{ cut_date, n_test_cells, raw_mae_c, corrected_mae_c, do_no_harm:true },
              cities: { "<City>": { delta_c, median_raw_c, mean_raw_c, n, sd_c, se_c,
                                    lambda_shrink, activated } } },
    "low":  { ... walk_forward.do_no_harm:false (gated off on current sparse history) } } }
```

## 6. FILES CHANGED (file:line)

| file | change |
|---|---|
| `scripts/fit_anchor_representativeness_debias.py` | NEW fitter: EB-shrunk, activation-guarded, lead-pooled δ_city + walk-forward do-no-harm report. Read-only DB (`mode=ro&immutable=1`), writes only the JSON artifact. |
| `src/calibration/anchor_representativeness_debias.py` | NEW loader `get_city_debias_c(city, metric)`: fail-soft, cached; returns δ_city ONLY for an activated, do-no-harm-gated **high** cell, else None. |
| `src/data/replacement_forecast_materializer.py:1455` | `bias_shift_c = None` → `bias_shift_c = get_city_debias_c(request.city, metric)` (fail-soft try/except). The only live-path edit. |
| `src/state/db_writer_lock.py` (allowlist) | allowlist the new read-only fitter (writer-lock antibody). |
| `architecture/script_manifest.yaml`, `architecture/test_topology.yaml` | registry mesh-maintenance for the new script + test. |
| `tests/test_anchor_representativeness_debias.py` | NEW RED-on-revert test (5 cases). |

## 7. TEST + RESULTS

`tests/test_anchor_representativeness_debias.py` — **5 passed**:
- **T1 (correct well-sampled):** Seoul (activated, n=834) → δ=−1.19; applied to a known cold cell
  (raw 28.8, settlement 30.0) → corrected 29.99 → crosses into the winning 30°C bin
  (`round(corrected)==round(settlement)` while `round(raw)!=`). **REDs if reverted to None.**
- **T2 (do-no-harm thin):** Ankara (n=23<N_min) → None → family-level fallback. **REDs if a thin
  city is corrected** (the exact overfit the prior version was refuted for).
- **T3:** fitter↔loader artifact round-trip.
- **T4:** LOW/non-high fails closed; a `do_no_harm=False` family is not applied even for an
  activated city.
- **T5:** missing artifact → None → byte-identical to today (artifact-gated, no shadow flag).

No-regression on existing fusion/anchor suites: `test_openmeteo_ecmwf_ifs9_aifs_soft_anchor`,
`test_bayes_precision_fusion_port_fidelity`, `test_bayes_precision_fusion_thin_anchor_retained`,
`test_bayes_precision_fusion_history_provider_materializer_wiring`,
`test_bayes_precision_fusion_no_leak_history_join`,
`test_bayes_precision_fusion_materializer_uses_persisted_current_rows_not_network`,
`test_bayes_precision_fusion_current_capture_missing_blocks_or_falls_back_with_reason`
— **all pass**. (The 3 `test_grid_representativeness` failures are pre-existing and unrelated:
the worktree `state/` lacks the gitignored legacy `grid_representativeness_offset.json` and
`sklearn` is absent; my change touches neither that loader nor `event_reactor_adapter.py`.)

Fitted-artifact sanity (current DB): HIGH cities=51 activated=50 τ=0.72;
WF cut=2026-04-15 raw_mae=1.7269 → corr_mae=1.6319 do_no_harm=True.

## 8. DEPLOY PROCEDURE

This is ARTIFACT-GATED — there is **no flag** (per the no-shadow law). The loader returns None
when the artifact is absent (current live state) → byte-identical to today.

1. **Merge** this worktree (the loader, the fitter, the materializer one-line wiring, the
   allowlist + registry entries, the test). No restart yet — with no artifact present the path is
   inert.
2. **Fit on the live DB:** `python3 scripts/fit_anchor_representativeness_debias.py`
   (writes `state/anchor_representativeness_debias.json`). Review the `high` family's
   `walk_forward.do_no_harm` (must be **true**) and the `n_activated` / per-city `activated` set.
   Do NOT promote if HIGH `do_no_harm` is false.
3. **Restart** the materializer daemon so the loader picks up the new artifact (module-level cache
   is per-process).
4. **Confirm δ_city is applied:** for an activated city (e.g. Seoul), read the freshest
   `forecast_posteriors` provenance — the persisted `anchor_value_c` should equal
   `raw_anchor − δ_city` (and the provenance carries the shifted center), and the fused μ\* should
   move warmer (cold cities) / cooler (hot cities) by ≈|δ_city|. A thin city (n<30) must be
   unchanged.
5. **Re-grade as history accumulates:** re-run the fitter weekly. As each city's `previous_runs`
   history grows, λ→1 (fuller correction) and any city below N_min crosses the activation
   threshold. Re-run the OOS edge test from `percity_corrected_oos.md` against fresh dense
   orderbook capture once the **live** anchor reaches ~1 month of per-city settled coverage — the
   accuracy fix is in; the tradeable-edge claim remains gated on liquidity + history depth there.

---

## RAW (deciding numbers)
- History depth: `previous_runs` high = 198 dates / per-city n 23..890 (median 384) vs
  `single_runs` 9 dates / n 1..6 — the depth the prior overfit lacked.
- Offset lead-stability: Seoul −1.10/−1.20/−1.10, Tokyo −0.70/−0.90/−0.75, Karachi
  +1.00/+0.80/+0.50 at leads 1/2/3 → lead-pooling licensed.
- Fitted δ_city (EB-shrunk): Tokyo −0.69, Seoul −1.19, Singapore −1.39, Hong Kong −1.29,
  Taipei −1.26, Karachi +0.69, Chengdu +0.10, Seattle −0.24, NYC −0.54, Houston −0.84,
  Wellington −1.00; thin Ankara (n=23) NOT activated.
- HIGH walk-forward: raw_mae 1.7269 → corrected_mae 1.6319 (13,100 test cells) → do_no_harm=True.
- Insertion: `replacement_forecast_materializer.py:1455`; loader
  `src/calibration/anchor_representativeness_debias.py`; fitter
  `scripts/fit_anchor_representativeness_debias.py`; test
  `tests/test_anchor_representativeness_debias.py` (5 passed).
```
```
*End. Prepared in worktree, NOT deployed.*
