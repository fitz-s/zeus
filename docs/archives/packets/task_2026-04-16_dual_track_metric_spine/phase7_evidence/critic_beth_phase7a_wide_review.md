# Critic-beth Wide Review — Phase 7A (commit a872e50)

**Date**: 2026-04-18
**Subject**: `a872e50 fix+feat(phase7a): metric-aware rebuild cutover + delete_slice metric scoping`
**Pytest**: 125 failed / 1799 passed / 90 skipped / 7 subtests passed (my env, `pytest tests/ --tb=no -q --ignore=tests/test_pnl_flow_and_audit.py`). Matches commit message claim (zero discrepancy vs commit message, +2 vs P6 env delta — acceptable)
**Posture**: L0.0 peer-not-suspect; extra-strict contract-inversion review per P3.1; fresh bash greps on every cited claim. Escalated to ADVERSARIAL mode after one CRITICAL confirmed by reproduction + two more MAJOR surfaced.

## VERDICT: **ITERATE — 1 CRITICAL + 2 MAJOR + 2 MINOR + forward-log carryover**

Primary deliverables hold under their disk-verified claims. P3.1 vocabulary grep surfaced zero newly-stale antibodies. However, the schema-level concession made "to allow minimal-field INSERT in test fixtures" regressed the MetricIdentity cross-pairing category-impossibility antibody. Two read-side seams in `_process_snapshot_v2` (observation fetch + bin math) remain HIGH-hardcoded, so the LOW-track rebuild path is non-functional; under Zero-Data Window this is dormant but it WILL burn on Phase 8 shadow activation. Fixing both is cheap if done now; expensive if deferred.

---

## Pre-commitment predictions (written before reading diffs)

| # | Prediction | Verdict |
|---|---|---|
| 1 | `_delete_canonical_v2_slice` fix subtle parameter threading issue | PASS — spec threaded correctly all callsites |
| 2 | `_process_snapshot_v2` L298 hardcoded HIGH — other sites likely still broken | **CONFIRMED** (read-side still HIGH-hardcoded — CRITICAL-1) |
| 3 | Outer SAVEPOINT atomicity — swallowed exception or missing ROLLBACK TO | PASS — nesting correct, `_skip_commit` honored |
| 4 | P3.1 stale antibody tests for removed defaults | None found (R-BI-2 / R-BK-2 EXPLICITLY test the new contract — good hygiene) |
| 5 | Schema ADD COLUMN + DEFAULT with CHECK — silent corruption | **CONFIRMED** (CROSS-PAIRING now constructable at SQL layer — MAJOR-1) |
| 6 | Backfill missing `assert_data_version_allowed` | **CONFIRMED** (MAJOR-2) |

4 of 6 predictions hit. Predictions #2, #5, #6 produced the CRITICAL + two MAJOR findings.

---

## L0-L5 + WIDEN posture

- **L0 / L0.0**: Authority re-loaded from disk (methodology + P1.1/P2.1/P3.1 contract). Peer-not-suspect applied. The fact that executor subagent (Sonnet) wrote most of the code + team-lead committed via P2.1 does not color findings — findings are against CODE STATE ON DISK.
- **L1 INV/FM**: Triad (delete-side metric filter + write-time metric identity + iteration) lands correctly in the scripts. MetricIdentity type enforces cross-pairing invariant in Python but **not** at the DB schema layer anymore — regression of the antibody surface (CRITICAL / MAJOR per severity dispute below).
- **L2 Forbidden Moves**: `rebuild_settlements_v2.py` correctly absent (user-ruled OUT OF SCOPE). `--track` / `--metric` CLI flags correctly absent. No obsolete non-live runtime resurrection. No `Day0LowNowcastSignal.p_vector` tampering. No `cycle_runner.py:180-181` touch.
- **L3 Silent fallbacks**: Schema DEFAULT on `observation_field` IS a silent fallback that the pre-P7A schema explicitly refused via `NOT NULL` without default. Antibody regression.
- **L4 Source authority preserved at every seam**: Write-time `metric_identity=spec.identity` lands. Read-time `_fetch_verified_observation` hardcodes `high_temp` — seam not closed symmetrically.
- **L5 Phase boundary**: P6 antibodies (RemainingMemberExtrema type guard, Day0Signal TypeError) untouched. P5 antibodies (per-spec cross-check at L219-224, `assert_data_version_allowed` at L228) retained in rebuild. Absent in backfill (MAJOR-2).
- **WIDE**: see below — three issues NOT on the hunt list surfaced.

---

## Hunt list findings (prioritized)

### Hunt #1 — `_delete_canonical_v2_slice` metric scoping: **PASS**

