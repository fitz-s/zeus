# Corrected-Belief Out-of-Sample Test — Does the σ-Widened + Center-Debiased Belief Beat the Market After Fee?

**Date:** 2026-06-14
**Mode:** READ-ONLY. DBs opened `?mode=ro&immutable=1`, timeout 25s, ISO-T. Unit = de-duplicated `(city, target_date, bin, direction)` EVENT. Raw scripts `/tmp/cb/*.py`.
**Charge:** `overconfidence_root.md` identified two CORRECTABLE defects above the alleged skill ceiling — (1) σ too narrow (z-std too high), (2) a fusion-doubled cold center bias — and claimed the prior "no edge" verdict was measured on a stale narrow-σ belief. This test reconstructs the belief, applies BOTH corrections **walk-forward / leakage-free**, and grades whether the CORRECTED belief has real after-fee edge vs the market OUT-OF-SAMPLE. The bar: OOS robustness, not in-sample.

---

## VERDICT

**CORRECTED BELIEF STILL HAS NO OOS AFTER-FEE EDGE. The skill ceiling binds.** The two corrections do exactly what `overconfidence_root.md` predicted on the *belief's internal statistics* — the cold bias is removed (z-mean 0.54→−0.05; warm-debias δ≈+0.5–0.9 °C OOS, matching the predicted +0.5–0.7) and the tails stop being overconfident (within-2σ 0.85→0.99) — **but none of it converts into market-beating edge, and on every actionable cut the corrections make the realized after-fee result WORSE, not better.** Three independent measurements decide it (n=295 settled belief cells, 6 target-date walk-forward folds 06-08→06-13; 266 self-graded market-cost events 06-09→06-13):

1. **No actionable +EV cell survives OOS, corrected OR uncorrected.** The `q_lcb' > ask + 1¢` gate (the live decision rule), fit leakage-free and evaluated on held-out dates with a proper bootstrap q_lcb: **buy_no +0.046→−0.022** (orig→corrected), **buy_yes −0.051→−0.030** — every CI spans or sits below zero, and the correction *degrades* both. The one cell that looked +EV (buy_no "FAR", +0.079, CI[+0.055,+0.106]) is a **settlement-defined artifact**: "FAR" used `|bin − settled|`, knowable only after the outcome. Recomputed with the model's OWN argmax bin (knowable at decision) the edge collapses to **+0.006 (orig, CI spans 0) → −0.015 (corrected)**, and its per-fold sign is unstable (negative on the largest fold 06-09 n59, positive only on n=2–3 folds). It is the base-rate favorite band, not corrected-belief alpha.

2. **The corrected belief is a WORSE predictor than the market, OOS.** Brier vs the realized outcome: **model 0.105 (orig) / 0.112 (corrected) vs market-implied 0.094**; log-score **0.362/0.370 vs 0.290**. The market price is sharper than the belief on both sides; the σ-widening *raises* Brier (underconfidence). The model does not beat the market.

3. **σ-widening OVER-corrects and bin-resolution DROPS.** Walk-forward z-std 1.44→**0.50** (overshoots the honest 1.0 — the corrected belief is now *under*confident, because a few confident-wrong cells (Taipei/Singapore/HK) inflate the training z-std and force the MLE widen to w≈2.2–3.0). Critically, **exact-bin hit-rate falls OOS 24.5%→18.9%** and settled-bin **Brier worsens 0.65→0.77** — the debias+widen does NOT improve held-out bin selection. The in-sample 25.7%→30.4% exact-hit lift `overconfidence_root.md` cited **does not survive out-of-sample**; it was an in-sample fit.

**Why no edge despite an honest calibration fix:** edge requires pinning the settlement BIN; the corrections improve the *shape* of the belief (center sign, tail coverage) but not its *center accuracy*. Reconstructed center MAE is **1.23 °C ≈ one bin-width** (matches the prior 1.30) and the debias leaves it unchanged (a uniform shift cannot reduce dispersion). A belief whose center is one bin-width uncertain cannot be re-calibrated into exact-bin edge — confirmed now on the corrected belief, closing the loop `edge_existence_decisive.md` and `alpha_hunt.md` left on the uncorrected belief. **Confidence: HIGH** (OOS, leakage-free, CI-backed, corrections degrade every actionable cell, model loses to market on Brier+log-score).

**One caveat on power, stated honestly:** the market-cost OOS panel is **266 events on 5 dates**, and the per-cell CIs are wide (the actionable buy_no gate CI is ±0.10). This is enough to REFUTE a large positive edge and to show the corrections do not help, but it is NOT enough to certify a small (<2¢) edge does not exist. The honest reading is "no detectable OOS edge at this power, and the corrections move the point estimate the wrong way" — not a proof of exactly zero.

---

## METHOD (leakage-free reconstruction + walk-forward)

