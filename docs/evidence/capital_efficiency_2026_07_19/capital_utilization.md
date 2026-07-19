# Capital Utilization Audit — Zeus (2026-07-19)

## ERRATUM (post-publish correction)

Item 3 in "Ranked idle-capital causes" below is **corrected** per the follow-up
investigation in `m5_latch_persistence.md`: the "~55% of reduce_only trips are
GREEN + zero-second `ws_gap_active`" claim was wrong — that was the total
GREEN-risk-level reduce_only bucket, not the ws_gap-caused subset. The actual
`ws_gap_active`-driven GREEN count is **91 rows (2.8% of reduce_only rows, 0.6%
of all 15,251 exit_monitor cycles)**, with negligible measured blocked-entry cost
(3 requeue events in 12 days of logs) — **no fix warranted** on this cause. The
GREEN=1808 bucket was actually `kill_switch_armed`/`heartbeat_lost` (~1874 rows,
~58% of all reduce_only cycles — the real dominant driver, flagged for separate
investigation) plus `systemic_unknown_side_effect_count>0` (~412) plus the 91
ws_gap rows. All other findings in this report (idle fraction, single-winner
auction bottleneck, Kelly sizing cascade, collateral/cap analysis) stand
unchanged.

Read-only investigation. All queries run `sqlite3 -readonly`. All timestamps UTC.
Window: last 30 days (2026-06-19 → 2026-07-19) unless noted.

Databases: `state/zeus_trades.db` (money-path truth: collateral, position_current,
position_events, venue_commands/facts) and `state/zeus-world.db` (forecast/decision
certificates — `decision_certificates` and `edli_no_submit_receipts` are fresher
here; `zeus_trades.db`'s copies of those two tables stop growing around 2026-07-09,
see caveat in §3).

---

## 1. Bankroll timeline — daily composition and idle fraction

