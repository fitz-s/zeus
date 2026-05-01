# TIGGE Schema Contract Fix — Antibody #16
**Date:** 2026-04-29  
**Author:** Executor (Antibody #16)  
**Operator directive:** "有漂移需要采取一劳永逸的解决方案不要每次都不对称"

## Problem

`ingest_grib_to_snapshots.py` rejected 3,696 HIGH track (mx2t6) JSONs with `MISSING_CAUSALITY_FIELD`. These were extracted by an older extractor version that omitted `causality`. Additionally, the docstring in `extract_tigge_mx2t6_localday_max.py` falsely stated causality was "OMITTED for high track (ingest defaults to OK/0)" — this default was removed in a prior audit, making the docstring a lie that would mislead future sessions.

Root cause: schema drift between extractors and ingester with no structural enforcement preventing divergence.

## Fix

### Files Created/Modified

| File | Lines | Change |
|------|-------|--------|
| `src/contracts/tigge_snapshot_payload.py` | 485 | NEW — canonical schema dataclass |
| `scripts/extract_tigge_mx2t6_localday_max.py` | 685 | Import + use TiggeSnapshotPayload in both return paths |
| `scripts/extract_tigge_mn2t6_localday_min.py` | 712 | Import + use TiggeSnapshotPayload in build_low_snapshot_json |
| `scripts/ingest_grib_to_snapshots.py` | 410 | Add from_json_dict as ONLY JSON read path |
| `tests/test_tigge_schema_contract.py` | 359 | NEW — 13 antibody tests |
| `tests/test_ingest_grib_law5_antibody.py` | 248 | Updated assertion for PROVENANCE_VIOLATION |

### Structural Mechanism (Why One-and-Done)

`TiggeSnapshotPayload` is the single canonical schema class. Both extractors **must** call `make_high_track()` / `make_low_track()` and `to_json_dict()`. The ingester **must** call `from_json_dict()`. If an extractor omits a field that `from_json_dict` considers required (`causality`, `data_version`, `unit`, `members`, `issue_time_utc`, `city`, `target_date_local`, `lead_day`), `ProvenanceViolation` is raised at ingest time — fail-closed, never silent.

AST-scan tests (Tests 3 & 4) enforce the import at the source level: if a future refactor removes the import, the test fails before any JSON is produced.

## High Track Row Count Delta

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| `ensemble_snapshots_v2` HIGH rows | 342,312 | 344,580 | +2,268 |
| `max(issue_time)` for HIGH | 2026-04-18T00:00:00+00:00 | 2026-04-28T00:00:00+00:00 | +10 days |

3,696 stranded JSONs were migrated (causality backfilled). 2,268 new rows written (1,428 skipped as already-ingested duplicates in the overlap window).

## Test Results

```
115 passed, 0 failed
```

Tests: test_tigge_schema_contract.py (13), test_ingest_grib_law5_antibody.py (7), test_phase4_ingest.py, test_phase5b_low_historical_lane.py, test_phase5_fixpack.py, test_phase4_5_extractor.py.

## Antibody Reference

This fix is Antibody #16 in the design pack. The antibody mechanism:
- **Structural gate**: `from_json_dict` raises `ProvenanceViolation` if causality is absent or malformed — impossible to bypass
- **AST enforcement**: tests scan extractor/ingester source for import presence — survives refactors
- **Round-trip test**: synthetic payloads via dataclass accepted by ingester — regression-tested
