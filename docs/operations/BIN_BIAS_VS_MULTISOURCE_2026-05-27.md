# Zeus Bin Bias vs Multi-Source Consensus Audit
**Date:** 2026-05-27  
**Branch:** feat/ft-ship-64  
**Scope:** READ-ONLY audit — no writes, no daemon restart, no trades  
**Analysis anchor:** Shanghai May-29 live trade (buy_yes 25°C @ 8¢, $1.34, entered on-chain)

---

## TL;DR — CORRECTED (2026-05-27, post sign-convention recheck)

> **Supersedes** the earlier "CONDITIONAL SAFE TO TRADE" conclusion written before the ENS bias_c sign was verified against source code. That text is preserved below marked `(superseded)`.

| Metric | Value |
|---|---|
| ENS bias_c sign convention | `corrected = member − effective_bias_c` — positive bias_c **cools** members |
| East-Asia cohort failure | **WRONG-DIRECTION correction** — bias_c=+1.25/+3.31 cools already-cold Shanghai/Qingdao ensemble |
| Dominant failure layer | **ENS** (wrong-dir, overshoot, insufficient — three distinct modes) |
| UNSOUND candidates (≥2°C) | **11** cities — HOLD on resume |
| SUSPECT candidates (1–2°C) | **11** cities — MONITOR only |
| SAFE to trade | **16** cities |
| Total accounted | 38 cities (16+11+11=38) |
| Trading-resume gate | **Trade SAFE-16 only; HOLD 22 (MONITOR+UNSOUND) until East-Asia bias_c refit + NYC overshoot addressed** |
| Authoritative verdict | The ENS full_transport_v1 correction is directionally inverted for East-Asia (Shanghai, Qingdao): it cools a cold-running ensemble, amplifying the defect; suppress or refit bias_c estimates before any East-Asia trade resumes. |

> **(superseded — pre-sign-correction read, 2026-05-27):** "CONDITIONAL SAFE TO TRADE — edge-trading mechanism naturally hedges peak-cold bias; NYC and East-Asia cluster warrant follow-up audit." This conclusion was written before `ens_error_model.py:141` was inspected and the correction direction confirmed. It is retained for audit trail only. **Do not act on it.**

---

## 1. Shanghai 25°C Trade Verdict

**A real trade fired: Shanghai May-29, buy_yes 25°C bin @ 8¢, $1.34, entered on-chain (2026-05-27T23:54).**

### Critical distinction: edge_bin ≠ p_cal argmax

Zeus does NOT trade the most probable bin — it trades the bin where `p_cal >> p_market` (edge). These are different:

| Metric | Bin | Label | Value |
|---|---|---|---|
| **p_cal argmax (peak belief)** | bin 0 | ≤23°C or below | p_cal=0.2796 |
| **Edge bin (TRADED)** | bin 2 | 25°C | p_cal=0.2615, p_market=0.0706 → edge=+0.1909 |

The market priced 25°C at 7.1¢ while Zeus's p_cal puts it at 26.2%. That gap is the trade signal — not a claim that 25°C is the most likely outcome.

### Source comparison for Shanghai May-29

| Source | Freshness | Forecast | Bin |
|---|---|---|---|
| Open-Meteo live (fresh, independent) | FRESH (fetched during audit) | **26.8°C** | bin 3 = 26°C |
| ECMWF open-data ensemble (51 members) | FRESH (2026-05-27 20:19, n=153) | 25.9°C | bin 3 = 26°C |
| ECMWF previous_runs | STALE ~5d | 26.4°C | bin 3 = 26°C |
| GFS previous_runs | STALE ~5d | 28.4°C | bin 5 = 28°C |
| Open-Meteo previous_runs | STALE ~5d | 27.2°C | bin 4 = 27°C |
| ICON previous_runs | STALE ~5d | 25.0°C | bin 2 = 25°C |
| UKMO previous_runs | STALE ~5d | 24.8°C | bin 2 = 25°C |

**All-source consensus: bin 3 = 26°C**  
**Source disagreement: 3 bins (bins 2–5)**

### Shanghai verdict

- **Edge bin (25°C) offset vs consensus:** −1 bin = **−1°C COLD**. Within inter-source disagreement (3 bins). **NOT flagged.**
- **p_cal peak offset vs consensus:** −3 bins = **−3°C COLD**. Calibration drift, not an edge-trade defect.
- **Operator's "~2°C cold" intuition:** Directionally correct for the peak belief; the traded bin itself is only −1°C from consensus, within noise.
- **Open-Meteo live independent check:** 26.8°C confirms consensus around 26–27°C. The 25°C trade is near the lower end of independent-source range but inside it.

**VERDICT: Shanghai 25°C trade is defensible as executed.** The trade is within noise relative to multi-source consensus. However, the subsequent sign-convention audit revealed the ENS correction is actively amplifying Shanghai's cold bias — the underlying model state is more concerning than the single trade suggests. Peak belief (≤23°C) reflects a structural defect, not noise.

---

## 2. All-Cities Bias Table

