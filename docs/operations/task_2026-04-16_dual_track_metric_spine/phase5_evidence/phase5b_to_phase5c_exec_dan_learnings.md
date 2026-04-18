# Phase 5B → 5C Learnings: exec-dan (upstream data / extractor specialist)

Written: 2026-04-17. Scope: Phase 4.5 (mx2t6 extractor) + Phase 5B (mn2t6 extractor + ingest unblock).

---

## 1. Latent System Issues — Things I Saw But Didn't Flag

### Shared utility duplication across both extractors (HIGHEST RISK)

`scripts/extract_tigge_mx2t6_localday_max.py` and `scripts/extract_tigge_mn2t6_localday_min.py` both contain verbatim copies of:

- `_compute_manifest_hash` (lines ~115 / ~440)
- `_now_utc_iso`
- `_city_slug`
- `_overlap_seconds`
- `_local_day_bounds_utc`
- `_issue_utc_from_fields`
- `_parse_steps_from_filename`
- `_parse_dates_from_dirname`
- `_find_region_pairs` (modulo PARAM_ID constant)
- `_iter_overlap_local_dates`
- `_cross_validate_city_manifests`
- `_load_cities_config`

This is ~200 LOC duplicated. Any fix to one (e.g. a ZoneInfo DST edge case in `_local_day_bounds_utc`) must be applied to both or they silently diverge. The 5B-follow-up backlog already names `scripts/_tigge_common.py` extraction — this is the concrete list. A fresh executor WILL miss one file when fixing a shared helper. The antibody is `_tigge_common.py` with a test that imports both extractors and asserts they use the same implementation for `_overlap_seconds`, `_local_day_bounds_utc`, `_issue_utc_from_fields`.

### `_extract_causality_status` in `ingest_grib_to_snapshots.py` is now dead code

Post-5B contract wiring, `ingest_json_file` reads `causality_status` from the payload's `causality.status` field via `_extract_causality_status`. But `validate_snapshot_contract` now runs BEFORE the row is written and already extracts causality from the same path. The function at `ingest_grib_to_snapshots.py:106-120` is no longer the gate — the contract is. It's still called but its output is no longer the last word. Safe to audit; possibly delete.

### `BoundaryClassification.effective_min` behavior when `any_boundary_ambiguous=True`

In `build_low_snapshot_json` (line ~270), when `any_boundary_ambiguous` is True, I chose to emit `clf.inner_min` (diagnostic value) for each member in the output `members` list even though the snapshot is quarantined. This is intentional — the remediation plan §6 says "extractor may still write diagnostic fields." But downstream ingest reads `members_json` directly. If a future consumer reads `value_native_unit` from a quarantined snapshot and uses it without checking `training_allowed`, they get a value that may be contaminated. The members list should ideally carry a `quarantined=True` flag per-member, or `value_native_unit` should be `null` for quarantined snapshots. Currently it's not null — it's inner_min (non-null). This is a silent trap.

### `_compute_required_max_step` uses fixed-offset timezone, not ZoneInfo

Both extractors compute step horizon using `timezone(timedelta(hours=city_utc_offset_hours))` (fixed offset) not `ZoneInfo`. This means DST transitions are NOT correctly modeled in the step horizon for DST-observing cities. For a city like Chicago (UTC-5 in winter, UTC-6 in summer), the step horizon for a summer date will be computed at the wrong offset. The `extract_one_grib_file` function in the mx2t6 extractor calls `ZoneInfo(city_tz).utcoffset(issue_utc)` to get the offset first, which is correct — but this is a point-in-time snapshot of the offset and not the target-date offset. For dates spanning a DST boundary, the step horizon math can be off by 1 hour. This has not been tested with a DST-transition fixture.

### Member-count assumption: 51 is hardcoded but not validated at GRIB-read time

Both extractors assume `MEMBER_COUNT = 51` (member 0 = control, 1..50 = perturbed). This is never validated against the actual GRIB content. If ECMWF ever changes ensemble size (they announced a 100-member EPS in their roadmap), or if a historical file has fewer perturbed members (e.g., partial download), the extractor silently emits `missing_members=[...]` for the absent members. The `training_allowed=False` gate catches it, but the user gets no explicit warning that "this GRIB had only 40 members, not 50." A `WARN_MEMBER_COUNT_MISMATCH` log line when `len(member_values) != MEMBER_COUNT` would surface this earlier.

### 51-source STALE scripts beyond `tigge_local_calendar_day_common.py`

During Phase 4.5 onboarding I had to read the 51-source scripts directory. Beyond the already-verdicted `tigge_local_calendar_day_common.py` (STALE_REWRITE: Kelvin silent-default + hardcoded absolute path), I spotted:

- `/51 source data/scripts/` — several scripts with no provenance header, referencing paths that no longer exist in the current workspace layout. I did not audit them formally; they need STALE/DEAD/CURRENT verdicts before any Phase 6 reuse attempt.
- The manifest at `51 source data/docs/tigge_city_coordinate_manifest_full_latest.json` is referenced by both extractors as `DEFAULT_MANIFEST`. If this file is rotated/updated with new cities, both extractors need re-run of `_cross_validate_city_manifests`. There is no automated check that fires when the manifest changes.

### The 511 mn2t6 GRIB files: date coverage concern

