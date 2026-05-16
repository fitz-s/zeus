# Legacy Code Provenance Audit — `tigge_local_calendar_day_common.py` (MAJOR-5 Step 1)

**Auditor**: critic-alice-2 (opus). Date: 2026-04-17.
**Target**: `/Users/leofitz/.openclaw/workspace-venus/51 source data/scripts/tigge_local_calendar_day_common.py` (238 LOC).
**Purpose**: gate MAJOR-5 vendor-or-keep decision per team-lead ruling.

---

### File: `51 source data/scripts/tigge_local_calendar_day_common.py`

- **git**: external repo (not a git-tracked file in zeus). `stat ctime=mtime=2026-04-15T06:24:52`. Co-moved with both TIGGE plans (dated same day) + reference extractor (dated 2026-04-15). Era = pre-Phase-0 zeus work, but **Phase-0 TIGGE-plan-era** from the 51-source-data side.
- **Dependencies**: 1 sibling import — `from tigge_regions import region_for_city` (line 15). Vendoring requires also vendoring `tigge_regions.py` (39 LOC, pure-data region definitions). Two-file commit not one-file.

- **Phase 0+ law conformance**:
  - **MetricIdentity**: N/A. Module is metric-agnostic computational helper (returns bare dicts via `selected_manifest_rows`). Does not claim `temperature_metric` / `physical_quantity`. This is intentional separation; not a violation — callers (high extractor, low extractor) attach identity at their own seam.
  - **`data_version` tag currency**: N/A — module emits no data_version claims.
  - **Canonical tags / quarantine**: N/A — no tag-string knowledge.
  - **51 ensemble members**: NOT enforced here (member-count is caller concern). Neutral.
  - **Local-calendar-day semantics**: fully correct. `local_day_bounds_utc` (lines 82-86) + `iter_overlap_target_local_dates` (lines 102-107) use `ZoneInfo` + microsecond-rounding-down for boundary correctness. DST-aware.
  - **`ceil_to_next_6h` (line 110-112)**: matches exec-dan's zeus-side `_ceil_to_next_6h` verbatim including the `CEIL_EPSILON_HOURS=1e-9` epsilon. Identical algorithm.
  - **`required_max_step_for_target_local_date` (line 115-124)**: same formula exec-dan implemented as `compute_required_max_step`. Key difference: keyword-only args (`*, timezone_name, issue_date_utc, target_local_date`) vs exec-dan's `(issue_utc, target_date, city_utc_offset_hours)`. **Semantically equivalent but signature-incompatible**. Common uses `timezone_name` string + ZoneInfo internally (correct, DST-aware). Exec-dan's takes a pre-computed integer offset (test-friendly but DST-blind on the helper itself; he redrives offset DST-aware at the call site per my earlier review dispatch).
  - **Unit handling**: `kelvin_to_native` (line 47-51) has **the same silent-default fallthrough bug I flagged as MODERATE-1 in exec-dan's extractor** — `if unit.upper()=='F': <convert>; return value_c` means ANY non-F unit silently produces C. K, empty, "degC", "fahrenheit" all fall to the C branch. exec-dan already fixed this on the zeus side (lines 414-418: raises on unit ∉ {C,F}). **Vendoring this module verbatim would re-introduce the bug exec-dan already resolved.**

- **Silent assumptions**:
  - **CRITICAL — hardcoded absolute path at line 18**: `ROOT = Path("/Users/leofitz/.openclaw/workspace-venus/51 source data")`. This is a machine-specific absolute path. If vendored as-is into zeus, (a) the constant points at a directory outside zeus's sphere, (b) any `DEFAULT_MANIFEST = ROOT / "docs" / ...` resolution still references the external tree. Zeus-side callers must override or ignore `ROOT`/`DEFAULT_MANIFEST`. This is exactly the constant-vs-contract silent-drift surface flagged in LOW-4 for exec-dan's own extractor: machine-specific constants embedded in code.
  - **Permissive unit** (already covered above): NC-03 / MODERATE-1 pattern.
  - **Regional sharding assumption**: `region_for_city` from `tigge_regions.py` assumes 4 fixed regions (americas, europe_africa, asia, oceania) with hardcoded bounding boxes. Zeus may add cities in edge-case latitudes (e.g. new Nordic cities) that fall outside these boxes. No fallback — raises `ValueError` inside `region_for_city` (line 31+ of tigge_regions.py). Fail-closed, correct, but requires zeus city list to be a subset of these regions.
  - **`find_region_pairs` file-naming convention** (line 164-184): hardcoded filename pattern `tigge_ecmwf_control_param_{param_slug}_steps_*.grib` + `perturbed` substitution. If TIGGE archive naming ever changes, this fails silently (returns empty list, not error). Shared with exec-dan's `_find_region_pairs` (same hardcoded pattern at `scripts/extract_tigge_mx2t6_localday_max.py:524`). Not a vendoring concern — concern is shared across sides.

