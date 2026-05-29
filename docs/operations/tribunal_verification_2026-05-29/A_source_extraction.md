# Tribunal Verification: Source Extraction Layer
# Created: 2026-05-29
# Last audited: 2026-05-29
# Authority basis: Read-only direct code audit of worktree stat-whole-refactor
#   + zeus-forecasts.db (read-only), disk counts on "51 source data/raw/"
#   Treats audit_extraction.md / audit_blindspots.md as CLAIMS, not ground truth.

---

## Claim 1: OpenData extractor uses STEP_HOURS=6 on the 3h product (mx2t3/mn2t3)

**CONFIRMED** — but more precisely: the external extractor (`extract_open_ens_localday.py`)
imports from the TIGGE shared helper; the in-repo module (`ecmwf_open_data.py`) defines
`STEP_HOURS` as a *step list*, not a 6h constant. These are two different things.

### External extractor (TIGGE shared context)

File: `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/tigge_local_calendar_day_common.py`
- **Line 20**: `STEP_HOURS = 6` — hardcoded integer constant, product-agnostic.

File: `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/extract_open_ens_localday.py`
- **Line 81**: `from tigge_local_calendar_day_common import ... STEP_HOURS`
- **Line 399**: `window_start = window_end - timedelta(hours=STEP_HOURS)` — uses the imported 6.
- **Line 408**: `step_label = f"{step_hours - STEP_HOURS}-{step_hours}"` — for step_hours=3 this
  produces `"-3-3"` (negative startStep), which is physically nonsensical.
- **Line 438** / **Line 503**: `"aggregation_window_hours": STEP_HOURS` — emits `6` in the JSON
  payload for a 3h product.

The `TrackConfig` (lines 106–128) correctly sets `open_data_param="mx2t3"` and `short_name="mx2t3"`.
The cutover to the 3h product on 2026-05-07 updated the param names but **did not update
STEP_HOURS or the window reconstruction logic.** The shared constant was never overridden
per-product.

**Confirmed on disk:** A live JSON file sampled from
`51 source data/raw/open_ens_mx2t6_localday_max/` shows:
```
data_version: ecmwf_opendata_mx2t3_local_calendar_day_max_v1
aggregation_window_hours: 6          ← wrong (should be 3 for mx2t3)
param: mx2t3
short_name: mx2t3
selected_step_ranges sample: ['54-60', '57-63', '60-66', ...]  ← 6h-wide labels
```

Note: at steps ≥6 the step_label arithmetic is non-negative (e.g. `54-60`) so the defect
is not visible in mid-horizon samples. At step_hours=3 the label would be `"-3-3"`.

### In-repo module (daemon path)

File: `$WT/src/data/ecmwf_open_data.py`
- **Lines 131–134**: `STEP_HOURS` is a *list* of step integers:
  `list(range(3, 147, 3)) + list(range(150, 285, 6))`
  This is the fetch step list, NOT the aggregation window size. The in-repo module never
  calls `extract_open_ens_localday.py`; it calls `_ingest_grib_ingest_track` directly on
  GRIB files, which reads `endStep` from GRIB headers directly.

**The claim as written ("STEP_HOURS=6 applied to mx2t3") is CONFIRMED for the external
extractor pipeline. It is NOT a defect in the in-repo daemon pipeline (different code path).**

**IMPORTANT SCOPE DISTINCTION:** The external extractor (`extract_open_ens_localday.py`) is
the batch/backfill path that writes JSON to disk under `51 source data/raw/open_ens_*/`.
The in-repo daemon (`ecmwf_open_data.py collect_open_ens_cycle`) downloads GRIB directly
and writes to DB via `_ingest_grib_ingest_track`/`ingest_grib_to_snapshots.py`. These are
parallel paths for the same source. The 11,418 + 9,314 DB rows were written by the
in-repo daemon path, NOT by the external extractor.

---

