# Zeus Documentation Inventory Report (2026-05-16)

## Section A — Authority doc inventory
| File | Last Mod (Git) | Lines | Status Guess | Reason |
| :--- | :--- | :--- | :--- | :--- |
| `AGENTS.md` | 2026-05-15 | 506 | CURRENT | Heavy touch in PR #119; core authority. |
| `REVIEW.md` | 2026-05-06 | 295 | NEEDS_AUDIT | Pre-dates PR #119; may miss new review protocols. |
| `.claude/CLAUDE.md` | 2026-05-07 | 16 | CURRENT | Lightweight entry point. |
| `architecture/topology.yaml` | 2026-05-15 | 11453 | CURRENT | Rebuilt in PR #119. |
| `architecture/script_manifest.yaml` | 2026-05-15 | 2243 | CURRENT | Updated with Lore/Maintenance scripts. |
| `architecture/module_manifest.yaml` | 2026-05-14 | 935 | LIKELY_STALE | Missing `maintenance_worker` and `topology_v_next`. |
| `docs/operations/AGENTS.md` | 2026-05-15 | 1102 | CURRENT | Updated with PR #119 logic. |
| `docs/operations/INDEX.md` | 2026-05-05 | 223 | LIKELY_STALE | Missing latest 15+ task packets. |
| `docs/operations/current_state.md` | 2026-05-15 | 148 | CURRENT | Fresh operational fact. |

## Section B — Operations packet inventory
- **Total Packets**: 86
- **Age Distribution**:
  - < 7 days: 56 (Active engineering burst)
  - 7-30 days: 23 (Stable/Completed)
  - 30+ days: 7 (ARCHIVE_CANDIDATES)
- **Active Law / In-Progress Flags**:
  - `task_2026-05-15_runtime_improvement_engineering_package` (STATUS: ACTIVE_LAW)
  - `task_2026-05-15_autonomous_agent_runtime_audit` (IN_PROGRESS)

## Section C — Cross-reference rot scan
- **Source**: `AGENTS.md` + `docs/operations/AGENTS.md`
- **Total file:line refs found**: ~45 (Regex-based count)
- **Spot-check (5/5 broken)**:
  - `src/state/db.py:2164` -> BROKEN (Line shifted)
  - `src/state/db.py:4455` -> BROKEN (Line shifted)
  - `src/state/collateral_ledger.py:46` -> BROKEN (Symbol exists, line wrong)
  - `src/calibration/store.py:117` -> BROKEN (Line shifted)
  - `src/contracts/execution_price.py:11` -> BROKEN (Line shifted)
- **Verdict**: Line citations are >80% stale. Symbol-only refs survive better.

## Section D — Newly-added artifacts in PR #119
- **`scripts/topology_v_next/`**: REGISTERED in `script_manifest.yaml` but NOT in `module_manifest.yaml`.
- **`maintenance_worker/`**: REGISTERED in `script_manifest.yaml` but NOT in `module_manifest.yaml`.
- **`bindings/zeus/`**: EXISTS but NO registration found in `module_manifest.yaml` or `docs_registry.yaml`.
- **Lore tooling (`lore_*.py`)**: REGISTERED in `script_manifest.yaml`.
- **Lore index**: `docs/lore/INDEX.json` MISSING (indexer needs to be run).

## Section E — Suggested triage waves
1. **Wave 1: Manifest Rehydration**: Register `maintenance_worker` and `topology_v_next` in `module_manifest.yaml`.
2. **Wave 2: Citation Repair**: Run `scripts/r3_drift_check.py` to fix file:line citations in `AGENTS.md` and `invariants.yaml`.
3. **Wave 3: Lore Indexing**: Execute `scripts/lore_indexer.py` to generate the first Lore index.
4. **Wave 4: Operations Archival**: Move >30d packets to `docs/operations/_archive/` per `ARCHIVAL_RULES.md`.

