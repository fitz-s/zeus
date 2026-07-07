# Zeus Kelly Asymmetric Loss Hand-off

Created: 2026-05-03
Last reused/audited: 2026-05-03
Authority basis: Operator ruling 2026-05-03 (Ruling A) — DDD v2 redesign decision
  that asymmetric loss preferences must NOT be encoded in the DDD floor.

## Decision (Ruling A)

The v2 DDD redesign (2026-05-03) removed city-specific asymmetric loss
overrides from the DDD floor. This was wrong in v1 because a floor override
lies about the physical station baseline, causing the algorithm to apply
discounts on days that are actually normal for that station.

The correct mechanism: per-city Kelly multipliers that scale final trade
size based on operator risk preferences, applied AFTER DDD discount.

## Affected Cities

| City | v1 Floor Override | Reason | Recommended Kelly Multiplier |
|------|------------------|--------|------------------------------|
| Denver | 0.85 (removed) | Conservative sizing on convective LOW | 0.7 (operator to finalize) |
| Paris | 0.85 (removed) | Conservative sizing (station drift risk) | 0.7 (operator to finalize) |

Note: Paris floor is currently EXCLUDED pending workstream A DB resync.
Once Paris workstream A completes, its floor will be set from empirical p05.
The Kelly multiplier should be configured at that time.

## Recommended Interface

```python
# In src/strategy/kelly.py (or config layer)
PER_CITY_KELLY_MULTIPLIER: dict[str, float] = {
    "Denver": 0.70,  # operator to finalize
    "Paris": 0.70,   # operator to finalize; activate after workstream A
}
```

## Composition Order

```python
# Final Kelly fraction (conceptual — wire per actual Kelly implementation)
final_kelly = base_kelly * per_city_kelly_multiplier.get(city, 1.0) * (1.0 - ddd_result.discount)
```

Where:
- `base_kelly`: Kelly fraction from calibration/signal layer
- `per_city_kelly_multiplier`: asymmetric loss adjustment per city (this doc)
- `ddd_result.discount`: DDD v2 output from `src/oracle/data_density_discount.py`

## Where to Wire

Target file: `src/strategy/kelly.py`

The multiplier should be applied as a final scaling step, after all other
Kelly adjustments (risk limits, position caps, oracle penalty). This keeps
the asymmetric loss preference as a single, clearly labelled coefficient
rather than embedded in multiple upstream signals.

## What NOT to Do

- Do NOT encode asymmetric loss preferences as DDD floor overrides.
- Do NOT hardcode city multipliers inline in the Kelly formula.
- Do NOT apply this multiplier in the DDD module itself.

## Implementation Status

**LANDED 2026-05-03** in `src/strategy/kelly.py`:

- `DEFAULT_CITY_KELLY_MULTIPLIERS = {"Denver": 0.7, "Paris": 0.7}` — table.
- `city_kelly_multiplier(city: str | None) -> float` — fail-OPEN to 1.0× for
  unknown city (correct semantics: most cities have no asymmetric override).
- `dynamic_kelly_mult(..., city: str | None = None)` — extended to take
  `city`; applies the multiplier as the final factor, after `strategy_key`.
  Default `city=None` preserves legacy behavior for unwired callers.
- Operator can override defaults via
  `config/settings.json::sizing::city_kelly_multipliers`. Sanity range
  enforced [0.0, 2.0]; malformed values fall back to defaults.
- Tests: `tests/test_city_kelly_multiplier.py` (14 cases, 14 passing).

**Wiring point still open** (operator-owned):
- `src/engine/evaluator.py:2755` (and `src/engine/replay.py:1599`) call
  `dynamic_kelly_mult(...)` without a `city` argument. Operator needs to
  thread `city=edge.city` (or whichever attribute carries the city in the
  decision context) at those call sites for the multiplier to take effect
  in production.
- Until wiring lands, the multiplier is a no-op for live serving — every
  call to `dynamic_kelly_mult` sees `city=None` and produces the same
  result as before.

This is a deliberate two-stage rollout: (1) module change is mechanical and
landed without coordination, (2) live wiring is operator-owned per the
project rule that touches to `src/engine/evaluator.py` happen on operator
schedule.
