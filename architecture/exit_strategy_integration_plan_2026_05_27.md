# Exit Strategy Integration Plan — Follow-up to Pure-Math PR

Status: ARCHIVED_REFERENCE

Created: 2026-05-27
Authority basis: Exit Strategy math review (operator, 2026-05-27)
Audit doc: `exit_strategy_audit_2026_05_27.md`

## Where this PR stops

This PR (pure-math + types) lands:

- `src/strategy/exit_observation_constraint.py` (D1)
- `src/strategy/exit_constrained_posterior.py` (D2)
- `src/strategy/exit_family_optimizer.py` (D3 pure layer)
- 78 unit + cross-module-relationship tests

It does NOT modify:

- `src/engine/cycle_runtime.py` monitor loop
- `src/state/portfolio.py::Position.evaluate_exit`
- `src/state/portfolio.py::ExitContext`
- `src/execution/exit_lifecycle.py`

These changes are deferred to a single-purpose follow-up PR per Zeus
memory rule "P0 live-money merges must be single-purpose". The math
layer landing first lets the follow-up be reviewed against locked,
typed building blocks.

## Why split

Per Fitz §1, K=5 structural decisions remain, but they split cleanly
along a SAFETY BOUNDARY:

- **Pure-math (this PR)** is reusable, total, side-effect-free, and
  cannot affect live trading on its own. Critic + tests bound it.
- **Integration (follow-up PR)** wires these objects into the live
  monitor + exit pipeline. Each wiring point is a live-capital seam
  that needs its own focused review (memory: P0 live-money merges
  must be single-purpose; first-live-order programmatic completion).

Pure-math is also independently useful for replay/backtest audits
before any live wiring goes in.

## Follow-up PR scope (D3-wiring + D4 + D5)

### Surfaces to touch

| File | Change | Risk class |
|---|---|---|
| `src/state/portfolio.py::ExitContext` | Add `settlement_progress_constraint: SettlementProgressConstraint \| None = None` field (frozen dataclass; default None preserves backward-compat) | Low |
| `src/state/portfolio.py::Position.evaluate_exit` | Insert deterministic-impossibility short-circuit (D5) AFTER the RED force-exit branch and BEFORE the missing-authority check | Live-safety |
| `src/engine/cycle_runtime.py::_build_exit_context` | Resolve constraint from the latest persisted settlement_day_observation_authority row for (city, target_date, metric); thread into ExitContext | Live-safety |
| `src/engine/cycle_runtime.py::execute_monitoring_phase` | Optional: pre-pass family grouping + ExitFamilyDecision cache so multi-bin families get joint cash-out decisions (D3 monitor wiring) | Performance (correctness via D5 short-circuit is already in place) |
| `src/state/portfolio.py::Position.evaluate_exit` | D4: replace `current_market_price` with `best_bid` in the forward_edge call site for generic edge-reversal trigger. `current_market_price` stays diagnostic on ExitContext. | Live-safety |
| `src/state/portfolio.py::ExitContext.missing_authority_fields` | Audit interaction with new constraint field (advisory mode still needs fresh_prob; deterministic mode + impossible bin can bypass fresh_prob gate per operator §5 priority order) | Live-safety |

### Required new tests (follow-up PR)

- `test_evaluate_exit_observation_impossible_short_circuit` — Position
  with `settlement_progress_constraint` flagging this bin impossible and
  `best_bid >= min_exit_bid` returns `ExitDecision(True, OBSERVATION_IMPOSSIBLE_*, immediate)` WITHOUT consecutive edge confirmation
- `test_evaluate_exit_observation_advisory_does_not_short_circuit` —
  advisory constraint NEVER yields IMPOSSIBLE reasons; existing branches drive
- `test_evaluate_exit_observation_impossible_no_bid_holds` — impossible
  + no bid returns HOLD with diagnostic reason (market closed)
- `test_evaluate_exit_forward_edge_uses_best_bid` — AST contract:
  evaluate_exit's `compute_forward_edge` call passes `NativeSidePrice(best_bid, direction)`, NOT `current_market_price`
- `test_monitor_phase_family_grouping_runs_optimizer_once_per_family`
  (when D3 monitor wiring is included)
- E2E replay: multi-bin family scenario from operator §6

### Authority resolution

`_build_exit_context` needs the latest observation row for
(city, target_date, metric). Two options:

1. **DB lookup** (preferred): `SELECT … FROM settlement_day_observation_authority WHERE city=? AND target_date=? AND temperature_metric=? ORDER BY recorded_at DESC LIMIT 1`. The row is already persisted by `_record_settlement_day_observation_authority` (cycle_runtime.py:3838).
2. **In-memory cache from current cycle**: when cycle_runtime built the row this tick, hand it through.

Option 1 is robust to cycle ordering; option 2 is cheaper. The
follow-up PR should use option 1 with optional in-memory shortcut.

### Activation flag

Per Zeus memory ("First live order: no manual completion ever" / "Live
alpha overrides legacy design loyalty"), the D5 short-circuit ships
**enabled by default** — it is a safety fix, not a strategy
optimization. Operator may flip via `ZEUS_EXIT_OBSERVATION_SHORTCIRCUIT_ENABLED=0` for emergency rollback only.

**D5 default-ON is contingent on the F-1 fix landing in this PR** (pre-
merge critic F-1, 2026-05-27 — buy_no direction-flip in the family
optimizer's hold_value computation). With F-1 resolved (commit
amended in this PR), D5 default-ON is safe because the per-leg
impossibility short-circuit it implements is buy_yes-only — the
buy_no path is decided by direction-aware `held_p = 1 - p_obs`
inside the family optimizer (D3) and the per-position
`Position.evaluate_exit` chain.

If a future revert ever re-introduces the F-1 regression, D5 must be
gated OFF on canary and the entire family-grouping path stays OFF —
the only safety improvement that can land then is the per-leg
impossibility short-circuit on buy_yes, which can be ported into
`evaluate_exit` directly without touching D2/D3.

D3 monitor wiring ships behind `ZEUS_EXIT_FAMILY_OPTIMIZER_ENABLED=0`
initially (default OFF) since it adds joint EV cash-out behaviour, and
gets promoted after shadow-mode validation. D4 likewise behind
`ZEUS_EXIT_FORWARD_EDGE_USES_BID=0` until canary-validated.

## Open questions for the follow-up

- Does the existing `Position` model carry a reference to its `Bin`? If
  not, `_build_exit_context` will need to look it up by market metadata.
- Where does the family's `p_family` vector live at monitor time? The
  refreshed posterior in `edge_ctx.p_posterior` is single-bin; the full
  family probability set may need re-derivation or storage. (May require
  a `family_posterior` cache keyed by `(market_family_id, recorded_at)`.)
- What is the canonical `min_exit_bid`? Currently `0.01` matches the
  Polymarket tick; confirm this matches operator policy.
- Does `Position.evaluate_exit`'s `RED_FORCE_EXIT` branch still take
  precedence over `OBSERVATION_IMPOSSIBLE_*`? Operator §4 priority order
  puts authorized observation #2 (after market-closed #1), suggesting
  RED #6 should still defer to OBSERVATION_IMPOSSIBLE — but RED is a
  risk-containment override. Recommend keeping RED first (current
  position) since RED is unconditional, and verifying with operator.

## Antibody discipline

Both PRs land with AST-wiring antibody tests on the seams they touch,
per PR #348's `test_pr348_unified_budget_seam_wiring.py` model. A unit
test passing in isolation does not prove the production seam is wired
— the antibody is the contract.
