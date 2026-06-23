# Entry Fill-Lane Diagnosis — ACKED entries CANCELLED instead of FILLED

- Created: 2026-06-23
- Authority basis: live-chain evidence (zeus_trades.db venue_commands/venue_order_facts, zeus-world.db edli_live_order_events) + executable source under the K4.0 REST-THEN-CROSS regime (docs/operations/consolidated_systemic_overhaul_2026-06-11.md). Read-only diagnosis; NO fix applied (operator verifies before any change).
- Money-path position: `execution` (entry placement -> fill). Upstream truth (belief/edge) is fresh post-66f28e3b; this defect is purely in the placement/fill lane.

## 0. Verdict (one line)

Entries are placed as **post-only GTC MAKER rests structurally below the ask** (`maker_limit_price = tick_down(min(bid+tick, ask-tick, reservation))`) and are **cancelled before they fill** by the `screen_resting_orders` pull loop (5-min CONFIRMED_VALUE_REFRESH + 20-min escalation deadline; a sub-300s BOOK_MOVED pull also exists) — a **rest -> pull -> re-rest** churn that yields **0 fills in the fair/ITM band over 24h** (0/15; only deep-OTM longshots fill, 4/35). Even a rest that survives to the deadline cannot cross because **FIX-B blocks the taker whenever ask+fee > q_lcb**. Dominant cancel cause = **(a) CANCEL-REPLACE CHURN (live: 10/10 cancels are `maker_rest_escalation` — 6 deadline + 4 CONFIRMED_VALUE_REFRESH) compounded with (b) NON-MARKETABLE MAKER PRICING + the FIX-B cross-block** — a structural maker-only trap. This is the codebase's own documented `MATCHED 19->0` pathology (event_reactor_adapter.py:10202-10219); the 2026-06-20 fix shifted the cancel horizon but did not restore fills.

## 1. The entry order lifecycle (decision -> ACK -> cancel), with file:line

### 1.1 Mode selection — the system is structurally a MAKER

`src/strategy/live_inference/mode_consistent_ev.py`:
- `select_rest_then_cross_mode()` (line 439) is the entry mode policy. Its **default verdict is `POLICY_REST_DEFAULT`** (line 612-614): "the genuine FIRST rest for a family … rests post_only GTC with the measured escalation deadline."
- `maker_limit_price()` (line 229-254) = `tick_down(min(bid+tick, ask-tick, reservation))`. The `ask - tick` cap makes a crossing maker limit **structurally unconstructable** (line 249-250): a BUY maker rest is placed **strictly below the best ask**, so it can only fill if the market trades DOWN into it. Module docstring (line 16-19) states the measured maker resting fill rate is "~10.8% … ZERO fills at p 0.30-0.80."
- Taker is gated three ways and is hard to reach:
  - `taker_spread_guard_reason()` (line 210) forbids crossing any book with relative spread > `TAKER_MAX_RELATIVE_SPREAD = 0.25` (line 58).
  - **FIX B q_lcb cap** (line 509-532): a taker cross is INADMISSIBLE whenever fresh `ask + fee > q_lcb`. For a mid-bucket buy at p≈0.55-0.69 against a typical 1-3c spread, the ask frequently sits at/above the conservative q_lcb, so the taker lane is blocked and the policy stays MAKER/no-trade.
  - `TAKER_OVER_MAKER_MARGIN = 0.15` hysteresis (line 87, applied 414-417) further biases toward MAKER.

### 1.2 The executor honors the maker/taker split

`src/execution/executor.py:450-490`: `post_only=True` requires `order_type in {GTC, GTD}` ("entry_resting_order_type_required", line 459-466); a taker requires `{FOK, FAK}`. So REST_DEFAULT => **post_only GTC** resting maker order. Submission/cancel primitives live in `src/venue/polymarket_v2_adapter.py` (`submit` 489, `cancel` 575); the post-only non-crossing guard is `_would_cross_post_only_book()` (src/engine/event_reactor_adapter.py:6614-6625: BUY would-cross iff `limit_price >= best_ask`).

### 1.3 Two independent cancellers race the resting maker order