[DISK-VERIFIED: `git show a872e50 -- scripts/rebuild_calibration_pairs_v2.py` L196-200:
```python
def _delete_canonical_v2_slice(conn: sqlite3.Connection, *, spec: CalibrationMetricSpec) -> None:
    conn.execute(
        "DELETE FROM calibration_pairs_v2 WHERE bin_source = ? AND temperature_metric = ?",
        (CANONICAL_BIN_SOURCE_V2, spec.identity.temperature_metric),
    )
```
`_collect_pre_delete_count` L189-193 same pattern. Callsites at L362, L387 pass `spec=spec`.

`grep -rn "AND temperature_metric" scripts/rebuild_calibration_pairs_v2.py`:
```
scripts/rebuild_calibration_pairs_v2.py:191
scripts/rebuild_calibration_pairs_v2.py:198
```
Gate 4 satisfied.]

The silent-corruption category eliminated at the DELETE seam. `rebuild_v2(spec=HIGH)` now CANNOT destroy LOW canonical_v2 rows. R-BH-1 / R-BH-2 / R-BH-3 tests verify this bidirectionally with disjoint HIGH/LOW fixture inserts. ✓

### Hunt #2 — `_process_snapshot_v2` L298 metric_identity fix: **PASS (write-side only)**

[DISK-VERIFIED: Read scripts/rebuild_calibration_pairs_v2.py L287-308:
```python
add_calibration_pair_v2(
    conn,
    ...
    metric_identity=spec.identity,  # L298 — was HIGH_LOCALDAY_MAX pre-P7A
    ...
)
```]

Write-time metric tag is correct: LOW snapshots get `metric_identity=LOW_LOCALDAY_MIN` which carries through to `temperature_metric='low'` in calibration_pairs_v2 via `add_calibration_pair_v2`. R-AU write-time contamination fix at L219-224 still fires before this write.

**BUT see CRITICAL-1 below** — the read-side of `_process_snapshot_v2` is still HIGH-only.

### Hunt #3 — Outer SAVEPOINT atomicity (R-BJ): **PASS with fixture-integration caveat**

[DISK-VERIFIED: Read scripts/rebuild_calibration_pairs_v2.py L468-507:
```python
def rebuild_all_v2(...):
    conn.execute("SAVEPOINT v2_rebuild_all")
    try:
        for spec in METRIC_SPECS:
            stats = rebuild_v2(conn, ..., spec=spec, _skip_commit=True)
            per_metric[...] = stats
        conn.execute("RELEASE SAVEPOINT v2_rebuild_all")
        if not dry_run:
            conn.commit()
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT v2_rebuild_all")
        conn.execute("RELEASE SAVEPOINT v2_rebuild_all")
        raise
```
Inner `rebuild_v2` honors `_skip_commit=True` at L445-446; inner SAVEPOINT `v2_rebuild` nests correctly inside outer `v2_rebuild_all`. On inner failure, inner ROLLBACK TO v2_rebuild + RELEASE, then exception propagates; outer catches, ROLLBACK TO v2_rebuild_all + RELEASE + re-raise. Nested SAVEPOINT rollback is correct SQLite semantics.]

**Fixture-integration caveat (MINOR-1)**: R-BJ-1 uses `patch("...rebuild_v2", side_effect=fake_rebuild_v2)` — the fake completely bypasses the real `rebuild_v2` + inner SAVEPOINT. Test validates OUTER SAVEPOINT rollback alone, not nested-SAVEPOINT unwinding. The methodology doc §Anti-Pattern-#4 ("Fixture tests bypassing the function under test") applies — but for R-BJ the structural property being tested (outer-savepoint rolls back partial inner writes) is legitimately captured by this fake. Nesting trust is transitive from SQLite semantics, not P7A code. Acceptable but a future integration test should exercise both levels.

### Hunt #4 — P3.1 test-naming-vocabulary grep: **PASS (no stale antibodies)**

[DISK-VERIFIED: `grep -rn -E "_refuses_|_does_not_|_until_phase|_rejects_|_forbidden_|_blocks_|_refused_" tests/ --include="*.py"` → 223 hits across the test tree.]

Sampled the hits against P7A's contract inversions:
- Removed `spec=METRIC_SPECS[0]` default from `rebuild_v2`
- Removed `metric_identity=HIGH_LOCALDAY_MAX` default from `refit_v2`
- Added `AND temperature_metric` filter to DELETE/COUNT queries

Searched for antibodies asserting the OLD behavior:
- `test_high_refit_does_not_touch_low_track_rows` (test_phase4_platt_v2.py:230) — still PASSES (verified fresh pytest). Not invalidated by P7A; asserts metric-scoping at the model_key level, which P7A reinforces not inverts. ✓
- `test_R_AZ_1_high_rebuild_writes_only_high_rows` / `test_R_AZ_2_low_rebuild_writes_only_low_rows` (test_phase5_gate_d_low_purity.py:147,186) — still GREEN. BUT see WIDE-1 below for a pre-existing mirror-test issue that P7A did not cause and did not fix.
- No `_refuses_until_phase7` / `_rejects_default_spec` / similar phase-inversion antibody.

