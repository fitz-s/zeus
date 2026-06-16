# Dead order lane (08:17→dark) root cause: per-token GET /book storm starves the forecast decision lane

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: operator standing goal (continuous settlement-graded POSITIVE-after-cost
  alpha) + RULE 1 (a suppression is OUR defect until settlement proves otherwise).
- Supersedes the "forecast over-confidence / q_lcb ≤ price" conclusion from the prior
  (pre-compaction) session for the POST-14:22-restart state: that q-edge gate fired on only
  **1** family post-restart; it is NOT the binding suppressor now. The binding suppressor is
  throughput/latency plumbing, below.

## Ground state (measured 2026-06-16 ~15:30 local / ~20:30Z)

- Daemon: `com.zeus.live-trading`, main tree `/Users/leofitz/zeus` on `live/iteration-2026-06-13`,
  HEAD `69da9e1eda`. Restarted **14:22:18** — carries all three morning fixes (`_math`
  `1574d5ce6b`, pre-warm `834502b90c`, receipt-ledger `69da9e1eda`). Writing fresh.
- Last LIVE forecast order: **08:17:07** (buy_no). Zero orders post-restart.
- `_math` SPINE_WIRING_FAULT: **0 post-restart** (the 25 in the rolling-400 window are stale,
  08:44–09:16; genuinely fixed in running code).
- Forecast events are emitted fine: **169 FORECAST_SNAPSHOT_READY pending**, consumer
  `edli_reactor_v1`, created 20:22Z, attempt_count low, not expired, available_at in the past.
  06-17 posteriors fresh (913, max computed_at 20:23Z). The lane is NOT starved of events and
  NOT a forecast-production failure.
- Post-restart reactor: **29 cycles in 64 min (~130s/cycle), 1–2 events processed/cycle.**
  Cycle-reason histogram: 39 TRADE_SCORE_NON_POSITIVE (day0) + 1 EVENT_BOUND (forecast q-edge).
  ~20 transient requeues (forecast events reached but EXECUTABLE_SNAPSHOT_BLOCKED, not decided).

## The single binding mechanism

`reactor.process_pending` runs a per-cycle wall-clock budget (`ZEUS_REACTOR_CYCLE_BUDGET_SECONDS`,
default 30–45s; pre-event check at reactor.py:745 returns once spent). Each family decision
runs **p99=59s, max=460s** (reactor.py:739 comment, measured 2026-06-15) — so the loop completes
only **1–2 events per cycle**. `_fair_lane_interleave` (reactor.py:734) keeps day0 in the first
slot (reactor.py:729), so under a 1-event budget the single slot is always day0 (Tier-0); the
169 Tier-1 forecast families are reached only on the 2-event cycles, where they
**transient-requeue on EXECUTABLE_SNAPSHOT_BLOCKED** instead of deciding.

**Why each decision takes 28–60s+, and why the captured book is already stale:** the capture /
decision path issues **sequential per-token `GET https://clob.polymarket.com/book?token_id=…`**
calls, ~650ms each, dozens per family (e.g. 24 distinct token GETs in one 15s span at 15:32:21–36
feeding a single family). Rate: **~900 GET /book per 10 min, all day** (flat across the 14:22
restart — NOT caused by the pre-warm). The batched `POST /books` path runs only **~20/10min**
(45:1 in favor of the slow per-token path). A 22-candidate family ≈ 44 token books ×650ms ≈ **28s**
of sequential HTTP — comparable to / exceeding the **30s snapshot freshness window**
(`_K1_DEFAULT_PRESUBMIT_FRESHNESS_SECONDS=30.0`), so the book the capture just produced is stale
almost immediately → `EXECUTABLE_SNAPSHOT_BLOCKED` → requeue → the family never decides → no order.

This ONE mechanism explains all three observed failures simultaneously:
1. Throughput collapse (1–2 families/cycle) → day0 monopolizes the budget → forecast starved.
2. EXECUTABLE_SNAPSHOT_BLOCKED requeue (#122) → capture slower than the 30s freshness window.
3. Forecast orders dark since 08:17 (morning: day0 quiet, forecast got slots; afternoon: US-daytime
   day0 `DAY0_EXTREME_UPDATED` churn floods the 1–2 slots/cycle).

## The fix (mechanism-level, settlement-honest, loosens NO gate)

Route the decision-path / pre-warm per-family book capture through the existing batch helper
`src/data/market_scanner.py::_prefetch_selected_orderbooks` (one `POST /books` per ≤500-token
chunk; byte-identical response shape to GET /book — `_normalize_prefetched_orderbook`, market_
scanner.py:2822-2825 already consumes a `prefetched_orderbook`). The batch substrate-refresh loop
(market_scanner.py:4109-4152) already does this; the decision-time / single-family capture path
(`main._edli_pre_submit_jit_book_quote_provider` and/or `_edli_decision_family_snapshot_refresher`
→ the reactor's `_family_snapshot_refresher` / `_process_event_unit` capture) does NOT — it hits
the per-token `_fetch_orderbook_snapshot` fallback for every candidate. Batch those so a 44-token
family capture is ~1s, not ~28s.

Expected result: capture ≪ 30s freshness window → forecast families decide instead of
EXECUTABLE_SNAPSHOT_BLOCKED-requeue; per-family latency drops ~28s→~1s → reactor processes
30-60+ families/cycle → both day0 AND forecast lanes drain → forecast families with honest edge
cross (as the morning's 8 buy_no crosses did), the rest honestly no-trade. NOT a forced order;
NOT a loosened gate; NOT a band-aid (it completes the 2026-06-15 interleave fix by removing the
latency that makes the budget fit only 1 event).

## What is NOT the cause (ruled out this session, with evidence)
- NOT `_math` (0 post-restart). NOT forecast over-confidence / q_lcb≤price (1 EVENT_BOUND post-restart).
- NOT event starvation (169 fresh FSR pending). NOT forecast production (913 fresh 06-17 posteriors).
- NOT the pre-warm regression (GET /book rate flat across the 14:22 deploy).
- NOT day0-vs-forecast tier ordering ALONE (the interleave exists; it fails only because the
  budget fits 1 event, which is the latency problem above).