**Note on freshness:** All `forecasts` table NWP sources for target_date 2026-05-29 were retrieved 2026-05-22 (~5 days stale). The only fresh independent source is ECMWF open-data ensemble (recorded 2026-05-27 20:19, n=153 members). Stale NWP consensus pulls toward late-May values that may not match current forecasts, creating artificial flags.

Two consensus passes are shown:
- **All-source consensus:** mode across all 6 sources (5 stale NWP + fresh ECMWF ens)
- **Fresh-ens-only offset:** Zeus argmax vs ECMWF ensemble mean exclusively

| City | Unit | Zeus argmax | Zeus label | All-src cons | Offset (bins) | Fresh-ens offset | Flag (all-src) | Post-stale-correction |
|---|---|---|---|---|---|---|---|---|
| Amsterdam | C | bin 8 | 29°C | bin 0 = ≤21°C | +8 | +2 | FLAGGED | ARTIFACT (stale NWP cold) |
| Ankara | C | bin 5 | 18°C | bin 7 = 20°C | −2 | +2 | — | — |
| Atlanta | F | bin 10 | ≥84°F | bin 7 = 78–79°F | +3 | +3 | — | — |
| Austin | F | bin 10 | ≥82°F | bin 10 = ≥82°F | 0 | 0 | — | — |
| Buenos Aires | C | bin 6 | 16°C | bin 4 = 14°C | +2 | 0 | — | — |
| Busan | C | bin 5 | 25°C | bin 8 = 28°C | −3 | −1 | — | East-Asia cold cluster |
| Cape Town | C | bin 6 | 19°C | bin 3 = 16°C | +3 | 0 | — | — |
| Chengdu | C | bin 10 | ≥33°C | bin 10 = ≥33°C | 0 | +2 | — | — |
| Chicago | F | bin 10 | ≥84°F | bin 10 = ≥84°F | 0 | +1 | — | — |
| Chongqing | C | bin 6 | 24°C | bin 6 = 24°C | 0 | +1 | — | — |
| Dallas | F | bin 10 | ≥88°F | bin 10 = ≥88°F | 0 | +1 | — | — |
| Denver | F | bin 10 | ≥76°F | bin 10 = ≥76°F | 0 | 0 | — | — |
| Guangzhou | C | bin 7 | 37°C | bin 3 = 33°C | +4 | +3 | — | — |
| Helsinki | C | bin 5 | 18°C | bin 0 = ≤13°C | +5 | 0 | — | — |
| Hong Kong | C | bin 8 | 32°C | bin 8 = 32°C | 0 | +1 | — | — |
| Houston | F | bin 10 | ≥90°F | bin 10 = ≥90°F | 0 | +2 | — | — |
| Istanbul | C | bin 6 | 22°C | bin 5 = 21°C | +1 | +2 | — | — |
| Jeddah | C | bin 10 | ≥37°C | bin 7 = 34°C | +3 | +1 | — | — |
| Karachi | C | bin 10 | ≥37°C | bin 9 = 36°C | +1 | 0 | — | — |
| London | C | bin 7 | 27°C | bin 6 = 26°C | +1 | 0 | — | — |
| Madrid | C | bin 5 | 35°C | bin 4 = 34°C | +1 | 0 | — | — |
| Mexico City | C | bin 10 | ≥22°C | bin 10 = ≥22°C | 0 | 0 | — | — |
| Miami | F | bin 2/4 | 84–85°F | varies | ±1–2 | 0 | — | — |
| Milan | C | bin 5 | 29°C | bin 5 = 29°C | 0 | 0 | — | — |
| Moscow | C | bin 6 | 10°C | bin 7 = 11°C | −1 | −1 | — | — |
| Munich | C | bin 6 | 25°C | bin 2 = 21°C | +4 | 0 | — | — |
| NYC | F | bin 7 | 82–83°F | bin 0 = ≤69°F | +7 | +3 | **FLAGGED** | **GENUINE WARM BIAS** |
| Panama City | C | bin 7 | 29°C | bin 6 = 28°C | +1 | +1 | — | — |
| Paris | C | bin 6 | 33°C | bin 0 = ≤27°C | +6 | +1 | FLAGGED | ARTIFACT (stale NWP cold) |
| Qingdao | C | bin 0 | ≤26°C | bin 5 = 31°C | −5 | −3 | — | East-Asia cold cluster |
| San Francisco | F | bin 10 | ≥66°F | bin 8 = 62–63°F | +2 | +5 | — | — |
| Sao Paulo | C | bin 4 | 20°C | bin 4 = 20°C | 0 | 0 | — | — |
| Seattle | F | bin 10 | ≥68°F | bin 8 = 64–65°F | +2 | +5 | — | — |
| Seoul | C | bin 10 | ≥27°C | bin 8 = 25°C | +2 | +1 | — | — |
| **Shanghai** | **C** | **bin 0** | **≤23°C** | **bin 3 = 26°C** | **−3** | **−3** | **—** | **East-Asia cold cluster** |
| Shenzhen | C | bin 6 | 33°C | bin 6 = 33°C | 0 | 0 | — | — |
| Singapore | C | bin 8 | 32°C | bin 7 = 31°C | +1 | +2 | — | — |
| Taipei | C | bin 6 | 29°C | bin 4 = 27°C | +2 | 0 | — | — |
| Tel Aviv | C | bin 4 | 24°C | bin 2 = 22°C | +2 | +1 | — | — |
| Tokyo | C | bin 3 | 24°C | bin 7 = 28°C | −4 | 0 | — | East-Asia cold cluster |
| Warsaw | C | bin 6 | 22°C | bin 2 = 18°C | +4 | 0 | — | — |
| Wuhan | C | bin 6 | 29°C | bin 6 = 29°C | 0 | +2 | — | — |

