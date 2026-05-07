# critic-beth — Phase 5C Wide Review

**Date**: 2026-04-17
**Subject**: Phase 5C — replay MetricIdentity (R-AV..R-AY) + Gate D low-purity (R-AZ)
**Commit base**: `3f42842` (fix-pack); 5C impl + tests unstaged
**Pytest (5C files)**: 10 passed, 2 failed RED (R-AZ-1/2), 0 skipped
**Full-suite regression**: 142 failed / 1779 passed / 99 skipped → 4 delta vs post-fix-pack 138
**Posture**: L0.0 peer-not-suspect, fresh bash greps on every cited claim

## VERDICT: **ITERATE** — 1 MAJOR + 2 CRITICAL regressions + Gate D scope question

5C replay-side core is structurally sound (typed fields, metric SQL filter, cache key). But the caller-chain threading stops 2 layers short of `run_replay`, and two pre-existing tests regressed on fix-pack + 5C changes. Gate D RED items point at a missing `rebuild_v2` spec kwarg that's in scope for 5C per handoff. Narrow surgical fixes; no re-architecture.

---

## Fresh disk-verify evidence

```
$ python -m pytest tests/test_phase5c_replay_metric_identity.py tests/test_phase5_gate_d_low_purity.py -v
12 collected → 10 passed, 2 failed (R-AZ-1, R-AZ-2)

$ grep -n "_replay_one_settlement|temperature_metric|def run_replay" src/engine/replay.py
242:  def _forecast_rows_for(..., temperature_metric: str = "high")
267:  def _forecast_reference_for(..., temperature_metric: str = "high")
302:  def _forecast_snapshot_for(..., temperature_metric: str = "high")
331:  def get_decision_reference_for(..., temperature_metric: str = "high")
1108: def _replay_one_settlement(ctx, city, target_date, settlement,
1113:   temperature_metric: str = "high",
1135:   decision_ref = ctx.get_decision_reference_for(..., temperature_metric=temperature_metric)
1933: def run_replay(start_date, end_date, mode, overrides, allow_snapshot_only_reference)
      ^^ NO temperature_metric param
2002:   outcome = _replay_one_settlement(ctx, city, target_date, settlement)
      ^^ metric NOT threaded — default "high" used silently
```

## L0-L5

### L0 / L0.0
Authority chain re-loaded post-subagent-start. Peer-not-suspect throughout. Zero discipline findings filed.

### L1 — INV / FM
`_forecast_rows_for` SQL at L250-259 now filters `AND temperature_metric = ?` (explicit). Return shape at L283-297 includes `decision_reference_source: Literal["forecasts_table_synthetic"]`, `decision_time_status: Literal["SYNTHETIC_MIDDAY"]`, `agreement: Literal["UNKNOWN"]` on synthetic path. Historical-decision path at L363-364 emits `decision_reference_source: "historical_decision"`, `decision_time_status: "OK"`. Typed-fields contract matches team-lead ruling.

### L2 — Forbidden Moves
- **Obsolete non-live runtime resurrection**: zero new refs.
- **setdefault on authority**: not introduced.
- **`decision_time` fabrication in synthetic path (B093 half-1)**: L286 now emits `"decision_time": None` — fabricated midday sentinel removed. R-AW-2 verifies. ✓

### L3 — Silent fallbacks
- `_forecast_rows_for` default `temperature_metric="high"` — risk that unaware callers get HIGH silently when they needed LOW. Mitigated by `_replay_one_settlement` explicitly threading, but the default is a trust-boundary weakener. Flag as MINOR.
- `forecast_col = "forecast_low" if temperature_metric == "low" else "forecast_high"` at L281, L306 — correct metric-conditional read.

### L4 — Source authority at seams: **MAJOR-1 FOUND**

**`run_replay` (L1933) → `_replay_one_settlement` (L2002): metric not threaded.**

Evidence:
- `run_replay(start_date, end_date, mode, overrides, allow_snapshot_only_reference)` has no `temperature_metric` parameter.
- L1968-1974 SELECTs from legacy `settlements` table (metric-agnostic). No way to determine per-row metric at this layer.
- L2002 calls `_replay_one_settlement(ctx, city, target_date, settlement)` — default `"high"` silently used.