Source: `collateral_ledger_snapshots` (last snapshot per day, `authority_tier='CHAIN'`
only — `DEGRADED` rows report `pusd_balance_micro=0` and are a connectivity artifact,
not a real balance, so they're excluded) joined against a reconstructed open-position
interval set from `position_events` (`ENTRY_ORDER_FILLED` → `EXIT_ORDER_FILLED`/`SETTLED`,
cost basis from `position_current.cost_basis_usd`).

(Note: `entry_exposure_obligations` — the table the initial brief pointed at for
"in-position cost basis" — only has 143 rows total since 2026-07-11; it is a narrow
family-rebalance bookkeeping table, not a comprehensive open-position ledger. Using
it as "deployed capital" undercounts by ~10-30x. The interval reconstruction below
uses the full `position_events`/`position_current` trade history instead.)

```sql
WITH entry AS (
  SELECT position_id, MIN(occurred_at) AS entry_at
  FROM position_events WHERE event_type='ENTRY_ORDER_FILLED' GROUP BY position_id
),
exitc AS (
  SELECT position_id, MAX(occurred_at) AS exit_at
  FROM position_events WHERE event_type IN ('EXIT_ORDER_FILLED','SETTLED') GROUP BY position_id
),
cost AS (SELECT position_id, cost_basis_usd FROM position_current),
intervals AS (
  SELECT entry.position_id, entry.entry_at, exitc.exit_at, cost.cost_basis_usd
  FROM entry LEFT JOIN exitc ON exitc.position_id=entry.position_id
  LEFT JOIN cost ON cost.position_id=entry.position_id
  WHERE entry.entry_at >= '2026-06-19'
),
daily_snap AS (
  SELECT substr(captured_at,1,10) AS d, MAX(id) AS max_id
  FROM collateral_ledger_snapshots
  WHERE captured_at >= '2026-06-19' AND authority_tier='CHAIN' GROUP BY d
),
snap AS (
  SELECT daily_snap.d, s.captured_at, s.pusd_balance_micro/1e6 AS pusd_usd,
         s.reserved_pusd_for_buys_micro/1e6 AS reserved_usd
  FROM daily_snap JOIN collateral_ledger_snapshots s ON s.id=daily_snap.max_id
),
open_at_snap AS (
  SELECT snap.d, COALESCE((SELECT SUM(i.cost_basis_usd) FROM intervals i
    WHERE i.entry_at<=snap.captured_at AND (i.exit_at IS NULL OR i.exit_at>snap.captured_at)),0) AS open_cost
  FROM snap
)
SELECT snap.d, snap.pusd_usd, snap.reserved_usd, open_at_snap.open_cost,
       (snap.pusd_usd+open_at_snap.open_cost) AS total_bankroll,
       100.0*(snap.pusd_usd-snap.reserved_usd)/(snap.pusd_usd+open_at_snap.open_cost) AS idle_pct
FROM snap JOIN open_at_snap ON open_at_snap.d=snap.d ORDER BY snap.d;
```

Daily result (USD):

| date | idle wallet | reserved | open cost basis | total bankroll | idle % |
|---|---|---|---|---|---|
| 06-19 | 1076.5 | 59.0 | 31.8 | 1108.3 | 91.8 |
| 06-20 | 1109.2 | 0.0 | 45.4 | 1154.6 | 96.1 |
| 06-21 | 1092.2 | 13.4 | 50.0 | 1142.2 | 94.5 |
| 06-22 | 1039.7 | 13.4 | 35.5 | 1075.2 | 95.5 |
| 06-23 | 943.4 | 33.5 | 214.5 | 1157.9 | 78.6 |
| 06-24 | 916.7 | 43.2 | 309.1 | 1225.8 | 71.3 |
| 06-25 | 1137.6 | 26.7 | 284.9 | 1422.5 | 78.1 |
| 06-26 | 1160.7 | 26.7 | 228.7 | 1389.5 | 81.6 |
| 06-27 | 1160.7 | 26.7 | 204.7 | 1365.5 | 83.0 |
| 06-28 | 1119.5 | 26.7 | 226.6 | 1346.2 | 81.2 |
| 06-29 | 1077.4 | 73.6 | 261.1 | 1338.4 | 75.0 |
| 06-30 | 1145.1 | 26.7 | 220.8 | 1365.9 | 81.9 |
| 07-01 | 1237.9 | 32.2 | 217.8 | 1455.7 | 82.8 |
| 07-02 | 1233.1 | 26.7 | 265.8 | 1498.9 | 80.5 |
| 07-03 | 1253.1 | 26.7 | 233.3 | 1486.4 | 82.5 |
| 07-04 | 1264.2 | 0.0 | 204.9 | 1469.1 | 86.1 |
| 07-05 | 1252.5 | 0.0 | 58.6 | 1311.1 | 95.5 |
| 07-06 | 1245.9 | 0.0 | 75.8 | 1321.7 | 94.3 |
| 07-07 | 1262.9 | 0.0 | 133.0 | 1396.0 | 90.5 |
| 07-08 | 1202.4 | 0.0 | 167.6 | 1370.0 | 87.8 |
| 07-09 | 1230.4 | 0.0 | 130.7 | 1361.1 | 90.4 |
| 07-10 | 1253.4 | 0.0 | 121.2 | 1374.6 | 91.2 |
| 07-11 | 1072.0 | 0.0 | 223.4 | 1295.4 | 82.8 |
| 07-12 | 1146.3 | 0.0 | 220.7 | 1367.0 | 83.9 |
| 07-13 | 1208.8 | 0.0 | 175.9 | 1384.6 | 87.3 |
| 07-14 | 541.0 | 0.0 | 838.2 | 1379.2 | 39.2 |
| 07-15 | 656.0 | 0.0 | 689.8 | 1345.9 | 48.7 |
| 07-16 | 1377.9 | 0.0 | 675.5 | 2053.4 | 67.1 |
| 07-17 | 1154.8 | 3.0 | 447.6 | 1602.4 | 71.9 |
| 07-18 | 1520.2 | 3.0 | 300.2 | 1820.4 | 83.3 |
| 07-19 | 1551.2 | 3.0 | 131.2 | 1682.5 | 92.0 |

**Average idle fraction across 30 days: 82.1%.** Average total bankroll $1389.3;
average deployed (concurrent open cost basis) $239.5; average idle wallet cash
$1149.8. Every single day the majority of the bankroll sat unreserved and undeployed
in the wallet; the best days (07-14/07-15, a genuine multi-position pile-up) still
left ~40-50% idle.

---

## 2. Deployment concurrency

Interval reconstruction (same `intervals` CTE as §1) sampled hourly for 728 hours
(2026-06-19 → 2026-07-19):

```sql
-- (hours(t) recursive CTE generating 1-hour ticks, LEFT JOIN intervals on
--  entry_at <= t < exit_at, aggregate COUNT/SUM per hour)
```

| metric | value |
|---|---|
| total hours sampled | 728 |
| hours with zero open positions | 24 (3.3%) |
| avg concurrent open positions | 24.5 |
| peak concurrent open positions | 43 |
| avg concurrent open cost basis | $233.8 |
| peak concurrent open cost basis | $838.2 (2026-07-15, sustained ~24h) |

Two things stand out:
- The "peak" $838 (07-14→07-15) is one week's worth of accumulated small
  ($10-30) positions piling up simultaneously across many cities — it is not one
  large trade, it's low correlation-managed concentration.
- Zero-exposure hours are rare (3.3%) but the position count is misleading:
  24.5 average *concurrent* positions at an average size of ~$10 each is why the
  average **dollar** utilization is still only ~17% of bankroll (§1) despite
  "always having some position count on."

### Where deployment actually gets capped: funnel loss, not the collateral caps

`edli_live_cap_day_slots` / `edli_live_cap_rate_window` / `edli_live_cap_usage`
**cap nothing** — confirmed both by the code (`src/events/live_cap.py:5-19`: *"the
`tiny_live` mechanism... is DELETED... This ledger no longer rejects or clamps...
The ledger records the (uncapped) Kelly notional; it caps NOTHING"*) and by data:
`reservation_status='REJECTED'` has **zero rows ever** in either DB
(`zeus_trades.db`: CONSUMED=365, RELEASED=1005, REJECTED=0; `zeus-world.db`:
CONSUMED=584, RELEASED=1168, REJECTED=0).

The real, live-enforced caps are `src/risk_allocator/governor.py`'s `CapPolicy`
(defaults in `config/risk_caps.yaml`, wired at
`src/execution/executor.py:6640` → `_assert_risk_allocator_allows_submit` →
`assert_global_allocation_allows` → `RiskAllocator.can_allocate`,
`src/risk_allocator/governor.py:330`):

| cap | value | file:line |
|---|---|---|
| max_per_market | $250 | `src/risk_allocator/governor.py:66`, `config/risk_caps.yaml` |
| max_per_event | $500 | `src/risk_allocator/governor.py:67` |
| max_per_resolution_window (default) | $750 | `src/risk_allocator/governor.py:68` |
| max_correlated_exposure | $1000 | `src/risk_allocator/governor.py:69` |

Evidence these caps are **essentially never the binding constraint**: the two
largest settled positions in 30 days were $249.77 and $248.82 — both sitting right
at the $250 per-market ceiling (i.e. the cap did bind for these two trades
specifically), but the 3rd-largest settled position was $80.75 and the median is
under $15. With peak concurrent correlated exposure at $838 (vs the $1000 cap) and
per-market at $250 vs typical trade sizes of $10-30, these caps have ~10-25x
headroom over what the strategy typically proposes. **They are not why capital
sits idle.**

The real bottleneck is upstream, in the candidate → order funnel:

```sql
-- KellyDryRunCertificate passed=1 per day (zeus-world.db) vs
-- ENTRY_ORDER_FILLED per day (zeus_trades.db, position_events)
```

| date | Kelly-passed candidates | actual entries filled |
|---|---|---|
| 06-22 | 236 | 2 |
| 06-28 | 302 | 3 |
| 07-07 | 707 | 10 |
| 07-14 | 205 | 59 |
| 07-15 | 3 | 0 |

Conversion from "Kelly sizing passed, positive edge" to "order actually filled"
ranges from <1% to ~29%, with no obvious daily-volume relationship (07-07 had 10x
more passing candidates than 07-14 but 6x fewer fills). The proximate cause:
`src/engine/global_single_order_auction.py:1` — *"Pure cross-family coordinator
for **one current executable order**... joins already-prepared complete family
simplexes into **one** auction... delegates the terminal-wealth objective to
`select_global_single_order`."* Every auction cycle across the entire 40+-city
universe selects **at most one** winning candidate to submit, regardless of how
many candidates independently pass Kelly sizing that cycle. Cycle cadence is
~43s (10,645 `global_single_order_auction` decision_log rows over 5 days in the
window that table retains — `zeus_trades.db.decision_log` itself is rolling/pruned
past ~2026-07-14, see §3 caveat), so cycle *frequency* isn't the limiter — cycles
run constantly and mostly conclude "no trade" or lose to feasibility/exit-mutex
checks downstream of the auction. This single-winner-per-cycle design is a
structural throttle independent of bankroll size or Kelly output: it caps
*how fast* new capital can go to work regardless of how much idle cash or how
many simultaneously-profitable opportunities exist.

Also present but secondary: **`reduce_only_mode_active`** (governor.py:394) blocks
*all new entries* (not exits) whenever `ws_gap_active` is true, kill_switch is
armed, any reconcile finding is open, a systemic unknown side effect exists, or
`risk_level` is non-GREEN. Measured over 30 days of `exit_monitor` decision_log
rows (15,408 rows, each embeds a `held_monitor_allocator_refresh` snapshot):

```sql
SELECT COUNT(*), SUM(artifact_json LIKE '%"reduce_only": true%')
FROM decision_log WHERE mode='exit_monitor' AND timestamp>='2026-06-19';
-- 15408 total, 3269 reduce_only=true (21.2%)
```

Breaking that 3269 down by `risk_level` at the time: GREEN=1808 (55%),
DATA_DEGRADED=811, RED=306, ORANGE=245, YELLOW=99. The GREEN-risk-level cases are
notable: `risk_level` was healthy, `reconcile_finding_count=0`, yet
`reduce_only=true` because `ws_gap_active=true` — and per the code
(`governor.py:397`: `if governor_state.ws_gap_active: return True`), this trips
**unconditionally**, with no seconds threshold (unlike the harder kill-switch at
`governor.py:413`, which requires `ws_gap_seconds > 15`). Sample row shows
`"ws_gap_seconds": 0` alongside `"reduce_only": true` — i.e. a momentary/stale
websocket-gap flag blocks new entries even when the gap itself is recorded as
zero seconds. This fired in ~21% of exit-monitor cycles over the month; it
doesn't explain the bulk of the idle-capital picture (that's the funnel/sizing
story above) but it is a free, low-risk fix candidate since it has no
compensating benefit at 0-second gaps.

---

## 3. Cap-table binding events (as asked) — zero, and a data-freshness caveat

Direct answer to "how often did day slots / rate windows / cap usage actually
bind": **zero times** in the last 30 days (or ever) — `edli_live_cap_usage` has
never recorded a `REJECTED` row, and `edli_live_cap_day_slots` /
`edli_live_cap_rate_window` are populated but never read as limits (per the
`live_cap.py` docstring, they're inert since the 2026-06-08 operator directive
that deleted the `tiny_live` cap). Total `kelly_size_usd` "deferred/blocked by
caps" = $0, because nothing in this mechanism defers or blocks anything anymore.

Caveat: `zeus_trades.db.decision_certificates` and
`zeus_trades.db.edli_live_cap_usage` stop growing around 2026-07-09
(48 rows / 2 rows respectively that day, then nothing), while the same tables in
`zeus-world.db` continue live through 2026-07-19. `zeus-world.db.edli_no_submit_receipts`
similarly has no rows between 2026-06-30 and 2026-07-19 (last burst was
2026-05-31 → 2026-06-12, then a handful on 06-28/06-29, then silence). This audit
used `zeus-world.db` for anything needing recent `decision_certificates`, but flags
that **NO_SUBMIT receipt volume for the most recent 20 days is not observable** in
either DB — either nothing has been NO_SUBMIT'd in three weeks (plausible, given
`live_cap` caps nothing and the risk_allocator caps rarely bind) or the receipt
writer stopped running; this audit did not chase that down further and it's worth
a follow-up if no-submit visibility matters operationally.

