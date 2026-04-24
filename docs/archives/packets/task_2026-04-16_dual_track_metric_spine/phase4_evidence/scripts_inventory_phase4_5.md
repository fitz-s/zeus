# Scripts Inventory — Phase 4.5 Pre-Work

**Author**: scout-finn (replacing scout-dave)
**Date**: 2026-04-16
**Purpose**: Inventory GRIB-adjacent scripts and external data so exec-dan can implement
`scripts/extract_tigge_mx2t6_localday_max.py` without duplicating existing work.

---

## 1. GRIB-Adjacent Scripts in `scripts/`

All paths absolute. Status determined by reading file headers + grep for `main()` behavior.

### `scripts/ingest_grib_to_snapshots.py` — 379 lines — **LIVE (Phase 4B implementation)**

The Phase 4B canonical ingestor. Reads pre-extracted local-calendar-day JSON files and writes
to `ensemble_snapshots_v2`. This is NOT a stub — Phase 4B fully implemented it (commit `5c48847`).
exec-dan does NOT need to touch this file; he produces the JSON it consumes.

Key functions reusable as reference patterns:
- `ingest_json_file()` at line 148 — the consumer exec-dan must satisfy (see §2 below)
- `_normalize_unit()` at line 63 — maps `"C"→"degC"`, `"F"→"degF"`; any other value raises ValueError
- `_manifest_hash_from_payload()` at line 71 — SHA-256 over `{data_version, physical_quantity, manifest_sha256, issue_time_utc, city, target_date_local}`
- `_extract_causality_status()` at line 106 — defaults `"OK"` when `causality` key absent (high track)
- `_extract_boundary_fields()` at line 123 — defaults `(0, 0)` when `boundary_policy` key absent (high track)

### `scripts/etl_tigge_ens.py` — 43 lines — **RETIRED (deprecated_fail_closed)**

Header says "Deprecated fail-closed TIGGE ENS entrypoint." `main()` prints retirement message
and returns exit code 2. Contains one **reusable helper** exec-dan should adopt:

- `tigge_issue_time_from_members(members)` at line 8 — derives UTC ISO issue-time string from
  GRIB member metadata (`data_date` + `data_time`). Handles `data_time` formatting (e.g. `0` → `"0000"` → `"00:00"`). Exec-dan should copy or import this function rather than re-derive.

### `scripts/etl_tigge_calibration.py` — 27 lines — **RETIRED (deprecated_fail_closed)**

Header says "Deprecated fail-closed TIGGE direct-calibration entrypoint." `main()` prints
retirement message, returns exit code 2. No reusable logic.

### `scripts/etl_tigge_direct_calibration.py` — 19 lines — **RETIRED (deprecated_fail_closed)**

Header says "Deprecated fail-closed TIGGE direct-calibration entrypoint." `main()` prints
retirement message, returns exit code 2. No reusable logic.

### `scripts/backfill_ens.py` — 186 lines — **LIVE (Open-Meteo ENS backfill, not TIGGE)**

Fetches ECMWF 51-member ENS via Open-Meteo API (`past_days` max 93 days). Writes to
legacy `ensemble_snapshots` (v1 table), NOT `ensemble_snapshots_v2`. Different data source
and different target table — no conflict with Phase 4.5 TIGGE extraction. No GRIB reading.

### `scripts/investigate_ecmwf_bias.py` — ~30+ lines — **DIAGNOSTIC (read-only)**

Reads from DB via `get_world_connection`. No GRIB logic. Queries `ensemble_snapshots` for
seasonal bias. Diagnostic only — no write targets relevant to exec-dan.

### `scripts/backfill_tigge_snapshot_p_raw.py` — unread (not GRIB-adjacent)

Detected via Glob but not GRIB-reading (name suggests P_raw backfill from snapshots, not
GRIB parsing). Not relevant to exec-dan's task.

---

## 2. `ingest_grib_to_snapshots.py` — Output-Consumer Shape (file:line anchors)