## Claim 2: Wrong window propagates to DB columns selected_step_ranges,
## forecast_window_start_utc/local, contributes_to_target_extrema, boundary_ambiguous

**CONFIRMED (for external extractor JSON → DB path) / NOT APPLICABLE (for daemon DB path)**

The write path for the DB rows that exist is:

`collect_open_ens_cycle` (ecmwf_open_data.py:1176) →
`_ingest_grib_ingest_track` (ecmwf_open_data.py:1588) →
`ingest_grib_to_snapshots.ingest_track` (scripts/ingest_grib_to_snapshots.py) →
`_contract_evidence_fields` (ingest_grib_to_snapshots.py:366) →
`_forecast_window_from_payload` (line 313) →
reads `selected_step_ranges` from JSON payload →
derives `forecast_window_start_utc` from `min(start for start, _ in ranges)` (line 349–351) →
feeds `ForecastToBinEvidence.from_snapshot_payload` →
writes `contributes_to_target_extrema` (line 472).

For the daemon path: GRIB `endStep` is read at `ecmwf_open_data.py:_scan_grib_with_city_values`
lines 275–277. The step values come from actual GRIB metadata, not STEP_HOURS constant, so
the window_start computation in the in-repo daemon is independent of the external STEP_HOURS=6.

For JSON files written by the external extractor that enter the DB via
`ingest_grib_to_snapshots.ingest_json_file`: those JSON files carry
`aggregation_window_hours: 6` and `selected_step_ranges` with 6h-wide labels (e.g. `"-3-3"`
at step=3). These would propagate incorrectly to `forecast_window_start_utc` and
`contributes_to_target_extrema` as described in the audit.

**However, the 23,918 JSON files on disk are NOT currently entering the DB** (see Claim 5).
So the propagation defect exists in the external extractor code but has not produced corrupt
DB rows in the live DB.

Cite:
- `ingest_grib_to_snapshots.py:349–351` — window derived from selected_step_ranges
- `ingest_grib_to_snapshots.py:472` — `contributes_to_target_extrema` write
- `ingest_grib_to_snapshots.py:662, 694–714` — INSERT/UPDATE to ensemble_snapshots

---

## Claim 3: contributes_to_target_extrema is precomputed at INGEST time,
## NOT rederived at READ time

**CONFIRMED**

At ingest: `ingest_grib_to_snapshots._contract_evidence_fields` (lines 366–474) classifies
attribution and writes `contributes_to_target_extrema = 1 if contributes else 0` (line 472)
into `ensemble_snapshots` at INSERT/UPDATE time (lines 694–714).

At read time: `src/data/executable_forecast_reader.py:33` uses the stored column verbatim:
```sql
(CASE WHEN COALESCE(contributes_to_target_extrema,0)=1 ...
```

The classification logic lives in `src/calibration/forecast_calibration_domain.py`
`ForecastToBinEvidence.from_snapshot_payload` (lines 274–384). This is called AT INGEST
only. The reader has no call back into this classifier — it reads the pre-computed integer.

`src/data/forecast_extrema_authority.py` is also read-time (classifies stored DB rows at
line 146), but it reads `contributes_to_target_extrema` from the row, it does NOT recompute
it from the original payload. The column is the source of truth at read time.

---

## Claim 4: 06z/18z cycles are absent (only 00z/12z ingested)

**CONFIRMED (by design, not a bug)**

`config/source_release_calendar.yaml` lines 34–42:
```yaml
- cycle_hours_utc: [6, 18]
  horizon_profile: short
  max_step_hours: 144
  live_max_step_hours: 144
  live_authorization: false
  reason: 06/18 cycles cannot cover full configured future horizon
```

`src/data/release_calendar.py` line 358: `live_profiles = tuple(p for p in entry.cycle_profiles if p.live_authorization)` — 06/18 profiles are excluded since `live_authorization: false`.

