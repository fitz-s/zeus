# PR 4 SCAFFOLD Report — DecisionGroupId + NOT NULL + §14.9 Decision + §14.10 Audit

**Date**: 2026-05-19  
**Branch**: `feat/phase0-pr4-decision-group-id-20260519`  
**Authority**: `PHASE_0_V4_ADDENDUM.md` (supersedes v3 in full)  
**Phase**: SCAFFOLD only — no production logic, no DB execution  

---

## 1. Scope Summary

PR 4 covers four structural decisions:

| Ref | Description | Status |
|-----|-------------|--------|
| R-4.1 | `DecisionGroupId` NewType + `decision_group_id_v1_hash()` | SCAFFOLD |
| R-4.2 | `NOT NULL` constraint on `calibration_pairs_v2.decision_group_id` (91M rows) | SCAFFOLD |
| R-4.3 | `§14.9` P_CLAMP_LOW operator deviation documented | ANNOTATION |
| R-4.4 | `§14.10` ±inf roundtrip audit | LIVE TESTS |
| §14.7 | `manager.py` n_eff fix | ALREADY DONE (pre-PR4) |
| §14.8 | Bootstrap group sampling | ALREADY DONE (pre-PR4, platt.py:170-181) |

---

## 2. Files Delivered

### New Files

| File | Type | Notes |
|------|------|-------|
| `src/contracts/decision_group_id.py` | SCAFFOLD | `DecisionGroupId` NewType + `decision_group_id_v1_hash()` signature |
| `scripts/audit_calibration_pairs_v2_null_groups.py` | SCAFFOLD outline | Read-only preflight audit |
| `scripts/migrate_calibration_pairs_v2_not_null.py` | SCAFFOLD outline | `--dry-run` default, `--apply`, `--require-free-disk-gib` |
| `scripts/rollback_calibration_pairs_v2_not_null.py` | SCAFFOLD outline | Emergency rollback |
| `tests/test_decision_group_id_constraint.py` | xfail SCAFFOLD | R-4.1 NOT NULL + hash tests |
| `tests/test_inv_eps_spec_conformance.py` | LIVE | INV-eps-spec-conformance drift detector |
| `tests/test_calibration_pairs_v2_migration.py` | xfail SCAFFOLD | R-4.3 migration fixture tests |
| `tests/test_outer_bin_inf_roundtrip.py` | LIVE | §14.10 ±inf roundtrip audit |
| `tests/test_decision_group_id_newtype_audit.py` | LIVE + xfail | R-4.5 AST + structural audit |

### Modified Files (annotation/comment only — no logic changes)

| File | Lines Changed | Notes |
|------|---------------|-------|
| `src/state/schema/v2_schema.py` | ~255 | Block comment marking NOT NULL target + migration context |
| `src/calibration/platt.py` | ~20-22 | §14.9 deviation annotation with trade documentation |
| `src/calibration/manager.py` | ~1099 | DecisionGroupId typing seam annotation |
| `architecture/topology.yaml` | appended | New `phase0-pr4-decision-group-id` packet declaration |
| `architecture/script_manifest.yaml` | after line 645 | Three new script entries |

---

## 3. §14.9 Decision Record

**Status**: Operator-approved documented deviation (2026-05-19).

| Dimension | Value |
|-----------|-------|
| Spec value (zeus_math_spec.md §14.9) | `eps=1e-6` |
| Code value (`platt.py:22`) | `P_CLAMP_LOW=0.01` |
| Deviation factor | 10,000× |
| Information loss range | `p ∈ [1e-6, 0.01] ∪ [0.99, 1-1e-6]` |
| Calibration rows affected if changed | 91,040,450 (full refit required) |
| Logit range at 0.01 | [-4.6, +4.6] (stable for lbfgs) |
| Logit range at 1e-6 | [-13.8, +13.8] (destabilising in tail samples) |
| Decision | **Option 1: Amend spec to 0.01**. Document trade, enforce CI antibody. |
| CI antibody | `tests/test_inv_eps_spec_conformance.py` — fails immediately if `P_CLAMP_LOW` drifts |