exec-dan's extractor must produce JSON files that satisfy `ingest_json_file()`. Every field
below is read by the ingestor; missing fields cause silent wrong-default behavior (per
exec-bob's final dump §2).

**Required top-level fields** (sourced from `exec_bob_final_dump.md §2` + disk-verified against
`ingest_grib_to_snapshots.py` lines 148–238):

| JSON field | Type | Where consumed in ingestor | Notes |
|---|---|---|---|
| `data_version` | str | line 163 → `assert_data_version_allowed()` | Must be `"tigge_mx2t6_local_calendar_day_max_v1"` |
| `unit` | str | line 167 → `_normalize_unit()` | `"C"` or `"F"` — NOT `"degC"` or `"K"` |
| `city` | str | line 171 | Exact name matching Zeus `cities_by_name` |
| `target_date_local` | str | line 172 | `"YYYY-MM-DD"` in city's local timezone |
| `issue_time_utc` | str | line 173 | ISO 8601 UTC e.g. `"2024-01-01T00:00:00+00:00"` |
| `physical_quantity` | str | line 86 (`_provenance_json`) | `"mx2t6_local_calendar_day_max"` |
| `manifest_sha256` | str | line 77 (`_manifest_hash_from_payload`) | SHA-256 of coordinate manifest |
| `lead_day` | int | line 141 → `lead_hours = lead_day * 24.0` | Days from issue to target local date |
| `training_allowed` | bool | line 185 | True only if all 51 members non-None |
| `members` | list[dict] | line 136 (`_members_list`) | 51 elements, each `{"member": int, "value_native_unit": float\|null}` |
| `nearest_grid_lat` | float | line 99 (`_provenance_json`) | Grid point used |
| `nearest_grid_lon` | float | line 100 (`_provenance_json`) | Grid point used |
| `nearest_grid_distance_km` | float | line 101 (`_provenance_json`) | Distance city→grid |
| `param` | str | line 91 (`_provenance_json`) | e.g. `"mx2t6"` |
| `short_name` | str | line 92 (`_provenance_json`) | GRIB short name |
| `step_type` | str | line 93 (`_provenance_json`) | e.g. `"max"` |

**Optional fields** (high track: ingestor defaults to OK/absent if missing):
- `causality` dict → `_extract_causality_status()` line 106 — high track omit; defaults `"OK"`
- `boundary_policy` dict → `_extract_boundary_fields()` line 123 — high track omit; defaults `(0, 0)`

**Output file path pattern** (from `ingest_grib_to_snapshots.py` lines 50–57 + `_TRACK_CONFIGS`):

```
51 source data/raw/tigge_ecmwf_ens_mx2t6_localday_max/{city_slug}/{issue_date}/
  tigge_ecmwf_mx2t6_localday_max_target_{target_date}_lead_{lead_day}.json
```

The ingestor calls `subdir.rglob("*.json")` (line 271) — it will pick up any `.json` under
the `tigge_ecmwf_ens_mx2t6_localday_max/` subdirectory. Directory structure within is flexible
as long as all files are discoverable via `rglob`.

---

## 3. External Data Location

**Path**: `/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/tigge_ecmwf_ens_regions_mx2t6/`

**Disk-verified count**: 420 GRIB files (exact match to handoff claim).

**Structure**: 4 region subdirectories:
```
americas/       — 57 date-range folders
asia/           — 57 date-range folders
europe_africa/  — 54 date-range folders
oceania/        — 54 date-range folders
```

**Date range**: `20240101` (2024-01-01) through `20250919` (2025-09-19).
- Earliest: `americas/20240101_20240103/`
- Latest: `oceania/20250919_20250921/`

**Naming convention** (sample — disk-verified):
```
tigge_ecmwf_control_param_121_128_steps_006-012-018-024-030-036-042-048-054-060-066-072-078-084-090-096-102-108-114-120-126-132-138-144-150-156-162-168-174-180-186-192-198-204.grib
tigge_ecmwf_perturbed_param_121_128_steps_006-012-018-024-030-036-042-048-054-060-066-072-078-084-090-096-102-108-114-120-126-132-138-144-150-156-162-168-174-180-186-192-198-204.grib
```

**Pattern decoded**:
- `control` = 1 control member; `perturbed` = 50 perturbed members → total 51 members
- `param_121_128` = ECMWF parameter 121 (mx2t6), table 128
- `steps_006-012-...-204` = 6-hourly accumulation steps from step 6 through step 204
- Step 204 = 204 hours = 8.5 days = supports west-coast day-7 requirement (step_204 needed for `lead_day=7` in Pacific timezone)
- Each date-range folder covers a 3-day window (e.g. `20240101_20240103`); files inside cover the full step horizon

Per folder: exactly 2 files (control + perturbed). Total 420 = 210 issue-date windows × 2 file types.

---

## 4. cities.json Timezone + Unit Map

**Path**: `/Users/leofitz/.openclaw/workspace-venus/zeus/config/cities.json`

All 51 cities — timezone + unit (disk-verified via python3 extraction):

**°F cities** (unit=F — members_unit must be `"degF"` after extraction):
| City | Timezone | DST |
|---|---|---|
| Atlanta | America/New_York | yes |
| Austin | America/Chicago | yes |
| Chicago | America/Chicago | yes |
| Dallas | America/Chicago | yes |
| Denver | America/Denver | yes |
| Houston | America/Chicago | yes |
| Los Angeles | America/Los_Angeles | yes |
| Miami | America/New_York | yes |
| NYC | America/New_York | yes |
| San Francisco | America/Los_Angeles | yes |
| Seattle | America/Los_Angeles | yes |

**°C cities** (unit=C — members_unit must be `"degC"` after extraction):
Amsterdam (Europe/Amsterdam), Ankara (Europe/Istanbul), Auckland (Pacific/Auckland),
Beijing (Asia/Shanghai), Buenos Aires (America/Argentina/Buenos_Aires), Busan (Asia/Seoul),
Cape Town (Africa/Johannesburg), Chengdu (Asia/Shanghai), Chongqing (Asia/Shanghai),
Guangzhou (Asia/Shanghai), Helsinki (Europe/Helsinki), Hong Kong (Asia/Hong_Kong),
Istanbul (Europe/Istanbul), Jakarta (Asia/Jakarta), Jeddah (Asia/Riyadh),
Karachi (Asia/Karachi), Kuala Lumpur (Asia/Kuala_Lumpur), Lagos (Africa/Lagos),
London (Europe/London), Lucknow (Asia/Kolkata), Madrid (Europe/Madrid),
Manila (Asia/Manila), Mexico City (America/Mexico_City), Milan (Europe/Rome),
Moscow (Europe/Moscow), Munich (Europe/Berlin), Panama City (America/Panama),
Paris (Europe/Paris), Sao Paulo (America/Sao_Paulo), Seoul (Asia/Seoul),
Shanghai (Asia/Shanghai), Shenzhen (Asia/Shanghai), Singapore (Asia/Singapore),
Taipei (Asia/Taipei), Tel Aviv (Asia/Jerusalem), Tokyo (Asia/Tokyo),
Toronto (America/Toronto), Warsaw (Europe/Warsaw), Wellington (Pacific/Auckland),
Wuhan (Asia/Shanghai)

**Dynamic step horizon requirement** (from handoff + step encoding above):

The GRIB files already contain steps through 204. The requirement is that the extraction code
selects the *correct* step(s) for each city's local calendar day. West-coast cities
(Los Angeles, San Francisco, Seattle) are UTC-8 (or UTC-7 DST), so `target_date_local`
day-end = 08:00 UTC next day. For lead_day=7, that requires accumulation through step 204+.
The step_204 files are present in every date-range folder — no download gap.

**Issue-to-local-midnight offset by timezone class** (exec-dan needs this for calendar-day max
aggregation):
- `America/Los_Angeles` (UTC-8/UTC-7): local midnight = UTC+8 or UTC+7 → latest step needed
- `America/New_York` / `America/Chicago` (UTC-5/UTC-4 or UTC-6/UTC-5): 5-6 hour offset
- All `Asia/*` and `Europe/*` timezones: ZoneInfo handles DST automatically

---

## 5. Python Dependencies

**`requirements.txt`**: grep for `cfgrib`, `pygrib`, `eccodes` → **no matches**. Neither
`cfgrib` nor `pygrib` is declared.

**`pyproject.toml`**: grep for `cfgrib`, `pygrib`, `eccodes` → **no matches**.

**Status**: MISSING. exec-dan must add either `cfgrib` or `pygrib` (+ `eccodes` system library)
to `requirements.txt` before the extraction script can run.

**Recommendation**: `cfgrib` (wraps `eccodes`, integrates with `xarray`). Existing codebase
uses `xarray` in other contexts. `pygrib` is lower-level and more direct. Either works;
cfgrib has cleaner member iteration for ECMWF ENS. System dependency: `eccodes` must be
installed via `brew install eccodes` on macOS.

---

## 6. Duplication / Dead-Code Hazards exec-dan Should Avoid

**1. Do NOT re-implement `tigge_issue_time_from_members()`** — it already exists in
`scripts/etl_tigge_ens.py:8`. The file is retired but the helper is correct and tested.
Import or copy it. Reimplementing risks `data_time` formatting bugs (trailing-zero edge cases).

**2. Do NOT write to `ensemble_snapshots` (v1)** — `backfill_ens.py` writes there; that path
is legacy. The extractor writes JSON files only; the ingestor (`ingest_grib_to_snapshots.py`)
handles the v2 DB write. Extractor = JSON producer only.

**3. Do NOT call `assert_data_version_allowed()` from the extractor** — that guard belongs
in the ingestor (line 165), not the extractor. The extractor is a raw GRIB→JSON transformer.
The provenance gate fires at ingest time.

**4. Do NOT use `peak_window` semantics** — `tigge_mx2t6_local_peak_window_max_v1` is now
in `QUARANTINED_DATA_VERSIONS` (Phase 4A). `data_version` must be
`"tigge_mx2t6_local_calendar_day_max_v1"` exactly.

**5. Do NOT default members_unit to `"degC"` silently** — the extractor must derive unit from
`cities.json` (`city["unit"]`): `"C"` → convert K→°C; `"F"` → convert K→°F. Kelvin values
must NEVER appear in the output JSON. `validate_members_unit` in the ingestor rejects `"K"`.

**6. Dead ETL scripts to ignore** — `etl_tigge_calibration.py`, `etl_tigge_direct_calibration.py`,
`etl_tigge_ens.py` all have `main()` that fails closed. They contain no reusable GRIB-reading
logic (they were pre-cfgrib stub callers). The only live code worth reusing is
`tigge_issue_time_from_members()` in `etl_tigge_ens.py`.

---

## Summary for exec-dan

- **Start fresh**: no existing GRIB-reading implementation to extend. Phase 4.5 is greenfield.
- **cfgrib missing**: add to `requirements.txt` first; `brew install eccodes` for system dep.
- **Reuse**: `tigge_issue_time_from_members()` from `etl_tigge_ens.py:8`.
- **Output contract**: satisfy every field in §2's required table (anchored at `ingest_json_file` line 148).
- **Input data**: 420 files across 4 region dirs, steps 006–204 (step_204 present — west-coast day7 covered).
- **Unit conversion**: K→°C or K→°F per city; output `unit: "C"` or `"F"` (single char, not `degC`).
- **Smoke test target**: 3 GRIB files, one per region (americas/asia/europe_africa), covering at least one °F city and one °C city.
