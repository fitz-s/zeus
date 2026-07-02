# Overconfidence Root-Cause — What Makes the Belief Overconfident & Adversely-Selected, and Is It Correctable?

**Date:** 2026-06-14
**Mode:** READ-ONLY. DBs opened `?mode=ro`, timeout 25s, ISO-T. Unit = de-duplicated `(city, target_date, bin_label, direction)` EVENT. Raw scripts in `/tmp/ocr/*.py`.
**Charge:** `alpha_hunt.md` + `edge_existence_decisive.md` proved the SYMPTOM (model claims q_no≈0.945, realizes 0.884; biggest disagreements LOSE −8.9¢; WR−cost = −1.9¢ symmetric) but punted on the MECHANISM — `edge_existence_decisive.md` called it "irreducible NWP center noise" (SKILL_CEILING); `diagnosis_confirmation.md` left the q_lcb-collapse cause open. RULE 1 + law 8: "no edge" is OUR defect — find the dominant CORRECTABLE cause.

---

## VERDICT

**DOMINANT CAUSE = CALIBRATION_SIGMA (σ systematically too NARROW), with a real secondary CORRECTABLE cold-CENTER bias that is part-provider / part-FUSION. NOT a skill ceiling.** The prior "no edge / overconfident" verdict is **measured on a regime the system already partly fixed** and has not re-measured.

Three settlement facts decide it (all re-measured this session, n=230 settled VERIFIED °C cells, `forecast_posteriors ⋈ settlement_outcomes`):

1. **σ is too narrow → drives the overconfidence.** Pooled z=(model_mean−settled)/σ has **std=1.71** (honest=1.0); only **80% of outcomes fall within 2σ** (honest=95%). The model's center RMSE is **1.55°C** but its median posterior σ is **1.04°C** — it needs σ **≈1.48× wider** to be honest. **Spread defect = 87.6% of the miscalibration MSE.** This is what makes q_no claim 0.945 when truth is 0.884.

2. **A second, smaller, correctable cold CENTER bias.** Model mean runs **−0.54°C COLD** vs settlement (z-mean=−0.65). MLE on settled cells: a **−0.70°C warm-correction lifts the exact-bin hit rate 25.7%→30.4%** — the alpha-relevant move. Provenance split: the raw OpenMeteo anchor is already cold −0.29°C (provider/data), and the **fusion DOUBLES it to −0.54°C** (the EB-bias / low-σ-instrument weighting pulls the posterior mean toward cold instruments) — so ~46% upstream-provider, ~54% FUSION_BIAS.

3. **The fix is already half-deployed and statistically UNMEASURED.** A σ-floor (`floor_steps≈1.80`) activated **2026-06-10**: median posterior σ jumped 0.88→1.46°C; z-std fell **1.93→1.35**, within-2σ rose **75%→86%**, cold bias shrank **−0.74→−0.31** (a wider σ also de-amplifies the fusion-cold pull, −0.38→−0.10). **The σ-floor closed ≈half the overconfidence gap.** BUT the entire "no edge" trade record (`no_trade_regret_events`, 1,484 deduped settled events) is **99% PRE-floor** (1,475 events decided ≤06-06; only 9 post-floor). **The realized q_no 0.945→0.884 overconfidence and the −1.9¢ adverse selection are measurements of the OLD narrow-σ belief — the corrected belief has essentially zero settled sample.**

**Could fixing it produce real edge?** PLAUSIBLY YES for a sub-population. The edge is governed entirely by center accuracy: buy_no on a bin **far** from settlement = **+0.5¢** edge (n=767), buy_no **near** settlement = **−13.5¢** (n=226); buy_yes **near** settlement = **+4.2¢** (n=76). The −0.70°C warm-correction moves YES bets toward the near-settle winning zone and stops NO bets on bins the cold center wrongly believed safe. **The alpha test is not yet run on the corrected belief — and it cannot be, because the σ-floor + a bias-correction are not yet reflected in any settled trade sample.** **Confidence: HIGH** on σ-narrowness being dominant (n=230, CI-strong z-std); HIGH on the cold-bias being real and fusion-amplified; HIGH that the prior no-edge verdict is regime-stale.

**RANK:** `CALIBRATION_SIGMA` (≈88% of miscalibration, dominant) ≫ `FUSION_BIAS`+provider cold-center (≈12%, but the alpha-moving lever) ≫ `CORRUPTED_DATA` (localized tail only) ≫ `SKILL_CEILING` (the irreducible floor BELOW both fixes, not the current binding constraint).

---

## 1. RELIABILITY CURVE — overconfidence is monotone, not noise (`no_trade_regret_events`, deduped)

Stored `q_live` binned into deciles, observed settlement win-freq per decile. `/tmp/ocr/reliability.py`.

