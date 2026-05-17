# Legacy Code Provenance Audit — Phase 4.5 Entry

**Auditor**: critic-alice-2 (opus). Date 2026-04-17.
**Scope**: 3 files exec-dan may reuse or mirror. Law baseline: Phase 0+ dual-track + §13-§22 + INV-14..22 + NC-11..15.

---

### File: `scripts/etl_tigge_ens.py`
- **git**: last touched `3e35808` "Close refit-preflight data rebuild package" (pre-Phase-0 preflight close; no Phase 1-4 commit has modified it).
- **conformance**:
  - MetricIdentity: N/A (pure metadata helper — no metric field).
  - members_unit: N/A (touches no temperature values).
  - data_version tag: N/A.
  - local-calendar-day: N/A (returns UTC cycle issue time, which is correct — `issue_time` IS UTC by construction).
  - 51 members: not hardcoded; iterates inputs.
  - DST: N/A (UTC-only).
- **Module status**: `main()` is a fail-closed stub (returns exit 2 with "retired" message). Only live export is `tigge_issue_time_from_members(members: list[dict]) -> str`.
- **verdict**: **CURRENT_REUSABLE** — but narrowly. The helper itself is a pure function that reads `data_date` / `data_time` from already-normalized member metadata and composes an ISO UTC string. It raises if members disagree (fail-closed, good). It has no coupling to old data_version tags, no Kelvin assumptions, no step-range constants.
- **action for exec-dan**: **Do not import this module.** The reference extractor at `51 source data/scripts/tigge_local_calendar_day_extract.py` already solves issue-time derivation via `issue_utc_from_grib_fields(data_date=..., data_time=...)` imported from `tigge_local_calendar_day_common`. That path is the Phase 0 canonical; `etl_tigge_ens.py`'s helper operates on a *different* input shape (post-normalization dicts, not raw GRIB fields) and would force exec-dan to normalize twice. Treat `etl_tigge_ens.py` as retired.

### File: `51 source data/scripts/tigge_local_calendar_day_extract.py`
- **git**: not a git repo; stat `ctime=mtime=2026-04-15`. Co-moved with both TIGGE plans dated 2026-04-15. This IS the Phase 0 canonical reference.
- **conformance**:
  - MetricIdentity: `TrackConfig` carries `mode`, `data_version`, `physical_quantity` — matches `HIGH_LOCALDAY_MAX` / `LOW_LOCALDAY_MIN` spine exactly (lines 54-55, 66-67).
  - members_unit: converts Kelvin to native via `kelvin_to_native(float(nearest["value"]), str(city["unit"]))` line 315 — explicit unit awareness, no silent K.
  - data_version tag: `tigge_mx2t6_local_calendar_day_max_v1` + `tigge_mn2t6_local_calendar_day_min_v1` — both post-quarantine canonical tags.
  - local-calendar-day: `local_day_bounds_utc(target_local_date, timezone_name)` + `iter_overlap_target_local_dates` + `overlap_seconds` gate (line 331). DST-aware (uses ZoneInfo via `timezone_name`).
  - 51 members: hardcoded `range(51)` at lines 98, 115, 169. Matches ECMWF TIGGE.
  - Low-track causality + boundary: full boundary policy (inner/boundary split lines 356-362, training_allowed=False if any member is boundary-ambiguous, line 190).
- **verdict**: **CURRENT_REUSABLE** — this is a Phase 0 reference implementation; no stale assumptions detected.
- **GAP vs R-R (the one gap exec-dan must NOT inherit silently)**: there is NO dynamic step horizon. The script filters by `max_target_lead_day` (default 7) and accepts whatever `stepRange` lands on disk. TIGGE_MN2T6 remediation plan + phase4_plan.md §4B call for `required_max_step = ceil_to_next_6h(local_day_end_utc - issue_utc)` so west-coast day7 requires step_204. **This computation is not in the reference.** If exec-dan mirrors verbatim, west-coast day7 slots will be silently truncated when the raw archive only carries through step_180. R-R test must fire on a synthetic west-coast-day7 case.
- **action for exec-dan**: **mirror the structure, add dynamic step horizon.** Import `tigge_local_calendar_day_common` helpers as-is (all current). Mirror the mx2t6_high `TrackConfig` into a zeus-side extractor. ADD a `required_max_step_for_city(city, issue_utc)` function and refuse to finalize a record whose highest present `stepRange` is below required — mark `training_allowed=false` + log. Do NOT copy the `TRACKS["mn2t6_low"]` branch (out-of-scope for 4.5; Phase 5).

### File: `scripts/ingest_grib_to_snapshots.py`
- **git**: last touched `5c48847` "Phase 4B" (2026-04-16, critic-alice PASS round 2). Current.
- **conformance** (quick scan only):
  - MetricIdentity: `ingest_json_file(... metric: MetricIdentity, ...)` line 148 — typed, no bare string.
  - members_unit: `_normalize_unit` maps `"C"/"F"` → `"degC"/"degF"`; `validate_members_unit` called line 169 before INSERT. Kelvin guard live.
  - data_version: `assert_data_version_allowed` gate present (per 4B verdict Q1); quarantined tags blocked.
  - 51 members: reads `members_json` from payload; does not hardcode count.
  - boundary / causality: reads `boundary_policy.boundary_ambiguous` + `causality.status` from payload (lines 106-133). Extractor-produced shape matches.
- **verdict**: **CURRENT_REUSABLE** — already the Phase 4B canonical downstream.
- **CONTRACT DRIFT FLAG (non-blocking for 4.5, flag for 4E / Phase 5)**: testeng-emma's R-L (dump §1) lists `local_day_start_utc` / `local_day_end_utc` / `step_horizon_hours` as required provenance fields. The reference extractor emits `local_day_window{start,end}` but ingest does NOT read it and does NOT store it as columns (grep confirms zero references to `local_day_window|step_horizon`). The v2 schema has no columns for them. 4B's R-L integration test passes because it only asserts the 7 DDL fields. The R-L spec in emma's dump is wider than the R-L implementation. Either R-L gets tightened (add columns + store) or emma's §1 gets narrowed. This is NOT a 4.5 blocker — the extractor emits the window correctly — but Gate C parity / Phase 5 low will depend on whether these fields are needed downstream.
- **action for exec-dan**: **consume, don't modify.** Match the payload shape the reference extractor already emits (`manifest_sha256`, `unit`, `value_native_unit`, `boundary_policy`, `causality`, `training_allowed`). Do not add fields the ingest doesn't read. If R-L gets tightened later, that's a separate ingest change — not 4.5 scope.

---

## On `tigge_issue_time_from_members` specifically

**Reuse: NO.** It is technically current-law (no quarantined tags, no silent unit drift), but it expects already-normalized member dicts with `data_date` / `data_time` keys — the raw eccodes pipeline in the reference extractor reads those fields directly from GRIB and composes the issue time via `issue_utc_from_grib_fields` (in `tigge_local_calendar_day_common`). Using `tigge_issue_time_from_members` would force double normalization. Scout-finn's "reusable" tag is accurate as a statement of cleanliness but wrong as a recommendation for Phase 4.5. The Phase 0 canonical path is `tigge_local_calendar_day_common.issue_utc_from_grib_fields`.

## Top carry-over for exec-dan

The reference extractor is structurally complete for high track EXCEPT dynamic step horizon (R-R). If exec-dan mirrors and adds that one function, R-Q/R-S/R-T/R-U all fall out of the reference's existing shape. Do not re-derive.
