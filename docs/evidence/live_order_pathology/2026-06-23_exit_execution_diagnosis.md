# Exit-Execution Diagnosis — why correct exits do not reach a fill

```
# Created: 2026-06-23
# Last audited: 2026-06-23
# Authority basis: live-chain evidence (zeus_trades.db position_current/position_events/venue_commands)
#                  + executable source (src/execution/exit_lifecycle.py, src/execution/executor.py,
#                  src/contracts/executable_market_snapshot.py, src/main.py ws_gap path).
#                  Read-only DB inspection, ?mode=ro, busy_timeout=10000. No code changed.
```

## TL;DR

The exit **decision** layer works: held positions are re-evaluated every ~2 min
(`MONITOR_REFRESHED` floods) and real physics-reversal triggers fire exit intents —
`CI_SEPARATED_REVERSAL` (300), `DAY0_OBSERVATION_REVERSAL` (52), `WHALE_TOXICITY` (24),
`MODEL_DIVERGENCE_PANIC` (13), `DAY0_HARD_FACT_BIN_DEAD` (20), `SETTLEMENT_IMMINENT` (11).
That is exactly the operator's "re-evaluate and sell on reversal" mission, and it is alive.

The exit **execution** layer is where money is lost: of 421 `EXIT_INTENT` events only
**19 ever became `EXIT_ORDER_FILLED`** (and 5 `EXIT_ORDER_POSTED`); the rest produced
**5,898 `EXIT_ORDER_REJECTED`** rows. The single highest-leverage root cause is that the
exit lane **fails closed at the SELL-side executable-snapshot/venue-readiness boundary and
then escalates to `backoff_exhausted` → admin-close instead of persistently retrying until a
sell lands**. Every dominant reject string reduces to "could not produce a usable SELL
snapshot / the submit channel was gated, so the sell never reached the venue."

The `executable_snapshot_gate min_order_size 5` is **NOT** a blanket blocker. It is a
per-**share** floor that fires only on genuine sub-5-share dust (≈6 lifetime positions, all
< 5 shares: 1.007, 1.28, 1.304, 4.95). It cannot block a meaningful exit.

## 1. The live exit-execution path (file:line)

Monitor re-evaluation → exit decision → sell:

1. **Monitor re-evaluation & exit trigger** — `src/engine/monitor_refresh.py` runs each
   cycle, emits `MONITOR_REFRESHED`, and routes pending exits. Reversal triggers
   (`CI_SEPARATED_REVERSAL`, `DAY0_OBSERVATION_REVERSAL`, `WHALE_TOXICITY`,
   `MODEL_DIVERGENCE_PANIC`) become an `EXIT_INTENT`.
2. **exit_pending_missing escalation / chain-truth gate** —
   `src/execution/exit_lifecycle.py:954` `handle_exit_pending_missing()`. Queries on-chain
   CTF balance via `_query_ctf_balance` using a funder resolved from Keychain
   (`resolve_funder_address`, exit_lifecycle.py:988). balance==0 → void;
   balance≤dust (`_CHAIN_BALANCE_DUST_SHARES = 0.01`, line 766) → dust-hold; balance>dust →
   route to live evaluation (FIX 2a, lines 1031-1109).
3. **SELL-side snapshot capture (the choke point)** —
   `src/execution/exit_lifecycle.py:2264` `_latest_or_capture_exit_snapshot_context()`.
   Tries the latest fresh persisted snapshot (`_latest_exit_snapshot_context`,
   exit_lifecycle.py:2008-2057); if none, captures a **fresh** one from current CLOB via
   `capture_executable_market_snapshot(... execution_side="SELL")`
   (exit_lifecycle.py:2342-2354). **On any failure it returns `{}`** (lines 2299, 2317,
   2324, 2365, 2393) or `{"venue_read_transient": True}` (line 2385).
4. **Dust pre-gate** — `src/execution/exit_lifecycle.py:1651` `_below_snapshot_min_order_error()`
   (defined line 1415): rejects to `_mark_exit_dust_hold` only if
   `effective_shares < snapshot.min_order_size` (per-share, line 1418).
5. **Sell construction & submit** — `src/execution/exit_lifecycle.py:1813`
   `place_sell_order(...)`, which routes into `src/execution/executor.py`. The U1 gate runs
   `assert_snapshot_executable` and the capability component
   `_capability_component("executable_snapshot_gate")` (executor.py:3505 / mirror 4591); on
   `MarketSnapshotError` it returns `status="rejected", reason="executable_snapshot_gate: {exc}"`
   (executor.py:3516 / 4607).
6. **U1 snapshot assertions** — `src/contracts/executable_market_snapshot.py:401`
   `assert_snapshot_executable()`:
   - line 416 `venue command requires executable market snapshot_id` (snapshot is None → empty context),
   - line 447 `SELL command requires bid-side executable snapshot evidence` (`orderbook_top_bid is None`),
   - line 475-477 `size {x} is below snapshot min_order_size {min}` (the dust floor).
