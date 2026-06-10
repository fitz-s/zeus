# Design vs Live EDLI Probability Chain — Per-Stage Divergence Audit

> **Pre-merge (2026-06-09):** describes the legacy baseline chain only. The strategy of record is now the replacement_forecast chain — authority `docs/authority/replacement_final_form_2026_06_09.md` (`u0r_bayes.fuse_u0r_posterior` → `emos.bin_probability_settlement`).

```
# Created: 2026-06-01
# Last reused or audited: 2026-06-01
# Authority basis: AGENTS.md §probability chain; docs/reference/zeus_math_spec.md v3;
#   src/engine/evaluator.py (design-faithful broad-cycle reference); HEAD 6fcd05a69f
# Mode: READ-ONLY audit (no edits, no git, no DB writes)
```

## Scope

The PROBABILITY half of the chain (AGENTS.md:13):

`51 ENS members → per-member daily max → Monte Carlo (sensor noise + ASOS rounding)
→ P_raw → Extended Platt (A·logit + B·lead_days + C) → P_cal → α-weighted Market
Fusion → P_posterior → Edge & Double-Bootstrap CI`

Three sources compared per stage:
- **DESIGN** — `docs/reference/zeus_math_spec.md`, AGENTS.md.
- **EVALUATOR** (design-faithful reference) — `src/engine/evaluator.py` (broad cycle).
- **LIVE EDLI** — `src/engine/event_reactor_adapter.py` event-bound kernel.

**Central structural fact**: both paths converge on the SAME compute engine —
`src/strategy/market_analysis.py::MarketAnalysis` and its `_bootstrap_bin` /
`_compute_posterior`, and the SAME `scan_full_hypothesis_family`. The divergences
are NOT separate re-implementations; they are differences in **what the EDLI path
hands to the shared MarketAnalysis constructor**. One omission (no `calibrator=`,
no `lead_days=`) silently changes the bootstrap interior.

---

## Per-stage table