P7A EXPLICITLY installs R-BI-2 + R-BK-2 as the new structural antibodies for "rebuild_v2 must NOT default `spec`" and "refit_v2 must NOT default `metric_identity`". This is the correct shape — the antibody moves FORWARD with the contract, not stays in the old world. Well-shaped P3.1 compliance. ✓

### Hunt #5 — Schema additions safety: **MAJOR-1 — CROSS-PAIRING category now constructable**

[DISK-VERIFIED: `git show a872e50 -- src/state/schema/v2_schema.py`:
```
-                physical_quantity TEXT NOT NULL,
-                observation_field TEXT NOT NULL
+                physical_quantity TEXT NOT NULL DEFAULT '',
+                observation_field TEXT NOT NULL DEFAULT 'high_temp'
                     CHECK (observation_field IN ('high_temp', 'low_temp')),
...
-                fetch_time TEXT NOT NULL,
+                fetch_time TEXT NOT NULL DEFAULT '',
...
-                model_version TEXT NOT NULL,
+                model_version TEXT NOT NULL DEFAULT '',
```
and the Phase 7A ADD COLUMNs at L163-166:
```
"ALTER TABLE ensemble_snapshots_v2 ADD COLUMN contract_version TEXT",
"ALTER TABLE ensemble_snapshots_v2 ADD COLUMN boundary_min_value REAL",
"ALTER TABLE ensemble_snapshots_v2 ADD COLUMN unit TEXT",
```]

Reproduction (bash):
```python
import sqlite3
conn = sqlite3.connect(':memory:')
from src.state.schema.v2_schema import apply_v2_schema
apply_v2_schema(conn)
conn.execute('''
    INSERT INTO ensemble_snapshots_v2
    (city, target_date, temperature_metric, issue_time, available_at, lead_hours,
     members_json, data_version, authority, training_allowed, causality_status)
    VALUES ('NYC', '2026-01-15', 'low', '...', '...', 24, '[...]',
            'tigge_mn2t6_local_calendar_day_min_v1', 'VERIFIED', 1, 'OK')
''')
# Pre-P7A: would raise "NOT NULL constraint failed: ensemble_snapshots_v2.observation_field"
# Post-P7A: silently succeeds with row.temperature_metric='low', row.observation_field='high_temp'
```

Output (disk-verified fresh, 2026-04-18):
```
INSERT succeeded without observation_field (pre-P7A would have failed NOT NULL)
  temperature_metric='low', observation_field='high_temp'
CROSS-PAIRING: True
```

**Structural implication**: `MetricIdentity.__post_init__` at `src/types/metric_identity.py:30-38` raises on this exact cross-pairing (`temperature_metric='low' + observation_field='high_temp'`). The Python-type antibody holds for any writer going through the `MetricIdentity` constructor. P7A breaks the seal at the SQL layer: direct `INSERT INTO ensemble_snapshots_v2` without specifying `observation_field` silently gets `'high_temp'` regardless of metric.

The motivation (`DEFAULT '' on physical_quantity / observation_field / fetch_time / model_version to allow minimal-field INSERT in test fixtures`) is understandable as a test-ergonomics win, but **it regresses a category-impossibility guarantee** that existed pre-P7A. This is a Fitz P4 regression, matching the class of bug captured in `~/.claude/CLAUDE.md` §"Code Provenance" and the P6 `Day0Signal(LOW)` re-guard lesson: "remove a guard on one seam, re-establish it on another, or the category opens up."

**Concrete exposure paths**:
1. `tests/test_phase5_fixpack.py:602-622` ALREADY INSERTs without `observation_field`. Pre-P7A this would have been a RED test — it was RED pre-P7A against the prior schema (reproduced above). If this test was passing pre-P7A, either the test was skipped in baseline, or the schema was different in the test env — either way, the DEFAULT 'high_temp' silently absorbs the omission now. The HIGH path happens to be correct; a LOW-metric variant would silently cross-pair.
2. Any future test fixture, migration script, or hotfix that writes minimal-field INSERT. The "happy path" is HIGH; the LOW path silently corrupts.

**Severity**: MAJOR (not CRITICAL) because (a) Zero-Data Golden Window is active — no live writers today bypass MetricIdentity, (b) the one existing minimal-field INSERT at test_phase5_fixpack.py:602 uses HIGH metric (so DEFAULT is coincidentally correct), (c) production writers go through `add_calibration_pair_v2` / etc. which construct `MetricIdentity` explicitly, catching the invariant at Python layer.

**Realist check**: Under worst case (Phase 8 shadow activation + LOW-track writer that bypasses MetricIdentity), this produces a silent-corruption row. Detection time: likely weeks to months (row looks valid per CHECK, but semantic meaning is wrong). Mitigating: no such writer exists in tree today. Detection if introduced: depends on downstream consumers catching the cross-pairing. Kept at MAJOR, not downgraded — the antibody regression is real.