---

## 4. Kelly sizing realism

`KellyDryRunCertificate` payloads (`zeus-world.db.decision_certificates`,
`passed=1`, last 30 days, n=2737):

```sql
SELECT AVG(json_extract(payload_json,'$.kelly_size_usd')),
       AVG(json_extract(payload_json,'$.bankroll_usd')),
       AVG(json_extract(payload_json,'$.kelly_multiplier'))
FROM decision_certificates WHERE certificate_type='KellyDryRunCertificate'
  AND decision_time>='2026-06-19' AND json_extract(payload_json,'$.passed')=1;
-- avg kelly_size_usd=15.84, avg bankroll_usd=1124.3, avg kelly_multiplier=0.0248, max size=355.92
```

The average *effective* Kelly multiplier actually applied is **2.48% of bankroll
times f\***, not the 25% base (`src/strategy/kelly.py:34`, default `kelly_mult=0.25`).
The shrinkage is the documented multiplicative cascade in
`src/strategy/kelly.py:424-517` (`dynamic_kelly_mult`): ci_width haircuts (0.7×/0.5×
cumulative), lead-time haircuts (0.6×/0.8×), portfolio-heat reciprocal
attenuation, `strategy_kelly_multiplier`, `city_kelly_multiplier`, plus the
phase-aware resolver's oracle penalty / observed-fraction / phase-source
haircuts (`kelly.py:197-270`). This is all intentional, documented risk
discipline — not a bug — but it is the dominant reason individual trade sizes
are $10-30 on a $1,100-1,550 bankroll (roughly 1-2% of bankroll per trade) even
before any collateral or allocator cap is checked.

