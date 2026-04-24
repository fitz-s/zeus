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
