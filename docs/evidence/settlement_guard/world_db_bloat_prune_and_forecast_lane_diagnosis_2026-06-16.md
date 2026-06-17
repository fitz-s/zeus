# World-DB 7M-row bloat prune + forecast-lane cross-blocker diagnosis (CORRECTED)

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: GOAL #83 (continuous settlement-graded POSITIVE-after-cost crosses) + RULE 1
  (a suppression is OUR defect until settlement proves otherwise).

## What was DONE (verified)

`opportunity_event_processing` (zeus-world.db) had **7,050,590** terminal rows
(4.06M expired + 2.11M ignored + 0.56M dead_letter). Root: the EDLI prune organ
`_edli_prune_pending_working_set` → `store.archive_*` only **marks** rows
`processing_status='expired'/'ignored'/'dead_letter'` — it **never physically DELETEs**
them. So the table (and the 42GB world DB) grew unbounded; the emit's archive-UPDATE +
`fetch_pending` JOIN held the world write-lock long.

Fix (one-time): `scripts/prune_terminal_opportunity_events.py` — cooperative batched
delete (retry/backoff, 5k batches, 60s busy_timeout) preserving the ~420 live
(pending/processing/claimed) + recent-3h events. Deleted **~7.0M** rows → terminal
remaining **69,534**. `opportunity_events` itself is APPEND-ONLY (no-DELETE + no-UPDATE
triggers) and was correctly skipped — it needs a separate cold-rotation (drop trigger →
archive → truncate → re-add), tracked, NOT done. All three split DBs are bloated:
world 42GB, **forecasts 36GB, trades 25GB**.

Durable follow-up (staged, not deployed): wire a physical processing-retention call into
`_edli_prune_pending_working_set` (bounded per cycle, retain_hours window) so the bloat
cannot recur.

## CORRECTION — the prune was hygiene, NOT the alpha unblock

Initial (wrong) model: `executable_market_snapshots`=0 → capture dead → forecast lane
fully starved. **Wrong.** That 0 is the EMPTY world-DB SHADOW table. The live snapshots
are in **zeus_trades.db** (capture writes via `trade_conn`,
`event_reactor_adapter.py:550`; gate reads via the K1 `trades` ATTACH). Ground truth:
`zeus_trades.db.executable_market_snapshots` = **4.9M rows, ~200K fresh / 3 min,
max_captured=now**. Capture works; the forecast lane was never dead — it is rate-limited.
Reactor throughput stayed ~2-3 families/cycle after the prune (the cost is per-decision
compute p99≈59s, not the world-DB queue).

## The ACTUAL cross-blocker (no cross since 08:17; 5 buy_no proofs accepted earlier today)

Reason histogram, last 120 reactor cycles:
- **92 TRADE_SCORE_NON_POSITIVE** — DAY0 families on the legacy scalar gate, all no-trade.
  Day0 FLOODS the reactor and consumes ~75% of throughput, starving the forecast/spine lane
  (the `_fair_lane_interleave` does not fully balance against the day0 pending volume).
- 17 EXECUTABLE_SNAPSHOT_BLOCKED (transient — family's city not captured that cycle).
- 8 FDR_REJECTED, 4 capital_efficiency, 4 EVENT_BOUND_ALL_CANDIDATES_REJECTED — forecast
  families priced but `q_lcb ≤ price`. Per RULE 1 the suppressed q_lcb is presumed OUR
  defect (legacy scalar q), not market efficiency — the q-engine quality lever.
- 3 LIVE_INFERENCE_INPUTS_MISSING / 1 MISSING_EXPECTED_MEMBERS — forecast-reader inputs
  missing (open task #70).
- 1 SUBMIT_ABORTED_FAMILY_REVERSED — a spine ΔU leg WAS selected but aborted at recapture
  (selected leg no longer the ΔU primary on fresh curves) = a fill-rate lever, the
  shortest path to a real cross (a tradeable +ΔU leg already exists).

`qkernel_spine_enabled() = True`, but priced forecast families surface legacy reasons
(`capital_efficiency`), so the spine's vector q is largely not the deciding authority on
them — they fall to the legacy scalar q_lcb gates.

## Net

Infrastructure (capture, world-DB bloat) was NOT the cross-blocker. The blockers are
q/gate quality + day0 throughput-flooding + fill-rate — the q-engine rebuild's core, not a
quick patch. Levers, most-tractable first: (3) SUBMIT_ABORTED_FAMILY_REVERSED recapture
re-rank; (1) day0 queue-domination throughput; (2) legacy-q_lcb suppression on forecast
families / spine routing.

## CORRECTION #2 (live runtime diagnostic, 2026-06-16 ~11:15) — the SPINE IS DECIDING

A temporary observability log at the spine dispatch (event_reactor_adapter.py:2509,
since reverted) settled it definitively. Every forecast cycle:
`QKERNEL_DISPATCH_DIAG event_type='FORECAST_SNAPSHOT_READY' flag=True elig=True day0=False take_spine=True`
then `spine_ran selected=True no_trade=None q_source='qkernel_spine'`. So the rebuilt
spine IS the deciding authority on forecast families and selects a vector-positive proof
EVERY cycle. The "0 qkernel_spine q_source / 0 QKERNEL_SPINE reasons" that misled the
earlier section is a RED HERRING: (a) `edli_no_submit_receipts` is STALE — newest row is
2026-06-12 (the "dead since 06-06" persistence failure), so receipt-based q_source/reason
analysis reflects 06-12, not now; (b) the receipt q_source is captured at proof GENERATION
(the legacy one-calibrator seam, era.py), not the spine overlay, so it never reads
`qkernel_spine` even when the spine decides.

## THE ACTUAL BINDING SUPPRESSOR ON NEW FILLS = the EXECUTION / rest-then-cross lane

The spine's +edge buy_no picks are placed as MAKER rests (REST_DEFAULT — the operator-
designed settlement-honest policy; a taker cross only fires via TAKER_ESCALATED_AFTER_REST
after the ~20-min deadline, src/strategy/live_inference/mode_consistent_ev.py). Live state:
ONE order (`command=6467b5c3 / 0x5ce1…`, the #131 "5.07 NO @0.64" orphan, created 09:34,
latest fact PARTIALLY_MATCHED matched=5.07) sits in an OPEN state. Because PARTIALLY_MATCHED
∈ open_fact_states, `_family_rest_state_from_venue_truth` (event_reactor_adapter.py:8181)
returns `unexpired_family_rest=True` → policy #1 HOLD_REST_IN_PROGRESS (`chosen_ev=-inf`)
perpetually blocks that family's re-decisions; the maker_rest_escalation job
"cancels" the order every 5 min (11:21, 11:26 — SAME command) but cannot clear a
partial-matched fill, so the family never escalates to a taker cross and never re-trades.

PRECISE FIX TARGET: presence-reconcile / settle the #131 partial-matched 5.07-NO order so
its family clears HOLD_REST_IN_PROGRESS (the #131 resolver was marked done but the order is
live again), AND verify TAKER_ESCALATED_AFTER_REST fires for a clean unfilled rest after the
deadline (escalated_after_rest from venue truth, :8191). This is the fill-lane lever — the
decision/q lane is healthy. Note: 6 in-flight 06-17 positions + this 5.07 NO settle/grade
tomorrow = the pending settlement-graded evidence.
