# PR332 Arendt Codex Critic Review

Date: 2026-05-24
Reviewed head: `a2a03a82390ea2c5a0ed4aec26ffba08d932e02c`
Reviewer: Codex subagent `019e5b75-24e9-7f50-8bdd-6775edaa06af` (Arendt)
Scope: strict EDLI v1 no-submit money-path semantics, no file edits.

## Verdict: NO-GO

PR332 no longer has the original `run_cycle` / `submit_existing_cycle_for_event`
EDLI wiring, and the main no-submit side-effect boundaries are materially
improved. But the review found two P0 money-path blockers in the repaired
implementation: EDLI can size Kelly against the wrong selected-token executable
price, and the FDR denominator can still collapse to the fresh-snapshot subset
rather than the full sibling family.

## P0 Blockers

### P0-1 Kelly can use the wrong native ask for the selected token

In `src/engine/event_reactor_adapter.py`, `_selected_snapshot_row_for_event()`
accepted any row where the requested `token_id` was either the row's YES or NO
token. It did not require `selected_outcome_token_id == token_id`. Because
`_latest_snapshot_rows_for_event_family()` deduped by `condition_id`, the
selected side could be dropped. Then `_execution_price_from_snapshot()` used
that row's ask.

Reproduction reported by critic:

```text
{'submitted': True, 'direction': 'buy_no', 'token_id': 'no-1', 'snapshot': 'yes-snap', 'c_fee_adjusted': 0.1045, 'kelly_pass': True}
```

Required fix:

```text
selected snapshot binding must require selected_outcome_token_id == selected_token_id
and outcome_label consistent with direction. Do not dedupe away side-specific
snapshot rows before selected-price resolution.
```

### P0-2 FDR denominator can still collapse to snapshot-covered siblings

`build_event_bound_no_submit_receipt()` derived the family universe from
`_latest_snapshot_rows_for_event_family()`, then built FDR hypotheses only from
those rows' YES/NO tokens. Missing, stale, or uncaptured sibling snapshots could
therefore shrink the denominator.

Reproduction reported by critic:

```text
{'submitted': True, 'hypotheses': 2, 'family_complete': True}
```

Required fix:

```text
Build the FDR denominator from canonical full sibling topology, then require
executable proof for the selected live hypothesis. If the denominator cannot be
proven complete, reject with FDR_FULL_FAMILY_PROOF_MISSING / FAMILY_INCOMPLETE.
```

## P1 Blockers

### P1-1 Market topology binding likely rejects real scanner rows

`_event_family_snapshot_binding()` required `COALESCE(mev.outcome, '') = ''`,
but `persist_market_events_v2()` writes `outcome = range_label`. Existing tests
used synthetic `outcome=''`, so they did not prove production scanner
compatibility.

### P1-2 Tests still over-prove by strings and synthetic fixtures

Some tests check source strings or simplified in-memory schemas rather than the
real scanner/snapshot relationship. Missing tests:

```text
- Same-condition YES/NO snapshot test: event selects NO, freshest row is YES,
  correct behavior is reject or select NO row only.
- Full-family denominator test: market_events_v2 has more sibling conditions
  than fresh snapshots; no-submit receipt must reject, not pass with reduced
  denominator.
- Production topology-shape test: use scanner-like market_events_v2 rows where
  outcome=range_label, not synthetic outcome=''.
- Relationship test from scanner snapshot capture to EDLI receipt: selected
  token, selected snapshot, execution price, FDR denominator, and receipt token
  must all agree.
```

## Confirmed Passes

```text
- EDLI runtime no longer calls run_cycle / submit_existing_cycle_for_event.
- Proof receipt now flows through EventBoundFinalIntentReceipt.
- Full market discovery is restored with find_weather_markets(... include_slug_pattern=True).
- Fresh-at-submit recapture is present.
- No-submit live-cap reservation is gated behind real submit and command/submitted status.
- Public market-channel events are rejected as direct trade candidates and fill truth is guarded.
```

## Verification Reported By Critic

```text
- 37 passed: EDLI no-bypass, FDR/Kelly adapters, no-submit, online invariants,
  market-channel guard, executable cost.
- 9 passed: exec freshness recapture plus no-submit live-cap tests.
- Full tests/events/test_market_channel_ingestor.py could not complete in the
  critic environment because importing src.main required missing local dependency apscheduler.
```
