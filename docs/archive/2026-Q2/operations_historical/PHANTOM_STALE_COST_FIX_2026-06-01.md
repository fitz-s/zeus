# Phantom Stale Cost Fix — 2026-06-01

## Root Cause

EDLI buy_no candidates for day0 "lowest temperature" markets (e.g. Paris June 1)
generated receipts with `c_fee_adjusted≈0.009` and `trade_score≈0.86` for markets
that had **already closed 25 minutes prior**.

The entry gate (`_latest_snapshot_rows_for_event_family`) filtered on
`active=1 AND closed=0` but never checked `market_end_at > decision_time`.
Polymarket marks markets `active=1, closed=0` up to the moment their on-chain
settlement resolves, which can be hours after `market_end_at`. A snapshot
captured at 12:01 UTC for a market that ended at 12:00 UTC was still selected
as a live candidate at 12:25 UTC.

**Why the cost looked like a real edge:**

Polymarket CLOB for a near-resolved NO token returns asks starting at 0.009
(the NO = ~0.3% probability complement). The `_explicit_depth_for_selected_token`
fallback used `orderbook_top_ask = 0.009` as the sole ask level.
`executable_cost("buy_no", shares=5)` walked that level → cost = 0.009 → fee-adjusted
= 0.009 × 1.1 = **0.0099**. Combined with `q_live=0.9968` this produced
`trade_score = 0.987 - 0.0099 ≈ 0.863` — an apparent 86¢ edge on a market that had
already settled.

## Live Evidence (June 1, 2026)

| Market | `market_end_at` | `decision_time` | `c_fee_adjusted` | `q_live` | `trade_score` |
|--------|-----------------|-----------------|------------------|----------|---------------|
| Paris lowest temp 14°C June 1 | 12:00 UTC | 12:25 UTC | 0.0099 | 0.9968 | 0.863 |
| Seoul 25°C June 1 | 12:00 UTC | 12:25 UTC | 0.0011 | — | — |

All receipts in the last 3h used `snapshot_id = ems2-1d2aae3582cbb3f1c17da3933d798f986d7364b8`
in `zeus_trades.db`. The world DB `edli_no_submit_receipts` accumulated phantom candidates
for every city × bin that settled that morning.

## Fix

**File:** `src/engine/event_reactor_adapter.py`  
**Function:** `_latest_snapshot_rows_for_event_family`  
**Lines:** after the `closed=0` predicate (~line 3812)

```python
if "market_end_at" in columns and fresh_at is not None:
    # Exclude markets whose settlement window has already closed (market_end_at <= decision_time).
    # NULL means no end constraint (legacy / perpetual markets) — always included.
    # This is the finalization-window gate: a market that has ended cannot be a live candidate
    # regardless of active/closed flags or price-book contents (Paris phantom June 1 root cause).
    predicates.append("(market_end_at IS NULL OR market_end_at > ?)")
    params.append(fresh_at.isoformat())
```

This gate applies at both call sites:
1. `executable_snapshot_gate_from_trade_conn` (line ~195) — uses `checked_at` as `fresh_at`
2. `build_event_bound_no_submit_receipt` (line ~507) — uses `decision_time` as `fresh_at`

Both call with `require_fresh=False`, which bypasses the 30s price-freshness window
(correct: identity gate should not decay with price age). The new gate is orthogonal
to price-freshness — it prevents markets with a closed settlement window from
becoming candidates at all.

## Invariant

**Pre-condition:** `market_end_at` is the Polymarket settlement deadline (UTC ISO string
in the DB, e.g. `"2026-06-01T12:00:00+00:00"`). It is set when the market is first
captured and does not change.

**Post-condition after fix:** A snapshot row where `market_end_at IS NOT NULL AND
market_end_at <= decision_time` will NEVER appear in `_latest_snapshot_rows_for_event_family`
regardless of `active`, `closed`, or price-book state. Only rows where
`market_end_at IS NULL OR market_end_at > decision_time` are returned.

## TDD Evidence

**Test file:** `tests/engine/test_event_reactor_no_bypass.py`

Three new tests, all added before the fix:

| Test | Before fix | After fix |
|------|-----------|-----------|
| `test_expired_market_end_at_snapshot_excluded_from_family_rows` | **RED** (returned 3 rows) | **GREEN** (0 rows) |
| `test_fresh_market_end_at_snapshot_included_in_family_rows` | GREEN | GREEN |
| `test_null_market_end_at_snapshot_included_in_family_rows` | GREEN | GREEN |

RED test assertion:
- market_end_at = `2026-06-01T12:00:00+00:00`, decision_time = `2026-06-01T12:25:00+00:00`
- Before fix: 3 rows returned (bug confirmed)
- After fix: 0 rows returned (gate enforced)

## Regression

Full test suites (`tests/engine/` + `tests/money_path/`):
- Baseline (HEAD before fix): 14 failed, 282 passed, 2 xfailed
- After fix: 14 failed, 285 passed, 2 xfailed (3 new tests added, all GREEN)
- 0 new failures attributable to this fix

(The `test_edli_online_config_defaults_inert_under_legacy_cron` discrepancy is caused
by the pre-existing `config/settings.json` change in the working tree, not by this fix.)

## Secondary Factor (not fixed here)

The `_explicit_depth_for_selected_token` fallback using `orderbook_top_ask` as a scalar
cost is a separate co-occurring issue for near-resolved markets. When `market_end_at`
gate is enforced, such snapshots are excluded before reaching the cost path — making
this a latent issue that no longer has live impact. A separate fix to the CLOB depth
parsing (`_depth_for_token_or_label` flat-format handling) would further harden the
cost path against degenerate books.
