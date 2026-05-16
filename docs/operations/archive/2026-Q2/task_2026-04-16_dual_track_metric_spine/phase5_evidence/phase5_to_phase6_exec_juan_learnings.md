# Phase 5C → Phase 6: exec-juan learnings

**Author**: exec-juan (sonnet executor)
**Written**: 2026-04-17, post Phase 5C commit `59e271c`
**Scope**: replay MetricIdentity half-1 (B093 half-1), Gate D test, fixture regression fixes

---

## 1. Schema validation discipline — the `forecasts.temperature_metric` catch

The 5C scope dispatch said "add `AND temperature_metric = ?` filter to `_forecast_rows_for`". I added it. The 5C test fixture explicitly creates a custom in-memory `forecasts` table with `temperature_metric TEXT DEFAULT 'high'` — so the tests passed green. The production `forecasts` table (created by `init_schema`, `src/state/db.py:651`) has no such column. The `except Exception: return []` guard in `_forecast_rows_for` silently swallowed the `OperationalError`, causing `n_replayed=0` in the forecast fallback path — a runtime-latent production regression invisible in all 5C tests.

**What exposed it**: critic-beth's wide review cross-referenced the SQL query against `src/state/db.py` DDL directly — a schema-first audit rather than a test-first audit. The test suite proved the custom fixture path; it did not prove the production path.

**Durable protocol for Phase 6**: before adding any SQL column reference to a query against a legacy table (anything under `init_schema` in `db.py`), do a two-step pre-flight:
1. `grep -n "CREATE TABLE.*<table_name>" src/state/db.py` — read the actual DDL.
2. Confirm the column exists in production schema before writing the WHERE or SELECT clause.

For Phase 6, the relevant table hazard is `settlements` — it has no `temperature_metric` column. `_replay_one_settlement` defaults to `temperature_metric="high"` for exactly this reason (legacy settlements rows carry no metric tag). Any Phase 7 work that adds metric awareness to settlement replay must add the column to the DDL and migration first, then add the SQL filter.

---

## 2. replay.py 5C patterns — what landed and what Phase 6 inherits

**Typed status fields**: `_forecast_reference_for` now returns a dict with Literal-typed fields (`decision_reference_source`, `decision_time_status`, `agreement`) instead of sentinel strings. The pattern: define the full Literal set at the top of the method, emit the same keys on every return path. Phase 6 Day0 split will add new `decision_reference_source` values; extend the Literal set, don't invent new dict shapes.

**3-tuple cache key**: `_decision_ref_cache` is keyed `(city_name, target_date, temperature_metric)`. The original 2-tuple would have caused cross-metric cache collisions once LOW replay is active. Phase 6 work that adds new cache structures must use the full `(city, date, metric)` key from day one — retrofitting is risky.

**Caller-chain threading pattern**: metric flows as `str = "high"` (not `MetricIdentity`) from `_replay_one_settlement` → `get_decision_reference_for` → `_forecast_reference_for`. The `str` default preserves backward compat with all callers that don't pass the param. Phase 6's Day0 caller chain should follow the same pattern: thread metric as `str = "high"` with a default, rather than requiring all callers to be updated simultaneously. The `run_replay` → `_replay_one_settlement` top-level threading is deliberately deferred to Phase 7/8 (team-lead MAJOR-1 ruling) because `settlements` table has no metric tag — that boundary needs a schema change, not just a Python param.

**metric-conditional column read vs SQL filter**: metric awareness in `_forecast_rows_for` is implemented as a Python-layer column read (`forecast_col = "forecast_low" if temperature_metric == "low" else "forecast_high"`), not a SQL WHERE filter. The SQL filter belongs in Phase 7 when we migrate to `historical_forecasts_v2` (which has `temperature_metric` natively). This is the general principle: **SQL filters against columns that don't exist in the production schema silently degrade through broad `except Exception` guards** — the failure mode is invisible.

---

## 3. Scope-creep recovery — conflicting authority sources

During 5C, testeng-hank sent an A2A message asserting R-AZ-1/2 needed `rebuild_v2` spec param as a 5C deliverable. I implemented it (3 hunks, tests went XPASS). Team-lead then ruled via testeng-hank that R-AZ-1/2 are deferred to Phase 7 — revert required. This created a wasted implementation cycle.

