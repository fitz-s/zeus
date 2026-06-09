# SUPERIOR_BLEND — Candidate blends vs the 0.1 center, measured on settlement SKILL

> Created: 2026-06-07
> Last reused/audited: 2026-06-07
> Authority basis: OBSERVE_BASELINE.md (Tokyo 0/5, KL 0/4 bin-selection misses), QLCB_HONESTY.md (3.24x underdispersion; q_lcb cohort-dependent, NOT validatable on history), REALIGN_0_1_AUTHORITY.md (live authority = replacement_0_1 = AIFS + Open-Meteo 0.1).
> Method: READ-ONLY (sqlite mode=ro) on LIVE DBs in /Users/leofitz/zeus/state. Truth = zeus-forecasts.settlement_outcomes WHERE authority='VERIFIED' (6640 rows, 2024-01-01..2026-06-06). Walk-forward only (per-city bias / residual estimated from PRIOR settled cells; train cutoff < target; no lookahead). Forecast SKILL only (bin-hit, PIT calibration, CRPS) — PATH-INDEPENDENT and history-validatable. q_lcb coverage / after-cost win-rate deliberately NOT claimed here (cohort-dependent; gate on post-redeploy live settled cohort per the brief).
> Scripts (measurement only, /tmp, not repo): skill_blend_eval.py, skill_robust.py, multimodel_blend.py.

---

## TL;DR

The SIMPLEST candidate that improves settlement skill in a MAJORITY of cities is **per-city walk-forward EB bias-correction of the center**. It is first-order. An **equal-weight GFS+ECMWF center blend** is a real but second-order add-on. **Dispersion widening does NOT improve bin-hit** (it is a calibration/coverage fix, ties to q_lcb, not a center-skill fix). The Tokyo/KL center-misses are a **systematic warm/cold bias**, not dispersion or wrong-model — and bias-correction is exactly the fix.

| candidate | pooled bin-hit | pooled CRPS | mean PIT | cities improved (bin-hit, n>=20) | verdict |
|---|---|---|---|---|---|
| BEFORE (raw ENS center) | 0.191 | 1.50 | 0.684 | — | baseline |
| (a) EB bias-correction | **0.259** | **1.21** | **0.537** | **35/50** | **WINNER — first-order** |
| (c) dispersion widening | 0.190 | 1.33 | 0.626 | 8/50 | NO bin-hit gain (CRPS-only) |
| (a)+(c) both | 0.259 | 1.17 | 0.519 | 35/50 | best CRPS, same bin-hit as (a) -> not worth the complexity for bin-hit |
| (b) GFS+ECMWF equal-weight blend (center-only) | see below | — | — | second-order | real but smaller than (a) |

All measured at lead=1 day (the trading horizon; matches the 24h underdispersion measurement in QLCB_HONESTY), ECMWF-ENS history as the 0.1-center proxy, n=4982 settled cells.

---

## DATA AVAILABILITY (what can / cannot be recomputed)

**The live 0.1 model has essentially NO settled history.** `forecast_posteriors` method `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor` = 171 rows, all target 2026-06-08/09, **0 overlap with any VERIFIED settlement**. `deterministic_forecast_anchors` (Open-Meteo IFS 9km) = 171 rows, same dates. So the LIVE posterior method itself cannot be skill-validated on history today.

**The only model with broad settled history is ECMWF ENS (TIGGE).** `ensemble_snapshots`, model_version `ecmwf_ens`, 51 members, degC-numeric, 2024-01-01..2026-05-11, 52 cities, ~770k high + ~730k low rows. **6629 of 6640 VERIFIED settlements join to an ENS snapshot.** This is the legitimate historical proxy for "the 0.1 center" because it is the SAME physical family (ECMWF) as the live IFS/AIFS path: scale (underdispersion) and bias findings transfer; a true cross-family *multi-model* skill claim for the live AIFS does not.

**A second decorrelated model DOES exist for candidate (b), but only as point centers.** `zeus-world.historical_forecasts` holds **GFS (34,811 rows, 2023-12..2026-06) and ECMWF (34,237 rows, 2024-02..2026-06)** deterministic forecasts, lead 1-7, high metric only. 4897 (city,date) pairs have both at lead=1; 2642 also join to a VERIFIED high settlement. These are POINT forecasts (no members) -> usable for center-skill (bin-hit, MAE) but they cannot emit a calibrated distribution without a bolted-on residual model. Within `ensemble_snapshots` the only other models are ECMWF variants (ifs025 56 cells, tigge 116 cells) -> same family, NOT decorrelated, not usable for (b).