**Impact**: The 5C caller-chain refactor is complete from `_replay_one_settlement` downward, but `run_replay` — the PUBLIC replay entry — never invokes with anything but default `"high"`. LOW track replay is structurally unreachable via the production path. Same shape as 5B's "contract-gate-unwired" antibody-not-defense concern (team-lead's L4 watch).

**Scope question for team-lead**: handoff §"Phase 5C scope" lists 5 items:
1. `_forecast_reference_for` typed status fields ✓
2. `_forecast_rows_for` SQL metric filter ✓
3. `_forecast_reference_for` metric-conditional branching ✓
4. `_decision_ref_cache` key with metric ✓
5. Gate D test

**`run_replay` threading is NOT explicitly listed**. Either (a) it's Phase 7/8 scope (LOW shadow mode activation, runtime flip), (b) it's an implicit-scope miss in this 5C. I'm flagging as MAJOR because the `_replay_one_settlement` metric param is net-zero defense without a top-level caller. Team-lead's ruling needed.

### L5 — Phase boundary: **2 CRITICAL regressions on pre-existing tests**

#### CRITICAL-1: `test_phase5a_truth_authority.py::test_read_mode_truth_json_none_mode_does_not_raise`

```
src/state/truth_files.py:155: in read_mode_truth_json
    raise ModeMismatchError(
E   ModeMismatchError: mode=None is not allowed — pass an explicit mode string...
```

Root cause: **Fix-pack commit `3f42842` regression**, NOT 5C. R-AQ hardened `read_mode_truth_json` to reject `mode=None` — but the 5A test was asserting the PRE-fix-pack behavior (`mode=None` does not raise). My PASS verdict on fix-pack missed this. Test name literally says "does_not_raise"; fix-pack inverted the semantics. **This is a finding I missed in the prior review** — L0.0 self-correction, no teammate discipline issue.

Fix shape: either (a) delete the 5A test (obsolete; the R-AQ test in fix-pack replaces its contract) or (b) invert it to `test_read_mode_truth_json_none_mode_raises`. Recommend (a) — R-AQ-1 in fix-pack is the authoritative post-antibody.

#### CRITICAL-2: `test_run_replay_cli.py` (2 failures) + `test_runtime_guards.py` (1 failure) + `test_backtest_outcome_comparison.py` (1 failure)

```
tests/test_run_replay_cli.py:107: assert relaxed.n_replayed == 1
E   AssertionError: assert 0 == 1
```

Root cause: **5C SQL metric filter regression**. `_forecast_rows_for` at L258 now adds `AND temperature_metric = ?`. Test fixtures that seed `forecasts` rows without explicitly setting `temperature_metric` → SQL filter rejects → `_forecast_reference_for` returns None → `n_replayed=0`. The `forecasts` table schema has `temperature_metric TEXT` but pre-5C fixtures probably didn't populate it (default NULL or absent column in older fixtures).

Fix shape: update the CLI test fixtures to seed `temperature_metric='high'` explicitly on forecast rows. Mechanical fixture update, ~1 line each.

**Affected tests** (from full-suite run):
- `test_run_replay_cli.py::test_run_replay_snapshot_only_can_fallback_to_forecast_rows`
- `test_run_replay_cli.py::test_replay_without_market_price_linkage_cannot_generate_pnl`
- `test_runtime_guards.py::test_trade_and_no_trade_artifacts_carry_replay_reference_fields`
- `test_backtest_outcome_comparison.py::test_new_lanes_write_to_zeus_backtest_not_replay_results` (possibly same class)

### Gate D RED items (R-AZ-1, R-AZ-2): missing `rebuild_v2` spec kwarg

Both tests fail at `inspect.signature(rebuild_v2)` → no `spec` param:

```
rebuild_v2 has no 'spec' parameter. Cannot run HIGH-spec rebuild in isolation —
cross-metric leakage is structurally unguarded.
Fix: add spec: CalibrationMetricSpec param to rebuild_v2 and propagate to _process_snapshot_v2.
```

Disk-verified: `rebuild_v2` at L314 takes `conn, *, dry_run, force, city_filter, n_mc, rng` — no `spec`. `_process_snapshot_v2` at L206 DOES take `spec` (fix-pack landing), but the public entry point doesn't propagate it. Per dispatch-economy, this is scope question (5C scope item #5 is the Gate D TEST; whether adding `spec` to `rebuild_v2` is "5C" or "Phase 7 metric-aware rebuild" is a scope call).