**(A) The 90-second `screen_resting_orders` pull loop — the dominant churn driver.**
`src/events/continuous_redecision.py:1669-1743` (`screen_resting_orders`). For every OPEN maker rest it fires a `CANCEL_REPLACE` when ANY of:
- `screen_reprice()` BELIEF_WORSENING — belief decayed >= `BELIEF_REPRICE_DELTA = 0.03` on a new snapshot (line 1110-1157).
- **BOOK_MOVED** — the live best bid drifted >= `REST_BOOK_DRIFT_TICKS = 1.0` tick (0.01) above our limit (line 1707-1713). A one-tick book wiggle pulls the order.
- **CONFIRMED_VALUE_REFRESH** — the rest is older than `REST_VALUE_REFRESH_MIN_AGE_SECONDS = 300s` (5 min) and the held-side q_lcb still clears the live ask by `IMPROVE_DELTA = 0.02` (line 1714-1740).

Live wiring: `_edli_continuous_redecision_screen_cycle` is an APScheduler **interval job at `seconds=90`** (src/main.py:6249-6342 build; src/main.py:9669-9677 registration). It routes the pulls through the maker_rest_escalation cancel path (src/main.py:6353-6356, 6579-6584).

**(B) The 20-minute escalation deadline job.**
`src/execution/maker_rest_escalation.py` — `run_maker_rest_escalation_cycle()` (554) / `find_expired_resting_entries()` (79). Cancels ENTRY rests whose latest venue fact is OPEN (LIVE/RESTING/PARTIALLY_MATCHED, line 55) and older than `MAKER_REST_ESCALATION_DEADLINE_MINUTES = 20.0` (mode_consistent_ev.py:131). Live wiring: APScheduler interval `minutes=5` (src/main.py:9652-9660). NOTE the stale comments at main.py:6003 and main.py:9649 still claim a "2.0h MEASURED" deadline — the constant was cut to **20 min on 2026-06-16** (mode_consistent_ev.py:117-131); the deadline comment is drift.

### 1.4 Why the cancel does NOT convert into a fill (the loop)

`_family_rest_state()` (src/engine/event_reactor_adapter.py:10164-10335) decides, from venue truth, the two policy flags `(unexpired_family_rest, escalated_after_rest)` that gate the next decision:
- After a cancel, the NEXT reactor cycle is supposed to re-cert the family and cross as `POLICY_TAKER_ESCALATED_AFTER_REST` (mode_consistent_ev.py:570-572).
- BUT escalation only ARMS if the rest was cancelled-UNFILLED after `escalation_arm_floor_seconds = min(20min, 300s) = 300s` (line 10224-10227). A rest pulled by the **90s screen before 5 min** (BOOK_MOVED at 1 tick) does NOT arm escalation; it re-decides as a fresh `REST_DEFAULT` and is re-posted — the **rest->pull->re-rest loop**. The codebase documents this verbatim (line 10204-10209): *"60 of 64 terminal-unfilled rests were cancelled at 5-20min by the continuous-redecision SCREEN … BEFORE the 20-min deadline job could fire; each then re-decided as a fresh REST_DEFAULT … an infinite rest->pull->re-rest loop, 0 crosses, MATCHED 19->0 by 06-20."*
- Even when escalation DOES arm, **FIX B** blocks the cross if `ask + fee > q_lcb` (mode_consistent_ev.py:525-532), and lane 6a (line 598-610) then returns a NO-TRADE rather than re-resting. So a correct +EV entry whose ask sits a cent above its conservative q_lcb **never crosses and never fills** — it perpetually rests-and-cancels or no-trades.

The 2026-06-20 conversion fix (arming on the 300s screen floor) reduced — but did not eliminate — the loop: the **BOOK_MOVED 1-tick pull (line 1707-1713) still fires sub-300s with no minimum-age guard**, so a one-tick book wiggle inside the first 5 minutes still cancels-without-arming and re-rests. That residual sub-floor pull is the live churn that matches the current evidence (CANCELLED >> FILLED post-resume).

## 2. Dominant cancel cause from real-chain evidence

Real-chain counts already established this session:
- zeus_trades.db `venue_commands`, last 6h: **CANCELLED=35, ACKED=5, EXPIRED=3, FILLED=1** (≈80% cancelled, one fill).
- zeus-world.db `edli_live_order_events`, last hour post-resume: **13 VenueSubmitAcknowledged, 0 UserTradeObserved** (orders reach venue/ACK, no fill observed).
- The genuine mid-bucket entries (intent_kind=ENTRY, side=BUY, price 0.55/0.58/0.59/0.69, size ~32) are ALL in state=CANCELLED.

### 2.1 ACK->CANCEL timing quantification

