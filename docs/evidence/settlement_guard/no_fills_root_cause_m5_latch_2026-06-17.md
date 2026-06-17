# No-fills root cause — M5 ws-gap submit latch frozen by a chain-confirmed-but-unjournaled fill (2026-06-17)

## Symptom
Daemon alive (PID 16301, `python -m src.main`, log growing), but **zero new venue_commands for ~4 hours**
(last at 00:59 UTC), spanning the 04:47 UTC restart. RULE 1: no-trade is OUR defect — root-caused below.

## The binding constraint is EMISSION, not belief
The q/belief lane is healthy — the reactor processes families and rejects them honestly (e.g. Singapore
buy_yes q_lcb 0.25 vs price 0.62 → ev −0.60, a correct no-edge reject). But ~half of every cycle's rejects are:

```
EDLI_LIVE_CERTIFICATE_BUILD_FAILED: PreSubmitRevalidated requires user_ws_status=OK
```

A candidate clears edge/FDR/trade_score, reaches pre-submit, then **cannot build a submit certificate**
because `user_ws_status != OK`. This is a hard, belief-independent wall on every otherwise-passing +edge candidate.

## The gate chain (all verified in code)
1. `live_order_aggregate.py:654` raises `PreSubmitRevalidated requires user_ws_status=OK` if not OK.
2. `main.py:7849` sets `user_ws_status = "OK" if user_ws_summary["allow_submit"] else "BLOCKED"`.
3. `main.py:7902` → `ws_gap_guard.summary()["entry"]["allow_submit"]`.
4. `ws_gap_guard.py:69` → `blocks_market()` returns True whenever `m5_reconcile_required=True`.
5. `m5_reconcile_required` clears only via `clear_after_m5_reconcile`, which **raises if any unresolved
   finding remains** (`ws_gap_guard.py:240`).

So **one** unresolved M5 finding latches ALL new submits closed.

## The one stuck finding
`state/zeus_trades.db::exchange_reconcile_findings`, `resolved_at IS NULL`, count = 1:
- `kind=position_drift`, `context=ws_gap`, recorded 02:55 UTC.
- subject token `8804…32593`, reason `exchange_position_differs_from_confirmed_trade_facts`.
- evidence: `exchange_size=10.86` vs `confirmed_journal=0`, `confirmed_wallet=0`.

## It is a REAL position, not stale-API noise
`position_current`: city **Seoul**, "lowest temperature 21°C June 18", **buy_no**, shares=10.86,
**chain_shares=10.86, chain_state='synced'** (chain reconciler `src/state/chain_reconciliation.py::reconcile`
— "chain is truth" — read on-chain balanceOf and confirmed 10.86), order_status=filled, chain_seen_at fresh.
On-chain truth (10.86) = exchange (10.86). The fill arrived during a user-channel ws_gap and was confirmed
ONLY on-chain; it was never written as a journaled "confirmed trade fact".

## Why it never auto-resolves (the resolver's blind spot)
`run_ws_gap_reconcile_and_clear` runs a fresh `run_reconcile_sweep` every cycle. Both the recorder
(`_record_position_drift_findings`) and resolver (`_resolve_position_drift_tokens_from_current_truth`) base
"wallet truth" on the **journal** (`_journal_positions_by_token`, confirmed-trade-facts), never on the
on-chain CTF balance (`position_current.chain_shares`). For this token exchange=10.86 but journal=0 → no
absorber matches → the drift is **re-recorded every sweep** → the latch can never clear. A hand-resolve of
the row is futile (re-recorded next cycle).

## Fix (antibody, durable) — `src/execution/exchange_reconcile.py`
New helper `_chain_confirmed_active_holdings_by_token`: on-chain-confirmed holdings for ACTIVE positions
(`phase='active' AND chain_state='synced' AND chain_shares>0`), keyed by the held outcome token
(`no_token_id` for buy_no), deduped by (token, order_id) like the terminal helper. Absorber added to BOTH the
recorder and resolver, right after the suppression check:

```
if chain_confirmed_size > 0 and _position_size_matches(exchange_size, chain_confirmed_size):
    resolve "position_drift_chain_confirmed_active_holding"; continue
```

The persisted `chain_shares` (the chain reconciler's data-api `/positions` read, `chain_state='synced'`) is
matched against the FRESH exchange `/positions` read at sweep time — the same surface, snapshot vs fresh, not
two independent oracles. The safety still holds: a real theft/loss/partial-drift surfaces FIRST in the fresh
read, lowering it below `chain_shares`, breaking the equality, so the finding stays OPEN (honest gate
preserved). Verified PASS by an independent opus reviewer (`verify_chain_confirmed_absorber_2026-06-17.md`).
The reviewer's optional "add chain_confirmed to the recorder `tokens` union" hardening was DECLINED: on a
transient fresh-exchange miss (exchange=0, on-chain=10.86) it would fall through to the journal absorber and
falsely resolve the finding as `position_drift_cleared`; keeping the token out of the union is fail-closed
(the latch waits one cycle for the next good read) and the live exchange read reliably returns 10.86.

## Tests
`tests/execution/test_chain_confirmed_active_holding_absorber.py` (6 tests):
record-skip, resolve-stuck (recorder + refresh), zero-unresolved latch-freedom, and two honest-gate negatives
(no on-chain holding / size mismatch → stays open). RED-on-revert verified (guard→False fails exactly the 4
chain-confirmed tests, honest-gate tests still pass). Reconcile suite 155/155, money-path + live-inference 345/345.

## Deploy
Restart the daemon → next M5 ws-gap sweep resolves the Seoul finding → unresolved=0 →
`clear_after_m5_reconcile` → `user_ws_status=OK` → +edge candidates can build certificates and fill.

## Secondary (NOT the binding blocker, noted for follow-up)
DB lock contention: `state/zeus_trades.db` is 27.6 GB with a 109 MB un-checkpointed WAL (autocheckpoint=1000
pages = 4 MB) → `refresh_pending_family_snapshots` frequently fails its first insert on `database is locked`
(`coverage=NONE`, `fresh_executable_city_count` oscillates 0↔21). This throttles how many families are
evaluated per cycle but does NOT by itself block submit; the M5 latch is the hard wall. Track separately.
