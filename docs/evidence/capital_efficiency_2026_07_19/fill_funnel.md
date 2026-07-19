# Order Fill Funnel — Candidate → Submit → Rest → Fill/Cancel (last 30 days)

Window: `2026-06-19T00:00:00Z` .. `2026-07-19T07:39Z` (`created_at`/`decision_time` bound, UTC).
Read-only, `sqlite3 -readonly` / `sqlite3.connect(..., uri=True)` with `?mode=ro` throughout. No market-price backtest — every number below is joined from decision-time certificates and venue-observed order/fill facts, never a replayed price series.

## 0. Data source map (read this before trusting any number below)

Zeus runs a two-DB split (K1) where tables with identical schemas live in both files but only one side is the live writer for a given table as of 2026-07-19:

| Table | Live/authoritative in | Evidence |
|---|---|---|
| `venue_commands`, `venue_command_events`, `venue_order_facts`, `execution_fact`, `reduce_position_economics`, `reduce_generations` | `state/zeus_trades.db` | `venue_commands` fresh to `2026-07-19T07:39`; the same table in `zeus-world.db` has 4 stale rows from `2026-05-19`. |
| `decision_certificates` (LIVE mode) | `state/zeus-world.db` | fresh to `2026-07-19T07:39`; the copy in `zeus_trades.db` stops at `2026-07-09` (stale replica). |
| `market_events` (city/target_date), `edli_no_submit_receipts` | `state/zeus-world.db` only | not present as a live table in `zeus_trades.db`. |

All queries below `ATTACH DATABASE 'file:.../zeus-world.db?mode=ro' AS world` onto a `zeus_trades.db?mode=ro` connection and cross the two DBs in-process (Python `sqlite3`, script at `/private/tmp/claude-501/-Users-leofitz-zeus/7589dc75-d443-4b7f-8e2b-24945ef3038c/scratchpad/funnel.py`).

