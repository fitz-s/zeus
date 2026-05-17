# R12 Disposition: topology_schema.yaml and inv_prototype.py
# Created: 2026-05-06
# Authority basis: ULTIMATE_DESIGN §9.1; RISK_REGISTER R12; IMPLEMENTATION_PLAN Phase 1

## Background

RISK_REGISTER R12 and ULTIMATE_DESIGN §9.1 mark both files as "subsumed" by the new
capability/invariant architecture. Phase 1 task instructs: verify nothing imports from
these files before deleting; preferred default is delete.

## Import / Reference Audit

### architecture/topology_schema.yaml

Active references found (non-archive):

| File | Nature |
|------|--------|
| scripts/topology_doctor.py:30 | `SCHEMA_PATH = ROOT / "architecture" / "topology_schema.yaml"` — loaded at module import |
| scripts/topology_doctor_ownership_checks.py:27,29,39,41,52,54,64,66,76,78,138 | Used as `owner_manifest` for issue records (11 call sites) |
| tests/test_topology_doctor.py:4371,4379,4381 | Test asserts path exists and has correct owner_manifest |
| tests/test_admission_kernel_hardening.py:61 | File path in hardening test fixture |

**Verdict: RETAINED**

topology_schema.yaml is ACTIVELY LOADED at runtime by topology_doctor.py and referenced
by 2 test files and topology_doctor_ownership_checks.py. Deletion would break:
- scripts/topology_doctor.py (FileNotFoundError on import)
- tests/test_topology_doctor.py (4 assertions fail)
- tests/test_admission_kernel_hardening.py (fixture missing)

ULTIMATE_DESIGN §9.1 "subsumed" refers to its conceptual role being superseded by
capabilities.yaml + reversibility.yaml. However, the file also serves as a runtime
schema definition consumed by topology_doctor. Those two roles are distinct; the
registry role is subsumed, but the runtime-consumption role remains active.

Retention is the safe decision. Deletion deferred to a future phase after topology_doctor
is refactored to load from capabilities.yaml instead.

### architecture/inv_prototype.py

Active references found (non-archive):

| File | Nature |
|------|--------|
| tests/test_inv_prototype.py:37-41 | Directly loads file via `importlib.util.spec_from_file_location` |
| tests/test_inv_prototype.py:225 | `from architecture.inv_prototype import PROTOTYPED_INVS` |
| tests/test_inv_prototype.py:245 | `from architecture.inv_prototype import all_drift_findings` |
| scripts/topology_doctor_digest.py:802 | References path "architecture/inv_prototype.py" in digest |
| tests/test_topology_doctor.py:4944 | References path in test fixture |

**Verdict: RETAINED**

inv_prototype.py is directly imported (not just referenced by path) in test_inv_prototype.py,
which calls `PROTOTYPED_INVS` and `all_drift_findings` as live module attributes. Deletion
would cause ImportError on test_inv_prototype.py and break topology_doctor_digest.py.

The ULTIMATE_DESIGN §9.1 "subsumed" intent is that its drift-detection role is superseded
by the extended invariants.yaml (which now carries capability_tags and relationship_tests).
However, the file's runtime symbols are still consumed by tests. Deletion deferred to a
future phase after test_inv_prototype.py is updated or removed.

## Import Grep Verification

```
grep -rn "topology_schema\|inv_prototype" src/ scripts/ tests/ --include="*.py" --include="*.yaml"
```

Results confirm: both files have live consumers. Zero broken imports if retained.

## Net LOC Impact

Since both files are retained, Phase 1 LOC delta excludes the ~885 LOC deletion
originally projected. Net Phase 1 delta is additions only:
- architecture/reversibility.yaml: ~80 LOC added
- architecture/invariants.yaml: ~102 LOC added (3 new keys × 34 invariants)
- evidence/*.md: ~200 LOC added (docs)
- scripts/topology_route_shadow.py: ~20 LOC delta (classifier patch)

Phase 1 net-add: approximately +400 LOC. The IMPLEMENTATION_PLAN §3 "net-add ≤ net-delete"
target assumed R12 deletions would balance additions; that assumption was incorrect given
active imports. Operator acknowledgment required if the LOC constraint is firm.

## Recommended Future Action

1. Refactor topology_doctor.py to load capability/reversibility YAML instead of
   topology_schema.yaml, then delete topology_schema.yaml.
2. After inv_prototype.py drift-detection is superseded by invariants.yaml extended keys,
   update test_inv_prototype.py to test against invariants.yaml directly, then delete
   inv_prototype.py.
