# Analysis Module Authority Book

**Recommended repo path:** `docs/reference/modules/analysis.md`
**Current code path:** `src/analysis`
**Authority status:** Reference for a derived-only zone. The module is no longer thin — eleven modules live here, including the six-class post-settlement grader (`settlement_skill_attribution.py`) that the calibration report and every skill claim rest on. This book exists to prevent accidental promotion of that analytics into decision authority: the zone reads truth and writes reports, never the reverse.

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
- `src/analysis/AGENTS.md` zone rules (derived-only: reads truth, writes reports)
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
| `settlement_skill_attribution.py` | Six-class post-settlement grader (skill/lucky win, skill/miscalibrated loss, stale decision, unattributable). The calibration report and every skill claim rest on it. |
| `regret_decomposer.py` | Decomposes realized regret against the decision-time certificate. |
| `exit_timing_attribution.py` | Attributes exit outcomes to timing versus belief change. |
| `evidence_report.py` | Evidence-ladder reporting over settled rows. |
| `epoch.py` | Epoch boundaries shared by the reports below. |
| `day0_boundary_report.py`, `event_opportunity_report.py`, `forecast_release_reaction_report.py`, `orderbook_execution_feasibility_report.py`, `settlement_guard_report.py` | Per-surface derived reports. |
| `market_analysis_vnext.py` | Offline market-analysis comparison surface. |

## 10. Relevant tests
- `tests/test_settlement_skill_attribution.py`, `tests/analysis/test_regret_decomposer.py`, `tests/analysis/test_exit_timing_attribution.py`, plus the run-mode and atlas-anchor suites that exercise these modules.
- Tests here prove the derived-only boundary as much as the arithmetic: analysis reads settled truth and emits reports, and nothing in this zone may write back into a surface it grades.

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