---

## 3. Direction Test

### Population-level statistics

| Metric | All-source | Fresh-ens-only |
|---|---|---|
| N cities | 45 | 45 |
| Mean offset | +1.22 bins | **+0.78 bins** |
| Median offset | +1.00 bins | **0.00 bins** |
| Positive (WARM) | 24 | 22 |
| Zero | 13 | 19 |
| Negative (COLD) | 8 | 4 |

**Population verdict: NOT systematically cold.** Fresh-ens-only median = 0 bins. Mean +0.78 bins indicates a slight WARM tilt population-wide. Cold outliers are a minority (4 cities vs 22 warm).

### East-Asia cold cluster (named finding)

Five cities show consistent COLD offset when measured against fresh ECMWF ensemble:

| City | Zeus argmax | ECMWF ens mean | Offset |
|---|---|---|---|
| Shanghai | ≤23°C | 25.9°C | **−2.9°C** |
| Qingdao | ≤26°C | 28.6°C | **−2.6°C** |
| Busan | 25°C | 25.8°C | **−0.8°C** |
| Tokyo | 24°C | 23.7°C | 0 (resolved: Tokyo ens 23.7°C aligns with bin 3 = 24°C) |
| Moscow | 10°C | 10.7°C | **−0.7°C** |

Shanghai and Qingdao show the strongest signal (−2.6 to −2.9°C). **Post sign-convention audit this is not merely "calibration cold-drift" — the ENS correction is actively amplifying it.** See §6 (Sign Convention) for full analysis.

### All-source vs fresh-ens-only flags: stale NWP artifacts

The 4 "FLAGGED" cities from all-source consensus (Amsterdam, London, NYC, Paris) include 3 artifacts:

| City | All-source flag | Root cause | Fresh-ens verdict |
|---|---|---|---|
| **Amsterdam** | +8 bins WARM | Stale NWP (5d ago) show 20–24°C; fresh ens = 27.5°C; consensus pulled to bin 0 | Zeus = 29°C, fresh ens = 27.5°C → **+1.5°C warm, NOT flagged** |
| **London** | +10 bins WARM (stale trace) | Earlier trace for target 2026-05-28 with different bin schema | Latest trace Zeus=27°C, fresh ens=27.0°C → **0 offset, NOT flagged** |
| **NYC** | +7 bins WARM | Stale NWP show 58–74°F; fresh ens = 75.9°F; Zeus = 82–83°F | Zeus vs fresh ens = **+3 bins (+6.3°F warm), GENUINE FLAG** |
| **Paris** | +6 bins WARM | Stale NWP show 22–27°C; fresh ens = 32.0°C; Zeus = 33°C | Zeus vs fresh ens = **+1°C warm, NOT flagged** |

**Post-stale-correction genuine flags: 1 (NYC)**

---

## 4. Source Freshness Summary

| Source | Kind | Data for May-29 | Freshness | Notes |
|---|---|---|---|---|
| Open-Meteo live | External API | 26.8°C (Shanghai) | FRESH (fetched 2026-05-27 during audit) | Only fetched for Shanghai as independent confirmation |
| ECMWF open-data ensemble | ensemble_snapshots_v2 | 51 members, n=153 | FRESH (2026-05-27 20:19) | Zeus's own ingest path — not fully independent |
| ECMWF previous_runs | forecasts table | Retrieved 2026-05-22 | **STALE ~5d** | May not reflect current synoptic pattern |
| GFS previous_runs | forecasts table | Retrieved 2026-05-22 | **STALE ~5d** | |
| Open-Meteo previous_runs | forecasts table | Retrieved 2026-05-22 | **STALE ~5d** | |
| ICON previous_runs | forecasts table | Retrieved 2026-05-22 | **STALE ~5d** | |
| UKMO previous_runs | forecasts table | Retrieved 2026-05-22 | **STALE ~5d** | |
| TIGGE | Gated | N/A | OFFLINE (disabled by flag) | Requires operator decision to enable |
| Tomorrow.io / Wunderground / NOAA NWS | External APIs | N/A | NOT INGESTED | No ingest path in zeus source registry |

**Independence note:** ECMWF open-data ensemble is Zeus's own primary ingest path. "Zeus vs ECMWF ens" measures calibration shift from input, not fully independent forecast bias. The only truly independent fresh check was the live Open-Meteo fetch for Shanghai (26.8°C), which confirms the consensus direction.

---

## 5. Recommendation — CORRECTED (supersedes pre-sign-correction text)

