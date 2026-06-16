# Fill-to-Position Canonical Path — 2026-06-16

Investigation of the exact code path from "observed confirmed venue trade" →
`venue_trade_facts` row → `position_lots` / `position_current` / `position_events`,
as executed by the live EDLI trading loop.

---

## 0. CRITICAL FINDING: The Stuck Aggregate is a Different Failure Class

The aggregate `edli_evt_01db461b9e7036125b2d880f781c8f77129015bb1ff47a6011169ca0ae2ede4b:edli_intent:...:94919691709...517`
**does NOT follow the normal orphan-fill pattern** that the existing bridges handle.

**Event history (8 events):**

```
seq=1  DecisionProofAccepted
seq=2  SubmitPlanBuilt        (condition, direction=buy_no, YES token)
seq=3  PreSubmitRevalidated   (city=Houston, target_date=2026-06-17, metric=high,
                                strategy_key=opening_inertia, unit=F, bin_label=...,
                                expected_edge_source_certificate_hash=dc41080d...)
seq=4  LiveCapReserved
seq=5  ExecutionCommandCreated
seq=6  VenueSubmitAttempted
seq=7  SubmitUnknown          (reason_code="EXECUTOR_SUBMIT_UNKNOWN:database is locked",
                                side_effect_known=False, venue_call_started=True,
                                NO venue_order_id in payload)
seq=8  CapTransitioned
```

**Projection state:** `PENDING_RECONCILE`, `venue_order_id = NULL`

**What is ABSENT:**
- No `VenueSubmitAcknowledged` event — the submit returned unknown before an ack
- No `UserTradeObserved` event — no fill event was ever appended
- No `venue_trade_facts` row for `trade_id=cf967209-599e-4813-b5c3-ee3c9221013b`
- No `venue_commands` row for this aggregate's `execution_command_id`

This means the EDLI aggregate has **no confirmed fill event** in the event store.
The `materialize_position_current_from_edli_fill` function checks for
`UserTradeObserved` with `fill_authority_state == "FILL_CONFIRMED"` first
and returns `None` immediately without it. The existing bridges also cannot inject
the fill because:

1. `append_confirmed_trade_facts_to_edli` — requires a `venue_trade_facts` row with
   `source='WS_USER'` AND `state='CONFIRMED'` AND a `VenueSubmitAcknowledged` event
   on the aggregate (JOINs on `ack.venue_order_id = trade.venue_order_id`). Neither
   exists.
2. `append_rest_filled_orphan_trade_facts_to_edli` — requires `venue_trade_facts` row
   AND `venue_commands` row (decision_id = execution_command_id). Neither exists.

**The boot resolver must:**
(A) Write `venue_trade_facts` + `venue_commands` + `VenueSubmitAcknowledged` to
    unlock the existing bridge, OR
(B) Inject a `UserTradeObserved` event directly via `append_reconcile_recovered_fill`,
    bypassing the trade-fact bridge.

Path (B) is the clean seam — but requires the aggregate ledger to accept a
`venue_order_id` even though the original submit was unknown. See §5 for the
constraint analysis.

---

## 1. venue_trade_facts Writer

**File:** `src/state/venue_command_repo.py`
**Line:** 2239
**Function:** `append_trade_fact`

```python
def append_trade_fact(
    conn: sqlite3.Connection,
    *,
    trade_id: str,
    venue_order_id: str,
    command_id: str,
    state: str,              # 'MATCHED' | 'MINED' | 'CONFIRMED' | 'FAILED' | ...
    filled_size: str,        # positive finite decimal text
    fill_price: str,         # positive finite decimal text
    source: str,             # 'WS_USER' | 'REST' | 'OPERATOR' | ...
    observed_at: str | datetime.datetime | None,
    raw_payload_hash: str,   # sha256 hex
    raw_payload_json: Any = None,
    fee_paid_micro: int | None = None,
    tx_hash: str | None = None,
    block_number: int | None = None,
    confirmation_count: int | None = None,
    venue_timestamp: str | datetime.datetime | None = None,
    local_sequence: int | None = None,
) -> int:   # returns trade_fact_id (lastrowid)
```

