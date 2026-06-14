# Edge-Existence Decisive Measurement — Calibration Track vs Forecast-Precision Constraint

**Date:** 2026-06-14
**Mode:** READ-ONLY. No edits, no live touch. DBs opened `?mode=ro`, timeout 25s. Every number below re-measured this session at source, not inherited from P1/P2/P3.
**The dispute adjudicated:** P1/P2 say cheap-bin q_lcb is CRUSHED/SUPPRESSED → fixable → unlocks alpha (the *calibration track*). P3_architecture (F1/F3/F4) says the opposite: every band's after-fee edge is NEGATIVE (ring −0.072) and peak-bin MAE ≈ one bin-width, so exact-bin selection is physically unavailable → no calibration alpha, the binding constraint is FORECAST PRECISION.
**Substrate:** `state/zeus-world.db::no_trade_regret_events` (260,936 rows, 40,009 graded with `would_have_won`, 2026-05-29→2026-06-14); `state/zeus-forecasts.db::forecast_posteriors.q_json` + `settlement_outcomes` (7,029 VERIFIED). Event-level dedup = ONE row per `city|target_date|bin_label|direction` (the duplicate-inflation guard the prior synthesis fell for). Dedup consistency verified: **0** bins where `would_have_won` differs across duplicate rows — the outcome is a settlement property of the bin, so dedup is sound.

---

## VERDICT

**NO CALIBRATION ALPHA — the binding constraint is FORECAST PRECISION.** P3_architecture's F3 and F4 are **CONFIRMED** by fresh, independent measurement. P1/P2's "suppressed crushed-q_lcb alpha" premise is **REFUTED**: every price band's after-fee edge is ≤0, the two well-powered buy_yes bands are *significantly* negative (CIs entirely below zero), and the model's peak-bin error equals one full bin-width — so no q_lcb relaxation can manufacture correct-bin edge the weather does not provide. **Confidence: HIGH** on the negative-edge finding (well-powered, CI-backed); **HIGH** on MAE≈bin-width.

---

## 1. AFTER-FEE REALIZED EDGE BY PRICE BAND (F3 reproduction)

Edge per share = `would_have_won (payoff 0/1) − entry_cost (c_cost_95pct) − 0.01 fee`. Event-level dedup. Wilson 95% CI on win-rate; normal 95% CI on mean edge.

| dir | band | n | wins | WR | WR 95% CI | mean cost | **edge after fee** | edge 95% CI |
|---|---|---:|---:|---:|---|---:|---:|---|
| buy_yes | cheap <0.05 | **314** | 2 | 0.006 | [0.002, 0.023] | 0.011 | **−0.0145** | **[−0.0233, −0.0058]** |
| buy_yes | **ring 0.05–0.15** | **66** | 2 | 0.030 | [0.008, 0.104] | 0.093 | **−0.0725** | **[−0.1146, −0.0304]** |
| buy_yes | near-ctr 0.15–0.40 | 70 | 19 | 0.271 | [0.181, 0.385] | 0.286 | −0.0247 | [−0.1274, +0.0780] |
| buy_yes | mid 0.40–0.60 | 24 | 9 | 0.375 | [0.212, 0.573] | 0.460 | −0.0954 | [−0.2954, +0.1046] |
| buy_no | deep-fav >0.60 | **957** | 868 | 0.907 | [0.887, 0.924] | 0.925 | **−0.0285** | **[−0.0449, −0.0120]** |
| buy_no | mid 0.40–0.60 | 15 | 8 | 0.533 | [0.301, 0.752] | 0.569 | −0.0460 | [−0.305, +0.213] |
| buy_no | ring 0.05–0.15 | 4 | 1 | 0.250 | [0.046, 0.699] | 0.098 | +0.1423 | [−0.341, +0.626] |

**Findings:**
- **Ring confirmed: buy_yes 0.05–0.15 → n=66, 2 wins, WR 0.030, edge after fee = −0.0725.** This is P3's exact "66 events, 2 wins, WR 0.030, edge −0.072". Reproduced to the digit. **Its 95% CI [−0.1146, −0.0304] is entirely below zero** — significantly negative, NOT underpowered.
- **Every buy_yes band is negative after fee.** The two best-powered (cheap n=314; ring n=66) have CIs strictly below 0.
- **buy_no deep-favorite (n=957, the base-rate band) is also −0.0285 after fee, CI below 0** — even favorite-buying loses 2.85¢/share net at these prices.
- **The only positive cell** (buy_no ring, +0.14) has **n=4** with a CI of [−0.34, +0.63] — pure noise, not a band.
- **No band's after-fee edge is positive with adequate n.** Confirmed.

