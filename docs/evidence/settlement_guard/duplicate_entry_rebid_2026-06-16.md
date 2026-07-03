# Duplicate concurrent resting ENTRY orders on the same family — root cause + fix

```
Created:       2026-06-16
Last audited:  2026-06-16
Authority basis: task #125 live-order-state duplicate lock (FIX A);
                 Tier-0 live-money defect, branch live/iteration-2026-06-13
```

## Summary

The spine placed **two (in fact three) resting ENTRY orders on the same family,
same token, same direction, same price (0.570)** on
`2026-06-16T23:39 / :45 / :48 UTC` (18:39 / 18:45 / 18:48 local), giving ~2x
intended exposure. The #125 duplicate-suppression lock
(`_locked_live_opportunity_active_order_reason`) **released on every successfully
submitted resting order**, because it misclassified `CapTransitioned(to_status='CONSUMED')`
as a TERMINAL (order-done) lifecycle event. `CONSUMED` is not "filled" — it is
the cap-commit emitted the instant a submit succeeds and the order rests **live,
unfilled**, on the venue. So the first order's own `CONSUMED` row released the
lock for the second event, and so on.

The fix is one SQL change: drop `'CONSUMED'` from the terminal cap-status set in
`_TERMINAL_EVENT_SQL`. A `CONSUMED`-resting order is now correctly classified
ACTIVE → the lock SUPPRESSES the duplicate ENTRY on re-decision. No edge / q /
freshness gate is touched.

## Exact reason the lock didn't fire (file:line)

`src/engine/event_reactor_adapter.py`, `_TERMINAL_EVENT_SQL` (the terminal-event
classifier used by `_locked_live_opportunity_active_order_reason`, called at
line 4582 before `SubmitPlanBuilt`):

Buggy fragment (pre-fix, line ~5021):

```sql
OR (event_type = 'CapTransitioned'
    AND json_extract(payload_json, '$.to_status')
        IN ('CONSUMED', 'RELEASED'))
```

A row from this query = TERMINAL → `return None` → **lock releases** → submit
allowed.

`CONSUMED` is emitted by `_transition_live_cap_after_submit`
(`src/engine/event_reactor_adapter.py:5417`) on the **`submit_result.status ==
"SUBMITTED"`** branch (lines 5431–5452): the submit SUCCEEDED and the order is
**resting live on the venue**. There is **no fill** at this point. A fill is a
separate, later `UserTradeObserved(fill_authority_state='FILL_CONFIRMED')` event.
Corroboration of the semantics: `_durable_unmaterialized_live_cap_reservations`
(line 683) treats `reservation_status IN ('RESERVED','CONSUMED')` as
**in-flight capital NOT yet represented by position truth** — i.e. submitted /
resting, not filled.

The lock keys correctly on the FAMILY identity `(condition_id, token_id,
direction)` from the `SubmitPlanBuilt` payload (lines 5063–5075), NOT on
`event_id` — so two different re-decision events on the same family DO see each
other's aggregate. That part was right. The defect was purely the **terminal
classification of the resting state**: each resting order's `CONSUMED` made the
lock believe the prior order was done, so the next event sailed through.

The behavior is independent of price: the retired 0.02 price-improvement gate is
gone, and the lock suppresses ANY duplicate while an order is genuinely active.
The live re-bids were same-price (0.570), so they were precisely the case that
must HOLD.

### Live evidence (recorded events, `state/zeus-world.db`)

token `35015396764119764057109967922516391182815114821189461579432074152958132060729`,
condition `0x8670653a20…`, direction `buy_no`, all `limit_price=0.57`:

| aggregate (prefix) | occurred_at (UTC)             | terminal marker present                |
|--------------------|-------------------------------|----------------------------------------|
| `edli_evt_af84d73d68` | 23:39:03 | `CapTransitioned to_status=CONSUMED` (resting LIVE) |
| `edli_evt_8aa7386ec6` | 23:45:50 | `CapTransitioned to_status=CONSUMED` (resting LIVE) |
| `edli_evt_b6fef78900` | 23:48:08 | `SubmitRejected` then `CapTransitioned RELEASED` (venue refused the 3rd) |

Each of the first two aggregates' lifecycle is:
`DecisionProofAccepted → SubmitPlanBuilt → PreSubmitRevalidated → LiveCapReserved
→ ExecutionCommandCreated → VenueSubmitAttempted → VenueSubmitAcknowledged →
CapTransitioned(CONSUMED)` — an **acknowledged, resting, unfilled** order. No
`UserTradeObserved`. The third aggregate was rejected by the venue (likely the
balance/duplicate already consumed), which is itself the symptom of the
over-submission.

