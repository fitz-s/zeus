# SD3 Live-Readiness Analysis — Shadow vs Realtime Forecast

- Created: 2026-05-28
- Author: autonomous session 866db2ea (Opus)
- Authority basis: operator directive 2026-05-28 — "shadow live + compare decision to actual forecast on website", "report fully … multiple hypotheses, clear before/after"
- Scope: READ-ONLY analysis. No DB writes, no flag changes, no promotion. Live trading remains SHADOW (`full_transport_live_enabled=false`).

---

## 0 — TL;DR (decision-grade)

1. **Shadow's live decisions are NOT produced by sd3.** They are the raw ensemble forecast with **zero error-model correction** (FT flag OFF). sd3 has never touched live, placed no order, produced no fill.
2. **sd3 is over-biased and would HARM accuracy.** Bin-independent test vs the realtime open-meteo forecast: raw ensemble is already within **1.37 °C** mean-abs of reality (HIGH); applying sd3 pushes it to **1.93 °C** the wrong way (over-warm). FT helps 68 cities / hurts 77.
3. **46 % of sd3 city-bias values exceed 1 °C; 18 % exceed 2 °C; worst is Jeddah −6.84 °C** — physically impossible as real forecast bias. These are artifacts.
4. **VERDICT: do NOT promote sd3, do NOT unshadow.** Promoting it places systematically wrong-side (over-warm) bets = losing money, the opposite of the profit directive.
5. **sd3 training data is GONE (decisive).** The residual pairs that produced sd3's biases are not retained in any accessible DB (`ens_refit_full_2026-05-25.db` is empty; staging has no `ensemble_snapshots`/settlements). The Jeddah −6.84 °C came from 29 pairs that no longer exist. The fit is **unauditable** — this alone blocks promotion under Constraint #4, independent of the accuracy regression.

---

## 1 — How the live "betting" decisions were obtained (operator's direct question)

> "如果sd3还没有promote那么你如何拿到的最新的下注成果？"

The decisions come from the **running `forecast-live` daemon on the pre-sd3 path**, not from sd3:

- The daemon reads the **live ensemble forecast** (`ensemble_snapshots_v2`, freshly issued 12Z today) → Monte-Carlo → `p_raw` probability vector over the market's temperature bins.
- Because `full_transport_live_enabled = false`, **no error-model correction is applied** — entry + monitor both use plain `p_raw`.
- These probabilities are logged to `probability_trace_fact` (70 clean rows in the last 12 h).

**sd3 appears in this analysis only as a SIMULATED overlay.** To estimate what sd3 *would* do, I applied its bias analytically: `gap_after = gap_before − bias_c`. No sd3-derived decision was ever read from a live table. The "after" column everywhere below is a simulation of promotion, not a measured result.

Live decision outcome breakdown (12 h, raw ensemble, FT OFF):

| trace_status / stage | count |
|---|---|
| complete · EDGE_INSUFFICIENT | 95 |
| pre_vector_unavailable · MARKET_FILTER | 29 |
| degraded · SIGNAL_QUALITY/DATA_UNAVAILABLE | 15 |
| complete (tradeable) | 12 |
| other (risk/FDR/signal) | ~22 |
| **orders placed** | **0** (flag off + shadow) |

So there is no profit/fill data — only computed probabilities. The system is decision-capable but trading-gated.

---

## 2 — Data provenance (verified state, 2026-05-28)

| surface | state | provenance |
|---|---|---|
| `full_transport_live_enabled` | `false` | settings.json:338 — shadow uses plain p_raw |
| live `model_bias_ens_v2` (world.db) | 71 HIGH + 8 LOW VERIFIED | **pre-sd3**, recorded 2026-05-25/27; schema has **no** `gate_set_hash` col |
| live `platt_models_v2` (world.db) | 137 HIGH ft_v1 is_active=1 | **pre-sd3**, fitted 2026-05-27 04:34 |
| live LOW ft_v1 Platt | absent (only `low\|none\|596`) | LOW ft path would FAIL-OPEN to plain p_raw |
| sd3 `model_bias_ens_v2` (staging) | 71 HIGH + 8 LOW STAGING | gate `deabf8f64bde27b7`, **never promoted** |
| sd3 `calibration_pairs_v2` (staging) | 14,552,870 HIGH + 329,280 LOW | gate `deabf8f64bde27b7`, manifests verified |
| staging path | `/private/tmp/scratch_ens_fit.db` | MC HIGH done 09:21, LOW done 11:06 |

