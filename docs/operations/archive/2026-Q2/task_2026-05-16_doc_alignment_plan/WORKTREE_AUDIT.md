# Worktree + Branch + Orphan Audit (2026-05-16)

Read-only audit of git fragmentation across the zeus repo. Result of round-3 scout dispatch (agent a9b84ff49d1323b1f). Multiple worktrees + branches + stashes from automated agent sessions in 2026-05-15.

## 1. Worktree Inventory + Triage

| Path | Branch | HEAD Commit | Age | Status | Disposition |
|------|--------|-------------|-----|--------|-------------|
| `/zeus` | `deploy/live-order-e2e-verification-2026-05-15` | `d0915a8b02` (post-merge) | 14h | Ahead of origin/deploy by 0 | **KEEP (canonical live)** |
| `/zeus-main-live...` | `(detached)` | `6be2f27b1a` | 6h | Detached HEAD | **AUTO_REMOVE** |
| `/zeus-live-order-goal-2026-05-15` | `feat/live-order-e2e-goal-2026-05-15` | `ad81a3e002` | 12h | Dirty (modified scripts) | **KEEP_LOCKED** (other-session WIP) |
| `/zeus-data-daemon-authority-chain-2026-05-14` | `feat/data-daemon-authority-chain-2026-05-14` | `8813c45370` | 24h | Clean (branch merged) | **STALE_DISCARD** |
| `/zeus-live-order-e2e-verification-2026-05-15` | `feat/live-order-e2e-verification-2026-05-15` | `e86a80e316` | 14h | Dirty | **KEEP_LOCKED** (other-session WIP) |
| `.claude/worktrees/agent-*` (16 total) | `worktree-agent-*` | various | 12-20h | All marked `locked` | **STALE_DISCARD** (per-path verify before remove) |
| `.claude/worktrees/status-md-refresh` | `worktree-status-md-refresh` | `45717e87cd` | 10h | Active (cherry-pick source) | **AUTO_REMOVE** (cherry-picked + pushed already) |

## 2. Local Branches (`git branch -vv`)

**Merged into main (safe to delete after worktree removal)**:
- `worktree-agent-a22208b47f876fb48` (pUSD fix worker; commit 75630214e1 in main)
- `worktree-agent-aee607f7328e119d3`

**Unmerged WIP (potential orphans — operator decision required before delete)**:
- `feat/data-daemon-authority-chain-2026-05-14` (last activity: 2026-05-15 00:20; check if work was carried into another branch)
- `feat/live-order-e2e-goal-2026-05-15` (last activity: 2026-05-15 23:54; **ACTIVE OTHER SESSION** — do NOT delete)
- `chore/post-k1-cleanup-2026-05-15` (status uncertain; verify with `git log --oneline main..chore/post-k1-cleanup-2026-05-15`)
- `fix/calibration-tigge-opendata-bridge-2026-05-11` (STALE — 5 days old; if zero unique commits, safe delete)

## 3. Stash Inventory

| ID | Created | Branch context | Files | Direction | Disposition |
|----|---------|----------------|-------|-----------|-------------|
| `stash@{0}` | 2026-05-15 | deploy/live-order... ("pre-status-cherry-pick") | `.claude/hooks/dispatch.py`, `.claude/hooks/registry.yaml`, `.codex/hooks/zeus-router.mjs`, `src/venue/polymarket_v2_adapter.py`, `tests/test_v2_adapter.py`, `scripts/topology_v_next/*` (15+ files) | MIXED (hook portions NEWER substantive; polymarket portions OLDER → would revert merged fix) | **PRESERVE SELECTIVELY** — apply hooks portions in WAVE 4; discard polymarket portions |
| `stash@{1}` | 2026-05-15 | deploy/live-order... (WIP on commit 5e0276f629 "p5.1 REVISE patch") | scope per-file inspection required | unknown | **INVESTIGATE** — git stash show -p before disposition |

## 4. Main Checkout Uncommitted Changes (`/zeus` on `deploy/...`)

268 lines pending across 7 files.

| File | Lines diff | Origin/Intent | Disposition |
|------|------------|---------------|-------------|
| `src/state/chain_reconciliation.py` | +72 | live-order-e2e investigation (Decimal-based positive check + `_pending_entry_has_durable_command` helper for venue_commands state lookup) | **KEEP_WIP** (other session owns) |
| `tests/test_live_safety_invariants.py` | +134 | live-order-e2e: `_seed_acked_entry_command` test helper, audit date 2026-05-16 | **KEEP_WIP** (other session owns) |
| `.claude/hooks/dispatch.py` | +20 | PR-monitor semantic improvements (reviewer-appearance-not-completion) | **EXTRACT → apply in WAVE 4** |
| `.claude/hooks/registry.yaml` | +6 | hook registry updates | **EXTRACT → apply in WAVE 4** |
| `.codex/hooks/zeus-router.mjs` | +10 | Codex mirror updates | **EXTRACT → apply in WAVE 4** |
| `docs/operations/task_2026-05-15_live_order_e2e_goal/LIVE_ORDER_E2E_GOAL_PLAN.md` | +21 | scope expansion documenting real accepted-order evidence | **KEEP_WIP** (other session owns) |
| `docs/operations/task_2026-05-06_hook_redesign/PLAN.md` | small | hook redesign notes | **KEEP_WIP** |

## 5. Top 5 Cleanup Priorities

1. **Verify-then-remove 16 agent-* worktrees**: Per-path `git status --short` check before `git worktree remove --force`. Skip any with uncommitted state or unique commits. Reclaims GB of disk.
2. **Consolidate stash@{0} hook portions**: Apply hooks improvements (dispatch.py, registry.yaml, zeus-router.mjs) to `feat/doc-alignment-2026-05-16` WAVE 4. Discard polymarket portions (would revert merged fix `75630214e1`).
3. **Investigate stash@{1}**: `git stash show -p stash@{1}` to determine substance + direction; preserve or drop per finding.
4. **Terminate `/zeus-data-daemon-authority-chain-2026-05-14`**: clean checkout, branch merged → `git worktree remove` + `git branch -D`.
5. **Reconcile other-session uncommitted WIP on canonical /zeus**: do NOT touch the 4 files marked KEEP_WIP. They belong to the live-order-e2e investigation chain.

## Provenance

Worktree audit dispatched 2026-05-16 by orchestrator-session 7f255122. Source agent ID: a9b84ff49d1323b1f. Original BATCH_DONE return contained 21 worktrees + 18 branches + 2 stashes; this artifact captures the triage table for plan execution reference.