> **(superseded — pre-sign-correction, 2026-05-27):** "CONDITIONAL YES — Shanghai trade within noise; East-Asia cluster requires follow-up calibration audit; NYC warm bias is the only genuine post-correction flag; this audit does NOT find evidence requiring an immediate flag OFF." This recommendation was written before the ENS correction sign was verified. It is retained for audit trail. **Do not act on it.**

### Corrected recommendation

**HOLD 22 cities. Trade SAFE-16 only on resume.**

Three findings changed the pre-sign recommendation:

1. **East-Asia ENS correction is WRONG-DIRECTION** (not "insufficient"). Shanghai (bias_c=+1.254) and Qingdao (bias_c=+3.306) receive a *cooling* correction on an ensemble that already runs cold. The correction amplifies the defect. This is a structural model error requiring bias_c refit or suppression — not a calibration tune.

2. **NYC ENS overcorrection is confirmed** (+3.5°C warming applied; result remains +3.0°C warm vs OM). The correction overshot neutral.

3. **11 additional UNSOUND cities** identified across Platt-distortion (Atlanta), shoulder-pile (Jeddah, Karachi), fail-open cold (Chongqing, Helsinki), and ENS-insufficient (Guangzhou, Wuhan) categories.

### Trading-resume gate

| Tier | Cities | Action |
|---|---|---|
| **SAFE (16)** | Austin, Cape Town, Chengdu, Dallas, Istanbul, London, Madrid, Mexico City, Moscow, Panama City, Paris, Sao Paulo, Seoul¹, Shenzhen, Singapore, Warsaw | **Trade on resume** |
| **MONITOR (11)** | Amsterdam, Ankara, Buenos Aires, Busan, Hong Kong, Milan, Munich, Qingdao², San Francisco, Taipei, Tel Aviv | **HOLD until recheck** |
| **HOLD (11)** | Atlanta, Chicago, Chongqing, Guangzhou, Helsinki, Jeddah, Karachi, NYC, Seattle, Shanghai, Wuhan | **DO NOT TRADE** |

¹Seoul FAIL-OPEN (bias_c=−3.034 suppressed, corr_str=0) but current offset +1.1°C warm is within resid_sd=2.271°C noise. Monitor if edge targets ≥27°C shoulder.  
²Qingdao is borderline MONITOR/HOLD — ENS wrong-direction, bias_c estimate is the defect. Recommend treating as HOLD until bias_c refit.

**Do not auto-trade any WRONG-DIR cohort city (Shanghai, Qingdao) regardless of edge signal until bias_c is refit and sign verified against live outcomes.**

---

## 6. ENS Bias Sign Convention (Source-Verified)

**This section was added 2026-05-27 after inspecting `src/calibration/ens_error_model.py:141`. It supersedes all prior narrative about correction direction.**

### Code path

```python
# src/calibration/ens_error_model.py:141
corrected = np.asarray(member_extrema, dtype=float) - eff_bias_native
```

Where `eff_bias_native = effective_bias_c * scale` and `effective_bias_c = λ * bias_c`.

**`bias_c` = how warm the ensemble runs relative to truth.**  
Positive bias_c → ensemble estimated to run warm → correction **subtracts** → members are **cooled**.  
Negative bias_c → ensemble estimated to run cold → correction **adds** → members are **warmed**.

### Worked examples

| City | bias_c | Direction | Applied correction | Observed cal_peak vs OM | Assessment |
|---|---|---|---|---|---|
| **Shanghai** | +1.254°C | System infers ensemble runs 1.25°C warm | **Cools** members by 1.25°C | −3.8°C COLD | **WRONG-DIR**: correction amplifies cold bias. bias_c estimate is the defect. |
| **Qingdao** | +3.306°C | System infers ensemble runs 3.3°C warm | **Cools** members by 3.3°C | −2.0°C COLD (uncorrected ≈ OM=28°C) | **WRONG-DIR**: correction drove a near-correct ensemble cold. bias_c estimate is the defect. |
| **NYC** | −3.467°C | System infers ensemble runs 3.5°C cold | **Warms** members by 3.5°C | +3.0°C WARM | **OVERSHOOT**: correction swung through neutral. Corrective direction correct, magnitude too large. |
| **Istanbul** | −2.375°C | System infers ensemble runs 2.4°C cold | **Warms** members by 2.4°C | +0.3°C warm | **EFFECTIVE**: correction brought ensemble to near-consensus. |

### Sign-convention verification caveat

This reinterpretation came mid-analysis. Before any refit action is taken, independently confirm:
1. The ensemble genuinely runs warm for Shanghai/Qingdao in the training period (i.e., bias_c=+1.25/+3.31 was estimated from real warm-biased TIGGE data, not from a downstream defect in the pairing process).
2. That the timezone-extraction bug (MEMORY: `project_forecast_cold_bias_timezone_2026_05_22`) affected the *training pairs* used to fit bias_c, not just the live inference path.

If point 2 is confirmed (timezone bug corrupted training pairs → spurious positive bias_c estimated → cooling correction on cold ensemble), the root cause and remediation in §7 stand.

---

## 7. Root Cause and Remediation

### Root cause

The timezone-extraction defect (documented in MEMORY: `project_forecast_cold_bias_timezone_2026_05_22`, identified 2026-05-22) causes systematic daily-max under-extraction for Asian cities due to timezone misalignment in the TIGGE ingestion path. This produces member_extrema values that are systematically low for East-Asian cities.

