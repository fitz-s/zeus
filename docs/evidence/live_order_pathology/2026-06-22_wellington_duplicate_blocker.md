# Wellington Duplicate Blocker — Venue Truth + Reconcile Verdict
# Date: 2026-06-22, ~16:44 UTC (investigation completed ~18:30 UTC)
# Mandate: monitorParity teammate — exact blocker record, venue truth, precise reconcile action

---

## 1. The Exact Blocking Record

**Blocked candidate:** Wellington highest temperature 17°C on 2026-06-24  
- direction: `buy_no`  
- token_id: `107131836834170805613128321665571901119891602541584479616724432516248693319800`  
- condition_id: `0x117622a544e8fe8fbea2316530d12bbfa9217f2d592777cb9b1bc847ac3cd68b`  
- q_lcb: 0.704, price: 0.562, edge_lcb: +0.142  
- rejection: `EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED`

**Blocking aggregate:**
```
aggregate_id: edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449
              :edli_intent:edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449
              :66056803750256461525842897441761005522616620854119790748180393208853879803996
direction:     buy_YES  (complementary bin — NOT the same direction as the blocked trade)
token_id:      66056803750256461525842897441761005522616620854119790748180393208853879803996
city:          Wellington
target_date:   2026-06-24
metric:        high
limit_price:   0.08
size:          84.1 USD
current_state: EXECUTION_COMMAND_CREATED  (no terminal event)
venue_order_id: NULL  (no venue_order_id was ever stamped)
updated_at:    2026-06-22T16:44:33.840855+00:00
```

**Why it blocks:** The duplicate check at `event_reactor_adapter.py:6319–6360` queries
`edli_live_order_events` for any `SubmitPlanBuilt` matching `city=Wellington AND target_date=2026-06-24
AND metric=high`. This query has **no direction filter** — a buy_YES aggregate with no terminal event
blocks all subsequent submissions for the entire family (YES and NO alike).

**Cap usage entry (committed, still RESERVED):**
```
usage_id:            edli_live_cap:59b1c9975d7c894ad7a9cc123f321ab5
cap_scope:           tiny_live_canary
reserved_notional:   6.728614681816076 USD
reservation_status:  RESERVED
created_at:          2026-06-22T16:44:33.841638+00:00
```

**Event sequence for the blocking aggregate:**
```
seq=1  DecisionProofAccepted   2026-06-22T16:43:09
seq=2  SubmitPlanBuilt          2026-06-22T16:43:09  buy_yes, 84.1 USD @0.08
seq=3  PreSubmitRevalidated     2026-06-22T16:43:09
seq=4  LiveCapReserved          2026-06-22T16:43:09
seq=5  ExecutionCommandCreated  2026-06-22T16:43:09  ← LAST EVENT (no terminal)
```

No `VenueSubmitAttempted`, no `SubmitRejected`, no `CapTransitioned` — the aggregate is frozen at
`ExecutionCommandCreated`.

---

## 2. Venue Truth Verdict: ZOMBIE (No Real Capital at Venue)

**verdict: ZOMBIE — no resting order at Polymarket**

Evidence:

1. **No venue_order_id**: `edli_live_order_projection.venue_order_id = NULL`. An order that
   actually reached Polymarket would have a venue_order_id stamped by `VenueSubmitAttempted`
   or `UserTradeObserved`. None exists.

2. **No venue_command row in zeus_trades.db**: Searched both `zeus_trades.db` (venue_commands)
   and `zeus-world.db` (venue_commands) for the Wellington YES token
   (`660568037...`). Zero matches. The execution_command_id exists only as a payload reference in
   `edli_live_order_events`; it was never materialized as a `venue_commands` row.

3. **No capital on chain**: Without a `venue_commands` row there is no order on Polymarket.
   The family never got past `ExecutionCommandCreated` to the venue submission layer.

