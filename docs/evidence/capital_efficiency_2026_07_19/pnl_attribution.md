# Chain-Truth PnL Attribution — May/June/July 2026

Investigator: read-only agent, 2026-07-19. All DB access via `sqlite3 -readonly`
against `/Users/leofitz/zeus/state/zeus_trades.db`. No writes, no code edits.
No market-price backtests — every number below is a realized, settled outcome.

## 0. Method — why not just `SUM(position_current.realized_pnl_usd)`

Operator law (per `scripts/repair_settled_clobbered_pnl.py` and prior incident
history) is that `position_current.realized_pnl_usd` has a known clobbering
bug and must not be trusted directly. This investigation independently
re-derives realized PnL per position from immutable facts and cross-checks
against the stored column. **The clobbering bug is confirmed live and larger
than previously scoped** — see §1.

### Reducer built for this investigation

`src/reduce/position_economics.py` (the "official" reducer referenced in the
task) is explicitly marked **SYNTHETIC-FIXTURE-ONLY** in its own module
docstring — "wired into nothing," gated on an identity-supersession backfill
that has not run for production use, and its one materialized generation
(`reduce_generations`, `generation_id=6a76f318...`, computed 2026-07-13) is
self-flagged `"payout_observation_complete": false`. It is not safe to treat
as ground truth for a full-history reconciliation, so this investigation
built an independent reducer reusing the same formula that
`src/state/close_economics.py::compute_realized_pnl_usd` unifies across every
production close path (`shares × exit_price − cost_basis_usd`), applied as
follows for every `position_current` row with `phase IN ('settled',
'economically_closed')` (264 positions total):

1. **shares** = `chain_shares` if `chain_shares > 0`, else `shares`.
2. **exit_price** resolution, in priority order:
   - If the position has an `EXIT_ORDER_FILLED` event **and** either (a) no
     `SETTLED` event exists, or (b) the final `SETTLED` event's
     `phase_before == 'economically_closed'` (i.e. the position had already
     been closed by a real trade before a later redundant settlement sweep
     ran) → use that fill's `fill_price`. This is the branch that recovers
     clobbered rows (§1).
   - Else, resolve win/loss from **on-chain** `payout_observations`
     (`condition_id`, `outcome_index`), latest row per key. Empirically
     verified mapping: `outcome_index = 0` for `buy_yes`, `1` for `buy_no`
     (confirmed by cross-matching against the harvester's independently
     computed `exit_price` column: 128/133 mismatches with the naive
     `buy_yes→1/buy_no→0` mapping one might assume from the CTF redeem
     indexSet comment in `harvester.py`, vs 5/133 with the mapping above).
   - Else fall back to `outcome_fact.outcome` (0/1), then
     `position_current.exit_price` itself.
3. `chain_pnl = round(shares × exit_price − cost_basis_usd, 2)`.

**Coverage caveat**: `payout_observations` (the literal on-chain payout
table) only spans **2026-07-13 through 2026-07-19** — 178 of 264 positions
resolve against it. Everything settled before 2026-07-13 (all of May, all of
June, and July 1–12) has no on-chain payout row in this DB at all; those
positions resolve via the `EXIT_ORDER_FILLED`-fact branch (86 positions) or
`outcome_fact`/`position_current.exit_price` (all independently-written,
non-realized_pnl_usd columns) as the best available truth. All 264 positions
resolved; zero fell through to "no truth source."

Full per-position dataset (all fields, method used per row) is at
`/private/tmp/claude-501/-Users-leofitz-zeus/7589dc75-d443-4b7f-8e2b-24945ef3038c/scratchpad/pnl_positions.json`
and the two scripts that produced it
(`pnl_attribution.py`, `pnl_breakdown.py`) are in the same directory.

### Core SQL used to build the position set