Matching `kelly_decision_id` (via the shared event+token suffix, `edli_kelly:` ↔
`edli_intent:` prefix swap) to actual filled notional (`venue_trade_facts`,
`state='CONFIRMED'`, joined through `venue_commands.decision_id`):

```sql
-- 288 matched candidate→fill pairs, last 30 days
-- avg kelly_size_usd requested: $9.91
-- avg actual filled notional:   $10.08
-- avg fill_ratio (filled/requested): 0.948
```

**The execution layer delivers ~95-100% of the requested Kelly notional** — no
material rounding/min-size/partial-fill haircut once a trade is actually
selected and submitted. The sizing shrinkage happens entirely upstream, in the
Kelly-multiplier cascade (§ above) and in the single-order-per-cycle selection
funnel (§2) — not at order placement or fill time.

---

## 5. Settlement → redeploy latency

`collateral_unsettled_proceeds` (both `direction` values), last 30 days:

```sql
SELECT direction, COUNT(*), AVG((julianday(settled_at)-julianday(created_at))*24*60),
       MAX(...), SUM(amount_micro)/1e6
FROM collateral_unsettled_proceeds WHERE created_at>='2026-06-19' GROUP BY direction;
```

| direction | rows | avg dwell (min) | max dwell (min) | total $ |
|---|---|---|---|---|
| INCOMING_PROCEEDS (sell fills) | 30 | 0.34 | 0.99 | 312.5 |
| OUTGOING_DEDUCTION (buy fills) | 163 | 0.38 | 9.58 | 1504.7 |
| unsettled right now | 0 | — | — | — |

