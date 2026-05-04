# PR-A: Live-block antibodies (PR-A + SF6 + SF7) — 2026-05-04

## Summary

Three composable antibodies for the 16-day live-block. Discovered iteratively through test-driven probing rather than upfront design — the original ROOT_CAUSE hypothesis (single ValueError loop) was incomplete; deeper iteration revealed **6 stacked structural failures**, three of which are addressed in this PR.

| Antibody | Commit | Files | Lines | Antibody-for SF |
|---|---|---|---|---|
| **PR-A**: `exc_info=True` | `bc429393` | 3 src files, 12 sites | +12/-6 | SF1 (logger.error swallows traceback) |
| **SF6**: `pause_entries` write-time logger | `8c1c03f1` | `control_plane.py:266-273` | +11/-1 | SF6 (silent DB pause writes) |
| **SF7**: RiskGuard SQL filter | `19fe3c89` | `riskguard.py:239-262` | +14/+0 | SF7 (schema-evolution false positive in trustworthiness scan) |
| **Repro test** | `e656fea5` | `scripts/repro_antibodies.py` + `D1_TRACEBACK.md` | +550/-0 | end-to-end verification |

## The discovery story

### What ROOT_CAUSE.md initially said
- 16-day daemon lock from "auto_pause:ValueError" loop in `state/zeus-world.db`
- 5 stacked failures (SF1-SF5)
- Plan: add `exc_info=True` (PR-A) → surface real D1 traceback → fix D1

### What testing actually revealed
1. PR-A's 12 `exc_info=True` edits deployed cleanly. **Never triggered** in 5 cycles of testing — current daemon does NOT raise the original ValueError.
2. Acceptance gate failure: `Cycle opening_hunt: 0 candidates` AND zero traceback AND zero `Entry path raised` log line, but DB acquired a NEW `auto_pause:ValueError` row at 03:40:38 UTC from a process that left zero observability behind.
3. Discovery → **SF6**: `pause_entries()` is silent on successful DB writes. Only DB-write FAILURES emit `logger.error`. Successful pauses leave no stderr footprint. This was the real "16-day blind spot" — not the missing exc_info.
4. SF6 fix added (`logger.warning` with caller stack via `traceback.format_stack()[-6:-1]`). Verified end-to-end via `scripts/repro_antibodies.py`.
5. After SF6 + PR-A deployed and tested, root blocker became `risk_level=DATA_DEGRADED` from RiskGuard. Direct REPL probe of `_trailing_loss_reference` revealed **SF7**: top-100 LIMIT query was dominated by rows from a writer-regression window (May 3 02:46-04:25 UTC) lacking the top-level `bankroll_truth_source` field — these crowded out 918 valid post-cutover rows further back.
6. SF7 fix: pre-filter the SQL query to `bankroll_truth_source = 'polymarket_wallet'`. Preserves architectural intent ("rows-disagree gate") while handling schema evolution. Verified: status changes from `inconsistent_history` → `stale_reference` → bootstrap GREEN.

## Test plan

### Antibody verification (repro_antibodies.py)
```bash
.venv/bin/python scripts/repro_antibodies.py
```
Expected (verified PASS):
- `Evaluation failed for Amsterdam 2026-05-06: test injection — antibody verification` + multi-line `Traceback`
- `ENTRIES_AUTO_PAUSED_DB_WRITTEN reason=auto_pause:ValueError_TEST issued_by=system_auto_pause` + 5 caller stack lines
- DB row in `control_overrides_history` with `issued_by=system_auto_pause, reason=auto_pause:ValueError_TEST`

### Live verification (post-merge)
```bash
launchctl kickstart -k gui/501/com.zeus.live-trading
launchctl kickstart -k gui/501/com.zeus.riskguard-live
```
Expected within 1-2 min:
- RiskGuard tick: `Tick complete: GREEN` (was DATA_DEGRADED)
- Cycle JSON: `'risk_level': 'GREEN'` (was DATA_DEGRADED)
- Cycle JSON: candidates > 0 (was 0 for 16 days)

## Out of scope (deferred to separate PRs)

- **D1**: original ValueError file:line — could not be reproduced in 5 test cycles. SF6 antibody will catch it next time. If needed, add a `ZEUS_TEST_INJECT_VALUEERROR` env var pattern.
- **22:40:38 mystery row** — silent DB writer producing system_auto_pause issuer with zero log trace. SF6 will catch on recurrence.
- **PR-B** (5 structural antibodies for SF1-SF5 from original ROOT_CAUSE): owner-tagged tombstone JSON, DB-first `is_entries_paused`, AST exc_info gate, history cleanup, INV registry. Scheduled for next week post 7-day observation window.
- **`initial_bankroll=0` reference rows**: trustworthiness predicate accepts them but their math is degenerate. Operator decision whether to add `> 0` requirement.

## Risk

- SF7 SQL filter is the most behavior-changing piece. Trade-off: rows lacking `bankroll_truth_source` top-level field are now SKIPPED rather than counted as "inconsistent". This means schema-incomplete rows can no longer trigger DATA_DEGRADED on their own. Genuine post-cutover disagreement (initial != effective among polymarket_wallet rows) still fires `inconsistent_history`. Architectural comment at `riskguard.py:302-304` intent preserved.
- SF6 `traceback.format_stack()` adds ~5 stack frames to log volume on every successful pause. Pause is rare (was ~1/75min in worst case); volume is acceptable.
- PR-A logger fix is additive (adds keyword arg to existing logger calls). No behavior change beyond noisier ERROR logs.

## Authority

- `docs/operations/task_2026-05-04_live_block_root_cause/ROOT_CAUSE.md` — original 5-SF analysis + empirical evidence
- `docs/operations/task_2026-05-04_live_block_root_cause/FIX_PLAN.md` v2 — post-critic plan for PR-A
- `docs/operations/task_2026-05-04_live_block_root_cause/A2_AUDIT.md` — 12 silent-logger sites
- `docs/operations/task_2026-05-04_live_block_root_cause/D1_TRACEBACK.md` — acceptance gate outcome
- `docs/operations/task_2026-05-04_live_block_root_cause/PR_A_BODY.md` — this document

🤖 Generated with [Claude Code](https://claude.com/claude-code)