4. **Context of 24 prior Wellington aggregates**: All 21 completed aggregates (YES and NO)
   terminated with `SubmitRejected` + `CapTransitioned(RELEASED)`. This one is anomalous —
   it stopped at `ExecutionCommandCreated` without the normal SubmitRejected progression. This
   is consistent with the executor having accepted the command internally but then being
   interrupted (daemon restart, lock, or processing gap) before the venue submit attempt.

5. **Live daemon log confirms**: The log shows `EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED`
   firing repeatedly for Wellington buy_NO after 16:43 UTC. No subsequent buy_YES
   `SubmitPlanBuilt` appears in `edli_live_order_events` after 16:43 — the blocking aggregate
   was left stranded by whatever interrupted the execution path.

**Capital at risk: $0.00 at Polymarket.** The $6.73 cap reservation in `edli_live_cap_usage` is
a local accounting entry; no venue order was placed.

---

## 3. Precise Reconcile Action

**Root cause:** The `ad064baf` aggregate has no terminal event and the cap usage entry is still
`RESERVED`. The duplicate check sees this aggregate as live (non-terminal), blocking the entire
Wellington/2026-06-24/high family indefinitely.

**Neither `Reconciled` nor `CapTransitioned RELEASED` can be appended directly:**
- `Reconciled` requires `SubmitUnknown` or `pending_reconcile=True` in the projection. This
  aggregate has neither (projection shows `pending_reconcile=0`, no SubmitUnknown).
- `CapTransitioned RELEASED` with `reason_code=SUBMIT_DISABLED` bypasses SubmitRejected (line
  468 of live_order_aggregate.py) but requires `execution_receipt_hash` — a real
  `DecisionCertificate.certificate_hash` from the execution receipt cert chain, not constructable
  ad-hoc without the certificate infrastructure.

**The exact two-step reconcile path via `LiveOrderAggregateLedger.append_event()` in zeus-world.db:**

### Step A: Append `SubmitRejected` (pre-submit form)

This is allowed after `ExecutionCommandCreated` without a prior `VenueSubmitAttempted` when
`_is_pre_submit_rejection_payload` returns True.

Required payload fields (all values from the existing `ExecutionCommandCreated` payload):
```python
payload = {
    # command binding (must match ExecutionCommandCreated payload exactly):
    "event_id":             "edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449",
    "final_intent_id":      "edli_intent:edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449:66056803750256461525842897441761005522616620854119790748180393208853879803996",
    "execution_command_id": "edli_exec_cmd:edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449:edli_intent:edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449:66056803750256461525842897441761005522616620854119790748180393208853879803996:66056803750256461525842897441761005522616620854119790748180393208853879803996:buy_yes",
    # pre-submit rejection markers (required by _is_pre_submit_rejection_payload):
    "pre_submit_rejection":  True,
    "submit_status":         "PRE_SUBMIT_ERROR",
    "venue_call_started":    False,
    # required by SubmitRejected validation:
    "reason_code":           "MANUAL_RECONCILE_ZOMBIE_PRE_SUBMIT",
}
```

Call via:
```python
from src.events.live_order_aggregate import LiveOrderAggregateLedger
import sqlite3
from datetime import datetime, timezone

conn = sqlite3.connect("/Users/leofitz/zeus/state/zeus-world.db")
conn.row_factory = sqlite3.Row
AGGREGATE_ID = (
    "edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449"
    ":edli_intent:edli_evt_ad064bafc031a30d8a8dac21573f9b3a6808b9c1e4713c6985c02807d9f44449"
    ":66056803750256461525842897441761005522616620854119790748180393208853879803996"
)
LiveOrderAggregateLedger(conn).append_event(
    aggregate_id=AGGREGATE_ID,
    event_type="SubmitRejected",
    payload=payload,  # as defined above
    occurred_at=datetime.now(timezone.utc),
    source_authority="manual_operator_reconcile",
)
conn.commit()
```

This appends `SubmitRejected` at sequence 6 (after `ExecutionCommandCreated` at seq 5) and
satisfies `_TERMINAL_EVENT_SQL`. The duplicate lock is released immediately.

