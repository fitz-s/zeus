# Phase 4C + 4D — critic-alice verdict (bundled review)

Date: 2026-04-16
Scope: `scripts/rebuild_calibration_pairs_v2.py` (new, 495 LOC), `scripts/refit_platt_v2.py` (new, 326 LOC). `src/calibration/store.py::deactivate_model_v2` at :396 (already landed in 4B commit `5c48847` — confirmed by `git log --all` on the file; team-lead's intel that this was new to 4D was stale). `tests/test_phase4_platt_v2.py` (+100 LOC, 2 new integration tests).

## Final verdict (post-iterate): **PASS**

## Round 1 verdict: ITERATE (historical — preserved below)

One CRITICAL runtime bug + one MAJOR test-design gap (same pattern as Phase 4B round 1) + several MODERATEs. The CRITICAL blocks any live run; the MAJOR is why the CRITICAL slipped through 19/19 green tests.

## L0 authority + disk

- Authority re-loaded (zeus_current_architecture.md §13-§22, zeus_dual_track_architecture.md §2/§5/§6/§8, both TIGGE plans). L0 clean.
- Disk verification:
  - `git log --oneline -1` → `5c48847 Phase 4B` committed.
  - `git status --short` shows `?? scripts/rebuild_calibration_pairs_v2.py`, `?? scripts/refit_platt_v2.py`, `M tests/test_phase4_platt_v2.py`. `src/calibration/store.py` is **NOT** `M` — `deactivate_model_v2` at :396 is already committed in 4B. Team-lead's message said store.py was `M` with the helper "new in 4D" — stale.
  - `wc -l scripts/rebuild_calibration_pairs_v2.py scripts/refit_platt_v2.py` → 495 + 326 matches.

## Tests (I ran them myself, actual output)

```
WU_API_KEY=dummy python -m pytest tests/test_phase4_platt_v2.py tests/test_phase4_rebuild.py -v
→ 19 passed in 0.18s
```
All R-I/R-J/R-M/R-N subtests green + 2 new 4D integration tests (`test_refit_twice_leaves_exactly_one_active_row`, `test_high_refit_does_not_touch_low_track_rows`) also green.

**But see CRITICAL-1 below** — all 19 tests exercise helpers or direct-INSERT fixtures; none actually invoke `rebuild_calibration_pairs_v2.rebuild_v2()`. When I called the real function's SELECT against a populated table:

```
$ python3 -c "... from scripts.rebuild_calibration_pairs_v2 import _fetch_eligible_snapshots_v2; _fetch_eligible_snapshots_v2(conn, None)"
ERROR: OperationalError no such column: source
```

## Findings

### CRITICAL-1 — `rebuild_v2` SELECTs a column that does not exist on `ensemble_snapshots_v2`
**File:line:**
- `scripts/rebuild_calibration_pairs_v2.py:131` — `SELECT snapshot_id, city, target_date, issue_time, lead_hours, available_at, members_json, data_version, source FROM ensemble_snapshots_v2`.
- `scripts/rebuild_calibration_pairs_v2.py:184` — `source = snapshot["source"] or ""`.
- `scripts/rebuild_calibration_pairs_v2.py:261` — `source=source` passed to `add_calibration_pair_v2`.

**Empirical evidence:**
```python
from src.state.schema.v2_schema import apply_v2_schema
cols = [r[1] for r in conn.execute('PRAGMA table_info(ensemble_snapshots_v2)')]
# ['snapshot_id', 'city', 'target_date', 'temperature_metric', 'physical_quantity',
#  'observation_field', 'issue_time', 'valid_time', 'available_at', 'fetch_time',
#  'lead_hours', 'members_json', 'p_raw_json', 'spread', 'is_bimodal', 'model_version',
#  'data_version', 'training_allowed', 'causality_status', 'boundary_ambiguous',
#  'ambiguous_member_count', 'manifest_hash', 'provenance_json', 'authority',
#  'recorded_at', 'members_unit', 'members_precision']
'source' in cols  # False
```

The rebuild's fetch query **cannot execute against the real schema**. Any live or dry-run invocation of `rebuild_v2()` will fail with `sqlite3.OperationalError: no such column: source` on line 131.

**How did 19 tests pass?** Because none of them run `rebuild_v2`, `_fetch_eligible_snapshots_v2`, or any code path that touches the broken SELECT. This is CRITICAL-1 + MAJOR-2 compounded: the test-design gap (MAJOR-2 below) hid a runtime bug.

**Why source was added (inferred):** `add_calibration_pair_v2` takes a `source=""` kwarg that feeds `_resolve_training_allowed` (INV-15). The rebuild wants to propagate that source end-to-end. **Correct intent, wrong column location**: `ensemble_snapshots_v2` stores source-identifying info in `data_version` (canonical tag) and `provenance_json` (detailed fields). There is no bare `source` column.

**Fix options (exec-carol, pick one):**
- (a) Remove `source` from the SELECT, derive it from `data_version` at the rebuild site: `source = "tigge" if data_version.startswith("tigge") else "ecmwf_ens" if data_version.startswith("ecmwf_ens") else ""`. Zero schema change. ~3 lines.
- (b) Parse `provenance_json` and extract `provenance.get("source", "")`. The `ingest_json_file` code at `scripts/ingest_grib_to_snapshots.py:85-103` does NOT currently write a `source` key to provenance_json — so (b) requires an extractor change too.
- (c) Add `source TEXT` column to `ensemble_snapshots_v2` via ALTER TABLE. Most invasive; defers to Phase 5 parity.

**Recommend (a).** The INV-15 whitelist in `_resolve_training_allowed` already does `data_version.startswith(...)`, so deriving `source` from `data_version` is semantically the same check. The SELECT becomes:
```python
SELECT snapshot_id, city, target_date, issue_time, lead_hours,
       available_at, members_json, data_version
FROM ensemble_snapshots_v2
```
And line 184 becomes:
```python
source = ""  # derived downstream from data_version via _resolve_training_allowed
```
or drop the `source=source` kwarg entirely (it defaults to "" in `add_calibration_pair_v2`).

### MAJOR-2 — R-M tests bypass the rebuild function (same pattern as Phase 4B round 1 MAJOR-1)
**File:line:** `tests/test_phase4_rebuild.py:189-213` (`_insert_calibration_pair_v2`).

The R-M test class `TestCalibrationPairsV2IdentityFields` uses `_insert_calibration_pair_v2` which calls `add_calibration_pair_v2(...)` DIRECTLY with hand-crafted kwargs. None of the 5 R-M tests invokes `rebuild_v2`, `_fetch_eligible_snapshots_v2`, or `_process_snapshot_v2`. So R-M proves the writer function works, NOT the rebuild pipeline.

This is structurally identical to MAJOR-1 from my 4B round-1 verdict — and exec-bob correctly addressed it for 4B by adding `TestIngestJsonFileIntegration`. Phase 4C has not yet had the same fix.

**Why MAJOR:** exactly the four-constraints #2 pattern. Design intent (R-M: rebuild produces correct rows) encoded at the wrong abstraction layer (writer function, not the rebuild). CRITICAL-1 is the proof — the SELECT is broken and no test catches it.

**Fix (exec-carol + testeng-emma, ~40 lines):** Add `TestRebuildV2PipelineIntegration` to `tests/test_phase4_rebuild.py` that:
1. Populates `ensemble_snapshots_v2` with a minimal eligible row + matching `observations.high_temp` row.
2. Calls `rebuild_v2(conn, dry_run=False, force=True)`.
3. Asserts `calibration_pairs_v2` has the expected rows with correct identity fields.
4. Separately: asserts that a payload with a quarantined `data_version` raises `DataVersionQuarantinedError` via the rebuild path.

This test would have failed CRITICAL-1 immediately.

### MAJOR-3 — 4D `_fit_bucket` `decision_group_ids=np.array(..., dtype=object)` passed to Platt
**File:line:** `scripts/refit_platt_v2.py:142-144, 153`.

`ExtendedPlattCalibrator.fit(..., decision_group_ids=decision_group_ids, ...)` is passed a numpy object-dtype array of strings. The legacy refit path in `src/calibration/manager.py:214-218` passes the same shape:
```python
decision_group_ids = np.array([p.get("decision_group_id") for p in pairs], dtype=object)
```
So this is not a regression vs legacy — the same shape works for the legacy refit. **But** I can't find a test that asserts the new 4D refit produces the same Platt parameters as the legacy refit on identical input. Phase 4E parity_diff is supposed to do that, but Phase 4E depends on real data from Phase 4.5.

Flag as MAJOR because: if the numpy dtype handling in `ExtendedPlattCalibrator.fit` interacts subtly with the new code path (e.g. via a caller that unpacks differently), 4E parity will detect it — but too late. Lower risk than CRITICAL-1, hence MAJOR not CRITICAL.

**Fix (testeng-emma, ~25 lines):** add `test_fit_bucket_produces_same_params_as_legacy_on_identical_input` to `tests/test_phase4_platt_v2.py`. Feed both paths synthetic identical input; assert `|A_v2 - A_legacy| < 1e-6` and same for B, C.

If this is deferred to Phase 4E parity run, fine — but testeng-emma should flag that the 4E parity gate is the first time we learn if the two refit paths numerically agree.

### MODERATE-8 — `deactivate_model_v2` hard-DELETE has no audit trail
**File:line:** `src/calibration/store.py:396-420`.

Deletes the row. No `deactivated_at` timestamp, no move to archive table. If an operator runs refit, the prior Platt parameters are gone forever.

Team-lead flagged this question explicitly. My take: **hard DELETE is acceptable for Phase 4** because:
- `fitted_at` + commit log gives replay capability via `git log` on the refit invocation.
- Platt params are deterministic given (data, bucket_key, seed), so replay regenerates them.
- `platt_models_v2_archive` would be premature: 420 GRIB files × N buckets × occasional refit = <50 rows/month of archive churn, tiny.

Flag as MODERATE (documentation gap) rather than argue for structural change: `deactivate_model_v2`'s docstring should say "DELETE, not soft-deactivate, because UNIQUE(model_key) blocks INSERT-after-soft-delete. Audit trail comes from commit history + fitted_at timestamp on the replacement row."

**Fix (exec-carol, 3 lines of docstring):** add the audit-trail note to the docstring.

### MODERATE-9 — `MIN_DECISION_GROUPS = 15` is local to 4D; spec has it in `maturity_level`
**File:line:** `scripts/refit_platt_v2.py:60` vs `src/calibration/manager.py:88-105`.

4D hardcodes `MIN_DECISION_GROUPS = 15` as Phase 2 maturity threshold (matches `maturity_level()` Level 3: `15 <= n < 50`). The value is right, but it's a duplicated constant. If a future spec change bumps Level 3's lower bound to 20, `manager.py::maturity_level` updates but 4D's local `MIN_DECISION_GROUPS` stays 15 — silent drift.

**Fix (exec-carol, 2 lines):**
```python
# At top of refit_platt_v2.py:
from src.calibration.manager import calibration_maturity_thresholds
MIN_DECISION_GROUPS = calibration_maturity_thresholds()[2]  # Level 3 threshold
```
This makes the constant track spec.

### MODERATE-10 — 4C dry-run + hard-failure rollback interaction
**File:line:** `scripts/rebuild_calibration_pairs_v2.py:322-326` vs `:366-383`.

In `dry_run=True` path (line 322), early return before any SAVEPOINT. Good.
In live-write path, hard failures (missing_city > 0, no_obs_ratio > 30%, pairs_written == 0) raise AFTER `stats.snapshots_processed` has been incrementing non-zero — but the `except` block on line 393 correctly `ROLLBACK TO SAVEPOINT`. ✓

**But:** line 366 `hard_failures = missing_city_count + stats.snapshots_unit_rejected` — if missing_city_count is nonzero and unit_rejected is nonzero, ANY value triggers rollback. That may be too strict: a single city whose config was removed should not roll back the whole rebuild. Legacy `rebuild_calibration_pairs_canonical.py` let unknown-city snapshots skip silently. Phase 4C's strictness is a behavioral change.

**Fix option (exec-carol decision):**
- Keep strict (current code): document the stricter-than-legacy behavior. "V2 refuses rebuild on any non-zero hard-failure count."
- Relax to legacy parity: change `if hard_failures:` to `if hard_failures > tolerance_threshold:` where tolerance is e.g. 5 or 1% of eligible.

**Recommend keep-strict + document.** Strict-over-permissive is the right direction for Phase 4 (fail-closed trading law). Add to 4C docstring: "Any unknown-city snapshot or any unit-rejected snapshot rolls back the full rebuild. Legacy allowed per-row skip; v2 is strict."

### LOW-4 — 4C queries `observations.source` but also doesn't verify the legacy-observations schema has a source column
**File:line:** `scripts/rebuild_calibration_pairs_v2.py:147, 151`.

```sql
SELECT city, target_date, high_temp, unit, authority, source
FROM observations
WHERE ... ORDER BY source DESC
```

Legacy `observations` DOES have a `source` column (per earlier scout inventory §1 / §3) — confirmed by my phase-3 review context. So this SELECT is fine. **But** I'm flagging it LOW because the inconsistency (`observations.source` exists but `ensemble_snapshots_v2.source` does not) is exactly the kind of silent schema divergence that produces CRITICAL-1. Future cross-table queries should always grep PRAGMA first.

Not actionable.

### LOW-5 — 4D refit `except Exception` at line 249 swallows too broadly
**File:line:** `scripts/refit_platt_v2.py:247-252`.

```python
try:
    _fit_bucket(conn, cluster, season, data_version, dry_run=dry_run, stats=stats)
except Exception as e:
    print(f"ERR {bucket_key}: {e}")
    stats.buckets_failed += 1
    failed_buckets.append(bucket_key)
```

This will catch `KeyboardInterrupt`? No — Python 3's `Exception` does not include `KeyboardInterrupt` (that's `BaseException`). OK.