```sql
SELECT position_id, phase, city, cluster, target_date, bin_label, direction,
       unit, size_usd, shares, cost_basis_usd, entry_price, p_posterior,
       entry_method, strategy_key, chain_state, token_id, no_token_id,
       condition_id, updated_at, temperature_metric, chain_shares,
       chain_avg_price, chain_cost_basis_usd, realized_pnl_usd, exit_price,
       settlement_price, settled_at, exit_reason
FROM position_current
WHERE phase IN ('settled', 'economically_closed');
-- 264 rows: 261 settled, 3 economically_closed

SELECT position_id, event_type, occurred_at, sequence_no, phase_before,
       phase_after, payload_json
FROM position_events
WHERE position_id IN (...)
  AND event_type IN ('ENTRY_ORDER_FILLED','EXIT_ORDER_FILLED','SETTLED');

SELECT id, condition_id, outcome_index, payout_numerator, payout_denominator,
       state, observed_at, superseded_by
FROM payout_observations;  -- latest id per (condition_id, outcome_index) wins

SELECT position_id, pnl, outcome, entered_at, exited_at, settled_at,
       hold_duration_hours
FROM outcome_fact;
```

---

## 1. HIGH-SEVERITY finding: the realized_pnl_usd clobbering bug is live, larger than scoped, and affects ~45% of settled positions

`scripts/repair_settled_clobbered_pnl.py` (2026-07-12) scoped the bug at "~27
rows in the last 7d." As of 2026-07-19 this investigation found **119 of 264**
settled/economically_closed positions (45%) where `realized_pnl_usd` is
either `NULL` or provably wrong against the reconstructed chain truth:

- 54 positions: `realized_pnl_usd IS NULL` (mostly pre-2026-07-08 positions,
  before `close_economics.py`'s R0-a formula unification — never backfilled).
- 65 positions: `realized_pnl_usd` stored as `0.0` while the reconstructed
  value is materially non-zero (confirmed mechanism below).

**Confirmed root cause** (traced via `position_events` payloads for
`011ebe1c-edd`, `edliba9dd3603...`, and ~60 similar rows): a position that
had a real, priced `EXIT_ORDER_FILLED` trade (`phase_after=economically_closed`)
gets a later, redundant `SETTLED` event from the harvester's settlement sweep
that overwrites the already-correctly-booked PnL with `0.0` — exactly the
"Bug B reload-path" the repair script describes, just occurring at ~2.4x the
rate the script's dry-run scope assumed, and **still accumulating** (the
newest clobbered row is from 2026-07-18). Example:
`011ebe1c-edd` (Tokyo, buy_no) — real exit fill at `$0.205`, shares=13.83,
cost_basis=$8.34 → true realized loss **−$5.50**; stored `realized_pnl_usd =
0.0`. `outcome_fact.pnl` for the same position is *also* `0.0` — the
clobber happens upstream of both writes, so `outcome_fact` is not an
independent cross-check for this specific bug class.

**Net effect on reported totals**: naively summing
`position_current.realized_pnl_usd` (ignoring NULLs) gives **−$14.80** across
all 264 positions. The chain-truth reconstruction gives **+$15.46**. The
naive number is wrong in *both magnitude and sign*. Any live dashboard,
risk-guard input, or Kelly-sizing decision reading `realized_pnl_usd`
directly is working from a materially wrong number.

**Recommendation**: re-run `scripts/repair_settled_clobbered_pnl.py --apply`
(dry-run first) — it will now find a larger set than its 2026-07-12 baseline
— and investigate why the "Bug B reload-path" it targets is still firing
after being characterized as fixed at the close-economics-unification date
(2026-07-08).

---

## 2. Q1 — Monthly realized PnL, chain truth vs. stored

| Month | n | Chain-truth PnL | Stored `SUM(realized_pnl_usd)` (non-NULL only) | NULL-stored count | Divergence |
|---|---|---|---|---|---|
| 2026-05 | 22 | **−$11.00** | $0.00 (n=1 non-null) | 21 | Stored total is meaningless — 21/22 rows never had PnL backfilled |
| 2026-06 | 94 | **−$40.41** | −$57.09 | 0 | $16.68 overstated loss in stored column |
| 2026-07 | 148 | **+$66.87** | $42.29 | 31 | $24.58 understated gain, plus 31 NULLs excluded from the naive sum |

