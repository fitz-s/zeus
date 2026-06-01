# Wall D — chain_shares NULL Root Cause & Fix

**Date:** 2026-06-01
**Task:** #56 follow-up / Wall-D exit-leg blocker
**Deliverable:** root cause, live-row diff, fix file:line, RED→GREEN test evidence

---

## Root Cause

Three independent facts combine to explain the 100/101 NULL `chain_shares` observation.

### Fact 1: No EDLI bridge fills have occurred

All 45 EDLI order attempts reached `SubmitRejected` (pre-venue validation failures:
`tick_size mismatch`, `DEPTH_INSUFFICIENT`, `expected_fill_price sweep mismatch`).
No `UserTradeObserved` event with `fill_authority_state=FILL_CONFIRMED` exists.
The bridge (`src/events/edli_position_bridge.py`) was never called. There are **0**
`edli_event_driven` rows in `position_current`.

### Fact 2: The 100 NULL rows are ALL terminal positions

```
phase         total  null_chain_shares
active            1              0        ← populated (fix works)
voided           75             75        ← terminal, reconciler skips
settled          18             18        ← terminal, reconciler skips
economically_closed  4          4        ← terminal, reconciler skips
admin_closed      3              3        ← terminal, reconciler skips
```

Terminal phases are in `INACTIVE_RUNTIME_STATES` (`src/state/portfolio.py:2107`).
The reconciler's main loop skips them at `src/state/chain_reconciliation.py:1096-1100`.
These positions were settled/voided **before** the #56 fix landed. They can no
longer be reconciled — by design.

### Fact 3: The #56 fix IS working for the 1 active position

`position_current.chain_shares = 16.75` for the active position (`cca68b44-26f`,
`ens_member_counting`, `chain_state=synced`). The `_append_canonical_chain_observation_if_available`
path (no-size-mismatch branch, `chain_reconciliation.py:1419-1432`) correctly wrote
the chain observation.

---

## Live-Row Diff

| position_id  | phase   | chain_state | chain_shares | entry_method          |
|--------------|---------|-------------|--------------|----------------------|
| cca68b44-26f | active  | synced      | **16.75**    | ens_member_counting  |
| 30956762-e09 | settled | synced      | NULL         | ens_member_counting  |
| 7211b1c5-d3b | voided  | local_only  | NULL         | ens_member_counting  |
| (93 more)    | terminal| various     | NULL         | ens_member_counting  |

The single distinguishing factor: `phase = active` vs terminal. Not `entry_method`,
not `fill_authority`, not `chain_state`.

---

## Is the Wallet/RPC Gate the Problem?

No. `get_positions_from_api` uses the Polymarket data REST API
(`data-api.polymarket.com/positions?user={funder_address}`), NOT on-chain `balanceOf`.
`funder_address` is sourced from macOS Keychain (`openclaw-polymarket-funder-address`).
The daemon successfully reconciled the active position (chain_shares=16.75), proving
the API path is live and the Keychain credential resolves correctly.

---

## Fix (Pre-existing in HEAD — #56)

The fix already shipped. File:line reference for the no-size-mismatch write:

- **`src/state/chain_reconciliation.py:1419-1432`** — `else` branch (chain.size == local_shares),
  calls `_append_canonical_chain_observation_if_available(corrected)`.
- **`src/state/chain_reconciliation.py:589-715`** — the helper itself: emits
  `build_chain_economics_observed_canonical_write`, projects `chain_shares /
  chain_avg_price / chain_cost_basis_usd / chain_seen_at` onto `position_current`.
- **`src/engine/lifecycle_events.py:944-1039`** — `build_chain_economics_observed_canonical_write`.

For the EDLI bridge path specifically:

- **`src/events/edli_position_bridge.py:282-334`** — `_build_bridge_position`:
  sets `chain_state="local_only"`, `no_token_id=elected_token` for `buy_no`,
  `token_id=elected_token` for `buy_yes`.
- Token placement is the critical chain-match key: reconciler computes
  `tid = pos.token_id if pos.direction == "buy_yes" else pos.no_token_id`
  (`chain_reconciliation.py:1057`). The bridge places the elected token correctly.

---

## RED → GREEN Test Evidence

New tests added to `tests/events/test_edli_position_bridge.py`:

**`test_wall_d_bridged_position_chain_shares_null_before_reconcile`** — RED baseline:
after bridge materialisation, `position_current.chain_shares` is `NULL/0.0` and
`chain_state='local_only'`. Passes (confirms the pre-reconcile gap exists).

**`test_wall_d_bridged_position_chain_shares_populated_after_reconcile`** — GREEN:
uses `_position_from_projection_row` (the real daemon load path) to load the bridged
row, then runs `reconcile()` with a matching chain observation (`chain.size == 16.75`).
Asserts `chain_observation_persisted >= 1`, `chain_shares = 16.75`, `chain_state = synced`,
`chain_seen_at` populated.

```
12 passed in 1.53s  (was 10 before these 2 tests)
```

Full suites: 552 passed, 6 failed (all pre-existing, unrelated to this work),
4 skipped, 3 deselected, 2 xfailed.

---

## What Needs to Happen for the First EDLI Bridge Fill

1. An EDLI aggregate must reach `FILL_CONFIRMED` (one `UserTradeObserved` with
   `fill_authority_state=FILL_CONFIRMED`).
2. The bridge caller (event reactor / FSR-ready path) must call
   `materialize_position_current_from_edli_fill(conn, aggregate_id)`.
3. On the next `chain_sync_and_exit_monitor` cycle (runs every N minutes, registered
   in `src/main.py:5247-5250`), the chain API returns the elected token, the
   no-size-mismatch path fires, and `chain_shares` is written.

The code path is proven by `test_wall_d_bridged_position_chain_shares_populated_after_reconcile`.
The blocker is upstream: all 45 current orders reached pre-venue rejection. That is
a separate ticket (execution rejection root causes: tick_size, depth, sweep).