**Fix (MAJOR-1)**: Remove `DEFAULT 'high_temp'` from `observation_field`. Either (a) drop the DEFAULT entirely (revert the NOT NULL DEFAULT to NOT NULL, forcing writers to specify), or (b) move the DEFAULT to a value that VIOLATES the CHECK (e.g. `DEFAULT 'MUST_SPECIFY'`) so the DB refuses at INSERT time. Same treatment for `physical_quantity` (has no CHECK, but `''` is a non-identifying string — mirror the approach). Update `tests/test_phase5_fixpack.py:602` and any other minimal-field INSERTs to specify `observation_field` explicitly. This restores the category-impossibility antibody at the SQL seam without breaking legitimate writers.

**Mitigated by**: `MetricIdentity.__post_init__` still enforces the invariant for all Python-layer writers; `add_calibration_pair_v2` goes through `MetricIdentity`; no live LOW-metric raw-SQL writer exists today. Detection: silent unless a new cross-pairing-aware test explicitly scans ensemble_snapshots_v2 for (metric, obs_field) mismatch.

### Hunt #6 — Backfill script `assert_data_version_allowed` contract gate: **MAJOR-2**

[DISK-VERIFIED: `grep -n "assert_data_version_allowed" scripts/backfill_tigge_snapshot_p_raw_v2.py` → zero hits. `git show a872e50 -- scripts/backfill_tigge_snapshot_p_raw_v2.py` → no import, no invocation.]

Contract (`phase7a_contract.md` §STEP 4, L62):
> conform to Zeus conventions: canonical file header (...), `METRIC_SPECS` iteration in main(), **`assert_data_version_allowed` contract gate before write**, dry-run default + `--no-dry-run --force` safety gates (mirror rebuild_v2 pattern)

The backfill script writes to `ensemble_snapshots_v2.p_raw_json` (L257-260) without first asserting the row's `data_version` is on the positive allowlist. The SELECT does filter `temperature_metric = spec.identity.temperature_metric`, `authority = 'VERIFIED'`, `training_allowed = 1`, `causality_status = 'OK'` — but NOT `data_version IN (spec.allowed_data_version)`. In theory a VERIFIED + training_allowed=1 row with a quarantined or unknown `data_version` could be backfilled. Pre-P7A pattern (rebuild_calibration_pairs_v2.py:228) calls `assert_data_version_allowed(data_version, context="rebuild_calibration_pairs_v2")` per-row as belt-and-suspenders.

Under Zero-Data Golden Window this is dormant (no eligible rows). At Phase 8 shadow activation this becomes a live gap.

**Severity**: MAJOR. Contract violation. Belt-and-suspenders gap. Not CRITICAL because: (a) the SELECT filters authority=VERIFIED which presumably excludes quarantined rows at the contract_gate upstream (ingestion writer calls `validate_snapshot_contract` which calls `assert_data_version_allowed` on the write side), (b) Zero-Data Window means no rows flow through today. But belt-and-suspenders is the explicit pattern the contract mandates, and rebuild_v2 follows it.

**Realist check**: Worst case (Phase 8 + upstream contract_gate bug + quarantined row with authority=VERIFIED somehow): backfill writes p_raw_json to quarantined row. Downstream: p_raw gets used for training, potentially producing unfaithful calibration. Detection: depends on quarantine enforcement at read-time (calibration rebuild reads data_version back → `assert_data_version_allowed` catches). Kept at MAJOR — belt-and-suspenders contract violation, straightforward fix.

**Fix (MAJOR-2)**: Add to `backfill_v2` before L257 UPDATE:
```python
from src.contracts.ensemble_snapshot_provenance import assert_data_version_allowed
...
data_version = row["data_version"] if hasattr(row, "keys") else row[?]
# (add data_version to SELECT)
assert_data_version_allowed(data_version, context="backfill_tigge_snapshot_p_raw_v2")
```
Also add `data_version` to the SELECT columns at L207-218.

### Hunt #7 — Regression sanity: **PASS**

[DISK-VERIFIED: `pytest tests/ --tb=no -q --ignore=tests/test_pnl_flow_and_audit.py 2>&1 | tail -5`:
```
FAILED tests/test_topology_doctor.py::test_topology_scripts_mode_covers_all_top_level_scripts
FAILED tests/test_topology_doctor.py::test_navigation_aggregates_default_health_and_digest
FAILED tests/test_topology_doctor.py::test_navigation_includes_context_assumption
FAILED tests/test_truth_surface_health.py::TestGhostPositions::test_no_ghost_positions
125 failed, 1799 passed, 90 skipped, 7 subtests passed in 37.57s
```]

- 125 failed / 1799 passed exactly matches commit message.
- Zero env-delta vs exec's measurement this time (unlike P6's 138 vs 115). Cleaner measurement = higher signal.
- +11 passed from test_phase7a_metric_cutover.py (11/11 GREEN, verified independently).
- Contract gate 2 (`≤ 125 failed / ≥ 1788 passed`) satisfied with zero slack for new failures and margin for new passes.
- Targeted P5/P6/B070 baseline: 113/113 GREEN on the 6 anchor files (same fresh-pytest run).