`venue_commands` has only `created_at`/`updated_at` (no separate ack/cancel timestamps) — the per-order lifecycle truth lives in **`venue_order_facts`** (`observed_at`, `state`, `local_sequence`), which is exactly the table `find_expired_resting_entries` and `_family_rest_state` read. The timing pull (delta = terminal `observed_at` − ack `observed_at`, joined through `venue_order_facts`) and the cancel-reason attribution from `venue_command_events.payload_json` (`source` / `cancel_reason`) are reported in §2.1-DATA below.

**§2.1-DATA (venue_order_facts / venue_command_events, queried 2026-06-23 02:00:42 UTC):**

Fill rate by price band, 24h, BUY ENTRY (the decisive table):

| Band | Orders | Filled (matched_size>0) | Fill rate |
|---|---|---|---|
| Deep-OTM (price < 0.10) | 35 | 4 | 11.4% |
| Fair (0.40-0.60) | 4 | 0 | **0.0%** |
| ITM (0.60+) | 11 | 0 | **0.0%** |
| **All BUY ENTRY** | 50 | 4 | 8.0% |

=> **Every genuine mid-bucket / favorite entry (price 0.40-0.80, the +EV picks) got ZERO fills in 24h.** The only fills are deep-OTM longshot churn (4/35), which is not the +EV book.

Cancel-reason attribution (CANCEL_REQUESTED events, last 6h; parsed from `venue_command_events.payload_json`): **10 total, 10/10 `source=maker_rest_escalation`**, split:
- `BLANK_DEADLINE` (the 20-min escalation job, blank cancel_reason): **6**
- `CONFIRMED_VALUE_REFRESH` (the 5-min screen value-refresh pull): **4**
- `BOOK_MOVED`: 0; `BELIEF_WORSENING`: 0 (in THIS 6h window).

Live state at query time: 20/50 ENTRY orders still `LIVE` with `max_matched=0`; ~12 rows show a mixed `LIVE`+`CANCEL_CONFIRMED` fact pair on the same command_id (the serial re-rest / duplicate-fact signature — same family re-posted, prior leg cancel-confirmed). Per-row ACK->CANCEL second deltas could NOT be cleanly computed because the duplicate fact rows carry identical `observed_at` timestamps (a fact-ingestion duplication — a secondary data-quality issue; it does not change the fill verdict, read from `matched_size`).

**Honest refinement of §0:** in THIS 6h sample the cancels are driven by the **CONFIRMED_VALUE_REFRESH 5-min screen pull (4) + the 20-min deadline job (6)** — i.e. predominantly **(a) CANCEL-REPLACE CHURN at the 5-20 min horizon**, NOT the sub-300s BOOK_MOVED pull (0 observed this window). The BOOK_MOVED sub-floor pull was the dominant driver in the 06-19/06-20 death-line evidence (event_reactor_adapter.py:10204-10209); the 2026-06-20 arming fix shifted the live cancels onto the 5-min CONFIRMED_VALUE_REFRESH + 20-min deadline, but the OUTCOME is unchanged: **0 fills in the fair/ITM band**, because both the 5-min value-refresh pull and the deadline cancel pull a non-marketable maker rest before it fills, and FIX-B still blocks the escalated cross. The leverage point therefore generalizes to **every sub-deadline pull (BOOK_MOVED and CONFIRMED_VALUE_REFRESH alike) on top of the maker-only placement**, not BOOK_MOVED alone (see revised §4).

### 2.2 Marketability test (order limit vs market at submit)

`maker_limit_price` guarantees `limit <= ask - tick` for every REST_DEFAULT order, so EVERY genuine entry is, by construction, a **non-marketable maker** sitting below the ask. This is proven structurally from the limit-price formula (mode_consistent_ev.py:249-250) and corroborated by the band-level fill table: the fair/ITM orders (price 0.40-0.80) sit below the ask, the market never traded down into them, and **0/15 filled in 24h** while `max_matched=0` held on every still-LIVE rest. (A per-order best_bid/best_ask snapshot from token_price_log was not separately tabulated in this pull; the structural guarantee + the 0/15 fair-ITM fill outcome already establish non-marketable maker pricing. A direct per-order book overlay can be added on operator request, but is not load-bearing — the limit-formula proves the order is sub-ask by construction.)

## 3. Maker vs Taker determination

