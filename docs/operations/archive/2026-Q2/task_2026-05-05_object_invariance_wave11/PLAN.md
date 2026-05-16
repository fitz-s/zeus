# Wave11 Object Meaning Invariance Plan

## Boundary

Scope: canonical DB current-position read models to RiskGuard, strategy health,
operator status, and report-facing portfolio exposure.

Selected boundary:
`position_current / execution_fact -> portfolio loader/status views -> RiskGuard and operator status exposure`.

Invariant restored by this wave:
Submitted target notional, current open cost basis, filled cost basis, fill
price, and fill evidence are separate economic objects. Once a row has
confirmed fill economics in canonical execution facts, downstream current-open
exposure, unrealized PnL, RiskGuard portfolio reconstruction, strategy health,
and operator status must consume fill-authority current-open economics or
explicitly stay legacy/unknown. They must not silently re-open submitted
`size_usd` as current exposure.

Non-goals:
- No production DB mutation, migration apply, backfill, relabeling, or legacy
  data rewrite.
- No venue/account mutation, live lock lifting, or live side effects.
- No schema migration in this wave unless a later operator decision explicitly
  authorizes it.
- No promotion of legacy rows without fill evidence into fill-authority truth.

## Route Evidence

Admitted route:
`pricing semantics authority cutover` admits `src/state/db.py`,
`tests/test_db.py`, and `tests/test_runtime_guards.py` for the DB read-model
repair.

Route conflict:
The same semantic boundary also reaches `src/riskguard/riskguard.py`,
`src/observability/status_summary.py`, and `tests/test_riskguard.py`, but the
pricing profile excludes them. The generic `modify risk or strategy` profile
matches RiskGuard files but does not admit `src/riskguard/riskguard.py`, only
selected risk policy files and tests. This is real topology incompatibility for
object-invariance waves: producer and consumer files for one economic object
are split across profiles that cannot jointly admit the boundary.

Planned handling:
Repair the admitted DB views first so downstream status/strategy-health
consumers receive explicit effective economics. Do not directly edit blocked
RiskGuard/status files unless a later admitted route or operator decision
widens the scope. Record any remaining consumer bypass as
OPERATOR_DECISION_REQUIRED instead of guessing.

## Findings

W11-F1: `query_position_current_status_view()` reports per-position
`size_usd` and totals `total_exposure_usd` from `position_current.size_usd`.
For filled positions where `size_usd` still denotes submitted target notional
and `cost_basis_usd` denotes current filled cost, operator-facing portfolio
exposure can materialize the submitted object as live exposure.

W11-F2: `refresh_strategy_health()` computes `open_exposure_usd` from
`SUM(size_usd)` and unrealized PnL from `shares * mark - cost_basis_usd`.
That can mix submitted notional for exposure with fill/current-open cost for
PnL in the same strategy-health row.

W11-F3: `query_portfolio_loader_view()` emits only projection fields plus a
single `entry_fill_verified` hint from `position_events.payload_json`. It does
not carry fill authority, fill price, filled shares, or filled cost basis from
canonical `execution_fact`, so loader consumers cannot distinguish legacy
numeric compatibility from confirmed fill economics.

W11-F4: `position_current` schema and `CANONICAL_POSITION_CURRENT_COLUMNS` do
not currently contain durable fill-authority columns. `lifecycle_events.py`
explicitly documents that such fields were previously dropped by
`upsert_position_current` and require a separate schema migration. This wave
therefore cannot claim a complete schema-level cutover without operator
approval for migration/backfill.

W11-F5: RiskGuard's `_portfolio_position_from_loader_row()` is downstream of
the loader and currently reconstructs `Position` from `size_usd`,
`shares`, `cost_basis_usd`, `entry_price`, and `entry_fill_verified` only.
Direct RiskGuard repair is route-blocked in this wave; DB view enrichment can
reduce contamination, but RiskGuard source changes need a separate admitted
route.

## Repair Plan

1. Add DB read helpers in `src/state/db.py` that derive entry-fill evidence from
   `execution_fact` only when `order_role='entry'`, terminal status is `filled`,
   `filled_at` exists, and both `fill_price` and `shares` are positive.
2. Use those helpers in `query_position_current_status_view()`,
   `query_portfolio_loader_view()`, and `refresh_strategy_health()` to expose:
   current-open exposure, effective shares, effective cost basis,
   average fill price, fill authority, economics authority, and an explicit
   evidence source.
