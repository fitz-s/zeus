# Phase 7A Contract — Metric-Aware Rebuild Cutover (critical portion)

**Issued**: 2026-04-18 post Phase 6 + B070 merge, sniper-mode.
**Basis**: operating contract P1-P7 (binding) + P1.1/P2.1/P3.1 (post-P6 amendments).
**Authority anchors**:
- Master plan `zeus_dual_track_refactor_package_v2_2026-04-16/00_MASTER_EXECUTION_PLAN_zh.md` §"Phase 7" acceptance criteria:
  > - bucket key / query / unique key 都带 metric
  > - high / low 可以同城同日共存
  > - bin lookup 永不跨 metric union
- User rulings:
  - `rebuild_settlements_v2.py` DROPPED from scope (self-fabrication antipattern: would synthesize settlement data from our own records rather than getting it from PM/WU/HKO truth sources)
  - Split P7 → P7A (feature/bug work, this contract) + P7B (naming hygiene, queued)
  - `_delete_canonical_v2_slice` metric-scoping bug fixed as FIRST action within P7A
- B093 half-2: deferred to P8 (Zero-Data Golden Window still active; migrating replay query to empty `historical_forecasts_v2` = dead plumbing)

**Target**: ONE commit `fix+feat(phase7a): metric-aware rebuild cutover + delete_slice metric scoping`.

## Why P7A is critical (silent corruption path)

Current state at `b74d026` VIOLATES acceptance criterion #3 ("bin lookup 永不跨 metric union"):

```python
# scripts/rebuild_calibration_pairs_v2.py:196-200
def _delete_canonical_v2_slice(conn):
    conn.execute("DELETE FROM calibration_pairs_v2 WHERE bin_source = ?", (CANONICAL_BIN_SOURCE_V2,))
    # ↑ missing: AND temperature_metric = ?
```

Consequence: running `rebuild_v2(spec=HIGH)` deletes BOTH HIGH and LOW canonical_v2 pairs. If HIGH rebuild then LOW rebuild runs sequentially, state is self-healing. If only HIGH runs, LOW calibration data is silently destroyed. Silent-corruption category.

Compounding: `rebuild_v2()` defaults to `spec=METRIC_SPECS[0]` (HIGH only). Operator has no built-in way to cut both tracks.

## Deliverables (single commit)

### CRITICAL STEP 1 (lands first, standalone verifiable)

**`scripts/rebuild_calibration_pairs_v2.py::_delete_canonical_v2_slice`** (L196-200):
- Accept `spec: CalibrationMetricSpec` kwarg
- Add `AND temperature_metric = ?` filter, bind `spec.identity.temperature_metric`
- Update `_collect_pre_delete_count` (L189-193) identically — count scoped per-metric
- Callsite `rebuild_v2()` L360, L382 — pass `spec=spec`

### STEP 2 — METRIC_SPECS iteration

**`scripts/rebuild_calibration_pairs_v2.py::rebuild_v2()`**:
- Remove default `spec=METRIC_SPECS[0]` from signature (force explicit call; main() iterates)
- `main()`: iterate `for spec in METRIC_SPECS:` — within ONE SAVEPOINT so LOW failure rolls back HIGH (atomic dual-track rebuild)
- `RebuildStatsV2`: add `per_metric_stats: dict[str, RebuildStatsV2]` OR collapse into per-spec counters on existing fields — keep CLI output per-metric readable
- Aggregate hard-failures gate at L414 operates on combined stats

### STEP 3 — refit_platt_v2 main() iteration

**`scripts/refit_platt_v2.py::main()`** (L211 default is HIGH):
- Replace single-metric default with `for spec in METRIC_SPECS:` iteration in main()
- `refit_all_buckets(metric_identity=spec.identity)` called per spec
- Return non-zero exit if any spec fails

### STEP 4 — backfill_tigge_snapshot_p_raw_v2 scaffolding

**CREATE `scripts/backfill_tigge_snapshot_p_raw_v2.py`**:
- Adapt (DO NOT blind-copy) from `zeus_dual_track_refactor_package_v2_2026-04-16/04_CODE_SNIPPETS/backfill_tigge_snapshot_p_raw_v2.py`
- Conform to Zeus conventions: canonical file header (`# Lifecycle: / # Purpose: / # Reuse:`), `METRIC_SPECS` iteration in main(), `assert_data_version_allowed` contract gate before write, dry-run default + `--no-dry-run --force` safety gates (mirror rebuild_v2 pattern)
- Writes `p_raw_vector` to `ensemble_snapshots_v2` rows scoped per-metric
- Expected live behavior under Zero-Data Golden Window: no rows to backfill → no-op with clear log. Test via synthetic fixture.

### NEW tests

**`tests/test_phase7a_metric_cutover.py`** — R-BH..R-BL (5 R-letters):
- **R-BH**: `rebuild_v2(spec=HIGH)` preserves LOW canonical_v2 rows (insert LOW fixture, run HIGH rebuild, assert LOW untouched)
- **R-BI**: `rebuild_v2` main() iterates both METRIC_SPECS — produces per-metric row counts in stats
- **R-BJ**: single SAVEPOINT atomicity — LOW-side `hard_failure` rolls back HIGH writes (no orphan rows on failure)
- **R-BK**: `refit_platt_v2` main() iterates METRIC_SPECS — both tracks refit in one invocation
- **R-BL**: `backfill_tigge_snapshot_p_raw_v2` synthetic-fixture path — writes p_raw only to rows matching spec.identity.temperature_metric

