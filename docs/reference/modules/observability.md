# Observability Module Authority Book

**Recommended repo path:** `docs/reference/modules/observability.md`
**Current code path:** `src/observability`
**Authority status:** Dense module reference for derived runtime summaries and health reporting.

## 1. Module purpose
Provide human/operator-facing summaries and scheduler health without pretending to be canonical truth.

## 2. What this module is not
- Not canonical DB truth.
- Not an authority layer.
- Not a replacement for state/reconciliation/risk truth.

## 3. Domain model
- Scheduler health surface.
- Status summary / operator-readable projections.

## 4. Runtime role
Reads canonical/derived runtime information and produces summaries or health signals for operators and other tooling.

## 5. Authority role
Derived-only by design. This book exists mainly to prevent observability from drifting into shadow authority.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `src/observability/status_summary.py` and `scheduler_health.py`
- `docs/authority/zeus_current_architecture.md` runtime-truth hierarchy
- `architecture/invariants.yaml` and current delivery law about derived surfaces

### Non-authority surfaces
- Any derived JSON/status output
- External dashboards or notifications

## 7. Public interfaces
- Status-summary builders
- Scheduler-health reporting helpers

## 8. Internal seams
- Health summary vs actual scheduler/runtime state
- Status summary vs canonical DB/chain state

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `status_summary.py` | Derived status/output surface; must stay downstream. |
| `scheduler_health.py` | Scheduler/job health writer/reader. |

## 10. Relevant tests
- tests/test_bug100_k1_k2_structural.py
- tests/test_architecture_contracts.py

## 11. Invariants
- Status output is derived and must not outrank DB/event truth.
- Health signals may inform operators but not silently become law.

## 12. Negative constraints
- Do not write canonical state from observability code.
- Do not hide missing canonical truth behind friendly summaries.

## 13. Known failure modes
- Operators trust status summary instead of reconciliation truth.
- Health JSON diverges from actual scheduler state and becomes a hidden bomb.

## 14. Historical failures and lessons
- [Archive evidence] Legacy truth-surface audits repeatedly found derived JSON/status files treated as if they were authoritative.

## 15. Code graph high-impact nodes
- `src/observability/status_summary.py` is a broad reader and therefore a blast-radius reader surface even if it is not a writer.

## 16. Likely modification routes
- Summary field change: verify upstream canonical readers and downstream consumers.

## 17. Planning-lock triggers
- Cross-zone observability work that changes source-of-truth assumptions or introduces new persistent surfaces.

## 18. Common false assumptions
- Status code is low-risk because it is read-only.
- A health file can stand in for canonical DB/runtime truth.

## 19. Do-not-change-without-checking list
- Any field that operators or supervisors read as safety-critical without tracing upstream truth

## 20. Verification commands
```bash
pytest -q tests/test_bug100_k1_k2_structural.py tests/test_architecture_contracts.py
python -m py_compile src/observability/*.py
```

## 21. Rollback strategy
Rollback summary/health changes together with any consumer updates.

## 22. Open questions
- Which observability outputs are currently relied on by humans as de facto authority and should be demoted in docs?

## 23. Future expansion notes
- Make observability-to-truth provenance explicit in module manifest.

## 24. Rehydration judgement
This book is the dense reference layer for observability. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
