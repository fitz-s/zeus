# critic-beth — Phase 5C Wide Review (final, post-xfail)

**Date**: 2026-04-17
**Subject**: Phase 5C replay MetricIdentity (R-AV..R-AY) + Gate D low-purity (R-AZ)
**Commit base**: `3f42842` (fix-pack); 5C impl on disk in `src/engine/replay.py` (+32/-24)
**Pytest (5C files)**: 10 passed + 2 xfailed (R-AZ-1/2 deferred Phase 7) + R-AZ-3 GREEN
**Full-suite regression**: 138 failed / 1780 passed / 99 skipped / 2 xfailed — **flat vs post-fix-pack baseline**
**Posture**: L0.0 peer-not-suspect, fresh bash greps on every cited claim
**Supersedes**: `critic_beth_phase5c_wide_review.md` (pre-xfail ITERATE draft)

## VERDICT: **ITERATE** — 1 CRITICAL runtime-latent + 1 MAJOR test hygiene

5C typed-fields and caller-chain threading are structurally sound within scope. But one CRITICAL production-runtime hazard: the new `AND temperature_metric = ?` SQL filter at `replay.py:258` will fail silently against the production `forecasts` table (no such column), swallowed by the outer `except Exception: return []`. Tests pass because fixtures build a parallel schema WITH the column. MAJOR: 2 fix-pack test regressions still unresolved on disk (my prior-review carry-over).

---

## L0 — Fresh disk-verify evidence

```
$ git diff --stat HEAD -- src/engine/replay.py
 src/engine/replay.py | 56 +++++++++++++++++---- (+32/-24)

$ pytest tests/test_phase5c_replay_metric_identity.py tests/test_phase5_gate_d_low_purity.py -v
10 passed + 2 xfailed + 1 passed = 13 items in 1.46s
 R-AZ-1 / R-AZ-2: XFAIL (Phase 7 deferral per team-lead ruling)
 R-AZ-3: PASS (Platt model_key metric-scoped — Gate D core antibody)

$ grep -n "DIAGNOSTIC_REPLAY|forecasts_table" src/engine/replay.py
41: DIAGNOSTIC_REPLAY_REFERENCE_SOURCES = frozenset({
44:   "forecasts_table_synthetic",   ← updated, old "forecasts_table" removed
289: "source": "forecasts_table_synthetic",
290: "decision_reference_source": "forecasts_table_synthetic",
175: if outcome.decision_reference_source in DIAGNOSTIC_REPLAY_REFERENCE_SOURCES
1285: if decision_reference_source in DIAGNOSTIC_REPLAY_REFERENCE_SOURCES
 ← emitter, consumer, frozenset all agree — no dead diagnostic skip

$ sqlite3 state/zeus-world.db ".schema forecasts"
CREATE TABLE forecasts (city, target_date, source, forecast_basis_date,
  forecast_issue_time, lead_days, lead_time_hours, forecast_high,
  forecast_low, temp_unit, retrieved_at, imported_at, UNIQUE(...));
 ← NO temperature_metric column

$ grep -n "def run_replay|temperature_metric" src/engine/replay.py | tail -5
1113:   temperature_metric: str = "high",    ← _replay_one_settlement (scope)
1135: decision_ref = ctx.get_decision_reference_for(..., temperature_metric=temperature_metric)
1933: def run_replay(start_date, end_date, mode, overrides, allow_snapshot_only_reference)
       ← NO temperature_metric param (Phase 7/8 deferred per team-lead ruling)
```

## L1 — INV / FM
Typed status fields lock: `decision_reference_source ∈ {"historical_decision","forecasts_table_synthetic"}`, `decision_time_status ∈ {"OK","SYNTHETIC_MIDDAY","UNAVAILABLE"}`, `agreement ∈ {"AGREE","DISAGREE","UNKNOWN"}`. Synthetic path at L286-297 correctly emits `agreement="UNKNOWN"`, `decision_time=None` (no fabricated midday). Historical-decision path at L363-364 emits `decision_reference_source="historical_decision"`, `decision_time_status="OK"`. Contract matches handoff §"Phase 5C scope" item 1. PASS.

