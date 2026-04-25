# Validation Matrix

| Area | Deterministic command | Live/global command | Expected |
|---|---|---|---|
| Python compile | `python3 -m py_compile scripts/topology_doctor.py scripts/topology_doctor_*.py` | same | no compile errors |
| Navigation P0 | targeted pytest for navigation fixtures | `python3 scripts/topology_doctor.py --navigation --task "x" --files <files> --json` | route digest exists; direct blockers separate |
| Closeout P0 | targeted pytest for closeout fixtures | `python3 scripts/topology_doctor.py closeout --changed-files <files> --summary-only` | changed-file blockers only |
| Issue model P1 | issue JSON compatibility tests | navigation/closeout JSON | old keys preserved; new metadata present |
| Docs/module P2 | docs/module fixture tests | `python3 scripts/topology_doctor.py --docs --json` | new books registered; no default-read archive leak |
| Ownership P3 | manifest ownership fixture tests | strict/ownership lane | duplicate/conflict owners detected |
| Graph P4 | graph sqlite fixture tests | graph status/context pack | derived limitations emitted; stale advisory |
| Test split P5 | `pytest -q tests/test_topology_doctor.py -m "not live_topology"` | `pytest -q tests/test_topology_doctor.py -m live_topology` | deterministic stable; live debt visible |

## 2026-04-25 execution evidence

| Phase | Command | Result | Notes |
|---|---|---|---|
| P0 | `python3 -m pytest -q tests/test_topology_doctor.py -k "navigation or closeout or strict_health"` | PASS (`19 passed`) | Closeout without work record/receipt correctly fails evidence gates. |
| P1 | `python3 -m pytest -q tests/test_topology_doctor.py -k "issue_ or blocking_modes or renderer or navigation or closeout"` | PASS (`26 passed`) | Legacy v1 issue JSON omits typed fields; v2 emits typed metadata. |
| P2 | `python3 -m pytest -q tests/test_topology_doctor.py -k "system_book or module_manifest or module_books or progress_handoff"` | PASS (`5 passed`) | Docs lane still has pre-existing drift, but no changed-scope P2 issues. |
| P3 | `python3 -m pytest -q tests/test_topology_doctor.py -k "ownership or manifest or maturity or system_book"` | PASS (`14 passed`) | `--ownership --json` passed with no issues. |
| P4 | `python3 -m pytest -q tests/test_topology_doctor.py -k "graph_appendix or context_pack_includes_graph_appendix or context_pack_handles_missing_graph_db"` | PASS (`6 passed`) | Graph status/context-pack emit `derived_not_authority` appendix; graph health has pre-existing drift. |
| P5 | `python3 -m pytest -q tests/test_topology_doctor.py -m "not live_topology"` twice | PASS (`226 passed, 16 deselected` twice) | `-m live_topology` runs and visibly fails 16 live/current-state tests. |
