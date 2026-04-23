# Supervisor API Module Authority Book

**Recommended repo path:** `docs/reference/modules/supervisor_api.md`
**Current code path:** `src/supervisor_api`
**Authority status:** Dense module reference for the outer-host typed boundary.

## 1. Module purpose
Define the narrow typed contract by which Venus/OpenClaw or any outer supervisor may read status and issue limited ingress without mutating Zeus law or inner truth by surprise.

## 2. What this module is not
- Not a general RPC surface.
- Not a way for outer hosts to rewrite control/state/authority semantics.
- Not an alternate truth surface.

## 3. Domain model
- Typed request/response contracts (O/P/C/O pattern).
- Outer-host boundary and ingress limitations.

## 4. Runtime role
Provides integration contracts for supervisors, monitors, or outer orchestration hosts while preserving Zeus's sovereignty over repo law and state truth.

## 5. Authority role
Boundary law adapter. It operationalizes the authority statement that outer hosts may observe and issue narrow ingress, but may not silently mutate Zeus authority, schema, DB truth, or control semantics.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `src/supervisor_api/contracts.py`
- `docs/authority/zeus_current_delivery.md` external boundary section
- `docs/authority/zeus_change_control_constitution.md` on anti-entropy governance

### Non-authority surfaces
- Outer-host memory or prompt injection
- Unlogged supervisor-side heuristics
- Ad hoc webhook payloads not represented in typed contracts

## 7. Public interfaces
- `contracts.py` typed boundary objects and validators

## 8. Internal seams
- Supervisor ingress vs control plane
- Supervisor status reads vs observability summaries

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `contracts.py` | Single typed surface; tiny in file count, large in boundary importance. |

## 10. Relevant tests
- tests/test_architecture_contracts.py
- tests/test_cross_module_relationships.py

## 11. Invariants
- Outer hosts may not silently mutate law, schema, DB truth, or control semantics.
- Supervisor contracts must be typed and minimal.

## 12. Negative constraints
- No implicit outer-host authority.
- No widening of command vocabulary without packeted governance.

## 13. Known failure modes
- Supervisor API becomes a backdoor control plane.
- Outer status payload is mistaken for canonical state.
- Boundary types drift from actual runtime/control semantics.

## 14. Historical failures and lessons
- [Archive evidence] Reality-crisis and authority-governance materials repeatedly show that outer orchestration easily becomes a hidden authority center unless contracts stay narrow.

## 15. Code graph high-impact nodes
- `src/supervisor_api/contracts.py` is small but boundary-critical; every import into engine/control/observability should be considered high-sensitivity.

## 16. Likely modification routes
- Any new ingress or status field requires matching control/state/docs review.

## 17. Planning-lock triggers
- Any edit under `src/supervisor_api/**`.

## 18. Common false assumptions
- Because the file is small, boundary risk is small.
- Outer hosts can carry long-lived semantics outside the repo.

## 19. Do-not-change-without-checking list
- Typed contract fields without matching consumer review

## 20. Verification commands
```bash
pytest -q tests/test_architecture_contracts.py tests/test_cross_module_relationships.py
python -m py_compile src/supervisor_api/*.py
```

## 21. Rollback strategy
Rollback boundary changes together with all callers. Never leave half-migrated typed contracts.

## 22. Open questions
- Does the current repo need a stronger supervisor ingress manifest?

## 23. Future expansion notes
- Add dedicated supervisor boundary tests if integration complexity grows.

## 24. Rehydration judgement
This book is the dense reference layer for supervisor api. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