**To change P_CLAMP_LOW in the future**:
1. Update `zeus_math_spec.md §14.9` to new value.
2. Update `EXPECTED_P_CLAMP_LOW` in `test_inv_eps_spec_conformance.py`.
3. Schedule full calibration refit (91M rows on forecasts.db).
4. Operator approval required.

---

## 4. NOT NULL Migration — Preflight Evidence

From `preflight/migration_dry_runs.json` (2026-05-17):

| Table | DB | Rows | NULL decision_group_id | Verdict |
|-------|----|------|------------------------|---------|
| `calibration_pairs_v2` | `zeus-forecasts.db` | 91,040,450 | **0** | SAFE |
| `calibration_pairs_v2_archived_2026_05_11` | `zeus-world.db` | 53,490,902 | **0** | SAFE |

**Migration path**: `SINGLE_STEP` (no backfill needed — 0 NULLs).

### Disk Constraint (OPERATOR ACTION REQUIRED)

| Resource | Value |
|----------|-------|
| `zeus-forecasts.db` size | ~49 GiB |
| `zeus-world.db` size | ~39 GiB |
| Peak disk needed (rebuild) | ~50 GiB (new table = full copy) |
| Free disk at audit (2026-05-17) | **~22 GiB** |
| **Shortfall** | **~28 GiB** |

**Operator must free ~30+ GiB before running `migrate_calibration_pairs_v2_not_null.py --apply`.**

The migrate script will enforce `--require-free-disk-gib 55` (default) and refuse to execute with insufficient headroom.

---

## 5. §14.10 ±inf Audit Findings

`P_CLAMP_LOW=0.01` / `P_CLAMP_HIGH=0.99` prevent `±inf` logit values in all normal operation paths. The audit is **largely closed** (per critic_4_pr4_stats.md). Live tests in `test_outer_bin_inf_roundtrip.py` confirm:

- `logit_safe(0.0)` and `logit_safe(1.0)` are finite.
- `logit_safe(p)` is finite for all `p ∈ [0, 1]`.
- `calibrate_and_normalize` sums to 1.0 even with outer-bin extremes near 0/1.
- `normalize_bin_probability_for_calibration` is finite for valid bin widths.

No changes to production code required for §14.10.

---

## 6. Topology Admission

Packet declared in `architecture/topology.yaml`:

```
id: "phase0-pr4-decision-group-id"
```

Covers all 13 PR 4 files plus `architecture/topology.yaml` and `architecture/script_manifest.yaml` in `allowed_files`.

---

## 7. Invariants Established

| Invariant | Mechanism |
|-----------|-----------|
| `INV-eps-spec-conformance` | `test_inv_eps_spec_conformance.py` — CI antibody against P_CLAMP_LOW drift |
| `INV-group-id-type` | `DecisionGroupId` NewType in `src/contracts/decision_group_id.py`; mypy enforcement at call sites post-PR4 |
| NOT NULL enforcement | `test_decision_group_id_constraint.py` (xfail until migration runs) |

---

## 8. What This PR Does NOT Do

- Does NOT change `P_CLAMP_LOW` value (documented deviation only).
- Does NOT execute the NOT NULL migration on any live DB.
- Does NOT implement `decision_group_id_v1_hash()` business logic.
- Does NOT wire `DecisionGroupId` type into `manager.py` call sites.
- Does NOT touch `zeus-forecasts.db` or `zeus-world.db`.
- Does NOT alter any existing test to pass differently.

---

## 9. Next Steps (PR 4 Implementation Phase)

1. **Operator frees ~30+ GiB disk** on the Zeus host.
2. Implement `decision_group_id_v1_hash()` in `src/contracts/decision_group_id.py`.
3. Wire `DecisionGroupId` type into `manager.py` (replace raw str at seam).
4. Implement migration scripts (remove `NotImplementedError`).
5. Run `audit_calibration_pairs_v2_null_groups.py` on live DBs to confirm still-0 NULLs.
6. Run `migrate_calibration_pairs_v2_not_null.py --apply --require-free-disk-gib 55`.
7. Activate (remove xfail from) `test_decision_group_id_constraint.py`.
8. Activate `test_calibration_pairs_v2_migration.py` against fixture.
9. Update `zeus_math_spec.md §14.9` to document 0.01 deviation explicitly.
