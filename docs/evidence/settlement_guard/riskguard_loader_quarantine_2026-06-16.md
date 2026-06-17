# RiskGuard loader brittleness — one un-loadable row stalled ALL trading (the #122/recovery aftershock)

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: GOAL #83 (continuous settlement-graded alpha) + RULE 1 (a "no fills" symptom is
  OUR defect until settlement proves otherwise). Live incident: trader `processed=0` for ~3.5h.
- Capability touched: `src/riskguard/riskguard.py` loader resilience (T0 safety component; reversibility
  = pure read-side loader behaviour; places NO order).

## Defect (live, evidenced)

After this session's boot presence-resolution recovered the orphaned Houston maker fill (5.07 NO @ 0.64),
a **dual-id DUPLICATE** position row appeared: `aef7968f-6f3` (UUID = the venue trade id) alongside the
canonical `edli6301b2b1…` for the SAME on-chain order `0x5ce1f9da…`. The duplicate is `fill_authority=
venue_confirmed_full` (fill-grade) but carries NO `execution_fact` provenance the RiskGuard fill-grade
loader requires (its execution_fact row exists by `position_id` but is not a terminal `filled`/`entry`
row, so `_query_entry_execution_fill_hints` doesn't surface it → `entry_economics_source` falls back to
`position_current_projection`).

`_load_riskguard_portfolio_truth` (riskguard.py:178) iterated the loader view; on the FIRST un-loadable
row it logged "Quarantining…" then **RE-RAISED** `RuntimeError("RiskGuard DB loader fault")` (the B052
comment said "quarantine" but the code crashed the loop). One bad row therefore failed EVERY tick:

```
ERROR: RiskGuard DB loader fault: fill-grade loader row missing execution_fact source provenance
ERROR: RiskGuard tick failed: …
```

→ RiskGuard produced no fresh check → the trader read `RiskGuard STALE: last check 3490–5423s ago →
Fail-closed RED` → `RISK_GUARD_BLOCKED ×5375` → `processed=0` → **zero crosses since ~04:41** (the spine
had crossed 6 buy_no orders 01:14–04:41 while RiskGuard was fresh). A single un-loadable duplicate row
disabled the entire risk system and thus all trading.

## Change (one-line root fix)

`src/riskguard/riskguard.py` — the loader now **quarantines-and-continues** (the B052 comment's own stated
intent) instead of re-raising: an un-loadable canonical row is excluded from the risk view, logged at
ERROR, and counted+exposed (`quarantined_count` / `quarantined_rows` in the returned truth dict). Disabling
the whole risk system over one bad row is strictly WORSE for risk than excluding that row. "Avoid silent
masking" is preserved by a LOUD, COUNTED, EXPOSED quarantine — not by crashing the tick. The B053
consistency lock and its log now account for quarantined rows (a KNOWN exclusion is not drift). The
trigger row here is a duplicate whose on-chain exposure is already accounted via the canonical position,
so excluding it neither double- nor under-counts.

## Reversibility / safety

Pure read-side loader behaviour; places no order, mutates no canonical truth. Worst case it excludes a
genuinely-missing LIVE position from the risk view — but that is loudly logged + counted, and is strictly
safer than the prior behaviour (whole-tick crash → RED → no trading at all). `git revert` restores the
re-raise.

## Verification

- `tests/test_riskguard.py::…::test_loader_quarantines_unloadable_row_instead_of_failing_whole_tick`
  PASSES with the fix; RED-on-revert proven (restoring the `raise` → `_load_riskguard_portfolio_truth`
  raises → test fails).
- Money-path + riskguard suites: ZERO new failures (the 7 `TrailingLossSemantics` + 4
  `test_finding_b_free_cash_bound` failures are pre-existing — verified identical at baseline).
- Live: restarted `com.zeus.riskguard-live` (PID 80851→70289). Last `RiskGuard tick failed` = 08:09:27
  (pre-restart); ZERO after the 08:10:19 restart. Status GREEN; trader `RiskGuard STALE` = 0;
  `processed` 0→1 (monitor: `TRADER_UNBLOCKED: processed=1`). Trading flowing again.

## Follow-up (not blocking; the loader-resilience is the durable safety net)

The recovery path (presence-resolver / fill-bridge) created a dual-id DUPLICATE position with an
incomplete `execution_fact` row. The durable prevention is to (a) dedup the recovered fill into the
canonical `edli…` position rather than minting a second UUID-keyed row, and (b) write a complete terminal
`filled`/`entry` execution_fact for a recovered fill so its provenance is loader-visible. Tracked separate
from this safety-net fix.