**What went wrong**: I acted on a teammate A2A before verifying it against the team-lead dispatch. The original 5C scope from team-lead did not list `rebuild_v2`. The A2A came from testeng (a peer), not team-lead (the authority).

**Durable escalation path when authority sources conflict**:
1. Stop. Do not implement.
2. Quote both conflicting signals to team-lead in a single message — one sentence each.
3. Wait for explicit ruling before touching the code.
4. If team-lead is unreachable and the conflict is blocking, implement behind an xfail marker (reversible) rather than landing real code.

The correct action when testeng-hank's A2A arrived was: "testeng-hank says R-AZ-1/2 need rebuild_v2 spec param in 5C. Team-lead 5C scope dispatch did not include this. Requesting ruling before proceeding." One message, no code.

---

## 4. Phase 6 structural hazards — day0_signal.py and evaluator.py:825

**`Day0Signal.__init__` LOW guard** (`src/signal/day0_signal.py:85-92`):

```python
if temperature_metric.is_low():
    raise NotImplementedError(
        "Day0Signal does not yet implement the low-temperature nowcast path. ..."
    )
```

This guard is the sentinel that makes LOW track structurally unreachable through the Day0 path today. Phase 6 splits this into `Day0HighSignal` / `Day0LowNowcastSignal`. The guard must be removed from `Day0Signal` only in the same commit that introduces the real low-track class. Do not remove the guard as a standalone change — that would allow `Day0Signal` to silently produce high-semantics output for low queries.

**`evaluator.py:825` MAX→MIN silent-corruption seam**:

```python
# L820-825:
# Phase 1: Day0Signal.__init__ raises NotImplementedError on low metrics,
# so member_mins_remaining is dead code today. When Phase 6 lands the
# Day0LowNowcastSignal split, the extrema producer must be re-split to
# emit separate max/min arrays — passing the max array here as mins is
# a Phase-6 TODO marker, not a valid low-track implementation.
member_mins_remaining=remaining_member_extrema,  # L825 — THIS IS THE MAX ARRAY
```

`remaining_member_extrema` is computed from `member_maxes` (the MAX array). Line 825 passes it as `member_mins_remaining`. This is silently wrong for LOW track — dead code only because the `Day0Signal.__init__` guard fires first. When Phase 6 removes the guard, this line will produce corrupted LOW probabilities with zero test failure (the array shape is valid, only the values are wrong).

**Co-landing imperative**: the fix to L825 (`member_mins_remaining=remaining_member_extrema_low` from a separately computed min array) MUST land in the same commit that removes the `Day0Signal.__init__` guard. These two changes are bound. Decoupling them creates a window where LOW track is enabled but silently receiving MAX values as its nowcast input.

---

## 5. Fresh-exec inheritance for Phase 6 replay/runtime work

**Must-reads before touching `src/engine/` in Phase 6**:
- `src/engine/replay.py` — read the 5C diff (`git show 59e271c -- src/engine/replay.py`) to understand what typed fields are now present and what `_forecast_rows_for` looks like.
- `src/signal/day0_signal.py:27-98` — full `__init__` including LOW guard at L85-92.
- `src/engine/evaluator.py:800-834` — the Day0 call site and L825 co-landing hazard.
- `docs/authority/zeus_dual_track_architecture.md §6` — DT#6 graceful-degradation law (LOW track must degrade gracefully, not raise, when nowcast data is unavailable).

**Do not touch in Phase 6**:
- `run_replay` signature — MAJOR-1 deferral ruling in effect. LOW track unreachable through `run_replay` is acceptable until Phase 7/8.
- `_decision_ref_cache` — already 3-tuple; do not shrink.
- `DIAGNOSTIC_REPLAY_REFERENCE_SOURCES` frozenset — contains `"forecasts_table_synthetic"` (correct post-5C). Do not revert to `"forecasts_table"`.

**Key invariant for Phase 6 caller-chain work**: when you add `temperature_metric` to any new function signature, check whether that function is called from a legacy path that doesn't have metric context. If yes, default to `"high"` and add a Phase 7/8 TODO. Do not propagate `None` or raise — that turns a scope deferral into a production error.