- **Belief reconstruction.** Latest `forecast_posteriors` posterior per `(city,target_date,metric)` joined to VERIFIED `settlement_outcomes`. Per cell, fit Gaussian `(μ,σ)` in Celsius by MLE over the per-bin `q_json` integrated across the stored `bin_topology` edges (C bins: center±0.5·step; F bins: native `lower_c`/`upper_c`, which are stored in Celsius — F settlement values converted °F→°C). Reconstruction reproduces the prior session's numbers: center bias **−0.50 °C** (doc −0.54), MAE **1.23** (doc 1.30), exact-hit **25.5%** (doc 25.7%), within-2σ **0.82** (doc 0.80). n=295 settled cells over 06-08→06-13.
- **Walk-forward correction (no leakage).** For each held-out target_date, fit the correction on **strictly-prior dates only**: δ = mean(settled−μ) [warm-debias], w = std(z) [calibration-honest σ-widen, clamped to [1,3]]. Apply μ'=μ+δ, σ'=σ·w to the held-out fold and recompute q over the settlement-preimage bins. (An MLE bin-loglik objective gave the same conclusion with even larger widen, w≈1.7–3.7.)
- **Market alpha.** Market ask = `no_trade_regret_events.c_cost_95pct`; outcome self-graded from VERIFIED settlement winning bin (the table's own `would_have_won` backfill is unrun for 06-09+, so it was reconstructed from settlement — same authority). After-fee edge = `won − cost − 0.01`. Bootstrap 95% CI (5000×). q_lcb' from a 400-draw μ-jitter bootstrap of the corrected belief.

## RESULTS TABLE (OOS, held-out folds, robust moment correction)

| cut | orig edge (CI) | corrected edge (CI) | reading |
|---|---|---|---|
| GATED q_lcb'>ask+1¢ buy_no (actionable) | **+0.046** [−0.058,+0.143] | **−0.022** [−0.117,+0.064] | corrected worse; CI spans 0 |
| GATED q_lcb'>ask+1¢ buy_yes (actionable) | −0.051 [−0.120,+0.048] | **−0.030** [−0.038,−0.023] | both negative |
| buy_no, |bin−argmax|≥2 (knowable FAR) | +0.006 [−0.062,+0.071] | −0.015 [−0.084,+0.049] | the "+EV" evaporates when actionable |
| buy_no, |bin−**settled**|≥2 (post-hoc) | +0.079 [+0.055,+0.106] | +0.079 [+0.055,+0.106] | settlement-defined; NOT a tradeable signal |
| buy_yes near | +0.016 [−0.121,+0.182] | −0.073 [−0.154,+0.029] | corrected worse |
| **Brier vs market** | 0.105 vs **0.094** | 0.112 vs **0.094** | market sharper both ways |
| **z-std (cal.)** | 1.44 | **0.50** (overshoots 1.0) | over-widened → underconfident |
| **exact-bin hit** | 0.245 | **0.189** | drops OOS |
| settled-bin Brier | 0.65 | **0.77** | corrected worse |

Per-fold buy_no act-FAR edge (orig): 06-09 **−0.021**(n62), 06-10 +0.372(n3), 06-11 +0.068(n13), 06-12 −0.055(n7), 06-13 +0.076(n2) — sign set by tiny folds; the powered fold is negative.

---

## RECONCILIATION WITH overconfidence_root.md

`overconfidence_root.md` was RIGHT that (a) σ was too narrow and (b) the center ran cold and the prior no-edge record was pre-floor — both reproduced here. It was WRONG that fixing them was "the most plausible route to a real after-fee edge sub-population." Tested OOS, the calibration fix is real but **orthogonal to edge**: it corrects the belief's self-consistency (z-mean, tail coverage) without touching center accuracy (MAE = 1 bin-width, unchanged by a shift), and edge is governed entirely by center accuracy. The in-sample exact-hit lift it relied on (25.7→30.4%) does not survive a leakage-free split (24.5→18.9% OOS). The honest sequence the prior doc asked for — finish σ-shape, debias center, THEN re-grade — has now been run end-to-end on the corrected belief, and the residual IS the skill ceiling.

## DECISION

Do **not** deploy the σ-candidate + center-debias as an alpha unlock — OOS it degrades every actionable cell and loses to the market on Brier/log-score. The center-debias (δ≈+0.6 °C) and an honest σ-widen are still worth promoting **for calibration honesty / correct sizing** (z-mean→0, tails honest), but NOT as an edge source, and the σ-widen must be tempered (the MLE z-std overshoots to underconfidence; target w≈1.4 from the trimmed-z, not 2.5). **Pivot to forecast-precision / microstructure:** the binding constraint is center MAE ≈ one bin-width. The only untested precision lever remains the short-lead day0/nowcast lane (`alpha_hunt.md` §"what would change the verdict"), where forecast MAE collapses — but the settled sample for it does not yet exist.

---

## RAW (deciding numbers)
- Reconstruction fidelity: center bias **−0.50 °C**, MAE **1.23**, exact-hit **25.5%**, within-2σ **0.82**, n=295 (reproduces prior session).
- Walk-forward correction (leakage-free): δ ≈ **+0.51…+0.90 °C** per fold; w ≈ **2.2…3.0** (MLE), trimmed honest target ≈1.4.
- OOS calibration: z-mean **0.54→−0.05**, z-std **1.44→0.50**, within-2σ **0.85→0.99**, exact-hit **0.245→0.189**, settled-bin Brier **0.65→0.77**.
- OOS market alpha (actionable, n=266 events / 5 folds): buy_no gate **+0.046→−0.022**, buy_yes gate **−0.051→−0.030**, buy_no knowable-FAR **+0.006→−0.015** — no CI excludes 0 on the positive side.
- Model vs market: Brier **0.105/0.112 vs 0.094**; log-score **0.362/0.370 vs 0.290** — model loses both, corrected loses by more.
- Power caveat: 266 market-cost events on 5 dates; CIs ±~0.10 on the gated cells. Refutes a large edge; cannot certify a sub-2¢ edge absent.

*End. Read-only. DBs `?mode=ro&immutable=1`. Scripts: /tmp/cb/{recon,walkforward,alpha2,alpha3,robust,calib_final}.py.*
