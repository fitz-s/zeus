# Structural (forecast-free) edge hunt — Polymarket weather neg-risk books

- Created: 2026-06-14
- Authority basis: operator RULE 1 ("edge exists; find it") — hunt FORECAST-INDEPENDENT
  structural mispricing in the existing order books, distinct from the (dead) forecast edge.
- Data: `state/zeus_trades.db::executable_market_snapshots` (3.72M snapshots, 10,623 bins,
  1,282 city·date neg-risk groups, 2026-05-15 → 2026-06-14). Settlement:
  `state/zeus-forecasts.db::settlement_outcomes` (7,026 VERIFIED).
- Mode: read-only. Raw outputs in `/tmp/arbhunt/` (cache.db, ANALYZE.txt, DEPTH.txt, FULLBT.txt).

## VERDICT

**NO HARVESTABLE STRUCTURAL EDGE.** A real K-bin YES-basket lock exists but is depth-capped at
the venue minimum order (~$5–10 capital/city·date). Across the **entire 30-day history**, taking
the single best basket per city·date, **99 of 506** evaluable city·dates showed a positive lock at
N=5 shares/bin, total realized locked profit **$7.76** (~$0.26/day; avg **+7.8¢ per ~$5 basket**).
The edge **inverts to a loss at N≥10–25 shares/bin** — one level into the book eats it entirely.
Single-bin locks and crossed books do **not exist** (the venue floors yes+no at $1.001). The books
are efficiently priced for any size a trader would care about.

## Venue mechanics established first (load-bearing)

- **Realized taker fee = 0.0**, licensed. `src/contracts/fee_authority.py` documents incident
  2026-06-12: the CLOB `base_fee=1000bps` in `fee_details_json` is the fee-schedule CAP, not the
  realized fee. `state/fee_reconciliation.json`: n_fills=42, observed_max_fee_fraction=0.0,
  fitted 2026-06-12, fresh (<30d). **All arb math below runs at 0% fee** — the most generous case.
  (Even nonzero, the fee shape is `fee_rate·p·(1−p)`, ~0 at the extremes where these books sit.)
- **Every market is neg_risk=1** (3.72M/3.72M). Each city·date = one neg-risk group of K bins
  (K=11 for 730 groups; 4–8 for most others). Each bin is its own condition_id with a YES and NO
  token, sharing the `event_slug`. Exactly one bin resolves YES (MECE — open-ended end bins make
  the grid collectively exhaustive), so a full YES-basket pays **exactly $1/unit by construction**;
  a full NO-basket pays exactly **(K−1)/unit**.
- Snapshot caveat handled: `orderbook_depth_json` carries the book for ONE selected token per row
  (mostly the NO side: 9,431 NO vs 1,192 YES in the latest cross-section). The analysis re-keys
  every snapshot by (event_slug, captured-second, side) and only scores **complete simultaneous
  same-second batches** (all K bins, one side) — this is what kills the time-misalignment false
  positives that a naïve latest-per-bin cross-section produces.

## Check-by-check results

### 1 & 2. K-bin basket arb (YES and NO) — REAL signal, depth-capped, NOT harvestable

Strict simultaneous complete batches (all K bins, same captured-second, one side):

| Basket | complete batches | Σ distribution | "locks" (Σ<fair) |
|---|---|---|---|
| YES | 37,676 (506 slugs) | Σ(yes_ask): median **1.089**, p5 1.013, **min 0.931** | 822 batches, Σ<1 |
| NO  | 12,397 (249 slugs) | Σ(no_ask)−(K−1): median **+0.059**, p5 −0.010, min −0.053 | 1,210 batches |

Median Σ is **above** fair (1.089 YES / +0.059 NO) — a structural venue overround, the opposite of
free money. A minority of batches dip below fair. Depth-walking the **best YES candidates** (buy N
shares of every bin, payout = N×$1):