**Entries are placed MAKER (post-only resting), not taker.** Evidence:
- Policy default is `POLICY_REST_DEFAULT` -> `PLACEMENT_MAKER` -> post_only GTC (mode_consistent_ev.py:612-614, 94).
- Executor enforces post_only => GTC/GTD (executor.py:458-466).
- Live ACK-without-fill (13 ACK / 0 UserTradeObserved in 1h; 0/15 fair-ITM fills in 24h) is the signature of resting maker orders that never get lifted.
- Taker is reachable only via the narrow escalation/event-end/fleeting lanes (mode_consistent_ev.py:570-596) AND must clear the FIX-B `ask+fee <= q_lcb` cap (line 525-532); for the observed mid-bucket books that cap is usually violated, so the taker lane is structurally starved.

Combined with the cancel-replace screen (90s scheduler tick; live cancels observed at the 5-min CONFIRMED_VALUE_REFRESH and 20-min deadline horizons), this is the textbook "maker-only with aggressive cancel-replace structurally prevents fills" case the task hypothesized.

## 4. Single highest-leverage root cause + proposed surgical universal fix

### Root cause
The entry policy is **maker-only by default (REST_DEFAULT post_only GTC, structurally sub-ask) whose resting orders are pulled by a cancel-replace screen BEFORE they fill, and whose only escape-to-fill (the escalated taker cross) is then blocked by FIX-B whenever the ask sits a cent above q_lcb.** The net is a closed trap: a correct +EV entry can only rest-unfilled -> get pulled -> re-rest (or no-trade). Live proof: **0/15 fills in the fair/ITM band over 24h**; 10/10 recent cancels are `maker_rest_escalation` (6 deadline + 4 CONFIRMED_VALUE_REFRESH).

Two compounding defects:

1. **The sub-deadline cancel-replace pulls** in `screen_resting_orders` (continuous_redecision.py:1669-1743) cancel a resting maker before it has had a real chance to fill. Two pull branches do this:
   - **BOOK_MOVED** (line 1707-1713): 1-tick book drift, **no minimum-rest-age guard** — fires inside the 300s escalation-arming floor, so the cancel does NOT arm escalation (`_family_rest_state` line 10224-10227) and the family re-rests (documented `MATCHED 19->0` loop, event_reactor_adapter.py:10204-10209). 0 observed in the most recent 6h (post the 06-20 arming fix), but it remains an unguarded sub-floor pull.
   - **CONFIRMED_VALUE_REFRESH** (line 1714-1740): at 5 min the rest is pulled to "re-price at fresh evidence" — the **dominant live canceller right now (4/10)**. It cancels a still-good resting order to re-run the cert; the re-cert almost always REST_DEFAULTs again (because FIX-B blocks the cross), so this is also a re-rest, not a fill.
2. **FIX-B + REST_DEFAULT jointly forbid the marketable cross** (mode_consistent_ev.py:509-532, 612-614): a +EV entry whose best ask is one tick above its conservative q_lcb can NEVER be lifted — maker rests below the ask (never filled), taker is inadmissible (ask+fee > q_lcb). The order can only cancel or no-trade. This is why even a rest that survives to the 20-min deadline (6/10 cancels) terminates unfilled rather than crossing.

The single highest-leverage lever is **#1's premature pulls**: they convert "a resting maker that could fill in the measured ~19% fast cohort, or survive to arm an admissible cross" into "never rests long enough to do either." Letting a rest LIVE to its arming floor is the universal change that lets the +EV book either fill passively or escalate to a marketable cross.