**What it does:**
- Validates state, fill economics, source, observed_at, raw_payload_hash
- Opens `SAVEPOINT` (uses `_savepoint_atomic(conn)`)
- `INSERT INTO venue_trade_facts (trade_id, venue_order_id, command_id, state,
  filled_size, fill_price, fee_paid_micro, tx_hash, block_number,
  confirmation_count, source, observed_at, venue_timestamp, local_sequence,
  raw_payload_hash, raw_payload_json)`
- Appends a provenance event
- If `state == 'FAILED'`: triggers `_rollback_optimistic_lots_for_failed_trade`
- Does NOT commit — caller owns transaction boundary

**DB connection:** Same `conn` passed in; must be a **zeus_trades.db** connection
(the table `venue_trade_facts` is in zeus_trades.db). Does NOT require
`world_write_lock` internally (uses SAVEPOINT only).

**Callers in live path:** `src/execution/fill_tracker.py` (fill tracker on WS
messages), `src/execution/exchange_reconcile.py` (`_append_linkable_trade_fact_if_missing`),
`src/ingest/polymarket_user_channel.py` (user channel ingestor).

Also writes `venue_order_facts` (`append_order_fact` in same file, line ~2150)
for order-level state, distinct from trade fills.

---

## 2. Fill → position_lots / position_current / position_events

There are **two** production paths:

### Path A: EDLI Lane (the correct path for this aggregate)

**Module:** `src/events/edli_position_bridge.py`
**Line:** 864
**Function:** `materialize_position_current_from_edli_fill`

```python
def materialize_position_current_from_edli_fill(
    conn: sqlite3.Connection,
    aggregate_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
```

**What it does (when `UserTradeObserved` with `fill_authority_state=FILL_CONFIRMED` exists):**

1. Reads all EDLI events for the aggregate from `edli_live_order_events`
2. Checks `_has_confirmed_fill` — returns `None` if no `FILL_CONFIRMED` event
3. `_resolve_identity` pulls from `PreSubmitRevalidated`:
   condition_id, token_id (YES-token), direction, city, target_date, bin_label,
   metric, unit, strategy_key, market_id, cluster, etc.
4. `_confirmed_fill_payloads` deduplicates fills by `trade_id`
5. `_aggregate_fill_economics` sums filled_size, VWAP price, fees
6. `_build_bridge_position` constructs a `Position` object. **Token placement:**
   - `buy_yes`: `pos.token_id = elected_token` (YES token from PreSubmitRevalidated)
   - `buy_no`: `pos.no_token_id = elected_token` (YES token stored as no_token_id!)
   The elected token is ALWAYS the YES token from the PreSubmitRevalidated event.
   Chain reconciliation then reads: `tid = pos.token_id if buy_yes else pos.no_token_id`.
7. `build_entry_canonical_write(pos, phase_after=ACTIVE, ...)` — returns
   `(events_batch, projection)` with 3 events: POSITION_OPEN_INTENT (seq=1),
   ENTRY_ORDER_POSTED (seq=2), ENTRY_ORDER_FILLED (seq=3)
8. First materialisation: `append_many_and_project(conn, events_batch, projection)`
   Replay: `upsert_position_current(conn, projection)` only
9. `log_execution_fact(conn, ...)` — writes to `execution_fact` table

**DB connection:** Requires `get_trade_connection_with_world_required(write_class="live")` —
a **zeus_trades.db** connection with **zeus-world.db ATTACHed as "world"**.
`edli_live_order_events` is read via `world.edli_live_order_events`.

**Does NOT commit.** Caller commits.

**INV-37:** All reads and writes on the single passed connection. Nested SAVEPOINT
inside `append_many_and_project`. No independent connection.

