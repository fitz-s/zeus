# ADVERSARIAL REVIEW — EDLI presence resolver (boot crash-loop fix)

- Created: 2026-06-16
- Reviewer: critic (read-only, adversarial)
- Surface (T0 live-money, state-mutating at daemon boot):
  - `src/execution/edli_presence_resolver.py` (NEW, full file)
  - `src/execution/edli_absence_resolver.py` (`boot_auto_resolve_stuck_unknowns` ladder change)
- Canonical deps audited: `src/events/live_order_reconcile.py`,
  `src/events/edli_position_bridge.py`, `src/events/live_cap.py`,
  `src/engine/event_reactor_adapter.py` (PreSubmitRevalidated/SubmitPlanBuilt emission),
  `src/main.py` (boot order 9388/9432; durable bridge scan 8137-8345),
  Polymarket CLOB trade schema (docs.polymarket.com get-trades / L2 methods).

## VERDICT PER FILE

- `edli_presence_resolver.py` — **LOOP-BACK** (1 CRITICAL, 2 MAJOR)
- `edli_absence_resolver.py` (ladder change) — **ACCEPT-WITH-RESERVATIONS** (1 MAJOR is shared with the ladder; logic otherwise sound)

Net: **LOOP-BACK.** The cost-basis / share count can be silently corrupted on the
exact maker-fill class this fix targets, and there is zero test coverage.

---

## SETUP NOTE (verify before re-reviewing)

The code under review lives in the MAIN tree `/Users/leofitz/zeus` (uncommitted:
`edli_presence_resolver.py` is untracked; `edli_absence_resolver.py` is modified-but-
unstaged). The review worktree `qkernel-rebuild` is on branch `claude/qkernel-rebuild`,
a DIFFERENT lineage where the presence resolver does not exist and the absence resolver
is the older 13926-byte version. This review was performed against the MAIN-tree files.
The fix is NOT yet committed on any branch — confirm it lands on the live daemon's
branch (`live/iteration-2026-06-13`) before relying on it.

---

## Pre-commitment predictions vs findings

Predicted (before reading): (1) top-level maker_address vs maker_orders[] attribution
confusion; (2) cost-basis inversion taker-price vs maker-price; (3) idempotency on
re-run; (4) fail-closed on genuine absence; (5) payload field completeness.
Outcome: (1) and (2) CONFIRMED as the same CRITICAL defect (the TAKER-leg branch).
(3) is safe (selection by pending_reconcile + bridge trade_id dedup). (4) is safe.
(5) surfaced a MAJOR cross-event-source identity dependency.

---

## CRITICAL — `_our_fill_legs` TAKER branch double-counts and corrupts cost basis on every maker fill

File: `src/execution/edli_presence_resolver.py:95-109` (TAKER leg) interacting with
`:110-126` (MAKER legs) and `build_presence_proof` aggregation `:166-171`.

Authoritative Polymarket trade schema (docs.polymarket.com — get-trades / L2 methods):
top-level trade fields are the TAKER/matched perspective —
`taker_order_id` (taker side), `price` ("price at which the trade was matched"),
`side` (taker side), `size` (trade size); `maker_address` = "on-chain address of the
maker"; `trader_side` ∈ {TAKER, MAKER} = whether the AUTHENTICATED USER is taker or
maker. A maker's OWN economics live in `maker_orders[]` (`order_id`, `maker_address`,
`price`, `matched_amount`, `asset_id`).

The incident is a MAKER fill (`trader_side=MAKER`; our order `0x5ce1f9da…` rested as
maker and filled — per the plan-evidence doc). In that trade:
- top-level `maker_address` == our funder `0x6a09…` (we are the maker of record), AND
- top-level `asset_id` == our token `9491…`.

