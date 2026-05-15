# Hidden Branch Inventory

## Packet Inventory
| Directory Name | Created Date | README/PLAN | Status (Grep) | Git Mod Count | Last Commit Date | Current Status (PLAN.md) |
|---|---|---|---|---|---|---|
| docs/operations/task_2026-05-08_topology_redesign_completion | 2026-05-08 | PLAN.md | - | 1 | 2026-05-08 08:57:10 | - |
| docs/operations/task_2026-05-07_hook_redesign_v2 | 2026-05-07 | PLAN.md | - | 1 | 2026-05-07 08:55:44 | - |
| docs/operations/task_2026-05-05_topology_noise_repair | 2026-05-05 | PLAN.md | - | 1 | 2026-05-06 06:04:26 | - |
| docs/operations/task_2026-05-06_topology_redesign | 2026-05-06 | PLAN.md | Status: DESIGN PROPOSAL | 1 | 2026-05-07 02:18:30 | Status: DESIGN PROPOSAL, NOT CURRENT LAW |
| docs/operations/task_2026-05-09_post_s4_residuals_topology | 2026-05-09 | PLAN.md | - | 1 | 2026-05-09 10:41:39 | - |
| docs/operations/task_2026-05-07_navigation_topology_v2 | 2026-05-07 | PLAN.md | - | 1 | 2026-05-07 06:40:09 | - |
| docs/operations/task_2026-05-06_hook_redesign | 2026-05-06 | PLAN.md | - | 1 | 2026-05-07 02:18:30 | - |

## Topology source files inventory
| File Path | File Size (Bytes) | Last Commit Date |
|---|---|---|
| scripts/topology_doctor.py | 111760 | 2026-05-14 12:43:41 |
| scripts/topology_doctor_receipt_checks.py | 9378 | 2026-04-16 04:22:44 |
| scripts/topology_doctor_reference_checks.py | 14778 | 2026-04-15 02:53:00 |
| scripts/topology_doctor_artifact_checks.py | 7982 | 2026-04-15 02:48:05 |
| scripts/topology_doctor_ownership_checks.py | 10376 | 2026-05-08 08:57:10 |
| scripts/topology_doctor_freshness_checks.py | 6499 | 2026-04-16 20:19:21 |
| scripts/topology_doctor_digest.py | 78132 | 2026-05-10 02:32:59 |
| scripts/topology_doctor_script_checks.py | 14284 | 2026-05-07 06:40:09 |
| scripts/topology_doctor_closeout.py | 19964 | 2026-05-08 08:57:10 |
| scripts/topology_doctor_docs_checks.py | 32681 | 2026-05-07 06:40:09 |
| scripts/topology_doctor_map_maintenance.py | 6122 | 2026-05-09 11:19:35 |
| scripts/topology_doctor_policy_checks.py | 39037 | 2026-05-07 06:40:09 |
| scripts/topology_doctor_registry_checks.py | 18485 | 2026-05-07 02:18:30 |
| scripts/topology_doctor_source_checks.py | 8775 | 2026-04-24 13:16:57 |
| scripts/topology_doctor_code_review_graph.py | 28251 | 2026-04-29 08:13:28 |
| scripts/topology_doctor_data_rebuild_checks.py | 6467 | 2026-04-16 04:50:45 |
| scripts/topology_doctor_cli.py | 30550 | 2026-05-10 00:25:28 |
| scripts/topology_doctor_test_checks.py | 8224 | 2026-05-07 06:40:09 |

## Live UNION_SCOPE_EXPANSION incident (2026-05-15)

The verification command for THIS package's PLAN.md
(`topology_doctor.py --navigation --files PLAN.md docs/operations/AGENTS.md`)
returns `admission: scope_expansion_required` with admitted=1, oos=1
(rejecting `docs/operations/AGENTS.md` as out-of-scope) under the canonical
"operation planning packet" task phrase. Removing `docs/operations/AGENTS.md`
from the call produces `admission: admitted` with admitted=1.

This is a live in-the-wild reproduction of the `UNION_SCOPE_EXPANSION`
friction pattern: a coherent docs-only change spanning the new packet AND
the operations doctrine doc cannot be admitted as one unit. The friction
forces either (a) two separate admission calls (slicing pressure), or
(b) re-phrasing to a different profile that covers AGENTS.md (phrasing
game). Both bypass the design intent of the planning gate.

Should appear as row 2 in `TOPOLOGY_FRICTION_INCIDENTS.md` (sibling audit
packet, after the "SDK method compatibility" incident which is row 1).