- **Verdict**: **STALE_REWRITE**.

  Rationale: the module is structurally correct on core logic (local_day_bounds, overlap, ceil_to_next_6h, issue_utc_from_grib_fields, city_slug, parse_steps_from_filename, parse_dates_from_dirname) but has TWO material bugs exec-dan already fixed on the zeus side (silent-default unit conversion) AND contains a machine-specific absolute path constant that would be dead weight in zeus. Vendoring verbatim is a regression. Vendoring with surgical edits is equivalent to just keeping exec-dan's Zeus-local helpers — except now zeus owns two parallel implementations and the drift surface is larger, not smaller.

  **The Phase 0 legacy-audit rule** ("no mechanical reuse; audit first, fix if needed") resolves this neatly: exec-dan's Zeus-local helpers are post-Phase-0, post-legacy-audit, already-fixed implementations. They have stronger provenance than the pre-Phase-0 common module. The common module is useful as a **reference** (e.g. when writing Phase 5 mn2t6 extractor, cross-check against this module's `required_max_step_for_lead_horizon` for Phase 5 boundary quarantine helper) but is not a reuse target.

- **Action per team-lead's conditional-Step-2 rule**: **KEEP exec-dan's Zeus-local helpers**. Do NOT vendor. Document MAJOR-5 resolution in single-commit message as: "MAJOR-5 resolved by accept-drift-debt: Zeus-local helpers preferred over pre-Phase-0 common. Common module's silent-default Kelvin bug (line 47-51) + hardcoded absolute path (line 18) + signature-incompatible keyword-only API make vendoring a regression. Reference copy at `/51 source data/scripts/tigge_local_calendar_day_common.py` SHA256=<compute at commit time>; available for Phase 5 cross-reference but not imported."

---

## MAJOR-5 Step 2 ruling (derived from Step 1 verdict)

**Outcome = `NOT CURRENT_REUSABLE` → KEEP zeus-local helpers.**

exec-dan's 8-item fold-in dispatch stands. Drop Step 2 (vendor work) from his remaining list. He still has:

1. MAJOR-2 refinement (`boundary_ambiguous=False` explicit emission — not omitted).
2. R-L tightening ingest side (schema ALTER + ingest read).
3. LOW-4 (import `HIGH_LOCALDAY_MAX` from `src.types.metric_identity`).

testeng-grace's 5-item dispatch unchanged, with one addition:

- R-AA (new per team-lead ruling): cities cross-validate at extractor init — `config/cities.json` name-set equals TIGGE manifest name-set; (lat,lon) within ±0.01° per city; fail-closed `CityManifestDriftError` on mismatch. **Load-bearing**, not trivial: catches silent drift between Zeus city identity and TIGGE grid-snap coordinates.

## Forward note for Phase 5 / Phase 6

The common module's `required_max_step_for_lead_horizon` and `raw_max_step_index` are NOT in exec-dan's zeus-local set. If Phase 5 low-track extractor needs batch-level max-step indexing (likely, for coverage reports), re-audit these two functions then — could be safely vendored with header + the Kelvin bug AND the absolute-path constant both fixed during vendoring. Flag as Phase-5-scout work.

The `ROOT` absolute path constant pattern (line 18) is a bigger epistemic flag: any sibling module in `51 source data/scripts/` likely carries the same smell. Phase 5 legacy audits should automatically check for absolute-path constants before vendoring anything.