7. **Reject handling** — exit_lifecycle.py:1825-1859: a "below min_order_size" sell error →
   `_mark_exit_dust_hold` (line 1829); any other → `_mark_exit_retry` (line 1846), which arms
   exponential backoff; after `MAX_EXIT_RETRIES` → `backoff_exhausted` → `mark_admin_closed`
   (exit_lifecycle.py:1130-1140) with `EXIT_CHAIN_MISSING_REVIEW_REQUIRED`.
8. **Submit-channel gate (ws_gap)** — `src/main.py:1525` `_ws_gap_m5_reconcile_required()` /
   `src/control/ws_gap_guard.py`: a user-channel websocket disconnect sets
   `m5_reconcile_required=True`, which blocks submits (sells included) until an M5 reconcile
   clears it. Release path exists at `src/main.py:1718`
   `_release_ws_gap_blocked_exit_retries_after_m5_clear`.

## 2. Failure-mode quantification (real-chain)

`zeus_trades.db.position_events`, all-time `EXIT_ORDER_REJECTED` = 5,898 vs
`EXIT_ORDER_FILLED` = 19, `EXIT_ORDER_POSTED` = 5. Buckets by (reason-signature, status):

| count | signature | status | meaning |
|---|---|---|---|
| 5,218 | `EXIT_CHAIN_MISSING` | retry_pending | **HISTORICAL** legacy re-stamp loop, now resolved |
| 320 | `<other>` | retry_pending | mixed (mostly ws_gap, see below) |
| 81 | `executable_snapshot_gate` | retry_pending | empty/stale SELL snapshot |
| 79 | `COLLATERAL` | retry_pending | collateral refresh/check fail |
| 72 | `EXIT_CHAIN_MISSING` | backoff_exhausted | legacy tail |
| 55 | `insufficient` | retry_pending | ctf_tokens_insufficient |
| 45 | `DUST` | backoff_exhausted | genuine sub-5-share dust hold |
| ~6 | `below snapshot min_order_size` | backoff_exhausted | dust floor (all < 5 shares) |

Raw error-string frequency (top): `exit_pending_missing` 5,293 (legacy);
`ws_gap=DISCONNECTED:websocket_disconnect...m5_reconcile_required` 265;
`executable_snapshot_gate: venue command requires executable market snapshot_id` 70;
`ctf_tokens_insufficient` 52; `executable_snapshot_market_end` 12;
`executable_snapshot_gate: SELL command requires bid-side` 11.

### The legacy flood is already fixed (not the current defect)

`EXIT_ORDER_REJECTED(EXIT_CHAIN_MISSING)` per day collapsed after 2026-06-12:
06-09 = 1276, 06-10 = 733, 06-11 = 724, 06-12 = 369, then 06-13..06-22 ≤ ~70/day. The
2026-06-12 funder-from-Keychain fix (exit_lifecycle.py:981-992) + 2026-06-20 FIX 2a
(lines 1031-1109) closed the re-stamp loop. So `exit_pending_missing` / `EXIT_CHAIN_MISSING`
is **resolved**, not the live defect. The 24 `position_current.chain_state='exit_pending_missing'`
rows (~$121) are mostly stale settled exits (`order_status='filled'/'sell_filled'`,
`exit_reason='SETTLEMENT'`, age ~13 d) whose chain-state projection never closed — a
projection-close lag, not a live sell block.

### The CURRENT (post-06-12) sell-execution blockers, by reversal trigger

(`EXIT_ORDER_REJECTED` grouped by the firing `exit_reason` family)

- **`CI_SEPARATED_REVERSAL`** (300 intents): **270 = `ws_gap` / m5_reconcile_required**, 46 collateral, 13 reconcile_finding_threshold. The live-reversal sell is gated by the user-channel WS-disconnect submit lock.
- **`DAY0_OBSERVATION_REVERSAL`** (52): **40 = `venue command requires executable [snapshot]`** (empty SELL snapshot context), 8 collateral, 5 ws_gap.
- **`WHALE_TOXICITY`** (24): **22 = `venue command requires executable`**, 5 collateral.
- **`MODEL_DIVERGENCE_PANIC`** (13): **52 = `ctf_tokens_insufficient`** (wallet lacks the tokens to sell), 6 collateral, 3 below-min dust.

### Current held set that died into backoff (15 meaningful + 2 dust)

`order_status='backoff_exhausted'` = 17 positions, $106.62. Split by shares:
**meaningful (≥5 sh) = 15 positions, $106.61**; dust (<5 sh) = 2, $0.01. The meaningful
ones (Chongqing $20.70/30sh, Paris $11.34/18sh, Taipei $9.42/13.65sh, London $8.19/13sh,
Wuhan $8.04/12sh, Karachi $7.92/12sh, Seattle $6.86, Seoul $6.21, Shanghai, Singapore…)
all sailed past the dust floor and were rejected by `executable_snapshot_market_end` /
`clob_market_info` / `venue command requires executable` — i.e. the SELL snapshot could not
be captured (market already ended or CLOB read failed) by the time the exit reached submit.

## 3. Is `min_order_size 5` a blanket blocker? — NO

