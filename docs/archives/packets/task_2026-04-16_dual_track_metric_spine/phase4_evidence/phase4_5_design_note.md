# Phase 4.5 Design Note — extract_tigge_mx2t6_localday_max.py

Author: exec-dan | Date: 2026-04-16 | Status: IMPLEMENTATION COMPLETE (awaiting critic-alice wide review)

---

## (a) Function signatures (5 testable public surfaces)

```python
def compute_required_max_step(
    issue_utc: datetime,
    target_date: date,
    city_utc_offset_hours: int,
) -> int:
    """
    Return minimum step hours required so a 6h bucket fully covers the end of
    target_date in the city's timezone. West-coast day7 → 204.
    Uses _ceil_to_next_6h(local_day_end_utc - issue_utc) with fixed UTC offset.
    Actual batch extraction computes step_horizon_hours via ZoneInfo for precision.
    """

def compute_manifest_hash(fields: dict) -> str:
    """
    SHA-256 of sorted-JSON of fields dict.
    Caller passes exactly these 6 keys:
    {data_version, physical_quantity, manifest_sha256, issue_time_utc, city, target_date_local}
    Stable across re-extractions of the same GRIB.
    """

def classify_boundary(
    inner_values: list[float],
    boundary_values: list[float],
) -> dict:
    """
    Classify per-member boundary ambiguity at snapshot level (not per-bucket).
    inner_values: member max values from fully-inner 6h buckets.
    boundary_values: member max values from boundary 6h buckets.
    Returns: {boundary_ambiguous: bool, ambiguous_member_count: int, training_allowed: bool}
    boundary_ambiguous = True when any boundary_min <= inner_min.
    """

def compute_causality(
    issue_utc: datetime,
    target_date: date,
    city_utc_offset_hours: int,
) -> dict:
    """
    Returns: {status: str, pure_forecast_valid: bool}
    High track: pure_forecast_valid = (issue_utc <= local_day_start_utc).
    status = 'OK' when pure_forecast_valid, else 'N/A_CAUSAL_DAY_ALREADY_STARTED'.
    """

def extract_one_grib_file(
    path,
    city,
    issue_utc: datetime,
    target_date: date,
    lead_day: int,
    *,
    cities_config=None,
    manifest_sha256_value: str = '',
    track: str = 'mx2t6_high',
) -> dict:
    """
    Read one GRIB file (control OR perturbed), return one JSON payload dict.
    Raises NotImplementedError for track != 'mx2t6_high'.
    NOTE: Single file = 1 member (control) or 50 members (perturbed); caller
    must combine both files for a full 51-member record.
    """
```

---

## (b) One-line description each

- `compute_required_max_step`: Compute the minimum GRIB step to cover end of a city's local calendar day (6h-aligned ceiling).
- `compute_manifest_hash`: Content-addressed SHA-256 hash of six provenance fields; stable across re-extraction.
- `classify_boundary`: Classify per-member boundary ambiguity at snapshot level; returns summary dict with training_allowed.
- `compute_causality`: Determine causality status and pure_forecast_valid for high track.
- `extract_one_grib_file`: Read one GRIB file and return full JSON payload dict (partial members if single file).

---

## (c) JSON output shape

```json
{
  "generated_at": "2024-01-01T00:00:00+00:00",
  "data_version": "tigge_mx2t6_local_calendar_day_max_v1",
  "physical_quantity": "mx2t6_local_calendar_day_max",
  "param": "121.128",
  "paramId": 121,
  "short_name": "mx2t6",
  "step_type": "max",
  "aggregation_window_hours": 6,
  "city": "Los Angeles",
  "lat": 33.9425,
  "lon": -118.408,
  "unit": "F",
  "manifest_sha256": "<sha256-of-manifest-file>",
  "issue_time_utc": "2024-01-01T00:00:00+00:00",
  "target_date_local": "2024-01-07",
  "lead_day": 6,
  "lead_day_anchor": "issue_utc.date()",
  "timezone": "America/Los_Angeles",
  "local_day_start_utc": "2024-01-07T08:00:00+00:00",
  "step_horizon_hours": 204,
  "local_day_window": {
    "start": "2024-01-07T08:00:00+00:00",
    "end": "2024-01-08T08:00:00+00:00"
  },
  "nearest_grid_lat": 33.75,
  "nearest_grid_lon": -118.5,
  "nearest_grid_distance_km": 18.2,
  "selected_step_ranges": ["144-150", "150-156", "...", "198-204"],
  "member_count": 51,
  "missing_members": [],
  "training_allowed": true,
  "manifest_hash": "<64-char sha256>",
  "members": [
    {"member": 0, "value_native_unit": 68.5},
    {"member": 1, "value_native_unit": 71.2},
    "... (51 total, member 0 = control forecast)"
  ]
}
```

**Key ingest-layer notes:**
- `unit` = `"C"` or `"F"` (single char). Ingest maps `C→degC`, `F→degF`.
- `value_native_unit` may be `null` for missing members.
- `local_day_start_utc` = local-calendar-day start time in UTC (ISO 8601). Required provenance field for `ensemble_snapshots_v2`.
- `step_horizon_hours` = max step hours requested from GRIB (e.g. 204 for LA day7). Required provenance field for `ensemble_snapshots_v2`.
- `causality` and `boundary_policy` are NOT emitted for high track (ingest defaults OK / no boundary).
- `lead_day` = integer days, anchor = `issue_utc.date()`.

---

## (d) Step horizon algorithm

```
For each (city, issue_utc, target_local_date):
  day_start_utc, day_end_utc = _local_day_bounds_utc(target_date, city_tz)  # via ZoneInfo
  step_horizon_hours = _ceil_to_next_6h((day_end_utc - issue_utc).total_seconds() / 3600.0)
  # West-coast (UTC-8): LA day7: issue 2024-01-01T00Z, target 2024-01-07
  #   day_end = 2024-01-08T08:00Z → delta = 200h → ceil_to_next_6h(200) = 204
  # GRIB files only contain steps downloaded; skip if required step absent.
```

`compute_required_max_step` (public, testable) uses a fixed integer UTC offset for deterministic unit testing. The batch path uses ZoneInfo directly (more accurate, handles DST).

---

## (e) Library choice: eccodes direct (not cfgrib/pygrib)

- eccodes Python bindings available in environment; cfgrib/pygrib not present.
- eccodes direct matches the pattern in the 51-source-data reference extractor.
- Regional GRIB files cover only their bounding box; cities outside raise `OutOfAreaError` (silently caught; all 4 regions together cover all 51 cities).
- 51 members = control file (perturbationNumber=0, member 0) + perturbed file (50 members, 1..50).

---

## (f) Provenance fields emitted (R-L contract)

All 7 ingest provenance fields emitted:
1. `data_version` = `"tigge_mx2t6_local_calendar_day_max_v1"`
2. `physical_quantity` = `"mx2t6_local_calendar_day_max"`
3. `manifest_sha256` — SHA-256 of TIGGE coordinate manifest file
4. `issue_time_utc` — ISO 8601 UTC
5. `local_day_start_utc` — local-calendar-day start in UTC (new, required by testeng-emma R-L)
6. `step_horizon_hours` — max step hours requested (new, required by testeng-emma R-L)
7. `manifest_hash` — content-addressed SHA-256 over 6 provenance fields

Note: `ingest_grib_to_snapshots.py` + `ensemble_snapshots_v2` schema will need ALTER to add columns for `local_day_start_utc` and `step_horizon_hours` (exec-carol's batch, Phase 4.5 fold-in commit).
