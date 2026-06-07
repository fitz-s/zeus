# OBSERVE_BASELINE — The "Before" Number for The Path

> Created: 2026-06-07
> Authority basis: REALIGN_0_1_AUTHORITY.md (live authority = replacement_0_1); settlement truth = zeus-forecasts.settlement_outcomes WHERE authority='VERIFIED'
> Method: READ-ONLY (sqlite mode=ro) on LIVE DBs in /Users/leofitz/zeus/state — zeus_trades.db (7.4 GB active), zeus-forecasts.db, zeus-world.db. No writes, no ATTACH. The empty /state/zeus-trades.db and /state/zeus-trade.db are decoys; the active book is zeus_trades.db.
> Goal frame: profit = after-cost realized win-rate on markets actually TRADED (filled), settled against VERIFIED truth. NOT logloss, NOT forecast accuracy.

---

## THE SINGLE MOST IMPORTANT NUMBER

**The live system is currently a LOSER on its settled traded book. After-cost realized win-rate = 20.8% (5 wins / 19 losses), total realized PnL = −$2.54 on $38.44 deployed (−6.6% ROI), across n=24 filled-and-VERIFIED-settled positions, target dates 2026-05-17 → 2026-05-29. Of 13 cities traded, only 3 are net positive; 10 are net negative.**

Both halves of the goal fail: win-rate (20.8%) is far below the ~51% breakeven threshold (binomial p<0.01 — the win-rate signal is statistically real even though the dollar loss is small), and dollar PnL is negative. A pessimistic exit-adjusted cross-check (treating the 9 `M5_EXCHANGE_RECONCILE` exit fills as real sales) is worse still: 12.5% win-rate, −$13.34 (−34.7% ROI). Either lens, the traded book loses money.

This is the BEFORE-baseline. It measures the **superseded mainline `opening_inertia` buy_yes regime**, because that is the only cohort that has reached settlement. The live `replacement_0_1` path that REALIGN_0_1_AUTHORITY.md designates as current authority has **only just begun filling** (first live fills 2026-06-06) and **has zero settled positions** — its profitability is unmeasurable today.

### Reconciliation (every cross-check agrees)
| Check | Value | Cross-checked against |
|---|---|---|
| Filled positions (shares>0) | 50 | position_current |
| Settled & VERIFIED | 24 | settlement_outcomes authority='VERIFIED' join |
| Still-open (June, unsettled) | 23 (22 buy_no + 1 buy_yes), $167.51 cost | VERIFIED horizon ends 2026-06-06 |
| QUARANTINED-only (excluded) | 3 | authority='QUARANTINED', not truth |
| Win-rate | 5/24 = 20.8% | string winning_bin match: 24/24 agree, 0 parse failures |
| Realized PnL | −$2.54 / $38.44 (−6.6%) | per-city sum (+$21.41 / −$23.94) = −$2.54 |
| Positive cities | 3 of 13 | London +$11.58, Chicago +$8.83, Karachi +$1.00 |

Fees are genuinely zero (Polymarket): `fee_paid_micro` NULL on all 173 fills, `fee_rate_bps='0'` in raw on-chain payload, and `cost_basis_usd == shares*entry_price` exactly. So "after-cost" == cost-of-entry; no hidden fee drag.

---

## LENS 1 — Realized PnL / Win-Rate (the ledger)

**Verdict: BASELINE IS A LOSER.** 20.8% win-rate, −$2.54 (−6.6% ROI), n=24, 2026-05-17→05-29.

