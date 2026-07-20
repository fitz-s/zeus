# Entry lead-time latency probe — why Zeus consumes ~19h of a ~38-41h market window before entering

Date: 2026-07-19. Window: last 30 days (2026-06-19 → 2026-07-19) unless noted. All DB reads `sqlite3 -readonly`, all timestamps UTC. Prior finding this extends: `docs/evidence/capital_efficiency_2026_07_19/universe_coverage.md` §4 (median entry lag ≈23h over a 14-day sample, no stage decomposition).

## 0. Method — reconstructing the pipeline from certificates, not logs

The certificate architecture (`decision_certificates`, `state/zeus-world.db`) timestamps every stage of one evaluation cycle for one family under a shared `event_id`, and several certificate types (`FinalIntentCertificate`, `ExecutionCommandCertificate`, `ActionableTradeCertificate`, `CandidateEvidenceCertificate`) carry `family_id`/`condition_id`/`event_id` directly in `payload_json` — no semantic-key parsing needed for those. `BeliefCertificate` does not carry `family_id` in its payload; it's recovered from `semantic_key` (`belief:<event_id>:<family_id>:<token_id>`).

Pipeline joined for 257 `ENTRY_ORDER_FILLED` events (`position_events`, `state/zeus_trades.db`, last 30 days, 89 distinct `condition_id`s, `condition_id` taken from `json_extract(payload_json,'$.condition_id')` — the `order_id` column is the **venue order hash**, not the condition id, a trap that produces silent zero-row joins if you use it):

1. **T0 market first seen**: `MIN(captured_at)` from `executable_market_snapshots` per `condition_id` (all-time, not window-bounded).
2. **T1 first priced posterior**: `MIN(decision_time)` of `BeliefCertificate` for the family (first-ever belief for that `family_id`, any cycle).
3. **T2 first candidate emitted**: `MIN(decision_time)` of `ActionableTradeCertificate` for the `condition_id`.
4. **T3 first order submitted**: `MIN(occurred_at)` of `VenueSubmitAttempted` in `edli_live_order_events`, joined via `aggregate_id.split(':')[0]` (= `event_id`) → `event_id→condition_id` map built from `ExecutionCommandCertificate` payloads.
5. **T4 fill**: the `ENTRY_ORDER_FILLED` `occurred_at`.

88 of 257 fills (covering 88 of 89 distinct conditions) resolved a complete T0→T4 chain; the rest lacked a `FinalIntentCertificate` match (older/recovered fills whose certificate predates the certificate architecture's current shape, or fills from `src.execution.command_recovery` repair events that don't carry `condition_id`). Full row-level CSV: `/private/tmp/claude-501/-Users-leofitz-zeus/7589dc75-d443-4b7f-8e2b-24945ef3038c/scratchpad/entry_leadtime_rows.csv`.

## 1. Decomposition — one hop absorbs essentially the entire lag

| hop | n | mean (h) | median (h) | min (h) | max (h) |
|---|---|---|---|---|---|
| **T0→T1 market-seen → first belief** | 88 | **15.54** | **17.02** | 0.01 | 58.32 |
| T1→T2 first belief → first candidate | 88 | 0.81 | 0.00 | 0.00 | 26.37 |
| T2→T3 first candidate → first submit | 85 | 0.04 | 0.00 | 0.00 | 2.46 |
| T3→T4 first submit → fill | 85 | 0.15 | 0.00 | 0.00 | 7.65 |
| **T0→T4 total** | 88 | **16.82** | **19.33** | 0.01 | 58.33 |

**T0→T1 (market seen → first-ever priced posterior) is 88-92% of the total lag** (mean 15.54/16.82 = 92.4%; median 17.02/19.33 = 88.1%). Every downstream hop has a **median of exactly 0.00h** — once a family has its first `BeliefCertificate`, candidate emission, order submission, and fill happen essentially synchronously within the same reactor pass. This directly answers the "which hop dominates" question: it isn't candidate rejection, submit rejection, or fill/liquidity friction — it's **getting a first price at all**.

By event_type (Day0 vs day-ahead) — `event_type` taken from the family's first `FinalIntentCertificate`:

| event_type | n | T0→T1 median (h) | T0→T4 total median (h) |
|---|---|---|---|
| FORECAST_SNAPSHOT_READY (day-ahead spine) | 72 | 17.00 | 18.49 |
| EDLI_REDECISION_PENDING | 11 | 12.44 | 13.52 |
| DAY0_EXTREME_UPDATED | 5 | 29.51 | 32.86 |

The day-ahead spine lane (FORECAST_SNAPSHOT_READY, 82% of the sample) carries almost the entire lag and is where the fix below applies. Day0 (n=5) is worse but too small a sample to generalize on its own.

By city, the same T0→T1 hop varies from 0.01h to 31h **within the same timezone and even within the same city** — Hong Kong ranges 0.28h→27.87h, Seoul 0.04h→30.16h, Paris 0.26h→58.32h across different fills in the window. A pure city-timezone/forecast-cycle explanation cannot produce that within-city spread; something else is gating individual families independently of geography. That something is a queue-priority mechanism (§2).