### Path B: Legacy direct-order lane (exchange_reconcile)

**File:** `src/execution/exchange_reconcile.py`
**Line:** 3691
**Function:** `_apply_entry_fill_projection_and_execution_fact` (private)

Called from `run_reconcile_sweep` via `_process_filled_entry_order`. Requires
an existing `venue_commands` row and a linkable trade fact. NOT applicable for
the stuck aggregate (no `venue_commands` row).

`position_lots` are written by `_append_entry_position_lots_for_command`
(line 3794) which is called from this function. It queries `canonical_trade_fact`
CTE over `venue_trade_facts`, deduplicates by `source_trade_fact_id`, and calls
`append_position_lot` (venue_command_repo.py:2346) for each confirmed/matched fill.

**`append_position_lot` signature:**
```python
def append_position_lot(
    conn: sqlite3.Connection,
    *,
    position_id: int,            # trade_decisions.trade_id integer
    state: str,                  # 'CONFIRMED_EXPOSURE' | 'OPTIMISTIC_EXPOSURE'
    shares: int | float | str | Decimal,
    entry_price_avg: str,
    captured_at: str | datetime.datetime,
    state_changed_at: str | datetime.datetime,
    exit_price_avg: str | None = None,
    source_command_id: str | None = None,
    source_trade_fact_id: int | None = None,
    source: str = "OPERATOR",
    observed_at: str | datetime.datetime | None = None,
    raw_payload_hash: str | None = None,
    raw_payload_json: Any = None,
    venue_timestamp: str | datetime.datetime | None = None,
    local_sequence: int | None = None,
) -> int:   # returns lot_id
```

INSERTs into `position_lots` under SAVEPOINT. `position_id` is the INTEGER
`trade_decisions.trade_id` (not the string position_id). Resolved via
`resolve_position_lot_id_for_command` which does:
```
trade_decisions WHERE runtime_trade_id = command.position_id
```

**The EDLI bridge does NOT write `position_lots`.** The bridge writes only
`position_events` + `position_current` (via `append_many_and_project`).
`position_lots` are a separate legacy accounting table populated only by the
direct-order lane (exchange_reconcile / fill_tracker). For a bridged EDLI
position, `position_lots` will remain empty unless a `venue_commands` row
with a linked `trade_decisions.runtime_trade_id` exists.

---

## 3. Live Loop Reconcile Entrypoint

The highest-level entrypoint that runs the full reconcile cycle is:

**File:** `src/main.py`
**Line:** 8080
**Function:** `_edli_user_channel_reconcile_cycle`

**Call site in main loop:** Called by the scheduler as a recurring job (cron-style).

**Sequence per cycle:**
1. Opens `world` connection for EDLI event writes
2. Polls `polymarket_user_channel` → appends `UserTradeObserved` events via
   `append_user_channel_message`
3. Calls `append_confirmed_trade_facts_to_edli(conn)` — bridge WS_USER CONFIRMED
   trade facts → `UserTradeObserved` EDLI events
4. Calls `append_rest_filled_orphan_trade_facts_to_edli(conn)` — fallback for
   WS dropout fills (REST trade fact + terminal command state)
5. Commits world connection
6. Opens `get_trade_connection_with_world_required(write_class="live")` as `bridge_conn`
7. For each recently-processed aggregate: `materialize_position_current_from_edli_fill`
8. `_edli_durable_fill_bridge_scan(bridge_conn)` — idempotent scan of ALL
   `UserTradeObserved:FILL_CONFIRMED` aggregates missing a `position_current` row
9. Commits `bridge_conn`

**Boot-time recovery function:**

**File:** `src/main.py`
**Line:** 8310
**Function:** `_edli_boot_fill_bridge_recovery`

```python
def _edli_boot_fill_bridge_recovery() -> None:
```

Opens `get_trade_connection_with_world_required(write_class="live")`, calls
`_edli_durable_fill_bridge_scan`, commits, closes. Runs once at daemon startup
before the trading loop. This is the **canonical standalone boot seam** for
healing orphaned confirmed fills.