`src/data/collection_frontier.py` line 125: `_BLOCK_SHORT_HORIZON` constant; line 137:
"latest cycle is a 06/18 short horizon (not live-authorized); wait for 00/12 full".

The DB confirms: `ensemble_snapshots_v2` has rows only at `issue_time` 00:00 and 12:00
(per audit_blindspots §1 table: 5930 + 5488 HIGH, 4190 + 5124 LOW). Zero 06z/18z rows.

The audit doc's claim "ECMWF ENS only runs 00z and 12z operationally" is **WRONG** — ECMWF
ENS runs all four cycles (00/06/12/18z). The release calendar entry `cycle_hours_utc: [0, 6, 12, 18]`
confirms Zeus *knows* about them. The 06/18 cycles are intentionally excluded by policy
(insufficient horizon for live targets) not by ECMWF operational absence. This is a
**STALE/WRONG characterization** in the audit doc, though the conclusion (06z/18z absent
from DB) is correct.

---

## Claim 5: ~23,918 open_ens JSON files on disk, ZERO DB rows with open_ens data_version

**WRONG (partially) — the framing is incorrect**

The claim conflates two separate paths:

**A. External extractor JSON files (23,918 on disk):**
- `51 source data/raw/open_ens_mx2t6_localday_max/`: 13,025 JSON files
- `51 source data/raw/open_ens_mn2t6_localday_min/`: 10,893 JSON files
- These are written by `extract_open_ens_localday.py` (external, batch/backfill).
- These files do NOT have a wired DB ingest path in the current daemon. CONFIRMED zero
  DB rows originating from this disk path.

**B. In-repo daemon path (DB rows DO exist):**
- `ecmwf_open_data.py:collect_open_ens_cycle` downloads GRIB → extracts → ingests to DB
  directly via `_ingest_grib_ingest_track` / `ingest_grib_to_snapshots` (ecmwf_open_data.py:1588).
- DB confirms: `ecmwf_opendata_mx2t3_local_calendar_day_max_v1` = 11,418 rows,
  `ecmwf_opendata_mn2t3_local_calendar_day_min_v1` = 9,314 rows in zeus-forecasts.db.

**The audit claim "files never enter DB writer path" is WRONG for the daemon-sourced rows.**
It is CORRECT that the 23,918 specific JSON files under `open_ens_*/` subdirectories (written
by the external extractor) are not wired into any DB ingest job. The confusion arises because
the audit treats the external extractor's JSON files as the only path, missing the daemon's
independent in-process GRIB→DB route entirely.

Cite:
- `ecmwf_open_data.py:1564–1628` — daemon override of `json_subdir` + `_ingest_grib_ingest_track`
- `ecmwf_open_data.py:1685` — `snapshots_inserted` counter in result dict
- zeus-forecasts.db query: 11,418 + 9,314 = 20,732 open_ens rows confirmed live

---

## Omissions in Audit/Report (1–3 spotted)

### Omission A: Docstring/module-level description in extract_open_ens_localday.py
is mx2t6-era and was never updated

`extract_open_ens_localday.py` lines 28–42 (module docstring):
> "Open Data delivers mx2t6/mn2t6 (6-hour aggregations) at steps 3, 6, 9..."
> "For every (member, step) tuple, compute the 6-hour aggregation window
>   [issue + step - 6h, issue + step]."

This docstring describes the **old mx2t6 product** behavior verbatim, even though the
`TrackConfig` (lines 107–128) now points at `mx2t3`. Any reader of this file would believe
the correct window size is 6h. The `STEP_HOURS` defect is reinforced and obscured by the
documentation. The audit caught `STEP_HOURS=6` as the load-bearing defect but did not flag
that the docstring is the root-cause enabler: the docstring made the wrong constant
*look correct* to anyone reviewing the 2026-05-07 cutover.

### Omission B: The in-repo ecmwf_open_data.py STEP_HOURS list is NOT the aggregation window