### Step B: Release the cap usage reservation

After Step A, call `LiveCapLedger.release()` to free the $6.73 phantom reservation:
```python
from src.events.live_cap import LiveCapLedger

USAGE_ID = "edli_live_cap:59b1c9975d7c894ad7a9cc123f321ab5"
LiveCapLedger(conn).release(USAGE_ID, reason="MANUAL_RECONCILE_ZOMBIE")
conn.commit()
```

This updates `edli_live_cap_usage.reservation_status = 'RELEASED'` and also deletes from
`edli_live_cap_day_slots` and `edli_live_cap_rate_window` for this usage_id. Same conn can be
used (world DB).

**DO NOT:**
- Cancel at Polymarket — there is no order to cancel
- Write directly to the live WAL DB via raw SQL INSERT/UPDATE bypassing `LiveOrderAggregateLedger`
  (it maintains hash chains and projection state atomically)
- Append `CapTransitioned RELEASED` without a valid `execution_receipt_hash`
- Delete the `edli_live_cap_usage` row (call `release()`, don't delete — deleting orphans accounting)

---

## 4. Context: Why This Aggregate Stalled

The 24 earlier Wellington aggregates (2026-06-22 from 11:04–16:43 UTC) all completed normally
with `SubmitRejected` + `CapTransitioned`. The `ad064baf` aggregate is the sole exception —
it reached `ExecutionCommandCreated` without progressing to `VenueSubmitAttempted`.

Most likely cause: daemon interruption or lock between the `ExecutionCommandCreated` write and
the venue submission attempt. The executor did not fire. Because `ExecutionCommandCreated` is
not a terminal event, the aggregate persists as a ghost blocking the family.

---

## 5. Current Status of All Wellington 2026-06-24 Aggregates

| aggregate (short) | direction | last event | terminal |
|---|---|---|---|
| edli_evt_83e7f262... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_f13b5a91... | buy_no | SubmitRejected+CapTransitioned | YES |
| edli_evt_1c1dfc3c... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_411f41c3... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_04907167... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_e34373d2... | buy_no | SubmitRejected+CapTransitioned | YES |
| edli_evt_40e42acb... | buy_no | SubmitRejected+CapTransitioned | YES |
| edli_evt_0bf1f3c3... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_ac3807c8... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_f676b60d... | buy_yes | SubmitRejected+CapTransitioned (2 loops) | YES |
| edli_evt_e297298... | buy_yes | SubmitRejected+CapTransitioned (2 loops) | YES |
| edli_evt_bdc2e5e... | buy_no | SubmitRejected+CapTransitioned | YES |
| edli_evt_3cc4d2a... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_3ac0608... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_1a04e3d... | buy_no | SubmitRejected+CapTransitioned | YES |
| edli_evt_0e2ff0b... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_9a56a03... | buy_no | SubmitRejected+CapTransitioned | YES |
| edli_evt_687e010... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_10336fe... | buy_yes | SubmitRejected+CapTransitioned | YES |
| edli_evt_23012b9... | buy_no | SubmitRejected+CapTransitioned | YES |
| **edli_evt_ad064ba...** | **buy_yes** | **ExecutionCommandCreated (STUCK)** | **NO — BLOCKER** |

Only `ad064baf` is non-terminal. Zero venue_order_ids across all 21 aggregates (all venue
submissions were rejected, none reached fill).

---

## 6. Files and DB Locations

- `zeus-world.db`: `edli_live_order_events` (7508 rows), `edli_live_order_projection`,
  `edli_live_cap_usage` — the tables the duplicate check reads
- `zeus_trades.db`: `venue_commands` (293 rows) — confirmed 0 rows for Wellington YES token
- Duplicate check code: `src/engine/event_reactor_adapter.py:6319–6360`
- Projection table shows aggregate stuck at `EXECUTION_COMMAND_CREATED` since 16:44 UTC