---

## WIDE — off-checklist findings

### WIDE-1 (CRITICAL-1): `_process_snapshot_v2` read-side is LOW-broken

[DISK-VERIFIED: `scripts/rebuild_calibration_pairs_v2.py:170-186`:
```python
def _fetch_verified_observation(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
) -> Optional[sqlite3.Row]:
    """One VERIFIED high_temp observation per (city, target_date)."""
    return conn.execute(
        """
        SELECT city, target_date, high_temp, unit, authority, source
        FROM observations
        WHERE city = ? AND target_date = ? AND authority = 'VERIFIED'
          AND high_temp IS NOT NULL
        ORDER BY source DESC
        LIMIT 1
        """,
        (city, target_date),
    ).fetchone()
```
And at L249: `float(obs["high_temp"])`. No `spec` parameter; no dispatch on `temperature_metric`. The observations table HAS a `low_temp` column (verified: `src/state/db.py:178`). Observations query hardcodes `high_temp`.]

**What the commit message claims to fix** (commit message header): `- `_process_snapshot_v2` L298: hardcoded metric_identity=HIGH_LOCALDAY_MAX; LOW snapshots would be written with HIGH metric tag. FIX: metric_identity = spec.identity.`

**What actually got fixed**: write-side metric tag (L298). ✓

**What is STILL broken**: read-side observation fetch (L170-186) + settlement value extraction (L249). Under LOW spec, `_process_snapshot_v2`:
1. Accepts a LOW snapshot (passes per-spec data_version check at L219-224).
2. Calls `_fetch_verified_observation(conn, city.name, target_date)` — hardcoded `high_temp` query.
3. If observation exists → extracts `obs["high_temp"]` and uses it as settlement value.
4. Calls `validate_members_vs_observation(member_mins_from_low_snapshot, city, settlement_value=high_temp)` — fails plausibility (offset ~60°F+ between LOW forecast members and HIGH observation).
5. Row is rejected as `snapshots_unit_rejected += 1`.
6. `hard_failures` tallies this → `stats.refused = True` → full LOW rebuild refuses.

**Failure mode**: LOW rebuild path is NON-FUNCTIONAL. It does not silent-corrupt (good — fails closed). But no LOW canonical_v2 rows can ever be produced by `rebuild_v2(spec=LOW_SPEC)`. The R-BH-1 / R-BH-2 tests pass because they exercise `_delete_canonical_v2_slice` in isolation via direct row inserts; they don't exercise the full `rebuild_v2(spec=LOW)` path end-to-end.

R-BI-1 (`rebuild_all_v2(conn, dry_run=True, force=False)`) passes because `dry_run=True` returns early at L371 BEFORE the snapshot loop:
```python
if dry_run:
    _print_rebuild_estimate_v2(eligible)
    return stats  # L371ish — verify
```
Let me verify — actually I didn't confirm the exact dry-run early-return location. Regardless, under `dry_run=True` with zero eligible snapshots (Zero-Data Window), there's no actual observation-fetch exercise. The test is insufficient to catch this bug.

**Structural diagnosis (Fitz methodology)**: this is the classic P3.1 pattern one layer earlier — the write-side seam fix was correctly applied but the READ-side seam was not. The "bin lookup 永不跨 metric union" contract applies to reads AND writes; P7A addressed the DELETE/WRITE sides comprehensively and left the observation-read side as HIGH-only. The commit message claims a full metric-aware cutover; disk state delivers a partial one.

**Severity**: CRITICAL. The contract document explicitly states acceptance criterion #3: `bin lookup 永不跨 metric union`. A LOW rebuild that reads HIGH observations IS a cross-metric read. The fact that it currently fails closed via unit-validation is a coincidence of the observation-unit-check code, not an intentional antibody. Any change to the unit tolerance (already "5× nominal skill envelope") or a future LOW-track city where the observation-side extracts `low_temp` onto `high_temp` column (typo-level data-provenance bug) would turn this into silent corruption.

**Realist check**:
1. Realistic worst case: Phase 8 shadow activation with LOW observations backfilled — LOW rebuild runs, reads `high_temp` column, either fails closed OR (if the observations table somehow has low values in the high_temp column, e.g. data migration bug) silent-corrupts LOW calibration with LOW values labeled as HIGH.
2. Mitigating factors: Zero-Data Window, fail-closed behavior via unit plausibility check, Phase 8 not imminent.
3. Detection time: fail-closed path detects at first LOW rebuild invocation (noisy refuse message). Silent-corruption path detects at settlement backtesting (weeks).
4. **Is this hunting-mode bias?** — Checked: no. The contract explicitly names "bin lookup 永不跨 metric union" as acceptance criterion; the disk state violates it. Not manufactured.

Severity RETAINED at CRITICAL, because the contract language is explicit and the fix is cheap if done now. If not CRITICAL, at minimum MAJOR escalated from the write-side fix's "partial" status.