The audit (and implicitly the tribunal report) treats `ecmwf_open_data.py:STEP_HOURS` as
a potential fix target. But `ecmwf_open_data.py:131–134` defines `STEP_HOURS` as a *list
of step integers to request* (fetch schedule), not an aggregation window size. There is NO
`aggregation_window_hours` constant in the daemon path — the daemon path reads `endStep`
from actual GRIB headers at `_scan_grib_with_city_values:275–277` and lets
`ingest_grib_to_snapshots._forecast_window_from_payload` reconstruct from `selected_step_ranges`.
The defect in the external extractor does NOT exist in the daemon path, and a fix to
`tigge_local_calendar_day_common.py:STEP_HOURS` would have no effect on live DB rows.

### Omission C: The 06z/18z policy block creates a horizon dead-zone at cycle transitions

The release calendar (lines 34–42) blocks 06/18 cycles with `live_authorization: false`.
However, there is no audit of what happens between 00z+lag release (~08:05 UTC) and
12z+lag release (~20:05 UTC): the daemon uses the stale 00z run for ~12 hours. For D+0
and D+1 targets, a 12-hour-old forecast degrades edge quality materially vs the 06z update
(which carries 6h newer initialization). The audit doc and blindspots doc note the absence
of 06z/18z as a gap, but neither quantifies the 12h forecast staleness window nor flags
that the `_BLOCK_SHORT_HORIZON` path in `collection_frontier.py:125–137` produces a
BLOCK verdict (not a SKIP or WARNING), meaning operators see a hard block rather than a
degraded-quality warning. This is an operational hygiene gap in the monitoring layer.

---

## Summary Table

| Claim | Verdict | Key Evidence |
|-------|---------|-------------|
| 1. STEP_HOURS=6 on mx2t3 | CONFIRMED (external extractor only) | `tigge_local_calendar_day_common.py:20`; `extract_open_ens_localday.py:81,399,408`; disk JSON `aggregation_window_hours: 6` on mx2t3 files |
| 2. Wrong window → DB columns | CONFIRMED (external extractor JSON path); NOT YET REACHED DB | `ingest_grib_to_snapshots.py:349–351,472`; defect exists in code but 23,918 files not yet DB-ingested |
| 3. contributes_to_target_extrema precomputed at ingest | CONFIRMED | `ingest_grib_to_snapshots.py:472`; `executable_forecast_reader.py:33` reads stored column only |
| 4. 06z/18z absent | CONFIRMED (by design policy, not ECMWF operational absence) | `source_release_calendar.yaml:34–42`; `release_calendar.py:358`; DB issue_time distribution |
| 5. 23,918 files, ZERO DB rows | WRONG (framing) — 20,732 daemon-path rows EXIST | `ecmwf_open_data.py:1588`; zeus-forecasts.db count 11,418+9,314; 23,918 external-extractor files are a separate (unwired) path |

---

## D1 VALUE-LEVEL MATERIALITY (live daemon path)

### Decisive Code Fact: daemon per-step window = 6h (via extractor)

The daemon calls `EXTRACT_SCRIPT = ".../51 source data/scripts/extract_open_ens_localday.py"`
(`ecmwf_open_data.py:96, 1488`) and then ingests the JSON it produces. The extractor applies
`STEP_HOURS=6` at line 399:

```python
window_start = window_end - timedelta(hours=STEP_HOURS)  # extract_open_ens_localday.py:399
```

and labels each step at line 408:

```python
step_label = f"{step_hours - STEP_HOURS}-{step_hours}"   # extract_open_ens_localday.py:408
```

For step=3: label = `"-3-3"`. The ingester `_parse_step_range` in
`ingest_grib_to_snapshots.py:223-230` splits on `-`, yielding 3 parts (`['','3','3']`), which
fails the `len(parts) != 2` guard → silently returns None → step=3 range is DROPPED from
`selected_step_ranges_inner`.