**`_edli_durable_fill_bridge_scan` signature (main.py:7857):**
```python
def _edli_durable_fill_bridge_scan(conn, *, now=None, limit: int = 500) -> int:
```
Finds every aggregate in `edli_live_order_events` with `UserTradeObserved` +
`fill_authority_state='FILL_CONFIRMED'` that has no `position_current` row
(dual-probe: wide 68-char ID + legacy 11-char ID). Calls
`materialize_position_current_from_edli_fill` for each. Returns count of healed fills.

---

## 4. buy_no Token Handling (Dual-Token-ID)

**From `_build_bridge_position` (edli_position_bridge.py:525):**
```python
direction = identity["direction"]          # "buy_no"
elected_token = identity["token_id"]       # YES-token from PreSubmitRevalidated
                                           # = 9491969170...517 (YES-token)

if direction == "buy_yes":
    pos.token_id = elected_token
else:
    pos.no_token_id = elected_token        # <-- buy_no: YES-token stored as no_token_id
```

**Chain reconciliation lookup (chain_reconciliation.py:1057):**
```python
tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id
```

So for `buy_no`:
- `pos.token_id` = empty / None (the YES slot is unset for buy_no positions)
- `pos.no_token_id` = YES-token 9491969... (the token actually TRADED/bought)
- Chain reconciliation looks up `pos.no_token_id` to find the on-chain holding

**What `_resolve_identity` extracts for this aggregate:**
```
condition_id = "0x0d623c05b8d291022d79724641f16add2098cd994ca3da6590831d088250d59b"
token_id     = "94919691709339926248609367448320440419051501993103034121677455440943187656517"  # YES-token
direction    = "buy_no"
```

The NO-token asset_id (`49128580161441347983725531208820142357219356580732147594666453507088262789094`)
mentioned in the task is the counterpart token. Zeus stores the **YES-token** in
`position_current.no_token_id` (and `position_current.token_id` stays NULL for
buy_no positions). The NO-token is NOT stored in the position record — the
convention is that `PreSubmitRevalidated.token_id` = YES-token and direction
encodes which side was bought.

---

## 5. Cleanest Reusable Seam for Boot Resolver

### The fundamental problem with this aggregate

The aggregate is in `PENDING_RECONCILE` state with:
- `venue_order_id = NULL` (SubmitUnknown, side_effect_known=False)
- No `UserTradeObserved` event
- No `venue_commands` row in zeus_trades.db
- No `venue_trade_facts` row for trade_id `cf967209-599e-4813-b5c3-ee3c9221013b`

The confirmed venue trade exists on-chain but is not linked to this aggregate in
any of Zeus's databases.

### What the boot resolver must do (in order)

**Step 1: Write `venue_commands` row** (zeus_trades.db)
The trade fact bridge JOINs `venue_commands ON cmd.decision_id = execution_command_id`.
Must create a minimal command row with the execution_command_id as `decision_id`,
the known `venue_order_id` from the confirmed trade (from on-chain data), and
`state='FILLED'`. Function: `append_venue_command` (venue_command_repo.py:~760,
requires snapshot/envelope prerequisites) or a direct INSERT under SAVEPOINT.

**Note:** `append_venue_command` requires `venue_submission_envelopes` row
(validated by `_assert_envelope_gate`). For a boot resolver this is likely
unavailable. Alternative: use `append_trade_fact` directly with a synthetic
`command_id` + inject a `VenueSubmitAcknowledged` EDLI event.

**The CLEANEST SEAM that avoids hand-rolling:**

