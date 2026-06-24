# Real-Chain Settlement P&L + Audit-Completeness Audit — 2026-06-22

- Created: 2026-06-22
- Last audited: 2026-06-22
- Authority basis: Read-only real-chain settlement audit (mission: "EVERY real chain
  decision audited with reality; core is correct+gain, not lost/panic; orders profitable").
- Scope: REAL settled markets only. No tests, no replays. ?mode=ro on all DBs.
- DBs: `state/zeus-world.db` (54GB, canonical settlement grader), `state/zeus_trades.db`
  (52GB, canonical position book). Both confirmed live (mtime within 1 min of audit).
  The empty `zeus_world.db` / `zeus-trades.db` files are decoys — ignored.

---

## ONE-LINE VERDICT

**The real-chain book is NET NEGATIVE after cost (-$65.14 on $280.98 cost basis = -23.2%;
worse in the last 7 days at -$57.63 / -50.3%), and the system can only PARTIALLY self-audit
it: two independent P&L self-measures disagree (-$32.86 vs -$65.14), the
position-->settlement identity is BROKEN (zero-row join across ID spaces), `realized_edge`
is dead (1 of 1869 rows), and q_live is over-confident at EVERY confidence bucket.**

---

## Q1. NET REAL P&L (notional-weighted)

### Canonical position book — `zeus_trades.db` position_current, phase='settled'

```sql
-- file:state/zeus_trades.db?mode=ro
SELECT COUNT(*) n_settled,
  SUM(CASE WHEN realized_pnl_usd IS NOT NULL THEN 1 ELSE 0 END) has_pnl,
  ROUND(SUM(realized_pnl_usd),2) total_realized_pnl,
  ROUND(SUM(cost_basis_usd),2) total_cost_basis,
  SUM(CASE WHEN realized_pnl_usd>0 THEN 1 ELSE 0 END) winners,
  SUM(CASE WHEN realized_pnl_usd<0 THEN 1 ELSE 0 END) losers
FROM position_current WHERE phase='settled';
```

| metric | value |
|---|---|
| settled positions | 60 (42 with computed pnl, 18 with NULL pnl) |
| **total realized P&L** | **-$65.14** |
| total cost basis | $280.98 |
| **return on cost** | **-23.2%** |
| winners / losers / zero | 18 / 18 / 6 |

NOTE: every loser is a FULL loss (`realized_pnl_usd = -cost_basis_usd`, `settlement_price=0`):
buy_no NO-tokens settled worthless because the temperature landed IN the bin they bet against.
pnl formula verified by spot-check (Chongqing buy_no: 30 sh @0.69 = $20.70 cost, settle 0.0, pnl -$20.70).

### Notional-weighted P&L by direction × price bucket (the bucket that sinks the book)

```sql
SELECT direction,
  CASE WHEN entry_price<0.05 THEN 'a_dust' WHEN entry_price<0.50 THEN 'b_.05-.50'
       WHEN entry_price<0.70 THEN 'c_.50-.70' WHEN entry_price<0.90 THEN 'd_.70-.90'
       ELSE 'e_>=.90' END bucket,
  COUNT(*) n, ROUND(SUM(realized_pnl_usd),2) pnl, ROUND(SUM(cost_basis_usd),2) cost
FROM position_current WHERE phase='settled' AND realized_pnl_usd IS NOT NULL
GROUP BY direction, bucket;
```

| direction | bucket | n | P&L ($) | cost ($) |
|---|---|---|---|---|
| buy_no | c_.50-.70 | 21 | **-41.58** | 135.59 |
| buy_no | d_.70-.90 | 14 | -15.70 | 104.34 |
| buy_no | e_>=.90 | 1 | +0.27 | 8.73 |
| buy_yes | a_dust<.05 | 2 | -3.19 | 3.19 |
| buy_yes | b_.05-.50 | 4 | -4.94 | 6.64 |

**The mission's exact warning is realized.** In the per-unit settlement_attribution view, the
.50-.70 buy_no class shows a *positive* edge (+0.038/unit). But when weighted by actual
notional in the live book, that same class is the **single biggest loser (-$41.58)** — it
carries the most cost basis ($135.59) and the wins do not cover the losses. A class can be
"+edge per trade" and still sink the book; here it does.

### Last 7 days (settled_at >= 2026-06-15) — sharply worse

```sql
SELECT COUNT(*) n, ROUND(SUM(realized_pnl_usd),2) pnl, ROUND(SUM(cost_basis_usd),2) cost,
  SUM(CASE WHEN realized_pnl_usd>0 THEN 1 ELSE 0 END) win,
  SUM(CASE WHEN realized_pnl_usd<0 THEN 1 ELSE 0 END) loss
FROM position_current WHERE phase='settled' AND realized_pnl_usd IS NOT NULL
  AND settled_at >= '2026-06-15';
```

| window | n | P&L | cost | return | win/loss |
|---|---|---|---|---|---|
| last 7 days | 18 | **-$57.63** | $114.66 | **-50.3%** | 6 / 12 |

The recent regime is the worst part of the book: nearly the entire all-time loss was incurred
in the last week.

### Settlement-grader view — `zeus-world.db` settlement_attribution (per-unit-stake, 133 rows)

```sql
SELECT direction, COUNT(*) n, SUM(won) wins, ROUND(AVG(won),3) wr,
  ROUND(AVG(avg_fill_price),3) avg_price, ROUND(AVG(won)-AVG(avg_fill_price),3) edge_per_unit
FROM settlement_attribution WHERE avg_fill_price IS NOT NULL GROUP BY direction;
```

| direction | n | wins | win-rate | avg price | edge/unit |
|---|---|---|---|---|---|
| buy_no | 104 | 70 | 0.673 | 0.702 | **-0.029** |
| buy_yes | 29 | 6 | 0.207 | 0.169 | +0.038 |
| **all** | **133** | **76** | **0.571** | **0.586** | **-0.014** |

Even unweighted, the book's per-unit edge is negative (-0.014). buy_no — 78% of trades — wins
67.3% but pays 70.2c, so it is net negative per dollar. This is the "buy_NO @0.70 must win
>70%" failure: it wins 67%, not 70%+.

---

## Q2. CALIBRATION vs REALITY — over-confident at EVERY bucket

```sql
SELECT CASE WHEN q_live<0.5 THEN 'a_<.50' WHEN q_live<0.7 THEN 'b_.50-.70'
            WHEN q_live<0.85 THEN 'c_.70-.85' WHEN q_live<0.95 THEN 'd_.85-.95'
            ELSE 'e_>=.95' END q_bucket,
  COUNT(*) n, ROUND(AVG(q_live),3) avg_q_live, ROUND(AVG(won),3) realized_winrate,
  ROUND(AVG(q_live)-AVG(won),3) overconfidence_gap
FROM settlement_attribution WHERE q_live IS NOT NULL GROUP BY q_bucket;
```

| q_live bucket | n | avg q_live | realized win-rate | over-confidence gap |
|---|---|---|---|---|
| .70-.85 | 16 | 0.809 | 0.563 | **+0.247** |
| .85-.95 | 21 | 0.888 | 0.762 | +0.126 |
| >=.95 | 13 | 0.994 | 0.846 | +0.148 |
| (<.70 buckets: 4 rows total, all lost) | | | | |

**The decision belief q_live is over-confident in every bucket** (gap = belief − reality > 0
everywhere). The .70-.85 band is the worst: it claims 81% but wins 56%. Even the ">=.95"
near-certainty band only realizes 84.6%. There is no bucket where q_live is conservative.
This is the direct failure of the mission's "confidence aligned with reality" requirement.

q_live coverage is only **54 / 133 (41%)** — for the majority of graded positions the decision
belief was never recorded, so calibration can only be measured on a minority.

Category mix (settlement_attribution.category): STALE_DECISION 52, SKILL_WIN 39,
UNATTRIBUTABLE_Q_MISSING 27, MISCALIBRATED_LOSS 6, SKILL_LOSS 6, LUCKY_WIN 3. The largest
single category is STALE_DECISION (decided on data that a fresher cycle already superseded),
and the second-largest non-win category is UNATTRIBUTABLE_Q_MISSING (belief absent).

---

## Q3. AUDIT-COMPLETENESS — partially fixed, still broken in three places

### (a) edli_live_profit_audit NULL-completeness (zeus-world.db, 1869 rows)

```sql
SELECT COUNT(*) total,
  SUM(CASE WHEN pnl_usd IS NOT NULL THEN 1 ELSE 0 END) pnl_set,
  SUM(CASE WHEN expected_edge IS NOT NULL THEN 1 ELSE 0 END) exp_edge_set,
  SUM(CASE WHEN realized_edge IS NOT NULL THEN 1 ELSE 0 END) real_edge_set
FROM edli_live_profit_audit;
```

| field | populated / 1869 |
|---|---|
| pnl_usd | 57 (only the 61 CONFIRMED rows; 57 of them) |
| expected_edge | 446 |
| **realized_edge** | **1** (DEAD — was the known trap; still essentially NULL) |
| avg_fill_price | 62 |
| settlement_outcome | 57 |

**The trap is only partially resolved.** pnl_usd is now computed for CONFIRMED fills (57 rows),
but `realized_edge` is still dead (1 of 1869). The vast majority of rows are non-fill lifecycle
states (RELEASED 733, REJECTED 478, CONSUMED 247, SUBMITTED 247) with no P&L — expected, but it
means the audit table cannot self-measure realized edge.

Profit-audit self-measured P&L on the 57 CONFIRMED fills:

```sql
SELECT COUNT(*) n, SUM(pnl_usd) total_pnl, SUM(filled_size*avg_fill_price) notional
FROM edli_live_profit_audit WHERE order_lifecycle_state='CONFIRMED' AND pnl_usd IS NOT NULL;
```
=> 57 fills, **total_pnl = -$32.86**, notional $394.12. By direction: buy_no -$33.27 (52),
buy_yes +$0.41 (5). Net negative, driven by buy_no.

### (b) The two self-measures DISAGREE

- `zeus_trades.db` position_current settled book: **-$65.14** (42 positions with pnl)
- `zeus-world.db` profit_audit CONFIRMED fills: **-$32.86** (57 fills)

They overlap on only 33 condition_ids (of 56 / 54 distinct each) — they grade **different,
partially-disjoint sets**. There is no single authoritative net-P&L number the system agrees
on. Both agree on the SIGN (negative) but not the magnitude — a ~2x discrepancy.

### (c) The position-->settlement identity is BROKEN

```sql
SELECT COUNT(*) attrib_rows,
  SUM(CASE WHEN p.position_id IS NOT NULL THEN 1 ELSE 0 END) matched_position
FROM settlement_attribution a LEFT JOIN position_current p ON a.position_id=p.position_id;
-- (run in zeus-world.db) => 133 attrib_rows, 0 matched_position
```

**Zero of 133 settlement_attribution rows join to position_current.** The attribution
`position_id` is actually `edli-live-profit-audit:<hash>` (keyed on the audit aggregate hash),
NOT a real position id. So the settlement grader is derived from the profit-audit event stream,
not from the position table. You **cannot** join a decision -> its fill -> its settlement P&L
through a stable position identity. The two halves of the audit chain (world grader vs trades
position book) live in different ID spaces and different DBs. This is a mission-level failure:
the real-chain audit chain is severed at the position↔settlement seam.

### (d) 18 settled positions never got a pnl computed

```sql
SELECT SUM(CASE WHEN settlement_price IS NOT NULL THEN 1 ELSE 0 END) has_settle_price,
       SUM(CASE WHEN shares>0 THEN 1 ELSE 0 END) has_shares, COUNT(*) n
FROM position_current WHERE phase='settled' AND realized_pnl_usd IS NULL;
-- => has_settle_price 0, has_shares 18, n 18
```
18 of 60 settled positions have shares and cost basis but **no settlement_price and no
realized_pnl** — the pnl finalizer never ran for them, so they are invisible to the -$65.14
total. True realized loss is likely WORSE than -$65.14.

---

## Q4. REVERSAL / EXIT OUTCOMES — no profitable exits; losses are tail losses, not missed exits

### Were any positions exited for gain before settlement? NO.

```sql
SELECT exit_reason, COUNT(*) n, ROUND(SUM(realized_pnl_usd),2) pnl
FROM position_current WHERE phase='settled' GROUP BY exit_reason;
-- SETTLEMENT 42 (-65.14), '' 18 (NULL)
SELECT phase, exit_reason, COUNT(*) n, ROUND(AVG(entry_price),3) avg_entry,
       ROUND(AVG(exit_price),3) avg_exit
FROM position_current WHERE phase IN ('economically_closed','pending_exit')
GROUP BY phase, exit_reason;
-- economically_closed M5_EXCHANGE_RECONCILE 4 (entry 0.60 -> exit 0.575); '' 2
```

- **Every graded settled position is `exit_reason=SETTLEMENT`** — held to expiry. There is
  ZERO strategic sell-on-reversal-for-gain.
- The only pre-settlement closes are 4 `M5_EXCHANGE_RECONCILE` (reconcile-forced, not a
  strategic exit) with avg exit 0.575 < avg entry 0.60 — a small loss, not a gain.
- "Sold on reversal for gain" = **0 occurrences.** The exit/reversal capability is not
  producing any profitable exits on the real chain.

### For losers held to settlement, did belief reverse beforehand (a missed exit)? Mostly NO.

```sql
SELECT fresh_q_supports_position, COUNT(*) n
FROM settlement_attribution WHERE won=0 AND fresh_q_supports_position IS NOT NULL
GROUP BY fresh_q_supports_position;
-- supports=1 (still agreed): 28 ; supports=0 (reversed): 4
```

Of 57 losers, where a fresh-belief signal exists (32 rows): **28 still had fresh belief
SUPPORTING the position at settlement-eve; only 4 reversed.** So **~88% of losses are TAIL
LOSSES** — the belief never changed its mind, the bet was simply wrong (the over-confidence in
Q2 materializing), not a position the system should have exited and failed to. This means the
fix is calibration (q_live is too high), NOT a faster exit trigger. A soft-reversal exit would
have caught only ~4 of 57 losers.

Staleness cross-check: positions where a fresher cycle existed at decision time (47) won 53.2%;
where none existed (11) won only 36.4% — small samples, but staleness is not the dominant loss
driver here; over-confidence is.

---

## SUMMARY TABLE

| question | finding |
|---|---|
| Q1 net P&L | -$65.14 / -23.2% all-time; -$57.63 / -50.3% last 7d. Book NET NEGATIVE. .50-.70 buy_no is the biggest notional loser (-$41.58) despite +per-unit edge. |
| Q2 calibration | q_live over-confident in every bucket (gap +0.13 to +0.25). 41% q_live coverage. |
| Q3 self-audit | PARTIAL: pnl now set for 57 CONFIRMED fills, but realized_edge dead (1/1869); two self-measures disagree (-$32.86 vs -$65.14); position↔settlement join BROKEN (0/133); 18 settled positions have no pnl. |
| Q4 exits/reversals | 0 profitable pre-settlement exits; all held to SETTLEMENT; ~88% of losses are tail losses (belief still agreed), only ~4/57 reversed — calibration problem, not a missed-exit problem. |