## 2. PEAK-BIN SELECTION ACCURACY (F4 reproduction)

Latest posterior per `city|date|metric`; argmax bin center vs `settlement_value` (°C cities, VERIFIED). n=230 (P3 used 219; I included all VERIFIED °C with a posterior). All 230 fall in the F3 era (2026-05-29+), so F3 and F4 describe the same window.

| metric | value | P3 claim | verdict |
|---|---|---|---|
| MAE \|peak_center − settled\| | **1.30 °C** | 1.28 °C | **CONFIRMED** |
| in bin-widths (1 °C interior) | **1.30** | ≈1.0 | **CONFIRMED** |
| median error | **1.00 °C** | 1.00 °C | **CONFIRMED** |
| exact-bin hit rate | **24.3% (56/230)** | 26% | **CONFIRMED** |
| within-1-bin | **67.0%** | 67% | **CONFIRMED** |

**Skill-vs-chance:** exact-hit 24.3% vs uniform-over-interior-bins chance 11.1% → **z = 6.39**. The model has **real, significant** exact-bin skill — but MAE = 1.30 bin-widths means it routinely lands one bin off, which is exactly enough to miss the exact bin ~76% of the time. Per-city variance is large: Manila 0.83 / London 0.70 / Tokyo 0.50 hit vs Seoul / Taipei / Chengdu / Wuhan 0.00. **The model cannot pin the exact bin reliably; its center error ≈ the bin width.** Confirmed.

## 3. DECISIVE SYNTHESIS

The calibration track would be valid only if there were a positive-edge band currently SUPPRESSED by an over-conservative q_lcb — i.e. correct-bin alpha a q_lcb relaxation would surface. **There is not**, for two independent, mutually-reinforcing reasons:

1. **No positive band exists to unsuppress (F3).** Every band's realized after-fee edge is ≤0; the two well-powered buy_yes bands are *significantly* negative. Relaxing q_lcb to admit the cheap/ring bins admits a **measured-losing** distribution (ring loses 7.25¢/share after the 1¢ fee). There is no suppressed winner — the ring is over-priced / adversely-selected for buy_yes, not under-priced.

2. **The bin cannot be pinned even in principle (F4).** Peak-bin MAE = 1.30 °C ≈ one full ring-bin width. Per operator law 8 (edge = selecting the correct bin), a model whose center error equals the bin width **cannot** be re-calibrated into exact-bin selection — the residual is irreducible NWP-ensemble center noise at this lead, not an over-conservative lower bound. A q_lcb/point-q fix changes *bound width*, not *center accuracy*; it is the wrong lever.

Per RULE 1 (no-edge is OUR defect) the defect is real, but it is **forecast precision** (the center is ~1 bin-width uncertain), not gate over-conservatism. The fix is sharpening the forecast/data to resolve the bin (the **data-source-audit track**), not relaxing calibration. The q_lcb≈0 the prior diagnosis flagged is the *honest* encoding of this irreducible uncertainty, not a bug to tune away.

---

## RAW NUMBERS (deciding)

- Ring (buy_yes 0.05–0.15) after-fee edge: **−0.0725**, 95% CI **[−0.1146, −0.0304]**, n=66, 2 wins.
- Cheap tail (buy_yes <0.05) after-fee edge: **−0.0145**, 95% CI **[−0.0233, −0.0058]**, n=314 (well-powered).
- Peak-bin MAE: **1.30 °C = 1.30 bin-widths** (median 1.00), n=230; exact-hit 24.3% vs 11.1% chance (z=6.39).
- No band positive at adequate n. Only positive cell n=4 (CI spans 0).

*End decisive measurement. Read-only. Scripts: /tmp/edge_f3*.sql, /tmp/edge_ci.py, /tmp/peak_bin*.py. DBs `?mode=ro`. Event-level dedup verified collision-free (0 outcome-inconsistent bins).*
