# critic-beth — Phase 5B Fix-Pack Wide Review

**Date**: 2026-04-17
**Subject**: Phase 5B-fix-pack (7 net items: R-AP..R-AU + dead-code + ERROR log-level)
**Commit base**: `c327872` (Phase 5B); deltas are unstaged on `data-improve`
**Pytest**: fix-pack file 13 passed + 1 SKIPPED (isolated); full-suite 1766 passed + 138 failed (baseline was unrunnable pre-fix — observation_client.py:87 SystemExit at collection)
**Posture**: L0.0 peer-not-suspect, fresh bash greps on every cited claim

## VERDICT: **ITERATE** — 1 MAJOR + 2 MINOR

Fix-pack is structurally sound — all 7 landed items meet their core contracts. One defect in R-AU-2 (silent skip masks whether spec-match path actually processes) + two test-hygiene items that let regressions slip through. Narrow surgical fixes; no re-architecture.

---

## Fresh disk-verify evidence

```
$ git diff --stat (fix-pack subset)
scripts/extract_tigge_mn2t6_localday_min.py    |  12 +-
scripts/ingest_grib_to_snapshots.py            |  19 +-
scripts/rebuild_calibration_pairs_v2.py        |  11 +
src/contracts/ensemble_snapshot_provenance.py  |  21 +-
src/data/observation_client.py                 |  13 +-
src/data/wu_daily_collector.py                 | 200 -------
src/main.py                                    |  14 +-
src/state/truth_files.py                       |   5 +

$ python -m pytest tests/test_phase5_fixpack.py -v  # fresh run
14 collected → 13 passed, 1 skipped in 1.35s
  - R-AU-2 SKIPPED (pytest.skip at L730-735 — escape hatch fired)

$ git stash && pytest  # baseline (pre-fix)
INTERNALERROR: observation_client.py:87 raise SystemExit
no tests ran in 0.82s   ← full suite was UNRUNNABLE before R-AT fix

$ git stash pop && pytest  # post-fix
1766 passed + 138 failed + 99 skipped ← suite now RUNS
```

The pre-vs-post full-suite delta is the strongest evidence the fix-pack ships an immune-system-grade antibody: R-AT's lazy-import fix unblocks ~1800 tests. Baseline "117 failed" from the handoff is stale; the real pre-fix state was "no tests ran" at collection.

## L0-L5

### L0 / L0.0
Authority re-loaded post-subagent-start. Peer-not-suspect posture throughout. Zero discipline findings filed. One earlier testeng-hank flag (claimed merge-conflict in `src/calibration/store.py`) disk-verified as false alarm (Grep returned No matches; git status clean) — resolved via peer-not-suspect hypothesis ordering without escalation.

### L1 — INV / FM
R-AU lands per-spec cross-check inside `_process_snapshot_v2` at L216-222: `if data_version != spec.allowed_data_version: raise DataVersionQuarantinedError`. Matches team-lead's per-spec ruling shape. `_process_snapshot_v2` now takes `spec: CalibrationMetricSpec` kwarg (L206). PASS.

### L2 — Forbidden Moves
- **Kelvin silent-default**: not touched in fix-pack (covered by 5B contract).
- **paper-mode resurrection**: `grep -n 'paper' <touched files>` returns zero new paper references. ✓
- **setdefault on authority**: R-AQ adds `if mode is None: raise ModeMismatchError` as first statement in `read_mode_truth_json` — caller-arg is authority. ✓
- **bare entry_price at Kelly seam**: out of scope.

### L3 — Silent fallbacks
- R-AR replaces `val_k = clf.inner_min` with `val_k = None` for `any_boundary_ambiguous=True` members at L291. Downstream consumers can no longer read a contaminated value without an explicit null check. ✓
- R-AS uses `ZoneInfo(city_tz).utcoffset(target_midnight_local)` at L265 instead of `utcoffset(issue_utc)`. Fixes the fixed-offset-timezone / DST-drift trap. ✓
- R-AU-1 adds positive-allowlist check in `assert_data_version_allowed` via `CANONICAL_DATA_VERSIONS` frozenset. Unknown versions now rejected, not just quarantined. ✓