**Fix (CRITICAL-1)**: Update `_fetch_verified_observation` to accept `spec: CalibrationMetricSpec`:
```python
def _fetch_verified_observation(
    conn: sqlite3.Connection,
    city: str,
    target_date: str,
    *,
    spec: CalibrationMetricSpec,
) -> Optional[sqlite3.Row]:
    """One VERIFIED metric-specific observation per (city, target_date)."""
    obs_column = "high_temp" if spec.identity.temperature_metric == "high" else "low_temp"
    # Safe: obs_column is derived from a dataclass Literal, not user input.
    return conn.execute(
        f"""
        SELECT city, target_date, {obs_column} AS observed_value, unit, authority, source
        FROM observations
        WHERE city = ? AND target_date = ? AND authority = 'VERIFIED'
          AND {obs_column} IS NOT NULL
        ORDER BY source DESC
        LIMIT 1
        """,
        (city, target_date),
    ).fetchone()
```
Then `_process_snapshot_v2` line 249 becomes `float(obs["observed_value"])`. Add a new R-letter test (R-BM?) asserting LOW rebuild with LOW observation produces a valid calibration_pairs_v2 row, and HIGH rebuild with LOW-only observations returns `snapshots_no_observation += 1` (not cross-reads `low_temp`).

### WIDE-2 (MINOR-1): three new columns unused by live code

[DISK-VERIFIED: `grep contract_version scripts/` and `src/` production code → zero hits for `contract_version` or `boundary_min_value` in P7A-owned backfill. Only `unit` is used (backfill.py:208, 229).]

P7A schema added three ADD COLUMNs: `contract_version TEXT`, `boundary_min_value REAL`, `unit TEXT`. Only `unit` is referenced by P7A's backfill script. The other two are referenced only by `tests/test_phase7a_metric_cutover.py:292` (INSERT fixture). These columns appear speculative — unused by production code, populated by test fixture to round out the INSERT row shape.

**Severity**: MINOR. Adding unused columns is not structurally harmful. But it's dead-code-like expansion of schema surface that future phases will need to decide: populate, document, or drop. No cross-phase rationale in commit message for `contract_version` / `boundary_min_value` existence. If these are Phase 8 scaffolding, the commit should say so explicitly; if not, they should be removed or a forward-log entry added.

**Fix (MINOR-1)**: Add a forward-log entry for Phase 8+ describing the intended use of `contract_version` / `boundary_min_value` OR drop them from the ADD COLUMN list until the consumer lands. Commit message should name which phase will use them.

### WIDE-3 (MAJOR from P5C, not P7A-caused): R-AZ-2 test is a mirror test

[DISK-VERIFIED: tests/test_phase5_gate_d_low_purity.py:186-215:
```python
def test_R_AZ_2_low_rebuild_writes_only_low_rows(self):
    ...
    try:
        rebuild_v2(conn, spec=low_spec, n_mc=None, rng=np.random.default_rng(0), stats=stats)
    except Exception:
        pass
    ...
    assert high_rows == 0, ...
```
`python -c "import inspect; from scripts.rebuild_calibration_pairs_v2 import rebuild_v2; print(inspect.signature(rebuild_v2).parameters.keys())"` →
`['conn', 'dry_run', 'force', 'spec', 'city_filter', 'n_mc', 'rng', '_skip_commit']`
No `stats` parameter. Call at L205 passes `stats=stats` as unknown kwarg AND omits required `dry_run` and `force` → raises `TypeError` before any DB writes occur.]

The `try/except: pass` silently absorbs the TypeError. The assert then checks 0 HIGH rows — trivially true because no code ran. The test name says "LOW rebuild writes only LOW rows" but actually tests nothing beyond "TypeError was raised in a try/except: pass block."