## Acceptance gates

1. `pytest tests/test_phase7a_metric_cutover.py` → 5/5+ GREEN (test count may exceed 5 with paired acceptance cases)
2. Full regression ≤ 125 failed / ≥ 1788 passed baseline (post-B070-merge, current env). **ZERO new failures.**
3. Dry-run `python scripts/rebuild_calibration_pairs_v2.py` prints per-metric snapshot counts for HIGH AND LOW (both non-negative, `stats.refused == False` path reachable under empty-v2 window)
4. `grep -rn "AND temperature_metric" scripts/rebuild_calibration_pairs_v2.py` shows ≥1 hit in `_delete_canonical_v2_slice` + `_collect_pre_delete_count`
5. `grep -rn "for spec in METRIC_SPECS" scripts/refit_platt_v2.py scripts/rebuild_calibration_pairs_v2.py scripts/backfill_tigge_snapshot_p_raw_v2.py` — ≥3 hits (iteration landed in all three)
6. critic-beth wide-review PASS

## Hard constraints — DO NOT

- **Create `rebuild_settlements_v2.py`** — user-ruled self-fabrication antipattern; OUT OF SCOPE forever
- Bundle P7B scope (`_tigge_common.py` extraction, alias removal, script_manifest.yaml registration) — queued for separate contract
- Touch `replay.py::_forecast_rows_for` (B093 half-2, deferred P8)
- Touch `cycle_runner.py:180-181` (DT#6 wiring, P8)
- Touch `Day0LowNowcastSignal.p_vector` (P9 Gate F)
- Add `--track` / `--metric` CLI flags — use spec dataclass iteration pattern (P5 METRIC_SPECS precedent)
- Run real batch extraction or lift Zero-Data Golden Window — synthetic fixtures only
- Modify `validate_snapshot_contract`, `PortfolioState.authority`, `ModeMismatchError`, `CalibrationMetricSpec` semantics (locked at 5A/5B)
- `git add` or `git commit` yourself (per P2.1; announce staged files to team-lead)

## Pointers (3, per operating contract P6)

1. **This contract** — scope truth
2. **`docs/operations/task_2026-04-16_dual_track_metric_spine/phase7_microplan.md`** — planner recommendation (Strategy A, R-letter scheme, 3 risks with mitigations; note: planner output preceded user ruling on `rebuild_settlements_v2` drop)
3. **`zeus_dual_track_refactor_package_v2_2026-04-16/04_CODE_SNIPPETS/backfill_tigge_snapshot_p_raw_v2.py`** — reference skeleton for Step 4 (adapt to Zeus conventions, do NOT blind-copy)

Conditional:
- `scripts/rebuild_calibration_pairs_v2.py` current state (top = `b74d026`)
- `scripts/refit_platt_v2.py` current state (per audit: metric_identity=HIGH default at L211)

## Executable bootstrap

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
git log --oneline -3
# Expected top: b74d026 merge: data-improve-debug → data-improve (B070)

pytest tests/test_phase6_day0_split.py tests/test_metric_identity_spine.py tests/test_b070_control_overrides_history_v2.py --tb=no -q
# Expected: 57/57 GREEN (P6 + B070 regression baseline)

pytest tests/test_phase5b_low_historical_lane.py tests/test_phase5c_replay_metric_identity.py --tb=no -q
# Expected: GREEN (P5 regression baseline)
```

## Team protocol during P7A

- **Team-lead**: silent. Monitor `git log` passively. Respond only to scope-ruling question (CRITICAL) or critic escalation.
- **exec-kai** (Sonnet, retained): implementation + R-BH..R-BL drafting (spec-first per P4, NO impl grep) + test execution. Use subagents aggressively per P6 pattern (Explore for scans, debugger for failures, verifier for regression deltas).
- **critic-beth** (Opus, retained): silent standby until team-lead dispatches commit candidate. Then ONE wide review with L0.0 posture + P3.1 test-naming-vocabulary grep. If ITERATE, one-cycle fix dispatch.
- **P2.1**: exec announces staged files to team-lead with (diff-stat, pytest tally, regression delta). Team-lead verifies per P1.1 (`git status --short` → isolate surprises), stages announced files, commits with accurate message, pushes, dispatches critic.

## What closes P7A

- commit candidate with 5 R-letters GREEN + regression ≤ 125 failed / ≥ 1788 passed baseline
- critic PASS via wide review
- team-lead updates handoff with P7A closure + forward-log (any scope-ruling items for P7B or P8)
- P7B contract issued when P7A handoff commits

P7B scope preview (for awareness, NOT implementation target in P7A):
1. `_tigge_common.py` extraction (15 safe mechanical helpers; 3 compute_* variants left untouched with DELIBERATELY_NOT_EXTRACTED markers)
2. `remaining_member_maxes_for_day0` alias removal (9 call sites incl. 2 monkeypatch latent sites)
3. `architecture/script_manifest.yaml` registration for 5 scripts (incl. the new `backfill_tigge_snapshot_p_raw_v2.py` from P7A Step 4)
