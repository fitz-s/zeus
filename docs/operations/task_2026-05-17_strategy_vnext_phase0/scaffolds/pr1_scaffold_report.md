# Phase 0 PR 1 SCAFFOLD Report: ResolutionEra + Twin Writer Consolidation + INV-37 ATTACH

**Branch**: `feat/phase0-pr1-resolution-era-20260519`
**Authored**: 2026-05-19
**Authority**: PHASE_0_V4_ULTRAPLAN.md §D.1, PHASE_0_V4_ADDENDUM.md, critic_1_pr1_settlement.md

---

## Topology Admission

**Received admission_status**: `advisory_only` (not `ADMITTED` as ULTRAPLAN expected)

**Root causes** (two pre-implementation actions required before implementation PR):

1. **Missing digest profile**: The multi-profile change set spans `money_path_pricing` + `data_ingestion` + `forecast_pipeline` + `state_read_model`. No profile in `architecture/digest_profiles.py` (canonical: `architecture/topology.yaml`) covers this combination. The topology_doctor CLI uses `topology_doctor_digest.py` profile matching, not the v_next binding layer.

2. **hard_stop for `src/execution/harvester.py`**: The binding YAML declares `src/execution/**` as `hard_stop_paths`. `harvester.py` (touch sites L1:1061 and L2:1338) returns `HARD_STOP` from `hard_safety_kernel.py`. Implementation PR requires explicit governance override before admission for this file.

**Mitigation applied in this SCAFFOLD**:
- `architecture/topology_v_next_binding.yaml` updated:
  - `money_path_pricing` profile: added `src/contracts/**` pattern
  - `forecast_pipeline` profile: added `src/engine/dispatch.py`, `src/strategy/market_phase_evidence.py`
  - Cohort `zeus.phase0_pr1_resolution_era` declared for the 7-file (non-hard-stop) change set
- With `--v-next-shadow`, the v_next admission engine reports `ADMIT` for all 7 non-hard-stop files
- Receipt at `preflight/route_receipts/pr1.json` documents actual status + gaps

---

## Files Authored

### New contracts/types

| File | Status | Purpose |
|------|--------|---------|
| `src/contracts/resolution_era.py` | SCAFFOLD | `ResolutionEra` (open str Enum), `EraAuthorityBasis` (frozen dataclass), era singleton constants |
| `src/state/settlement_writers.py` | SCAFFOLD | `write_settlement_v2_with_era_provenance()`, `dispatch_era_basis()`, `_build_era_provenance()`, era authority singletons |

### Scripts

| File | Status | Purpose |
|------|--------|---------|
| `scripts/audit_settlements_v2_era_provenance.py` | SCAFFOLD outline | Classify all settlements_v2 rows by era status |
| `scripts/backfill_settlements_v2_era_provenance.py` | SCAFFOLD outline | Idempotent backfill for 2829 BLEEDING rows |
| `scripts/rollback_settlements_v2_era_provenance.py` | SCAFFOLD outline | Restore pre-backfill provenance (requires snapshot) |
| `scripts/migrate_settlement_commands_in_flight_at_era_flip.py` | SCAFFOLD outline | Quarantine in-flight commands at era boundary (Critic P7-3) |

### Tests

| File | Status | Test IDs | Purpose |
|------|--------|----------|---------|
| `tests/test_resolution_era_dispatch.py` | SCAFFOLD xfail | R-1.1, R-1.2, ERA_DEAD boundary | Era dispatch relationship tests |
| `tests/test_twin_writer_no_duplicate_uma_tag.py` | SCAFFOLD xfail | R-1.3 | AST audit: `harvester_live_uma_vote` eliminated from twin paths |
| `tests/test_settlement_writer_inv37_attach.py` | SCAFFOLD xfail | R-1.4 | INV-37 ATTACH+SAVEPOINT compliance |
| `tests/test_uma_resolution_listener_late_revalidation.py` | SCAFFOLD xfail | R-1.5 | Reorg safety + late revalidation |
| `tests/test_inv_era_provenance_post_cutover_count.py` | SCAFFOLD skip | CI antibody | Post-merge COUNT=0 gate |

### Topology binding update

| File | Change |
|------|--------|
| `architecture/topology_v_next_binding.yaml` | Added `src/contracts/**` to `money_path_pricing`; added `src/engine/dispatch.py` + `src/strategy/market_phase_evidence.py` to `forecast_pipeline`; added cohort `zeus.phase0_pr1_resolution_era` |

---

## LOC Estimate

