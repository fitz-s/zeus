# Per-City Representativeness De-Bias — Out-of-Sample, Does the CORRECT (per-city, two-sign) Center Fix Beat the Market on Tradeable Cities?

**Date:** 2026-06-14
**Mode:** READ-ONLY. DBs opened `?mode=ro&immutable=1`, `.timeout 25000`, ISO-T. Unit = de-duplicated `(city, target_date, bin)` EVENT. Raw scripts `/tmp/pc/*.py`; belief reconstruction reused verbatim from `/tmp/cb/recon.py` (same Gaussian-over-settlement-preimage-bins fit that produced `corrected_belief_oos.md`).
**Charge:** `corrected_belief_oos.md` refuted a GLOBAL warm shift — but that test was mis-specified, because `cold_bias_metadata_root.md` proved the bias is **per-city, two-sign** (Tokyo −2.18°C … Karachi +2.48°C). A global shift necessarily worsens the warm cities. The CORRECT fix — a per-city representativeness de-bias δ_city fit walk-forward from each city's own prior settled residuals — was UNTESTED. This test runs it and grades accuracy + after-fee edge, separating LIQUID (US/tradeable) from ILLIQUID (Asian/Euro) cities.

---

## VERDICT

**PER-CITY CORRECTS ACCURACY ON A FEW CITIES BUT PRODUCES NO TRADEABLE EDGE. The decision-grade liquid/US cells do not clear zero, and the only CI-positive cell is an illiquid penny-longshot artifact where the per-city correction adds nothing.**

The per-city de-bias is the **right shape** of fix and beats the global shift on the one accuracy metric that matters for bin selection — but two facts kill it as a deployable alpha:

1. **It is critically underpowered and overfits the per-city δ.** The live OpenMeteo IFS9 anchor (the one feeding the belief) has settled history only for **6 dates (06-08→06-13)** — so a strictly-prior walk-forward δ_city is fit on **n_prior = 1–4** residuals per city. On aggregate this makes per-city the WORST of the three: pooled center MAE **per-city 1.34 > orig 1.23 > global-null 1.18**, and pooled exact-bin hit **per-city 0.221 < orig 0.240 < global-null 0.270**. δ_city helps a handful (Seattle MAE −0.82, Amsterdam −0.78, Taipei −0.77, Seoul −0.72, Houston −0.60) but HURTS more (NYC −1.39, Tokyo −0.76, Karachi −0.73, Beijing −0.72) because a 1–4-point median is high-variance noise. The two-sign per-city structure is real; the per-city *estimator* is too thin to be net-positive out-of-sample at this history depth.

2. **No liquid/US cell beats the market with a CI that excludes zero.** On the 11 US/F cities (34 distinct settled (city,date) cells — the structural power ceiling), the gated after-fee edge on the per-city-corrected belief is **buy_yes +0.024 (CI[−0.011,+0.064], n=157)** and **buy_no −0.012 (CI[−0.111,+0.085], n=70)**. Per-city beats original on both (buy_yes orig +0.007, buy_no orig −0.033), and the US exact-bin hit jumps **0.20→0.42** — but every CI spans zero, and the hit jump rests on **n=24 cells / 7 bin-flips, several driven by δ_city fit on n_prior=1** (Houston/Dallas 06-09). Promising direction, not a certifiable edge.

3. **The single CI-positive cell is an illiquid artifact, not alpha.** ILLIQUID buy_yes per-city = **+0.0205, CI[+0.007,+0.035], n=665** (depth≥100 shares) — but mean executable ask is **0.006** (median 0.001): 658/665 trades are deep-out-of-the-money YES tokens bought for ~0.2¢, win rate 3.6%. The "edge" is the structural penny-longshot mechanic, **identical for original (+0.0197) and per-city (+0.0205)** — the correction contributes nothing. It decays monotonically by date (+0.137 on 06-08 → −0.004 on 06-13) and is exactly the settlement-favorite/longshot artifact `corrected_belief_oos.md` already flagged. Illiquid buy_no's apparent +0.032 collapses to **+0.011 (CI spans 0)** once real executable depth is required (n 464→122; 74% had thin/no book) — confirming the charge's stale/thin-book warning.