The gate is `submitted_size < snapshot.min_order_size` at
`src/contracts/executable_market_snapshot.py:475-477`, and `submitted_size` is **shares**
(`position.effective_shares`, exit_lifecycle.py:1417/1817), not USD. `min_order_size = 5`
means 5 **shares**, Polymarket's per-order minimum. Evidence: every "below min_order_size"
reject in the log is sub-5-share (`size 1.007`, `1.28`, `1.304337`, `4.95`); and 15 of 17
backoff positions hold ≥5 shares yet were never blocked by this floor. A meaningful $50
reversal (e.g. 100 shares @ $0.50, or even 8 shares @ $6) clears 5 shares trivially. **The
dust floor cannot prevent cutting a real loser.** The Wellington case
(`size 1.007 is below ... 5`) is a genuine 1-share dead-bin dust residue and the dust-hold is
correct behavior there.

## 4. Single highest-leverage root cause + proposed surgical fix

**Root cause (universal):** the exit lane only obtains a SELL-side executable snapshot at the
moment of submit, and that capture **fails closed by returning `{}`**
(`src/execution/exit_lifecycle.py:2299/2317/2324/2365/2393`) on the common live conditions —
WS-disconnect submit-lock (`ws_gap`), market-ended (`executable_snapshot_market_end`), or a
transient CLOB read (`clob_market_info`). The executor then rejects on
`assert_snapshot_executable` (`executable_market_snapshot.py:416/447`,
`executor.py:3516/4607`), and the rejected exit is sent to **exponential backoff →
`backoff_exhausted` → admin-close** (`exit_lifecycle.py:1130-1140`). So a *correct* reversal
exit that just needs to wait out a 30-second WS reconnect or a transient CLOB blip is instead
**permanently abandoned** after a bounded retry budget, and the position rides to settlement.
This is the opposite of the operator's "constantly re-evaluate and sell before the market
notices" mandate: the system gives up on the sell.

**Highest-leverage surgical fix location & approach:**

The `ws_gap` / `venue-readiness` rejections must NOT consume the bounded exit-retry budget
that terminates in admin-close. They are *channel-not-ready*, not *position-not-sellable*.

- **Primary (file:line):** `src/execution/exit_lifecycle.py` reject-routing at lines
  **1845-1859** (the generic `_mark_exit_retry` branch) and the chain at
  **1116-1155** (`handle_exit_pending_missing` legacy tail → `backoff_exhausted` →
  `mark_admin_closed`). Classify channel-readiness errors —
  `ws_gap=*/m5_reconcile_required`, `executable_snapshot_market_end` while the market is still
  pre-settlement-open, `clob_market_info`/`TransientVenueReadError`, and
  `venue command requires executable market snapshot_id` caused by an empty capture — as a
  **non-budget-consuming "channel_not_ready" retry** that does NOT increment
  `exit_retry_count` toward `backoff_exhausted`/admin-close. Keep the genuine terminal cases
  (true dead-bin no-bid, confirmed `chain_confirmed_zero`, sub-5-share dust) on the existing
  fail-closed path. This matches the existing intent of `venue_read_transient`
  (exit_lifecycle.py:1632) and the existing `_release_ws_gap_blocked_exit_retries_after_m5_clear`
  release (`src/main.py:1718`) — extend that "don't burn the budget on a channel gap"
  treatment to the snapshot-capture path so a reversal exit keeps retrying every cycle a bid
  exists, rather than dying after N transient gaps.
- **Why universal, law-respecting:** it is a single classification change in the exit
  reject-router that applies to *every* position and *every* reversal trigger; it adds **no
  cap, allowlist, throttle, shadow, or flag** (it removes a premature terminal abandonment),
  and it is pure live-chain logic (no backtest/replay). It does not weaken U1 — the sell still
  requires a real bid-side executable snapshot before submit; it only stops the engine from
  permanently retiring a sellable position because the channel was briefly unavailable.

**Secondary (smaller, independent):** `MODEL_DIVERGENCE_PANIC` exits failing
`ctf_tokens_insufficient` (52) are a chain-reconciliation/shared-wallet truth issue
(`src/state/chain_reconciliation.py`), not a snapshot issue — the local position believes it
holds tokens the wallet no longer has. That is a separate reconcile fix and should be handled
on the chain-truth lane, not bundled into the exit-retry classification fix above.

## Verification posture

All counts above are from read-only queries against the shared live DBs
(`/Users/leofitz/zeus/state/zeus_trades.db`) at 2026-06-23, opened `?mode=ro` with
`PRAGMA busy_timeout=10000`. No fix has been applied — operator verifies before any change.
The proposed fix is a localized reject-classification change in
`src/execution/exit_lifecycle.py` and must be proven by re-running the live exit lane and
confirming `EXIT_ORDER_FILLED` rises relative to `EXIT_ORDER_REJECTED(ws_gap|snapshot)` for
reversal-triggered exits, with no new double-submits (single-flight guard at
exit_lifecycle.py:1042-1081 preserved).
```