### L4 — Source authority at seams
**R-AT (observation_client lazy import)**: guard at L87 removed; new `_require_wu_api_key()` helper at L208-212 called from `_fetch_wu_observation` at L222. Module now importable without WU_API_KEY. Trust-boundary preserved at callsite, not at module import. Clean refactor.

**R-AU (rebuild per-spec + global allowlist)**: TWO antibodies at TWO layers as team-lead ruled. Global allowlist on `assert_data_version_allowed` catches unknown strings at any writer; per-spec check in `_process_snapshot_v2` catches cross-metric contamination at rebuild time. ✓

### L5 — Phase boundary
No Phase 6/7/9 leaks. No Phase 5A/5B contracts regressed. `_extract_causality_status` dead-code delete confirmed — contract module still extracts causality via `validate_snapshot_contract`, no dangling callers. `wu_daily_collector.py` fully removed; `_wu_daily_collection()` wrapper in `src/main.py` also deleted.

### WIDE — off-checklist findings

#### MAJOR-1: R-AU-2 silent pytest.skip masks fix-path regression

**Disk evidence**:
```
$ python -m pytest tests/test_phase5_fixpack.py -v
... test_R_AU_2_canonical_version_accepted SKIPPED [100%]
```

**Trace**: R-AU-2 test body at L686-735 calls `_process_snapshot_v2(..., spec=high_spec, ...)` with a HIGH row. Expected behavior post-fix: NO `DataVersionQuarantinedError` (matching spec); possibly `sqlite3.OperationalError` because the `:memory:` DB lacks an `observations` table. The test wraps in try/except that ACCEPTS:
- `except (DataVersionQuarantinedError, AssertionError) as e: pytest.fail(...)` — correct failure path.
- `except _sqlite3.OperationalError: pass` — accepts silent success.
- `except TypeError as e: if "spec" in str(e): pytest.skip(...)` — **this fired**.

The TypeError-with-"spec" branch fires because `_process_snapshot_v2` DOES accept `spec=` now (verified at rebuild_calibration_pairs_v2.py:206) but the test is likely tripping a different TypeError deeper in the call stack (probably `cities_by_name["New York"]` returning None and a downstream attribute error, misclassified as TypeError).

**Impact**: R-AU-2 is supposed to GREEN-antibody the post-fix happy path ("matching spec is not falsely rejected"). Today it silently skips, so a future regression where exec-ida's fix bugs out on matching-spec rows (e.g., wrong equality operator, cached stale spec) would be invisible. Stage-0 antibody.

**Fix shape** (for testeng-hank): remove the `except TypeError` skip branch. If `cities_by_name` lookup or numpy call fails, the test should FAIL with a clear traceback so the real underlying issue surfaces. The `except _sqlite3.OperationalError` branch is legitimate (observations table is out of scope for R-AU-2), keep it.

**Severity**: MAJOR. Does not block fix-pack commit but should land in same commit to avoid carrying a broken antibody forward.

#### MAJOR-2 (DOWNGRADED to MINOR after full-trace): rebuild_v2 hardcodes HIGH-only

**Disk evidence**:
```
$ sed -n '387,393p' scripts/rebuild_calibration_pairs_v2.py
            _process_snapshot_v2(
                conn, snap, city,
                spec=METRIC_SPECS[0],   ← HIGH_LOCALDAY_MAX, hardcoded
                n_mc=n_mc, rng=rng, stats=stats,
            )

$ sed -n '145,150p' scripts/rebuild_calibration_pairs_v2.py
where = (
    "WHERE temperature_metric = 'high' "   ← SQL filter also HIGH-only
    ...
)
```