## 2. Mechanism — not physical, not a correctness gate: a two-layer priority queue that puts new families behind old ones

Ruled out first:

- **(a) Physical forecast-cycle limit.** `src/data/dissemination_schedules.py:15-18,46-63` — GFS completes ~base+4h14m, ECMWF ENS Day-0 ~base+6h40m, reconstructed-provenance sources default to a 12h lag (`_RECONSTRUCTED_LAG`, line 66). Models run 4×/day (00/06/12/18Z). Worst case this bounds the physical wait to roughly 6h (cycle interval) + ≤12h (dissemination) ≈ 18h — close to, but below, the observed 17-19h **median**, and far below the observed 58h **max** and the within-city spread. Physical cadence cannot be the sole or even dominant driver.
- **(b) An explicit "young market" gate.** `src/events/triggers/forecast_snapshot_ready.py:488-548` (`classify_forecast_snapshot`) — under the current live posterior-backed path (`data_version == "forecast_posteriors.replacement_0_1_neutral_carrier"`, the `rmf-<city>|<date>|<metric>|<cycle>` snapshot IDs actually seen in `position_events.snapshot_id`), completeness is decided purely by **whether a posterior row exists and is certified `COMPLETE`/`LIVE_ELIGIBLE`** (line 529-548) — the legacy ensemble member-count floor (`min_members_floor=40`, line 494) is explicitly bypassed for this path (comment, line 517-524: "accept it as COMPLETE without demanding observed_members >= expected_members"). There is no minimum-age/minimum-lead-time check in this function. Consistent with T1→T2 median = 0.00h: once a posterior exists, nothing blocks the candidate.
- **(d) Rejection loop on young markets.** Ruled out by the same T1→T2 = 0 finding — no repeated reject/retry cycle is consuming time between belief and candidate.
- **(e) Sizing/liquidity.** Downstream of pricing entirely; irrelevant to whether a first posterior gets computed.

What actually gates T0→T1 is a **priority queue that always serves already-tracked families before brand-new ones**, at two separate layers:

**Layer 1 — the forecast-posterior materialization queue explicitly prioritizes held positions over everything else, including brand-new families.** `src/data/replacement_forecast_live_materialization_queue.py:655-729` (`_cycle_advance_seed_priority_map`), consumed at `:1162` and `:1436` (`sorted(seed_files, key=lambda path: _cycle_advance_file_sort_key(path, priority))`):
```python
value = (0 if int(held_position or 0) == 1 else 1, str(enqueued_at or ""))
```
Priority key `(0, …)` — an already-held position's belief refresh — sorts strictly ahead of `(1, …)` — everything else, **including a family with zero prior belief**. The docstring (line 665-668) states the intent plainly: "a large batch of held-position requests can otherwise spend live cycles on non-held cities while a held position has stale belief" — i.e., this was deliberately built to protect existing exposure, with no separate tier for "never-priced-yet" families. This queue drains at `materialization_queue_poll_seconds=1` (default, `src/ingest/forecast_live_daemon.py:1276-1284`) with `poll_batch_limit=8` per tick (`src/data/replacement_forecast_production.py:203-210`) — fine when backlog is small, but during a forecast-cycle rollover (every 6h, all currently-held families' redecisions land in the queue simultaneously), a brand-new family's first-ever materialization request queues behind the entire held-position batch. This directly explains the within-city variance: whether a new family gets priced fast depends on how large Zeus's current held-position book is at that moment, not on the city or the forecast provider.

**Layer 2 — once a posterior exists, the reactor's decision loop has a hard per-cycle throughput ceiling shared 1:1 across lanes, with no new-vs-refresh split inside the forecast lane.** `src/events/reactor.py:70` (`DEFAULT_REACTOR_CYCLE_BUDGET_SECONDS = 22.0`, reduced from 30s by commit `246d4d7fd4` 2026-06-26 "fix(live): keep reactor cycles within cadence" — a liveness fix that further tightened this ceiling), inside a `reactor_scan_interval_seconds=60` cadence (`src/main.py:6657-6663`). Per `reactor.py:1197-1198`: "the per-cycle wall-clock budget completes only ~3-4 family decisions (p99=59s each)" — corroborated by the prior incident doc `docs/evidence/spine_lane_unstarve_2026-06-15.md` (L4/L5), which measured the day0 lane fully starving the forecast/spine lane under this same budget and fixed it with `_fair_lane_interleave` (`reactor.py:406-444`, commits `3d28dd913`/`bef367183`/`fa0cdfaaa`, still live at HEAD — confirmed called at `reactor.py:1280`). That fix round-robins **day0 vs forecast** 1:1. It does **not** distinguish, inside the forecast lane itself, between `FORECAST_SNAPSHOT_READY` (a family's first-ever decision) and `EDLI_REDECISION_PENDING` (refreshing an already-priced family) — `_FORECAST_DECISION_EVENT_TYPES = frozenset({"FORECAST_SNAPSHOT_READY", "EDLI_REDECISION_PENDING"})` (`reactor.py:403`) treats them as one undifferentiated lane. The 2026-06-15 incident doc's own per-decision latency finding — "~22 sequential live book fetches per family decision… ~15s of the ~22s budget" — was flagged as an open lever ("parallelizing the 22 fetches cuts ~15s→~2s… high-leverage") and was **never implemented**: `get_orderbook_snapshots` (`src/data/polymarket_client.py:651-712`) already batches all tokens into one `/books` POST, so if 22 sequential fetches are still observed live today they are happening in a different loop than that batched call; this needs a follow-up trace but is out of scope for this report's fix.

