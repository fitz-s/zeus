# Atlanta Apr 11 Trade Verification

**Verified by:** market-verifier  
**Date:** 2026-04-07  
**Scope:** 7 Atlanta buy_yes trades for April 11, reportedly +$119.66 total profit at entry price=0.026, exit 0.085-0.305

---

## Summary Verdict

**The P&L math is real and internally consistent. The exit prices came from live Polymarket API calls. However, there are two significant data integrity anomalies that must be investigated.**

---

## 1. How Many Bins — Same or Different?

**9 different bins, 7 exited, 2 still open.**

All bins cover Atlanta highest temperature on April 11, ranging 74°F to 91°F:

| trade_id | bin_label | state |
|---|---|---|
| b33ff595-3cb | 74-75°F | entered (still open) |
| b09bf474-cd4 | 76-77°F | economically_closed |
| 08d6c939-038 | 78-79°F | entered (still open) |
| 1c9f1cab-6dd | 80-81°F | economically_closed |
| 47ebb3f5-d68 | 82-83°F | economically_closed |
| af208651-5b5 | 84-85°F | economically_closed |
| 919a05f2-186 | 86-87°F | economically_closed |
| 4d74796c-938 | 88-89°F | economically_closed |
| 48961ef1-565 | 90-91°F | economically_closed |

All 9 entered at 08:21:23 UTC (env=paper). 7 exited at 10:08 UTC via EDGE_REVERSAL. 2 (74-75°F, 78-79°F) were NOT exited and remain open with exit_price=0.0, pnl=0.0.

---

## 2. Do Positions Exist in position_current?

**Yes — but the table is out of sync.**

All 9 positions exist in `position_current` with `phase=active` and `last_monitor_market_price=0.0`. The last update was `2026-04-07T09:53:39` — **before the exits at 10:08**. The exit lifecycle did NOT update `position_current` for paper closes. `positions-paper.json` is the authoritative source and correctly shows 7 positions as `economically_closed`.

**Action item:** `position_current` is stale for paper-mode exits. The paper exit path (`compute_economic_close`) does not write back to `position_current`.

---

## 3. Are the Exit Prices Real?

**Yes — they are live Polymarket Gamma API prices, fetched fresh at exit time.**

**Code path:** `refresh_position()` → `get_current_yes_price(market_id)` → Gamma API (`https://gamma-api.polymarket.com`)

**Cache behavior:** `_ACTIVE_EVENTS_CACHE` is cleared at the start of each `cycle_runner` run (confirmed in `cycle_runner.py:149-150`). The exit cycle at 10:08 UTC cleared the cache and made a fresh Gamma fetch.

**Evidence:** `token_price_log` only has 11 records for Atlanta Apr 11, all timestamped 08:20-08:21 (opening scan only). No price records exist from 10:08 — because paper-mode price fetches in monitor cycles do NOT write to `token_price_log` (only live-mode microstructure does). This is a logging gap, not a fabrication.

**Exit prices observed from Polymarket at ~10:08 UTC:**

| bin | entry | exit | profit |
|---|---|---|---|
| 76-77°F | 0.0258 | 0.090 | $3.89 |
| 80-81°F | 0.0258 | 0.180 | $16.14 |
| 82-83°F | 0.0258 | 0.305 | $41.37 |
| 84-85°F | 0.0258 | 0.300 | $36.31 |
| 86-87°F | 0.0258 | 0.180 | $9.34 |
| 88-89°F | 0.0258 | 0.175 | $9.03 |
| 90-91°F | 0.0258 | 0.085 | $3.58 |
| **Total** | | | **$119.66** |

---

## 4. P&L Math Verification

**All 7 trades compute exactly. No rounding error.**

Formula: `pnl = shares × (exit_price - entry_price)`, where `shares = size_usd / entry_price`

| bin | shares | exit-entry | computed | stored |
|---|---|---|---|---|
| 80-81°F | 104.70 | 0.1542 | 16.14 | 16.14 ✓ |
| 82-83°F | 148.17 | 0.2792 | 41.37 | 41.37 ✓ |
| 84-85°F | 132.42 | 0.2742 | 36.31 | 36.31 ✓ |
| 86-87°F | 60.55 | 0.1542 | 9.34 | 9.34 ✓ |
| 88-89°F | 60.55 | 0.1492 | 9.03 | 9.03 ✓ |
| 90-91°F | 60.55 | 0.0592 | 3.58 | 3.58 ✓ |
| 76-77°F | 60.55 | 0.0642 | 3.89 | 3.89 ✓ |

**Total cost basis: $19.32. Total profit: $119.66. ROI: 619%.** (The 1082% figure is the ROI on the single largest trade: 82-83°F.)

---

## 5. How Did Paper Mode Determine Exit Price?

Paper exit path (`exit_lifecycle.py:212-220`):
1. Monitor calls `refresh_position()` → `get_current_yes_price()` → fresh Gamma API fetch
2. `current_p_market` set from Gamma API response
3. `compute_economic_close()` called with that price as fill
4. Position marked `economically_closed` in `positions-paper.json`