| buy_no decile | mean_q | realized | **gap (q−real)** | n |
|---|---:|---:|---:|---:|
| 2 (0.90–0.94) | 0.916 | 0.822 | **+0.095** | 101 |
| 3 (0.94–0.97) | 0.953 | 0.851 | **+0.101** | 101 |
| 4 (0.97–0.99) | 0.988 | 0.921 | +0.067 | 101 |
| 6–9 (≈1.000) | 1.000 | 0.93–0.98 | +0.02…+0.07 | 404 |

**Gap is positive in EVERY decile.** The model is overconfident *everywhere*, worst in the high-confidence favorite bins (deciles 2–4: claims 0.92–0.99, delivers 0.82–0.92). buy_yes top decile: claims **0.467**, realizes **0.250** (+0.217 overconfident) — the adverse-selection signature. A monotone, sign-consistent gap = a **calibration-shape defect**, not random forecast error.

## 2. DECISIVE σ-vs-CENTER DECOMPOSITION (`forecast_posteriors ⋈ settlement`, n=230, `/tmp/ocr/fp_decisive.py`)

| quantity | value | honest target | reading |
|---|---:|---:|---|
| z-std = std((mean−settled)/σ) | **1.71** | 1.00 | σ ~1.7× too narrow |
| within-2σ coverage | **80%** | 95% | overconfident tails |
| within-1σ coverage | 58% | 68% | overconfident core |
| center bias (mean−settled) | **−0.54°C** | 0 | cold offset |
| center RMSE | 1.55°C | — | true center error |
| median posterior σ | 1.04°C | ≈1.55 | needs **1.48×** widen |
| **bias² share of MSE** | **12.4%** | — | center bias is secondary |
| **variance share of MSE** | **87.6%** | — | **σ-narrowness dominates** |

MLE refit on settled cells (`/tmp/ocr/sigmafix.py`): joint optimum **bias=−0.70°C, σ-scale=1.60×** → NLL/event 2.47→2.00 (**−0.47 nats**). σ-only (1.80×) = −0.33 nats; bias-only (−0.70°C) = −0.28 nats AND raises argmax exact-bin hit **25.7%→30.4%**.

## 3. CENTER-BIAS PROVENANCE — provider + FUSION, not per-city corruption (`/tmp/ocr/anchor_vs_post.py`, `percity.py`)

- **Near-universal cold sign across cities** (≈30 of 38 cities have negative bias) ⇒ a SHARED upstream defect, not random station corruption.
- Anchor (raw OpenMeteo) cold **−0.29°C**; posterior cold **−0.54°C** ⇒ **FUSION ADDS −0.25°C of cold** (the low-σ cold instruments dominate the precision-weighted T2 fusion). Pre-floor the fusion added −0.38; post-floor only −0.10 (wider σ de-weights the cold pull).
- **CORRUPTED_DATA is a localized TAIL, not the mass:** Taipei (bias −2.64, σ 0.89, z 2.42), Singapore (−1.49, σ 0.55, z 2.67), Hong Kong (z 3.18, σ 0.58) are *confident-and-wrong* outliers — a handful of cities with pathologically tight σ on a wrong center. Worth a station/window spot-audit, but they are <10 cities, not the −0.54°C systemic offset. settlement_value is **100% integer** (clean rounding, not the bias source); F/C units are clean-partitioned (US=F, rest=C) — no unit-mix corruption.

## 4. THE FIX IS HALF-DEPLOYED AND UNMEASURED (the regime trap, `/tmp/ocr/floor_active.py`, `prepost.py`, `substrate_era.py`)

Posterior median σ by computed_at day: 06-07/09 = **0.83–0.90** → 06-10..06-13 = **1.60–1.84** (σ-floor `floor_steps≈1.80` activated ~06-10; live `state/sigma_scale_fit.json` carries `family C/F: fitted=True, k=1.0, w=0.0, floor_steps≈1.80`, consumed at `src/data/replacement_forecast_materializer.py:646-700`).

| regime | n | bias | med σ | **z-std** | within-2σ | MAE |
|---|---:|---:|---:|---:|---:|---:|
| PRE-floor (<06-10) | 124 | −0.74 | 0.88 | **1.93** | 75% | 1.32 |
| POST-floor (≥06-10) | 106 | −0.31 | 1.46 | **1.35** | 86% | 1.06 |

**The σ-floor closed ≈half the gap (z-std 1.93→1.35).** Residual z-std 1.35 ⇒ σ still ~1.35× too narrow; the candidate `m`/`w` two-normal mixture (`sigma_scale_fit.candidate.json`) targets exactly this residual but is `candidate:true`, candidate, un-promoted.

