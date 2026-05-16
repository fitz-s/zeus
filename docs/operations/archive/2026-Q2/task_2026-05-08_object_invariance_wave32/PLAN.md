# Object Invariance Wave 32 - Venue Fill Cost Basis Continuity

Status: PLANNING-LOCK EVIDENCE FOR LOCAL SOURCE/TEST SLICE, NOT LIVE UNLOCK, NOT VENUE OR DB MUTATION AUTHORITY

Created: 2026-05-08
Last reused or audited: 2026-05-08
Authority basis: root AGENTS.md object-meaning invariance goal; docs/to-do-list/known_gaps.md D3; src/state/AGENTS.md; src/execution/AGENTS.md; Polymarket fee and order lifecycle docs checked 2026-05-08

## Scope

Repair one bounded D3 boundary class:

`venue-confirmed entry fill economics -> canonical portfolio loader -> Position effective open exposure -> monitor/risk/report read models`

This wave does not mutate live/canonical databases, run migrations, backfill or
relabel rows, submit/cancel/redeem venue orders, publish reports, or authorize
live unlock. It is source/test enforcement only.

## Phase 0 - Repo-Reconstructed Map

Money-path slice for this wave:

`execution_fact(fill_price, shares, filled_at, terminal_exec_status='filled') -> _query_entry_execution_fill_hints() -> _position_current_effective_entry_economics() -> query_portfolio_loader_view()/query_position_current_status_view() -> Position.effective_cost_basis_usd -> risk/monitor/status/strategy_health`

Authority surfaces:

- Venue fill evidence authority: `execution_fact` rows with entry role,
  terminal filled status, `filled_at`, positive `fill_price`, and positive
  `shares`.
- Current open slice authority: `position_current.shares` /
  `position_current.cost_basis_usd`, especially after partial exits.
- Runtime position economics authority: `src/state/portfolio.py::Position`.
- Read-model authority: `src/state/db.py::query_portfolio_loader_view` and
  `query_position_current_status_view`.
- External venue reality: Polymarket orders are limit orders; matches transfer
  tokens and pUSD atomically at settlement. Taker fees are market-specific,
  match-time, and a function of traded shares, price, and fee rate.

External references checked:

- `https://docs.polymarket.com/concepts/order-lifecycle` - matched orders
  transfer tokens from seller to buyer and pUSD from buyer to seller; price
  improvement means the actual match price can differ from submitted limit.
- `https://docs.polymarket.com/trading/fees` - fees are determined per market
  at match time; makers are not charged, takers pay `C * feeRate * p * (1-p)`.

## Phase 1 - Boundary Selection

| Candidate | Live-money relevance | Material values | Bypass/legacy risk | Patch safety |
| --- | --- | --- | --- | --- |
| Venue fill cost -> portfolio loader | Direct monitor/risk/report exposure and PnL basis | `fill_price`, `shares`, `filled_cost_basis_usd`, `position_current.cost_basis_usd`, `effective_cost_basis_usd` | Current `min(projection_cost, filled_cost)` can cap real fill cost | Safe if no DB writes and partial-exit semantics remain tested |
| Evaluator cost object -> Kelly | Direct sizing | `ExecutionPrice`, fee rate, p_market, ask/depth | Broad strategy/evaluator surface | Already partially repaired; larger follow-up |
| Fee role/maker-taker persistence | Direct true realized cost | liquidity role, fee rate, fee amount | Requires schema/backfill/live venue facts | OPERATOR_DECISION_REQUIRED for durable schema |

Selected: venue fill cost -> portfolio loader. Repo code already has a
venue-confirmed fill filter, but then reinterprets the fill cost as capped by
the current/projection cost basis. That is correct for a reduced open slice
after partial exit, but wrong for a still-full open fill whose realized cost
exceeds target/projection due executable price, price improvement/adverse fill,
share rounding, or fee semantics.

## Phase 2 - Material Value Lineage

