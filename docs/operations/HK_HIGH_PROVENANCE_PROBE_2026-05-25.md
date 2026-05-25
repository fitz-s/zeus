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
