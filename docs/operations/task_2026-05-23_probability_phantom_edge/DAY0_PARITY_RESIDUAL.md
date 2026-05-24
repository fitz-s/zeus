# DAY0_PARITY_RESIDUAL.md
# Day 0 Forecast Authority Parity — Residual Scope After DAY0-P1

**Created:** 2026-05-24
**Authority:** evaluator.py:3289-3292; DAY0-P1 PR #322
**Status:** DEFERRED (day0 stays shadow; residual is not urgent)

---

## What DAY0-P1 Fixed (included in PR #322)

DAY0-P1 fixed the **run-SELECTION** bug inside the day0 legacy path:
- Before: `_query_snapshot_for_day0()` returned any matching row, not necessarily the
  freshest from a FULL_CONTRIBUTOR source. Stale or partial-coverage runs could
  contaminate the day0 signal.
- After: selection now enforces `source_type = 'FULL_CONTRIBUTOR'` and orders by
  `issue_time DESC LIMIT 1`, ensuring the freshest complete run is used.

This was a query-layer fix **within** the legacy `fetch_ensemble` + `period_extrema` path.
It did NOT migrate day0 onto `read_executable_forecast()` bundle authority.

---

## What Full Parity Would Require

The non-day0 cutover gate at `evaluator.py:3289-3292`:

```python
use_executable_forecast_cutover = (
    entry_forecast_cfg is not None
    and not is_day0_mode          # ← day0 explicitly excluded
)
```

For day0 to consume `read_executable_forecast()` (bundle authority), two conditions
must both be satisfied:

### Condition A: `members_hourly` format compatibility

`read_executable_forecast()` returns an `ExecutableForecast` object whose member array
is structured for the full-horizon ensemble pipeline (shape `[hours, members]`).

The day0 signal pipeline (`day0_high_signal.py`, `day0_high_nowcast_signal.py`) consumes
`members_hourly` from the legacy fetch but then applies `period_extrema` — it expects
the raw per-hour-per-member matrix to extract intraday max/min over the settlement window.

The executable forecast reader does not currently expose `members_hourly` in the format
the day0 pipeline expects. Bridging this requires either:
- Option A: Extend `ExecutableForecast` to carry `members_hourly` alongside `p_raw`/`p_cal`.
- Option B: Adapt the day0 signal pipeline to work directly from `p_raw`/`bins` (already
  calibrated) rather than re-deriving from raw members.

Option B changes day0 signal semantics (no longer recomputes extrema from raw members;
accepts pre-calibrated probabilities). This is a non-trivial design decision requiring
operator sign-off.

### Condition B: Readiness-state check for day0 bundle

The bundle reader checks `readiness_state = 'READY'` on the producer. Day0 trades on a
shorter horizon and may need a different readiness signal (e.g. nowcast-ready, not
full-horizon-ready). This requires a new `source_run_coverage` annotation or a
separate readiness row for the day0 window.

---

## Why This Is Deferred

- Day0 is currently in **shadow mode** (no live capital at risk from day0 decisions).
- The DAY0-P1 fix ensures the day0 legacy path uses the freshest complete run, which
  is the dominant data-quality risk.
- Full bundle-authority parity would require resolving the format mismatch (Condition A)
  and a new readiness signal (Condition B) — both operator-design decisions.
- No operational urgency while day0 stays shadow.

## Residual Work Item (when day0 promotion is considered)

1. Decide Option A vs Option B for members_hourly format.
2. Define day0 readiness signal (new `source_run_coverage` tag or separate producer).
3. Remove `and not is_day0_mode` from `use_executable_forecast_cutover` at `evaluator.py:3291`.
4. Write integration test: day0 candidate reads from bundle reader, not `fetch_ensemble`.
5. Operator sign-off before day0 comes out of shadow.

---
*Authority: evaluator.py:3289-3292; PR #322 (feat(prob) merge 2026-05-24)*
