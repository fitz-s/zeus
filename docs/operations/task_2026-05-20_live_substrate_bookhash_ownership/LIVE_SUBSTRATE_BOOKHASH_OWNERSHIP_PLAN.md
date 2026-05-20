# Live Substrate Book-Hash Ownership Plan

Created: 2026-05-20
Last reused or audited: 2026-05-20
Authority basis: AGENTS.md topology navigation + live runtime evidence 2026-05-20

## Objective

Fix the broken relationship between the live executable-market snapshot writer and
the `book_hash_transitions` audit table so market discovery cannot partially
refresh live trading substrate with `no such table: book_hash_transitions`.

## Facts

- `src/main.py::_market_discovery_cycle` opens `get_trade_connection(write_class="live")`
  and passes that connection to `refresh_executable_market_substrate_snapshots`.
- `capture_executable_market_snapshot` writes `executable_market_snapshots` and
  then writes `book_hash_transitions` on the same connection when the orderbook
  hash changes.
- Current production `state/zeus_trades.db` contains fresh
  `executable_market_snapshots` rows but has no `book_hash_transitions` table.
- Current production `state/zeus-world.db` contains `book_hash_transitions` but
  has zero live `executable_market_snapshots` rows.
- `architecture/db_table_ownership.yaml` declares world ownership for the active
  snapshot table and marks the trade copy as a legacy ghost, which conflicts with
  the running writer.

## Structural Decision

The active executable-market snapshot substrate is trade/execution substrate.
`book_hash_transitions` must be co-located with the active
`executable_market_snapshots` writer and reader, not opened through a second DB
inside the scanner. The fix must make writer, schema initialization, table
ownership, and tests name the same DB identity.

## Non-Goals

- Do not change pricing, Kelly, calibration, risk, venue submit semantics, or
  settlement/redeem logic in this slice.
- Do not move existing production snapshot rows from trades to world in this
  hotfix.
- Do not claim live stability from a unit test or one cycle.

## Relationship Test

Before implementation, add a test that uses the same DB identity as live market
discovery: a trade-rooted connection with executable snapshots initialized. Run
two captures for the same condition with different orderbook hashes and assert:

- the snapshot refresh summary has `failed == 0`,
- executable snapshots are present in the same DB,
- `book_hash_transitions` is present in the same DB,
- the transition row records the previous and new raw orderbook hash.

## Deployment Gate

Before touching production DB state:

- backup `state/zeus_trades.db`,
- dry-run schema creation against a temporary copy,
- verify boot schema registry accepts the table,
- deploy code on `main`,
- restart live from clean `main`,
- verify repeated market discovery runs without `book_hash_transitions` failures.

## Runtime Acceptance

Completion of this slice requires live evidence, not only tests:

- live code SHA equals `origin/main` and worktree is clean,
- `market_discovery` reports fresh snapshots with `failed == 0`,
- subsequent decision cycle reports `market_scan_authority=VERIFIED` without
  `degraded=True` from substrate write conflicts,
- evaluator either submits orders or records economically legitimate no-trade
  reasons with fresh forecast and venue data.