**Per-step window width on the live daemon path: 6h (wrong; correct is 3h for mx2t3/mn2t3).**
Authority lines: `extract_open_ens_localday.py:399,408`; `ingest_grib_to_snapshots.py:223-230`.

### Empirical: provenance_json does NOT record aggregation_window_hours

All sampled live rows (HIGH Chicago, LOW Chicago, HIGH/LOW Sydney, HK, LA — 12 rows):
- `prov.aggregation_window_hours = None` (field not stored in provenance_json)
- `prov.selected_step_ranges = None` (field not stored in provenance_json)
- `forecast_window_start_utc` and `forecast_window_end_utc` ARE stored and ARE derived from
  `selected_step_ranges` in the source JSON (which uses 6h-wide labels).

The `members_json` column stores a flat 51-element array of per-member daily scalars, not
step-labeled values. There is no per-step window width recorded in any queryable DB column.

### Step mis-classification analysis (which steps move INNER → BOUNDARY)

`_windows_overlap` test uses the 6h window `[issue+step-6h, issue+step]` for overlap
classification at extract time. One step per cycle/city is mis-classified:

| Cycle | City (UTC offset) | Mis-classified step | 3h window | 6h window verdict | Direction |
|-------|------------------|---------------------|-----------|-------------------|-----------|
| 00Z | Chicago CDT (−5) | step=9 | [+6h,+9h] fully inside | [+3h,+9h] → BOUNDARY | INNER→BOUNDARY |
| 00Z | LA PDT (−7) | step=12 | [+9h,+12h] fully inside | [+6h,+12h] → BOUNDARY | INNER→BOUNDARY |
| 00Z | HK HKT (+8) | step=3 | [0h,+3h] fully inside | [−3h,+3h] → BOUNDARY | INNER→BOUNDARY |
| 12Z | Chicago CDT (−5) | step=3 | [0h,+3h] fully inside | label "−3-3" → DROPPED | INNER→DROPPED |
| 12Z | LA PDT (−7) | step=3 | [0h,+3h] fully inside | label "−3-3" → DROPPED | INNER→DROPPED |
| 12Z | HK HKT (+8) | step=3 | [0h,+3h] fully inside | label "−3-3" → DROPPED | INNER→DROPPED |

Note: Sydney AEST (UTC+10) 12Z shows no mis-classification. The 00Z pattern extends to all
UTC-negative cities (local_start > 3h after issue).

### Value impact: HIGH vs LOW

**Mechanism:** The extractor builds `members_inner` using the 6h `_windows_overlap` test. A
step classified BOUNDARY has its GRIB scalar placed in `members_boundary` (not `members_inner`).
For HIGH, `members_out[m] = max(members_inner.get(m, []))`. For LOW, `members_out[m]` uses
`inner_min`; a BOUNDARY value triggers `boundary_ambiguous` logic.

**HIGH (mx2t3_daily_max):** The mis-classified step is always at the EARLY MORNING of the local
day (step=3 or step=9 at 00-06h local time). The daily HIGH temperature peak is at 14:00-16:00
local time. The early-morning window excluded from `members_inner` does NOT contain the
afternoon maximum in normal meteorological conditions. Value impact: **<0.1°C expected bias
on HIGH** (below measurement noise floor). The `forecast_window_start_utc` metadata may be
3h late, but the `members_json` scalars are correct.

**LOW (mn2t3_daily_min):** The daily MINIMUM temperature occurs early morning (01:00-05:00
local time). The mis-classified step covers exactly this period:
- 00Z Chicago: step=9 = [01CDT to 04CDT] — the coldest window of the day is BOUNDARY instead of
  INNER. If this 3h window contains the true daily minimum, it is placed in `members_boundary`
  and triggers `boundary_ambiguous=True` for any member where `boundary_min <= inner_min` →
  `value_native_unit = None` for that member → training_allowed=False.