3. Keep legacy rows explicit: if no qualifying execution fact exists, preserve
   projection values and mark fill/economics authority as legacy/none.
4. Add relationship tests proving a filled entry with submitted target
   different from confirmed fill cost reports current-open exposure from the
   fill-authority cost basis, not submitted `size_usd`.
5. Run planning-lock with this file as evidence, focused DB/RiskGuard/status
   tests that are admitted, and critic review before moving to another wave.

## Verification Plan

Required:
- `python3 -m py_compile src/state/db.py tests/test_db.py tests/test_runtime_guards.py`
- Focused `tests/test_db.py` coverage for status view, portfolio loader view,
  and strategy health preserving fill-authority current-open economics.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave11/PLAN.md`
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory`
- Critic review with explicit cross-module prompts for DB views, RiskGuard,
  observability status, report/export, and legacy fallbacks.

Blocked verification:
Full RiskGuard source repair and direct status-summary source repair are
blocked by current topology admission. If DB-view repair leaves a source-level
consumer bypass in those files, close Wave11 as OPERATOR_DECISION_REQUIRED for
the remaining consumer slice rather than silently widening scope.

Completed checks after DB-view repair:
- `python3 -m py_compile src/state/db.py tests/test_db.py`
- `pytest -q -p no:cacheprovider tests/test_db.py`: 41 passed, 19 skipped.
- `pytest -q -p no:cacheprovider tests/test_riskguard.py`: 48 passed.
- Focused `tests/test_db.py` fill-authority view relationship tests: 3 passed.
- Focused `tests/test_phase5a_truth_authority.py -k query_portfolio_loader_view`: 2 passed, 18 deselected.
- Focused `tests/test_runtime_guards.py` loader/exposure checks with process-local `sklearn` stub: 2 passed.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files src/state/db.py tests/test_db.py docs/operations/task_2026-05-05_object_invariance_wave11/PLAN.md --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave11/PLAN.md`: ok.
- `python3 scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory`: ok.
- `python3 scripts/topology_doctor.py --schema`: ok.
- `git diff --check`: ok.
- Static contamination sweep: `rg -n "FROM position_current|JOIN position_current|position_current.*size_usd|size_usd.*position_current|total_exposure_usd|open_exposure_usd" src scripts tests -g"*.py"` found active source consumers routed through `query_position_current_status_view()`, `query_portfolio_loader_view()`, and `refresh_strategy_health()` for the repaired economic exposure surface. Other direct `position_current` reads in source are phase/identity/latency checks (`chain_reconciliation`, `ws_poll_reaction`, harvester phase lookups, replay subject identity), not current-open exposure arithmetic.

Blocked local check:
- `tests/test_pnl_flow_and_audit.py -k 'query_position_current_status_view or query_portfolio_loader_view'` could not collect in this local environment because `apscheduler` is not installed. Earlier runtime checks also required the known local `sklearn` stub.

Critic verdict:
- Wave11 critic verdict: APPROVE. No S0/S1 findings for DB-view repair.
- Critic residual S2 provenance note: RiskGuard is numerically compatible but
  drops fill-authority provenance fields from the loader row. This does not
  block Wave11 because direct RiskGuard source repair is route-blocked here,
  but it remains a later topology/operator decision.
- Critic residual S2 test gap closed in this wave: added
  `test_position_current_views_preserve_current_open_reduction_after_partial_exit`,
  proving an original fill of 25/50 does not reopen exposure after the current
  projection has reduced to 10/20. Status, loader, and strategy health all
  report 10/20 current-open economics.
- Critic residual S3 status-summary note: strategy-health snapshots can be
  stale beside fresh portfolio status; route-blocked status-summary source
  repair remains deferred.

## Topology Notes

This wave exposes a compatibility problem rather than a single bad route:
object-meaning invariance boundaries are cross-profile by design. The route
system admits producers (`src/state/db.py`) under pricing semantics, but excludes
the protective/reporting consumers (`src/riskguard/riskguard.py`,
`src/observability/status_summary.py`) that make the same economic object safe
or unsafe. A future topology delta should add an object-invariance profile or
allow declared producer-consumer boundary slices to admit both sides with
explicit no-live-mutation constraints.