| # | Stage | Design formula | Evaluator impl | Live EDLI impl | DIVERGENCE | Severity | file:line |
|---|-------|----------------|----------------|----------------|------------|----------|-----------|
| 1 | Monte Carlo (P_raw generator) | `floor(m+ε+0.5)`, ε~N(0,σ²), σ_F=0.5/σ_C=0.3, n=10,000 ×51 (§4.2-4.4) | `p_raw_vector_from_maxes(...)` default `n_mc=ensemble_n_mc()`=10,000 | `p_raw_vector_from_maxes(members,city,semantics,bins)` default `n_mc` | **NONE** — identical shared function, identical default n_mc, identical σ/rounding | OK | EDLI `event_reactor_adapter.py:3596`; eval `evaluator.py:4146`; fn `ensemble_signal.py:173,205` |
| 2 | P_raw vector | MC histogram → per-bin sum / N_total, normalized | `p_raw_vector_from_maxes` → normalize | same fn → `arr/arr.sum()` | **NONE** for the math. EDLI-only extra: optional pre-MC per-city bias subtraction (flag `edli_bias_correction_enabled`, default **OFF**) hoisted at `_market_analysis_from_event_snapshot` | OK (correction inert at default) | EDLI `:3318,3596-3602`; bias `:3487-3567` |
| 3 | **Extended Platt — POINT serve** | `P_cal = σ(A·logit(P_raw) + B·lead_days + C)` (§6.1) | `calibrate_and_normalize(p_raw, cal, lead_days_for_calibration, bin_widths)` | `calibrate_and_normalize(p_raw, cal, lead_days, bin_widths)` | **NONE** — same fn, full A·logit+B·lead_days+C; **B·lead_days IS present** in live serve. EDLI passes real `lead_days` from `_snapshot_lead_days` | OK | EDLI `:3689` + `:3633,3700`; eval `:4541`; fn `platt.py:218-233,328` |
| 3b | **Extended Platt — bias-corrected domain** | (no design carve-out) | n/a — evaluator has no per-city pre-MC bias hoist | When `_edli_bias_corrected` True → **identity Platt** (`p_cal = normalized p_raw`, skips fitted A/B/C) | EDLI-only forced-identity. **Inert at default** (`edli_bias_correction_enabled` OFF). Live risk only if flag flipped | LOW (gated OFF) | EDLI `:3624-3629` |
| 3c | Platt fallback (no fitted bucket) | n_eff<15 → use P_raw (§6.5) | `cal is None` → uncalibrated path | `cal is None` → identity (normalized p_raw) + WARN | Minor: EDLI normalizes-and-returns; evaluator routes an explicit uncalibrated branch. Same numeric outcome (identity) | LOW | EDLI `:3664-3688`; eval `:4548-4551` |
| 4 | **α-weighted Market Fusion** | `P_post = α·P_cal + (1−α)·P_market` (§7); BUT §15.7: `MODEL_ONLY_POSTERIOR_MODE` is the **intended default**, fusion shadow-only | `posterior_mode=MODEL_ONLY_POSTERIOR_MODE` → `compute_posterior` returns normalized P_cal, **market prior dropped** | `posterior_mode=MODEL_ONLY_POSTERIOR_MODE` → identical | **NONE between paths.** Market fusion is DISABLED in BOTH — an intentional `forecast_only` choice per §15.7/§15.8, NOT a lost EDLI stage | OK (by design) | EDLI `:3374`; eval `:5192`; fn `market_fusion.py:262-301` |
| 4b | α weight | `compute_alpha(...)` per decision (§7.2) | `alpha = compute_alpha(cal_level, spread, agreement, lead, …)` passed to MarketAnalysis | hardcoded `alpha = base_alpha.level1` | EDLI skips `compute_alpha`. **Inert** because MODEL_ONLY ignores α in the posterior. Would matter only if fusion were ever enabled | LOW (inert) | EDLI `:3363`; eval `:4970,5179` |
| 5 | P_posterior | model-only (per §15.7) | `MarketAnalysis._compute_posterior` model-only | same shared method | **NONE** — same object, same method, market-independent q | OK | `market_analysis.py:316-330` |
| 6 | **Edge & Double-Bootstrap CI** | resample members + redraw noise (σ_model) + **sample (A,B,C) Platt params (σ_parameter)**; recompute P_raw→P_cal→P_post→Edge; CI=[p5,p95]; p=mean(edge≤0) (§8.2) | passes `calibrator=cal` → bootstrap has `has_platt=True` → per-iter `σ(A·logit+B·lead_days+C)` with **bootstrap_params resampling** | passes **NO `calibrator=`** and **NO `lead_days=`** → `has_platt=False` → `p_cal_boot = p_raw` (**RAW, uncalibrated**); σ_parameter layer **absent**; `_lead_days=3.0` default unused | **MATERIAL.** EDLI bootstrap perturbs RAW p_raw, never the calibrated surface, and omits Platt-parameter uncertainty. n / percentiles / p-value formula identical, but the resampled quantity differs from the point q_posterior space | **HIGH** | EDLI ctor `:3356-3377` (no calibrator/lead_days); shared boot `market_analysis.py:752-788`; scan `market_analysis_family_scan.py:81,107` |
| 6b | Bootstrap n / percentiles / p-value | B=`edge_n_bootstrap()`, [5%,95%], p=mean(edge≤0) | `edge_n_bootstrap()`=200 | `edge_n_bootstrap()`=200 | **NONE** — same n, same `np.percentile(...,5/95)`, same `mean(edges≤0)` | OK | `config.py:535` + `settings.json:140`; boot `market_analysis.py:815-817` |
| 6c | q_lcb reconstruction | edge LCB → Kelly | edge-space LCB consumed directly | EDLI converts edge-LCB back to **probability space**: `q_lcb = ci_lower + cost_by_direction` | Path-specific re-derivation (valid given fixed-c_b bootstrap), not a math error; depends on 6's interior being correct | INFO | EDLI `:3111-3124` |

---

## Divergences ranked by q-impact