**My read**: Gate D TEST cannot pass without the rebuild_v2 spec kwarg. If the test is in-scope for 5C per handoff, the minimal impl enabling the test (rebuild_v2 accepts spec, passes through to _process_snapshot_v2) is also in-scope. ~10-15 LOC.

### WIDE — off-checklist

- **MINOR**: `temperature_metric: str = "high"` default across `_forecast_rows_for`, `_forecast_reference_for`, `_forecast_snapshot_for`, `get_decision_reference_for`. Silent-HIGH default is a 5B-style setdefault-analog. If team-lead wants hard safety, `temperature_metric: str` (no default, required) forces every caller to choose. Testeng's R-AX-1 `inspect.signature` test would catch future param removal but not default restoration. Flag as MINOR forward-hardening.
- **R-AY-1 test self-consistency**: L332-337 has a `pytest.fail(...)` at the end of the `if "temperature_metric" not in sig.parameters:` branch. Post-fix (metric IS in sig), the branch is skipped entirely and the test passes without asserting anything — that's fine, but the test body after L322 is unreachable post-fix and becomes dead code documenting a pre-fix state. Clean test file, nit only.

## Legacy-audit verdicts

- `src/engine/replay.py` diff (~50 LOC) — **CURRENT_REUSABLE**. Five method signatures extended; dual-callsite `forecast_col` metric-branching correct. No stale helper left behind.
- `tests/test_phase5c_replay_metric_identity.py` (360 LOC, NEW) — **CURRENT_REUSABLE**. Lifecycle header present; real-entry-point calls; no fixture bypass on R-AV/R-AW (DB-seeded bins drive `_forecast_reference_for` end-to-end).
- `tests/test_phase5_gate_d_low_purity.py` (271 LOC, NEW) — **CURRENT_REUSABLE pending rebuild_v2 spec**. Structural assertion via `inspect.signature`; RED signals genuine gap.

## Recommendation

**ITERATE — single compound round**. Dispatching a2a direct per dispatch-economy:

**To exec-juan** (MAJOR-1 + CRITICAL-2):
1. Thread `temperature_metric` through `run_replay` → `_replay_one_settlement` (OR explicit scope-ruling deferral to Phase 7/8). Team-lead ruling requested before implementation.
2. Update `test_run_replay_cli.py` + `test_runtime_guards.py` + `test_backtest_outcome_comparison.py` fixtures to seed `temperature_metric='high'` on forecast rows. ~1 line per test.

**To exec-ida** (Gate D + fix-pack tidy):
3. Delete obsolete `test_phase5a_truth_authority.py::test_read_mode_truth_json_none_mode_does_not_raise` (replaced by R-AQ-1 in fix-pack). Self-carry-over from my fix-pack wide-review miss.
4. Add `spec: CalibrationMetricSpec` kwarg to `rebuild_v2`, propagate to `_process_snapshot_v2`. ~10-15 LOC. Unblocks R-AZ-1/2.

**Scope question to team-lead**: is `run_replay` threading in-scope for 5C or deferred to Phase 7/8 (LOW shadow/activation)? My read is deferral-acceptable if explicitly ruled — the half-chain threading is coherent as a 5C landing slice if LOW activation is gated behind Phase 8 anyway.

Post-ITERATE: expect 14 passed on 5C tests (12 existing + R-AZ-1/2 flipping GREEN after rebuild_v2 spec lands); full-suite regression back to 138 failed or better.

## Commit scope (if ITERATE lands clean)

~5-6 files:
- `src/engine/replay.py` (5C impl, already on disk)
- `scripts/rebuild_calibration_pairs_v2.py` (add spec to rebuild_v2)
- `tests/test_phase5c_replay_metric_identity.py` (NEW, 10 tests)
- `tests/test_phase5_gate_d_low_purity.py` (NEW, 3 tests)
- `tests/test_run_replay_cli.py` (fixture update)
- `tests/test_runtime_guards.py` + `test_backtest_outcome_comparison.py` (if same class)
- `tests/test_phase5a_truth_authority.py` (delete obsolete test)

Suggested commit header: `feat(phase5C): replay MetricIdentity half-1 + Gate D purity antibody + rebuild_v2 spec prop`.

---

*Authored*: critic-beth (opus, persistent)
*Disk-verified*: 2026-04-17, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`, fresh pytest on 5C files + full suite + Grep confirming caller-chain threading gap at `run_replay`:L2002.