### Proposed surgical universal fix (file:line + approach — NOT applied; operator verifies)
**Give every resting maker entry an uninterrupted maker window = the escalation-arming floor (300s), by gating BOTH sub-deadline microstructure pulls behind that floor.** In `src/events/continuous_redecision.py:screen_resting_orders`:
- BOOK_MOVED branch (line ~1707-1713): add `and rest.quote_age_ms >= REST_VALUE_REFRESH_MIN_AGE_SECONDS * 1000` — the same age floor the value-refresh branch already uses (line 1714).
- CONFIRMED_VALUE_REFRESH branch (line ~1714-1740): it already has the 300s floor, but the pull cancels a GOOD rest to re-cert. Tighten it so it pulls ONLY when the re-cert would change the order (the held-side q_lcb no longer clears the live ask, i.e. the rest is now -EV), not merely when edge still exists — i.e. make it a "rest is now stale-bad" pull, not a "refresh for its own sake" pull. (Today's condition `ask_edge >= IMPROVE_DELTA` pulls a rest that is STILL good, which is the churn.)

Effect: a rest gets a full 300s maker window (captures the measured ~19% fast-fill cohort); a cancel that fires after 300s ARMS escalation (`_family_rest_state` line 10288), so the next cycle CROSSES as `TAKER_ESCALATED_AFTER_REST` when the ask is admissible (<= q_lcb, FIX-B unchanged) or cleanly no-trades — instead of re-posting an identical unfillable rest. The +EV book finally gets fills it can monitor/exit.

Properties vs operator law:
- **Universal**, not order-type / price-band specific: changes pull cadence for ALL resting entries.
- **No cap / allowlist / throttle / shadow**: removes premature cancels; adds no notional cap, no flag-default-OFF, no time-ban. Reuses the EXISTING `REST_VALUE_REFRESH_MIN_AGE_SECONDS`; introduces no new magic number.
- **No backtest**: justified by live-chain evidence (0/15 fair-ITM fills; 10/10 maker_rest_escalation cancels) + the code's own documented loop.
- Belief-decay pulls (BELIEF_WORSENING, line 1690-1697) stay UNGATED — genuine new adverse evidence should still pull a stale-favorable rest immediately. Only the bare-microstructure pulls (BOOK_MOVED, and the refresh-for-its-own-sake half of CONFIRMED_VALUE_REFRESH) get the age/stale-bad floor.

**Test impact (operator must verify):** `tests/events/test_continuous_redecision_resurrection.py` ASSERTS BOOK_MOVED pulls fire at `quote_age_ms=0.0` (fresh quote) — lines 600, 623, 670 — and there are value-refresh pull tests too. These encode the exact churn behavior being removed; they must flip to assert a fresh (<300s) rest HOLDS and only a matured / now-stale-bad rest is pulled. That test change IS the antibody for the fix, not collateral damage. `test_rest_then_cross_adapter_seam.py:156` already documents the loop evidence the fix closes.

Secondary (lower leverage, do NOT bundle): FIX-B forbids crossing above q_lcb by design (conservative-entry law); it is NOT the lever to loosen. The honest way to fill the +EV entries it blocks is to let the maker rest survive (fix #1) so it fills passively or arms a cross when the ask drops to <= q_lcb. The goal is fills that can be monitored/exited — achieved by stopping the churn, not by lifting the conservative cross cap.

Data-quality side note (separate, lower priority): the duplicate `venue_order_facts` rows with identical `observed_at` for one command_id (mixed LIVE+CANCEL_CONFIRMED) are a fact-ingestion duplication that defeats clean per-order timing analytics. Worth a follow-up, but it does not change the fill verdict (read from `matched_size`).

## 5. Evidence ledger (every claim cited)

| Claim | Citation |
|---|---|
| Entry default = post_only GTC maker rest | mode_consistent_ev.py:612-614, 94; executor.py:458-466 |
| Maker limit is structurally below the ask (non-marketable) | mode_consistent_ev.py:229-254 (esp. 249-250) |
| Measured maker fill ~10.8%, 0 at p0.30-0.80 | mode_consistent_ev.py:16-19 (docstring) |
| Taker blocked by spread guard / FIX-B / hysteresis | mode_consistent_ev.py:58, 509-532, 87 |
| 90s screen pulls rests on BOOK_MOVED(1 tick)/value-refresh | continuous_redecision.py:1669-1743; main.py:9669-9677 |
| 20-min deadline job (comment says 2.0h — stale) | maker_rest_escalation.py:71-76; mode_consistent_ev.py:131; main.py:9652-9660, 6003 |
| Escalation arms only after 300s; sub-floor pull => re-rest loop | event_reactor_adapter.py:10224-10227, 10266-10293; documented loop 10202-10219 |
| Live counts CANCELLED=35/FILLED=1 (6h); 13 ACK/0 fill (1h) | zeus_trades.db venue_commands; zeus-world.db edli_live_order_events (this session) |
| Fill rate by band: deep-OTM 4/35, fair 0/4, ITM 0/11 (24h) | §2.1-DATA (venue_order_facts, queried 2026-06-23 02:00:42 UTC) |
| 10/10 cancels = maker_rest_escalation; 6 deadline + 4 CONFIRMED_VALUE_REFRESH (6h) | §2.1-DATA (venue_command_events.payload_json) |
| Non-marketable maker pricing (limit sub-ask by construction) | §2.2-DATA + mode_consistent_ev.py:249-250; 0/15 fair-ITM fills corroborate |
| Duplicate venue_order_facts (identical observed_at, mixed LIVE+CANCEL_CONFIRMED) | §2.1-DATA data-quality note |
