# Types Module Authority Book

**Recommended repo path:** `docs/reference/modules/types.md`
**Current code path:** `src/types`
**Authority status:** Dense module reference for value/object types that protect Zeus from category errors.

## 1. Module purpose
Make critical categories unconstructable or at least hard to misuse: units, market/bin identity, metric identity, observation atoms, and solar/auxiliary typed values.

## 2. What this module is not
- Not mere convenience wrappers.
- Not an optional style layer.
- Not the place to hide semantic conversions without tests.

## 3. Domain model
- Temperature and unit-aware quantities.
- Market/bin identity types.
- Metric identity types.
- Observation atoms and other typed payloads.

## 4. Runtime role
Shared foundational types used across contracts, signal, calibration, state, and execution.

## 5. Authority role
K0-adjacent safety substrate. Change-control constitution names semantic value types as frozen-kernel material.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `src/types/temperature.py`, `market.py`, `metric_identity.py`, `observation_atom.py`, `solar.py`
- `docs/authority/zeus_change_control_constitution.md` K0 frozen-kernel section
- `architecture/invariants.yaml` on metric identity and unit discipline

### Non-authority surfaces
- Primitive floats/ints in convenience scripts that bypass type protections

## 7. Public interfaces
- Typed value classes and validators

## 8. Internal seams
- Metric identity vs market/bin identity
- Temperature vs observation atom conversions

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `temperature.py` | Unit-aware temperature type. |
| `market.py` | Market/bin typing and validation. |
| `metric_identity.py` | High/low and model-family identity spine. |
| `observation_atom.py` | Typed observation payloads. |
| `solar.py` | Auxiliary typed solar values. |

## 10. Relevant tests
- tests/test_architecture_contracts.py
- tests/test_cross_module_invariants.py
- tests/test_config.py

## 11. Invariants
- Unit semantics must be protected by types and tests, not reviewer memory.
- Metric identity is first-class; bare strings are serialization details.

## 12. Negative constraints
- Do not collapse typed semantic values back to raw primitives without justification.

## 13. Known failure modes
- Unit or metric identity bugs become silently possible when types weaken.
- Stringly-typed market/bin labels drift from actual semantics.

## 14. Historical failures and lessons
- [Archive evidence] Change-control constitution explicitly elevated semantic value types into frozen-kernel territory because natural-language review alone was insufficient.

## 15. Code graph high-impact nodes
- `src/types/metric_identity.py` and `market.py` are likely small but high fan-out safety types.

## 16. Likely modification routes
- Any new type or field change must be reviewed across all consumers.

## 17. Planning-lock triggers
- Any change under `src/types/**` affecting value semantics.

## 18. Common false assumptions
- Types are just ergonomics and can be relaxed for speed.
- A primitive string is good enough for metric identity.

## 19. Do-not-change-without-checking list
- MetricIdentity semantics
- Unit-enforcing temperature behavior

## 20. Verification commands
```bash
pytest -q tests/test_architecture_contracts.py tests/test_cross_module_invariants.py tests/test_config.py
python -m py_compile src/types/*.py
```

## 21. Rollback strategy
Rollback type changes with all touched consumers; mixed old/new type assumptions are dangerous.

## 22. Open questions
- Should additional semantic wrappers be promoted into types to reduce cross-module string drift?

## 23. Future expansion notes
- Add a typed-contract compatibility matrix to module manifest.

## 24. Rehydration judgement
This book is the dense reference layer for types. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
