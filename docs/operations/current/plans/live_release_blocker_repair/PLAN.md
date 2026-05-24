# Live Release Blocker Repair Plan

Date: 2026-05-24
Status: implementing
Parent objective: `docs/operations/current/GOAL.md` (`current_live_recovery`)

## Target Result

Repair the three current-main live-release blockers surfaced by the PR-map review:

1. Forecast source truth must fail closed when `source_run.source_cycle_time` is missing or unparseable.
2. Submit-time executable recapture must not treat reconstructed stale snapshot flags as current tradability authority.
3. Standard CTF redemption confirmation must have economic payout proof comparable to the negRisk path, or fail to review.

## Constraints

- Preserve existing DB split, venue truth, lifecycle, and settlement semantics.
- Keep edits scoped to source/test files directly needed for the three blockers.
- Do not touch unrelated dirty worktree files.
- No live venue side effects; validation is local/static/pytest only.

## Repair Slices

### Slice 1: Forecast Source Cycle Time

Files expected:
- `src/data/executable_forecast_reader.py`
- `tests/test_executable_forecast_reader.py`

Required behavior:
- Parse `source_cycle_time` once.
- Return `SOURCE_CYCLE_TIME_UNPARSEABLE` when missing/unparseable.
- Preserve distinct valid source cycle time even when it differs from decision time.

### Slice 2: Submit-Time Recapture Tradability

Files expected:
- `src/engine/cycle_runtime.py`
- `src/data/market_scanner.py`
- `tests/test_market_scanner_provenance.py`

Required behavior:
- Reconstructed stale snapshot identity must not fabricate current `accepting_orders` / `enable_orderbook` truth.
- Reconstructed recapture must require explicit current authority for tradability.
- Missing CLOB archived/orderbook fields in reconstructed mode must fail closed.

### Slice 3: Standard CTF Redemption Proof

Files expected:
- `src/execution/settlement_commands.py`
- `tests/test_settlement_commands.py`

Required behavior:
- Standard CTF receipt success without matching payout proof must transition to review, not confirmed.
- Matching condition id and nonzero payout are required before confirmation.
- Existing negRisk proof behavior remains intact.

## Verification

Run topology navigation/planning checks for changed files, targeted pytest for each slice, and cheap static checks (`git diff --check`; `py_compile` for touched Python files if targeted tests do not already import them).

Stop when all three blockers have code/tests and local targeted verification evidence, or when topology blocks implementation with no admitted safe path.