**Bias machinery already exists** (enable-not-build): `zeus-world.model_bias_ens` (153 rows, per city/season/metric posterior_bias_c + posterior_sd_c) and `model_bias` (165 rows). The EB correction this doc validates is the same statistic these tables already hold.

---

## DATA-PROVENANCE HAZARD FOUND (Fitz Constraint #4 — confirms QLCB open-Q #8)

`settlement_outcomes.settlement_unit='F'` is a **MISLABEL** for US cities. For NYC/Miami/SF the column says 'F' but `settlement_value` is degC-NUMERIC (NYC high "21.0" = 21C, realistic; 21F = -6C is absurd) and `ensemble_snapshots.members_unit='degF'` is ALSO wrong — members are degC. A naive F->C conversion produced CRPS ~80 and mean_PIT=0.000 (whole member cloud below "obs"). Proof: in RAW numeric space (no conversion) BOTH C- and F-labelled cities show member_mean - obs = -1.0C with ZERO cells |gap|>20. **CORRECT HANDLING: compare members and settlement_value in their stored degC-numeric frame; do NOT trust settlement_unit / members_unit; guard |member_mean - obs|>20 as corruption.** Any per-city skill number computed with a unit conversion off these labels is wrong. This hazard sits underneath the whole forecast/settlement boundary and any future skill or calibration work must apply the raw-numeric rule.

---

## BIN-SELECTION DIAGNOSIS (why Tokyo / KL miss)

The OBSERVE baseline's Tokyo 0/5 and KL 0/4 are **systematic center BIAS**, not dispersion, not wrong-model, not a grid artifact.

