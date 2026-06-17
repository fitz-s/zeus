# ARCH_PLAN_EVIDENCE — boot crash-loop: presence-reconcile the #122-orphaned maker fill

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: GOAL #83 (continuous settlement-graded fills) + RULE 1 (a "no fill"
  symptom is OUR defect until settlement proves otherwise) + #122 db-lock root cause +
  the existing absence-resolver boot antibody (task #48, `edli_absence_resolver.py`).
- Capability touched: live-order aggregate truth + cap ledger + position materialisation
  (T0, reversibility = state mutation under `world_write_lock`; on-chain position already
  exists — this records it, it does NOT place an order).

## Defect (live, settlement-graded — the session-long "no fills")

The daemon `com.zeus.live-trading` was crash-looping every ~30 s (launchd KeepAlive),
NEVER reaching the reactor trading loop, since at least 2026-06-16 ~09:41 UTC. Boot raised:

```
RuntimeError: EDLI_LIVE_READINESS_FAIL:EDLI_STAGE_UNRESOLVED_SUBMIT_UNKNOWN:1,EDLI_STAGE_LIVE_CAP_RESERVED:1
  at src/main.py:819 _assert_edli_stage_readiness
```

Root cause chain (fully closed, evidence below):

1. A live `buy_no` POST_ONLY_LIMIT order (token `9491…656517`, condition `0x0d623c05…`,
   limit 0.64, size 5.078125) was submitted ~09:39 UTC. Its post-submit **order-id
   recording hit `database is locked`** — the #122 zeus_trades.db write-contention —
   so the executor recorded `SubmitUnknown` (`reason_code="EXECUTOR_SUBMIT_UNKNOWN:database
   is locked"`, `venue_call_started=true`, `side_effect_known=false`) with **no
   venue_order_id**.
2. The order nonetheless reached the venue, **rested as a maker, and FULLY FILLED**:
   authenticated `get_trades()` returns trade `cf967209-599e-4813-b5c3-ee3c9221013b`
   (CONFIRMED, tx `0xd076cff5…`) whose maker sub-order `0x5ce1f9da…` is OURS — exact
   4-way match to SubmitPlanBuilt (asset `9491…656517`, outcome "No", side BUY, price 0.64,
   matched_amount 5.07). Funder = `adapter.funder_address = 0x6a096d5042cba434521E2cdb95A1fBa789a09b7f`
   = the trade's `maker_address`. **Definitively ours.** We hold **5.07 NO shares @ 0.64
   ≈ $3.25**.
3. Because there was no venue_order_id, the order/trade poller (polls by order id) never
   ingested the fill → `venue_trade_facts`, `position_lots`, `position_current`,
   `position_events` ALL have **0 rows** for it. The position is **unmanaged** (no exit,
   no settlement grading) — a silent P&L orphan.
4. Boot readiness (`_assert_edli_stage_readiness`, live mode) refuses to enter the trading
   loop while `pending_reconcile=1` + cap `RESERVED`. The boot antibody (#48,
   `boot_auto_resolve_stuck_unknowns`) runs the **authenticated-ABSENCE** resolver, which
   correctly **refuses** ("matching exposure; do not release cap") because the fill is real.
   But there is **no PRESENCE path** to resolve a confirmed fill → readiness stays blocked →
   `RuntimeError` → exit 1 → launchd restart → infinite crash-loop. Zero trading.

## Change

Two parts, both using one new core (the missing symmetric half of the boot resolver):

**Part B (antibody, the durable fix):** add a **presence-resolution** path to the boot
auto-resolver. When the absence proof refuses *because a matching CONFIRMED trade exists*
for our token at our own maker/taker order, build a **presence proof** (authenticated REST
trade fact for our token, owner = our funder) and reconcile the orphan to FILL_CONFIRMED via
the EXISTING canonical seam `live_order_reconcile.append_reconcile_recovered_fill` (built for
exactly this orphan class — the HK 30°C 2026-06-12 incident), materialise the position
through the canonical fill→position path, append `Reconciled(pending_reconcile=False)`, and
transition the cap `RESERVED → CONSUMED` (NOT released — the money was spent). Fail-closed is
preserved: it fires ONLY when (a) the only blockers are the stuck-unknown class AND (b) a
matching CONFIRMED trade owned by our funder is proven; any ambiguity, a non-matching trade,
a missing trade, or a foreign owner falls through to the original raise.

**Part A (one-time repair):** run Part B's resolver once against the current orphan to
register the 5.07-NO position and clear readiness, so the next launchd restart boots clean.
The repair IS the antibody's first (verified, dry-run-first) invocation.

## Reversibility / safety

- Records a position that ALREADY exists on-chain; it places NO order and cannot over-spend.
  Strictly more correct than the crash-loop (which leaves the position unmanaged).
- Cap → CONSUMED (the truthful terminal for a real fill), never RELEASED.
- Attribution is exact (token + price + size + side + funder-owner), so the shared-wallet
  caveat (operator co-trades non-weather markets on the same wallet) cannot cause a false
  match — the proof requires OUR funder as the matched order's owner on OUR weather token.
- All writes append-only through the canonical event-sourced ledgers under `world_write_lock`.
- Rollback: the one-time repair is event-sourced (no destructive writes); `git revert` the
  code; restart. The position, once registered, is managed by the normal exit/settlement loop.

## Test / verification

- Dry-run the presence proof + planned events; confirm `_readiness_counts` would become (0,0)
  and a `position_current`/`position_lots` row would materialise for token `9491…656517`,
  shares 5.07, entry 0.64, direction buy_no.
- Unit: presence resolver materialises FILL_CONFIRMED + CONSUMED on a matching CONFIRMED
  trade; REFUSES (raises, falls through) on no-match / foreign-owner / absent trade.
- Money-path regression (`tests/money_path/ tests/strategy/live_inference/`) introduces zero
  new failures.
- Live signal: after the one-time repair the daemon boots past `_assert_edli_stage_readiness`,
  the reactor cycles, and the 5.07-NO position appears in the position ledger and is
  settlement-graded when condition `0x0d623c05…` resolves.

## OUTCOME (verified live, 2026-06-16 06:17–06:23 local)

The antibody self-healed on the REAL boot path (no manual write): boot ladder logged
`boot absence resolution refused (matching exposure) -> attempting presence`, then
`PRESENCE_RESOLVED ... filled=5.07@0.6400`, `AFTER: unresolved_submit=0 reserved_cap=0`.
First two boots after deploy failed `UserTradeObserved requires raw_user_channel_message_hash`
(the aggregate ledger requires + dedupes on that field) — fixed by adding a STABLE
identity hash to the recovered-fill payload (idempotent). Verified after:
- daemon STABLE (PID 23224, no launchd churn); reactor scheduler running at 60s; zero
  `EDLI_LIVE_READINESS_FAIL` after 06:17:08.
- `position_current` = Houston 2026-06-17 "88-89°F" buy_no, shares 5.07, entry 0.64,
  size_usd 3.2448, phase active; `position_events`: OPEN_INTENT→POSTED→FILLED→
  **CHAIN_SIZE_CORRECTED**→MONITOR_REFRESHED×n. Exactly 1 FILL_CONFIRMED + 1 Reconciled
  (no double-append). Cap = CONSUMED.
- `maker_rest_escalation` independently re-confirms `order=0x5ce1f9da… PARTIALLY_MATCHED
  matched=5.07`.

## Reviewer reconciliation (presence_resolver_review_2026-06-16.md — LOOP-BACK)

The reviewer's CRITICAL (attribution inflates to ~10.14 shares via a spurious top-level
TAKER leg) DID NOT manifest — empirically the live position is 5.07 (single maker leg).
Root of the discrepancy: the reviewer assumed top-level `maker_address` is OUR funder on a
maker fill; in the real trade it is the COUNTERPARTY's wallet and top-level `asset_id` is the
COMPLEMENT token, so both `_our_fill_legs` guards (asset==our-token AND wallet==our-funder)
correctly excluded the top level. Reviewer's durable value = hardening: added a double-count
FAIL-CLOSED guard (`total_size > order_size*1.02 -> refuse`) so any future mis-attribution
refuses instead of recording an inflated position, plus `tests/execution/test_presence_resolver.py`
(8 tests: attribution, shared-wallet rejection, taker branch, absence fail-closed, double-count
guard). MAJOR #1 (materialise-after-readiness-clear silent-orphan risk) is mitigated by the
durable fill-bridge scan design — idempotent, self-healing every cycle, loud-on-failure — and
the position did materialise + chain-correct. Follow-up (not blocking): consider verifying
PreSubmitRevalidated identity completeness before consuming the cap.

Committed: a9317421ea on `live/iteration-2026-06-13`.