**The corrected belief is still a worse predictor than the market where the market is real.** On liquid US cells the per-city center fix improves self-consistency (z-mean +0.47→+0.15) and lifts exact-hit, but does not convert to a market-beating, CI-positive, depth-backed cell. **Confidence: HIGH** that there is no tradeable per-city edge at this power; **MEDIUM-HIGH** that per-city is a genuine accuracy improvement on ~5 cities that would matter at adequate history depth.

**This does NOT refute the per-city de-bias as a calibration/accuracy upgrade** — it refutes it as a *deployable alpha at current history depth*. The binding constraint is unchanged from every prior doc: center MAE ≈ one bin-width, and now additionally **6 settled dates of live-anchor history is too few to fit a stable per-city offset**.

---

## METHOD (per-city, walk-forward, leak-free)

- **Belief.** Reused `/tmp/cb/panel.pkl` (n=295 settled cells, HIGH+LOW, 06-08→06-13): latest `forecast_posteriors` posterior per `(city,target_date,metric)` ⋈ VERIFIED `settlement_outcomes`; per cell Gaussian `(μ,σ)` MLE over `q_json` integrated across the stored `bin_topology` Celsius edges (F bins use native `lower_c/upper_c`, F settlements converted →°C). Reproduces prior fidelity (bias −0.50, MAE 1.23, exact-hit 25.5%).
- **δ_city (representativeness offset).** For each held-out (city, target_date), δ_city = **robust median of `(settled_c − anchor_c)`** over that city's **strictly-prior** settled dates (anchor = latest `deterministic_forecast_anchors.anchor_value_c` per cell — the raw OpenMeteo IFS9 anchor that feeds fusion). Low-parameter, walk-forward, no leakage. A city with zero prior settled dates falls back to the global δ (and is excluded from per-city-only accuracy cuts via `has_pc`). Applied μ'_city = μ + δ_city, σ held fixed to isolate the CENTER fix. Global-null δ = robust mean of ALL prior residuals (the `corrected_belief_oos.md` construction) — the benchmark per-city must beat.
- **Market.** Executable ask per bin: zeus-forecasts `market_events` (condition_id → bin range, native unit) ⋈ **zeus_trades `executable_market_snapshots`** (condition_id → `orderbook_top_ask`, `depth_at_best_ask`), latest snapshot captured ≤ target_date EOD. 3201/3245 panel bins matched to a live book. After-fee edge = `won − ask − 0.01`; gate = model q > ask + fee (live decision rule); bootstrap 95% CI (8000×). Depth≥100-share gate applied as the tradeability filter.
- **Liquid/illiquid split.** LIQUID = 11 US/F-settlement cities (NYC, Seattle, Atlanta, Miami, Chicago, San Francisco, Houston, Denver, Austin, Dallas, Los Angeles) per charge; ILLIQUID = the 38 C-unit international cities.

## RESULTS

### Accuracy (σ fixed; center debiased) — per-city is net WORSE on aggregate, better only on a few
| cut | orig | per-city | global-null |
|---|---|---|---|
| ALL exact-hit | 0.240 | **0.221** | 0.270 |
| ALL center MAE | 1.233 | **1.338** | 1.177 |
| ALL z-mean | +0.623 | +0.223 | +0.340 |
| LIQUID/US exact-hit | 0.200 | **0.425** | 0.275 |
| LIQUID/US MAE | 1.128 | 1.230 | 1.059 |
| ILLIQUID exact-hit | 0.247 | **0.185** | 0.269 |

Per-city HELPS (HIGH, MAE drop): Seattle +0.82, Amsterdam +0.78, Taipei +0.77, Seoul +0.72, Houston +0.60. Per-city HURTS: NYC −1.39, Tokyo −0.76, Karachi −0.73, Beijing −0.72, Panama City −0.69. Net negative because thin-n δ_city adds noise on more cities than it corrects.

