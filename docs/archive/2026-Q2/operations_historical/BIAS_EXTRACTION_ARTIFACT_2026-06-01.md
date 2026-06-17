# BIAS EXTRACTION ARTIFACT — root-cause audit (read-only)

- Created: 2026-06-01
- Authority basis: operator URGENT read-only RCA request 2026-06-01; HEAD 6fcd05a69f
- Scope: is the live "cold bias" an ENSEMBLE DAILY-MAX EXTRACTION defect, or a genuine forecast/model difference?
- Method: independently re-scanned raw ECMWF Open Data ENS `mx2t3` GRIB (cycle 2026-06-01 00z, steps 3–144h)
  for Tokyo 06-02 and Singapore 06-03, recomputed per-member local-calendar-day max over the correct local-day
  window, compared to the STORED `members_json` daily-max and to open-meteo's ECMWF deterministic.

## VERDICT (headline)

**NOT an extraction artifact.** The daily-max extraction is provably correct — independently recomputed
per-member daily-max is **bit-identical** to the stored `members_json` for BOTH cities. Timezone window,
3h `mx2t3` aggregation, and the "fully-inside-local-day" inner-window selection are all correct and capture
the local afternoon peak. The Tokyo gap vs open-meteo is a **model/resolution difference**, not our bug:
open-meteo serves **IFS HRES (9 km, O1280, single deterministic)**; Zeus consumes **IFS 0.25° ENS (25 km,
51-member ensemble)**. Different model, different resolution, central-tendency vs single realization.

## Per-city evidence

| City | target | stored median | recomputed median (independent) | open-meteo ECMWF (HRES) | extraction_error | window used |
|------|--------|---------------|---------------------------------|-------------------------|------------------|-------------|
| Tokyo | 2026-06-02 | 21.87 °C | **21.87 °C** (n=51, mean 21.83, min 20.69, max 23.16) | 26.3 °C (reported) | **0.00 °C** | local `[06-01T15Z .. 06-02T15Z]` = full 24h, UTC+9 ✓ |
| Singapore | 2026-06-03 | 30.66 °C | **30.66 °C** (n=51, mean 30.63, min 29.43, max 32.00) | 31.4 °C (reported) | **0.00 °C** | local `[06-02T16Z .. 06-03T16Z]` = full 24h, UTC+8 ✓ |

Extraction error (stored − correct-recompute) = **0.00 °C for both cities.** The "cold" value is what the ENS
genuinely forecasts, faithfully extracted.

### Tokyo 06-02 diurnal proof (member 0, value = max temp in prior 3h, window-end in local time)

```
step  6 end_local 06-01 15:00 val 24.1C  out  (prior day's afternoon peak)
step 18 end_local 06-02 03:00 val 20.5C  INNER
step 21 end_local 06-02 06:00 val 20.5C  INNER
step 24 end_local 06-02 09:00 val 21.2C  INNER
step 27 end_local 06-02 12:00 val 21.5C  INNER   <- genuine 06-02 afternoon PEAK
step 30 end_local 06-02 15:00 val 21.5C  INNER
step 33 end_local 06-02 18:00 val 20.3C  INNER
step 36 end_local 06-02 21:00 val 18.8C  INNER
step 39 end_local 06-03 00:00 val 18.4C  INNER
```

The afternoon-peak windows (12:00, 15:00 local) ARE captured as INNER, ARE included in the max, and the ENS
peak genuinely tops out at ~21.5 °C for member 0 (23.16 °C warmest member). `INNER-only` and `ALL-overlap`
daily-max are identical (21.87) — boundary windows would not change the result. No peak step is dropped, no
window is clipped, no UTC/local misalignment exists. Selected step ranges stored in the JSON
(`15-18 … 36-39`, eight 3h windows) exactly tile the local day.

## Why Tokyo gap is large and Singapore gap is small (consistent with model/resolution, NOT a window bug)

- **Grid snap.** Tokyo manifest (35.6018, 139.7752) snaps to ENS 0.25° grid point **(35.500, 139.750)** —
  ~11 km SW of central Tokyo, a 25 km cell that averages urban land with **Tokyo Bay**. A bay-contaminated
  25 km cell runs cooler at the daily max. HRES (9 km) resolves the inland urban cell separately → warmer.
- Singapore manifest (1.3521, 103.8198) snaps to **(1.250, 103.750)** — flat equatorial, SST-dominated,
  spatially uniform, so the 25 km ENS and 9 km HRES nearly agree (Δ ≈ 0.7 °C).
- This city-specific spread (large for coastal/urban Tokyo, small for uniform Singapore) is the signature of a
  **resolution/model-physics difference**, exactly as the hypothesis noted — but the cause is the upstream
  ENS-vs-HRES product, not Zeus's extraction. Even the warmest ENS member (23.16 °C) is 3 °C below HRES 26.3 °C,
  so no extraction choice over THIS ENS data could reach 26.3 °C.

## Extraction code path (file:line)

- Live orchestration: `src/data/ecmwf_open_data.py:97` (`EXTRACT_SCRIPT`), invokes the external extractor via
  subprocess at `src/data/ecmwf_open_data.py:1501`.
- **Canonical daily-max extractor:** `51 source data/scripts/extract_open_ens_localday.py`
  - aggregation window product-derived (3h for mx2t3): `:108` `_aggregation_window_hours_for_param`, with a
    fail-closed cross-check against each GRIB message's own `lengthOfTimeRange` at `:322-338` (makes a wrong
    6h-on-3h window unconstructable — the prior London-DST-class defect is already fixed here, 2026-05-29).
  - window overlap / inner-selection: `:380` `_windows_overlap`, used at `:461-478`.
  - **per-member daily-max = `max(inner vals)`: `:493`** (HIGH track).
  - grid-point snap (regular_ll fast path, bit-identical to `codes_grib_find_nearest`): `:239-287`.
- Local-day bounds (tz-correct, ZoneInfo local-midnight → UTC):
  `51 source data/scripts/tigge_local_calendar_day_common.py:108` `local_day_bounds_utc`.
- Stored snapshot read path: `src/data/ecmwf_open_data_ingest.py:400-424`; authority classifier
  `src/data/forecast_extrema_authority.py:117`.

## Secondary (cosmetic, non-causal) finding

Singapore's stored `forecast_window_start_utc` = `06-02T18:00Z` while `local_day_start_utc` = `06-02T16:00Z`
(a 2h gap; the provenance field recorded the first covered step-window boundary rather than the local-day
start). This did **not** affect the daily-max (the afternoon peak at step 57 / 17:00 local is fully inside
both windows), but the `forecast_window_start_utc` provenance field is slightly under-stated for cities whose
first inner 3h window starts after local midnight. Flagged for provenance hygiene only; it is **not** the
cold-bias cause and changes no extracted value.

## Implication for the bias-correction decision

- The cold-bias is **NOT** an extraction artifact for these cities — fitting a bias correction onto
  `forecast − observed` will NOT be "correcting our own extraction bug." It would be correcting a genuine
  **ENS(25km) → station** representativeness/model gap (resolution + bay/urban siting + ensemble-mean vs the
  station's realized max).
- This is consistent with MEMORY anchor `project_a4_coldbias_structural_2026_05_31` (modest city-specific bias,
  Tokyo cold, corrections currently OFF): the right fix is a **city-specific (or grid-representativeness)
  bias/regime correction**, not a change to the extraction window.
- Do NOT change `extract_open_ens_localday.py` window/aggregation logic to chase the gap — that would corrupt a
  correct extractor.