When these defective low member values were paired against real observed highs to fit `model_bias_ens_v2`, the pairing shows:  
`observed_high − member_max > 0` → observation warmer than member → bias_c estimated as **positive (ensemble runs warm)**.

But this positive bias_c estimate is spurious — the ensemble is not actually warm-biased for Shanghai/Qingdao. The member_extrema were under-extracted due to the timezone defect. The true ensemble runs cold (or near-neutral) for these cities.

Result: full_transport_v1 stores positive bias_c for Shanghai/Qingdao → applies cooling correction at inference → amplifies the actual cold bias.

### Failure chain

```
Timezone misalignment in TIGGE ingest
  → member_extrema underestimated (daily-max extracted from wrong local-time window)
  → training pairs: obs_high >> defective_member_max
  → bias_c estimated as large positive (spurious "ensemble runs warm" signal)
  → full_transport_v1 applies cooling correction at inference
  → already-cold ensemble cooled further
  → p_cal peak cold vs reality by 3–4°C
  → edge trades at bins near the cold peak → systematic cold-side edges
```

### Remediation

**Correct action:** Suppress or refit East-Asia bias_c estimates using correctly-extracted member_extrema.

**Incorrect action:** Applying a larger warm correction (increasing correction magnitude or adding a second layer). That would address the symptom without fixing the defective bias_c estimate and could interact with future correct refits unpredictably.

**Recommended steps:**
1. Confirm timezone-extraction fix is live in the current ingest path (check MEMORY: `project_forecast_cold_bias_timezone_2026_05_22` for resolution status).
2. If fix is live: re-run full TIGGE historical ingest for East-Asian cities (Shanghai, Qingdao, Busan, Tokyo) and refit `model_bias_ens_v2` with corrected member_extrema.
3. If fix is not live: suppress bias_c for East-Asian cities (set correction_strength=0, fail-open) until refit is possible — this removes the wrong-direction amplification.
4. After refit: verify new bias_c sign and magnitude against holdout before re-enabling correction.
5. Re-run Platt calibration for East-Asian cities after bias_c refit (calibration pairs used bias-corrected members; stale Platt is trained on defective p_raw).

**Do not re-enable East-Asia bias correction without confirming the sign of the new bias_c estimate against live outcomes.**

---

## 8. Full Bias Decomposition — All Layers

**Method:** READ-ONLY query of zeus-world.db + fresh open-meteo fetch (38/38 cities, target 2026-05-29)  
**Reference DBs:** `state/zeus-world.db` (VERIFIED rows), `state/zeus-forecasts.db` (0 ft_v1 rows — all ENS bias is in world.db)

### Layer 1: ENS Bias — `model_bias_ens_v2` (family=full_transport_v1, metric=high, season=MAM)

All 38 cities have VERIFIED rows in `zeus-world.db`. Season: MAM (March–April–May).

**Cities with |bias_c| > 2°C:**

| City | bias_c (°C) | effective_bias_c | corr_str | resid_sd | Note |
|---|---|---|---|---|---|
| **Dallas** | −10.021 | −10.021 | 1.000 | 2.118 | STRUCTURALLY SUSPICIOUS: \|bias_c\|=10°C unrealistic; likely estimator misfit (shoulder saturation → inflated estimate). Shoulder-same with OM → SOUND, but magnitude needs investigation. |
| **Jeddah** | −6.987 | −6.987 | 1.000 | 1.772 | Warms members +7°C; still +4.8°C warm vs OM → insufficient |
| **NYC** | −3.467 | −3.467 | 1.000 | 1.522 | Warms members +3.5°C; result +3.0°C warm vs OM → OVERSHOOT |
| **Seattle** | −3.441 | −3.441 | 1.000 | 2.245 | Warms members +3.4°C; uncorrected peak ≈ OM; after correction ≈16.9°C; Platt A=2.70 then dumps 55% into ≥68°F shoulder — Platt is primary driver |
| **San Francisco** | −3.243 | −3.243 | 1.000 | 1.664 | Warms +3.2°C; result +1.4°C warm → partial |
| **Seoul** | −3.034 | **0.000** | **0.000** | 2.271 | **FAIL-OPEN**: large known bias suppressed (corr_str=0) |
| **Guangzhou** | −2.792 | −2.792 | 1.000 | 2.317 | Warms +2.8°C; still +2.5°C warm vs OM → insufficient |
| **Istanbul** | −2.375 | −2.375 | 1.000 | 0.939 | Warms +2.4°C; result +0.3°C → effective |
| **Chicago** | −2.273 | −0.142 | 0.063 | 1.854 | Near fail-open (6% strength); still +4.8°C warm vs OM |
| **Hong Kong** | −2.108 | −2.108 | 1.000 | 1.356 | Warms +2.1°C; result −1.7°C cold → partial under-correction |
| **Austin** | −2.082 | −2.082 | 1.000 | 1.880 | Warms +2.1°C; shoulder-same as OM → SOUND |
| **Qingdao** | **+3.306** | **+3.306** | 1.000 | 1.722 | **WRONG-DIR**: cools members by 3.3°C; uncorrected ensemble ≈ OM=28°C → correction drove peak to ≤26°C (−2°C cold). bias_c estimate is the defect. |