| Surface | Scaffold LOC | Est. Implementation LOC |
|---------|-------------|------------------------|
| `src/contracts/resolution_era.py` | 95 | 95 (no new bodies needed; types are the implementation) |
| `src/state/settlement_writers.py` | 140 | ~200 (fill pseudocode bodies) |
| Scripts (4) | 160 total | ~500 total |
| Tests (5) | 180 total | ~350 total (fill xfail bodies) |
| Binding YAML | 45 | 0 (done in SCAFFOLD) |
| **SCAFFOLD total** | **~620** | — |
| **Full PR 1 estimate** | — | **~1,150** |

---

## Citation Corrections (vs ULTRAPLAN v4)

| Citation | v4 stated | Verified actual | Status |
|---------|-----------|----------------|--------|
| L4: `harvester_truth_writer.py` twin writer | `:557` | `:556` | ROT — corrected in this report |
| L11: `execution_intent.py` `DecisionSourceContext` class | `:607-615` | class `:597`, fields `:607-615` | MINOR ROT — fields still valid |
| L2: `harvester.py` writer | `:1338` | `:1338` | PASS |
| L1: `harvester.py` gate | `:1061` | `:1061` | PASS |
| L3: `harvester_truth_writer.py` gate | `:353` | `:353` | PASS |
| `get_forecasts_connection_with_world()` | `src/state/db.py:205` | `:205` | PASS |

---

## Open Questions for SCAFFOLD Critic

**Q1 (BLOCKING): Digest profile for PR 1 multi-profile change set.**
The topology_doctor cannot report `ADMITTED` for the 8-file change set without a profile in `architecture/topology.yaml` (canonical) / `architecture/digest_profiles.py` (derived). The implementation executor must add `zeus.phase0_pr1_resolution_era` to `architecture/topology.yaml` and run `scripts/digest_profiles_export.py` before implementation admission. Is there a pre-existing profile that should subsume this, or does a new profile need operator ratification?

**Q2 (BLOCKING): Governance override for `src/execution/harvester.py` hard_stop.**
`src/execution/**` is in `hard_stop_paths`. The implementation PR must edit `harvester.py:1061` (L1 gate) and `:1338` (L2 writer). What is the governance override path? Options: (a) remove harvester.py from hard_stop for the duration of PR 1, restoring after merge; (b) declare a one-time operator-approved exception in the topology YAML; (c) split harvester.py changes into a separate PR with its own override.

**Q3 (ARCHITECTURE): ERA_DEAD watermark signal type.**
Critic P4 requires that when `uma_resolution_listener` returns an empty log window, the dispatcher must use an explicit ERA_DEAD watermark — not silent fallthrough. The SCAFFOLD uses `...` bodies. What is the intended signal type: an exception class, a sentinel return value, or a DB flag? This must be resolved before R-1.2 extension test bodies can be written.

**Q4 (SCHEMA): `era_watermark` table DDL.**
The SCAFFOLD references `era_watermark` in world.db for recording era transitions. The table DDL is described in pseudocode comments only. The implementation must define the full schema (columns, constraints, indexes) and create the migration. Is the DDL governed by `src/state/schema/v2_schema.py` (world.db schema) or a separate migration file?

**Q5 (DISK): Backfill safety with 22Gi free and active daemon.**
`preflight/migration_dry_runs.json` reports `disk_sufficient_for_rebuild: false` (22Gi free vs 87GB total DBs) and `daemon_active: true`. The backfill script outline specifies 500-row chunks + `PRAGMA busy_timeout=5000`. Is 500-row chunking sufficient to coexist with the daemon's write frequency, or does the backfill require a maintenance window with daemon paused? This must be answered before the backfill script is implemented.

---

## Pre-Implementation Checklist (for implementation executor)

- [ ] Add `zeus.phase0_pr1_resolution_era` digest profile to `architecture/topology.yaml`
- [ ] Regenerate `architecture/digest_profiles.py` via `scripts/digest_profiles_export.py`
- [ ] Obtain operator governance override for `src/execution/harvester.py` hard_stop
- [ ] Re-run `topology_doctor.py --navigation --intent modify_existing` with all 8 files; verify ADMITTED
- [ ] Resolve ERA_DEAD watermark signal type (Q3) before implementing R-1.2 extension test
- [ ] Resolve `era_watermark` table DDL (Q4) before implementing write path
- [ ] Fill implementation bodies in `src/state/settlement_writers.py` (pseudocode → real code)
- [ ] Consolidate `harvester.py:1338` and `harvester_truth_writer.py:556` through settlement_writers.py
- [ ] Consolidate gate logic at `harvester.py:1061` and `harvester_truth_writer.py:353`
- [ ] Remove all `xfail` markers from tests after implementation
- [ ] Remove `skip` from `test_inv_era_provenance_post_cutover_count.py` after backfill completes
- [ ] Verify antibody: COUNT=0 for `harvester_live_uma_vote` rows with `settled_at >= 2026-02-21`