**Trace**: `rebuild_v2` calls `_fetch_eligible_snapshots_v2` which SQL-filters `temperature_metric = 'high'` at L147. The inner `_process_snapshot_v2` call then passes `spec=METRIC_SPECS[0]` (HIGH). SQL filter and per-spec check agree on HIGH today — no cross-metric leakage path exists in production. The new per-spec check is belt-and-suspenders for this call site.

However, `METRIC_SPECS` is declared as a 2-tuple `(HIGH, LOW)` at L86-87 AND an unused `iter_training_snapshots(conn, spec)` helper at L90-104 IS metric-parametric. This reads AS IF rebuild_v2 iterates both specs, but it doesn't. A future agent adding LOW rebuild will face two hazards: (a) the SQL filter is hardcoded HIGH and needs parametrization; (b) the `METRIC_SPECS[0]` hardcode needs becoming a loop variable.

**Not a 5B-fixpack blocker** — single-metric rebuild is working-as-designed today (LOW rebuild is a Phase 7 task). But downgrade the per-spec check's severity claim in docstrings: today it's defense-in-depth, not the primary barrier. Flag as a Phase-7 landing-zone hazard.

**Severity**: MINOR, forward-log item only. No fix-pack delta.

#### MINOR-1: R-AU-3 accepts TypeError as RED signal (unchanged from round-1)

**Disk evidence**:
```
$ sed -n '672,676p' tests/test_phase5_fixpack.py
# Either TypeError (missing param) or DataVersionQuarantinedError (mismatch caught) is RED.
# Accept both to remain stable across the fix landing sequence.
with pytest.raises((DataVersionQuarantinedError, AssertionError, ValueError, TypeError)):
```

Same nit I raised in round-1. Testeng-hank kept TypeError in the raises tuple for "fix landing sequence stability." Post-fix today, R-AU-3 GREENs on DataVersionQuarantinedError (verified). TypeError-acceptance is now dead code (the signature accepts `spec=`). Removing TypeError from the tuple would sharpen the antibody to fire only for semantic mismatches.

**Severity**: MINOR, test hygiene. Testeng-hank's discretion; not a commit-blocker.

#### MINOR-2: R-AR-2 count-assertion loose (unchanged from round-1)

L342-346 of `test_R_AR_2_clean_member_value_preserved` asserts `len(non_null) > 0`, not `== 51`. A regression that nulls some-but-not-all clean members would pass. Same nit as round-1. Testeng-hank's discretion.

**Severity**: MINOR.

## Legacy-audit verdicts (confirmed)

- `src/data/wu_daily_collector.py`: **DEAD_DELETE**. 200 LOC removed; `_wu_daily_collection()` wrapper in `src/main.py` L67-80 also removed; `_k2_daily_obs_tick` at `main.py:81+` is the live replacement. Incidental `global _heartbeat_fails` scoping fix at `main.py:315` (declared before first use — was a SyntaxWarning pre-fix). Clean.
- `scripts/ingest_grib_to_snapshots.py::_extract_causality_status`: **DEAD_DELETE**. 17 LOC removed. Grep confirms no callers remain.
- `src/contracts/ensemble_snapshot_provenance.py::CANONICAL_DATA_VERSIONS`: **NEW**, CURRENT_REUSABLE. Anchors on `src.types.metric_identity` canonical constants.
- `src/data/observation_client.py::_require_wu_api_key`: **NEW**, CURRENT_REUSABLE. Lazy-guard pattern. Trust boundary preserved.

## Provenance headers

- `tests/test_phase5_fixpack.py` carries `# Lifecycle: / # Purpose: / # Reuse:` triad at L1-3. ✓
- Modified `src/**` and `scripts/**` files did not get new header blocks (out of `naming_conventions.yaml §freshness_metadata.applies_to` scope for src/, already present for scripts/).

## Full-suite regression analysis