- All 24 settled positions are **buy_yes**; the YES bins overwhelmingly missed the observed temperature. The net loss is held up only by concentration: London 18°C won 3× on the same market/date (3 separate fills of one winning bin), plus a single Chicago and Karachi win.
- **Per-city winners:** London +$11.58 (3/3), Chicago +$8.83 (1/1), Karachi +$1.00 (1/1).
- **Per-city losers:** Miami −$6.97 (0/2), Kuala Lumpur −$3.27 (0/4), Wuhan −$3.16 (0/1), Tokyo −$2.65 (0/5), Munich −$1.66, Jeddah −$1.63, Shanghai −$1.34, Jakarta −$1.21, Singapore −$1.05, Manila −$1.00.
- **Systematic forecast misses, not noise:** Tokyo lost all 5 bets (bins 11–17°C, observed 16°C but YES bins still missed) and Kuala Lumpur lost all 4 (bins 25–29°C, observed 34°C — far outside). These were cheap lottery YES bets (entry 0.003–0.43) that didn't hit.
- **Strategy flip at the cohort boundary:** the 24 settled are 100% buy_yes; the 23 still-open June positions are 22 buy_no + 1 buy_yes, and hold 81% of total deployed cost ($167.51). The 20.8% baseline describes the OLD regime and may not predict the new buy_no book.

**Reliability:** TRUSTWORTHY. Win/loss validated 24/24 against the raw `winning_bin` STRING (not just numeric parse). Zero-fee triple-confirmed. Only VERIFIED settlement used; QUARANTINED excluded. position_current's own `realized_pnl_usd`/`settled_at` are populated for only 1 of 19 settled rows — **unreliable, NOT used**; PnL was recomputed from fills + settlement.

---

## LENS 2 — q_lcb Coverage (iron rule #6: is the lower bound honest?)

**Verdict: q_lcb is OPTIMISTIC / OVERCONFIDENT — NOT a true conservative lower bound.**

On the settled filled positions with a recoverable entry-time q_lcb: **pooled realized win-rate = 20.8% vs pooled mean entry q_lcb = 31.8%** (independent re-run reproduced 18.2% vs 27.4% on the n=22 exact-match subset — same direction, same verdict). Iron rule #6 requires realized ≥ q_lcb; it **FAILS in aggregate** and fails in most mid/high bands.

q_lcb definition is the system's own (read from `src/strategy/market_analysis.py:987–1000`): bootstrap CI is on EDGE (`edge = p_posterior − cost`), and the restore formula is `q_lcb = ci_lo + market_price`. So entry-time q_lcb = `entry_price + edge_ci_lower`, recovered from `selection_hypothesis_fact` (ci_lower, meta_json.entry_price, preferring selected_post_fdr=1, closest entry price).

**Most dangerous failures — high claimed lower-bound, settled out-of-the-money:**
| City / bin | entry q_lcb | result |
|---|---|---|
| Wuhan 26°C+ (May 19) | 0.697 | LOST (settled 23°C) |
| Jeddah 36°C (May 22) | 0.631 | LOST |
| Miami 84–85°F (May 18) | 0.607 | LOST |
| Miami 86–87°F (May 22) | 0.540 | LOST |

Band [0.6,0.7): 0% realized. Band [0.5,0.6): ~50% (n=2). Only the [0.4,0.5] and [0.9,1.0] bands look honest, and [0.4,0.5] is inflated by the London-18°C market counted 3×. The deep-OTM tail (q_lcb 0.0–0.2, 11 lottery bins) is "technically over" but economically trivial — the real damage is the 0.5–0.7 band paying for edge that did not exist (realized edge on the high-conviction bets was NEGATIVE).

**Likely root cause** (open): the dangerous cases all settled 2–3 bins away from prediction — pointing at ensemble spread / representativeness-sigma being too TIGHT at entry (narrow bootstrap edge CI because the forecast was overconfident), rather than a calibrator mis-map. Worth confirming against `settlement_sigma_floor`.

**Reliability:** TRUSTWORTHY in direction; per-band n is tiny (1–9), so band rates are anecdotal. `trade_decisions` was confirmed UNUSABLE as a q_lcb source (98 distinct order_id / 1560 rows; ci_lower=edge=0 for ~95% of rows). The FILLED universe is derived from append-only `position_events` / `position_current`, not the polluted decision log.

---

## LENS 3 — Strategy Attribution (who makes/loses money; has replacement_0_1 filled?)

