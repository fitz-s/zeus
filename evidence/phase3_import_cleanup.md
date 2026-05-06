# Phase 3 Import-Chain Cleanup — Phase 4.B Executor Record

**Date:** 2026-05-06
**Authority basis:** IMPLEMENTATION_PLAN §6 Phase 4.B B-3; phase3_h_decision.md §"Phase 4 mandatory conditions"

## Finding

Phase 4.B pre-investigation showed 44 collection errors with `.venv/bin/python3 -m pytest --collect-only`.
The brief described "113 import-chain failures" (pre-existing Phase 3 debt from deleted modules
`topology_doctor_digest.py`, `_context_pack.py`, `_core_map.py`). Actual investigation revealed the
collection errors were caused by three missing packages in the project venv, not deleted-module importers:

```
ModuleNotFoundError: No module named 'apscheduler'   — scheduler-related tests
ModuleNotFoundError: No module named 'eccodes'        — ECMWF/TIGGE extractor tests
ModuleNotFoundError: No module named 'sklearn'        — calibration/platt tests
```

The `eccodes` package was missing from `.venv/` (the other two were absent from the system Python,
not the venv). Installing `eccodes` into `.venv/` resolved all 44 collection errors.

## Disposition per file

| File / module | Error | Disposition | Rationale |
|---|---|---|---|
| `tests/test_phase4_5_extractor.py` | `ModuleNotFoundError: No module named 'eccodes'` | **RESOLVED** (install eccodes) | Package dependency, not deleted-module import |
| `tests/test_phase4_6_cities_drift.py` | `ModuleNotFoundError: No module named 'eccodes'` (transitive) | **RESOLVED** (install eccodes) | Same |
| All other collection errors | `sklearn`, `apscheduler` | Pre-existing — present only with system Python (not venv), not triggered by `.venv/bin/python3` | Not Phase 3 import debt |

## Post-cleanup state

```
.venv/bin/python3 -m pytest --collect-only -q 2>&1 | tail -3
5625/5641 tests collected (16 deselected) in 3.14s   ← zero collection errors
```

## Topology_doctor deleted-module importers

A search for `topology_doctor_digest`, `_context_pack`, `_core_map`, `packet_prefill` in test files
found **zero** stranded importers. These deletions either happened cleanly or the affected tests were
already removed in Phase 3. No test files required disposition (a), (b), or (c).

## Full-suite baseline (post-cleanup)

Full suite with `.venv/bin/python3`: `320 failed, 5128 passed, 158 skipped` — this is the
pre-existing domain-logic failure baseline, unchanged by Phase 4.B gate authoring.
Gate tests add 15 new passed tests.

**Signed: phase4_b_executor (2026-05-06)**