**Step 1 (simpler):** Inject `VenueSubmitAcknowledged` EDLI event into the
aggregate (world.db), binding the known `venue_order_id`
(`0x...` from the Polymarket trade — **must be obtained from the Polymarket API
or on-chain data since it is not in Zeus's databases**).

**Step 2:** Write `venue_trade_facts` row (zeus_trades.db) via `append_trade_fact`
with the confirmed trade data (trade_id, venue_order_id, command_id that matches
the one written in Step 1 — or use a synthetic command_id and also write a
`venue_commands` row).

**Step 3:** Let `append_rest_filled_orphan_trade_facts_to_edli` (or call
`append_reconcile_recovered_fill` directly) inject the `UserTradeObserved` EDLI
event on the world connection.

**Step 4:** Call `materialize_position_current_from_edli_fill(bridge_conn, agg_id)`
on a trade connection with world ATTACHed.

### Alternatively: Direct injection via `append_reconcile_recovered_fill`

```python
# src/events/live_order_reconcile.py:96
append_reconcile_recovered_fill(
    ledger,               # LiveOrderAggregateLedger(world_conn)
    aggregate_id=agg_id,
    event_id=...,         # from PreSubmitRevalidated: event_id field
    final_intent_id=...,  # from PreSubmitRevalidated: final_intent_id
    venue_order_id=...,   # THE CONFIRMED VENUE ORDER ID (needed from on-chain)
    occurred_at=...,      # trade timestamp
    payload={
        "trade_id": "cf967209-599e-4813-b5c3-ee3c9221013b",
        "filled_size": "5.07",
        "fill_price": "0.36",
        "avg_fill_price": "0.36",
        "transaction_hash": ...,
        "source_trade_fact_authority": "venue_trade_facts:RECONCILE:CONFIRMED",
        "venue_command_state": "FILLED",
        "recovery_basis": "boot_resolver:submit_unknown_with_confirmed_trade",
        "raw_user_channel_message_hash": ...,   # required by aggregate validation
    },
)
```

**Constraint:** `_require_user_channel_submit_binding` checks (live_order_aggregate.py:500):
1. `PENDING_RECONCILE` projection is OK (not terminal `RECONCILED`)
2. `occurred_at >= ExecutionCommandCreated.occurred_at` — OK
3. A `VenueSubmitAttempted` OR `VenueSubmitAcknowledged` OR `SubmitUnknown` exists — **OK** (SubmitUnknown at seq=7 satisfies this)
4. `venue_order_id` must be non-empty — **the resolver must supply the confirmed venue_order_id**
5. If `SubmitUnknown.venue_order_id` is non-None, it must match — the SubmitUnknown event has **no venue_order_id** (payload has no such key), so `bound_order_id = None` → no mismatch check → **any venue_order_id passes**
6. `raw_user_channel_message_hash` must be non-empty and unique

**This means `append_reconcile_recovered_fill` CAN be called on this aggregate**
**provided the caller supplies:**
- The confirmed `venue_order_id` (obtained from the Polymarket API or on-chain)
- A synthetic unique `raw_user_channel_message_hash` (e.g., sha256 of the trade_id + "boot_resolver")
- Correct `event_id`, `final_intent_id` (from the PreSubmitRevalidated event)

After that, `materialize_position_current_from_edli_fill` runs normally.

### Seam summary: cleanest standalone callable sequence

```python
# Step 1: On the world connection (write_class="live")
from src.events.live_order_aggregate import LiveOrderAggregateLedger
from src.events.live_order_reconcile import append_reconcile_recovered_fill

world_conn = get_world_connection(write_class="live")
ledger = LiveOrderAggregateLedger(world_conn)
append_reconcile_recovered_fill(
    ledger,
    aggregate_id=FULL_AGGREGATE_ID,
    event_id=EVENT_ID_FROM_PRE_SUBMIT,
    final_intent_id=FINAL_INTENT_ID,
    venue_order_id=VENUE_ORDER_ID_FROM_POLYMARKET,
    occurred_at=TRADE_OBSERVED_AT,
    payload={...required fields including trade_id, filled_size, fill_price,
              raw_user_channel_message_hash, source_trade_fact_authority,
              venue_command_state, recovery_basis...},
)
world_conn.commit()
world_conn.close()

# Step 2: On the trade connection with world ATTACHed
from src.state.db import get_trade_connection_with_world_required
from src.events.edli_position_bridge import materialize_position_current_from_edli_fill

bridge_conn = get_trade_connection_with_world_required(write_class="live")
result = materialize_position_current_from_edli_fill(bridge_conn, FULL_AGGREGATE_ID)
bridge_conn.commit()
bridge_conn.close()
```

**This sequence goes through the SAME canonical code path the live loop uses.**
No hand-rolled position creation. position_lots are NOT written (the EDLI bridge
does not write them — only the direct-order lane does), but position_current and
position_events are written correctly with FILL_AUTHORITY_VENUE_CONFIRMED_FULL,
which is in FILL_GRADE_FILL_AUTHORITIES, so chain-reconciliation + exit + harvester
+ redeem will all see the position.

---

## 6. Key Values for the Stuck Aggregate

```
FULL_AGGREGATE_ID = "edli_evt_01db461b9e7036125b2d880f781c8f77129015bb1ff47a6011169ca0ae2ede4b:edli_intent:edli_evt_01db461b9e7036125b2d880f781c8f77129015bb1ff47a6011169ca0ae2ede4b:94919691709339926248609367448320440419051501993103034121677455440943187656517"

EVENT_ID            = "edli_evt_01db461b9e7036125b2d880f781c8f77129015bb1ff47a6011169ca0ae2ede4b"

FINAL_INTENT_ID     = "edli_intent:edli_evt_01db461b9e7036125b2d880f781c8f77129015bb1ff47a6011169ca0ae2ede4b:94919691709339926248609367448320440419051501993103034121677455440943187656517"

EXECUTION_COMMAND_ID = "edli_exec_cmd:edli_evt_01db461b9e7036125b2d880f781c8f77129015bb1ff47a6011169ca0ae2ede4b:edli_intent:...:buy_no"

condition_id        = "0x0d623c05b8d291022d79724641f16add2098cd994ca3da6590831d088250d59b"
YES-token           = "94919691709339926248609367448320440419051501993103034121677455440943187656517"
NO-token (asset_id) = "49128580161441347983725531208820142357219356580732147594666453507088262789094"
direction           = "buy_no"
filled_size         = "5.07"
fill_price          = "0.36"
trade_id            = "cf967209-599e-4813-b5c3-ee3c9221013b"
strategy_key        = "opening_inertia"

# MISSING — must obtain from Polymarket API:
VENUE_ORDER_ID      = unknown (SubmitUnknown left side_effect_known=False; no venue_order_id persisted)
```

**The blocking unknown:** The `SubmitUnknown` event has no `venue_order_id`
(`side_effect_known=False`). The Polymarket REST API `/data/trade?id=cf967209-...`
or `/data/order?asset_id=49128580...` must be queried to get the order_id that
produced the confirmed trade `cf967209-...`.

---

## 7. Connection / Lock Semantics

| Operation | Connection | Lock Required |
|---|---|---|
| `append_reconcile_recovered_fill` | `get_world_connection(write_class="live")` | No explicit mutex; world is single-writer via write_class |
| `materialize_position_current_from_edli_fill` | `get_trade_connection_with_world_required(write_class="live")` | No `world_write_lock` (bridge only reads world, writes trades) |
| `append_trade_fact` | zeus_trades.db connection | SAVEPOINT only |
| `append_position_lot` | zeus_trades.db connection | SAVEPOINT only |
| `append_many_and_project` | zeus_trades.db connection | SAVEPOINT (nested-safe) |

INV-37 constraint: every read and write in `materialize_position_current_from_edli_fill`
must happen on the **single** `bridge_conn`. The canonical write path nests its own
SAVEPOINT. No independent connections opened inside the bridge. The caller owns the
commit boundary (single `bridge_conn.commit()` after `materialize_...` returns).