| Value | Real object denoted | Origin | Source authority | Evidence class | Unit/side/time | Transform | Persistence | Downstream consumers | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `execution_fact.fill_price` | venue observed average entry fill price | `log_execution_fact` / fill trackers | venue/command evidence | fill finality | probability-dollar/share, match/fill time | positive finite guard | `execution_fact` | loader, risk, status | Preserved |
| `execution_fact.shares` | venue observed filled CTF shares | same | venue/command evidence | fill finality | shares, match/fill time | positive finite guard | `execution_fact` | loader, risk, status | Preserved |
| `filled_cost_basis_usd` | actual entry fill notional before any explicit fee extension | `_query_entry_execution_fill_hints` | derived from venue fill fields | fill finality | USDC, fill time | `fill_price * shares` | transient loader dict | loader/Position | Broken when capped by projection for full-open positions |
| `position_current.cost_basis_usd` | current open-slice cost projection | canonical projection | lifecycle/current state | open exposure | USDC, latest position state | projection from entry/exit lifecycle | `position_current` | loader/Position | Preserved for partial exit |
| `effective_cost_basis_usd` | cost basis of currently open shares | loader / `Position` property | should combine fill authority with current open slice | live monitor/risk read model | USDC, monitor/read time | currently `min(current_cost, fill_cost)` | read model / runtime object | risk, monitor, status, strategy health | Ambiguous/broken |

## Phase 3 - Failure Classification

### W32-F1 - Venue-confirmed fill cost can be capped by target/projection cost

Severity: S1. It can corrupt live monitor/risk exposure, unrealized PnL,
strategy health, report/replay attribution, and learning slices. It is S0 only
if a downstream rule directly sizes exits/new risk from the understated number.

Object meaning that changes:

The real filled entry cost (`fill_price * shares`) becomes a planned/current
projection cap (`position_current.cost_basis_usd`) even when the open share
count still denotes the full venue-confirmed filled slice.

Boundary:

`execution_fact` fill evidence -> canonical portfolio loader / `Position`
runtime economics.

Code path:

- `src/state/db.py::_query_entry_execution_fill_hints()` derives
  `filled_cost_basis_usd = fill_price * shares`.
- `src/state/db.py::_position_current_effective_entry_economics()` then
  replaces it with `min(projection_cost_basis_usd, filled_cost_basis_usd)`.
- `src/state/portfolio.py::Position.effective_cost_basis_usd` repeats the same
  cap for runtime objects.

Economic impact:

If the fill is still fully open and real fill cost exceeds target/projection,
Zeus understates exposure and overstates PnL. The stale object can flow into
status, strategy health, risk, monitor decisions, reports, and replay.

Reachability:

Active read path for DB-backed portfolio loading and status/strategy health.

## Phase 4 - Repair Design

Invariant restored:

Venue-confirmed fill cost is the cost basis for the open fill slice. A lower
current/projection cost can reduce it only when the current open shares prove
that the position has been reduced after entry. In that case the reduced open
cost is explicitly the proportional cost of remaining filled shares, not a
silent cap.

Durable mechanism:

- Add one source-level helper in `src/state/portfolio.py` for fill-authority
  open-slice cost derivation.
- Reuse the helper in `Position.effective_cost_basis_usd` and
  `src/state/db.py::_position_current_effective_entry_economics`.
- Preserve `effective_shares = min(current_open_shares, entry_fill_shares)`.
- Relationship tests cover:
  - full-open fill cost exceeding projection survives into loader/status/strategy
    health;
  - partial-exit reduced open slice still uses the remaining share ratio.

Deferred operator decision:

Persisting explicit fee amount, liquidity role, fee rate, and fee authority on
fill rows likely requires schema/data migration and venue-fact capture approval.
This wave does not infer or backfill those fields.

## Phase 5 - Verification Plan

- Relationship/unit tests:
  - `tests/test_db.py` for `execution_fact -> loader/status/strategy_health`;
  - `tests/test_live_safety_invariants.py` for `Position.effective_cost_basis_usd`.
- Focused existing DB/risk/status tests for fill-authority read models.
- Compile touched source/tests.
- Planning-lock and map-maintenance closeout.

## Implemented Repair

- `src/state/portfolio.py`
  - Added `fill_authority_effective_open_cost_basis()`, a shared derivation for
    fill-grade current open cost basis.
  - `Position.effective_cost_basis_usd` now uses venue-confirmed fill cost when
    current open shares still represent the full fill, and reduces the cost only
    when current open shares are lower than entry filled shares.
- `src/state/db.py`
  - `_position_current_effective_entry_economics()` now uses the same helper
    instead of `min(projection_cost_basis_usd, filled_cost_basis_usd)`.
  - `projection_cost_basis_usd` remains visible as projection context; it no
    longer silently caps a full-open venue-confirmed fill cost.