**Cities with correction_strength=0 (FAIL-OPEN):**  
Chengdu, Chongqing, Helsinki, London, Madrid, Mexico City, Milan, Moscow, **Seoul**, Shenzhen  
(10 cities; Seoul highest-risk at bias_c=−3.034 suppressed)

**East-Asia wrong-direction summary:** See §6. Shanghai (bias_c=+1.254) and Qingdao (bias_c=+3.306) both receive cooling corrections on cold-running ensembles. The bias_c estimates are the defect, not the correction magnitude.

### Layer 2: Platt Calibration — `platt_models_v2` (family=full_transport_v1, metric=high, season=MAM)

Two rows per city (primary = larger n_samples). All VERIFIED. Normal slope range: A ≈ 1.3–2.1.

**Extreme Platt params (A > 2.5 or structural concern):**

| City | A (slope) | B (intercept) | n_samples | Risk |
|---|---|---|---|---|
| **Mexico City** | **4.653** | 0.051 | 1,993 | EXTREME — hyper-sharpens; shoulder pile risk |
| **Beijing** | 2.727 | 0.055 | 2,010 | HIGH — no live trace (pre_vector_unavailable) |
| **Seattle** | 2.700 | 0.063 | 1,985 | HIGH — primary driver of +6.5°C warm offset |
| **Tel Aviv** | 2.554 | 0.058 | 1,986 | HIGH — sharpens upper tail |
| **Seoul** | 2.530 | 0.017 | 2,002 | HIGH — fail-open ENS + sharp Platt → 49% at ≥27°C vs OM=25.9°C |
| **Chicago** | 2.231 | 0.083 | 1,989 | MODERATE — amplifies warm shoulder |
| **Dallas** | 2.267 | 0.049 | 1,985 | MODERATE — shoulder-same, no net distortion |
| **NYC** | 2.112 | 0.084 | 1,985 | MODERATE — reinforces ENS overshoot |
| **Atlanta** | 2.016 | 0.054 | 1,985 | MODERATE — **sole large Platt peak shift**: raw 81°F → cal ≥84°F (+1.94°C) |
| **Denver** | 1.960 | 0.083 | 1,985 | MODERATE |

Southern Hemisphere (Buenos Aires, Cape Town, Sao Paulo): JJA/SON proxy seasons, slope 1.32–1.88 — normal.

**Platt peak shifts:** Atlanta only (+1.94°C, A=2.02 doubles shoulder probability). All other cities: 0°C peak shift (distribution reshaping without bin crossing).

### Layer 3: Per-Candidate Decomposition + Verdicts (All 38 Cities, May 29 Target)

**Consensus source:** Open-Meteo API (fetched 2026-05-27, all 38 cities).  
**Shoulder-bin correction:** Dallas (≥88°F), Austin (≥82°F), Mexico City (≥22°C) — Zeus peak = OM bin → offset = 0.