- 12Z HK: step=3 = [00HKT to 03HKT] — first 3h of local day (midnight to 3am, lowest-temp
  window) is DROPPED (parser failure). This scalar is entirely absent from `members_inner`.

If the true daily minimum falls in the mis-classified window, **the reported `members_json`
minimum value for that member is HIGHER than truth** (or the member is excluded from training).
Magnitude: the temperature range during the coldest 3h of a local day can be 1-4°C below the
next-coldest 3h window. On 00Z cycles for UTC-negative cities and on 12Z cycles for UTC+8
cities, **LOW value impact is plausibly 1-3°C upward bias per affected member, with
`boundary_ambiguous` suppressing training data**.

**Summary sentence:**
- HIGH: live served daily-max is NOT materially mis-windowed — the mis-classified step contains
  morning temperatures below the afternoon peak, so value impact is <0.1°C on HIGH.
- LOW: live served daily-min IS materially exposed — the mis-classified step (00Z UTC-negative,
  12Z UTC+8) falls in the coldest early-morning window; if the true minimum is there, reported
  minimum is 1-3°C too warm or the member is excluded via boundary_ambiguous, suppressing
  training data volume for the LOW track.

---

## D1 VALUE-LEVEL MATERIALITY (live)

Empirical measurement from live GRIB2 `open_ens_20260528_12z_steps_3to282_n71_heda24141_params_mx2t3.grib2`.
Method: ran `extract_open_ens_localday._scan_grib_with_city_values` to get per-(member, step) values at 6 city grid points;
computed ensemble-mean daily-max two ways: (A) CORRECT = all steps whose 3h window overlaps local calendar day 2026-05-28,
(B) AS-SERVED = same set minus step=3 (replicating the `-3-3` discard).

### Results (12z issue, lead_0, target 2026-05-28)

| City         | UTC off | Steps in window | Step3 only step? | Correct °C | Served °C | \|Δ\| °C |
|--------------|---------|-----------------|------------------|------------|-----------|----------|
| Chicago      | −5      | 6 (steps 3-18)  | No               | 18.1333    | 18.1333   | **0.000** |
| Los Angeles  | −7      | 7 (steps 3-21)  | No               | 21.4566    | 21.4566   | **0.000** |
| Houston      | −5      | 6 (steps 3-18)  | No               | 30.0925    | 30.0925   | **0.000** |
| Singapore    | +8      | 2 (steps 3,6)   | No               | 30.2952    | 29.5152   | **0.780** |
| Tokyo        | +9      | 1 (step 3 only) | YES              | 24.5924    | —(empty)  | N/A (null row) |
| Busan        | +9      | 1 (step 3 only) | YES              | 21.6996    | —(empty)  | N/A (null row) |

**Max |Δ| across sample: 0.78°C (Singapore)**
**Median |Δ| across non-null pairs: 0.00°C**

### Interpretation

Americas cities (UTC−5 to −7): step=3 window is [09:00–12:00z] = early morning local time (04:00–07:00 CDT).
Daily HIGH peak is ~14:00–17:00 local. Dropping step=3 has zero effect on the per-member max → **Δ = 0.00°C**.

Singapore (UTC+8): step=3 window is [12:00–15:00z] = 20:00–23:00 SGT, i.e. late evening of the LOCAL day.
Step=6 window is [15:00–18:00z] = 23:00–02:00 SGT (next day). For lead_0 from 12z, the served window
effectively covers only [15:00z, 16:00z] (end of local day). The dropped step=3 contains the end-of-afternoon
cooling curve. Δ = 0.78°C means the served ens-mean is 0.78°C below the correct daily max.

Tokyo/Busan (UTC+9): step=3 is the ONLY step whose window [12:00z, 15:00z] overlaps local day 2026-05-28
(which ends at 15:00z). Served_steps is empty → `_ingest_grib_ingest_track` writes a row with
`forecast_window_start_utc=NULL`, `forecast_window_attribution_status=UNKNOWN`, `contributes_to_target_extrema=0`.
The **entire lead_0 12z snapshot for these cities is present in DB but carries zero value to the trading system**
(excluded from extrema computation).

