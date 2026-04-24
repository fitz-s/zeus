# DT-v2 Package Triage — Phase 5B Entry Survey

# Lifecycle: phase5_evidence/scout_dt_v2_package_triage.md
# Purpose: One-pass relevance triage of zeus_dual_track_refactor_package_v2_2026-04-16/ for Phase 5B entry.
# Reuse: Read-only survey. Not a work-order; exec-dan reads alongside team_lead_handoff.md.

Author: scout-finn (sonnet, fresh post-compact)
Date: 2026-04-17
Branch: data-improve HEAD: 977d9ae (Phase 5A landed)

---

## File-by-file triage

| File / Dir | LOC | Content | Relevance |
|---|---|---|---|
| `00_MASTER_EXECUTION_PLAN_zh.md` | 828 | Full phase decomposition (Phases 1-9), structural decision set SD-1..SD-8, repo diagnosis. **Phase 5 at L565-584**: low historical lane — extract, coverage, ingest, calibration substrate. No mention of `scan_tigge_mn2t6`. | `[5B-CRITICAL]` |
| `01_REPO_DIAGNOSIS_AND_FATAL_BLOCKERS_zh.md` | 666 | Original repo diagnosis — 4 fatal blockers (schema single-track, Day0 asymmetry, boundary law not DB-encoded, source registry duplication). Most issues now resolved in Phases 1-5A. | `[REFACTOR-BACKGROUND]` |
| `02_FILE_BY_FILE_PATCH_MAP_zh.md` | 452 | Per-file patch specs: what to change, minimal safe approach, acceptance criteria. Covers db.py, market_scanner, observation_client, ensemble_signal, evaluator, replay, day0_signal, calibration, platt. **No mention of LEGACY_STATE_FILES or build_truth_metadata** (those belong to 5A seam, now landed). | `[5B-REFERENCE]` |
| `03_SCHEMA/00_WORLD_DB_V2_DECISION_zh.md` | 320 | Schema rationale for v2 tables. Decision basis for new primary keys. | `[REFACTOR-BACKGROUND]` |
| `03_SCHEMA/01_world_db_v2.sql` | 419 | Full DDL for all v2 tables including `historical_forecasts_v2`. **Authoritative row-identity reference** for 5C replay migration. | `[5C+]` |
| `03_SCHEMA/02_compat_views.sql` | 36 | Compat views bridging v1→v2 reads. | `[5C+]` |
| `04_CODE_SNIPPETS/ingest_snapshot_contract.py` | 66 | **Canonical low-track boundary + causality gate logic**. `validate_snapshot_contract()` encodes the `boundary_ambiguous → training_allowed=false` rule inline at L53-56. `N/A_CAUSAL_DAY_ALREADY_STARTED → training_allowed=false` at L58-59. This is the spec anchor for Phase 5B ingest. | `[5B-CRITICAL]` |
| `04_CODE_SNIPPETS/rebuild_calibration_pairs_v2.py` | 98 | Metric-parametrized rebuild snippet using `CalibrationMetricSpec` + `METRIC_SPECS` tuple. `iter_training_snapshots()` filters by `temperature_metric`, `data_version`, `training_allowed=1`, `authority='VERIFIED'`. Uses `spec.identity.observation_field` for dynamic field selection. | `[5B-CRITICAL]` |
| `04_CODE_SNIPPETS/refit_platt_v2.py` | 91 | Metric-aware refit snippet. | `[5B-REFERENCE]` |
| `04_CODE_SNIPPETS/metric_identity.py` | 69 | Reference MetricIdentity dataclass + singleton pattern. Already landed in repo. | `[REFACTOR-BACKGROUND]` |
| `04_CODE_SNIPPETS/day0_signal_router.py` | 117 | Day0 routing to `Day0HighSignal` / `Day0LowNowcastSignal`. Phase 6 scope. | `[5C+]` |
| `04_CODE_SNIPPETS/day0_observation_context.py` | 80 | `Day0ObservationContext` dataclass with `low_so_far`. Phase 6 scope. | `[5C+]` |
| `04_CODE_SNIPPETS/backfill_tigge_snapshot_p_raw_v2.py` | 99 | Metric-aware p_raw backfill. Phase 7 scope. | `[5C+]` |
| `04_CODE_SNIPPETS/source_registry_adapter.py` | 66 | Source registry centralization adapter. Phase 3 already handled. | `[REFACTOR-BACKGROUND]` |
| `05_TESTS/test_ingest_contract.py` | 17 | Stub tests for `validate_snapshot_contract`. R-AF anchor material for testeng-grace. | `[5B-REFERENCE]` |
| `05_TESTS/test_metric_isolation.py` | 52 | Tests that high/low rows don't cross-contaminate in v2 tables. | `[5B-REFERENCE]` |
| `05_TESTS/test_schema_dual_track.py` | 33 | Schema-level dual-track tests. | `[5B-REFERENCE]` |
| `05_TESTS/test_day0_low_nowcast.py` | 22 | Day0 low nowcast tests. Phase 6. | `[5C+]` |
| `05_TESTS/test_observation_context_low.py` | 24 | Low observation context tests. Phase 6. | `[5C+]` |
| `06_DOC_PATCHES/` | 232 | Doc patches for AGENTS.md, architecture, data_rebuild_plan, deep_map. Apply when main docs updated. | `[REFACTOR-BACKGROUND]` |
| `07_ROLLOUT/operator_checklist.md` | 34 | Pre-go-live gate checklist. | `[REFACTOR-BACKGROUND]` |
| `07_ROLLOUT/rollout_and_rollback_zh.md` | 187 | Rollout phases and rollback procedures. | `[REFACTOR-BACKGROUND]` |
| `08_TIGGE_DUAL_TRACK_INTEGRATION_zh.md` | 188 | **Low-track ingest field mapping** (physical_quantity, data_version, boundary_ambiguous, training_allowed formula). Canonical reference for extractor output JSON shape + ingest target mapping. | `[5B-CRITICAL]` |
| `README.md` | 60 | Package index and usage guide. | `[REFACTOR-BACKGROUND]` |
| `SOURCE_EVIDENCE_zh.md` | 42 | Evidence citations for structural decisions. | `[REFACTOR-BACKGROUND]` |
| `death_trap.md` | 18 | Death-trap law summary (commit-before-export ordering). Already encoded in canonical_write.py. | `[REFACTOR-BACKGROUND]` |
| `zeus-architecture-deep-map.md` | 1350 | Full architecture map — all layers, file responsibilities, hazards. | `[5B-REFERENCE]` |
| `zeus-pathology-registry.md` | 362 | Catalog of recurring failure modes (FM-##). | `[REFACTOR-BACKGROUND]` |
| `zeus-system-constitution.md` | 1500 | Invariant registry (INV-##) + structural laws. | `[5B-REFERENCE]` |

---

## Specific questions

### Q1: Scanner scope — `scan_tigge_mn2t6_localday_coverage.py`

**NOT MENTIONED** in the package. `grep -rn "scan_tigge_mn2t6"` across all 14 root files and all subdirs returns zero matches. The master plan Phase 5 scope (L565-584) lists "coverage" as a deliverable (`coverage 里 high/low 分开报告`) but names no specific script. Coverage reporting requirement is real but the script name/Gate-D binding is **not in this package** — it is a Zeus-internal convention, not package-specified.

**Verdict**: `scan_tigge_mn2t6_localday_coverage.py` is implied by Phase 5 acceptance criteria but deferred by this package. Its Gate-D requirement and timing must be ruled by team-lead, not derived from this package.

### Q2: `classify_boundary_low` — reference code in package

**`classify_boundary_low` does not appear anywhere in this package** (grep across all code snippets: zero matches). The boundary classification logic is encoded INLINE in `04_CODE_SNIPPETS/ingest_snapshot_contract.py:39-59`:

```python
boundary_ambiguous = bool(
    payload.get("boundary_policy", {}).get("boundary_ambiguous", False)
)
# ...
if spec.temperature_metric == "low" and boundary_ambiguous:
    training_allowed = False
    causality_status = "REJECTED_BOUNDARY_AMBIGUOUS"
```

The package design is: the **extractor** sets `boundary_policy.boundary_ambiguous` in the JSON output; the **ingestor** reads it as a pass-through field. There is no `classify_boundary_low` function in the package authority chain. Exec-dan's extractor must compute `boundary_ambiguous` (the cross-midnight leakage determination) and emit it into the JSON. The ingestor then gates on it. The package spec anchor is `ingest_snapshot_contract.py:39-59` + `08_TIGGE_DUAL_TRACK_INTEGRATION_zh.md §4.2`.

### Q3: B078 — LEGACY_STATE_FILES + build_truth_metadata for low-lane

**Not addressed in this package** at all. Zero matches for `LEGACY_STATE_FILES`, `build_truth_metadata`, `status_summary`, or `positions.json` across all package files. This is consistent with B078 being a Zeus-internal truth-file contract issue, not a TIGGE integration concern. B078 was assigned to Phase 5A/5B absorption per `zeus_dt_coordination_handoff.md` and is now owned by the Phase 5B commit per team_lead_handoff.md §Phase 5B scope.

### Q4: Phase boundary match — package Phase 5 vs our 5A/5B/5C decomposition

**Partial mismatch** — manageable:

- Package Phase 5 = "low historical lane: download + extract + coverage + ingest + calibration substrate" (monolithic).
- Our decomposition: 5A = truth authority seam (DONE at 977d9ae), 5B = extractor + ingest + rebuild/refit, 5C = replay MetricIdentity half-1.
- **Coverage reporting** is in package Phase 5 but NOT in our 5B scope. If Gate D requires a coverage report, this gap needs a team-lead ruling.
- **5C (replay migration to historical_forecasts_v2)** is our team's split-off from B093; the package defers this to Phase 7 ("metric-aware rebuild"). Our 5C half-2 aligns with package Phase 7 — the bifurcation is correct.
- No scope leakage risk: package phases are advisory at this point; Phases 1-5A are DONE. Package Phase 6+ (Day0 split, shadow) is our Phase 6-9.

---

## Recommended team-lead reads for Phase 5B entry

1. **`04_CODE_SNIPPETS/ingest_snapshot_contract.py`** (66 LOC) — The authoritative boundary + causality gate logic. Exec-dan's extractor must output JSON that passes `validate_snapshot_contract()`. Read before approving extractor design.
2. **`08_TIGGE_DUAL_TRACK_INTEGRATION_zh.md`** (188 LOC) — Definitive low-lane ingest field mapping (physical_quantity, data_version, JSON shape, `training_allowed` formula). The extractor output JSON schema lives here.
3. **`04_CODE_SNIPPETS/rebuild_calibration_pairs_v2.py`** (98 LOC) — Shows the `CalibrationMetricSpec` / `METRIC_SPECS` tuple pattern that exec-emma must implement when parametrizing rebuild/refit for `--track`. Authoritative shape for the `iter_training_snapshots()` query.
4. **`00_MASTER_EXECUTION_PLAN_zh.md §Phase 5`** (L565-584, ~20 lines) — The package's own acceptance criteria for Phase 5. Check "coverage 里 high/low 分开报告" against our Gate D definition.
5. **`05_TESTS/test_metric_isolation.py`** (52 LOC) — Test patterns that prove high/low rows never cross in v2 tables. Testeng-grace's R-AF letter set should cover these shapes.