## L2 — Forbidden Moves
- Paper-mode resurrection: zero new refs.
- `decision_time` fabrication: removed at L286. R-AW-2 verifies.
- `setdefault` on authority: not introduced.
- JSON-before-commit (DT#1): out of scope.

## L3 — Silent fallbacks
`temperature_metric: str = "high"` default on 4 methods (L242/267/302/331) is a trust-boundary weakener analogous to 5B MINOR-NEW-1. Acceptable under dispatch-economy as forward-log item, but noted: required-keyword would force every caller to choose. Team-lead deferred to follow-up.

## L4 — Source authority at seams: **CRITICAL runtime-latent FOUND**

### CRITICAL-1: Production DB schema incompatibility

**Evidence**:
```
src/engine/replay.py:258:                  AND temperature_metric = ?
src/state/db.py:651: CREATE TABLE forecasts (... NO temperature_metric column)
state/zeus-world.db: no such column: temperature_metric  (runtime query fails)
```

At runtime against production `forecasts`, the SQL query at L248-261 raises `sqlite3.OperationalError: no such column: temperature_metric`. The outer `except Exception: return []` at L263-264 swallows it silently. `_forecast_reference_for` returns None → `get_decision_reference_for` fallback lane returns None → `_replay_one_settlement` skips the settlement → `run_replay.n_replayed=0`.

**Impact**: **HIGH-track replay regression in production runtime**. Pre-5C, `run_replay()` HIGH path worked. Post-5C, the HIGH path (which `run_replay` hardcodes via `temperature_metric="high"` default in `_replay_one_settlement:1113`) silently skips all settlements because the SQL now references a nonexistent column.

**Why tests don't catch this**: `tests/test_phase5c_replay_metric_identity.py:47` builds a FIXTURE schema that explicitly includes `temperature_metric TEXT DEFAULT 'high'`. Tests pass. The comment at L256 of that file even acknowledges: "forecasts table has separate forecast_high + forecast_low columns (no temperature_metric..." — testeng knew, but the fixture doesn't match production. Fitz Constraint #4 exactly: code-fixture-agreement, code-production-disagreement.

**Severity: CRITICAL in production, invisible in test matrix**. This is a latent runtime bomb the moment someone invokes `run_replay()` on the live DB.

**Fix shape (TWO options)**:
- **Option A (remove SQL filter, Phase 7 defers)**: per team-lead's earlier scope message ("forecasts table lacks temperature_metric column; NO SQL WHERE filter"), remove `AND temperature_metric = ?` from L258 and revert `_forecast_rows_for` to `(city_name, target_date)` signature. The metric-conditional column read at L281 (`forecast_col = "forecast_low" if ... else "forecast_high"`) already handles metric dispatch correctly WITHOUT the SQL filter. The `_forecast_reference_for` metric param survives for downstream cache-key and column-selection purposes. This matches team-lead's stated scope ruling and defers SQL-filter to Phase 7 where `historical_forecasts_v2` has the column.
- **Option B (add column to production)**: ALTER TABLE `forecasts` ADD COLUMN `temperature_metric TEXT DEFAULT 'high'` + backfill. Bigger scope; touches schema migrations; probably overreach for 5C.

**Recommended: Option A**. Per team-lead's scope message and dispatch, exec-juan appears to have kept the SQL filter by oversight. Surgical revert; ~5 LOC + test R-AX updates to match column-read-only semantics. The R-AX class is ALREADY named `TestForecastRowsMetricConditionalRead` (not "SqlMetricFilter"), suggesting testeng already anticipated this shape.

### run_replay threading (scope-ruled, NOT a finding)
`run_replay` at L1933 has no `temperature_metric` param; `_replay_one_settlement(..., settlement)` at L2002 omits metric kwarg, uses default `"high"`. Per team-lead's scope ruling (Phase 7/8 deferral for LOW activation), this is **intentional**. LOW track is structurally unreachable via `run_replay` today — by design. Not a finding; documented expected state.

## L5 — Phase boundary: **MAJOR carry-overs from fix-pack review**

### MAJOR-1: Two fix-pack test regressions STILL present

My prior fix-pack PASS verdict missed these. Still on disk today:

**1. `test_phase5a_truth_authority.py::test_read_mode_truth_json_none_mode_does_not_raise`** — test asserts pre-fix-pack behavior (`mode=None` does NOT raise); R-AQ in fix-pack inverted the contract. Fails post-fix-pack. Test is obsolete; R-AQ-1 in `test_phase5_fixpack.py` replaces it.

Fix: delete the obsolete test method. ~5 LOC.

**2. 4 tests in `test_run_replay_cli.py`, `test_runtime_guards.py`, `test_backtest_outcome_comparison.py`** — fixture seeds forecasts rows without `temperature_metric`; SQL filter rejects → `n_replayed=0` vs expected `1`. If Option A (remove SQL filter) lands, these PASS automatically — no fixture updates needed.

**Note**: `test_run_replay_cli.py::test_replay_without_market_price_linkage_cannot_generate_pnl` has a SEPARATE pre-existing failure unrelated to 5C (missing `replay_results` table in fixture). Out of scope.

### WIDE — off-checklist findings

- **R-AZ-3 GREEN antibody** — save_platt_model_v2 correctly generates metric-scoped `model_key`. HIGH + LOW with same cluster/season produce distinct keys. This is Gate D's core structural invariant and it passes. ✓
- **xfail markers on R-AZ-1/2** — Phase 7 deferral per team-lead. Strict xfail so post-Phase-7 re-run flips to XPASS cleanly. Clean deferral.
- **Test file `_make_replay_db` fixture at L47** creates parallel schema with `temperature_metric` — this is the Fitz Constraint #4 hazard surfaced. Not a fix for 5C itself; filing as forward-log for Phase 7 fixture-schema-unification.

## Legacy-audit verdicts

- `src/engine/replay.py` (+32/-24): **CURRENT_REUSABLE pending CRITICAL-1 resolution**. Typed-fields work is clean. SQL filter is the hazard.
- `tests/test_phase5c_replay_metric_identity.py` (360 LOC, NEW): **CURRENT_REUSABLE**. Lifecycle header present; no fixture bypass; cache-key collision test sharp. Note: fixture schema differs from production (Constraint #4 hazard but intentional isolation).
- `tests/test_phase5_gate_d_low_purity.py` (271 LOC, NEW): **CURRENT_REUSABLE**. xfail markers on R-AZ-1/2 are clean deferral; R-AZ-3 exercises the Platt metric-scope invariant.

## Recommendation

**ITERATE — 1 surgical round**:

**To exec-juan** (CRITICAL-1):
- Option A: remove `AND temperature_metric = ?` from `_forecast_rows_for` SQL (L258). Revert signature to `(city_name, target_date)` — but KEEP the metric param on `_forecast_reference_for`, `get_decision_reference_for`, etc. (for cache-key + column selection). The metric flows through the Python layer but doesn't pollute the SQL layer. Net ~5 LOC delta + ~10 LOC test adjustment on R-AX-1/2.

**To exec-ida** (MAJOR-1 carry-over):
- Delete `test_phase5a_truth_authority.py::test_read_mode_truth_json_none_mode_does_not_raise` (obsolete per fix-pack R-AQ-1).

**No scope escalation needed**. Team-lead's Phase 7/8 deferral ruling for `run_replay` stands.

Post-ITERATE expectation:
- 13 passed + 2 xfailed on 5C test files (R-AZ-3 GREEN stays; R-AZ-1/2 xfailed stays).
- 4 currently-failing pre-existing tests (`test_run_replay_cli`, `test_runtime_guards`, `test_backtest_outcome_comparison`) flip GREEN automatically.
- `test_phase5a_truth_authority` obsolete test removed.
- Full-suite regression count drops from 138 → ~134 or better.

## Commit scope (if ITERATE lands clean)

~4 files:
- `src/engine/replay.py` (remove SQL filter, keep all other 5C work)
- `tests/test_phase5c_replay_metric_identity.py` (R-AX tests may need minor adjustment for column-read-only semantics)
- `tests/test_phase5_gate_d_low_purity.py` (NEW, R-AZ with xfail markers)
- `tests/test_phase5a_truth_authority.py` (delete obsolete R-AQ-predecessor test)

Suggested commit header: `feat(phase5C): replay MetricIdentity typed-fields + cache key + Gate D R-AZ-3; B093 half-1`.

## 5C forward-log (unchanged)

1. **Phase 7**: migrate `_forecast_rows_for` from `forecasts` to `historical_forecasts_v2`. When that table lands with `temperature_metric` column, re-add the SQL filter here.
2. **Phase 7**: `rebuild_v2` spec kwarg + METRIC_SPECS iteration → unblocks R-AZ-1/2 xfail flip to GREEN.
3. **Phase 7/8**: `run_replay` temperature_metric threading → enables LOW replay in shadow mode.
4. **Structural hardening (deferred)**: remove `temperature_metric: str = "high"` default; require explicit kwarg on all 4 methods.
5. **Post-commit triage**: remaining pre-existing failures (test_topology_doctor, test_truth_surface_health ghost positions, etc.) unrelated to 5C.

---

*Authored*: critic-beth (opus, persistent)
*Disk-verified*: 2026-04-17 19:42 CST, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`, fresh pytest + Grep + sqlite3 schema inspection. One mid-review concurrent-write artifact (L40-50 sed briefly showed divergent state) reconciled via authoritative Grep re-read at close of session.