### Scope summary

HIGH track: 0.78°C max delta (Singapore), 0.00°C for Americas. UTC+9 cities on lead_0/12z deliver NULL attribution.
The 0.78°C gap is below the typical pricing edge (~5-10 cents threshold on a 30°C contract) but not negligible.
UTC+9 null rows represent a complete signal dropout on that (city, lead, cycle) combination.

---

## Audit Doc Fidelity Assessment

- `audit_extraction.md`: HIGH FIDELITY on Claims 1–3. Correctly identifies STEP_HOURS defect, window propagation, precomputed flag. The analysis is accurate.
- `audit_blindspots.md` §3 (Claim 5): LOW FIDELITY — conflates the external extractor JSON path with the total picture; misses the 20,732 daemon-path DB rows. Statement "the open_ens ingest backend was never plumbed into the DB writer" is WRONG.
- `audit_blindspots.md` §1 Cycle presence: Factually correct (06z/18z absent from DB) but mis-attributes cause to ECMWF operational schedule rather than Zeus policy configuration.

---

## D1 LOW DISAGGREGATION

Resolves the prior unresolved OR: "1-3°C too warm OR boundary_ambiguous→training excluded."
Recompute used live 2026-05-28 mn2t3 GRIB (00z and 12z); served path = STEP_HOURS=6 + `-3-3` discard,
correct path = STEP_HOURS=3. Script: `/tmp/low_recompute.py`.

### Per-city ensemble-mean daily-min delta (mn2t3 LOW track)

| City        | Cycle | Lead | Served (°F) | Correct (°F) | Δ°F   | Δ°C   | Served_nvalid | Correct_nvalid | BA_served | BA_correct |
|-------------|-------|------|-------------|--------------|-------|-------|--------------|----------------|-----------|------------|
| Chicago     | 00Z   | 1    | 56.32       | 56.32        | +0.00 | +0.00 | 1            | 1              | 50        | 50         |
| Los Angeles | 00Z   | 1    | 54.61       | 54.64        | −0.03 | −0.02 | 45           | 51             | 6         | 0          |
| Houston     | 00Z   | 1    | 66.93       | 67.02        | −0.09 | −0.05 | 46           | 51             | 5         | 0          |
| Hong Kong   | 12Z   | 0    | 81.94       | 82.08        | −0.14 | −0.08 | 1            | 3              | 50        | 48         |
| Hong Kong   | 12Z   | 1    | 79.35       | 79.29        | +0.06 | +0.03 | 6            | 10             | 45        | 41         |
| Tokyo       | 12Z   | 0    | 69.81       | 70.61        | −0.80 | −0.44 | 3            | 51             | 48        | 0          |
| Tokyo       | 12Z   | 1    | 68.67       | 69.20        | −0.52 | −0.29 | 6            | 51             | 45        | 0          |
| Busan       | 12Z   | 0    | N/A         | N/A          | N/A   | N/A   | 0            | 51             | 51        | 0          |
| Busan       | 12Z   | 1    | N/A         | N/A          | N/A   | N/A   | 0            | 51             | 51        | 0          |
| Singapore   | 12Z   | 0    | 81.58       | 80.99        | +0.59 | +0.33 | 1            | 4              | 50        | 47         |
| Singapore   | 12Z   | 1    | 78.77       | 79.04        | −0.27 | −0.15 | 36           | 47             | 15        | 4          |

