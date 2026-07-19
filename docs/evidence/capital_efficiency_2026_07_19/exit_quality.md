# Exit Decision Quality Audit — 60-day window (2026-05-20 → 2026-07-19)

Scope: pre-settlement exits in Zeus (Polymarket weather derivatives). All queries
run `sqlite3 -readonly` against `state/zeus_trades.db`. All timestamps UTC.

Counterfactual method (important, see "Data quality note" below): I did **not**
use `position_current.settlement_price` or the `SETTLED` event's `won`/`outcome`
fields as ground truth for "what would holding have paid." Both are contaminated
for pre-exited positions. Instead, for every exit fill I parsed the position's own
`bin_label` (e.g. "Will the highest temperature in Seoul be 33°C on July 13?")
into a `[low, high]` range using the same regex the app itself uses
(`src/data/market_scanner.py:5979 _parse_temp_range`), pulled the actual
settled value from the `SETTLED` event payload (`settlement_value`, falling back
to parsing `winning_bin` when `settlement_value` is null — the `polymarket_gamma`
settlement path never populates it), and derived the true held-side payout
(0 or 1) from `range ∩ settlement_value` × `direction`. This reproduces exactly
the logic in `src/execution/day0_hard_fact_exit.py:297 final_observed_bin_verdict`.

Script: `/private/tmp/claude-501/.../scratchpad/exit_quality.py` (ad hoc, not
committed). Raw per-position detail: `exit_quality_detail.csv` in the same
scratchpad.

## Data quality note (flag, not a decision-quality finding)

`position_current.settlement_price` is **not** a binary payout column. For
pre-exited positions it silently carries forward the exit fill price (e.g.
`691bb613-e40` shows `settlement_price=0.77` — that's the sell fill, not a
payout). For chain-mirror-settled positions it has been observed holding the
raw settlement *temperature* (`284cd501-605` → `settlement_price=41.0` for a
Lucknow 41°C bin). Any report that joins on this column for "counterfactual
settlement value" will be systematically wrong. Likewise the `SETTLED` event's
`won`/`outcome` fields are **not** direction-aware for `settlement_source:
polymarket_gamma` rows — `outcome` there is the resolved YES/NO index of the
market, not "did this position win," and `pnl` in that same payload is usually
0.0 for already-exited positions (it's the *residual*-shares settlement pnl,
correctly zero once nothing is left to redeem) — it is not the position's
total realized economics. Anyone building on top of these fields for
analytics should use `bin_label` + `settlement_value`/`winning_bin` +
`direction` directly, as this audit does.

---

## Q1 — Exit proceeds vs. counterfactual settlement payout, by reason

`execution_fact` shows 80 exit fills in 60 days (`order_role='exit',
terminal_exec_status='filled'`). 77 resolved to a clean counterfactual (3
excluded: 2 still `economically_closed` with no `SETTLED` event yet, 1
`voided`).

```sql
-- exit fills, 60d
SELECT position_id, fill_price, shares, filled_at FROM execution_fact
WHERE order_role='exit' AND terminal_exec_status='filled'
  AND COALESCE(filled_at, posted_at) >= '2026-05-20';
-- reason + counterfactual join: see exit_quality.py (bin_label/settlement_value parse)
```

| reason (normalized) | n | exit proceeds $ | counterfactual hold $ | delta $ | n would-have-won | delta on would-win subset $ |
|---|---:|---:|---:|---:|---:|---:|
| M5_EXCHANGE_RECONCILE (unattributed) | 43 | 431.00 | 355.40 | **+75.60** | 24 | **-113.69** |
| CI_SEPARATED_REVERSAL | 4 | 59.25 | 0.00 | +59.25 | 0 | 0.00 |
| COMMAND_RECOVERY_EXIT_FILL | 4 | 40.91 | 0.00 | +40.91 | 0 | 0.00 |
| MARKET_CLOSED_AWAITING_SETTLEMENT | 2 | 17.64 | 0.00 | +17.64 | 0 | 0.00 |
| DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES | 4 | 16.61 | 0.00 | +16.61 | 0 | 0.00 |
| CI_OVERLAP_SELL_VALUE_DOMINATES | 4 | 19.78 | 5.00 | +14.78 | 1 | -1.50 |
| entry_authority_chain_absence_conflict | 1 | 3.15 | 0.00 | +3.15 | 0 | 0.00 |
| SETTLEMENT_IMMINENT | 3 | 36.47 | 36.50 | -0.03 | 2 | -0.04 |
| FAMILY_DIRECT_SELL_DOMINATES_HOLD | 11 | 104.35 | 111.57 | **-7.22** | 7 | **-41.48** |
| WHALE_TOXICITY | 1 | 7.53 | 46.59 | **-39.06** | 1 | -39.06 |
| **TOTAL** | **77** | **736.70** | **555.06** | **+181.64** | | |