**Caveats that bound every number in this report:**
- `edli_no_submit_receipts` (candidates rejected *before* ever submitting) has gone silent since `2026-06-29` — 20 days with zero NO_SUBMIT receipts recorded. This report therefore covers only the **submit → rest → fill/cancel** stage (orders that did reach the venue), not the upstream candidate-rejection stage; that upstream stage cannot currently be measured for the last 3 weeks from this table.
- `reduce_position_economics` (the on-chain payout+fills reducer — the only PnL source this report uses; **`position_current.realized_pnl_usd` is deliberately not used**, per standing operator guidance that only the chain-truth reducer is authoritative) has its latest generation frozen at `computed_at=2026-07-13T23:19:47Z`. 47 of the 170 positions opened by fills in this window are not yet in that generation (pending settlement/reduction) — the PnL figures in §4/§6 are therefore partial and biased toward older, already-settled trades.
- Certificate matching: `ActionableTradeCertificate.final_intent_id` is regex-matched against `venue_commands.decision_id` (`edli_intent:<event_id>:<token_id>` substring). 897/914 (98%) of ENTRY commands matched a certificate; 17 did not (likely commands whose certificate predates the query's `decision_time` cutoff) and are excluded from cert-dependent stats (§3, §4, §6) but included in the raw funnel counts (§1).
- Day0 flag is approximated as `target_date == date(created_at)`; it is not the authoritative day0-admission gate (`src/engine/day0_admission.py`), so treat the Day0 split in §1 as directional.

## 1. Funnel counts — last 30 days

Mechanism: `src/events/continuous_redecision.py:2507` `screen_resting_orders` (belief-decay / book-drift / value-refresh pulls) → `src/execution/staleness_cancel.py:456` `run_c3_staleness_cancel_cycle` (q-staleness + 20-minute TTL backstop, `bootstrap_rest_deadline_minutes()` = `MAKER_REST_ESCALATION_DEADLINE_MINUTES = 20.0` at `src/strategy/live_inference/mode_consistent_ev.py:139`) → `src/engine/event_reactor_adapter.py:24397` PASS-1 escalation arming → `select_rest_then_cross_mode` (`src/strategy/live_inference/mode_consistent_ev.py:487`).

```sql
SELECT command_id, decision_id, position_id, token_id, market_id, side, price, created_at, state
FROM venue_commands WHERE intent_kind='ENTRY' AND created_at >= '2026-06-19T00:00:00';
```

**914 ENTRY venue_commands submitted in 30 days.**

| Outcome bucket | Count | % |
|---|---:|---:|
| `filled_full` (state=FILLED) | 200 | 21.9% |
| `filled_partial_then_pulled` (matched_size>0, then cancelled/expired) | 60 | 6.6% |
| `pulled_unfilled` (cancelled/expired, matched_size=0) | 599 | 65.5% |
| `rejected_pre_rest` (REJECTED/SUBMIT_REJECTED — never reached the book) | 55 | 6.0% |

Any-fill rate = (200+60)/914 = **28.4%**. Two-thirds of everything submitted is pulled with zero fill.

**By order type** (`order_type` from first `SUBMIT_REQUESTED` event payload — the real maker/taker split, not `venue_commands.side` which is always `BUY`):

| order_type | n | filled_full | filled_partial | pulled_unfilled | rejected | fill rate |
|---|---:|---:|---:|---:|---:|---:|
| GTC (maker rest) | 730 | 37 | 59 | 597 | 37 | **13.2%** |
| FOK | 132 | 120 | 0 | 2 | 10 | 90.9% |
| FAK (taker cross) | 47 | 43 | 1 | 0 | 3 | 93.6% |
| (null) | 5 | — | — | — | 5 | — |

80% of order volume is a GTC maker rest, and that lane converts at **13.2%** — the taker lanes (FOK/FAK, 20% of volume) convert at >90%. This is the dominant shape of the funnel: the strategy defaults to resting, and resting mostly fails to capture.

**By `rest_then_cross_policy`** (decision-time policy on the matched `ActionableTradeCertificate`, `src/strategy/live_inference/mode_consistent_ev.py:182-188`):

| policy | n | share |
|---|---:|---:|
| `REST_DEFAULT` | 456 | 49.9% |
| `MAKER_TAKER_FORBIDDEN` | 346 | 37.8% |
| `REST_DAY0_MAKER_ONLY` | 39 | 4.3% |
| `TAKER_EDGE_CLEARS_BOUND` | 21 | 2.3% |
| `HOLD_REST_IN_PROGRESS` | 17 | 1.9% |
| `TAKER_ESCALATED_AFTER_REST` | 18 | 2.0% |
| no cert matched | 17 | 1.9% |

94% of decisions land in a maker-rest policy lane (`REST_DEFAULT`+`MAKER_TAKER_FORBIDDEN`+`REST_DAY0_MAKER_ONLY`+`HOLD_REST_IN_PROGRESS`); only 2.0% ever reach `TAKER_ESCALATED_AFTER_REST` (see §6).

**By direction** (from cert `direction`, 897/914 matched): `buy_no` 732 (80%), `buy_yes` 165 (18%), unmatched 17. `buy_no` fill rate = (186+42)/732 = 31.1%; `buy_yes` fill rate = (12+18)/165 = 18.2%. `buy_no` fills roughly 1.7x more often than `buy_yes` in this window.

**By Day0 vs not** (approximate, see caveat): Day0 fill rate = (29+3)/51 = 62.7%; non-Day0 fill rate = (169+57)/846 = 26.7%. Day0 orders fill more than 2x as often — consistent with `src/engine/day0_admission.py` forcing maker-only-but-tighter/urgent execution near settlement.

**By city** (top 15 by volume, fill rate = any-fill / total):

| City | n | fill rate |
|---|---:|---:|
| Seoul | 92 | 0.53 |
| Paris | 66 | 0.47 |
| Kuala Lumpur | 54 | 0.13 |
| Manila | 49 | 0.16 |
| Singapore | 43 | 0.12 |
| Shenzhen | 42 | 0.21 |
| Ankara | 37 | 0.14 |
| Wuhan | 31 | 0.52 |
| Moscow | 29 | 0.41 |
| Taipei | 29 | 0.24 |
| Tokyo | 28 | 0.29 |
| Lucknow | 26 | 0.12 |
| Hong Kong | 24 | 0.50 |
| Buenos Aires | 24 | 0.08 |
| Helsinki | 24 | 0.29 |

There is a >6x spread in fill rate by city (Buenos Aires 8% vs Seoul 53%) at comparable volume — this is not noise at n=24-92; it points to systematically thinner books/wider spreads in the low-fill cities that the maker-rest default does not adapt to.

## 2. Time-to-fill / dead resting time (GTC maker rests only)

```sql
-- rest_start = MIN(occurred_at) WHERE event_type='SUBMIT_ACKED' per command_id
-- first_fill  = MIN(observed_at) WHERE state IN ('MATCHED','PARTIALLY_MATCHED') in venue_order_facts
-- cancel_at   = MAX(occurred_at) WHERE event_type IN ('CANCEL_ACKED','EXPIRED') per command_id
```

| | n | p50 | p90 | mean |
|---|---:|---:|---:|---:|
| GTC filled (rest_start→first_fill) | 95 | 527s (8.8min) | 513,512s (~143h, outlier-dominated) | 77,533s |
| FAK filled (taker, sanity check) | 37 | 0.0s | 0.0s | 0.0s |
| GTC unfilled-pulled resting duration (rest_start→cancel/expire) | 573 | 396s (6.6min) | 1,458s (24.3min) | 731.5s |

The GTC-filled p90 is a small-n (95) outlier artifact — a handful of long-lived rests finally cross weeks later; the p50 (8.8 min) is the representative fill latency when a maker rest does work.

The unfilled-pulled p90 (24.3 min) sits just past the 20-minute `MAKER_REST_ESCALATION_DEADLINE_MINUTES` TTL backstop (`src/strategy/live_inference/mode_consistent_ev.py:139`, wired through `bootstrap_rest_deadline_minutes()` in `src/state/order_state_predicates.py:121`) — confirming the TTL is the terminal cause for the tail of pulls, while the p50 (6.6 min) is dominated by earlier evidence-driven pulls (`CONFIRMED_VALUE_REFRESH`, `BOOK_MOVED` — see §5 reason table).

**Dead weight**: summed resting-seconds across all GTC rests in 30 days = 863,088s (~240 hours of aggregate book presence). Of that, 419,168s (~116 hours) belongs to rests that were pulled with zero fill: **48.6% of all GTC book-presence time produced no execution.**

## 3. p_fill_lcb calibration — decision-time estimate vs realized fill

```sql
-- p_fill_lcb from ActionableTradeCertificate payload_json top-level field, matched to
-- venue_commands via final_intent_id; realized outcome = matched_size>0 OR state='FILLED'
```

| p_fill_lcb bucket | n | realized fill rate |
|---|---:|---:|
| [0, 0.5) | 63 | 17.5% |
| [0.5, 0.8) | 8 | 75.0% |
| [0.8, 0.9) | 4 | 100.0% |
| [0.9, 0.95) | 5 | 60.0% |
| [0.95, 0.98) | 12 | 41.7% |
| [0.98, 0.99) | 12 | 83.3% |
| [0.99, 0.995) | 34 | 44.1% |
| [0.995, 0.999) | 320 | 28.7% |
| **[0.999, 1.0]** | **439** | **25.5%** |

n=897 total. **The dominant mass of decisions (439/897 = 49%) carries a decision-time `p_fill_lcb` ≥ 0.999 — a near-certainty claim — yet realizes only a 25.5% fill rate.** The [0.995,0.999) bucket (320 orders, 36% of the sample) realizes 28.7%. This is a severe, systematic *over*-estimate of fill probability at exactly the mass the sizing/gating logic treats as safest — `p_fill_lcb` is not tracking "will this maker rest actually get matched before it's pulled"; it appears to model something closer to "will this price level eventually trade at all, given unlimited patience," which the 20-minute TTL and the belief/book-drift screens never grant. This is the calibration failure most directly upstream of §1's 13.2% GTC fill rate.

## 4. EV lost to non-fill vs realized PnL of the filled cohort

```sql
-- forfeited EV per pulled_unfilled order with a matched cert:
--   shares = kelly_size_usd / c_fee_adjusted ; EV = shares * (q_lcb_5pct - c_fee_adjusted)
-- realized PnL: reduce_position_economics @ latest generation (chain-truth reducer),
--   payout_pnl_usd where payout_status IN (RESOLVED_ZERO, RESOLVED_NONZERO), else realized_pnl_usd
```

- **585** `pulled_unfilled` orders had a matched cert with computable decision-time EV. Sum of forfeited expected value = **+$2,798.18** over 30 days.
- Sum of approved-but-never-filled notional (`kelly_size_usd`) across those 585 orders = **$6,832.82**. Sum of notional that *did* get deployed across the 258 filled orders = **$2,639.14**. Roughly **2.6x more approved capital rested-and-died than ever reached the book as an executed fill.**
- Chain-truth realized PnL of the filled cohort (170 positions; 123 found in the latest reducer generation `b84c3f8b…` computed `2026-07-13T23:19:47Z`, `payout_status`: 39 CLOSED_VIA_FILLS, 37 RESOLVED_NONZERO, 44 RESOLVED_ZERO, 3 PENDING): **sum = −$188.40**. 47 filled positions are not yet in this generation (opened after the reducer's freeze point) and are excluded — treat this figure as partial/incomplete, biased toward the earlier half of the window.

**The forfeited-EV pool from unfilled orders (+$2,798, decision-time expectation) is an order of magnitude larger in magnitude than the chain-truth realized result of the orders that actually filled (−$188, partial).** Two non-exclusive explanations: (a) `p_fill_lcb`/`q_lcb` miscalibration (§3) inflates the paper EV of orders that were never going to fill anyway, so the $2,798 figure itself is optimistic; (b) classic maker adverse selection — a resting order is disproportionately more likely to actually get hit when the market has moved against the resting side, so the filled cohort's realized economics are worse than the unconditional decision-time edge would predict. Both point the same direction: the capture mechanism, not the underlying edge, is the leak.

## 5. Repricing races / chase cost

```sql
SELECT json_extract(payload_json,'$.cancel_reason'), count(*)
FROM venue_command_events WHERE event_type='CANCEL_REQUESTED' AND occurred_at>='2026-06-19'
GROUP BY 1 ORDER BY 2 DESC;
```

| cancel_reason | n |
|---|---:|
| `CONFIRMED_VALUE_REFRESH` | 398 |
| (no reason field — plain executor CANCEL, e.g. exit-side/duplicate-suppression cancels) | 148 |
| `BOOK_MOVED` | 64 |
| `ACTIVE_DUPLICATE_SUPPRESSED_BETTER_CANDIDATE` | 8 |
| `INVALID_PENDING_ENTRY_ACTIONABLE_CERTIFICATE_AUTHORITY` | 7 |
| `FAMILY_OPTIMUM_SHIFT` | 7 |
| `MAKER_REST_DEADLINE_EXPIRED` | 5 |
| `FAMILY_BEST_CANDIDATE_CHANGED` | 3 |
| `OPERATOR_SAFETY_HALT_BAD_TRADE_WINDOW` | 1 |

`CONFIRMED_VALUE_REFRESH` (the belief-decay/value-refresh pull, `screen_resting_orders` step 3, `src/events/continuous_redecision.py:2596`) is 62% of all named cancel reasons — the system re-prices resting orders far more than it pulls them for book drift.

For `BOOK_MOVED` pulls specifically (mechanism: `src/events/continuous_redecision.py:2565-2593`, gated behind the same 5-minute `REST_VALUE_REFRESH_MIN_AGE_SECONDS` floor as escalation-arming):

- 63 `BOOK_MOVED` pulls in the window.
- **31/63 (49%)** have the same `(city, target_date, direction)` family re-enter a new order within 1 hour of the pull.
- Mean price delta of the re-entry vs. the pulled order = **+0.0053** (i.e., the re-entered limit price is ~0.5¢ worse, on average, than the price just pulled) — a real but modest chase cost per re-entry given typical prices of 0.5-0.8.

Half of book-drift pulls chase back into the same family within the hour at a slightly worse price — consistent with the "infinite rest→pull→re-rest loop" failure mode the 2026-06-23 fix (`src/events/continuous_redecision.py:2575-2586`) was built to gate, though that fix only requires the drift to persist past the 5-minute floor, not that the chase actually recovers value.

## 6. Escalation outcomes (rest→cross)

```sql
-- escalated = rest_then_cross_policy == 'TAKER_ESCALATED_AFTER_REST' on matched cert
-- escalation_arm_floor_seconds = min(20min*60, REST_VALUE_REFRESH_MIN_AGE_SECONDS=300s) = 300s
--   (src/engine/event_reactor_adapter.py:24343-24346)
```

| lane | n commands | fill rate | filled-cohort chain-truth PnL (partial) |
|---|---:|---:|---:|
| `TAKER_ESCALATED_AFTER_REST` | 18 | **88.9%** | −$17.59 (9/16 positions reduced) |
| `REST_DEFAULT` (plain maker) | 456 | **31.6%** | −$39.40 (47/75 positions reduced) |

Escalated fills convert at **2.8x** the rate of plain maker rests, at comparable (small-sample, both negative) realized PnL so far — but the escalation lane is used for only **18 of 914** ENTRY commands in 30 days (2.0%), while §2 shows **573** unfilled GTC rests aged past the 5-minute arm floor (median unfilled-rest duration 6.6 min, p90 24.3 min — both above the 300s arm floor). The arming mechanism (`PASS 1`, `src/engine/event_reactor_adapter.py:24397-24417`) is cheap to trigger, but something downstream (the single-flight `unexpired_rest` antibody at `event_reactor_adapter.py:15216-15230`, or a fresh-book/spread-guard rejection at cross time) is suppressing escalation far more than the arm-eligible population would suggest. This is the highest-fill-rate lane in the whole funnel and it is nearly unused.

## 7. Ranked funnel leaks (by $ / EV magnitude, largest first)

1. **GTC maker-rest default captures 13.2% of submitted volume vs 90%+ for taker lanes, and 80% of all order volume goes through it.** (§1) — the single largest structural leak; every other finding below is a symptom or contributor.
2. **p_fill_lcb is severely overconfident at the mass point (0.999-1.0 bucket, 49% of decisions): 25.5% realized fill rate vs a near-certainty estimate.** (§3) — likely root cause feeding both the maker-default bias (§1) and the inflated forfeited-EV estimate (§4).
3. **$6,833 of approved order notional rested and died unfilled in 30 days vs $2,639 that was actually deployed (2.6:1) — $2,798 of decision-time EV forfeited on those pulls, while the filled cohort's chain-truth realized result over the same window is −$188 (partial).** (§4) — the forfeited pool dwarfs the realized result, whether read as "money left on the table" or as evidence the paper edge on unfilled orders was never real.
4. **The `TAKER_ESCALATED_AFTER_REST` lane converts at 88.9% (2.8x plain maker) but is used for only 18/914 (2%) of entries, despite 573 unfilled GTC rests aging past its 5-minute arm floor.** (§6) — cheapest, highest-leverage lever to pull: whatever is gating escalation past the arm condition (single-flight antibody / fresh-book re-check) is discarding the funnel's best-performing lane almost entirely.
5. **48.6% of all GTC maker book-presence time (116 of 240 aggregate hours) produces zero fill.** (§2) — pure dead weight on rate/queue budgets and duplicate-suppression windows, independent of any PnL question.
6. **Fill rate varies 6x by city at comparable volume (Buenos Aires 8% vs Seoul 53%)** (§1) and **half of BOOK_MOVED pulls chase back into the same family within an hour at a ~0.5¢ worse price** (§5) — smaller, second-order leaks; worth a city-tiered maker/taker policy and a chase-cost cap respectively, but neither approaches the magnitude of #1-#4.

---

**Biggest single lever**: fix or bypass whatever is currently blocking `TAKER_ESCALATED_AFTER_REST` from firing on the 573 unfilled GTC rests that already clear its 5-minute arm floor — that lane alone converts at 88.9% vs the 31.6% the same orders get today under `REST_DEFAULT`, and would recapture a large share of the $6,833/month currently dying as unfilled maker rests, without needing to touch the underlying edge model. p_fill_lcb recalibration (#2) is the second lever and the deeper fix, since it's plausibly why the system defaults to (and over-trusts) the maker-rest lane in the first place.