- **The error is a persistent, learnable bias.** Pooled high lead=1 member_mean - settlement = **-1.0C** (ENS center runs ~1C cold). Monthly bias is roughly stationary across all 29 months (-0.5 to -1.4C, near-zero only in some summer months) -> a real seasonal-modulated systematic, not noise.
- **PIT exposes it as bias, not spread.** BEFORE mean_PIT = 0.684 pooled (ideal 0.5) -> the observation systematically lands in the UPPER tail of the predicted cloud = the cloud is centered too cold. Tokyo BEFORE PIT 0.805, KL 0.942, Singapore 0.877, Busan 0.960 -> these "loser" cities have the observation almost always ABOVE the entire member cloud. That is a center/bias miss, NOT a too-tight-spread miss (a spread miss would still center correctly).
- **Bias-correction moves PIT toward 0.5 AND lifts bin-hit:** Tokyo PIT 0.805->0.587, bin-hit 0.222->0.254, CRPS 1.24->0.83; KL PIT 0.942->0.751, bin-hit 0.097->0.129, CRPS 2.86->1.61; Singapore bin-hit 0.094->0.415; SF 0.000->0.146. Widening alone leaves the center wrong (Tokyo bin-hit 0.222->0.206) -> confirms the miss is location, not scale.
- **Underdispersion (QLCB's 3.24x) is the SEPARATE q_lcb problem.** It governs coverage/over-confidence (after-cost ruin), not which bin you pick. Widening fixes CRPS (47/50 cities) and PIT-coverage but barely touches bin-hit (8/50). So the two reports are consistent and orthogonal: bias-correction fixes SKILL (bin selection); widening fixes HONESTY (q_lcb). Do not conflate.

---

## BEFORE / AFTER (walk-forward, lead=1, ENS history)

### Candidate (a) per-city EB bias-correction (degC space; shrink city bias toward global, subtract from members)
POOLED n=4982: bin-hit 0.191 -> **0.259** (+6.8pt, +36% rel); CRPS 1.50 -> **1.21** (-19%); mean PIT 0.684 -> **0.537**.
Majority: bin-hit better in **35/50** cities (n>=20); CRPS better in 41/50.
**Robust to tuning** (eb_prior in {4,8,16} x min_train in {3,5,10}): bin-hit 0.256-0.265 every cell, PIT 0.53-0.55 every cell.
**Out-of-sample (temporal holdout, test ONLY on 2026, bias learned walk-forward from 2024-25):** bin-hit 0.164 -> **0.233** (+6.9pt); CRPS 1.75 -> 1.35; PIT 0.700 -> 0.562; majority 35/50. Clean generalization, not in-sample fitting.

### Candidate (c) dispersion widening (inflate per-member kernel to walk-forward realized residual sd)
POOLED bin-hit 0.191 -> 0.190 (flat); CRPS 1.50 -> 1.33; PIT_KS 0.347 -> 0.215. Majority bin-hit 8/50, CRPS 47/50.
Verdict: a CALIBRATION/COVERAGE fix (improves CRPS + PIT spread, ties directly to the q_lcb underdispersion remedy), NOT a bin-selection fix. Keep it for q_lcb honesty (QLCB FIX-C/D), do not credit it with skill.

### Candidate (a)+(c) both
POOLED bin-hit 0.259 (= (a) alone), CRPS 1.17 (best). Adds nothing to bin-hit over (a); only marginal CRPS. Parsimony: not worth the extra mechanism for the SKILL claim.

### Candidate (b) equal-weight GFS+ECMWF center blend (center-only, n=2642, high, lead=1)
RAW centers: gfs bin-hit 0.143 / MAE 2.55; ecmwf 0.153 / 2.59; **blend 0.160 / MAE 2.23** -> blend beats both singles; MAE drop (2.59->2.23) is the main gain.
EB-corrected centers: gfs 0.196; ecmwf 0.194; **blend 0.217 / MAE 1.98** -> blend still best.
**But bias-correction dominates blending:** EB-single-ecmwf (0.194) > RAW-blend (0.160). EB-ecmwf beats RAW-ecmwf in **31/51** cities; the blend's lift over EB-single is only 26/51 (bare majority).
Verdict: (b) is REAL and stacks with (a), but it is second-order. The decorrelated second model (GFS) earns a modest center-error reduction; the first-order win is still the bias term.

---

## RECOMMENDATION (parsimony-bounded)

1. **Adopt candidate (a): per-city walk-forward EB bias-correction of the 0.1 center.** Simplest, first-order, robust, generalizes out-of-sample, majority of cities, and directly fixes the Tokyo/KL bin-selection misses. The statistic already lives in `model_bias_ens` -> enable/route, do not rebuild. Apply to the live AIFS/Open-Meteo center the same per-city bias estimated against VERIFIED settlement.
2. **Optionally add candidate (b)** as a thin equal-weight average of two decorrelated centers (ECMWF + GFS) AFTER bias-correcting each -> EB-blend 0.217 vs EB-single 0.194. Cheap (a mean of two numbers), real, but a smaller increment; include only if the second feed is already in the pipeline. Do NOT escalate to learned weights (B6/B7: learned weights add ~nothing).
3. **Do NOT credit widening (c) with skill.** Route it to the q_lcb honesty track (QLCB FIX-C/D) where it belongs — it fixes coverage/over-confidence, not bin selection.
4. **Honest ceiling caveat (F4).** Even bias-corrected, pooled bin-hit is ~0.26 on 1C bins — many cities sit near the irreducible-error ceiling (a 1C bin against ~2.0-2.4C RMSE caps achievable hit-rate). Bias-correction recovers the systematic part; the rest is genuine forecast uncertainty. The honest claim is "removes a measurable systematic miss," not "makes the forecast sharp."
5. **q_lcb / after-cost win-rate (cohort-dependent) is NOT claimed here.** Per the brief and the old buy_yes-floor lesson, gate any trading-edge claim on the post-redeploy LIVE settled cohort, not this history.

---

## OPEN QUESTIONS

1. **AIFS-specific bias/spread is unmeasured.** All numbers use ECMWF-ENS as proxy. AIFS (live) has 0 settled cells. The bias DIRECTION will almost certainly transfer (same ECMWF family) but the AIFS magnitude must be re-fit once June+ AIFS posteriors settle (mirrors QLCB open-Q #2).
2. **Season-aware bias may beat the per-city constant.** The monthly bias series shows summer-vs-winter modulation; a per-(city,season) EB term (which `model_bias_ens` already keys on) could add a few more points — test before adding, parsimony first.
3. **Candidate (b) for the LOW metric is untested** — `historical_forecasts` carries only `forecast_high`. Low-metric multi-model blend needs a forecast_low feed that does not exist in this table.
4. **The -1.0C center bias vs the q_lcb underdispersion are layered** (QLCB open-Q #3): a correctly-widened q_lcb on a still-biased center is still warm/cold-shifted. Bias-correction (a) MUST precede or accompany any widening, or the wider interval just covers the wrong location.
5. **settlement_unit / members_unit mislabel (above) should be treated as a standing data-provenance defect**, not a one-off — any new skill/calibration consumer that trusts those unit fields will silently corrupt F-city numbers.
