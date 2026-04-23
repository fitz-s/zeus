# Tests System Authority Book

**Recommended repo path:** `docs/reference/modules/tests.md`
**Current code path:** `tests`
**Authority status:** Dense system reference for the test suite as executable law, regression net, and architecture evidence.

## 1. Module purpose
Explain how the test suite protects law, module boundaries, and migration safety in Zeus, and why current machine mapping is still too thin.

## 2. What this module is not
- Not mere quality theater.
- Not proof by age.
- Not a flat blob of `pytest` files without topology.

## 3. Domain model
- Law-gate tests.
- Cross-module relationship tests.
- Migration and antibody tests.
- Advisory/diagnostic vs blocking categories.

## 4. Runtime role
Tests do not run in production, but they are the main executable proof that repo law is still encoded in code.

## 5. Authority role
Delivery law ranks executable source/tests above prose docs. A failing architecture/law test means the change or the plan is wrong, not that the test is inconvenient.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `tests/AGENTS.md`
- `architecture/test_topology.yaml`
- High-sensitivity test files such as architecture/law/cross-module/dual-track/data-source tests

### Non-authority surfaces
- Old skipped tests with stale assumptions
- Reports claiming a test once passed without current rerun

## 7. Public interfaces
- Pytest suites and contract validation manifests

## 8. Internal seams
- Blocking law tests vs useful regression tests vs diagnostics
- Top-level tests vs tests/contracts manifests
- Freshness headers vs stale reused tests

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `tests/test_architecture_contracts.py` | High-value law/structure gate. |
| `tests/test_cross_module_invariants.py / test_cross_module_relationships.py` | Relationship protection. |
| `tests/test_topology_doctor.py` | Machine governance/tooling gate. |
| `tests/test_day0_* / test_diurnal* / test_calibration* / test_db.py` | Module families that need stronger manifest routing. |
| `tests/test_tier_resolver.py / test_obs_v2_writer.py / test_hk_rejects_vhhh_source.py` | Recent antibody pattern examples. |

## 10. Relevant tests
- The entire suite is the subject here; top exemplars are listed above.

## 11. Invariants
- Old tests are not proof by age.
- High-sensitivity tests are not deleted/xfail'd without written sunset plan.
- Relationship tests matter more than isolated unit tests for cross-module work.

## 12. Negative constraints
- Do not classify advisory tests as active law gates.
- Do not let new top-level tests land without manifest classification.

## 13. Known failure modes
- Suite grows while `test_topology.yaml` remains too compressed to route it.
- Module work proceeds with only local unit tests and misses cross-module failures.
- Stale tests survive long after the underlying semantics changed.

## 14. Historical failures and lessons
- [Archive evidence] Many historical failures were semantic and cross-module; they would not have been caught by isolated local tests alone.

## 15. Code graph high-impact nodes
- Graph should identify impacted tests, but Zeus still lacks a strong text surface mapping module-to-test families.

## 16. Likely modification routes
- Any non-trivial source change should identify law, relationship, and module-specific tests.
- Any new module book should name relevant tests explicitly.

## 17. Planning-lock triggers
- Changes to test topology, high-sensitivity tests, or test category semantics.

## 18. Common false assumptions
- A large suite automatically means good coverage.
- If tests pass, architecture intent must have landed.
- One passing old test is enough evidence for reuse.

## 19. Do-not-change-without-checking list
- High-sensitivity law tests without explicit sunset or replacement plan
- Test manifest categories without updating topology docs

## 20. Verification commands
```bash
pytest -q tests/test_topology_doctor.py
pytest -q tests/test_architecture_contracts.py tests/test_cross_module_invariants.py tests/test_cross_module_relationships.py
python scripts/topology_doctor.py --tests --json
```

## 21. Rollback strategy
Rollback test-topology or law-test edits together with the code/doc change they supported.

## 22. Open questions
- Which existing tests are advisory/diagnostic but still look like active law gates because manifesting is too thin?

## 23. Future expansion notes
- Expand `architecture/test_topology.yaml` into a proper per-module and per-law routing map.

## 24. Rehydration judgement
This book is the dense reference layer for tests. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