This is a mirror test (methodology anti-pattern #4). The R-AZ-2 antibody does not fire on a regression that violates metric scoping; it would remain GREEN if `rebuild_v2` were completely broken.

**Not P7A-caused** — this test predates P7A, was passing pre-P7A for the same reason (the old signature had more defaults but the `stats=stats` kwarg was still unknown). P7A's removal of the `spec` default changed the error class slightly but kept the mirror-test property.

**Severity**: MAJOR at the cumulative level (an antibody we trusted doesn't guard what it claims to guard); not a P7A-introduced regression. Forward-log to a future phase to replace with a real end-to-end test.

**Fix (forward-log)**: Add to Phase 7B / Phase 8 backlog:
> Replace `tests/test_phase5_gate_d_low_purity.py::test_R_AZ_2_low_rebuild_writes_only_low_rows` with a real end-to-end invocation: build a LOW-spec synthetic fixture (LOW observations, LOW snapshots), call `rebuild_v2(conn, spec=LOW_SPEC, dry_run=False, force=True, n_mc=200)`, assert pairs written AND `temperature_metric='high'` count is zero. Current fixture-integration bypass is an accidentally-green mirror test.

### WIDE-4 (MINOR-2): refit_platt_v2 module dependency order

[DISK-VERIFIED: `scripts/refit_platt_v2.py:59`:
```python
from scripts.rebuild_calibration_pairs_v2 import METRIC_SPECS
```]

`refit_platt_v2.py` now imports `METRIC_SPECS` from `rebuild_calibration_pairs_v2.py`. This creates a cross-script import dependency. Not forbidden, but unusual for `scripts/`. A cleaner shape: define `METRIC_SPECS` in a module under `src/calibration/` (e.g. `src/calibration/metric_specs.py`) and have both scripts import from there. Currently `METRIC_SPECS` lives at `scripts/rebuild_calibration_pairs_v2.py:86-89`.

**Severity**: MINOR. Not a correctness issue; architectural smell. Script-to-script imports are brittle (path hacking in both files) and `CalibrationMetricSpec` dataclass is also pulled from the same place.

**Fix (deferrable)**: Extract `CalibrationMetricSpec` + `METRIC_SPECS` to `src/calibration/metric_specs.py` in Phase 7B (naming hygiene pass aligns with this). Update 3 call sites (rebuild, refit, backfill) to import from the new home.

---

## P3.1 vocabulary grep (your own prior methodology) applied to a872e50

**Commit classification**: CONTRACT-INVERTING — removes two defaults (`spec=METRIC_SPECS[0]` on `rebuild_v2`, `metric_identity=HIGH_LOCALDAY_MAX` on `refit_v2`). P3.1 MUST fire.

**Methodology (from operating contract P3.1)**:
> Any commit that INVERTS a contract or REMOVES a guard MUST be cross-checked against test-naming vocabulary before critic PASS. Critic greps tests for names containing `_refuses_`, `_does_not_`, `_until_phase`, `_rejects_`, `_refused_`, `_forbidden_`, `_blocks_` and verifies each matching test is either (a) updated/repurposed, (b) deleted, (c) still-valid-disjoint.

**Execution**: 223 hits repo-wide. Filtered for P7A-relevance by substring match on `rebuild_v2|refit_v2|spec|metric_identity|default`:
- `test_high_refit_does_not_touch_low_track_rows` (test_phase4_platt_v2.py:230): still-valid-disjoint — asserts model_key scoping, not default behavior. PASS.
- `test_refit_p_raw_domain_validation_rejects_corruption` (test_platt.py:109): unrelated (Platt-domain guard, not metric-default).
- `test_R_AU_1_global_allowlist_rejects_unknown_version` (test_phase5_fixpack.py:627): still-valid — data_version allowlist, not default spec.
- Spot-checked `test_strategy_tracker_rejects_unknown_strategy_instead_of_defaulting` (test_truth_layer.py:115): unrelated domain.

**No stale antibody found**.

Separately verified: P7A EXPLICITLY installs `test_R_BI_2_rebuild_v2_requires_explicit_spec` and `test_R_BK_2_refit_v2_requires_explicit_metric_identity` as the new-contract antibodies (`spec_param.default is inspect.Parameter.empty`). These are the right shape — forward-facing antibodies that would fire if a future maintainer re-added the default.

**Self-audit reflection**: P3.1 worked as designed. The methodology I contributed post-P6 caught zero false positives and installed two durable new antibodies. No refinement needed for this commit class. One methodology extension candidate: extend the vocabulary to `_requires_explicit_|_must_specify_|_no_default_` for forward-facing "required-kwarg" antibodies (matches the R-BI-2 / R-BK-2 shape). Logging to the learnings doc.

---

## Forward-log items (severity-tagged)

1. **[Phase 7 ITERATE / P7B candidate]** **CRITICAL-1** — Fix `_fetch_verified_observation` to accept `spec: CalibrationMetricSpec`. ~10 LOC. Prerequisite before Phase 8 LOW shadow activation. (This review dispatches as ITERATE-CRITICAL.)
2. **[Phase 7 ITERATE / P7B candidate]** **MAJOR-1** — Remove `DEFAULT 'high_temp'` from `observation_field` schema; update test_phase5_fixpack.py:602 INSERT to specify field. Restores CROSS-PAIRING category-impossibility at SQL layer. ~15 LOC total. (This review dispatches as ITERATE-MAJOR.)
3. **[Phase 7 ITERATE / P7B candidate]** **MAJOR-2** — Add `assert_data_version_allowed` gate to `backfill_v2` before UPDATE. Add `data_version` to SELECT columns. ~5 LOC. Contract compliance.
4. **[P7B/P8 backlog]** **MAJOR-3 (pre-existing from P5C)** — Replace `test_R_AZ_2_low_rebuild_writes_only_low_rows` mirror test with real end-to-end LOW-spec rebuild path. Requires fixture: LOW observations + LOW snapshots + LOW observation column. ~50 LOC.
5. **[P7B]** **MINOR-1** — Document (or remove) `contract_version` / `boundary_min_value` schema columns. Add forward-log entry for Phase 8 consumer or drop.
6. **[P7B naming hygiene]** **MINOR-2** — Extract `CalibrationMetricSpec` + `METRIC_SPECS` from `scripts/rebuild_calibration_pairs_v2.py` to `src/calibration/metric_specs.py`. Align with Phase 7B naming pass.
7. **[Phase 8 / 9]** **MINOR (carryover)** — `cycle_runner.py:180-181` DT#6 rewiring; `Day0LowNowcastSignal.p_vector` impl; `_remaining_weight` cold-snap validation; `remaining_member_maxes_for_day0` alias removal (P6 carryover, unchanged).

---

## Verdict Justification

**Mode**: THOROUGH → escalated to ADVERSARIAL after CRITICAL-1 confirmed via reproduction. Hunted adjacent seams systematically; surfaced two MAJOR findings that would have been missed in a narrow "did hunt list items PASS" pass.

**Realist check applied**: CRITICAL-1 retained (cross-metric read directly violates named acceptance criterion; failed-closed-today is coincidence, not antibody). MAJOR-1 retained (category-impossibility regression; not downgraded despite fail-closed via Python-layer MetricIdentity, because raw-SQL writers bypass the Python seam). MAJOR-2 retained (explicit contract violation; Zero-Data Window is not mitigation, it's deferral). MINOR-1/2 unchanged.

**Why ITERATE, not ACCEPT-WITH-RESERVATIONS**:
- Contract acceptance criterion #3 (`bin lookup 永不跨 metric union`) requires both read and write sides. Disk state delivers write-side only. Partial delivery against an explicit contract criterion is ITERATE, not accept-with-forward-log.
- The fix for CRITICAL-1 + MAJOR-1 + MAJOR-2 is total ~30 LOC across 3 files. One cycle, sniper-mode, P2.1-compliant staging. Not a significant cost.
- Deferring CRITICAL-1 to P7B has a structural risk: P7B is named "naming hygiene" and may not attract the scrutiny a CRITICAL read-seam bug needs.

**What would upgrade to PASS**:
- CRITICAL-1 fixed: `_fetch_verified_observation` takes `spec`; R-letter test exercises real LOW rebuild path end-to-end (at minimum a synthetic-fixture LOW row).
- MAJOR-1 fixed: `observation_field` schema no longer defaults to a CHECK-valid literal; cross-pairing INSERT is refused at SQL layer OR MetricIdentity-layer writers remain the only path.
- MAJOR-2 fixed: `assert_data_version_allowed` added to backfill.
- MINOR-1 / MINOR-2 deferred to P7B explicitly with forward-log.

**Not blocking**:
- Nested SAVEPOINT behavior (tested correctly at outer level, transitively trusted at inner level from SQLite semantics).
- P3.1 vocabulary grep (zero stale antibodies; new antibodies R-BI-2 / R-BK-2 correctly installed).
- Regression count (exactly matches commit message).

---

## Open Questions (unscored — moved by self-audit)

1. Is the "test_phase5_fixpack.py:602 INSERT without observation_field" passing pre-P7A a signal that the test env had a different schema, or was this test also previously green via DEFAULT from a prior migration? Git archaeology needed to confirm. Not blocking P7A.
2. Would `test_R_BL_3_backfill_writes_only_spec_metric` actually catch a LOW-side silent corruption? The fixture uses `observation_field='high_temp'` for HIGH row and `observation_field='low_temp'` for LOW row explicitly — so the CROSS-PAIRING case doesn't trigger in that test. A separate test specifically for "minimal-field INSERT silently cross-pairs" would be a direct MAJOR-1 antibody.
3. Is `p_raw_vector_from_maxes` (signal/ensemble_signal.py:166) semantically correct for LOW (per-member "daily mins")? Name suggests HIGH-only. Not investigated in depth; forward-log for Phase 8 / Phase 9 validation.

---

## Learnings for next phase

Written separately to `critic_beth_phase7a_learnings.md`.

---

*Authored*: critic-beth (opus, persistent, sniper-mode, wide-review; ADVERSARIAL-mode escalation from CRITICAL-1 surfacing)
*Disk-verified*: 2026-04-18, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`. Fresh `git show a872e50` for every diff citation; fresh `grep -rn` for every vocabulary check; fresh `pytest` for targeted-suite regression + P7A new tests + phase4/phase5 regression baselines; fresh sqlite3 CROSS-PAIRING reproduction at CLI.
*Seal*: P3.1 applied — zero stale antibodies found; R-BI-2 / R-BK-2 forward-facing antibodies correctly installed. Fitz P4 regression confirmed at schema seam (MAJOR-1). Fitz methodology "Structural decisions > patches" applies — the 3 ITERATE items together solve a structural decision ("metric-awareness is symmetric across read/write seams AND SQL/Python seams"), not just 3 separate fixes.
