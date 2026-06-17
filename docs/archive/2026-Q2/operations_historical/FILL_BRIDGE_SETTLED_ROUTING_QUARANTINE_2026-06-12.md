# Fill-Bridge Settled-Market Routing and Quarantine Plan
# Created: 2026-06-12
# Authority basis: fill-bridge retry-spiral incident 2026-06-12

## Problem

The EDLI durable fill-bridge scan (`_edli_durable_fill_bridge_scan`, src/main.py ~7760-7864)
retries every failing aggregate every ~60s indefinitely. Two ancient aggregates from 2026-06-06/07
(markets long settled) were re-discovered by `append_rest_filled_orphan_trade_facts_to_edli`
and now raise `EDLI_BRIDGE_STRATEGY_MISSING` 1278+ times. Worse: failed aggregates consume
the scan's `orphaned_seen >= limit` budget, starving new real fills.

## Root Cause

`_resolve_strategy_key_from_pre_submit` correctly refuses to guess strategy identity for
pre-era payloads lacking `strategy_key`/`event_type`. The scan has no settled-market routing
and no per-aggregate failure tracking — every failure retries forever.

## Structural Fix (two category-killers)

### 1. Settled-market terminal routing
A confirmed fill whose market is already settled must NEVER be materialized into
position_current. Route to `SETTLED_MARKET_FILL_BOOKED` disposition, persisted in
`edli_fill_bridge_dispositions` table (zeus-world.db via init_schema). Excluded from future
candidate scans (NOT IN probe). One WARNING log; no exception raised; no position_current row.

Settlement truth in priority order:
- `settlements` table with `authority='VERIFIED'` and matching `city`/`target_date`/`temperature_metric`
- Conservative fallback: `target_date` strictly older than `today - 1` (UTC) for daily weather markets

### 2. Bounded-retry quarantine
Track consecutive failures per aggregate (in same disposition table, disposition=
`QUARANTINED_BRIDGE_FAILURE` after N=10 attempts). Failed-but-below-threshold: continue
retrying (transient faults heal). At quarantine: one ERROR log, excluded from future scans.
Failed aggregates do NOT consume the scan's new-fill budget (count only successful bridges
toward limit, or check limit only after skip/quarantine/settle routing is applied).

## Files Changed

- `src/state/schema/edli_fill_bridge_dispositions_schema.py` (NEW): schema owner
- `src/state/db.py`: register new table in init_schema
- `src/events/edli_position_bridge.py`: add settled-market check + disposition write/read helpers
- `src/main.py`: update `_edli_durable_fill_bridge_scan` — quarantine tracking, limit-fix, disposition probe

## Tests

New file: `tests/events/test_fill_bridge_settled_routing_quarantine.py`
- Settled-market fill → SETTLED_MARKET_FILL_BOOKED disposition, no position_current, never re-selected
- Pre-era payload on non-settled market → retries N times then QUARANTINED_BRIDGE_FAILURE, excluded
- Fresh valid fill on live market → bridges to position_current as before (regression pin)
- Failed aggregate does not starve a later valid aggregate in same scan

## Schema

Table `edli_fill_bridge_dispositions` in zeus-world.db (world_class, added to init_schema):
- aggregate_id TEXT PRIMARY KEY
- disposition TEXT CHECK (IN ('SETTLED_MARKET_FILL_BOOKED', 'QUARANTINED_BRIDGE_FAILURE'))
- reason TEXT NOT NULL
- attempt_count INTEGER NOT NULL DEFAULT 0
- last_error TEXT
- created_at TEXT NOT NULL
- updated_at TEXT NOT NULL

No existing tables modified. Schema fingerprint must be re-pinned after init_schema change.
