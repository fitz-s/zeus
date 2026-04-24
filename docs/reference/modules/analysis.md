# Analysis Module Authority Book

**Recommended repo path:** `docs/reference/modules/analysis.md`
**Current code path:** `src/analysis`
**Authority status:** Dense reference for an intentionally thin/placeholder module. This book exists mainly to prevent accidental promotion of ad hoc analytics into authority.

## 1. Module purpose
Document that `src/analysis` is currently minimal and must not become an ungoverned catch-all.

## 2. What this module is not
- Not a hidden strategy lab.
- Not a place to store current facts, ad hoc experiments, or packet residue.
- Not a law surface.

## 3. Domain model
- At present, repo reality shows little or no durable analysis code here.

## 4. Runtime role
Minimal or none at present.

## 5. Authority role
The main rule is containment: keep analysis derived, explicit, and demotable unless it graduates into a real module with tests/manifests.

## 6. Read/write surfaces and canonical truth
### Canonical truth surfaces
- `src/analysis/AGENTS.md` current placeholder status
- `docs/authority/zeus_current_delivery.md` authority hygiene and packet doctrine

### Non-authority surfaces
- Any ad hoc notebook/result dumped into src/analysis without lifecycle tags

## 7. Public interfaces
- None stable enough to treat as a public API today

## 8. Internal seams
- N/A until durable code exists

## 9. Source files and their roles
| File / surface | Role |
|---|---|
| `(directory currently sparse)` | Treat as placeholder unless durable code lands with packet, tests, and manifest registration. |

## 10. Relevant tests
- No dedicated durable tests are clearly surfaced today; this is itself evidence that analysis should stay non-authority until promoted deliberately.

## 11. Invariants
- Analysis must remain derived and non-canonical unless explicitly promoted.

## 12. Negative constraints
- Do not let this directory become a junk drawer for unclassified logic.

## 13. Known failure modes
- Ad hoc analytics quietly become relied on without tests or manifests.

## 14. Historical failures and lessons
- [Archive evidence] Many historical packets and scratch artifacts demonstrate that unsupported analysis content becomes noise unless given a clear lifecycle.

## 15. Code graph high-impact nodes
- No confirmed high-impact nodes; this is a low-density surface.

## 16. Likely modification routes
- If durable code lands here, create source_rationale/test_topology/module-manifest entries in the same packet.

## 17. Planning-lock triggers
- Any proposal to make analysis durable or authority-bearing.

## 18. Common false assumptions
- Because analysis is low-risk, it can remain unregistered.

## 19. Do-not-change-without-checking list
- N/A — the real rule is do not promote silently

## 20. Verification commands
```bash
python scripts/topology_doctor.py --planning-lock --changed-files <files> --plan-evidence <packet-plan> --json (when promoting code here)
```

## 21. Rollback strategy
Prefer deletion or demotion of accidental analysis code rather than half-supporting it.

## 22. Open questions
- Is analysis meant to stay empty, or should some durable replay/research logic graduate here later?

## 23. Future expansion notes
- If analysis becomes real, split into durable subpackages with tests and a dedicated module book.

## 24. Rehydration judgement
This book is the dense reference layer for analysis. Keep its launcher surface and `architecture/module_manifest.yaml` entry aligned, and do not promote it into authority or packet status.
