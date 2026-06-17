# M5 stuck-finding root cause + fix — terminal-position double-count freezes submit latch

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: R3 M5 reconcile (`src/execution/exchange_reconcile.py` header), INV-37, operator Tier-0 truth-reconciliation guardrails (reconcile-from-venue-truth, never overwrite real fill/position facts).
- Branch (live main tree): `live/iteration-2026-06-13`. **Not committed, daemon not restarted** (orchestrator owns deploy).

## TL;DR

The M5 submit latch was frozen by ONE unresolved `position_drift` finding —
but **NOT** the `fcdfec76` command in the brief. The real stuck finding is a
duplicate-position **double-count** of an intra-Zeus on-chain holding. The fill
in the brief (`fcdfec76`) is fully accounted; the latch is held by a *different*
token whose terminal `position_current` rows were summed twice.

Root cause: `_closed_position_token_holdings_by_token` SUMS `shares` across every
terminal `position_current` row for a token, even when multiple rows are the SAME
on-chain holding (same venue `order_id`). Fix: dedupe by `(token, order_id)` —
each distinct on-chain fill counted once; distinct orders still sum.

The fix makes the existing resolver branch
`position_drift_closed_position_token_holding` resolve the finding straight from
venue truth (the exchange's reported 5.07) with **no fact overwrite**.

---

## 1. Position-accounting verification

### 1a. The brief's `fcdfec76` fill — FULLY ACCOUNTED (not the blocker)

`command fcdfec76ee584c80` (BUY 9.81237993972495 @0.74, market 2549499, order
`0x41fe…f0b`, position `0c5e3773-ad6`):

- `venue_commands.state = FILLED`; `venue_trade_facts` → MATCHED/CONFIRMED full fill
  with tx_hash present (real on-chain money). ✔
- `position_current[0c5e3773-ad6]`: `shares=9.81`, `cost_basis=7.2594`,
  `entry_price=0.74`, `order_status=filled`, `fill_authority=venue_confirmed_full`,
  `chain_shares=9.81`. The fill IS recorded as a position. ✔
- `position_events` seq 3 `ENTRY_ORDER_FILLED` (14:38) → active; seq 4
  `CHAIN_SIZE_CORRECTED` (14:39) chain_reconciliation `chain_state=synced`,
  `chain_shares_after=9.81`. On-chain confirmed. ✔

So `fcdfec76` is **terminal at venue AND accounted as a 9.81@0.74 position**. It
is NOT the unresolved M5 finding. (It later flips to `voided` via
`PHANTOM_NOT_ON_CHAIN` chain-reconciliation churn, but that is a separate
chain-reconciliation concern and does not hold the M5 latch.)

### 1b. The ACTUAL unresolved M5 finding

`exchange_reconcile_findings` WHERE `resolved_at IS NULL`:

```
finding_id = 8ea59574-8b3f-4cb4-9d25-3e09f7debe5c
kind       = position_drift
subject_id = 94919691709339926248609367448320440419051501993103034121677455440943187656517   (a NO token)
context    = ws_gap
recorded_at= 2026-06-16T19:28:48Z
```

evidence_json:
```
exchange_size            = 5.07
expected_wallet_size     = 10.14   <-- 2x the real holding
closed_position_token_size = 10.14
confirmed_wallet_size    = 0
journal_size             = 0
reason = exchange_position_differs_from_expected_wallet_facts
closed_position_evidence_class = terminal_position_current_chain_holdings
```

### 1c. Why expected_wallet = 10.14 — duplicate position rows of ONE fill

Token `9491…517` has THREE `position_current` rows, all `no_token_id=9491…517`,
all `shares=5.07`, all `order_id=0x5ce1f9da…d18c1`:

| position_id | phase | chain_state | shares | order_status |
|---|---|---|---|---|
| `edlid7eae…d745` | voided | synced | 5.07 | filled |
| `aef7968f-6f3` | voided | synced | 5.07 | partial |
| `edli6301b…fa39` | pending_exit | synced | 5.07 | filled |

The closed-holding view (`phases {settled,admin_closed,voided}` ×
`chain_states {synced,exit_pending_missing}`) qualifies the **two voided** rows →
`5.07 + 5.07 = 10.14`.

But there is exactly **ONE underlying on-chain holding**:
- `venue_trade_facts` for `0x5ce1…`: a single `trade_fact_id=205`, `filled_size=5.07`,
  `state=CONFIRMED`, `tx_hash=0xd076cff5…d34f`, via command `6467b5c3fed84750`.
- `venue_order_facts` for `0x5ce1…`: matched 5.07 then EXPIRED (0.008 remainder).

The wallet holds **5.07 once**. The exchange correctly reports 5.07. Zeus's
expected-wallet computation double-counted the same fill because it summed two
position lifecycle rows that describe the same holding → eternal `position_drift`
→ M5 `unresolved_findings=1` → latch closed → `user_ws_status != OK` → every live
submit blocked by `EDLI_LIVE_CERTIFICATE_BUILD_FAILED: requires user_ws_status=OK`.

**Verdict: no real accounting gap.** The 5.07 holding is recorded (in fact
over-recorded). Nothing was untracked; nothing to paper over. The defect is a
read-only double-count in the expected-wallet view, not a missing position.

---

## 2. Resolver defect (file:line)

`src/execution/exchange_reconcile.py` → `_closed_position_token_holdings_by_token`
(was ~line 4360):

```python
out[token_id] = out.get(token_id, Decimal("0")) + amount   # SUMS every terminal row
```

This is the single source consumed by BOTH drift paths:
- `_record_position_drift_findings` (line 1875) — records/refreshes findings.
- `_resolve_position_drift_tokens_from_current_truth` (line 2495) — the refresh
  resolver the M5 latch calls every cycle.

Because `expected_wallet = available_wallet(0) + settlement(0) + closed(10.14) = 10.14`
and `exchange = 5.07`, none of the resolution branches match
(`5.07 != 0` and `5.07 != 10.14`), so the finding is re-recorded forever. This is
the exact disease family the file header already documents (the 2026-06-10
void-misbooking double-count) — but here there is **no operator external close**;
it is a pure intra-Zeus duplicate-position double-count, which the existing K=1
absorbers (gated on operator-ack / external-close) do not cover.

---

## 3. Fix — dedupe by on-chain holding identity `(token, order_id)`

`_closed_position_token_holdings_by_token` now groups rows by `(token, order_id)`
and takes the representative (max) share per group, then sums across **distinct**
orders:

```python
SELECT position_id, token_id, no_token_id, direction, shares, order_id   # + position_id, order_id
...
holdings_by_order: dict[str, dict[str, Decimal]] = {}
for row in rows:
    ...
    order_id = str(row["order_id"] or "").strip()
    group_key = order_id if order_id else f"__no_order__:{row['position_id']}"  # NULL → never collapse
    token_groups = holdings_by_order.setdefault(token_id, {})
    token_groups[group_key] = max(token_groups.get(group_key, Decimal("0")), amount)
out = {tok: sum(groups.values(), Decimal("0")) for tok, groups in holdings_by_order.items()}
```

(Full diff: `git diff src/execution/exchange_reconcile.py` — 25-line change in one
function.)

### Why this reconciles FROM venue truth (not an overwrite)

- The exchange's reported position (5.07) is the authority. The fix makes Zeus's
  expected-wallet view equal the single real on-chain holding so the EXISTING
  branch `closed_position_size>0 AND match(exchange, expected_wallet)` resolves the
  finding as `position_drift_closed_position_token_holding`.
- **No fact is mutated.** `venue_trade_facts` / `venue_order_facts` are append-only
  (DB triggers ABORT UPDATE/DELETE); `position_current` rows, shares, tx_hash, and
  cost basis are untouched. Only a read-only summation is corrected.
- Dedup key = `order_id` (the venue order that produced the fill), the on-chain
  holding identity. Same order ⇒ same holding ⇒ count once. Distinct orders ⇒
  distinct fills ⇒ still sum.

### Live verification against `state/zeus_trades.db` (after fix, in-process)

```
closed_position_holdings[9491…517]  BEFORE = 10.14   AFTER = 5.07
settlement = 0, confirmed_journal = 0, suppressed_external = False
=> expected_wallet = 0 + 0 + 5.07 = 5.07 == exchange 5.07
=> resolver hits 'position_drift_closed_position_token_holding' -> finding resolved
=> M5 unresolved_findings -> 0 -> latch opens -> user_ws_status=OK -> submits proceed
```

### Non-regression guard (also confirmed against live data)

Token `1139…946` has two terminal rows of 6.0 on **DISTINCT** orders
(`0x17b8…`, `0x56c0…`) → still sums to 12.0 after the fix (its findings were
already resolved; no regression). The audit of the whole closed-holding population
found exactly these two multi-row tokens; only `9491…517` (single order, summed
twice) was the bug.

---

## 4. RED-on-revert proof

New tests in `tests/test_exchange_reconcile.py`:

- `test_duplicate_terminal_positions_same_order_count_holding_once` — two terminal
  `voided` rows, SAME token + SAME order_id, both 5.07; exchange 5.07; asserts the
  finding resolves.
  - **On the pre-fix `sum()` code → FAILS** (`expected_wallet 10.14 != 5.07`,
    finding re-recorded; `assert not any(... position_drift ...)` → `assert not True`).
  - **On the fixed code → PASSES.**
- `test_distinct_orders_same_token_still_sum_terminal_holdings` — two terminal rows,
  DISTINCT orders, 6+6; exchange 12.0; asserts resolved.
  - **PASSES on BOTH pre-fix and fixed code** → proves the new duplicate test is a
    true regression guard, not over-fit to the fix (distinct orders must keep
    summing).

Revert proof captured by swapping the dedup body back to the legacy `sum()` and
re-running: duplicate test FAILED, distinct-orders test PASSED; restored fixed
version, both PASS.

---

## 5. Test evidence (fresh)

```
tests/test_exchange_reconcile.py ............................ 98 passed
tests/test_command_recovery.py .............................. 97 passed
tests/test_reconcile_operator_external_close.py
  + tests/execution/test_settled_external_absorber.py
  + tests/execution/test_terminal_chain_closed_phantom_absorber.py ... 23 passed
M5 / ws-gap / latch suite (refresh_unresolved_reconcile_findings,
  clear_after_m5_reconcile, fresh_reconcile_snapshot callers) ......... 130 passed
tests/money_path/ ................................................... 195 passed
```

All green. `.venv/bin/python` syntax + import of the modified module OK.

---

## 6. Immediate unblock — note for orchestrator

- **No manual data reconcile is required and none was performed.** With the fixed
  code deployed, the next M5 sweep auto-resolves finding `8ea59574` from venue
  truth (the `closed_position` branch), latch opens, submits proceed. The defect
  was a read-only computation, so correcting it is sufficient.
- The LIVE daemon is running the OLD in-memory code, so the latch stays closed
  until the orchestrator redeploys/restarts (I did not commit or restart per the
  guardrails). There is no safe data-only immediate unblock that doesn't either
  (a) violate the append-only fact triggers or (b) hand-resolve the finding and
  mask the class for any future duplicate token — so the durable code fix is the
  correct and only lever.
- `INV-37` not implicated: the change is read-only (a SELECT + in-memory sum); no
  cross-DB writes.

## 7. Files changed

- `src/execution/exchange_reconcile.py` — `_closed_position_token_holdings_by_token`
  deduped by `(token, order_id)` (+ inline antibody comment).
- `tests/test_exchange_reconcile.py` — two new tests (RED-on-revert + non-regression).
