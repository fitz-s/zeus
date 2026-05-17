# SCOUT TIER 1 Inventory — 39 per-subdir AGENTS.md

## Summary table

All paths are repo-relative (resolve from worktree root). Workers MUST edit files
in their own worktree only — never at canonical `/Users/leofitz/.openclaw/workspace-venus/zeus/`.

| path | lines | last_touched | category |
|------|-------|--------------|----------|
| config/AGENTS.md | 47 | 2026-05-02 | STALE |
| tests/AGENTS.md | 142 | 2026-05-14 | CLEAN |
| docs/AGENTS.md | 71 | 2026-05-02 | STALE |
| scripts/AGENTS.md | 70 | 2026-05-06 | STALE |
| src/AGENTS.md | 35 | 2026-04-28 | STALE |
| config/reality_contracts/AGENTS.md | 19 | 2026-04-10 | SUSPECT |
| architecture/packet_templates/AGENTS.md | 18 | 2026-04-10 | SUSPECT |
| architecture/self_check/AGENTS.md | 15 | 2026-04-24 | STALE |
| architecture/ast_rules/AGENTS.md | 16 | 2026-04-10 | SUSPECT |
| .github/workflows/AGENTS.md | 25 | 2026-04-13 | SUSPECT |
| tests/contracts/AGENTS.md | 16 | 2026-04-10 | SUSPECT |
| .agents/skills/AGENTS.md | 18 | 2026-04-30 | STALE |
| docs/artifacts/AGENTS.md | 24 | 2026-05-07 | STALE |
| docs/runbooks/AGENTS.md | 52 | 2026-05-06 | STALE |
| docs/to-do-list/AGENTS.md | 21 | 2026-05-02 | STALE |
| docs/review/AGENTS.md | 47 | 2026-05-06 | STALE |
| docs/authority/AGENTS.md | 47 | 2026-04-23 | STALE |
| docs/reference/AGENTS.md | 109 | 2026-05-15 | CLEAN |
| docs/reports/AGENTS.md | 31 | 2026-04-22 | SUSPECT |
| src/types/AGENTS.md | 35 | 2026-04-23 | STALE |
| src/analysis/AGENTS.md | 19 | 2026-04-23 | STALE |
| src/calibration/AGENTS.md | 46 | 2026-04-28 | STALE |
| src/contracts/AGENTS.md | 66 | 2026-04-28 | STALE |
| src/supervisor_api/AGENTS.md | 33 | 2026-04-23 | STALE |
| src/risk_allocator/AGENTS.md | 25 | 2026-04-28 | STALE |
| src/observability/AGENTS.md | 30 | 2026-04-23 | STALE |
| src/state/AGENTS.md | 105 | 2026-05-02 | STALE |
| src/execution/AGENTS.md | 49 | 2026-05-06 | STALE |
| src/riskguard/AGENTS.md | 46 | 2026-04-23 | STALE |
| src/venue/AGENTS.md | 37 | 2026-04-30 | STALE |
| src/data/AGENTS.md | 88 | 2026-05-02 | STALE |
| src/engine/AGENTS.md | 90 | 2026-04-24 | STALE |
| src/signal/AGENTS.md | 38 | 2026-04-23 | STALE |
| src/control/AGENTS.md | 37 | 2026-04-28 | STALE |
| src/strategy/AGENTS.md | 57 | 2026-05-06 | STALE |
| docs/operations/learning_loop_observation/AGENTS.md | 279 | 2026-04-29 | STALE |
| docs/operations/calibration_observation/AGENTS.md | 236 | 2026-04-29 | STALE |
| docs/operations/edge_observation/AGENTS.md | 74 | 2026-04-28 | STALE |
| docs/operations/ws_poll_reaction/AGENTS.md | 147 | 2026-04-29 | STALE |
| docs/operations/attribution_drift/AGENTS.md | 102 | 2026-04-28 | STALE |
| docs/reference/modules/AGENTS.md | 58 | 2026-04-28 | STALE |

## Per-category counts
- CLEAN: 2
- STALE: 32
- SUSPECT: 7

## Recommended WAVE 3 batch breakdown
Per plan v3 §5 WAVE 3: ≤4 parallel sonnet executors, 8-10 docs each.

- **Batch A (CLEAN — 2 docs, DONE)**: `tests/AGENTS.md`, `docs/reference/AGENTS.md`.
- **Batch B (SUSPECT — 7 docs)**: `config/reality_contracts/AGENTS.md`, `architecture/packet_templates/AGENTS.md`, `architecture/ast_rules/AGENTS.md`, `.github/workflows/AGENTS.md`, `tests/contracts/AGENTS.md`, `docs/reports/AGENTS.md`, `.agents/skills/AGENTS.md`.
- **Batch C (STALE first 16)**: `config/AGENTS.md`, `docs/AGENTS.md`, `scripts/AGENTS.md`, `src/AGENTS.md`, `architecture/self_check/AGENTS.md`, `docs/artifacts/AGENTS.md`, `docs/runbooks/AGENTS.md`, `docs/to-do-list/AGENTS.md`, `docs/review/AGENTS.md`, `docs/authority/AGENTS.md`, `src/types/AGENTS.md`, `src/analysis/AGENTS.md`, `src/calibration/AGENTS.md`, `src/contracts/AGENTS.md`, `src/supervisor_api/AGENTS.md`, `src/risk_allocator/AGENTS.md`.
- **Batch D (STALE second 16)**: `src/observability/AGENTS.md`, `src/state/AGENTS.md`, `src/execution/AGENTS.md`, `src/riskguard/AGENTS.md`, `src/venue/AGENTS.md`, `src/data/AGENTS.md`, `src/engine/AGENTS.md`, `src/signal/AGENTS.md`, `src/control/AGENTS.md`, `src/strategy/AGENTS.md`, `docs/operations/learning_loop_observation/AGENTS.md`, `docs/operations/calibration_observation/AGENTS.md`, `docs/operations/edge_observation/AGENTS.md`, `docs/operations/ws_poll_reaction/AGENTS.md`, `docs/operations/attribution_drift/AGENTS.md`, `docs/reference/modules/AGENTS.md`.