Every one of the 193 unsettled-proceeds rows in the window settled in **under 10
minutes**, most in under a minute; nothing is currently outstanding. **Settlement
dwell time is not a source of idle capital.** This is consistent with the
operator-law fact that Zeus never submits the redeem transaction itself
([[redeem-abandoned-third-party]] — a third party redeems); the *ledger*
reconciliation (this table) is near-instant, whatever separate on-chain redeem
timing exists for the underlying CTF tokens is outside this ledger's scope and
wasn't in reach of a read-only DB audit.

---

## 6. Return on deployed capital vs return on total bankroll

July 2026 (calendar month to date, 2026-07-01 → 2026-07-19):

```sql
SELECT COUNT(*), SUM(realized_pnl_usd), SUM(cost_basis_usd)
FROM position_current WHERE phase='settled' AND settled_at>='2026-07-01' AND settled_at<'2026-08-01';
-- 153 settled positions, realized_pnl_usd = +42.29, cumulative cost_basis_usd = 1887.52
```

Average bankroll and average *concurrently* deployed capital over the same
19 days (same interval-reconstruction method as §1, restricted to July):

```
avg_total_bankroll_july = $1426.5
avg_concurrent_deployed_july = $233.7   (16.4% average utilization)
peak_concurrent_deployed_july = $828.9
```

