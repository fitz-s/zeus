# Zeus Data Completeness Audit Report

## Summary

| Metric | Value |
|---|---|
| Total Exits | 5 |
| Tick Covered | 4 |
| High-Confidence Replayable | 0 |
| Fully Skipped | 5 |

## Missing-Field Ranking

| Field | Missing Count | Stage |
|---|---|---|
| `trade_id` | 4 | entry |
| `market_id` | 4 | entry |
| `entry_price` | 4 | entry |
| `size_usd` | 4 | entry |
| `entry_method` | 4 | entry |
| `entry_ci_width` | 4 | entry |
| `decision_snapshot_id` | 4 | entry |
| `entered_at` | 4 | entry |
| `p_posterior` | 4 | entry |

## Stage-of-Loss Ranking

| Stage | Total Missing Fields |
|---|---|
| entry | 36 |

## Root Cause Analysis

> **Primary bottleneck: `_track_exit()` in `portfolio.py`**
> 
> 9 entry-stage fields are missing because `_track_exit()` was not
> persisting them into `recent_exits`. This has been fixed in this commit.
> All 5 existing skipped exits are **objectively unrecoverable**
> because no secondary data source (`trade_decisions`: 0 rows,
> `decision_log`: 0 trade fills) contains the missing fields.

## Per-Exit Detail

- ❌ Exit 0 (NYC): unrecoverable — missing: ['trade_id', 'market_id', 'entry_price', 'size_usd', 'entry_method', 'entry_ci_width', 'decision_snapshot_id', 'entered_at', 'p_posterior']
- ❌ Exit 1 (Atlanta): unrecoverable — missing: ['trade_id', 'market_id', 'entry_price', 'size_usd', 'entry_method', 'entry_ci_width', 'decision_snapshot_id', 'entered_at', 'p_posterior']
- ❌ Exit 2 (Austin): no_ticks
- ❌ Exit 3 (San Francisco): unrecoverable — missing: ['trade_id', 'market_id', 'entry_price', 'size_usd', 'entry_method', 'entry_ci_width', 'decision_snapshot_id', 'entered_at', 'p_posterior']
- ❌ Exit 4 (Dallas): unrecoverable — missing: ['trade_id', 'market_id', 'entry_price', 'size_usd', 'entry_method', 'entry_ci_width', 'decision_snapshot_id', 'entered_at', 'p_posterior']