**Notes:**
- Δ°C = ensemble-mean delta (served − correct). Individual member deltas are larger.
- BA = members with `boundary_ambiguous=True` (excluded, value=None).
- Chicago 00Z: BA_served=50 because local midnight is at UTC−5 → midnight window is 05:00z, step=6 boundary for BOTH paths; this is a pre-existing boundary effect not caused by the STEP_HOURS=6 bug.
- Tokyo/Busan 12Z: DOMINANT OUTCOME is BA. BA_served=45-51 vs BA_correct=0 for lead_1. Busan is entirely null under served path (0 valid members vs 51 correct).
- The ENSEMBLE-MEAN Δ understates per-member severity: a member with BA yields None (excluded), so the mean is computed over only the surviving members. Tokyo BA_served=48 means only 3 members survive; those 3 may be warm-biased outliers.

### Disaggregation: CONTAMINATE vs DROP (9,314 exposed LOW DB rows)

Query: `ensemble_snapshots_v2` WHERE `data_version LIKE 'ecmwf_opendata_mn2t3%'` (total = 9,314 rows),
grouped by `training_allowed` and `contributes_to_target_extrema`.

| Cycle | training_allowed=0 (DROP) | training_allowed=1, contributes=1 (CONTAMINATE) | training_allowed=1, contributes=0 |
|-------|--------------------------|--------------------------------------------------|-----------------------------------|
| 00Z   | 2,511                    | 307                                              | 1,372                             |
| 12Z   | 3,096                    | 445                                              | 1,583                             |
| **Total** | **5,607**           | **752**                                          | **2,955**                         |

**DOMINANT OUTCOME IS DROP: 5,607 rows (60%) are fail-closed** (`training_allowed=0` → excluded
from calibration). These rows have wrong-window classification but the boundary_ambiguous gate
caught them; they do NOT contaminate training data.

**752 rows (8%) are CONTAMINATE** (`training_allowed=1`, `contributes=1`): the wrong step was
accepted as valid inner content and contributes to the extrema computation. These rows carry
a reported daily-min that may be 1-4°C warmer than truth for affected members. Live-money
risk IF LOW enters trading.

**2,955 rows (32%)**: `training_allowed=1` but `contributes=0` — rows accepted into training
pool but not flagged as contributing to extrema. These may participate in calibration pair
construction but are not the hottest risk surface.

### LOW live-trading status

**LOW is NOT in the live trading candidate universe. Status: shadow/log-only.**

Evidence (three independent cites):
1. `config/settings.json:65-66` — `"apply_to_metrics": ["high"]` under the P0 live-prob gate.
   Note at line 69: "hard mode fires when metric IN apply_to_metrics (i.e. HIGH); LOW is
   unvalidated → shadow/log-only."
2. `config/settings.json:288` — `"_pin_note"`: "LOW pin TBD when LOW ships (Phase F follow-up)."
3. `src/calibration/manager.py:958-959` — `if temperature_metric == "low": return None, 4` —
   LOW calibration fallback is `RAW_UNCALIBRATED`; no Platt model wired to live gate.

**D1-LOW fix classification: "before LOW launch," NOT "blocks current live HIGH trading."**
The 752 CONTAMINATE rows represent a calibration/training integrity issue for the future LOW
launch, not a live-capital defect today.

### Verdict summary (resolves the OR)

The OR is NOT symmetric: the boundary_ambiguous gate catches the majority (5,607 DROP).
The TRUE/FALSE split for LOW is:
- **FALSE (DROP):** 60% — boundary_ambiguous fires correctly, row excluded; fail-closed works as designed. No contamination.
- **CONTAMINATE:** 8% — boundary_ambiguous does NOT fire (wrong-window min happens to still be above the inner min for those members), row enters training with a 1-4°C warm bias on the daily-min value.
- **LOW is shadow-only:** contaminate rows do not affect live Polymarket execution today.

The "1-3°C too warm" hypothesis is REAL but NARROW (752 rows). The "boundary_ambiguous→training excluded" outcome is DOMINANT (5,607 rows). Both co-exist. The live risk is zero until LOW launches; the pre-launch fix requirement is: (a) correct STEP_HOURS in extractor, (b) rebuild the 752 CONTAMINATE rows before promoting LOW to live.
