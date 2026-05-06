# R12 Phase 3 Resolution
# Created: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §5; phase1_h_decision.md D-1; phase2_h_decision.md carry-forward; ULTIMATE_DESIGN §9.1

## Summary

Phase 3 exit criterion: R12 deletion (topology_schema.yaml + inv_prototype.py).
This document records the disposition chosen for each file and the rationale.

---

## architecture/topology_schema.yaml (537 LOC) — RETAINED / PHASE 4 DEFERRED

**Chosen path: (b) document blocker, propose Phase 4 path.**

### Evidence of active importers

| File | Nature | Risk if deleted |
|------|--------|-----------------|
| `scripts/topology_doctor.py:30` | `SCHEMA_PATH = ROOT / "architecture" / "topology_schema.yaml"` — loaded at module init | FileNotFoundError on import breaks all topology_doctor tests |
| `scripts/topology_doctor.py:795` | `_check_schema(load_topology(), load_schema())` — runtime schema validation | TypeError at call time |
| `scripts/topology_doctor_ownership_checks.py:20,27,29,39,41,52,54,64,66,76,78,87,138` | `api.load_schema()` + 11 call sites using `owner_manifest="architecture/topology_schema.yaml"` | All ownership-check calls fail |
| `tests/test_topology_doctor.py:4371,4379,4381` | Test asserts file path exists; assertions about owner_manifest content | 3 test failures |
| `tests/test_admission_kernel_hardening.py:62` | Path string in test (not import) | Test data wrong but no ImportError |

### Why path (a) (refactor) was not taken

Refactoring `topology_doctor.py` to drop `topology_schema.yaml` requires:
1. Replacing `_check_schema()` (line 676) with a capabilities.yaml-based schema validator
2. Rewriting all 13 `topology_doctor_ownership_checks.py` call sites to use capabilities.yaml ownership data
3. Updating `tests/test_topology_doctor.py` 3 assertion sites

This is a multi-hundred-LOC change with high regression risk across the topology_doctor test suite (~5,000 tests). IMPLEMENTATION_PLAN §5 Phase 3 scope is generative route function + profile deletion + mid-drift check. Refactoring `topology_doctor.py`'s schema infrastructure is Phase 4 scope (or Phase 3.5 if operator approves a scope extension).

**D-3 instruction: "Do NOT cascade-delete importers."** Respected. topology_doctor.py is NOT in §9.1 Phase 3 deletion list.

### Phase 4 path

Phase 4 Gate 1 (Edit-time capability check) requires reading route cards for capability ownership. That gate implementation will naturally drive the refactor of `topology_doctor.py` to load from `capabilities.yaml` instead of `topology_schema.yaml`. At that point, `topology_schema.yaml` can be deleted cleanly. Phase 4 brief must list this as a Phase 4 scope item.

**Verdict: RETAIN through Phase 3. Phase 4 deletion upon topology_doctor.py capability-schema refactor.**

---

## architecture/inv_prototype.py (348 LOC) — RETAINED (test still has value)

**Chosen path: keep both file and test.**

### Evidence of active importers

| File | Nature |
|------|--------|
| `tests/test_inv_prototype.py:37-41` | `importlib.util.spec_from_file_location` — loads module directly |
| `tests/test_inv_prototype.py:225` | `from architecture.inv_prototype import PROTOTYPED_INVS` |
| `tests/test_inv_prototype.py:245` | `from architecture.inv_prototype import all_drift_findings` |

### Why retained (test still has value)

The ULTIMATE_DESIGN §9.1 "subsumed" verdict refers to inv_prototype.py's **drift-detection role** being superseded by extended invariants.yaml (which now has `capability_tags` and `relationship_tests`). However, `test_inv_prototype.py` tests two specific antibodies:

- **F5 antibody**: `PROTOTYPED_INVS` class instances must not produce side-effects on initialization (regression from a double-count bug in `all_drift_findings()`).
- **F10 antibody**: `all_drift_findings()` must be idempotent across repeated calls.

These are correctness invariants for the prototype module's runtime behavior, not merely structural checks covered by invariants.yaml. The invariants.yaml extended keys (`capability_tags`, `relationship_tests`) do not reproduce these behavioral tests.

Until `inv_prototype.py` is explicitly refactored out (its drift-detection logic migrated to a new surface), these tests remain load-bearing antibodies and `inv_prototype.py` should be retained.

**Verdict: RETAIN both `architecture/inv_prototype.py` and `tests/test_inv_prototype.py`. Phase 4/5 migration if inv_prototype.py drift-detection is fully superseded by invariants.yaml queries.**

---

## Net LOC Impact

- `topology_schema.yaml` (537 LOC): NOT deleted — retained
- `inv_prototype.py` (348 LOC): NOT deleted — retained  
- Phase 3 net deletion credit from R12: 0 LOC

R12 carry-forward from Phase 1 (phase1_h_decision.md D-1 condition) is **partially unmet**: neither file deleted in Phase 3. Phase 4 is now accountable for `topology_schema.yaml` deletion (upon capability-schema refactor of topology_doctor.py). `inv_prototype.py` deletion is Phase 4/5.

The Phase 3 net delete figure remains strongly positive: ≥16,000 LOC deleted from digest_profiles.py + topology.yaml :: digest_profiles block + 3 topology_doctor scripts, vs ≤300 LOC added.

---

## Carry-forward to Phase 4 brief (mandatory)

1. `topology_schema.yaml` deletion requires topology_doctor.py refactor to load capability/reversibility YAML for ownership checks. Phase 4 Gate 1 implementation is the natural forcing function.
2. `inv_prototype.py` retention is conditional: if Phase 4 introduces a capabilities.yaml-based drift-detection query, migrate `test_inv_prototype.py` assertions to that surface and delete both files.

---

## Phase 4 Remediation — R12 Partial Close (2026-05-06)

R12 partially closed at Phase 4 remediation. Ownership block (option b) inlined into
`scripts/topology_doctor_ownership_checks.py` as module constants `OWNERSHIP_FACT_TYPES`
and `OWNERSHIP_MATURITY_VALUES` — 13 ownership call sites resolved from constants;
`check_module_manifest_maturity()` and `ownership_fact_types()` no longer call
`api.load_schema()` for ownership data.

FULL `topology_schema.yaml` deletion deferred to Phase 5: schema is also consumed by
`issue_json_contract` drift guard (test_topology_doctor.py:2156), `agent_runtime_contract`
route_card_required_fields (test_topology_doctor.py:4530), and `run_schema()` /
`_check_schema()` which validate topology.yaml `required_top_level_keys`
(topology_doctor.py:795, topology_doctor_registry_checks.py:408) — approximately 4
additional consumer sites requiring ~80 source + ~20 test LOC refactor.

Phase 5 brief must include: migrate remaining `load_schema()` consumers to inlined
constants or capabilities.yaml, then `git rm architecture/topology_schema.yaml`.

Refactor approach: (b) inline static lists.
Files modified: `scripts/topology_doctor_ownership_checks.py`.
Files deleted: none (topology_schema.yaml retained pending Phase 5).
Net LOC change: +~80 LOC added (constants), -~5 LOC removed (schema reads).