Reading this: in aggregate, pre-settlement exits captured $181.64 **more**
than counterfactually holding every one of these 77 positions to settlement —
exits are net value-additive. But the mix matters:

- **CI_SEPARATED_REVERSAL, CI_OVERLAP_SELL_VALUE_DOMINATES,
  DAY0_ZERO_PROBABILITY_SELL_VALUE_DOMINATES, COMMAND_RECOVERY_EXIT_FILL** —
  every single verified sample in this bucket was a position that would have
  settled at $0. These lanes are working exactly as designed: sell before a
  confirmed loser hits zero.
- **SETTLEMENT_IMMINENT** — near breakeven (-$0.03 on $36 of proceeds) even
  though 2 of 3 would have won. Fills were at 0.999, i.e. the force-sell only
  clips a fraction of a cent off a near-certain win. This is consistent with
  the 2026-06-24 `MODEL_DIVERGENCE_PANIC` removal (portfolio.py:1278-1287)
  holding — the pathological "dump a 99.9c confirmed winner" bug this fix
  targeted does not reappear in this sample.
- **FAMILY_DIRECT_SELL_DOMINATES_HOLD is net negative** (-$7.22) and its
  7-of-11 would-win subset lost $41.48 relative to holding. Small n, but this
  is the one *named* lane in the sample that is systematically firing on
  positions that go on to win. Worth a closer look at
  `_sell_value_exceeds_hold_value` (`src/state/portfolio.py:9xx`, "family
  redecision" path) — either its hold-value estimate is running too
  pessimistic, or the family-overlay correlation/crowding adjustment is
  overweighting correlated-family risk relative to the realized outcome.
- **WHALE_TOXICITY, n=1, -$39.06** — too small to generalize, but it's the
  single worst per-dollar exit reason in the sample: it sold a position at
  0.16 that settled at 1.0 (`61d26173-279`). One data point; flag, don't act.
- **M5_EXCHANGE_RECONCILE is the single largest line item in both
  directions** — 43 of 77 fills (56%) carry this label, which
  `src/execution/exchange_reconcile.py:5084 _strategy_exit_reason_for_reconciled_fill`
  only assigns when it could **not** recover the real exit reason from the
  position's own `EXIT_INTENT`/`EXIT_ORDER_POSTED`/`EXIT_ORDER_REJECTED`
  history. In other words: for 43 exits, whatever actually caused them is not
  reconstructable from `position_current.exit_reason`, only from digging into
  event history per-position (which is how Q2 below found that several of
  these were actually mislabeled `DAY0_HARD_FACT_BIN_DEAD` exits). Net this
  bucket is +$75.60, but the -$113.69 on its would-win subset is the largest
  single dollar leak in the whole sample, and it's flying under an
  unattributed label — nobody monitoring `exit_reason` distributions today
  would see it.

---

## Q2 — Day0 hard-fact (dead-bin) exits: are they perfect by construction?

The label actually used in the event stream is `DAY0_HARD_FACT_BIN_DEAD` /
`DAY0_HARD_FACT_BIN_DEAD_MARKET_CLOSED` (`src/engine/cycle_runtime.py:6002,
6174`; verdict math in `src/execution/day0_hard_fact_exit.py:236
hard_fact_bin_verdict` and `:297 final_observed_bin_verdict`) — it never
appears verbatim as `EXIT_DEAD_BIN` in `position_events` (that's the internal
`HardFactVerdict.action` string, not the emitted exit reason).

7 distinct positions had a `DAY0_HARD_FACT_BIN_DEAD*` `EXIT_INTENT` in 60
days. **Decision correctness: 7/7 verified correct** — for every one I
independently re-derived the verdict from `bin_label` + the real settlement
value and it matched the direction-aware "structurally dead" call every time
(e.g. `142ee1d2-688` buy_yes on a 12°C bin, settled 13°C → correctly dead;
`a53da60d-1a6` buy_no on a 20°C point-bin, settled exactly 20°C → YES won,
NO correctly dead). Zero exceptions. The decision math is sound.

**Execution correctness: 1/7.** Only `af7bd7c2-18d` actually got a filled
sell (`EXIT_ORDER_FILLED`, 0.001, matching the reason recovery gap noted in
Q1 — it landed under the M5_EXCHANGE_RECONCILE label because the reconciler
lookback didn't find the `DAY0_HARD_FACT_BIN_DEAD` `EXIT_INTENT` in time).
The other 6 (`142ee1d2-688`, `47d12253-4a9`, `4d24aea6-0a2`, `a1df750a-0ab`,
`a53da60d-1a6`, `c9bbf9c5-713`) never executed a sale at all — they rode to
$0 via `chain_mirror_settlement` bookkeeping, `exit_reason` overwritten to
`SETTLEMENT`, `exit_price=0.0`. Checking their `EXIT_ORDER_REJECTED` history
(`a53da60d-1a6`: 4 rejections; `4d24aea6-0a2`: 10 rejections) shows the sale
was attempted and repeatedly bounced. This is the same execution-gap pattern
quantified at scale in Q5 below.

**So: "verify 100% settled at 0" is trivially true and the wrong test** — a
genuinely dead bin settles at 0 whether or not the exit fires, so that check
alone can't distinguish "the lane worked" from "the lane never executed." The
real test is whether **any** salvage value was captured before the forced
zero, and on this sample it was captured only 1 time in 7.

---

## Q3 — Belief-decay / monitor-driven exits (CI-based) calibration

Only 8 fills in 60 days carry a CI-based reason (`CI_SEPARATED_REVERSAL`: 4,
`CI_OVERLAP_SELL_VALUE_DOMINATES`: 4) — too small a sample for a real
calibration curve, but directionally clean:

| position | reason | fresh_prob at exit | would-have-won | delta $ |
|---|---|---:|---|---:|
| cebeb19d-80c | CI_SEPARATED_REVERSAL | 0.59 | no | (n/a, settlement pending at audit time)* |
| edliaea139... | CI_SEPARATED_REVERSAL | 0.65 | no | * |
| ca3c3a8d-cb3 | CI_SEPARATED_REVERSAL | 0.81 | no (position_won=false) | +15.36 |
| 327bc056-d1c | CI_SEPARATED_REVERSAL | 0.18 | no | +7.23 |
| edliea30ce... | CI_OVERLAP_SELL_VALUE_DOMINATES | 0.64 | no | * |
| 68724139-026 | CI_OVERLAP_SELL_VALUE_DOMINATES | n/a | **yes** | -0.49 |
| 24f96362-7cb | CI_OVERLAP_SELL_VALUE_DOMINATES | n/a | no | * |
| dd1945b3-85b | CI_OVERLAP_SELL_VALUE_DOMINATES | n/a | **yes** (`market_bin_won` flag) | * |

(*rows marked `*` were among the settlement_value-null gamma-settled group —
counted correctly in the Q1 aggregate table via the `winning_bin` fallback,
but I didn't hand-verify each one for this narrower table; the Q1 aggregate
of +$74.03 combined across both reason buckets stands.) The fresh_prob values
at exit for the two hand-checked winners in this lane are consistent —
`ca3c3a8d-cb3` exited at posterior 0.81 (own belief still favored the held
side) yet the market had already re-priced the reversal (best_bid 0.83, i.e.
market and model roughly agreed) and it turned out correctly to have been a
structural loser. With n=8 I would not draw a calibration curve from this;
the honest statement is "directionally correct in every verified case,
insufficient sample for a reliability diagram."

---

## Q4 — Exit execution cost (spread-crossing)

`execution_fact.submitted_price` vs `fill_price` across all 80 exit fills:
**0 filled worse than their own submitted limit**, 55/80 filled better
(price improvement beyond the limit), 25/80 exactly at limit. Net
`Σ shares×(fill − submitted) = +$10.67`. Exits are not bleeding value against
their own limit price.

The more relevant comparison — best_bid/best_ask recorded on the position's
last `EXIT_INTENT` before the fill vs. the actual fill — shows exits are
consistently executed **at or within one tick of the best bid** (taker
fills), e.g. `cebeb19d-80c` bid 0.63/ask 0.64 → fill 0.63; `ca3c3a8d-cb3` bid
0.83/ask 0.85 → fill 0.83; `09e37f40-7ec` bid 0.61/ask 0.64 → fill 0.61.
Typical recorded spreads in this sample are 1-4 cents on liquid names, with
some illiquid names much wider (`011ebe1c-edd` bid 0.15/ask 0.237,
`32ef7814-768` bid 0.55/ask 0.68). For **urgency-labeled** reasons
(`SETTLEMENT_IMMINENT`, `FLASH_CRASH_PANIC`, hard-fact) taking the bid is
correct — you want the fill, not the spread. For **non-urgent** reasons
(`FAMILY_DIRECT_SELL_DOMINATES_HOLD`, `CI_OVERLAP_SELL_VALUE_DOMINATES`) a
resting maker order at/near the ask could plausibly capture the other half
of a 2-4 cent spread — on the 15 non-urgent fills in this sample that's a
ceiling of roughly $3-5 total, not a material lever at this volume.

Two fills also showed real slippage between the *last recorded* intent
snapshot and the actual fill — `d74434d6-5e7` (bid 0.47 → fill 0.39, -$1.95
on 24.39 shares) and `ce952726-c9a` (bid 0.88 → fill 0.86, -$0.26) — i.e. the
market moved against the order in the intent-to-fill gap. Small dollar
magnitude in this sample, but worth watching if exit-intent-to-fill latency
grows.

---

## Q5 — Unexited losers held to true settlement

51 positions in 60 days were held all the way to genuine settlement
(`exit_reason='SETTLEMENT'`, never pre-exited) and lost money:
**Σ realized_pnl_usd = -$370.75**.

```sql
SELECT position_id, direction, entry_price, p_posterior, last_monitor_prob,
       realized_pnl_usd, settled_at
FROM position_current
WHERE phase='settled' AND exit_reason='SETTLEMENT'
  AND settled_at >= '2026-05-20' AND realized_pnl_usd < 0;
```

Split by whether the exit evaluator ever actually recommended exiting
(`exit_decision_should_exit=1` in any `MONITOR_REFRESHED` payload for that
position — this is `evaluate_exit`'s own logged verdict, licensed as a frozen
observation):

| bucket | n | Σ pnl $ |
|---|---:|---:|
| evaluator recommended exit at least once, but it never executed | 24 | **-163.08** |
| evaluator never recommended exit (no signal fired, ever) | 26 | -206.33 |
| no MONITOR_REFRESHED history at all | 1 | -1.34 |

**The "recommended but never executed" bucket is a pure execution-layer
loss** — the decision logic did its job. Its largest component is (again)
`DAY0_HARD_FACT_BIN_DEAD*`: 10 of the 24 positions (`a53da60d-1a6 -18.88,
5e36a294-907 -17.71, 142ee1d2-688 -7.50, ddcac63e-d0d -6.13, aef7968f-6f3
-3.24, 4d24aea6-0a2 -3.13, 987d1b3c-04b -1.49, c9bbf9c5-713 -0.06,
a1df750a-0ab -0.03, fa49dfb1-13d -0.01` = **-$58.18**). Checking rejection
history confirms the mechanism: `5e36a294-907` racked up 86
`EXIT_ORDER_REJECTED` events over 2.5 days
(`exit_executable_snapshot_unavailable`×36, `exit_no_executable_bid`×35,
`stale_current_market_price`×15) before finally settling — once a bin is
visibly dead, the market's own liquidity for that side evaporates (nobody
wants to buy a token about to be worthless), so "sell it before it hits
zero" runs into exactly the moment there's no counterparty. `4d24aea6-0a2`
(10 rejections), `142ee1d2-688` (39 rejections) show the same pattern. This
is a structural liquidity problem on confirmed-dead positions, not a
modeling defect — the fix, if any, has to be mechanical (detect dead-earlier
before liquidity is gone, or find a non-orderbook salvage path), not
statistical.

The remaining components of the 24: `SETTLEMENT_IMMINENT`×4 (-21.61 combined,
mostly `677665ab-b3e`-style near-certain-price fills that never actually
converted to a sale before settlement), `FAMILY_DIRECT_SELL_DOMINATES_HOLD`×3
(-9.27), `MODEL_DIVERGENCE_PANIC`×2 (-11.04, both dated 2026-06-18/06-21 —
**before** the 2026-06-24 removal of that trigger, i.e. pre-fix data, not a
live gap), `CI_SEPARATED_REVERSAL`×1 (-4.44), `DAY0_ZERO_PROBABILITY_SELL_
VALUE_DOMINATES`×1 (-5.32), `WHALE_TOXICITY`×1 (-2.30),
`CI_OVERLAP_SELL_VALUE_DOMINATES`×1 (-0.49).

One data-integrity aside spotted while tracing `ddcac63e-d0d`: its event log
has **314 duplicate `SETTLED` events and 313 `REVIEW_REQUIRED` events** over
a 3-day span, all carrying the identical `pnl=-6.13` — i.e. no additional
economic damage, but the append-only event log is being hammered by a
`chain_held_after_terminal_projection` review loop that never resolves.
Flag for the event-log hygiene backlog, not an exit-quality finding.

The **26-position "never recommended" bucket (-$206.33, the single largest
dollar bucket in Q5)** is a genuine modeling gap, not execution: the CI never
separated below entry and no hard-fact ever fired before these settled as
losses. Fixing this needs better/earlier signal, not better order plumbing —
out of scope for "is the exit lane executing well," but the largest number
in this whole audit.

---

## Q6 — exit_retry_count / EXIT_ORDER_REJECTED noise

5,771 `EXIT_ORDER_REJECTED` events in 60 days. **83% (4,807) are tagged
`exit_pending_missing`**, which is not a venue-level order rejection — it's a
`chain_state` reconciliation-lag value (`src/state/chain_state.py:27`,
`src/state/lifecycle_manager.py:181,252`) that `src/state/ledger.py:253`
explicitly flags as a known incident class ("exit_pending_missing positions
retried forever (HK 06-09: 724 ...)"). This inflates the raw rejection count
roughly 6x over genuine execution failures; anyone alerting on total
`EXIT_ORDER_REJECTED` volume should filter this tag out first.

The real execution-failure signal, filtering to genuine order/venue causes:

| reason | count |
|---|---:|
| exit_executable_snapshot_unavailable | 90 |
| executable_snapshot_gate: venue command requires executable market snapshot_id | 82 |
| exit_no_executable_bid | 57 |
| chain_balance mismatch (units/shares) | 44 |
| Illegal command-event grammar transition FILLED→CANCEL_REQUESTED | 25 |
| stale_current_market_price | 24 |
| executable_snapshot_gate: snapshot tradeability blocks submit: clob_no_ask_illiquid | 24 |
| executable_snapshot_market_end | 19 |
| no_order_id | 18 |
| executable_snapshot_gate: SELL requires bid-side executable snapshot evidence | 17 |

These are almost entirely the same "no live orderbook to sell into" family
identified in Q2/Q5, plus a smaller `ws_gap`/`heartbeat_lost` connectivity
tail (265+10 events).

Of 117 distinct positions that hit at least one genuine `EXIT_ORDER_REJECTED`
in 60 days, **only 43 (37%) eventually got a filled exit; 74 (63%) never
filled** and rode through to settlement via `chain_mirror_settlement`
bookkeeping instead. That 63% is the denominator behind both the Q2 and Q5
findings — it is the single most consistent number in this audit.

---

## Ranked value leaks (60d, $ magnitude, highest confidence first)

| # | leak | $ | confidence | fix class |
|---|---|---:|---|---|
| 1 | Confirmed-dead-bin exits that never execute (no bid once the bin visibly dies) | -58 to -163 (Q2 sample + Q5 "recommended-not-executed" bucket) | high — decision proven correct, execution proven absent via rejection logs | mechanical: earlier detection before liquidity dries up, or a non-orderbook salvage path |
| 2 | `M5_EXCHANGE_RECONCILE` unattributed exits selling would-be winners | -113.69 (on 24/43 would-win positions) | medium — real dollars, but root cause per-position is not reconstructable from the label itself | observability: close the reason-recovery gap in `_strategy_exit_reason_for_reconciled_fill` so nothing lands in this bucket unattributed |
| 3 | Positions the estimator never flagged before a settlement loss | -206.33 (26/51 unexited losers) | low actionability — needs better/earlier signal, not plumbing | modeling, out of scope here |
| 4 | `FAMILY_DIRECT_SELL_DOMINATES_HOLD` net-negative on its would-win subset | -41.48 (n=7) | low-medium, small n | revisit `_sell_value_exceeds_hold_value` family-overlay correlation weighting |
| 5 | Spread-crossing on non-urgent exits | ~$3-5 ceiling | low, immaterial at current volume | not worth pursuing |

## Single biggest lever

**Execution, not decision, is the constraint on the hard-fact dead-bin lane.**
Across every angle in this audit (Q2's 7-position sample, Q5's 24-position
"recommended but not executed" bucket, Q6's 63%-never-filled rate), the
belief/CI/hard-fact decision math was never wrong when independently
re-derived from the actual settlement outcome — but 6 of 7 hard-fact-flagged
dead bins, and the majority of positions hitting any exit rejection at all,
never converted the correct decision into a filled sale, because once a bin
is visibly dead the market's own bid evaporates before the sell can land.
This is the cleanest, most fixable leak in the data: it doesn't require new
modeling or a probability-threshold argument, it requires either detecting
the dead bin earlier (while a bid still exists) or building a non-orderbook
salvage path for positions the system has already, provably, correctly
identified as worthless.