| City | p_raw peak | p_cal peak | OM (°C) | Cal off | ENS eff_bc | Platt shift | **Tier** | Root cause |
|---|---|---|---|---|---|---|---|---|
| Austin | 82°F+ | 82°F+ | 33.4 | 0 | −2.08 | 0 | **SAFE** | Shoulder-same |
| Cape Town | 19°C | 19°C | 18.5 | +0.5 | −0.59 | 0 | **SAFE** | ENS-partial |
| Chengdu | ≥33°C | ≥33°C | 32.2 | +0.8 | 0 | 0 | **SAFE** | Fail-open/small bias |
| Dallas | ≥88°F | ≥88°F | 33.7 | 0 | −10.02 | 0 | **SAFE** | Shoulder-same |
| Istanbul | 22°C | 22°C | 21.7 | +0.3 | −2.38 | 0 | **SAFE** | ENS effective |
| London | 27°C | 27°C | 26.6 | +0.4 | 0 | 0 | **SAFE** | Fail-open/small bias |
| Madrid | 35°C | 35°C | 34.7 | +0.3 | 0 | 0 | **SAFE** | Fail-open/small bias |
| Mexico City | ≥22°C | ≥22°C | 27.7 | 0 | 0 | 0 | **SAFE** | Shoulder-same |
| Moscow | 10°C | 10°C | 9.6 | +0.4 | 0 | 0 | **SAFE** | Fail-open/small bias |
| Panama City | 29°C | 29°C | 29.2 | −0.2 | −0.62 | 0 | **SAFE** | ENS-partial |
| Paris | 33°C | 33°C | 33.8 | −0.8 | −1.21 | 0 | **SAFE** | ENS-partial |
| Sao Paulo | 20°C | 20°C | 20.0 | 0 | +0.26 | 0 | **SAFE** | ENS-partial |
| Seoul | ≥27°C | ≥27°C | 25.9 | +1.1 | 0 (fail-open) | 0 | **SAFE**¹ | FAIL-OPEN; offset within resid_sd |
| Shenzhen | 33°C | 33°C | 32.7 | +0.3 | 0 | 0 | **SAFE** | Fail-open/small bias |
| Singapore | 32°C | 32°C | 31.6 | +0.4 | −1.51 | 0 | **SAFE** | ENS-partial |
| Warsaw | 22°C | 22°C | 22.8 | −0.8 | −0.02 | 0 | **SAFE** | ENS-partial |
| Amsterdam | 29°C | 29°C | 30.8 | −1.8 | −0.25 | 0 | **MONITOR** | ENS under-corrected |
| Ankara | 18°C | 18°C | 19.7 | −1.7 | −1.61 | 0 | **MONITOR** | ENS-partial |
| Buenos Aires | 16°C | 16°C | 14.9 | +1.1 | +0.10 | 0 | **MONITOR** | ENS-partial small |
| Busan | 25°C | 25°C | 23.3 | +1.7 | +0.97 | 0 | **MONITOR** | ENS-partial |
| Hong Kong | 32°C | 32°C | 33.7 | −1.7 | −2.11 | 0 | **MONITOR** | ENS residual |
| Milan | 29°C | 29°C | 30.9 | −1.9 | 0 | 0 | **MONITOR** | Fail-open/forecast-disagr |
| Munich | 25°C | 25°C | 26.8 | −1.8 | −0.73 | 0 | **MONITOR** | ENS-partial |
| Qingdao | ≤26°C | ≤26°C | 28.0 | −2.0 | +3.31 | 0 | **MONITOR**² | ENS-WRONG-DIR |
| San Francisco | ≥66°F | ≥66°F | 17.5 | +1.4 | −3.24 | 0 | **MONITOR** | ENS-mismatch |
| Taipei | 29°C | 29°C | 27.5 | +1.5 | −1.20 | 0 | **MONITOR** | ENS-partial |
| Tel Aviv | 24°C | 24°C | 25.3 | −1.3 | −1.39 | 0 | **MONITOR** | ENS-partial (FDR-filtered) |
| **Atlanta** | **81°F** | **≥84°F** | **23.2** | **+5.7** | −0.09 | **+1.94** | **HOLD** | **PLATT-DISTORTION** |
| **Chicago** | **≥84°F** | **≥84°F** | **24.1** | **+4.8** | −0.14 | 0 | **HOLD** | **FORECAST-DISAGR (60% at ≥84°F vs OM=75°F)** |
| **Chongqing** | **24°C** | **24°C** | **26.3** | **−2.3** | 0 | 0 | **HOLD** | **FAIL-OPEN cold** |
| **Guangzhou** | **≥37°C** | **≥37°C** | **34.5** | **+2.5** | −2.79 | 0 | **HOLD** | **SHOULDER-WARM (insufficient correction)** |
| **Helsinki** | **18°C** | **18°C** | **20.1** | **−2.1** | 0 | 0 | **HOLD** | **FAIL-OPEN cold** |
| **Jeddah** | **≥37°C** | **≥37°C** | **32.2** | **+4.8** | −6.99 | 0 | **HOLD** | **SHOULDER-PILE (99.98% at ≥37°C)** |
| **Karachi** | **≥37°C** | **≥37°C** | **34.0** | **+3.0** | +1.37 | 0 | **HOLD** | **SHOULDER-PILE (100% at ≥37°C)** |
| **NYC** | **82–83°F** | **82–83°F** | **25.1** | **+3.0** | −3.47 | 0 | **HOLD** | **ENS-OVERSHOOT (+3.5°C warms, remains +3.0°C warm)** |
| **Seattle** | **≥68°F** | **≥68°F** | **13.5** | **+6.5** | −3.44 | 0 | **HOLD** | **PLATT+ENS (A=2.70; 55% in shoulder vs OM=56°F)** |
| **Shanghai** | **≤23°C** | **≤23°C** | **26.8** | **−3.8** | +1.25 | 0 | **HOLD** | **ENS-WRONG-DIR (cools cold ensemble; live edge active)** |
| **Wuhan** | **29°C** | **29°C** | **26.9** | **+2.1** | −1.25 | 0 | **HOLD** | **ENS-INSUFFICIENT** |

¹Seoul FAIL-OPEN: corr_str=0, bias_c=−3.034 suppressed. Offset +1.1°C is within resid_sd=2.271°C. Treat as SAFE while monitoring; would move to MONITOR if edge targets ≥27°C shoulder.  
²Qingdao is border MONITOR/HOLD. ENS-WRONG-DIR and bias_c estimate is defective. Recommended action: treat as HOLD until bias_c refit.

**Tier counts: SAFE=16, MONITOR=11, HOLD=11. Total=38. ✓**

### Layer 4: Dominant Bias Source by Cohort