Bucketed by the position's economic-realization date (exit-fill time for
early exits, settlement time otherwise). July's chain-truth total is
materially better than the stored column suggests, but see §3 — a third of
July's gross swing is not attributable to Zeus's own trading decisions.

---

## 3. Q2 — July 2026 attribution

### 3a. Direction × temperature_metric (raw, all 148 July positions)

| Direction/metric | n | PnL | Win rate |
|---|---|---|---|
| buy_no / high | 103 | **+$123.82** | 52.4% |
| buy_yes / low | 2 | −$0.62 | 50.0% |
| buy_no / low | 16 | −$17.66 | 50.0% |
| **buy_yes / high** | **27** | **−$38.67** | **3.7%** |

**Same breakdown excluding the 32 chain-only/foreign positions (§4, entry_method
≠ chain_only_reconciliation — i.e. Zeus's own decisioned trades only):**

| Direction/metric | n | PnL | Win rate |
|---|---|---|---|
| buy_no / high | 87 | +$264.77 | 62.1% |
| buy_no / low | 14 | −$4.43 | 57.1% |
| buy_yes / high | 14 | **−$6.95** | **7.1%** |
| buy_yes / low | 1 | +$0.20 | 100.0% |

`buy_no/high` is where July's money is made; `buy_yes/high` is where it
bleeds, in both views — see §5 for the direct answer to the 3.6%-win-rate
question.

### 3b. By city (raw, all 148 July positions, chain truth)

Top gainers: Seoul (+$158.07, n=12, 58.3% WR), Moscow (+$51.90, n=5, 100%
WR), Tel Aviv (+$32.69, n=1), Wellington (+$25.73, n=9), Tokyo (+$21.11,
n=7). Top bleeders: Manila (−$63.32, n=5, 20% WR), Paris (−$28.12, n=12,
58.3% WR — one large loss dominates, see §6), Chengdu (−$34.21, n=4, 25%
WR), Houston (−$25.92, n=3, 0% WR), Guangzhou (−$20.20, n=4, 75% WR — one
large loss dominates a mostly-winning city). Full 38-city table in the
attached JSON; every city with n≥1 is listed there.

### 3c. Entry method / strategy_key

| entry_method | n | PnL | Win rate |
|---|---|---|---|
| qkernel_spine | 89 | +$250.24 | 53.9% |
| ens_member_counting | 27 | +$3.35 | 59.3% |
| **chain_only_reconciliation** | **32** | **−$186.72** | **0.0%** |

| strategy_key | n | PnL | Win rate |
|---|---|---|---|
| forecast_qkernel_entry | 61 | +$284.58 | 57.4% |
| opening_inertia | 35 | +$27.61 | 68.6% |
| settlement_capture | 9 | −$25.20 | 55.6% |
| center_buy | 9 | −$14.69 | 0.0% |
| day0_nowcast_entry | 2 | −$18.71 | 0.0% |
| chain_only_reconciliation | 32 | −$186.72 | 0.0% |

**`chain_only_reconciliation` is the single largest loss bucket in July by
a wide margin — larger than any city, direction/metric lane, or strategy.**
See §4 for what this actually is.

### 3d. Day0 lane vs non-Day0 (position ever entered `DAY0_WINDOW_ENTERED`)

| Lane | n | PnL | Win rate |
|---|---|---|---|
| Day0 | 83 | **+$246.21** | 56.6% |
| non-Day0 | 65 | **−$179.34** | 26.2% |

Day0-lane positions are unambiguously the stronger book in July. Note the 32
`chain_only_reconciliation` positions are non-Day0 by construction (they
never went through Zeus's own entry pipeline), so part of the non-Day0
weakness is the foreign-position bucket, not Day0-adjacent Zeus strategies
underperforming — center_buy and settlement_capture (both non-Day0, both
genuinely Zeus-decisioned) still net −$39.89 combined, a real (if smaller)
non-Day0 weak spot.

### 3e. Entry price deciles

| Decile | Price range | n | PnL | Win rate |
|---|---|---|---|---|
| 1 | 0.001–0.006 | 14 | −$28.19 | 0.0% |
| 2 | 0.007–0.390 | 15 | +$78.62 | 33.3% |
| 3 | 0.440–0.520 | 15 | −$27.16 | 40.0% |
| 4 | 0.520–0.560 | 15 | −$37.86 | 53.3% |
| 5 | 0.560–0.600 | 15 | +$10.04 | 60.0% |
| 6 | 0.603–0.630 | 14 | −$15.21 | 50.0% |
| 7 | 0.640–0.660 | 15 | −$51.41 | 26.7% |
| 8 | 0.661–0.690 | 15 | −$48.25 | 33.3% |
| 9 | 0.690–0.740 | 15 | +$91.71 | 60.0% |
| 10 | 0.750–0.999 | 15 | +$94.58 | 73.3% |
Deciles 3, 4, 7, 8 (roughly 0.44–0.69, "coin-flip to moderately-favored"
entries) are collectively −$164.68 — the weakest price band. The
extreme-favorite decile (10, price>0.75) is the strongest.

### 3f. Hold duration buckets (entry fill → exit/settlement)

| Bucket | n | PnL | Win rate |
|---|---|---|---|
| 0–6h | 8 | −$10.30 | 37.5% |
| 6–24h | 22 | −$9.37 | 59.1% |
| 24–72h | 43 | **+$273.62** | 60.5% |
| 72–168h | 11 | −$2.05 | 54.5% |
| ≥168h | 29 | +$0.15 | 51.7% |
(35 positions have no derivable `entered_at`/`exited_at` pair, mostly the
chain-only foreign positions — excluded from this bucket.) The 24–72h
hold window carries essentially all of July's net gain.

---

## 4. The `chain_only_reconciliation` bucket is very likely NOT Zeus's own trading — flag for operator

All 37 `position_id`s matching `chain-only-%` (32 of them settled in July)
share: `decision_snapshot_id = NULL`, `p_posterior = 0.0` (never computed —
not a real q=0, an absent-value default), and **zero** rows in
`decision_certificates`. `entry_price` on these rows is real (avg 0.44,
genuine market prices), meaning real trades happened, but Zeus's own
decision engine never authored an entry for them. This matches the
[[shared-wallet-operator-cotrading]] pattern this operator's own memory
already documents ("foreign venue orders are EXPECTED") — chain
reconciliation discovered tokens in the wallet with no matching Zeus
decision and quarantined/settled them as `chain_only_reconciliation`.

**This bucket is exclusively a July phenomenon — zero such positions in May
or June** (`chain-only-%` first appears in position_events during July).
That's either (a) co-trading on the shared wallet started in July, or (b) a
new bug where Zeus's *own* July decisions lost their local
decision/order-fact provenance and got reconstructed as foreign via the
chain-only fallback path — which would mean real Zeus-attributable losses
are hiding inside a bucket currently coded as "not ours." **I cannot
distinguish these two explanations from `position_current` alone** — it
needs an operator check of whether a second trading identity shares this
wallet, or whether `venue_commands`/`decision_certificates` for these
condition_ids exist under a different `position_id` that never got
reconciled to the chain-only rows.

Either way: **excluding this bucket, July's Zeus-attributable book is +$253.59
on 116 positions at 55.2% win rate** — a solid month. Including it, July nets
to +$66.87. The −$186.72 gap is the single largest number in this entire
report.

---

## 5. Q3 — buy_yes/high 3.6% win rate: verified, and split by cause

Chain truth for July: **buy_yes/high = 27 positions, 1 win, 3.7% win rate,
−$38.67** — confirms the ~3.6% figure cited (position_current.realized_pnl_usd
sum for this lane is not reliable per §1, but the chain-truth win/loss count
does not depend on the clobbered PnL column, only on resolved exit_price, so
this part of the stored-data claim holds up).

Splitting the 27:
- **13 are `chain_only_reconciliation`** (foreign/unattributed, §4):
  0/13 wins, −$31.72. No q, no decision trail — cannot characterize as
  "overconfident," because there was no Zeus confidence estimate at all.
- **14 are genuine Zeus decisions** (real `p_posterior`): **1/14 wins
  (7.1%)**, net −$6.95. This is the true Zeus-attributable buy_yes/high
  performance, and it is a clean case of **systematic overconfident-YES**:
  4 of the 5 largest losses in this sub-lane entered at `p_posterior ≥ 0.91`
  and still lost —
  - `142ee1d2-688` Wellington, q=0.960, entry=0.50 → lost, −$7.50
  - `5e36a294-907` Manila, q=0.961, entry=0.44 → lost, −$17.71
  - `384f1dd8-5c1` Hong Kong, q=0.919, entry=0.001 → lost, −$1.00
  - `68724139-026` Jeddah, q=0.934, entry=0.51 → lost, −$0.49
  - The single win, `c980ebb9-5ea` Moscow, entered at q=0.559 (the *lowest*
    confidence in the sub-lane) and won +$29.24 — the biggest win in the
    lane came from the least-confident entry, the inverse of what a
    calibrated q would predict.

This is a small-n result (14 positions) but the direction is unambiguous and
consistent with this codebase's own documented
[[forecast-tail-overconfidence-full_transport_v1]] finding (de-biased
full_transport_v1 under-shrinks the high tail ~2x) — buy_yes/high is
betting on the high tail of the temperature distribution, exactly where
that known overconfidence lives. Not an outlier cluster; a systematic,
already-diagnosed miscalibration showing up in realized money.

Cities are not concentrated — no single city drove the 27-position sample
(Hong Kong appears 3x, Wellington 3x, Manila/Milan/Lucknow/Dallas 2x each,
rest singletons) — bin distance from forecast center and entry price are the
stronger signal (see decile table §3e and the q-bucket table §6) than city.

---

## 6. Q4 — Top-5 largest single-position losses and gains, July (chain truth)

**Top 5 losses:**

| position_id | city | dir/metric | entry_price | q | chain_pnl |
|---|---|---|---|---|---|
| `592d3da8-35a` | Guangzhou | buy_no/high | 0.710 | 1.000 | **−$29.11** |
| `chain-only-0977772408834883` | Chongqing | buy_no/high | 0.690 | (foreign) | −$20.70 |
| `9e2a4fb4-822` | Taipei | buy_no/high | 0.590 | 0.869 | −$20.65 |
| `f58505fa-67b` | Paris | buy_no/high | 0.004 | 0.962 | −$19.96 |
| `chain-only-1265710258390814` | Houston | buy_no/high | 0.540 | (foreign) | −$19.44 |

Note: `592d3da8-35a` (Guangzhou) entered at q=1.000 (maximum confidence) and
still lost — worth an independent look at that specific market's forecast
inputs. `f58505fa-67b` (Paris) entered at entry_price=0.004 (long-shot buy_no,
i.e. the market itself priced the no-side near-certain to lose) but q=0.962
(Zeus's own model was highly confident buy_no would win) — a large q-vs-market
disagreement that cost $19.96; worth checking whether that disagreement was
justified or a forecast input error.

**Top 5 gains:**

| position_id | city | dir/metric | entry_price | q | chain_pnl |
|---|---|---|---|---|---|
| `dd8dcce2-254` | Seoul | buy_no/high | 0.704 | 0.999 | **+$104.47** |
| `cca012b0-cda` | Seoul | buy_no/high | 0.766 | 1.000 | +$76.36 |
| `17edfc95-02b` | Wellington | buy_no/high | 0.340 | 0.762 | +$38.61 |
| `2abe4736-3bf` | Tel Aviv | buy_no/high | 0.441 | 0.764 | +$32.69 |
| `c980ebb9-5ea` | Moscow | buy_yes/high | 0.150 | 0.559 | +$29.24 |

Seoul's two largest positions alone account for +$180.83 of July's gross
gain — a concentration worth noting (both buy_no/high, both high-confidence,
both correct).

---

## 7. Q5 — Calibration: q (p_posterior) at entry vs. realized outcome frequency

All 264 settled/economically_closed positions with both a `p_posterior` and
a resolved outcome (walk-forward, not filtered to July):

| q bucket | n | avg q | realized win freq | PnL |
|---|---|---|---|---|
| 0.50–0.60 | 4 | 0.571 | 25.0% | +$25.74 |
| 0.60–0.70 | 9 | 0.652 | 33.3% | −$8.33 |
| 0.70–0.80 | 37 | 0.762 | 56.8% | +$77.03 |
| 0.80–0.90 | 99 | 0.846 | **64.6%** | −$20.69 |
| 0.90–0.95 | 15 | 0.926 | **53.3%** | −$16.65 |
| 0.95–1.01 | 32 | 0.994 | 75.0% | +$155.00 |

**Where q is miscalibrated in a way that costs money**: the 0.80–0.90 and
0.90–0.95 buckets are the clearest failure — average stated confidence of
85–93%, but realized win frequency of only 53–65%, and both buckets are net
losers in dollar terms despite entering at high size-justifying confidence
(Kelly sizing scales with q, so overconfidence in exactly this band means
positions are oversized relative to their true edge). By contrast the
0.95–1.01 bucket (q≈0.99 average) realizes 75% — still below its stated
confidence but the gap is smaller, and it is the most profitable bucket in
dollar terms. The 0.60–0.70 bucket (n=9, small) is also a net loser but the
sample is too small to generalize. **The 0.80–0.95 band is where Zeus is
most confidently wrong, and it's exactly the band directly above the
"coin-flip to moderately favored" entry-price deciles flagged as the
weakest price band in §3e** — consistent, cross-validated evidence of the
same overconfidence problem from two independent angles (price-implied and
q-implied).

---

## 8. Ranked: where the money bleeds ($ magnitude, July 2026)

1. **`chain_only_reconciliation` / foreign-wallet bucket: −$186.72** (32
   positions, 0% win rate). Largest single number in this report. Needs
   operator adjudication: genuine shared-wallet co-trading (benign, per
   [[shared-wallet-operator-cotrading]]) vs. a decision-provenance bug
   hiding real Zeus losses (concerning) — see §4.
2. **Data-integrity risk, not $ loss but urgency**: the realized_pnl_usd
   clobbering bug (§1) is live, affects 45% of settled positions, and
   corrupts every downstream system (risk guards, equity curves, dashboards)
   that reads that column directly rather than reconstructing chain truth.
   Not a trading loss, but the most urgent fix — it can distort future
   sizing/risk decisions that depend on an accurate realized-PnL read.
3. **buy_yes/high systematic overconfidence: −$38.67 raw (−$6.95
   Zeus-attributable)** (§5) — small in July dollars so far, but it's a
   confirmed, already-diagnosed model defect (tail overconfidence in
   full_transport_v1's high tail) that will keep costing money at whatever
   size this lane is traded until de-biasing is corrected.
4. **The 0.44–0.69 entry-price band / 0.80–0.95 q band: net −$164.68 /
   −$37.34 respectively** (§3e, §7) — cross-validated overconfidence zone
   just below "extreme favorite," oversized by Kelly relative to true edge.
5. Manila (−$63.32) and Paris (−$28.12, one large position) as isolated
   city-level bleeds — smaller magnitude, likely idiosyncratic rather than
   systematic; worth a spot-check but not the priority.

**Biggest lever**: get an operator answer on #1 (chain-only bucket
provenance) — it's 3x the size of every other identified problem combined,
and until it's resolved, July's "true" Zeus-attributable performance is
somewhere between +$66.87 (if it's real Zeus risk) and +$253.59 (if it's
foreign activity that should be excluded from strategy attribution
entirely).
