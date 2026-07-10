# runtime_open_exposure_snapshot -- Plan

Date: 2026-07-10
Branch: `agent/runtime-throughput-first-principles`
Status: implementation complete; independent review clear

## Background

The live reactor loads and hydrates all 958 historical portfolio rows each tick
to compute exposure from five runtime-open positions. The hot path must scale
with current exposure, not history.

## Scope

_See sibling scope.yaml for machine-readable scope._

## Deliverables
- Filter the canonical loader SQL to runtime-open phases before hydration.
- Build one cycle-scoped `PortfolioState` from the reactor's existing trade connection.
- Preserve poison-row containment and fail-closed real-submit behavior.
- Leave recovery and non-reactor full-portfolio consumers unchanged.

## Verification
- Exposure and correlation parity across open, pending, terminal, and partial-fill fixtures.
- Reactor relationship test: one bounded snapshot, no `load_portfolio()` call.
- Live-DB read-only benchmark and query-plan evidence.
- Independent code review of the four-file runtime slice.

## Current Evidence

Captured 2026-07-10 on the canonical trade DB through read-only connections:

- Full projection: 958 rows; typed runtime-open set: 5 rows.
- Specialized snapshot: the same 5 IDs and `total_exposure_usd=40.2239`.
- Same warm read-only connection after final reviewer fixes: full rebuild median
  160.8 ms over 7 runs; specialized median 0.438 ms and p95 0.508 ms over
  200 runs.
- Full rebuild issued 3,716 read statements; specialized snapshot issued 6.
- The loader owns a short `BEGIN`/`ROLLBACK`; `total_changes=0` and the
  connection is transaction-free on return.
- Terminal history is excluded before fill hydration. Trade schema bootstrap
  adds `idx_execution_fact_position_role_effective_fill_time`; its EXPLAIN antibody uses
  `init_schema_trade_only`, the same path called by main and deploy startup.
- Tests: 29 focused runtime/poison/authority/index/gate checks, 15 DB-loader
  checks, 44 portfolio/EDLI relationship checks, and 42 source/bridge checks
  passed. `py_compile` and `git diff --check` passed.
- The full `tests/test_runtime_guards.py` file remains non-clean at 263 passed,
  2 skipped, and 29 failures. Those failures are outside this four-file slice's
  new call paths (evaluator/forecast fixtures, order-cleanup schema, monitoring
  counters, and other pre-existing PR surfaces); this packet does not claim a
  repository-wide clean pass.
- The full registered money-path file remains non-clean at 23 passed and 22
  failures because its pre-existing `FakeScheduler` lacks the current
  `add_listener` method. The changed gate antibody itself passes. The broader
  DB/state/portfolio set was 115 passed and 20 unrelated failures from stale
  event/schema APIs; focused loader tests remain green.

A window-ranking attempt reduced SQL count but regressed both full and hot
latency; it was removed. The first independent architecture review found that
compatibility coercion could normalize malformed/missing exposure authority to
zero. The specialized path now validates required columns, identity values,
raw local/chain economics, cross-field chain/fill authority, current-money-risk
chain completeness, and filled execution facts strictly before constructing a
canonical runtime-exposure snapshot. It also preserves pending partial-fill
economics while keeping unfilled pending entries at zero. The pending-fill
projection is explicitly runtime-only, so full loader/recovery semantics stay
unchanged; both persisted cost fields must equal `shares * entry_price`, and
full authority in a pending phase and settled authority in any runtime-open
phase fail closed. A second-connection test proves the projection and fill
reads share one SQLite snapshot, and the index test proves existing-schema
bootstrap recreates a missing index. Staged-only code review found no runtime
issues after remediation; independent architecture review is `CLEAR`.