The pre-sd3 VERIFIED rows aren't even applied — flag-off means plain p_raw end-to-end. Promoting sd3 (task R7) + flipping the flag (M5) is what would activate correction.

---

## 3 — Before/after comparison

### 3.1 Method note — two measurements, one valid

- **Comparison A (bin-distorted — DISCARDED):** binned the shadow probability mass and used bin-midpoint temps. Open-ended top bins (e.g. Mexico City "21 °C or higher", true 26-29 °C) capped the implied temp at 21.5 °C → fake −7.9 °C gaps. Pure measurement artifact.
- **Comparison B (bin-independent — AUTHORITATIVE):** raw ensemble member mean (the forecast's actual central estimate, °C) vs open-meteo realtime daily-max forecast (the "website" forecast). No bin distortion. 471 city×date snapshots, next 5 days.

### 3.2 Authoritative result (Comparison B)

| | raw ensemble (BEFORE = current shadow) | sd3-applied (AFTER = simulated promote) |
|---|---|---|
| HIGH mean gap (ens − website) | −0.79 °C | +0.28 °C (flips to over-warm) |
| HIGH **mean abs gap** | **1.37 °C** | **1.93 °C — worse** |
| HIGH helps / hurts | — | better 68 / **worse 77** |
| LOW mean abs gap | 0.93 °C | 1.54 °C — worse |
| LOW helps / hurts | — | better 2 / **worse 10** |

**Interpretation:** the uncorrected ensemble already tracks reality within ~1.4 °C (HIGH) / ~0.9 °C (LOW) — inside the operator's "forecast-settlement gap shouldn't exceed 2 °C" tolerance. sd3 over-subtracts a large negative bias and over-warms, increasing divergence.

### 3.3 Per-city spot checks (raw ensemble vs website, °C)

| city | date | raw ens | website | gap | sd3 bias_c | sd3 verdict |
|---|---|---|---|---|---|---|
| Jeddah | 05-28 | 36.8 | 36.0 | +0.8 | −6.84 | would push to ~43 °C — absurd |
| Shanghai | 05-30 | (ens close to web) | — | small | −3.15 | over-warm |
| Tokyo | 05-28 | 27.5 | 23.6 | +3.9 | −1.46 | already warm, FT worsens |
| Seoul | 05-28 | 25.0 | 21.8 | +3.2 | −1.66 | already warm, FT worsens |
| Amsterdam | 05-30 | 24.2 | 28.7 | −4.5 | −1.52 | FT helps here |
| Mexico City | 05-30 | ~26 (ens) | 29.4 | −3.4 | +0.03 | FT can't help |

Bidirectional, city-specific. No global cold bias in the raw ensemble — contradicts the premise that sd3's large negative corrections are warranted.

---

## 4 — sd3 bias magnitude (the red-flag distribution)

| band | count |
|---|---|
| \|bias_c\| ≤ 1 °C | 43 / 79 (54 %) |
| \|bias_c\| > 1 °C | **36 / 79 (46 %)** |
| \|bias_c\| > 2 °C | **14 / 79 (18 %)** |

Worst offenders (HIGH): Jeddah −6.84, Busan −3.92, Jakarta −3.80, San Francisco −3.38, Shanghai −3.15, Kuala Lumpur −3.13, Wellington −3.08, Manila −2.72, Houston −2.63, Lagos −2.58, Istanbul −2.37, Chicago −2.27, Panama City −2.23.

A real ensemble forecast off by −6.84 °C systematically for a hot desert city (Jeddah) is implausible — especially when its **live** ensemble matches the website to +0.8 °C. The historical fit, not the forecast, is wrong.

Note: the worst offenders carry `coverage_months={4,5}` or `{5}` (1-2 months) and **blank `n_paired`** → the bias is driven by a thin TIGGE prior, not robust live pairing.

---

## 5 — Hypotheses for the inflated bias (ranked)

**H1 — 12Z nighttime-window contamination (STRONGEST).** `src/calibration/ens_bias_repo.py:152` documents this exact failure in its own docstring: *"the TIGGE mx2t6 12Z snapshot covers 12Z→12Z (nighttime) and systematically misses the afternoon HIGH extremum, producing a −3 to −4 °C cold bias in the prior."* The loader prefers the 0Z cycle for HIGH but **falls back to 12Z when no 0Z snapshot exists for a date**. Cities whose history is 12Z-dominant get a fabricated cold bias. Evidence fit: concentrated in HIGH metric, hot/tropical cities, MAM season. Could stack to −3 to −7 °C when combined with thin priors.

**H2 — settlement station/unit mismatch.** If a city's settlement reads a hotter station than the forecast coordinates, every pair carries a constant offset. Would explain Jeddah/Busan specifically. Constraint #4 (data provenance).

**H3 — sparse-coverage prior over-fit (CONTRIBUTING).** Worst offenders have 1-2 months coverage and blank n_paired → bias = thin TIGGE prior mean, no live correction. SD2 conservative-σ widens the variance but does NOT correct the central estimate.

**H4 — data_version staleness.** Prior residuals drawn from an older ensemble data_version whose extraction window differs from the current live extractor.

**Most likely: H1 × H3** — thin priors computed from window-contaminated nighttime snapshots.

---

## 6 — Provenance gap: sd3 training data is GONE (decisive finding)

Attempted to confirm H1 by pulling Jeddah's per-cycle residuals (`ens_mean − settlement` grouped by 0Z/12Z). The training data does not exist on disk:

- `load_bucket_residuals` reads `ensemble_snapshots` + `settlement_outcomes`. The producer (`fit_full_transport_error_models.py`) runs against a scratch copy of an `ens_refit_full_*.db` and **refuses** the canonical DBs by basename.
- `ensemble_snapshots` / `settlement_outcomes` are **not base tables** in world / forecasts / staging — they are runtime views over the `_v2` tables.
- `ensemble_snapshots` (base) exists only in `zeus_trades.db` and **has ZERO Jeddah rows**.
- The named source `state/ens_refit_full_2026-05-25.db` **is EMPTY — zero tables/views.**
- The staging DB (`scratch_ens_fit.db`) holds the sd3 `model_bias_ens_v2` rows (incl. Jeddah −6.84, n_prior=29) but has **no `ensemble_snapshots` and no settlements** — only `ensemble_snapshots_v2` (471 near-future rows, no Jeddah history).

**Conclusion: the residual pairs that produced sd3's biases are not retained in any accessible DB.** The Jeddah −6.84 °C was computed from 29 pairs that no longer exist. The fit is therefore **completely unauditable** — the window-contamination hypothesis (H1) can be neither confirmed nor refuted, because the input is gone.

Per Constraint #4 (data provenance > code correctness): **an error model whose training data cannot be located or re-derived MUST NOT enter the live computation chain.** sd3's biases are not merely suspect — they are unverifiable. This is now the primary, sufficient reason to block promotion, independent of the accuracy regression in §3.

**Antibody required:** the producer must persist its residual-pair source (or a content hash + retained snapshot) into the `pair_batch` manifest, so every `bias_c` is traceable to the exact pairs that produced it. Today the manifest records `fit_signature_hashes` but the underlying residual rows are discarded — a fit you cannot re-derive is a fit you cannot trust.

---

## 7 — Recommendations / next dominoes

1. **HOLD** — do not promote sd3 (R7), do not unshadow (M5). Current standing.
2. **Locate the residual source** the producer ATTACHed for `ensemble_snapshots` / `settlement_outcomes` (trace `fit_full_transport_error_models.py` ATTACH/view setup). Without it the fit is unauditable.
3. **Confirm H1** once the source is found: Jeddah/Busan/Shanghai per-cycle residual breakdown. Expect 12Z (or NULL-cycle) rows to carry the −3 to −7 °C, 0Z rows much smaller.
4. **Category-killing fix** (after confirmation): in `load_bucket_residuals`, for HIGH **reject** 12Z-fallback instead of using it (fail-closed, no nighttime snapshot → no residual), and require `n_paired ≥ MIN_PAIRED_N` before trusting a prior-only bias. Makes the −6.84 class structurally impossible. Relationship test first (cross-module invariant: HIGH residual window ⊆ local-afternoon), then implementation.
5. **Profit path, separate decision (operator):** the raw-ensemble shadow is the better forecaster of the two (1.37 vs 1.93 °C). If live profit is the near-term goal, trading on the raw ensemble (R9, parked in another session) is a more defensible candidate than sd3 — but it carries its own gates (collateral/DATA_DEGRADED) and is your call, not autonomous.

---

## 8 — Evidence artifacts (this session)

- `$CLAUDE_JOB_DIR/ensemble_vs_website.csv` — 471-row bin-independent comparison (raw vs sd3-simulated vs website).
- `$CLAUDE_JOB_DIR/shadow_vs_website.csv` — 54-row bin-based comparison (superseded by the above; retained for audit).
- `docs/operations/ROW_ACTION_MANIFEST_2026-05-28.csv` — sd3 row reproducibility (72 REPRODUCIBLE / 13 INSUFFICIENT_PRIOR / 2 COVERAGE_MISLABELED / 0 NON_REPRODUCIBLE).
- `docs/operations/ENS_SD3_BEFORE_AFTER_2026-05-28.csv` — per-row pre/post-sd3 transition classes.

---

## 9 — T1/T2/T3 results: two data bugs found (2026-05-28, operator redesign)

Per operator directive (evidence-ledger + candidate selection), ran the 00z/12z test and
built the cycle-strict unit-normalized evidence extractor (`scripts/build_ens_residual_evidence.py`).

**CORRECTION to §6:** the residual data is NOT gone. It lives in `state/zeus-forecasts.db`
(33 GB): Jeddah has 7,803 HIGH ens rows + 45 settlements, 2024-2026. §6's "unauditable / data
gone" conclusion was wrong — I had searched world/trades/empty-scratch, not forecasts.db. The
sd3 fit IS auditable. (The provenance *antibody* in §6 still stands: the producer should persist
its evidence; but the data exists.)

### Bug 1 — 12z nighttime-window contamination (H1 CONFIRMED for a subset)

Per-cycle residual (ens_mean − settlement), HIGH:

| city | 00z | 12z | verdict |
|---|---|---|---|
| Seoul | −0.94 | −4.97 | 12z contaminated |
| Hong Kong | +0.69 | −3.36 | 0z clean |
| Jakarta | −3.10 | −7.83 | 12z strong |
| Busan | −2.67 | −5.72 | 12z strong |
| San Francisco | −3.76 | −3.76 | window-independent (station gap) |

The metadata that *should* flag this (`contributes_to_target_extrema`) is unreliable — Jeddah/SF
carry `contributes=1` on BOTH cycles. So the fix is **cycle-strict extraction** (HIGH→0z-only,
LOW→12z-only), not metadata trust. This is why the original producer leaked 12z: it only
*preferred* 0z and trusted a flag that doesn't exclude contaminated samples.

### Bug 2 — settlement unit normalization (NEW)

US-city settlements are stored in °F (members_unit='degF', settlement_value in °F). A code path
converted ensemble members to °C while leaving settlement in °F → fabricated −40 to −54 °C
"biases" for Austin/NYC/SF. Fix: normalize settlement with the same `members_unit`.

### Clean residuals after BOTH fixes (0z-only + unit-normalized)

| city/season | clean bias_c | prior sd3 |
|---|---|---|
| Jeddah MAM | −3.68 | −6.84 |
| San Francisco MAM | −3.76 | (−51 raw) |
| Jakarta SON | −3.10 | −3.80 |
| Busan MAM | −2.67 | −3.92 |
| Shanghai MAM | −2.60 | −3.15 |
| Seoul MAM | −1.01 | −1.66 |
| Hong Kong MAM | +0.69 | +0.63 |
| Austin MAM | +0.26 | (−54 bug) |
| NYC MAM | −0.23 | (−45 bug) |
| London (all) | −0.2 to −0.7 | — |

Most cities collapse to ±1 °C → raw/scale-only will win OOS. A residual cluster (Jeddah, SF,
Jakarta, Busan, Shanghai at −2.6 to −3.8) stays meaningful → T4 OOS proper-score test against
settlement must adjudicate whether these are stable station offsets (correct) or noise (use raw).

### Reframe of the open-meteo comparison (§3)

§3 measured ens vs open-meteo (another forecast). But the market settles on the WU station, and
for SF/Jeddah the WU station diverges from open-meteo by several °C. So "sd3 worse vs open-meteo"
does not prove "sd3 worse at predicting settlement." The only valid arbiter is held-out proper
score vs SETTLEMENT (T4) — exactly the operator's Principle 1.

### Status

- T1 (00z/12z test): DONE — H1 confirmed for subset, refuted for SF/Jeddah (station gap).
- T2 (evidence ledger) + T3 (cycle-strict + unit-norm extraction): DONE — `scripts/build_ens_residual_evidence.py`.
- T4 (candidate scorer, OOS proper-score selection vs settlement): NEXT.
- T5 (12-city smoke, pre-full-MC gate): after T4.
- Verdict unchanged: HOLD sd3. The clean rebuild proceeds via T4/T5.
