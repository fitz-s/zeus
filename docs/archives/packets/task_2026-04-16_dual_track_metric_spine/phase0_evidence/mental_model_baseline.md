# Trading-Core Mental Model — Phase 1 Baseline

Established 2026-04-16 after personal reading of 10 critical files by main-thread.
This file anchors the "understanding" layer that survives context resets. Do not delete.

## DT evidence map (file : line → current state)

| DT# / SD | Anchor | Current state |
|---|---|---|
| DT#1 commit ordering | `src/engine/cycle_runner.py:302-311` | `save_portfolio()` + `save_tracker()` JSON writes fire BEFORE `store_artifact(conn, artifact)` DB commit. Classic split-state on crash. |
| DT#2 RED force-exit | write: `src/riskguard/riskguard.py:816-817` → read: `riskguard.py:1008-1030 get_force_exit_review()` → consume: `src/engine/cycle_runner.py:227-231, 265` | Flag only sets `entries_blocked_reason='force_exit_review_daily_loss_red'`. No active-position sweep pathway exists anywhere in the repo. |
| DT#3 FDR family drift | `src/strategy/selection_family.py:22-34 make_family_id()` includes `strategy_key` in parts; call sites: `src/engine/evaluator.py:398 strategy_key=""`, `:437 strategy_key=strategy_key`, `:481 strategy_key=row["strategy_key"]`, `:574 strategy_key=""` | 4 call sites in one file; 2 pass empty string, 2 pass real value. Same market can land in two different FDR families depending on path → silent discovery-budget reset. |
| DT#4 chain three-state | `src/state/chain_reconciliation.py:341-376 skip_voiding = _truly_active>0 and len(chain_positions)==0`; `_STALE_GUARD_SECONDS = 6*3600` | Binary decision + stale patch. No `CHAIN_UNKNOWN` first-class state. Staleness logic embedded inline in `reconcile()`. |
| DT#5 Kelly executable-price | signature: `src/strategy/kelly.py:24 kelly_size(p_posterior, entry_price: float, bankroll, ...)`; call sites: `src/engine/evaluator.py:179, 191 kelly_size(... entry_price=edge.entry_price ...)`; cascade-constants law INV-13: `kelly.py:74 require_provenance("kelly_mult")` (ALREADY ENFORCED — do not touch). | Bare float flows from `EdgeDecision.entry_price` straight into sizing. No distributional object exists anywhere in the codebase. |
| DT#6 graceful degrade | flag set: `src/state/portfolio.py:673 portfolio_loader_degraded: bool = False`, degraded at `:951, :995`; raise: `src/engine/cycle_runner.py:179-180 raise RuntimeError(...Failsafe subsystem shutdown)` | Whole cycle dies, monitor + exit + reconciliation all suppressed. |
| SD-2 schema | `src/state/db.py:158 settlements`, `:219 market_events`, `:253 ensemble_snapshots`, `:273 calibration_pairs`, `:313 platt_models`, `:570 observation_instants`; plus `day0_residual_fact` + `historical_forecasts` (not yet located but mentioned in blockers) | All legacy tables; none carry `temperature_metric`. `settlements` unique key is `(city, target_date)`. |
| SD-4 Day0 half-threading | `src/signal/day0_signal.py:26-244 Day0Signal`. Constructor (`:34-103`) accepts `observed_low_so_far`, `member_mins_remaining`, `temperature_metric="high"`. Body (`p_vector`, `observation_weight`, `_temporal_closure_weight`, `obs_dominates`, `forecast_context`) uses ONLY `obs_high`, `ens_remaining`, `day0_blended_highs`. | **Worst kind of half-threading**: caller with `temperature_metric="low"` silently receives high-semantics probability. Returning wrong-but-plausible numbers is worse than a visible failure. |

## Phase 1 穿线 current state (`evaluator.py`)

- `:81 temperature_metric: str = "high"` — EdgeDecision field default.
- `:269 _normalize_temperature_metric` — string normalizer already exists.
- `:650, 746, 781, 821, 1011` — temperature_metric threaded via string.
- `:794 if temperature_metric == "low" and candidate.observation.get("low_so_far") is None: reject` — consumer-ahead-of-producer (production of low_so_far is not wired).
- `:807-809` — passes `observed_low_so_far` to `Day0Signal` WHEN available, but `Day0Signal.p_vector` ignores it.

## Scaffolding that is already correct (preserve, do not rewrite)

- `src/strategy/kelly.py:74 require_provenance("kelly_mult")` → INV-13 cascade-constants already enforced in `dynamic_kelly_mult`. Do not fold INV-13 into INV-21 work.
- `src/riskguard/riskguard.py:988-997 get_current_level()` fail-closed-on-stale (>5 min → RED). Pattern to mirror for DT#4 CHAIN_UNKNOWN state machine.
- `src/state/chain_reconciliation.py:39-75 ChainPositionView` frozen dataclass per-cycle snapshot. Structure is right; only missing explicit three-valued state enum on top.
- `src/engine/cycle_runner.py:167-172` provenance-registry precheck wiring — pattern to mirror for metric identity precheck once Phase 1 lands.
- `src/state/portfolio_loader_policy.py choose_portfolio_truth_source()` → `policy.source` is already a typed authority contract; DT#6 fix extends its consumer, not its producer.

## Phase 1 relationship-test drafts (main-thread owns)

- **R1-metric-identity-type**: `MetricIdentity(temperature_metric="high").observation_field != "low_temp"`. Assigning the wrong `observation_field` to a `high` identity must be unrepresentable (frozen dataclass + Literal types + runtime assert in `__post_init__`).
- **R2-day0-low-ignores-obs-high**: `Day0LowNowcastSignal(...)` invoked without `obs_high` must produce a valid probability vector. Today's `Day0Signal(...) p_vector()` must raise or skip when constructed with `temperature_metric="low"` (Phase 6 splits; Phase 1 only asserts the type seam exists).
- **R3-fdr-family-canonical-identity**: `make_family_id(strategy_key="")` and `make_family_id(strategy_key=None)` must resolve to the same canonical family ID as the documented grammar — either always-include or always-exclude `strategy_key`. A "drift" call site that bypasses the choke-point helper must be detectable by test.
- **R4-metric-identity-cannot-be-string**: At a minimum choke-point (e.g., `Day0Signal.__init__`), passing `temperature_metric: str` instead of `MetricIdentity` must raise TypeError (runtime) and mypy-error (static).

## Phase 1 non-goals (deliberate)

- No Day0Signal class split (Phase 6 owns).
- No `kelly_size()` signature change (pre-Phase 9 owns).
- No World DB v2 tables (Phase 2 owns).
- No graceful-degradation monitor lane (Phase 6 owns).
- No RED force-exit sweep (risk phase before Phase 9 owns).
- No `low_so_far` provider work (Phase 3 owns).

## Phase 1 entry dispatches (to launch once main-thread approves)

1. `Explore` subagent — full repo inventory of `"high"`, `"low"`, `"high_temp"`, `"low_temp"` string literals, categorized into (observation-field label / config key / display / metric param / comment / test fixture).
2. `Explore` subagent — every `make_family_id(` call site with the exact `strategy_key=` argument expression, across the whole repo.
3. `architect` (opus, read-only) — trace `candidate.temperature_metric` → `evaluator` → `Day0Signal` → `p_vector`; produce the canonical relationship-invariant set to go with R1-R4 above.

Main-thread consolidates R1-R4 after those return; then `test-engineer` implements, then `executor` implements the module and threading, then `critic` adversarial review.