**The verdict-stale fact:** the deduped settled trade substrate behind the entire "no edge" finding is **1,475/1,484 events decided ≤06-06 (99% PRE-floor)**; 06-10/06-11 contribute **9 events**. The realized 0.945→0.884 overconfidence and −1.9¢ adverse selection are properties of the **superseded narrow-σ belief.** No settled sample exists to grade the corrected belief.

## 5. ALPHA TEST — edge IS center-accuracy-governed (correctable lever, `/tmp/ocr/alpha_test.py`)

After-fee edge on 1,468 settled events, split by |bet-bin temp − settlement|:

| direction | population | n | after-fee edge |
|---|---|---:|---:|
| buy_no | FAR (\|bin−settle\|≥2°C) | 767 | **+0.0052** |
| buy_no | NEAR (<2°C) | 226 | **−0.1348** |
| buy_yes | NEAR (<2°C) | 76 | **+0.0418** |
| buy_yes | FAR | 399 | −0.0402 |

Edge flips sign on center accuracy. A −0.70°C warm-correction shifts the belief center toward truth, moving bets out of the losing NEAR-NO / FAR-YES cells into the winning FAR-NO / NEAR-YES cells. **This is the concrete path by which fixing the correctable cause could flip a sub-population to positive after-fee edge** — untested because it requires the corrected belief in the live settled record.

---

## RANKED CAUSES + FIXES

| rank | cause | share of overconfidence | fixable | fix | expected effect |
|---|---|---|---|---|---|
| **1** | **CALIBRATION_SIGMA** (σ too narrow) | **~88% of MSE** | YES — partly done | Promote the σ-shape candidate (`sigma_scale_fit.candidate.json` `m`/two-normal) to push residual z-std 1.35→1.0; the `floor_steps≈1.80` already live closed half | Restores within-2σ 80%→95%; collapses the q_no 0.945→0.884 claim toward honest; removes the spurious +EV the narrow σ manufactured on NEAR bins |
| **2** | **FUSION_BIAS + provider cold center** | ~12% of MSE (the alpha lever) | YES | Debias the OpenMeteo anchor (−0.29°C) AND damp the fusion's cold over-pull (−0.25°C); a global +0.5–0.7°C warm EB-bias correction | Exact-bin hit 25.7%→30.4%; moves YES bets into the +4.2¢ near-settle zone — the most plausible route to a real after-fee edge sub-population |
| 3 | CORRUPTED_DATA (tail) | <10 cities | YES, localized | Spot-audit station/local-day-window for Taipei, Singapore, Hong Kong, Ankara (tight σ + large cold offset = confident-wrong) | Removes a few adverse-selected confident-wrong cells; not the systemic mass |
| 4 | SKILL_CEILING | the floor below 1–3 | NO | — | Real (NWP center error ≈1.0–1.3°C at lead persists even corrected), but it is BELOW causes 1–2, not the current binding constraint. The MAE-1.30 "ceiling" the prior docs cited is the PRE-floor measurement; honest σ-coverage + center-debias must be applied BEFORE declaring the residual irreducible |

**Decisive correction to the prior verdict:** `edge_existence_decisive.md` concluded SKILL_CEILING ("irreducible NWP center noise") from a PRE-σ-floor, cold-biased belief. Two correctable defects sit ABOVE that floor and were measured INTO it. The honest sequence is: (a) finish the σ-shape promotion, (b) debias the anchor+fusion center, (c) THEN re-grade against the live settled record — only after both fixes is a residual-skill-ceiling claim sound. The alpha may be real; it has not been tested on a corrected belief.

---

## RAW (deciding numbers)
- σ-narrowness: z-std **1.71**, within-2σ **80%**, RMSE/σ **1.48×**, variance share **87.6%** (n=230).
- Cold center: posterior bias **−0.54°C**; anchor **−0.29°C**; **fusion adds −0.25°C**; MLE warm-corr −0.70°C lifts exact-hit **25.7→30.4%**.
- σ-floor regime split: z-std **1.93→1.35**, within-2σ **75→86%**, bias **−0.74→−0.31** (06-10 cutover).
- Trade-record staleness: **1,475/1,484 (99%) of settled "no-edge" events are PRE-floor (≤06-06)**.
- Edge by center accuracy: buy_no FAR **+0.5¢** / NEAR **−13.5¢**; buy_yes NEAR **+4.2¢**.
- Live artifact: `state/sigma_scale_fit.json` `floor_steps≈1.80, k=1.0, w=0.0, candidate:true` consumed at `src/data/replacement_forecast_materializer.py:646`. Fusion EB-bias at `src/forecast/bayes_precision_fusion.py:67 eb_bias`, SIGMA_FLOOR=0.8 at `:53`.

*End. Read-only. Scripts: /tmp/ocr/{reliability,fp_decisive,sigmafix,percity,unitprobe,anchor_vs_post,floor_active,prepost,substrate_era,alpha_test}.py.*
