# Stash Extracts Disposition (2026-05-16)

WAVE 0.4 of doc-alignment plan. Selective extraction of two stashes on canonical /zeus checkout.

Extracts physically live at canonical `/Users/leofitz/.openclaw/workspace-venus/zeus/state/stash_extracts/2026-05-16/` (state/ is gitignored — extracts are working state, not committed authority).

## stash@{0} — "pre-status-cherry-pick"

Created during STATUS.md cherry-pick work on deploy/live-order-e2e-verification-2026-05-15 (orchestrator session 7f255122).

**Extracted to canonical `state/stash_extracts/2026-05-16/stash0/`** (for WAVE 4 consumption):
- `dispatch.py` — `.claude/hooks/dispatch.py` improvements: PR-monitor semantic updates (reviewer-appearance-not-completion, PR-state-check, thread-aware fetch instructions)
- `dispatch.py.patch` — diff form for selective application
- `registry.yaml` — `.claude/hooks/registry.yaml` updates supporting the dispatch.py changes
- `zeus-router.mjs` — `.codex/hooks/zeus-router.mjs` mirror updates

**DROPPED (would revert merged fix 75630214e1)**:
- src/venue/polymarket_v2_adapter.py stale content
- tests/test_v2_adapter.py stale content (removed the 69 lines of pUSD regression tests)

**Status**: stash@{0} preserved in canonical stash list (NOT dropped). WAVE 4 consumes from extracts; canonical stash can be `git stash drop stash@{0}` after WAVE 4 confirms apply.

## stash@{1} — "WIP on 5e0276f629 (p5.1 REVISE)"

Created during P5.1 maintenance worker REVISE patch work on deploy branch.

**Extracted to canonical `state/stash_extracts/2026-05-16/stash1/`**:
- `AGENTS.md` — single file extraction (only file in stash)

**Status**: stash@{1} preserved. AGENTS.md extract should be diffed against current AGENTS.md to determine if substantive or already-superseded; operator decision required.

## Action items for WAVE 4

1. Apply `stash0/dispatch.py.patch` as commit on `feat/doc-alignment-2026-05-16` along with WAVE 4 hook wiring fixes.
2. Diff `stash1/AGENTS.md` vs current `AGENTS.md`. If empty/trivial → drop stash@{1}. If substantive → review with operator.
3. After WAVE 4 commits + verification: `git stash drop stash@{0}` and `git stash drop stash@{1}` in canonical.

## Provenance

Disposition recorded 2026-05-16 by orchestrator session 7f255122. wave-0-executor agent (a284ea76a2a6aa1d1) performed extraction; orchestrator wrote this DISPOSITION after worker partial-completion.