Scout inventory shows 511 files from 2024-01-01 to 2025-11-22. That's ~22 months of data. Each GRIB file covers a date range (some span multiple days per the `_parse_dates_from_dirname` logic). What I don't know: whether the step coverage within each file is complete (6, 12, ..., 180+ hours) or whether some dates have truncated step ranges from download interruptions. The GRIB integrity validator (`validate_tigge_mn2t6_grib.py`) from the remediation plan is NOT yet implemented — it was listed as a Phase 3 download task but was deferred. Before full batch extraction, a step-coverage scan per date is essential. A date with only steps 6-72 will produce snapshots with `step_horizon_deficit_hours > 0` for any city with lead day > 3, and those will all have `training_allowed=False`.

---

## 2. Cross-Phase Patterns: MAX vs MIN Extractor Siblings

What repeated identically: manifest hash computation, step-range parsing, city config loading, output path structure, GRIB message iteration loop, member accumulation pattern, `codes_grib_find_nearest` call site.

What differed and must NOT be copied without thought: (a) `classify_boundary_low` is MIN-specific — the `boundary_ambiguous = boundary_min <= inner_min` check makes no sense for MAX (a boundary high never "leaks" into the local-day max in the same way), (b) `causality` is mandatory and first-class for LOW; it was optional/defaulted for HIGH, (c) `members_unit='K'` for LOW (raw Kelvin out); HIGH converts to 'C'/'F' at extract time, (d) `training_allowed` for LOW has a third gate (boundary_ambiguous) that HIGH does not.

For Phase 6's nowcast path: do NOT copy the historical-batch extractor pattern. Nowcast needs streaming, not file-by-file GRIB iteration. The key reusable primitive is `classify_boundary_low` applied to a live observation feed, not the `_collect_grib_file_low` accumulator.

---

## 3. Forward Hazards for 5C / Phase 6 / Phase 7

**Phase 5C (replay MetricIdentity):** The ingest pipeline now routes via `temperature_metric`, but the replay path may still assume a single-track world. Any replay code that calls `ingest_json_file` without passing the correct `metric: MetricIdentity` will silently write rows with wrong `observation_field`. Verify the replay path passes `LOW_LOCALDAY_MIN` not `HIGH_LOCALDAY_MAX` for low-track replays.

**Phase 6 (Day0 nowcast):** The extractor emits `N/A_CAUSAL_DAY_ALREADY_STARTED` for positive-offset city Day0 slots. The nowcast path must consume `low_so_far` + remaining-step forecast. The bridge input needed: `local_day_start_utc`, `issue_utc`, `city_tz` (to compute hours elapsed), and live observation of min-so-far. None of this comes from the historical extractor — it's a separate data source. Don't conflate.

**Phase 7 (zero-data window exit):** When user lifts the window, the recommended order for the 511 mn2t6 GRIB files: (1) smoke 1 file → validate JSON shape + `validate_low_extraction` passes; (2) sample 5% of dates across seasons → check per-city quarantine rates; (3) if quarantine rate < 20% for all cities, proceed to full batch; (4) flag cities exceeding 20% for review BEFORE ingest. Do not ingest first and audit later — boundary-quarantined rows that enter calibration pairs are hard to remove cleanly.

---

## 4. Fresh Executor Inheritance

**Must-read before touching either extractor:** `TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md` §4-§6 (step horizon + causality + boundary law), `08_TIGGE_DUAL_TRACK_INTEGRATION_zh.md` §3-§5 (JSON field contract), and the Phase 4.5 extractor itself to understand the sibling pattern.

**Gotcha #1:** `codes_grib_find_nearest` returns a list of dicts, not a single dict. Both extractors take `[0]`. This is correct for single nearest-point lookup, but if the API changes or `outOfBounds` is raised, the `except Exception: continue` silently skips the city for that message. A city that is consistently outside the GRIB bounding box will produce an all-null members list and `training_allowed=False` — with no log line explaining why.

**Gotcha #2:** The `is_control` flag is inferred from `"control" in file_path.name`. If ECMWF renames their files (e.g., `cf` instead of `control`), member 0 will be incorrectly assigned to the perturbed ensemble number from `codes_get(gid, "number")`, corrupting the member→value mapping. Both extractors have this brittleness.

**Gotcha #3:** `_parse_dates_from_dirname` splits on `"_"` to detect date ranges. Directory names like `20240101_20240107` work. But if a directory uses a different separator or has an unexpected format, the function raises `ValueError` and the entire pair is skipped without warning.

---

## 5. Tooling Observations

**Smoke-testing on 1 GRIB file without eccodes installed:** eccodes is a system dependency (`brew install eccodes`). If it's missing, the import fails at module load, not at GRIB open time. Confirm `python -c "from eccodes import codes_grib_new_from_file"` before running any extraction task.

**Smoke-test command (safe, output to /tmp):**
```bash
python scripts/extract_tigge_mn2t6_localday_min.py \
  --raw-root "/path/to/51 source data/raw" \
  --output-root /tmp/mn2t6_smoke \
  --manifest-path "/path/to/51 source data/docs/tigge_city_coordinate_manifest_full_latest.json" \
  --max-pairs 1 --cities "Chicago" --overwrite
```
Then `python -c "import json; d=json.load(open('/tmp/mn2t6_smoke/...json')); print(d['boundary_policy'], d['causality'])"` to spot-check fields.

**Directory layout:** `raw/tigge_ecmwf_ens_regions_mn2t6/{region}/{YYYYMMDD}/tigge_ecmwf_{control|perturbed}_param_122_128_steps_6-204.grib`. The `{region}` level is non-obvious — each region covers a geographic bounding box. Cities outside a region's bounding box produce silent skips (the `except Exception: continue` in `codes_grib_find_nearest`). If a city yields zero members across all regions, the output will be a correctly-shaped JSON with all 51 members null. This is currently indistinguishable from a download gap.