So the TAKER branch guard at `:96-99` (`asset_id == tok AND maker_address == funder`)
**fires on our own maker fill**. It then appends a "TAKER" leg carrying:
- `price = top-level trade price` (the matched/taker price — NOT necessarily our 0.64),
- `size = top-level trade size` (the taker's total matched size across ALL makers),
- `venue_order_id = taker_order_id` (the COUNTERPARTY's order id).

The MAKER branch ALSO fires (`:111-115`) and appends our true leg
(`price=0.64`, `size=5.07`, `venue_order_id=0x5ce1f9da`).

Dedup at `:151-159` is keyed `(trade_id, venue_order_id)`. The two legs have DIFFERENT
venue_order_ids (counterparty taker id vs our maker id), so **both survive**. Then
`build_presence_proof` sums them (`:166-171`):
`total_size = top_level_size + 5.07`, `avg_fill_price = notional-weighted blend of the
taker price and 0.64`. The recovered-fill payload (`:284-285`) carries these AGGREGATED
values, and the position bridge reads `filled_size`/`avg_fill_price` verbatim
(`edli_position_bridge.py:373,376`). The bridge's own trade_id dedup does NOT save you:
the corruption is already baked into the single recovered-fill payload's economics.

Result on a multi-maker taker, or whenever top-level size ≠ our matched_amount: the
position registers with INFLATED shares and a WRONG cost basis — the exact "mis-record a
position / wrong cost basis" failure this review hunts for. Even in the benign
single-maker case where top-level size==5.07 and price==0.64, the shares double to ~10.14
unless the values coincide AND dedup collapses them (it does not).

This is also the precise attribution method the canonical reconcile path deliberately
AVOIDS: `exchange_reconcile.py` and `polymarket_user_channel.py` attribute OUR leg by
matching our submitted ORDER ID against `taker_order_id` / `maker_orders[].order_id`
(`exchange_reconcile.py:3871-3953`, `polymarket_user_channel.py:280-326`), never by
top-level `maker_address`. The presence resolver invents a new, weaker attribution.

Confidence: HIGH (schema-verified; canonical-code-verified).
Why it matters: T0 live money. Wrong shares/cost-basis flow into position_current →
exit sizing, settlement grading, and P&L are all wrong; a doubled share count books a
phantom $3+ position and mis-grades the real one.

FIX:
1. Drive role off `trader_side`, not `maker_address`. Build OUR legs by matching our
   intended `venue_order_id`/`taker_order_id` against the trade's `taker_order_id`
   (when `trader_side=="TAKER"`) and against each `maker_orders[i].order_id`
   (always, for our maker legs) — mirror `exchange_reconcile._trade_order_ids` /
   `_selected_maker_order`. Do NOT use top-level `maker_address` as the ownership test.
2. Take economics from the matched leg: for a maker, `maker_orders[i].price` +
   `matched_amount`; for a taker, top-level `price` + `size` ONLY when our order id
   IS the `taker_order_id`. Never append both a maker leg and a top-level "taker" leg
   for the same physical fill.
3. Add the assertion that our SubmitPlanBuilt `venue_order_id`/intent is among the
   trade's order ids before attributing (presence requires id-level ownership, not
   just wallet+token), preserving the shared-wallet antibody at the strength the
   canonical path already uses.

---

## MAJOR — Position never materialises on the crash-looping boot; depends on a LATER pass + cross-event identity

File: `src/main.py:9388` (`_edli_boot_fill_bridge_recovery()`) runs BEFORE
`src/main.py:9432` (`_assert_edli_stage_readiness` → `boot_auto_resolve_stuck_unknowns`
→ `resolve_presence`). The presence resolver only appends `UserTradeObserved
(FILL_CONFIRMED)` + `Reconciled` + cap CONSUME; it does NOT call
`materialize_position_current_from_edli_fill` itself. The boot bridge scan that DOES
materialise the position already ran (at 9388) BEFORE the FILL_CONFIRMED event existed,
so on the boot that clears the readiness gate the position is still NOT in
position_current. It only materialises on (a) the next per-cycle reconcile durable scan
(`main.py:8563`, gated by `_edli_user_channel_reconcile_runtime_enabled` — disabled by
default unless `edli_user_channel_reconcile_enabled`), or (b) the NEXT boot.

Consequences:
- The plan-evidence doc's claimed live signal ("the 5.07-NO position appears in the
  position ledger" on the repaired boot) does NOT hold on that same boot. Readiness
  clears, daemon proceeds, but the position is invisible to exit/harvest/redeem until a
  later pass. If the per-cycle reconcile job is not enabled, materialisation waits for
  the next restart — capital remains unmanaged in the interim.
- The bridge requires `PreSubmitRevalidated` to carry `city/target_date/bin_label/
  metric/unit/strategy_key` (`edli_position_bridge.py:_resolve_identity` raises
  `EDLI_BRIDGE_MARKET_IDENTITY_MISSING` / `_NOT_RUNTIME_LIVE` otherwise). The presence
  resolver reads identity from `SubmitPlanBuilt` (which carries only token/condition/
  direction/size), NOT from `PreSubmitRevalidated`. If the orphan's
  `PreSubmitRevalidated` lacks any required identity field, or the strategy_key is not
  runtime-live, the cap is ALREADY CONSUMED and pending_reconcile ALREADY cleared, but
  the position bridge raises forever and the position never materialises — and the
  readiness gate no longer flags it, so the orphan is silently unmanaged (worse than a
  loud crash-loop). For a normal weather order final_intent carries these, so it usually
  works, but the failure is silent and partial-completion is not atomic.

Confidence: HIGH (boot order line-verified; bridge identity reqs verified).
Why it matters: the fix's stated purpose is to MANAGE the orphaned position. Clearing
readiness without materialising the position on the same boot only half-solves it and
removes the very signal (pending_reconcile/RESERVED) that flagged the orphan.

FIX: have `resolve_presence` call `materialize_position_current_from_edli_fill` for each
resolved aggregate INSIDE the same `world_write_lock` txn (on a trade-conn-with-world,
INV-37), so the position is created atomically with the FILL_CONFIRMED/Reconciled/
CONSUME. If that is not feasible on the world-only connection, re-run
`_edli_boot_fill_bridge_recovery()` (or the durable scan) AFTER
`_assert_edli_stage_readiness` returns, and assert the position exists before declaring
readiness cleared. Either way, do not CONSUME the cap until the position is materialised.

---

## MAJOR — Zero test coverage for a T0 live-money mutation; promised tests do not exist

`tests/execution/test_edli_absence_resolver_boot.py` covers absence only. There is NO
test referencing `resolve_presence`, `build_presence_proof`, or `_our_fill_legs`
(grep over `tests/` = 0 hits). The plan-evidence doc
(`boot_presence_reconcile_2026-06-16.md`, "Test / verification") explicitly promises:
"Unit: presence resolver materialises FILL_CONFIRMED + CONSUMED on a matching CONFIRMED
trade; REFUSES (raises, falls through) on no-match / foreign-owner / absent trade." None
of these exist. The CRITICAL attribution defect would have been caught instantly by a
single multi-maker / trader_side=MAKER fixture.

Confidence: HIGH.
FIX: before merge, add unit tests with realistic `get_trades()` fixtures:
(a) trader_side=MAKER single maker == us → shares 5.07 @ 0.64 (NOT 10.14, NOT blended);
(b) trader_side=MAKER, our maker + a foreign co-maker on same token (shared wallet) →
only our matched_amount attributed; (c) foreign-owner-only trade → REFUSE (raise);
(d) absent trade → REFUSE; (e) multi-maker taker where top-level size > our leg → our
leg only. Assert the recovered-fill payload economics AND the materialised
position_current row.

---

## MINOR findings

1. `edli_presence_resolver.py:123` —
   `"fees": _f(mk.get("fee_rate_bps")) and 0.0 or 0.0`. This is a convoluted no-op that
   always yields 0.0 (and `fee_rate_bps` is a RATE in bps, not a fee amount, so reading
   it as a fee would be wrong anyway). Maker fills on Polymarket are typically fee-free,
   so 0.0 is benign, but write it plainly as `"fees": 0.0` with a comment. Also at
   `:107` the TAKER leg reads `trade.get("fees")` — the schema field is `fee_rate_bps`,
   not `fees`, so this is always 0.0 too. Fees are effectively hard-zeroed; acceptable
   for maker fills but make it explicit, not accidental.

2. `edli_presence_resolver.py:313-319` — `cap_ledger.consume(...)` is not wrapped; a
   second concurrent run (manual + boot) raises `LiveCapError("only RESERVED ... can be
   consumed")` inside `world_write_lock`, rolling back the whole txn. That is fail-closed
   (safe) but the absence path catches its analog differently; consider catching the
   already-CONSUMED case idempotently so a concurrent re-run is a clean no-op rather than
   a rollback that re-raises to the boot ladder.

3. `edli_presence_resolver.py:178` — `venue_command_state="FILLED" if total_size+1e-9 >=
   order_size else "PARTIAL"`. With the CRITICAL double-count, `total_size` is inflated,
   so this will report FILLED even for a genuine partial. Resolve the CRITICAL first;
   then PARTIAL is correctly derived from the true matched_amount. Verified that
   `append_reconcile_recovered_fill` accepts PARTIAL (`live_order_reconcile.py:124-128`
   only requires the 3 provenance fields), and PARTIAL does not chase a remainder because
   open_orders=0 on venue — OK once the size is correct.

---

## What's MISSING (gaps)

- No assertion that our intended order id appears in the trade's order ids before
  attributing — attribution rests entirely on wallet+token, the weakest signal and the
  one the canonical path explicitly does not trust alone.
- No atomic position materialisation (see MAJOR #1): cap CONSUME and pending_reconcile
  clear are not transactionally coupled to position_current existence.
- No guard that the resolved aggregate's `PreSubmitRevalidated` is bridge-materialisable
  BEFORE consuming the cap (silent-orphan risk).
- No persisted capture of the raw incident trade JSON (`cf967209…`) in the evidence dir
  to pin the actual top-level size/price for the single-maker case — the one artifact
  that would let you confirm the benign-vs-corrupt boundary empirically. Capture it.
- No idempotency test for two presence runs (selection-level safety is sound but untested).

## Confirmed SOUND (no defect)

- Fail-closed on genuine ABSENCE: `_our_fill_legs` returns [] when no leg matches our
  funder+token; `build_presence_proof` raises ("no CONFIRMED trade owned by our funder";
  `:160-164`); the ladder catches and the boot fails closed (`edli_absence_resolver.py:
  364-371`). Presence correctly REFUSES when the order never landed. PASS.
- Absence-then-presence ladder is state-clean: absence builds ALL proofs before any write
  and raises on matching exposure before writing, so falling through to presence leaves
  no partial absence writes (`edli_absence_resolver.py:230` builds proofs;
  `build_absence_proof:172-173` raises pre-write). PASS.
- Cap semantics: CONSUMED (not RELEASED) is correct for a real fill; `consume()` requires
  RESERVED and the row IS RESERVED at apply time (readiness counts RESERVED;
  `live_cap.py:179-199`). PASS (modulo MINOR #2 concurrency).
- INV-37 / txn: all writes on ONE world connection under `world_write_lock(conn)`,
  caller-owned txn, no independent connection (`edli_presence_resolver.py:253-326`).
  PASS. (Caveat: the position bridge needs a trade-conn-with-world; see MAJOR #1 for
  where to run it.)
- Idempotency vs re-run: `_pending_aggregates` selects only `pending_reconcile=1`, so a
  resolved aggregate is not re-processed; bridge dedups by trade_id; second boot does not
  re-fire the ladder. PASS.

---

## Multi-perspective notes

- EXECUTOR: cannot tell from the code that the position won't appear this boot — the
  comment says it materialises "through the canonical fill->position bridge" but that
  bridge already ran. Misleading.
- STAKEHOLDER: the stated goal (manage the orphaned 5.07-NO position) is not fully met on
  the repaired boot; readiness clears but management is deferred.
- SKEPTIC: strongest argument the approach is wrong — it attributes by wallet+token when
  the entire rest of the codebase attributes by order id; the one novel decision in this
  fix is the one that breaks.

## What would upgrade the verdict

LOOP-BACK → ACCEPT requires: (1) rewrite `_our_fill_legs` to attribute by order id and
take maker economics from `maker_orders[]` (CRITICAL); (2) materialise the position
atomically with the cap CONSUME, or assert it before clearing readiness (MAJOR #1);
(3) add the promised unit tests, including a trader_side=MAKER multi-maker fixture
(MAJOR #2). MINORs are nice-to-have.

Review mode: escalated to ADVERSARIAL after the CRITICAL surfaced (1 CRITICAL + 2 MAJOR
trips the threshold); adjacent canonical attribution paths were pulled in to confirm the
defect is a true deviation, not a house style.