### Replay proof against the live rows

Running the FIXED terminal query vs the OLD terminal query against agg #1's real
recorded events:

```
agg#1 (resting, CONSUMED only)
  OLD-buggy query  -> row (1,)  => RELEASE  (the live defect, reproduced)
  FIXED query      -> None      => SUPPRESS (lock holds; 2nd ENTRY blocked)
```

So under the fix, the 23:45 re-decision would have emitted the
`EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED` HOLD and placed NO second order.

## The fix (diff)

`src/engine/event_reactor_adapter.py` — terminal-event SQL:

```diff
         OR (event_type = 'CapTransitioned'
-            AND json_extract(payload_json, '$.to_status')
-                IN ('CONSUMED', 'RELEASED'))
+            AND json_extract(payload_json, '$.to_status') = 'RELEASED')
```

Plus comment corrections (the `_TERMINAL_EVENT_SQL` header and the
`terminal_row` / ACTIVE-state inline notes) restating that `CONSUMED` is a
resting-live (ACTIVE) state, not a fill. No control-flow change beyond the SQL.

### Why the fix does not deadlock / over-suppress

A resting order does reach a real terminal state later, all of which remain in
the terminal set:

- **Fill** → `UserTradeObserved` (terminal). Family releases and re-sizes.
- **900s timeout / cancel / authenticated absence** → `Reconciled` +
  `CapTransitioned(RELEASED)` (both terminal — `src/execution/command_recovery.py`,
  `src/execution/edli_absence_resolver.py`). Family re-bids at fresh price.
- **Venue reject** → `SubmitRejected` (terminal).

So a CONSUMED-resting order suppresses ONLY while it is genuinely resting; the
moment it fills or closes, the lock releases. This is verified by
`test_fixA_cap_transitioned_consumed_then_fill_releases_lock`.

## Why no edge / q / freshness gate is loosened

This change is confined to the duplicate-suppression lock's terminal-state
classifier. It only ever makes the lock **suppress MORE** (a resting order is now
ACTIVE instead of falsely-terminal). It adds no path that lets a submit through
that previously held; it removes no certification, q_lcb, after-cost EV,
freshness, or price gate. All downstream re-certification on a released family is
unchanged. Reads of "is there an active order" come from the venue-truth-backed
`edli_live_order_events` aggregate lifecycle (no fabrication). No orders are
cancelled by this change — the two existing live orders self-expire on their 900s
timeout; this fix only STOPS NEW duplicates.

## RED-on-revert test

`tests/money_path/test_edli_live_readiness.py`:

- `test_fixA_cap_transitioned_consumed_suppresses_resting_live_order`
  (replaces the old, defect-encoding `test_fixA_cap_transitioned_consumed_releases_lock`):
  a family with an acknowledged, resting `CONSUMED` order, re-decided at the SAME
  price (0.70) AND at a WORSE price (0.72), must yield a non-None
  `EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED` HOLD — no second ENTRY.
- `test_fixA_cap_transitioned_consumed_then_fill_releases_lock` (guard against
  over-suppression): a `CONSUMED` order that subsequently FILLS
  (`UserTradeObserved`) IS terminal — the lock releases.

RED-on-revert confirmed: re-adding `'CONSUMED'` to the terminal set makes the
first test fail with `Got: None` (lock released on a resting order); restoring
the fix makes it pass.

## Test output

```
# Money path (full canary suite):
$ python3 -m pytest tests/money_path/test_edli_live_readiness.py -q
........................................................                 [100%]
56 passed in 1.19s

# FIX A lock family (incl. both new tests):
$ python3 -m pytest tests/money_path/test_edli_live_readiness.py -k fixA -q
...........                                                              [100%]
11 passed, 45 deselected in 1.18s

# RED-on-revert (CONSUMED re-added to terminal set):
$ python3 -m pytest ... -k consumed_suppresses_resting_live -q
FAILED ...::test_fixA_cap_transitioned_consumed_suppresses_resting_live_order
  AssertionError: ... the lock MUST suppress the duplicate ENTRY.  Got: None
1 failed
# -> fix restored -> green again.
```

## Files changed

- `src/engine/event_reactor_adapter.py` — `_TERMINAL_EVENT_SQL` (drop `'CONSUMED'`
  from terminal cap-status set) + comment corrections.
- `tests/money_path/test_edli_live_readiness.py` — replaced the defect-encoding
  CONSUMED-releases test with the RED-on-revert suppress test; added the
  CONSUMED-then-fill release guard.

## Status

NOT committed, daemon NOT restarted (per instruction). The two already-resting
live orders are left to self-expire on their 900s timeout; the orchestrator
reviews/commits/deploys.
