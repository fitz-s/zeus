# docs/operations/_archive

This directory holds shipped and closed task packets from the **2026-04-26 cohort** that
were landed via PRs #20, #22, and #23 (merged to main) and are no longer actively cited
from `docs/operations/current_state.md` mainline pointers.

## Archive criteria (applied 2026-04-29)

A task dir was moved here when ALL of the following hold:

1. **Shipped**: work was completed and merged to `main` via PR #20, #22, or #23.
2. **Not cited**: `current_state.md` mainline pointers do not reference the dir.
3. **mtime cohort**: dir originates from the 2026-04-26 batch or earlier (pre-2026-04-27).

## Why `git mv` (not delete)

Full history is preserved. `git log --follow docs/operations/_archive/<dir>` shows
all commits that touched the original path. Nothing is lost; this is purely browse-noise
reduction.

## Provenance references

| PR | Title summary | Merged |
|----|---------------|--------|
| #20 | Execution state truth (P0 hardening, command bus) | 2026-04-26 |
| #22 | Live readiness completion | 2026-04-26 |
| #23 | Full data midstream fix plan | 2026-04-26 |

## Active dirs NOT archived

The following remain at `docs/operations/` root (active or still cited):

- `task_2026-04-26_ultimate_plan/` — R3 PRIMARY ACTIVE plan source
- `task_2026-04-26_polymarket_clob_v2_migration/` — active CLOB v2 supporting packet
- `task_2026-04-23_midstream_remediation/` — POST_AUDIT_HANDOFF cited from current_state.md
- `task_2026-04-23_graph_rendering_integration/` — recent, small
- `task_2026-04-27_*` and `task_2026-04-28_*` — all active
- `zeus_topology_system_deep_evaluation_package_2026-04-24/` — still cited
- `zeus_world_data_forensic_audit_package_2026-04-23/` — still cited