| Cohort | Cities | Dominant failure | Key fact |
|---|---|---|---|
| **East-Asia WRONG-DIR** | Shanghai, Qingdao | ENS-WRONG-DIR | bias_c=+1.25/+3.31 → cooling correction on cold ensemble. Root: timezone-extraction defect in training pairs. Fix = suppress/refit. |
| **US warm shoulder-pile** | Atlanta, Chicago, Seattle, NYC | PLATT + ENS-OVERSHOOT | Atlanta: Platt A=2.02 shifts peak +1.94°C. Seattle: Platt A=2.70 dumps 55% into ≥68°F. NYC: ENS overshoots by +3°C. |
| **MENA shoulder-pile** | Jeddah, Karachi, Guangzhou | SHOULDER-PILE | 99–100% probability in ≥37°C bin; OM says 32–35°C. Bin granularity inadequate. |
| **EU cold fail-open** | Chongqing, Helsinki | FAIL-OPEN cold | correction_strength=0 despite being cold vs OM. |
| **EU partial-correction** | Amsterdam, Ankara, Munich, Milan | ENS-PARTIAL | −1.7 to −1.9°C cold; FT correction under-corrects European cities. |
| **Effective corrections** | Istanbul, Paris, Singapore, Austin, Panama City | ENS-effective | Correction within ±0.5°C of OM. |

### Layer 5: Fail-Open Cities Detail

| City | bias_c | corr_str | Tier | Risk |
|---|---|---|---|---|
| **Seoul** | −3.034°C | 0.000 | SAFE (+1.1°C) | MEDIUM — large known bias suppressed |
| Chongqing | −0.254°C | 0.000 | HOLD (−2.3°C) | HIGH — cold offset despite small bias_c |
| Helsinki | −0.473°C | 0.000 | HOLD (−2.1°C) | HIGH — cold offset despite small bias_c |
| Milan | −0.396°C | 0.000 | MONITOR (−1.9°C) | MEDIUM |
| Mexico City | −0.507°C | 0.000 | SAFE (0.0°C) | LOW — shoulder-same |
| London | −0.275°C | 0.000 | SAFE (+0.4°C) | LOW |
| Shenzhen | −0.339°C | 0.000 | SAFE (+0.3°C) | LOW |
| Chengdu | −0.345°C | 0.000 | SAFE (+0.8°C) | LOW |
| Madrid | +0.452°C | 0.000 | SAFE (+0.3°C) | LOW |
| Moscow | +0.486°C | 0.000 | SAFE (+0.4°C) | LOW |

---

## 9. Authoritative 3-Tier Classification

### SAFE — Trade on Resume (16 cities)
Austin, Cape Town, Chengdu, Dallas, Istanbul, London, Madrid, Mexico City, Moscow, Panama City, Paris, Sao Paulo, Seoul, Shenzhen, Singapore, Warsaw

### MONITOR — Hold Until Recheck (11 cities)
Amsterdam, Ankara, Buenos Aires, Busan, Hong Kong, Milan, Munich, Qingdao, San Francisco, Taipei, Tel Aviv

### HOLD — Do Not Trade (11 cities)
Atlanta, Chicago, Chongqing, Guangzhou, Helsinki, Jeddah, Karachi, NYC, Seattle, Shanghai, Wuhan

**Count check: 16 + 11 + 11 = 38 ✓**

**Trading-resume instruction:** On trading resumption, enable only SAFE-16. MONITOR and HOLD cities require the following before re-enabling:
- **WRONG-DIR (Shanghai, Qingdao):** bias_c refit with corrected member_extrema; sign verification against live outcomes
- **OVERSHOOT (NYC):** bias_c refit or correction_strength tuning
- **PLATT-DISTORTION (Atlanta, Seattle):** Platt refit or slope clamp at A ≤ 2.0
- **SHOULDER-PILE (Jeddah, Karachi, Guangzhou):** Bin structure review
- **FAIL-OPEN cold (Chongqing, Helsinki):** Root-cause investigation of large cold offset despite small or zero bias_c

---

## Appendix: Methodology

### Bin assignment
- `probability_trace_fact.p_cal_json`: Zeus's calibrated per-bin probability vector
- `p_cal argmax`: bin with highest Zeus probability
- `edge_bin_idx`: bin where `p_cal − p_market` is largest
- Bin labels from `bin_labels_json`; source forecast temps mapped to bins via nearest-bin matching

### Consensus method
- All-source: mode across available source bins (stale NWP + fresh ens)
- Fresh-ens-only: Zeus p_cal argmax vs ECMWF ensemble mean

### Stale-NWP artifact detection
- NWP previous_runs retrieved 2026-05-22 (~5 days stale for 2026-05-29 target)
- Post-correction analysis uses fresh ECMWF ensemble as primary benchmark

### Traces used
- `probability_trace_fact` rows with `recorded_at > '2026-05-27T22:27:00'`
- Target date: 2026-05-29 (primary); 45 city-target_date pairs in §2–3, 38 cities with full bias decomposition in §8

### Sign-convention audit
- Source: `src/calibration/ens_error_model.py`, line 141
- Audited: 2026-05-27 during this session
- Prior to audit: correction direction was inferred from expected behavior, not verified

**[LIMITATION]** Open-meteo is a single external source; inter-model disagreement ±1–2°C. MONITOR cities bordering HOLD (Qingdao, Milan, Amsterdam) should be re-evaluated when fresh ECMWF ensemble ingest completes. Platt peak-shift analysis uses p_raw argmax as proxy — continuous Platt transformation understated by point-bin attribution. Seoul SAFE verdict rests on offset within resid_sd noise; structural fail-open risk remains.