Net: both layers independently reward "already in the book" over "new," which is the opposite of what the entry-lag economics need (being first on a family that has never been priced is exactly the alpha this investigation is chasing).

## 3. Is either gate justified by evidence, or scar tissue?

Both are **evidence-driven, not arbitrary** — every constant here traces to a dated incident with measured numbers (`docs/evidence/spine_lane_unstarve_2026-06-15.md` L1-L5; commit `246d4d7fd4` 2026-06-26; commit `a4b50fdcc` 2026-07-18 "perf(forecast): seek queued priority scopes" hardened exactly this held-position priority path yesterday). This is not unjustified gate mass to collapse. But the specific **gap** — no third priority tier for "never-priced-yet" families ahead of "stale refresh of an already-priced family" — was never a deliberate decision either way; it's an unaddressed dimension of a fairness fix that solved a different, correctness-motivated problem (protecting held positions from stale belief). Collapsing it doesn't remove a protection; it adds one the operator's own stated goal (informed-first pricing wins a zero-sum game) has been missing.

## 4. Cost of lateness in July — not cleanly computable from this data, and the naive method is confounded

For July fills with a resolvable `condition_id` + fill price + fill size (`venue_trade_facts.fill_price`/`filled_size` joined via `position_events.payload_json.source_trade_fact_id`, 72 of 189 July fills), compared fill price against the best (lowest) ask available for the same token in the **first 6h** of the market's life (`executable_market_snapshots.orderbook_top_ask` where `selected_outcome_token_id` matches the filled side):

- All 72: total delta = **-$512.00** (fill price *below* the early-window best ask on net — i.e. naively "cheaper" to enter late).
- Excluding 2 degenerate dead-bin trades (fill price < $0.01, i.e. near-certain resolved bins where price collapsed to ~0 as the outcome became obvious): remaining 70 trades sum to **-$99.00** (28 trades cost more late, +$29.47; 36 cost less late, -$128.47).

**This is not evidence that lateness is free or beneficial.** 98% of Zeus's selected hypotheses are `buy_no` (`universe_coverage.md` §2), and NO-side prices on a correctly-directional bet drift toward $0 as the resolution date nears and uncertainty resolves — a structural feature of the book's composition, not a competitive-timing effect. The two excluded outliers alone (-$324, -$89 = 81% of the raw total) are exactly this dynamic. A clean isolation of "dollars lost to being late vs a genuinely competing maker" would need a counterfactual (the edge/Kelly size the engine *would have* computed at the T0+6h price, not just the price delta), which isn't reconstructable from frozen snapshots alone. **Honest answer: July's dollar cost of lateness is not demonstrable from this dataset** — report the mechanism (§1-3) as the actionable finding, not a fabricated $ figure.

## 5. The direct fix

Add a third priority tier to `_cycle_advance_seed_priority_map` (`src/data/replacement_forecast_live_materialization_queue.py:655-729`): a family with **zero prior `BeliefCertificate`** (never priced) should sort ahead of `held_position` refreshes, not behind them — `(-1 if never_priced else (0 if held_position else 1), enqueued_at)`. The lookup this needs (does this family have any existing belief) is a cheap existence check against data the function already has a live connection to. This is a same-file, same-function change — no new machinery, no observation period, no shadow: change the sort key, ship it.

This targets Layer 1 exactly (§2), which is upstream of and independent from Layer 2's per-cycle decision budget; it doesn't require touching the reactor's `_fair_lane_interleave` or its budget constants, which are separately evidence-justified and higher-risk to alter (the budget was tightened, not loosened, in the most recent related fix, specifically to avoid reintroducing a cycle-overlap liveness bug). If Layer 1 alone doesn't close the gap, the second-order lever is splitting `_FORECAST_DECISION_EVENT_TYPES` fairness (`reactor.py:403-444`) so `FORECAST_SNAPSHOT_READY` (first-ever) gets its own guaranteed slice ahead of `EDLI_REDECISION_PENDING` (refresh) within the forecast lane — same shape of fix, one layer downstream.

Not a physical limit: the physical dissemination floor (§2) is bounded well under the observed median, let alone the observed max — there is real headroom being lost to policy, not physics.
