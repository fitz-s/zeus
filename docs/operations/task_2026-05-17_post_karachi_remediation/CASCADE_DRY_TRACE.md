# CASCADE_DRY_TRACE — Karachi 5/17 Auto-Completion Audit
Created: 2026-05-17 | Authority: zeus-deep-alignment-audit-skill / operator brief

---

## §1 Pre-flight: settlement_commands row count + schema

```
sqlite3 -readonly state/zeus_trades.db
  "SELECT COUNT(*) FROM settlement_commands"  → 0
  "PRAGMA user_version"                       → 4
```

Schema matches migration `202605_add_redeem_operator_required_state.py` exactly:
CHECK includes `REDEEM_OPERATOR_REQUIRED`. user_version=4 confirms PR-126 migration
applied. The 0-row count is consistent with the migration comment: "Production state
at migration time: 0 rows" — the table has been empty since creation.

---

## §2 Enqueue path: harvester_pnl_resolver → settlement_commands

Entry function: `resolve_pnl_for_settled_markets(trade_conn, forecasts_conn)`
  `src/execution/harvester_pnl_resolver.py:38`

Guard: `ZEUS_HARVESTER_LIVE_ENABLED != "1"` → no-op early return
  `harvester_pnl_resolver.py:57`

Reads: `forecasts.settlements WHERE authority = 'VERIFIED'`
  `harvester_pnl_resolver.py:72–81`

On VERIFIED rows → calls `_settle_positions(trade_conn, portfolio, city, ...)`
  `harvester_pnl_resolver.py:139`

`_settle_positions` is in `src/execution/harvester.py:2016`. Inside, for each
matching position with `exit_price > 0` and `pos.condition_id` set:
  `harvester.py:2181–2191` — calls `enqueue_redeem_command(conn, condition_id=...,
  payout_asset="pUSD", ...)`

`enqueue_redeem_command` at `harvester.py:555` calls `request_redeem(...)` from
`src/execution/settlement_commands.py` → INSERTs row in `settlement_commands` with
state `REDEEM_INTENT_CREATED`.

Scheduler: `src/main.py:1385` — `_harvester_cycle` runs at `"interval", hours=1`.
(grep-verified: `grep -n "add_job" src/main.py` returns this line; hook line-count was stale.)

---

## §3 Adapter return path

`_redeem_submitter_cycle` polls `settlement_commands WHERE state IN
(_SUBMITTABLE_STATES)` every 5 minutes (`main.py:1403`).

For each row: calls `submit_redeem(command_id, adapter, ..., conn=conn)`.
`src/execution/settlement_commands.py:324`.

Inside `submit_redeem`: calls `adapter.redeem(condition_id)`.
`src/venue/polymarket_v2_adapter.py:611` — returns
`{"errorCode": "REDEEM_DEFERRED_TO_R1"}` (stub; SDK has no V2 redeem surface).

State transition at `settlement_commands.py:411`:
  `raw_payload.get("errorCode") == "REDEEM_DEFERRED_TO_R1"` → `state_after =
  REDEEM_OPERATOR_REQUIRED`. Log line emitted: `[REDEEM_OPERATOR_REQUIRED]
  command_id=... condition_id=...`.

Full state path:
  `REDEEM_INTENT_CREATED` (enqueue_redeem_command, harvester.py:2183)
  → `REDEEM_SUBMITTED` (submit_redeem enter, settlement_commands.py:~370)
  → `REDEEM_OPERATOR_REQUIRED` (DEFERRED_TO_R1 stub, settlement_commands.py:413)

No `REDEEM_DEFERRED_TO_R1` state exists in the DB CHECK; the errorCode triggers the
`REDEEM_OPERATOR_REQUIRED` transition directly.

---

## §4 Operator CLI gate

`scripts/operator_record_redeem.py` — NORMAL mode requires source state
`REDEEM_OPERATOR_REQUIRED`, accepts `--condition-id` + `--tx-hash` argv.
Transitions to `REDEEM_TX_HASHED`. Idempotent: same hash on TX_HASHED → exit 0
no-op. Different hash → exit 6 reject.

Operator invocation:
```
python scripts/operator_record_redeem.py \
  --condition-id <condition_id> \
  --tx-hash <0x...64hex>
```

---

## §5 Current cascade stage (live DB)

```
SELECT phase, COUNT(*) FROM position_current GROUP BY phase
  active       5
  day0_window  1
  voided       52
```

Zero rows in `settled`, `redeemed`, `redeem_pending`. No position has reached the
`settled` phase since the system went live.

---

## §6 Karachi-specific cascade gating

```
SELECT position_id, phase FROM position_current WHERE position_id='c30f28a5-d4e'
  c30f28a5-d4e | day0_window
```

Karachi is in `day0_window`. Chain state: `synced`. Order status: `partial`.
`_settle_positions` predicate (harvester.py:2050–2064): loads `position_current`
phase by `(city, target_date)` and skips positions already in terminal DB phase.
`day0_window` is NOT a terminal phase — it will NOT be skipped.

Forecasts.settlements for Karachi 2026-05-17: **0 rows** (empty query). UMA has not
resolved yet; the truth writer has not written a VERIFIED row for today's market.

---

## §7 Verdict

**VERDICT: (a) — INNOCUOUS.**

The 0-row `settlement_commands` table reflects that NO position has transitioned to
`settled` phase since PR-126 (2026-05-15). The Karachi position is currently in
`day0_window`. The VERIFIED settlement row for Karachi 2026-05-17 does not yet exist
in `forecasts.settlements` — UMA has not resolved.

The enqueue path is fully wired and sound:
1. UMA resolves → truth writer writes VERIFIED row in forecasts.settlements
2. `_harvester_cycle` (hourly, `main.py:1385`) reads it → `_settle_positions` →
   `enqueue_redeem_command` → `settlement_commands` INSERT (REDEEM_INTENT_CREATED)
3. `_redeem_submitter_cycle` (every 5 min, `main.py:1403`) picks it up → adapter
   returns DEFERRED_TO_R1 → state → REDEEM_OPERATOR_REQUIRED + log emitted
4. Operator sees log, runs `operator_record_redeem.py --condition-id ... --tx-hash ...`
5. State → REDEEM_TX_HASHED (cascade liveness contract satisfied; no manual completion
   of the redeem intent itself)

---

## §8 Karachi 7h plan

**Operator action = NONE pre-UMA.** Let the cascade fire automatically.

Post-UMA checklist (no code changes needed):
1. Monitor `logs/zeus-live.err` for `[REDEEM_OPERATOR_REQUIRED] command_id=...` line
   — appears within ~65 min of UMA write (up to 1h harvester tick + 5 min submitter)
2. Once logged: run `python scripts/operator_record_redeem.py --condition-id
   0xc5faddf4810e0c14659dbdf170599dcb8304ef42afcccb84992b4d8fcb0f44ae
   --tx-hash <tx_from_polymarket_ui>`
3. Confirm `state/zeus_trades.db settlement_commands` row is `REDEEM_TX_HASHED`

No code fix required. Cascade chain is structurally sound.