But it WILL catch `OperationalError`, `RuntimeError`, `ValueError`, `LinAlgError` indiscriminately. A Platt numerical failure looks identical to a "connection dropped" failure. Operator-facing debug is harder.

**Fix (exec-carol, optional):** narrow the except to expected Platt-fit failures: `except (np.linalg.LinAlgError, ValueError, RuntimeError)`. Let `sqlite3.OperationalError` propagate so DB schema mismatches fail loud (would have caught CRITICAL-1's analog).

Flag LOW not MODERATE because the SAVEPOINT rollback still fires via the outer `except` on line 265, so data correctness is preserved. Only operator-debug clarity degrades.

### LOW-6 — `deactivate_model_v2` rowcount relies on `cursor.rowcount` for DELETE
**File:line:** `src/calibration/store.py:416-420`.

`conn.execute("DELETE FROM ... WHERE model_key = ?", (...))` — `.rowcount` on DELETE returns the deleted count for the last statement. SQLite supports this correctly. But the return value is returned to 4D as `deactivated` (line 175 of refit), summed into `stats.deactivated_rows`. If the DELETE matches 0 rows (no prior model), returns 0. OK, no fix.

### INFO-3 — 4C dry-run + city filter shows estimated rowcount per unit class
**File:line:** `scripts/rebuild_calibration_pairs_v2.py:414-435`.

Nice operator feature. No action.

### INFO-4 — Field-name contract between 4B ingest writer and 4C rebuild reader
The 4B ingest emits: `city`, `target_date`, `temperature_metric`, `physical_quantity`, `observation_field`, `issue_time`, `available_at`, `lead_hours`, `members_json`, `model_version`, `data_version`, `training_allowed`, `causality_status`, `boundary_ambiguous`, `ambiguous_member_count`, `manifest_hash`, `provenance_json`, `members_unit`.

4C reads: `snapshot_id`, `city`, `target_date`, `issue_time`, `lead_hours`, `available_at`, `members_json`, `data_version`, ~~`source`~~.

Mismatch is **just** `source` (CRITICAL-1). Everything else aligns correctly. The `snapshot_id` is an auto-populated PK from 4B's INSERT (not in its VALUES list, correctly). INV-14 metric identity fields are not in the 4C SELECT but are re-derived from `HIGH_LOCALDAY_MAX` at write time — correct separation.

After CRITICAL-1 fix, the contract is clean.

## Pre-mortem (updated, 2-week silent failure hunt)

My 4A pre-mortem predicted Kelvin drift → antibody landed (validate_members_unit + integration test). My 4B round-1 surfaced the test-design gap → antibody partially landed for 4B (TestIngestJsonFileIntegration) but NOT for 4C/4D.

**Updated pre-mortem for 4C/4D + Phase 4.5+:** in 2 weeks, Phase 4.5 extractor produces the expected `tigge_ecmwf_ens_mx2t6_localday_max/` JSON. 4B ingest populates `ensemble_snapshots_v2`. Operator runs `python scripts/rebuild_calibration_pairs_v2.py --no-dry-run --force`. It immediately fails with `OperationalError: no such column: source`. Operator assumes schema migration is incomplete, re-runs `apply_v2_schema`, same error. Spends half a day before noticing the bug is in the SELECT, not the schema.

The antibody: the integration test in MAJOR-2 that calls `rebuild_v2` end-to-end would have caught this at review time (today), not at production-run time (2 weeks from now). **The CRITICAL-1 bug is itself the proof of MAJOR-2 being a real risk, not theoretical.**

**Second-order pre-mortem:** even after CRITICAL-1 fix, the MAJOR-3 refit-parity question remains: do 4D Platt fits numerically agree with legacy refit? Phase 4E parity_diff is the answer, but if parity fails after real-data runs, someone (exec-bob/exec-carol/exec-dan) will be chasing "is the bug in the extractor, the ingest, the rebuild, the refit, or the parity script?" with no isolated antibody per stage. Adding testeng-emma's MAJOR-3 test now isolates that.

## Dispatch

**ITERATE.** Route directly to exec-carol (she owns 4C+4D).

- **exec-carol (CRITICAL-1, ~3 lines):** remove `source` from SELECT on rebuild_calibration_pairs_v2.py:131 and line 184 + drop `source=source` from line 261 (or keep but pass `source=""`). Empty source still triggers INV-15 `_resolve_training_allowed` check on `data_version` prefix.
- **exec-carol + testeng-emma (MAJOR-2, ~40 lines):** add `TestRebuildV2PipelineIntegration` to `tests/test_phase4_rebuild.py` that populates `ensemble_snapshots_v2` + `observations`, calls `rebuild_v2(dry_run=False, force=True)`, asserts expected `calibration_pairs_v2` rows written. Same antibody pattern as exec-bob's 4B `TestIngestJsonFileIntegration`.
- **testeng-emma (MAJOR-3, ~25 lines):** add legacy-vs-v2 Platt parity test to `test_phase4_platt_v2.py` on synthetic identical input. Assert |ΔA| + |ΔB| + |ΔC| < 1e-5.
- **exec-carol (MODERATE-8, ~3 lines):** augment `deactivate_model_v2` docstring with audit-trail note.
- **exec-carol (MODERATE-9, ~2 lines):** wire `MIN_DECISION_GROUPS` to `calibration_maturity_thresholds()`.
- **exec-carol (MODERATE-10, ~2 lines):** document strict hard-failure policy in `rebuild_v2` docstring.
- **Backlog (team-lead):** LOW-4/-5/-6, INFO-3/-4.

## Re-verification plan

After exec-carol returns, I will:
1. Re-grep for `SELECT ... source FROM ensemble_snapshots_v2` — must be zero matches.
2. Re-run my `_fetch_eligible_snapshots_v2` smoke test (the one that just failed) — must return 0 rows without raising.
3. Run `tests/test_phase4_platt_v2.py + tests/test_phase4_rebuild.py -v` — must still show 19 passed + new integration test(s).
4. Run full 4A+4B+4C+4D battery + Phase 0-3 regression — must show zero regressions.

## Big-picture paragraph

Phase 4C+4D are structurally good: SAVEPOINT rollback works, helpers don't pre-commit (no Phase 2 `store_artifact` trap), INV-14/15 gates are respected, metric isolation via `temperature_metric='high'` SELECT predicate is correct, dry-run + force double-gate is consistent with 4B pattern. The implementation reads like thoughtful code. What's concerning is that CRITICAL-1 — a trivially-empirically-detectable SELECT-column bug — passed 19 green tests because none of them exercised the actual rebuild pipeline. **This is exactly MAJOR-1 from my 4B round-1, repeated one phase later.** exec-bob fixed it for 4B; exec-carol needs the same pattern for 4C. Once MAJOR-2's integration test lands, CRITICAL-1 becomes impossible to re-introduce silently, which is the structural antibody the four constraints demand. Phase 4E parity_diff.md will be the definitive answer on whether 4D refits agree with legacy — but MAJOR-3 recommends adding an earlier, synthetic-input parity test so we don't first discover numerical divergence in Gate C.

---

## Round 2 — re-verification (all fixes landed)

**Final verdict: PASS.** Phase 4C+4D ready for commit.

### Fix verification (disk + grep + empirical reproducer + pytest)

| Finding | Status | Evidence |
|---|---|---|
| CRITICAL-1: phantom `source` column in SELECT | RESOLVED | `scripts/rebuild_calibration_pairs_v2.py:131` SELECT no longer references `source`. Line 184 `source = ""  # ensemble_snapshots_v2 has no source column; INV-15 gates on data_version prefix`. Empirical reproducer (the one that raised `OperationalError` in round 1) now returns `rows: 1` — SELECT executes cleanly. |
| MAJOR-2: pipeline integration test missing | RESOLVED | `tests/test_phase4_rebuild.py:295` `TestRebuildV2PipelineIntegration` added. Line 357 `test_rebuild_v2_writes_high_track_identity_fields` imports `rebuild_v2` at line 368, calls `rebuild_v2(conn, dry_run=False, force=True, n_mc=200, rng=rng)` at line 376, asserts non-zero pairs + correct identity fields (lines 379-392). Exercises the full `_fetch_eligible_snapshots_v2` SQL path — any phantom-column regression would fail immediately. |
| MAJOR-3: synthetic Platt parity test | RESOLVED (testeng-emma) | `tests/test_phase4_platt_v2.py:285` `TestFitBucketNumericalParity`. Line 306 test. `n_bootstrap=0` at lines 326/337 (deterministic point fit — cleaner than my sketched seed-coupling approach). Tolerance `1e-9` at lines 341/344. Improvement over my proposed fix. |
| MODERATE-8: `deactivate_model_v2` docstring audit note | RESOLVED | `src/calibration/store.py:405-412` — added: "Deletion (not soft-deactivation) is required because UNIQUE(model_key) means the old row must be removed before the new INSERT can succeed with the same key. Audit trail lives in git commit history and the new row's fitted_at timestamp — no separate soft-delete log is needed for this internal pipeline operation." |
| MODERATE-9: `MIN_DECISION_GROUPS` wired to spec | RESOLVED | `scripts/refit_platt_v2.py` imports `calibration_maturity_thresholds` and destructures `_, _, MIN_DECISION_GROUPS = calibration_maturity_thresholds()  # level3 = refit threshold`. Silent spec drift now impossible. |
| MODERATE-10: strict hard-failure policy documented | RESOLVED | exec-carol confirmed 6-line comment block added to `rebuild_v2` before `hard_failures` check. |

### Final test battery (I ran these myself)

```
tests/test_phase4_rebuild.py + test_phase4_platt_v2.py -v → 21 passed in 1.21s
Full Phase 4 + Phase 0-3 regression battery                → 107 passed, 7 subtests passed in 1.57s
```

The 21 includes 7 INV15SourceWhitelistGate + 5 R-M identity-field tests + 1 new `TestRebuildV2PipelineIntegration` + 7 Platt family isolation + 1 new `TestFitBucketNumericalParity`. Zero regression on Phase 0-3.

### Empirical CRITICAL-1 reproducer (before vs after)

Round 1:
```
$ python3 -c "... _fetch_eligible_snapshots_v2(conn, None)"
ERROR: OperationalError no such column: source
```

Round 2 (same script, same fixture):
```
$ python3 -c "... _fetch_eligible_snapshots_v2(conn, None)"
FIX VERIFIED: SELECT executed, rows returned: 1
```

The bug is structurally eliminated, and the integration test in MAJOR-2 now prevents regression.

### Deferred (carried forward intentionally)

- LOW-4/LOW-5/LOW-6 (narrow except clauses, observations.source cosmetic, rowcount semantics) → backlog.
- INFO-3/INFO-4 → no action.

### Pattern observation

This is the second time exec-bob/exec-carol landed a Phase N implementation with solid structural code but a test suite that encoded R-invariants at the writer-helper layer instead of the pipeline layer, letting a real bug slip through. The antibody fix (integration test calling the real entry-point function) is now applied in BOTH 4B and 4C/4D. **Proposal for future phases:** testeng-emma should adopt "integration test at the pipeline entry point" as a standing R-invariant pattern whenever the R-letter asserts "the `*_vN.py` script produces rows with property X." Writer-level tests are fine as units but must be paired with at least one entry-point-level integration test.

Phase 4C+4D PASS. Ready for commit.