```
Dallas  2026-06-15  Σtop=0.9310  N=5:+$0.35  N=10:+$0.34  N=25:-$0.99  N=50:-$4.82  N=100:-$28.23
London  2026-06-08  Σtop=0.9390  N=5:+$0.31  N=10:+$0.07  N=25:-$1.91  N=50:-$19.33
Paris   2026-06-08  Σtop=0.9410  N=5:+$0.29  N=10:-$0.06  N=25:-$7.46  N=50:-$22.31
NYC     2026-06-15  Σtop=0.9370  N=5:+$0.31  N=10:+$0.44  N=25:-$0.60  N=50:-$3.29
```

The top-of-book asks are a handful of penny-resting orders (~5 shares each); level 2 is far worse,
so the basket flips negative immediately past the venue minimum. These are the **best 4 of 822**.

**Full backtest** (best complete YES batch per city·date, depth-walked, FEE=0, graded MECE payout=$1):
- 506 city·dates evaluable. N=5sh/bin feasible in 505; **profitable in 99**; median lock5 = **−$0.125**.
- **Sum of ALL positive locks at N=5 over 30 days = $7.76** → avg **+7.8¢ per ~$5 basket**, best +34.5¢.
- N=10sh/bin: profitable in 77; median **−$0.313**. Net of the median, the strategy LOSES money;
  only a hand-picked positive subset is green, and only at the $5 floor.

NO-basket: same story — the −5.3¢ headline gaps sit on bins with minsz≈0 (dust); not scalable.

### 3. Single-bin lock (yes_ask + no_ask < 1) — DOES NOT EXIST

406,737 same-second YES&NO pairs. **min(yes_ask+no_ask) = 1.0010**, p5 = 1.0010, median 1.010.
**Zero** true simultaneous single-bin locks. The venue holds both sides ≥ $1.001 at every observed
instant. The mint/sell side (yes_bid+no_bid>1) is the mirror and equally absent (median sum ~0.99).

### 4. Crossed / stale books — DOES NOT EXIST

**0** crossed-book snapshots (bid ≥ ask) across all 1.42M cached books. No internal free money.
Stale-price-vs-settled was not separately harvestable: the penny (0.001) asks that look like stale
locks are the resting-floor of losing bins and are precisely what makes the YES-basket Σ dip < 1 —
already captured (and shown depth-capped) in check 1.

### 5. neg-risk capital-efficiency mispricing — NOT PRESENT

The full-NO basket prices at median (K−1)+0.059, i.e. the market already prices the NO set at ~its
(K−1) collateral value, not at full-K collateral. No structural discount to capture; the venue's
neg-risk collateral efficiency is already in the quotes.

## Why this is not harvestable (the binding constraint)

The edge is **liquidity-bound, not pricing-bound**. The penny mispricings are real but each rests on
~5 shares. Realizable profit ≈ **$0.08–0.35 per city·date, ~3–4 city·dates/day positive**, i.e. on
the order of **$0.25–1.00/day gross** before any gas/operational cost, and it requires firing 11
simultaneous min-orders per basket. Past ~$10 capital every basket is negative. There is no size.

## What WOULD change this verdict (none currently true)

- A depth shift letting the sub-$1 YES-basket fill at ≥$100/bin (never observed: deeper levels are
  always richer). 
- A nonzero realized rebate making maker-resting the 11 bins inside the spread +EV at size (check 4
  shows spreads exist, but capturing them needs fill probability the data can't promise forecast-free).
- A true crossed book or a single-bin sub-$1 (zero occurrences in 30 days / 406k pairs).

## Reproduction

`/tmp/arbhunt/`: `extract.py` → `cache.db` (per-condition recent books, indexed),
`analyze.py` → `ANALYZE.txt` (simultaneous batch + single-bin), `depth.py` → `DEPTH.txt`
(depth-walk of top candidates), `fullbt.py` → `FULLBT.txt` (full realized-lock backtest).
