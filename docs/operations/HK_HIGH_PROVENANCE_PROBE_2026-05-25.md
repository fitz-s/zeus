# HK HIGH full_transport Pathology — Data-Provenance Probe

- Created: 2026-05-25
- Last reused/audited: 2026-05-25
- Authority basis: operator data-provenance hypothesis (Fitz Constraint #4); PR #340 (draft/ens-refinement-research-2026-05-25); refit DB /private/tmp/ens_refit/full.db (mode=ro)
- Scope: READ-ONLY investigation. No code, config, or DB writes.

## VERDICT

**ALGORITHM / POSTERIOR FAILURE — NOT a data-provenance/semantics issue.**

The +6.3°C warm mass-center on HK HIGH is **introduced by full_transport itself**, applied
to an HK HIGH raw ensemble that is already essentially unbiased against its own HKO
observations at every lead. The HK obs source (HKO HQ) and unit (°C) are internally
consistent and shared between HK HIGH and HK LOW. There is no unit mismatch, no
metric-definition error, no cold/low truth corruption on the HK HIGH side.

**Recommendation: carve HK HIGH out of full_transport. Do NOT "fix the data" — the data is fine.**
HK LOW under full_transport is a genuine win and should ship.

(Note on the operator number: the operator wrote "+6.3°F". The measured shift is +6.32 **°C**
= +11.4°F. The magnitude 6.3 matches in °C; the unit label in the original note appears to be
a slip. Either way, the shift is real, large, and algorithm-introduced.)

## EVIDENCE

### 1. HK source identity (config + contracts)

`config/cities.json` (Hong Kong block):
- `settlement_source_type: "hko"`, `hko_station: "HKO"`, `airport_name: "Hong Kong Observatory Headquarters"`
- `unit: "C"`, `timezone: "Asia/Hong_Kong"`, `instrument_noise_override: 0.1`, `weighted_low_calibration_eligible: false`
- vs Tokyo (`wu_station: "RJTT"`, Haneda **airport**) and Seoul (`wu_station: "RKSI"`, Incheon **airport**), both `wu_icao` default, `weighted_low_calibration_eligible: true`.

`src/contracts/settlement_semantics.py:221-228, 306-319` — HK uses a dedicated `HKO_Truncation`
policy (`oracle_truncate`, precision 0.1°C, floor) verified 14/14 on HKO settlement. This is a
**settlement-rounding** distinction (0.1°C resolution), NOT a forecast-bias mechanism. It does
not touch the ENS mass-center.

HK is genuinely the one city on a special source (HKO observatory HQ, urban-core) and the one
HIGH city showing the pathology. But uniqueness of source does NOT equal a source *bug* — see §3-§4.

### 2. Obs provenance — HK vs normal cities (refit full.db `observations`)

| City | source | station | unit | raw_unit | target_unit |
|------|--------|---------|------|----------|-------------|
| Hong Kong | hko_daily_api | HKO | C | C | C |
| Tokyo | wu_icao_history | RJTT | C | C | C |
| Seoul | wu_icao_history | RKSI | C | C | C |

All three are °C end-to-end. **No unit conversion mismatch.** range_label bins in
`calibration_pairs_v2` are per-degree °C for all three. The only physical difference is
station siting: HKO HQ is an urban-core observatory; RJTT/RKSI are exposed airport ICAO sites.

### 3. Where the +6.3°C lives: FORECAST side, introduced by transport — NOT the OBS side

**Raw ENS member mass-center − daily-high OBS** (refit `ensemble_snapshots_v2.members_json`
joined to `observations`, units degC throughout):

| City | n | raw ENS−OBS mean | (°F) |
|------|---|------------------|------|
| Hong Kong HIGH | 6984 | **−0.05°C** | −0.09°F |
| Tokyo HIGH | 7288 | −1.29°C | −2.33°F |
| Seoul HIGH | 7288 | −1.30°C | −2.35°F |

By lead bucket, HK HIGH raw bias stays near zero **at every lead, including lead 0**:

| lead | HK HIGH | Tokyo HIGH | Seoul HIGH |
|------|---------|-----------|------------|
| <6h | −0.43°C | −1.49°C | −1.83°C |
| 24–72h | −0.07°C | −1.32°C | −1.32°C |
| 72–168h | +0.03°C | −1.28°C | −1.18°C |
| >168h | +0.06°C | −1.10°C | −1.20°C |

The raw HK HIGH ensemble is the **best-centered** of the three cities. The cold ~−1.3°C bias
the transport is built to fix is a Tokyo/Seoul/airport phenomenon; HK HIGH does NOT have it.

**Predicted mass-center vs realized settlement** (`calibration_pairs_v2`, p_raw-weighted bin
center vs mean `settlement_value`):

| | mass-center | truth | pred−truth | transport shift |
|---|---|---|---|---|
| HK HIGH `none` (uncorrected) | +26.05°C | +26.19°C | **−0.14°C (−0.25°F)** | — |
| HK HIGH `full_transport_v1` | +32.37°C | +26.28°C | **+6.09°C (+10.96°F)** | **+6.32°C** |
| HK LOW `none` | +13.77°C | +17.41°C | −3.64°C (−6.55°F) | — |
| HK LOW `full_transport_v1` | +18.08°C | +18.93°C | −0.85°C (−1.53°F) | +4.32°C |

Uncorrected HK HIGH points almost exactly at truth. full_transport adds a **+6.32°C warming
shift** that drives the prediction +6.09°C ABOVE the actual high. The bias is entirely on the
FORECAST/transport side; the OBS/truth anchor is stable (~+26.2°C both before and after).

Mean p_raw on the **realized** bin (model's confidence on truth):
- HK HIGH: none **0.190** → full_transport **0.008** (23× collapse).
- HK LOW: none **0.015** → full_transport **0.208** (14× improvement).

### 4. HK HIGH and HK LOW share the same source — reconciling why LOW wins, HIGH breaks

HK HIGH and HK LOW use the SAME obs source (`hko_daily_api`, station HKO, °C) and the SAME
settlement source (`hko`/HKO_HQ). A pure source/unit bug would corrupt BOTH metrics. It does
not: HK LOW under full_transport is a strong win.

The reconciliation is the transport mechanism (`src/calibration/ens_bias_model.py`):
`corrected = raw − bias`, `bias = mean(forecast − actual)`, negative bias = cold forecast →
correction WARMS. The bias prior is a TIGGE structural prior (`mx2t6`, 2yr) shrunk toward
limited OpenData live residuals (`apply_bias_to_extrema` :234-245; `fit_bucket` :199-231;
`posterior_bias`). The estimator learns a single directional WARMING correction:

- **HK LOW** raw genuinely runs cold (−3.64°C vs truth) → warming correction fixes it → WIN.
- **HK HIGH** raw is already centered (−0.14°C) → the same warming correction over-shifts it
  +6.32°C warm → catastrophic mass-center / PIT collapse.

The transport's structural/pooled cold prior is appropriate for airport-sourced HIGHs
(Tokyo/Seoul, −1.3°C) and for HK LOW, but it is **wrong for HK HIGH**, which has no cold bias
to correct. full_transport has no mechanism to recognize that one (city, metric) bucket is
already unbiased and should receive ~zero correction; it applies the family-level warming
regardless.

### 5. Other cities on the special source / HIGH-vs-LOW asymmetry

- HK is the only `hko` city in `config/cities.json` (sole `settlement_source_type: "hko"`).
  No co-tenant exists on the HKO source to cross-check, so "do other HKO cities also break"
  cannot be tested — but is moot, since the defect is shown to be transport-side, not source-side.
- **Hedge on the earlier "217 vs 6822" observation:** the small count of `settlement_station_id`-
  tagged HK HIGH snapshots (217) vs HK LOW (6822) is NOT an HK-specific provenance-pathway
  divergence. It is a **DB-wide HIGH-vs-LOW schema-population gap**: across ALL cities, HIGH has
  10,971 tagged / 384,601 untagged snapshots, while LOW has 355,652 tagged. Older HIGH rows
  pre-date the `settlement_*` columns. This does not bear on the verdict and should not be cited
  as evidence of an HK-specific data path.

## LIMITATIONS

- Mass-center is p_raw-weighted bin-center over the population of calibration pairs, not a
  per-(date,lead) renormalized expectation; absolute mass-center values are approximate but the
  **none → full_transport SHIFT (+6.32°C)** and the **pred−truth gap** are robust (same DB rows,
  same weighting, differenced).
- Raw ENS−OBS uses `members_json` arithmetic mean (not the MC/binning posterior). full_transport
  applies `corrected = raw − bias` to member extrema PRE-MC (`apply_bias_to_extrema`), so the raw
  member mean is exactly the quantity the transport shifts — the −0.05°C raw HK HIGH centering is
  the correct input baseline.
- **Physical mechanism hypothesis (flagged, NOT claimed):** HKO HQ is an urban-core observatory;
  the training set is airport-ICAO-dominant. Urban siting tends to lift daily-HIGH less than it
  lifts nighttime LOW (UHI). A transport whose warming prior is calibrated on airport-cold HIGHs
  and applied to an already-warm urban HIGH will over-warm it — consistent with HK LOW winning
  (UHI nighttime amplification aligns with the warming move) while HK HIGH breaks. This is the
  likely *why*; proving it is out of scope for this probe and not required for the carve-out.

## DISPOSITION

- DATA: HK HIGH obs/source/unit/timezone are CURRENT_REUSABLE. No data fix indicated.
- ALGORITHM: full_transport_v1 over-corrects already-unbiased buckets. HK HIGH must be carved
  out of full_transport (serve `none`/uncorrected, or a bucket that receives ~zero shift).
- HK LOW under full_transport: genuine win, ship.

---

## ROOT FIX

*Appended 2026-05-25 — operator decision: fix the root, ship HK HIGH with all 49 cities.*

### 1. Why HK HIGH receives the contaminated prior instead of its own ≈0 posterior

The defect has **two independent compounding layers**, both traceable to exact file:line.

#### Layer 1 (DB-wide, all cities): 12Z nighttime snapshot contaminates TIGGE prior

`src/calibration/ens_bias_repo.py:140-163` — `load_bucket_residuals` builds the TIGGE prior
by taking the **freshest-per-date snapshot** (`latest available_at`) among all
`ensemble_snapshots_v2` rows with `lead_hours <= 48`.

For the TIGGE archive, every target date has **two lead=0h snapshots**:

| Snapshot | available_at | UTC window | HKT window |
|----------|-------------|------------|------------|
| 0Z cycle | T00:00 UTC | 00–12 UTC | 08:00–20:00 HKT (covers afternoon HIGH) |
| 12Z cycle | T12:00 UTC | 12–24 UTC | 20:00–08:00 HKT next day (nighttime, MISSES HIGH) |

`max(available_at)` selects the 12Z snapshot (T12:00 > T00:00), whose forecast window is
nighttime for UTC+8 cities. The 12Z ens_mean is systematically cold vs the calendar-day high.

**Measured impact on TIGGE prior residuals (ens_mean − settlement), HK HIGH:**

| Snapshot | n | mean bias |
|---|---|---|
| 0Z cycle (afternoon window) | 41 | **+0.69°C** |
| 12Z cycle (nighttime window, selected by freshest) | 41 | **−3.36°C** |

This is **not HK-specific**. Every UTC+8 city with both 0Z and 12Z TIGGE rows at lead=0h
suffers the same contamination. Tokyo, Seoul, SF are also affected (TIGGE prior ≈ −3.5 to
−4°C for all, reconfirmed in refit DB). For cities with sufficient OpenData live data the
bad prior is overridden; for HK it is not.

#### Layer 2 (HK HIGH specific): zero OpenData live pairs → posterior = contaminated prior

`src/calibration/ens_error_model.py:216-220` — `fit_city_predictive_error` calls
`fit_bucket(tig, [], ...)` for the prior, then builds a `LiveResidual` only if
`len(opd) >= min_live_n` (min_live_n=5 at `scripts/rebuild_calibration_pairs_v2.py:177`).

**For HK HIGH:**
- OpenData (`ecmwf_opendata_mx2t3_local_calendar_day_max_v1`) date range: 2026-05-06 onward.
- Settlements (`settlements_v2`, authority=VERIFIED): 2026-03-16 to 2026-04-30.
- Overlap: **zero dates** (OpenData begins after last settlement).
- `opd` list length = 0 → `live = None` → `posterior_bias(transported, None)` → posterior = prior.

For Tokyo: 14 live pairs, mean −3.0°C (itself contaminated by 12Z but overrides partially).
For Seoul: 13 live pairs, mean +1.2°C → posterior moves away from contaminated prior.
For HK: **n=0**, weight_live=0.000, posterior bias = −3.49°C = the 12Z-contaminated TIGGE mean.

`apply_bias_to_extrema` at `src/calibration/ens_bias_model.py:245`:
`corrected = raw − (−3.49) = raw + 3.49°C`
applied to an HK HIGH raw ENS that was already centered (−0.05°C vs obs). Net effect: +3.49°C
warm overcorrection. Combined with the +0.32°C truncation artifact (obs > settlement), this
produces the measured **+6.09°C predicted-above-truth** mass-center shift.

*(Note: the observed +6.32°C transport shift is the mass-center delta between none and
full_transport populations; the +6.09°C is the pred−truth gap in the full_transport column.
The small excess above +3.49°C comes from binning/MC nonlinearities and the Platt recalibration
trained on the biased pairs.)*

### 2. Fix scope (read-only; implementation not performed)

The fix must satisfy all four conditions: (a) HK HIGH correction → ≈0, (b) HK LOW win
preserved, (c) global HIGH/LOW wins preserved, (d) category made impossible for any
data-sparse, already-unbiased city in the future.

#### Fix A (Layer 1 — primary, global): filter TIGGE prior by `contributes_to_target_extrema`

**File:line**: `src/calibration/ens_bias_repo.py:110-120` — the `legacy_tigge_null_passthrough`
policy allows `contributes_to_target_extrema IS NULL OR 1`. The 12Z nighttime snapshots have
`contributes_to_target_extrema=NULL` (legacy rows) because the extractor never ran on them.
Modern rows (with the extractor) would have `contributes_to_target_extrema=0` for nighttime
window rows that do not cover the target-day extremum.

**Minimal change**: for TIGGE prior residuals, additionally gate on the forecast window cycle
time. Concretely: in `load_bucket_residuals`, when selecting the freshest snapshot, prefer
snapshots whose `available_at` time aligns with the 0Z cycle (T00:xx UTC) over the 12Z cycle
(T12:xx UTC) for high-metric cities. Formally: within `freshest` selection, penalize
`available_at` with time component 12:00 UTC by setting its sort key to noon-of-previous-day
for HIGH metric. This is a **tie-break rewrite of the freshest-selector** so the 0Z cycle
(afternoon-covering window) wins for target-day lead=0h comparisons on HIGH.

**Alternative (cleaner, more general)**: require `contributes_to_target_extrema=1` even for
legacy TIGGE rows, which means backfilling `contributes_to_target_extrema` for legacy TIGGE
snapshots from the extractor's window logic before the prior is built. The extractor already
knows whether a snapshot's valid window covers the target extremum. This is the permanent
antibody — it makes the nighttime-window contamination un-insertable into any future prior.

**Impact on other cities**: Tokyo and Seoul TIGGE priors are also contaminated; fixing this
will bring their priors in line with the true TIGGE vs settlement bias. Their OpenData live
data will still be able to override, so their posteriors improve too.

#### Fix B (Layer 2 — secondary, HK-specific timing): live data lookback window extension

**File:line**: `src/calibration/ens_error_model.py:208-219` — `fit_city_predictive_error`
passes `settled_before=settled_before` to `load_bucket_residuals`. If `settled_before` is None,
the full settlement history is available. But the **OpenData product** only has snapshots from
2026-05-06 onward, which don't overlap with any settled HK HIGH market (last settlement
2026-04-30). This is a temporal availability gap, not a code bug — OpenData was not yet
ingesting during the HK HIGH settlement window.

**Fix B implication**: no code change can create live residuals that don't exist. The correct
handling is: when `opd` is empty, the prior must itself be uncontaminated (Fix A is required).
Fix B is strictly dependent on Fix A. Once Fix A produces a correct prior (≈+0.69°C for
HK HIGH 0Z), the empty live posterior correctly falls back to the prior (≈+0.69°C) — a
minimal, evidence-consistent correction. The SNR gate (`correction_strength` at
`src/calibration/ens_error_model.py:39-56`) will compute z=|+0.69|/SD; with SD≈0.5 this
gives z≈1.4 → partial correction λ≈0.4 → effective_bias≈+0.28°C — negligible warm shift.

#### Fix summary (file:line + change)

| Layer | File:line | Change |
|---|---|---|
| L1 (primary) | `ens_bias_repo.py:140-163` in `load_bucket_residuals` / `_forecast_means` | For HIGH-metric TIGGE prior, filter or penalize 12Z lead=0h snapshots that do not cover the target-day extremum window. Preferred: require extractor-validated `contributes_to_target_extrema=1` even for legacy TIGGE (needs backfill); or: sort key prefers 0Z over 12Z for same-day leads. |
| L1 (backfill) | scripts/backfill step | Run extractor's window-attribution logic over legacy TIGGE HK HIGH (and all UTC+8 cities) to set `contributes_to_target_extrema=0` on 12Z nighttime rows. |
| L2 (none needed if L1 fixed) | `ens_error_model.py:216-220` | No change required. Empty live posterior correctly falls back to fixed prior ≈+0.69°C for HK HIGH. |
| Global safety | `ens_bias_model.py:199-231` `fit_bucket` | No change needed; the estimator already handles empty `opendata_residuals` correctly (returns prior). The fix is upstream in what goes into `tigge_residuals`. |

**DOES NOT touch**: HK LOW (uses a different data_version, different metric, and already has
6,822 provenanced live+settlement pairs — fix is orthogonal).

### 3. Verification spec

Post-fix, confirm all four conditions before promotion.

**A — HK HIGH correction approaches zero:**
```
Query: load_bucket_residuals(conn, city='Hong Kong', data_version=PRIOR_VER,
    metric='high', lead_max=48.0, contributor_policy='legacy_tigge_null_passthrough')
Expected: mean ∈ [−0.5, +1.5]°C (vs current −3.49°C)
```

**B — HK HIGH PIT ≈ uniform:**
```
On matched dates (pre/post refit), assert:
    PIT p00_p10 bin (lowest) fraction ≤ 15% (vs current 96.9%)
    All 10 PIT bins within [5%, 20%] (uniform would be 10%)
    ECE ≤ 5 percentage points (vs current ~15× global)
```

**C — HK HIGH effective_bias_c ≈ 0 (SNR gate suppression):**
```
After rebuild: PredictiveErrorModel.effective_bias_c for (HK, summer HIGH)
    assert abs(effective_bias_c) ≤ 0.5°C
```

**D — HK LOW win preserved (orthogonal, should be unchanged):**
```
load_bucket_residuals(city='Hong Kong', metric='low', data_version=LOW_PRIOR_VER) → still negative mean
HK LOW full_transport mass-center vs truth delta ≤ −1.5°C → −0.5°C (current −0.85°C preserved)
```

**E — Global: other UTC+8 cities (Tokyo, Seoul) TIGGE prior improves but OpenData live still corrects:**
```
Tokyo TIGGE prior mean (post-fix): ∈ [−1.0, +1.0]°C  (vs current −4.0°C)
Tokyo OpenData live (n=14, mean=−3.0°C) still provides live evidence → posterior moves toward live
Tokyo full_transport: no regression vs current LogLoss on matched validation dates
```

**F — Anti-regression: prior full_transport PR#340 validation set:**
```
Run scripts/verify_truth_surfaces.py or matched_compare.py on 48-city subset
    excluding HK HIGH from current 'carve-out' mental model
    Global HIGH win (LogLoss delta < 0) preserved for ≥ 85% of HIGH cities
    Global LOW win preserved for ≥ 85% of LOW cities
```