**Has replacement_0_1 filled yet? YES — but nothing it filled has settled.**
- `edli_live_order_events`: **0** in zeus_trades.db and zeus-forecasts.db (as the context doc expected), **2,384 in zeus-world.db** — including **19 `UserTradeObserved` fills, first 2026-06-06T19:12Z, last 2026-06-07T15:56Z**. Before that, Jun 1 was 94 `SubmitRejected` vs 124 `VenueSubmitAttempted` — replacement_0_1 was attempting but being rejected at the venue until Jun 6.
- The 19 EDLI fills correspond to ~42 edli-sourced entry fills in execution_fact (opening_inertia 23, settlement_capture 14, center_buy 1, unknown 4) — granularity differs between the world event log and execution_fact. All from 2026-06-06+, all in the unsettled June cohort.

**Settled P&L by strategy (the only resolved attribution):**
| Strategy | Source | Settled markets | Net | Win-rate |
|---|---|---|---|---|
| opening_inertia | mainline | 14 | −$3.19 | 14.3% (2/14) |
| center_buy | mainline | 1 | $0.00 (breakeven, $5.34 notional) | 0/1 |
| settlement_capture | EDLI-only | 0 (none settled) | — | — |

`opening_inertia` is the only strategy with resolved PnL, and it loses. Losses concentrate in larger-notional ($0.78–$1.63) YES bets bought cheap (0.003–0.32) that settled to zero. Best single position +$4.14; worst −$1.63. `settlement_capture` is a new EDLI-only strategy that did not exist pre-cutover — entirely unsettled.

**Fill-quality contrast (apples-to-oranges caveat):** mainline opening_inertia entries show avg fill_quality −0.003 (mild adverse fill); EDLI-sourced entries show 1.0 (perfect) — but EDLI fills are recent, on different markets/liquidity, so the comparison is not yet meaningful.

**Coverage gaps (iron rule #1):** 6 zero-trade weekdays (2026-05-26, 06-01, 06-02, 06-04 had no `no_trade_events` either → system likely offline; 05-27/05-28 had `no_trade_events` dominated by `confidence_band_insufficient`). 126 venue_order_facts stuck in LIVE state (posted May 15–21, never updated) — possible stale state or locked collateral. `EXIT_CHAIN_MISSING_REVIEW_REQUIRED` on ~1,377 exited decisions — possible systemic exit-tracking gap.

---

## RELIABILITY CAVEATS (read before quoting the baseline forward)

1. **Small n.** 24 settled positions. The −$2.54 dollar figure is within noise of breakeven; the 20.8% win-rate is NOT (binomial p<0.01). Quote the win-rate as the strong signal, the dollar loss as directional.
2. **Cohort mismatch.** The baseline is 100% mainline buy_yes (superseded). The live authority (replacement_0_1, buy_no native-NO) holds 81% of deployed capital and has ZERO settled outcomes. **The baseline does not yet describe the path that is live.** It is the floor the new path must beat, not a forecast of it.
3. **Concentration.** 3 London fills of one winning market drive most of the positive PnL and the one "honest" q_lcb band. Per-market (not per-fill) the winning breadth is thinner.
4. **Exit-fill ambiguity.** The 9 `M5_EXCHANGE_RECONCILE` exit fills carry pnl=NULL — unknown whether real on-chain sales or reconciliation bookkeeping. If real, true realized PnL is −$13.34 and 3 settlement-winners were sold early at a loss (an exit-logic leak worth a follow-up).
5. **32 of 65 ENTRY_ORDER_FILLED early-May positions** have no city/bin payload — excluded from both q_lcb recovery and settlement matching. Their win/loss skew is unknown and could shift the aggregate.

---

## WHAT COULD NOT BE COMPUTED

- **replacement_0_1 realized profitability / q_lcb honesty** — the live path has 19 fills but 0 settled positions (June 6–9 markets settle June 7+); unmeasurable until they resolve. This is the number that actually matters going forward and it does not exist yet.
- **USD-denominated PnL for EDLI-sourced and settlement_capture positions** — unsettled.
- **Exit-fill economic reality** (real sales vs bookkeeping) — pnl=NULL.
- **The 50-filled vs 293-mainline-order gap** — out of scope for the realized-trade ledger; the 50 shares>0 rows are the actual traded book (rest voided/rejected/never-filled). Does not affect the baseline.