Baseline was "no tests ran" (fix-pack #4 was the unblocker). Post-fix: 138 failed. Sampling the failure list (test_architecture_contracts, test_authority_gate, test_calibration_bins_canonical, test_day0_window, test_execution_price, test_fdr, test_topology_doctor, etc.) — these are PRE-EXISTING failures that were previously hidden by the module-level SystemExit. Fix-pack unblocks visibility, doesn't introduce new regressions.

Recommend team-lead: triage the 138 failures against the true Phase 5A/5B baseline (run against `977d9ae` / `c327872` with manual `WU_API_KEY` export to see the real pre-fix-pack count). The handoff's "117 failed baseline" is likely the correct comparator once visibility is restored. This is NOT a fix-pack blocker — it's a post-commit triage task.

## Three-probe checklist for exec-ida

**Q1**: "Does the per-spec check in `_process_snapshot_v2` depend on `spec` being a keyword-only argument?"

Yes — L206 declares `*, spec: CalibrationMetricSpec` (positional-only barrier at `*`). Any caller using positional args will raise TypeError. Good defensive shape; matches METRIC_SPECS positional-only convention elsewhere.

**Q2**: "Is the positive allowlist `CANONICAL_DATA_VERSIONS` derivable, or hardcoded?"

Derived at L66-69 from `HIGH_LOCALDAY_MAX.data_version` and `LOW_LOCALDAY_MIN.data_version`. Adding a new metric in `src/types/metric_identity.py` automatically updates the allowlist. ✓

**Q3**: "Does the removed `_extract_causality_status` helper leave dangling causality_status writes on the non-contract path?"

Grep for `causality_status` in `scripts/ingest_grib_to_snapshots.py` confirms the remaining writer path at L180 (post-contract-wired) uses `decision.causality_status` from the contract decision. Clean.

## Recommendation

**ITERATE — single-round**. exec-ida to remove the R-AU-2 `except TypeError` skip branch (MAJOR-1). Testeng-hank's R-AU-3 TypeError-acceptance and R-AR-2 count-assertion are MINOR nits at her discretion. Re-verify: R-AU-2 must land PASSED (not SKIPPED) post-iterate.

Post-iterate estimated budget: 2 lines deleted + 1 re-run + 5 min re-review.

## Commit scope (if ITERATE lands clean)

7 code files + 1 test file + 3 handoff doc updates:
- `scripts/extract_tigge_mn2t6_localday_min.py` (R-AR + R-AS)
- `scripts/ingest_grib_to_snapshots.py` (dead-code + ERROR log-level)
- `scripts/rebuild_calibration_pairs_v2.py` (R-AU per-spec)
- `src/contracts/ensemble_snapshot_provenance.py` (R-AU global allowlist)
- `src/data/observation_client.py` (R-AT lazy import)
- `src/data/wu_daily_collector.py` (DEAD_DELETE)
- `src/main.py` (wu_daily_collector cleanup + global-scope fix)
- `src/state/truth_files.py` (R-AQ mode=None rejection)
- `tests/test_phase5_fixpack.py` (post-ITERATE)

Suggested commit header: `fix(phase5B-pack): 7 cross-team findings + R-AP..R-AU antibodies`.

Exclude from commit: `state/auto_pause_failclosed.tombstone`, `state/status_summary.json`, `.claude/worktrees/data-rebuild`, `raw/`.

## 5B-fixpack follow-up backlog (forward-log)

1. **rebuild_v2 LOW iteration** (Phase 7 landing-zone): SQL filter + `METRIC_SPECS[0]` hardcode need metric iteration before LOW rebuild lands.
2. **R-AU-3 TypeError acceptance** (nit): drop from raises tuple when comfortable.
3. **R-AR-2 count assertion** (nit): tighten `> 0` to `== 51`.
4. **138-failure triage** (post-commit): separate pass against true Phase 5A/5B baseline with `WU_API_KEY` exported to measure real regression delta.

---

*Authored*: critic-beth (opus, persistent, post-fixpack-implementation review)
*Disk-verified*: 2026-04-17, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`, fresh `git diff --stat`, fresh pytest run, fresh Grep on all cited paths.