1. **[HIGH] Bootstrap CI is computed on RAW p_raw, not calibrated p_cal, and omits
   Platt-parameter (σ_parameter) sampling.** `_market_analysis_from_event_snapshot`
   (`event_reactor_adapter.py:3356-3377`) constructs `MarketAnalysis` with neither
   `calibrator=` nor `lead_days=`. Inside the shared `_bootstrap_bin`
   (`market_analysis.py:752-788`) this forces `has_platt=False`, so every bootstrap
   iteration sets `p_cal_boot_all = p_raw_all` — the raw Monte-Carlo distribution,
   never the `σ(A·logit+B·lead_days+C)` surface. Consequences:
   - The **point** q_posterior uses calibrated p_cal (stage 3, correct), but the
     **CI around it** is drawn in raw-probability space → the bootstrap distribution
     is centered/shaped differently from the point estimate it is supposed to bound.
   - Design §8.2's **σ_parameter** layer (sample A,B,C from `bootstrap_params`) is
     entirely absent → CI is too NARROW on the Platt-uncertainty axis but mis-located
     on the calibration axis. Net q-impact on `q_lcb_5pct` (the FDR p_value, the
     prefilter `ci_lower>0`, and the trade_score gate) is direction-dependent and
     can flip a candidate's admit/reject. This is the dominant probability-half
     divergence and is NOT gated by any flag — it is the always-on EDLI behavior.

2. **[LOW, gated OFF] Forced-identity Platt on the bias-corrected domain**
   (`:3624-3629`). When `edli_bias_correction_enabled` is ON, EDLI subtracts a
   per-city bias pre-MC and then **bypasses the fitted A/B/C**, using identity Platt
   (train/serve lockstep rationale). Default OFF, so inert today; becomes a live
   stage-loss the instant the flag is flipped without a refit on the corrected domain.

3. **[LOW, inert] α not computed per-decision** (`:3363` hardcodes `base_alpha.level1`
   vs evaluator's `compute_alpha`). Inert under MODEL_ONLY posterior; latent if
   fusion is ever enabled.

4. **[INFO] No divergence on stages 1, 2, 3-point, 4 (fusion), 5, 6-count.** MC,
   P_raw, point Extended Platt (incl. B·lead_days), model-only posterior, and the
   bootstrap n/percentile/p-value machinery are byte-for-byte the same shared code.

---

## 10-line verdict

1. Market fusion is **NOT a lost EDLI stage** — `MODEL_ONLY_POSTERIOR_MODE` is the
   designed default (math spec §15.7/§15.8) and BOTH evaluator (`evaluator.py:5192`)
   and EDLI (`event_reactor_adapter.py:3374`) disable α-weighted market blending identically.
2. The **B·lead_days Platt term is NOT lost** in the live point serve: EDLI calls
   `calibrate_and_normalize(p_raw, cal, lead_days, …)` (`:3689`) — the full
   `σ(A·logit + B·lead_days + C)` — same as the evaluator (`:4541`), with a real
   `lead_days` from `_snapshot_lead_days`.
3. The ONE material, always-on divergence is the **double-bootstrap CI**: EDLI builds
   `MarketAnalysis` without `calibrator=`/`lead_days=` (`:3356-3377`), so the shared
   `_bootstrap_bin` runs with `has_platt=False` and bootstraps **RAW p_raw**, dropping
   the calibrated surface AND the σ_parameter (A,B,C resampling) layer (§8.2).
4. Impact: point q is calibrated but its CI is drawn in the wrong (raw) space and is
   missing Platt uncertainty → `q_lcb_5pct`, FDR p_value, prefilter, and trade_score
   can mis-gate. Severity HIGH. This is a translation-loss seam, not a flagged choice.
5. Secondary (LOW, gated OFF): bias-corrected-domain forced-identity Platt (`:3624`)
   and hardcoded α (`:3363`) — both inert at current settings but latent stage-losses.
6. MC sample count (10,000), σ_instrument, WMO rounding, P_raw normalization, and
   bootstrap n=200 / [5%,95%] / p=mean(edge≤0) are all identical across paths.
7. Top fix target: pass `calibrator=cal` and `lead_days=lead_days` into the EDLI
   `MarketAnalysis(...)` so the bootstrap perturbs the calibrated surface with Platt-
   parameter sampling — restoring §8.2 without touching the (correct) point serve.
8. Trade-off of that fix: it makes EDLI CIs **wider** (restores σ_parameter), which
   will reject more marginal candidates — a correctness gain, a throughput cost.
9. No edits/DB/git performed; all citations verified against HEAD 6fcd05a69f.
10. Report: `docs/operations/DESIGN_VS_LIVE_PROBABILITY_2026-06-01.md`.