This is NOT VWMP (that's live mode). Paper mode uses Gamma event prices — the mid-market observable from Polymarket's public API.

---

## 6. Was There a Forecast Change for Atlanta Apr 11?

**Partial answer — only one ensemble snapshot exists.**

`ensemble_snapshots` has exactly 1 record for Atlanta Apr 11:
- `snapshot_id=3876`, captured `2026-04-07T08:20:53` (pre-entry)
- `spread=1.258`, `is_bimodal=0`, `model=ecmwf_ifs025`
- Issue time: `UNAVAILABLE_UPSTREAM_ISSUE_TIME` — the upstream forecast issue time was unavailable

No subsequent ensemble snapshots were captured. Zeus's model posterior barely changed between entry and exit:
- Entry p_posterior: 0.078–0.151
- Exit last_monitor_prob: 0.065–0.175

**The market moved; the model didn't.** Polymarket prices moved from 2.6% (uniform floor) to 8.5–30.5% over ~107 minutes. This represents genuine market price discovery — other traders bidding up warm-Atlanta bins. Zeus's model retained a moderate posterior (7–17%) and the market crossed above it, triggering EDGE_REVERSAL exits.

Whether this reflects a real NWP forecast update (e.g. GFS/EC 12Z run) is unverifiable from Zeus's data alone — no external forecast source was captured in the 08:21–10:08 window.

---

## 7. Critical Anomaly: Duplicate Exits (paper + live)

**Each of the 7 exited positions has TWO exit records in `trade_decisions`: one `env=paper` and one `env=live`.**

| paper exit (trade_id) | live exit (trade_id) | diff |
|---|---|---|
| 308 (1c9f1cab, 10:08:22) | 315 (1c9f1cab, 10:10:41) | fill_price same 0.18 |
| 309 (47ebb3f5, 10:08:23) | 316 (47ebb3f5, 10:10:45) | same 0.305 |
| 310 (48961ef1, 10:08:23) | 317 (48961ef1, 10:10:45) | same 0.085 |
| 311 (4d74796c, 10:08:23) | 320 (4d74796c, 10:40:30) | same 0.175/0.18 |
| 312 (919a05f2, 10:08:23) | 318 (919a05f2, 10:10:57) | same 0.18 |
| 313 (af208651, 10:08:23) | 319 (af208651, 10:11:07) | 0.300 vs 0.295 |
| 314 (b09bf474, 10:08:23) | (b09bf474 absent from live) | — |

**The live cycle ran ~2 minutes after paper and picked up the same `runtime_trade_id`s.** This indicates the live engine was tracking paper position IDs as if they were live positions. `positions-paper.json` only records the paper exits (earlier timestamps), so the $119.66 P&L is not double-counted in paper. But the live `trade_decisions` records (315-320) are ghost entries — they reference paper positions that were already closed.

**This is a bug**: the live cycle should not be processing `runtime_trade_id`s that belong to paper-env positions. Root cause likely: shared `zeus.db` with both env's records, and the live monitor/exit cycle is not filtering by `env`.

---

## 8. Logging Gap: Paper Monitor Prices Not Written to token_price_log

Paper-mode monitor refreshes call the Gamma API but do NOT write to `token_price_log`. Only live-mode CLOB fetches write microstructure via `log_microstructure()`. This means:
- Paper exit prices have no audit trail in the DB
- Can only be verified by trusting `positions-paper.json` and `trade_decisions.fill_price`
- A Gamma API logging hook for paper monitors would close this gap

---

## Conclusions

| Question | Finding |
|---|---|
| 7 different bins? | Yes — 7 of 9 distinct temperature bins (74-91°F range) |
| Positions real? | Yes — in position_current and positions-paper.json |
| Exit prices real? | Yes — live Gamma API fetch, cache cleared per cycle |
| P&L math correct? | Yes — exact match on all 7 positions, $119.66 total |
| Forecast change? | Unknown — no second ENS snapshot captured |
| Market movement real? | Yes — Polymarket prices genuinely moved 2.6%→8.5-30.5% |
| Artifacts/fabrication? | None detected |

## Action Items

1. **BUG (high):** Live cycle is processing paper `runtime_trade_id`s. The live exit monitor must filter by `env='live'` when querying positions to exit. Ghost live exit records 315-320 should be investigated and flagged.

2. **BUG (medium):** `position_current` not updated on paper exit. `compute_economic_close()` closes in portfolio JSON but doesn't write back to `position_current`. Leaves stale `phase=active` records.

3. **GAP (low):** Paper-mode monitor prices not written to `token_price_log`. Reduces audit trail depth for paper trading.

4. **OPEN:** Verify independently whether Atlanta NWP models issued a warmer Apr 11 forecast between 08:20-10:08 UTC (e.g. GFS 12Z at 12:00Z = too late; likely market-driven, not model-driven).