Three ways to read the same $42.29:

| basis | denominator | return |
|---|---|---|
| cumulative traded cost basis (turnover, ~8x turns of the same capital) | $1887.52 | **2.24%** |
| average concurrently-deployed capital (time-weighted, what was actually at risk) | $233.7 | **18.1%** over 19 days |
| average total bankroll (all capital, including idle) | $1426.5 | **2.96%** over 19 days |

**The gap is the utilization opportunity, stated plainly:** the strategy earns an
18.1%-over-19-days return on the capital it actually puts to work, but only
16.4% of the average bankroll is working at any given time — so the bankroll-level
return (2.96%) is a small fraction of what it would be if a larger share of the
$1,426 average bankroll were deployed at the same per-dollar edge. This is a
back-of-envelope scaling (it assumes the strategy could source ~6x more
simultaneously-profitable candidates at the same edge density without hitting
liquidity/depth or correlated-exposure limits — not verified here, and the
$1000 correlated-exposure cap plus per-city/per-market depth are real limits
worth checking before assuming linear scaling holds). What *is* directly
evidenced: nothing about collateral, settlement latency, or the live-cap ledger
is what's holding utilization at 16%. The two structural throttles are (a) the
Kelly-multiplier cascade producing ~1-2%-of-bankroll trade sizes, and (b) the
single-winning-order-per-auction-cycle design capping how many of those small
trades can go live per unit time.

---

## Ranked idle-capital causes ($ magnitude)

1. **Single-order-per-auction-cycle bottleneck** (`global_single_order_auction.py`)
   — hundreds of Kelly-passed candidates/day, ~2-30 actual entries/day; this is
   the largest unexplained gap between "capital available + edge exists" and
   "capital deployed." No $ cap value to quote (it's a throughput limiter, not
   a notional limiter) but it directly explains why average concurrent deployed
   capital ($233.7) is ~16% of average bankroll ($1426.5) even on days with
   hundreds of passing candidates.
2. **Kelly-multiplier cascade** (`kelly.py` dynamic_kelly_mult + phase-aware
   resolver) — average *effective* multiplier 2.48% of bankroll × f\*, avg trade
   size $9.91-15.84 vs $1100-1550 bankroll. Intentional risk discipline, but it
   is the second-largest reason exposure per trade stays small.
3. **`reduce_only_mode_active` on stale/zero-second `ws_gap_active`** — blocks
   all new entries in ~21% of exit-monitor cycles over 30 days; ~55% of those
   trips occur at `risk_level=GREEN` with `ws_gap_seconds=0`, i.e., a data-quality
   flag rather than genuine risk elevation. Free removal candidate: gate
   `ws_gap_active`'s reduce-only trip on the same `ws_gap_seconds_limit`
   threshold the kill-switch already uses (governor.py:413), instead of
   tripping unconditionally (governor.py:397).
4. **risk_allocator per-market/event/correlated caps ($250/$500/$750/$1000)** —
   evidenced binding for exactly 2 of 225 settled trades in 30 days (both sized
   right at the $250 per-market ceiling). Real but marginal at current position
   sizes; would only start mattering if (1) or (2) above were loosened.
5. **Settlement/redeem dwell time** — not a cause. Sub-10-minute ledger
   settlement on 100% of observed unsettled-proceeds rows.
6. **`edli_live_cap_*` day-slot/rate-window/usage tables** — not a cause. Caps
   nothing since 2026-06-08 by design; zero REJECTED rows ever.