- Tests
  - Added a `Position` relationship test proving full-open venue fill cost can
    exceed projection without cap.
  - Added a DB read-model relationship test proving the same value survives
    through status view, portfolio loader, and strategy health.
  - Added a conservative fallback test proving missing current open shares do
    not reduce venue-confirmed fill cost.
  - Existing partial-exit tests remain intact and prove reduced open slices
    still reduce exposure.

## Verification Results

- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_live_safety_invariants.py::test_full_open_fill_authority_cost_basis_can_exceed_projection_without_cap tests/test_live_safety_invariants.py::test_partial_exit_fill_reduces_effective_open_fill_authority_exposure tests/test_db.py::test_position_current_views_use_fill_authority_current_open_economics tests/test_db.py::test_position_current_views_do_not_cap_full_open_fill_cost_to_projection tests/test_db.py::test_position_current_views_preserve_current_open_reduction_after_partial_exit -q --tb=short` -> `5 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m py_compile src/state/portfolio.py src/state/db.py tests/test_db.py tests/test_live_safety_invariants.py` -> pass.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_riskguard.py::TestRiskGuardSettlementSource::test_portfolio_loader_fill_authority_requires_source_time_provenance -q --tb=short` -> `1 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_db.py::test_position_current_views_missing_open_shares_do_not_reduce_fill_cost tests/test_db.py::test_position_current_views_do_not_cap_full_open_fill_cost_to_projection tests/test_db.py::test_position_current_views_preserve_current_open_reduction_after_partial_exit -q --tb=short` -> `3 passed`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_db.py -q --tb=short` -> `53 passed, 17 skipped`.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/.venv/bin/python -m pytest tests/test_live_safety_invariants.py::test_exit_micro_position_hold_uses_fill_authority_cost_basis tests/test_live_safety_invariants.py::test_full_open_fill_authority_cost_basis_can_exceed_projection_without_cap tests/test_live_safety_invariants.py::test_partial_exit_fill_reduces_effective_open_fill_authority_exposure tests/test_live_safety_invariants.py::test_duplicate_fill_aggregation_updates_fill_authority_open_exposure tests/test_live_safety_invariants.py::test_mixed_authority_duplicate_keeps_fill_slice_separate tests/test_live_safety_invariants.py::test_same_order_update_cannot_regress_fill_authority_to_legacy tests/test_live_safety_invariants.py::test_same_order_update_cannot_regress_full_fill_to_partial_fill -q --tb=short` -> `7 passed`.

## Critic Loop

- Wave32 critic verdict: APPROVE.
  - Confirmed fill authority is gated by `execution_fact` entry role, terminal
    filled status, `filled_at`, positive `fill_price`, and positive `shares`.
  - Confirmed full-open venue fill cost is no longer capped by projection cost.
  - Confirmed partial-exit, cancelled remainder, duplicate fills, RiskGuard
    loader rows, status, and strategy health showed no semantic regression.
  - Non-blocking suggestion: pin zero/missing `position_current.shares`
    conservative fallback. Added test after critic approval.

## Downstream Sweep

- Monitor/risk paths: `Position.effective_cost_basis_usd` and loader rows now
  agree on the same fill-authority cost derivation.
- Status/strategy-health paths: `query_position_current_status_view()` and
  `refresh_strategy_health()` now preserve full-open venue fill cost above
  projection; tested.
- Partial-exit paths: reduced open shares still reduce cost exposure; tested
  both at `Position` and DB read-model boundaries.
- Replay/report/learning paths: no legacy/corrected row rewrite was performed.
  Remaining D3 work is explicit typed execution-cost lineage and persisted
  fee/liquidity-role authority, not this read-model cap.

## Topology Notes

- The semantic phrase `pricing semantics authority cutover` admitted the
  source/test slice, while packet docs and known-gaps updates remained
  out-of-scope under navigation even though local packet rules require them.
- Planning-lock accepted the full changed-file set with this plan evidence.
  No schema/live-data command was run.

## Stop Conditions

Stop and request operator decision if repair requires:

- live/prod DB mutation, backfill, relabel, migration, or settlement harvest;
- changing venue submission, fee capture schema, or Polymarket account actions;
- changing partial-exit lifecycle semantics outside current open-slice cost
  derivation;
- claiming D3 fully closed beyond this bounded venue-fill read-model repair.