### After-fee edge (per-city q, gated, bootstrap CI)
| cut | n | edge | CI | reading |
|---|---:|---:|---|---|
| LIQUID/US buy_yes | 157 | **+0.024** | [−0.011,+0.064] | beats orig (+0.007); CI spans 0 |
| LIQUID/US buy_yes, depth≥100 | 45 | +0.050 | [−0.016,+0.139] | underpowered |
| LIQUID/US buy_no | 70 | −0.012 | [−0.111,+0.085] | beats orig (−0.033); ≤0 |
| ILLIQUID buy_no | 464 | +0.032 | [−0.0005,+0.063] | **collapses to +0.011 CI∋0 at depth≥100 (n→122)** |
| ILLIQUID buy_yes, depth≥100 | 665 | **+0.0205** | **[+0.007,+0.035]** | penny-longshot artifact (ask 0.006); orig +0.0197 → per-city adds 0 |

US exact-hit jump 0.167→0.417 (HIGH, n=24, cities-with-prior): 7 bin-flips gained / 1 lost; gainers Houston/Dallas/Atlanta/Seattle/LA, several on n_prior=1.

---

## RECONCILIATION

`corrected_belief_oos.md` was right that the GLOBAL shift has no edge, but it could not have found per-city edge because it never fit per-city. This test supplies the missing per-city correction and reaches a **sharper, not contradictory** conclusion: the per-city fix is the correct shape and beats the global on US bin-selection, but (a) at 6 dates of live-anchor history the per-city δ overfits and is net-negative on aggregate accuracy, and (b) it produces no CI-positive, depth-backed, market-beating cell on tradeable cities. Operator law 8 / RULE 1 ("correct metadata → correct bin → edge") holds for the *bin* step on US cities (exact-hit rises) but **breaks at the edge step**: the corrected bin still loses to a sharper market, and the only CI-positive number is an illiquid artifact independent of the correction.

## DECISION

Do **not** deploy the per-city de-bias as an alpha source — there is no tradeable OOS edge on liquid cities at current power. It is worth promoting as a **calibration/accuracy improvement for the ~5 cities with a consistent large offset** (Seattle, Seoul, Taipei, Houston, Amsterdam) — but ONLY once the live OpenMeteo IFS9 anchor accumulates enough settled history per city (the 6-date window forces n_prior=1–4 and overfits; ≥20–30 settled dates/city would let δ_city stabilize). The decisive missing ingredient is **history depth on the live anchor**, not a better estimator. Re-run this exact test after the live anchor reaches ~1 month of per-city settled coverage; until then the per-city center fix is accuracy-honest but not edge-bearing, and the binding constraint remains center MAE ≈ one bin-width.

---

## RAW (deciding numbers)
- History depth (live IFS9 anchor): settled 06-08→06-13 only → δ_city fit on **n_prior 1–4/city**; US power ceiling **34 settled (city,date) cells**.
- Accuracy: pooled MAE per-city **1.338** > orig 1.233 > global 1.177; pooled exact-hit per-city **0.221** < orig 0.240 < global 0.270. US exact-hit 0.200→**0.425** (n=24/7-flips).
- Liquid/US edge: buy_yes per-city **+0.024 CI[−0.011,+0.064]** n=157 (orig +0.007); buy_no **−0.012 CI[−0.111,+0.085]** n=70. All CIs ∋ 0.
- Illiquid buy_yes depth≥100 **+0.0205 CI[+0.007,+0.035]** n=665 — mean ask **0.006**, win 3.6%, per-city≈orig (+0.0197) → penny-longshot artifact, not correction-driven; decays +0.137(06-08)→−0.004(06-13).
- Illiquid buy_no +0.032 → **+0.011 CI∋0** at depth≥100 (n 464→122; 74% thin book) — stale/thin book confirmed.
- Market matching: 3201/3245 panel bins matched to live `executable_market_snapshots` book.

*End. Read-only. Scripts: /tmp/pc/{percity_test,edge_test,validity,scrutinize,us_accuracy,final_lcb}.py + /tmp/cb/recon.py (belief). Anchors/binmap/asks exported to /tmp/pc/*.json|*.pkl.*
