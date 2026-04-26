# Phase 3 Audit Log — P3 Midstream Trust Item Verification

Created: 2026-04-26
Authority basis: phase 3 plan §1
Source HEAD at audit: `5f6e502` (post-phase-2 + 6 review-fix commits)
Audit window: 2026-04-26 within 10-min before plan write

---

## P3.1 — Day0 binary→continuous (workbook claim: needs implementation)

**Audit query**: `obs_dominates|observation_weight|day0_obs_dominates_threshold` across src/ + tests/ + scripts/

**Findings**:
- `src/signal/day0_signal.py:166` — `obs_weight = self.observation_weight()` (used)
- `src/signal/day0_signal.py:189` — passed as kwarg `observation_weight=obs_weight` (used)
- `src/signal/day0_signal.py:215-228` — `def observation_weight(self)` body, returns `day0_observation_weight(...)` (typed continuous fn)
- `src/signal/day0_signal.py:249-254` — `def obs_dominates(self) -> bool: ... # Legacy boolean interface. Prefer observation_weight() for continuous blending.`
- `src/signal/day0_signal.py:258` — return dict carries `"observation_weight": self.observation_weight()`
- `src/config.py:324-325` — `def day0_obs_dominates_threshold()` (legacy threshold reader)
- `tests/test_instrument_invariants.py:146` — `def test_day0_observation_weight_increases_monotonically` (relationship test pinning continuous weighting)

**External callers of `obs_dominates`**: ZERO outside `day0_signal.py` itself. The legacy method exists for backward compat but is DEAD code.

**External callers of `day0_obs_dominates_threshold`**: ZERO outside the legacy `obs_dominates` method that uses it.

**Status**: workbook P3.1 was already implemented before PR #19 was opened (per known_gaps.md "FIXED 2026-03-31"). Phase 3 declares P3.1 complete; optional dead-code removal deferred to a future cleanup packet.

---

## P3.2 — Entry/exit epistemic symmetry (workbook claim: needs closure)

**Audit query**: `ci_width|EdgeContext|conservative_forward_edge|forward_edge` across src/engine + src/execution + src/contracts

**Findings**:
- `src/contracts/edge_context.py:8` — `class EdgeContext` with `ci_width` property at L33
- `src/execution/exit_triggers.py:21` — `from src.execution.exit_triggers import conservative_forward_edge`
- `src/execution/exit_triggers.py:148-149` — `evidence_edge = conservative_forward_edge(forward_edge, current_edge_context.ci_width)` (CI-aware)
- `src/execution/exit_triggers.py:195-196` — twin site, same pattern
- **`src/engine/monitor_refresh.py:721-722`** — **degenerate CI initialization**:
  ```python
  ci_lower = current_forward_edge
  ci_upper = current_forward_edge
  ```
- `src/engine/monitor_refresh.py:723-735` — conditional bootstrap CI overrides degenerate fallback:
  ```python
  bootstrap_ctx = getattr(pos, "_bootstrap_context", None)
  if bootstrap_ctx is not None and len(bootstrap_ctx["bins"]) > 1:
      # ... fresh bootstrap CI ...
  ```
- `src/engine/monitor_refresh.py:759, 764, 768` — EdgeContext built using `pos.entry_ci_width` (pre-existing entry CI width is tracked on the Position)

**Bug**: when `_bootstrap_context` is absent (e.g., position re-loaded from JSON fallback after process restart, or constructed in a test fixture without the cached context), `ci_lower == ci_upper == current_forward_edge` → `ci_width = 0` → `conservative_forward_edge(forward_edge, 0)` = `forward_edge` (raw point estimate) → exit decisions revert to point logic without the safety margin entry guarantees.

**Known_gaps.md context**:
- "[FIXED] Exit uses CI-aware conservative edge instead of raw point estimate (2026-03-31)" — only fixed the exit_triggers consumer, not the upstream monitor that PRODUCES the CI it consumes.
- "[FIXED] MC count: monitor=1000, entry=5000 → both 5000 (2026-03-31)" — fixed MC count for fresh bootstrap, but not the no-bootstrap fallback path.

**Status**: real bug remains at the no-bootstrap-context fallback path. P3.2 slice fixes by hoisting `entry_ci_width` as the fallback width.

---

## P3.3 — Typed execution contracts boundary (workbook claim: needs extension)

**Audit query**: `tick_size|slippage|RealizedFill|max_slippage` across src/execution + src/contracts

**Findings**:
- `src/contracts/realized_fill.py:6-15` — module docstring describes typed RealizedFill with components.
- `src/contracts/realized_fill.py:21` — `from src.contracts.slippage_bps import SlippageBps`
- `src/contracts/realized_fill.py:34-47` — RealizedFill dataclass has `slippage: SlippageBps` field.
- `src/contracts/realized_fill.py:86-101` — `RealizedFill.from_prices` constructor.
- `src/contracts/slippage_bps.py:5-13` — module docstring on typed SlippageBps.
- `src/execution/executor.py:128` — `max_slippage=0.02` (raw float, untyped boundary value).
- `src/execution/executor.py:236-237` — uses TickSize.for_market correctly (already typed).
- `src/execution/executor.py:242-243` — `slippage = current_price - best_bid; if current_price > 0 and slippage / current_price <= 0.03:` (raw arithmetic on raw floats).

**Gap**: typed atoms exist and TickSize is used; SlippageBps is NOT used at the executor send/fill boundary. RealizedFill is NOT constructed at fill receipt.

**Status**: real boundary-typing gap. P3.3 slice replaces the raw float and threads RealizedFill at receipt.

---

## P3.4 — v2→legacy fallback alerting (workbook claim: needs implementation)

**Audit query**: `legacy fallback|load_platt_model\b|legacy.*platt` in src/calibration/manager.py

**Findings**:
- `src/calibration/manager.py:21` — `from src.calibration.store import (..., load_platt_model, ...)` (legacy reader).
- `src/calibration/manager.py:134-138` — docstring records the v2-then-legacy fallback contract:
  ```
  1. Try platt_models_v2 filtered by (temperature_metric, cluster, season)
  2. If v2 miss, fall back to legacy platt_models (HIGH historical continuity)
  ```
- `src/calibration/manager.py:172` — `model_data = load_platt_model(conn, bk)` — primary-bucket fallback site, executes silently when v2 returns None.
- `src/calibration/manager.py:232` — `model_data = load_platt_model(conn, bk_fb)` — season-only fallback site, twin pattern.
- `src/calibration/manager.py:182, 235, 283, 303, 309, 314, 334` — existing logger.warning + logger.debug usage in the file (so logger is wired and idiomatic).

**Gap**: both fallback sites execute without logging. Operators watching calibration health can't tell when v2 coverage is incomplete.

**Status**: real observability gap. P3.4 slice adds WARNING log at each fallback site with cluster/season/metric context.

---

## Summary

| P3 item | Audit verdict | Phase 3 disposition |
|---|---|---|
| P3.1 Day0 weighting | Already implemented; legacy interface dead | Declare complete; defer cleanup |
| P3.2 Entry/exit CI symmetry | Real bug at no-bootstrap fallback path | Active slice |
| P3.3 Execution typed boundary | Typed atoms exist but executor.py:128/242 untyped | Active slice |
| P3.4 v2→legacy alerting | No log at fallback sites | Active slice |

3 active slices + 1 declared-complete item.

End of phase 3 audit log.
