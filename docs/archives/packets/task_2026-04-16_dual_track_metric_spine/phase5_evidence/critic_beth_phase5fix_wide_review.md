# critic-beth — Phase 5B Fix-Pack Wide Review (final)

**Date**: 2026-04-17
**Subject**: Phase 5B-fix-pack — 9 items (7 fix-pack + 1 test file + 1 bonus Py3.14 fix)
**Commit base**: `c327872` (Phase 5B); deltas unstaged on `data-improve`
**Pytest (fix-pack file)**: 14 passed, 0 failed, 0 skipped
**Full-suite regression**: 1716 passed / 138 failed / 99 skipped (flat vs handoff baseline; R-AT unblocked collection)
**Posture**: L0.0 peer-not-suspect, fresh bash greps on every cited claim
**Supersedes**: `critic_beth_fixpack_wide_review.md` (intermediate ITERATE draft)

## VERDICT: **PASS**

Fix-pack ships structurally sound. All 9 items disk-verified. R-AU two-layer antibody (global allowlist + per-spec cross-check) matches team-lead's ruling exactly. One MINOR forward-log item for Phase 7 (rebuild_v2 metric iteration); does not block commit.

---

## Fresh disk-verify evidence

```
$ python -m pytest tests/test_phase5_fixpack.py -v
14 items collected → 14 passed in 1.40s

$ git diff --stat (fix-pack scope)
scripts/extract_tigge_mn2t6_localday_min.py    |  12 +-
scripts/ingest_grib_to_snapshots.py            |  19 +-
scripts/rebuild_calibration_pairs_v2.py        |  11 +
src/contracts/ensemble_snapshot_provenance.py  |  21 +-
src/data/observation_client.py                 |  13 +-
src/data/wu_daily_collector.py                 | 200 -------
src/main.py                                    |  14 +-
src/state/truth_files.py                       |   5 +

$ grep -n "spec: CalibrationMetricSpec|spec.allowed_data_version|spec=METRIC_SPECS" scripts/rebuild_calibration_pairs_v2.py
206:    spec: CalibrationMetricSpec,
217:    if data_version != spec.allowed_data_version:
220:        f"does not match spec.allowed_data_version={spec.allowed_data_version!r}. "
389:                spec=METRIC_SPECS[0],
```

## L0-L5

### L0 / L0.0
Authority re-loaded twice across session (subagent-start boundary). Peer-not-suspect throughout. Four disk-vs-report disagreements resolved via concurrent-write / memory-lag hypothesis:
- scout-gary Target 6 "already landed" — actually partially landed (global allowlist present, per-spec absent). Refinement negotiated, not discipline.
- testeng-hank `store.py` "merge conflict" flag — fresh Grep confirmed no conflict markers; stale-read artifact. Zero escalation.
- team-lead "14/14 GREEN" dispatch at timestamp T — fresh disk at T+30s showed 12p/1f/1s (exec-ida had reverted the per-spec diff). Stop-the-line to team-lead with three reconciliation options; exec-ida re-landed, I re-verified.
- testeng-hank "no RED-GREEN evidence for R-AP..R-AT" — accurate framing: those are post-hoc regression antibodies against `c327872`, not landing-transition antibodies. R-AU-3 is the only genuine pre-fix RED of this cycle. Accepted.

Zero discipline findings filed. Zero false escalations.

### L1 — INV / FM
R-AU lands per-spec cross-check at `_process_snapshot_v2:217-222`: raises `DataVersionQuarantinedError` when `data_version != spec.allowed_data_version`. Signature gains kwarg-only `spec: CalibrationMetricSpec` at L206. Call site at L389 passes `spec=METRIC_SPECS[0]` consistent with the `temperature_metric='high'` SQL filter at L147. Triad (`data_version + temperature_metric + physical_quantity`) preserved at every v2 seam.

### L2 — Forbidden Moves
- **Kelvin silent-default** — untouched (5B contract).
- **obsolete non-live runtime resurrection** — Grep on touched files returns zero new obsolete non-live runtime refs.
- **setdefault on authority** — R-AQ adds `if mode is None: raise ModeMismatchError` as first statement in `read_mode_truth_json`; caller-arg is authority.
- **bare entry_price at Kelly seam** — out of scope.

### L3 — Silent fallbacks
- R-AR (`extract_tigge_mn2t6_localday_min.py:291`): `val_k = None` replaces `clf.inner_min` for `any_boundary_ambiguous=True` members. Downstream consumers can no longer read contaminated values without explicit null check.
- R-AS (`extract_tigge_mn2t6_localday_min.py:265`): `ZoneInfo(city_tz).utcoffset(target_midnight_local)` replaces `utcoffset(issue_utc)`. DST-boundary drift closed (London 2026-03-29 case verified by R-AS-1 test).
- R-AU-1 (`ensemble_snapshot_provenance.py:66-69, 141-144`): `CANONICAL_DATA_VERSIONS` frozenset derived from `HIGH_LOCALDAY_MAX.data_version` and `LOW_LOCALDAY_MIN.data_version`. `assert_data_version_allowed` converts from quarantine-block-only to positive-allowlist. Unknown versions now rejected everywhere.

### L4 — Source authority at seams
- **R-AT (`observation_client.py:87→208-212`)**: module-level `SystemExit` guard removed; new `_require_wu_api_key()` helper called from `_fetch_wu_observation`. Trust boundary preserved at callsite, not at import. Immune-system-grade antibody: full suite went from "no tests ran" (pre-fix collection crash) to 1716 passed.
- **R-AU two-layer antibody** landed per team-lead ruling option (b): contract-layer global allowlist (structural)+rebuild-layer per-spec cross-check (per-call). Future `tigge_mx2t6_v2` scenario — where HIGH has two canonical versions simultaneously — is covered only by the per-spec check. Defense-in-depth shape is correct.
- **Ingest contract gate intact** (`ingest_grib_to_snapshots.py:164-167`): `validate_snapshot_contract` still called at `ingest_json_file`; rejection now at `logger.error` level (was `warning`). 5B seam not regressed.

### L5 — Phase boundary
No Phase 6/7/9 leaks. No Phase 5A/5B contracts regressed. `_extract_causality_status` (`ingest_grib_to_snapshots.py:107-120`) dead-code delete confirmed — contract module is the sole causality extractor on the hot path. `wu_daily_collector.py` DEAD_DELETE (200 LOC); `_wu_daily_collection()` wrapper in `src/main.py:67-80` also removed; `_k2_daily_obs_tick()` is the live replacement.

**Incidental `global _heartbeat_fails` scoping fix** at `src/main.py:330` — moved `global` declaration before first use (was SyntaxWarning under Py3.14). Accepted as scope-creep per team-lead discipline note; not a fix-pack item.

### WIDE — off-checklist findings

#### MINOR forward-log (not blocking): `rebuild_v2` metric iteration

`rebuild_v2` hardcodes HIGH-only: SQL filter at L147 `WHERE temperature_metric = 'high'` and inner call at L389 `spec=METRIC_SPECS[0]`. Per-spec check at L217-222 is defense-in-depth today (no production leakage path exists). However:
- `METRIC_SPECS` at L86-87 is a 2-tuple `(HIGH, LOW)`.
- `iter_training_snapshots` at L92 is metric-parametric.
- Code reads AS IF both specs iterate, but only HIGH runs.

Phase 7 LOW rebuild needs explicit metric-loop wiring in both the SQL filter and the `_process_snapshot_v2` call site. Filed for Phase-7 landing-zone hazard, not fix-pack scope. Per-spec check semantic is forward-ready — when LOW iteration lands, adding `for spec in METRIC_SPECS: ...` around L380-395 with parametrized SQL suffices.

## Legacy-audit verdicts

- `src/data/wu_daily_collector.py` → **DEAD_DELETE** (clean; no dangling callers; `_k2_daily_obs_tick()` replacement live at `main.py:81+`).
- `scripts/ingest_grib_to_snapshots.py::_extract_causality_status` → **DEAD_DELETE** (17 LOC removed; contract is sole hot-path causality extractor).
- `src/contracts/ensemble_snapshot_provenance.py::CANONICAL_DATA_VERSIONS` → **NEW, CURRENT_REUSABLE** (frozenset derived from MetricIdentity constants; auto-updates on new metric addition).
- `src/data/observation_client.py::_require_wu_api_key` → **NEW, CURRENT_REUSABLE** (lazy-guard pattern; trust boundary preserved).

## Provenance headers

- `tests/test_phase5_fixpack.py` carries `# Lifecycle: / # Purpose: / # Reuse:` triad.
- Modified `src/**` / `scripts/**` files retain existing headers; no new headers required (naming_conventions.yaml applies_to scope does not bind `src/contracts/**`).

## 5B-follow-up backlog (forward-log, unchanged)

1. **Phase 7: rebuild_v2 LOW iteration** — parametrize SQL filter + METRIC_SPECS loop.
2. **Phase 6: evaluator.py:825 co-landing** — MAX-array-as-MIN-input with `Day0Signal.__init__` guard removal (unchanged from prior learnings).
3. **Phase 9: INV-21 / INV-22 coverage** — DT#5 + DT#3 machine-check gap.
4. **Post-commit triage (separate lane)**: 138 pre-existing test failures exposed by R-AT's suite-unblock. Not fix-pack regressions; triage against baseline with `WU_API_KEY` exported.

## Commit recommendation

**PASS — 5B-fixpack READY for stage + commit.**

**Stage these 9 files**:
```
scripts/extract_tigge_mn2t6_localday_min.py       (R-AR + R-AS)
scripts/ingest_grib_to_snapshots.py               (dead-code + ERROR log-level)
scripts/rebuild_calibration_pairs_v2.py           (R-AU per-spec)
src/contracts/ensemble_snapshot_provenance.py     (R-AU global allowlist)
src/data/observation_client.py                    (R-AT lazy import)
src/data/wu_daily_collector.py                    (DEAD_DELETE)
src/main.py                                       (wu_daily cleanup + Py3.14 scoping fix)
src/state/truth_files.py                          (R-AQ mode=None rejection)
tests/test_phase5_fixpack.py                      (14 tests, NEW)
```

**Exclude from commit**: `state/auto_pause_failclosed.tombstone`, `state/status_summary.json`, `.claude/worktrees/data-rebuild` submodule, `raw/`, `README.md`, `docs/to-do-list/*` session-handoff markdown.

**Suggested commit header**: `fix(phase5B-pack): 7 cross-team findings + R-AP..R-AU antibodies`.

**Commit body should include**:
- 14/14 phase5_fixpack GREEN (R-AP..R-AU)
- Full-suite flat vs baseline (138 failed pre-existing, unblocked by R-AT lazy import)
- R-AU two-layer defense per team-lead ruling option (b): contract-layer + rebuild-layer
- `wu_daily_collector.py` + `_extract_causality_status` dead-code removal
- Phase-7 forward-log: `rebuild_v2` LOW iteration (belt-and-suspenders today)

---

*Authored*: critic-beth (opus, persistent, post-ITERATE re-verification)
*Disk-verified*: 2026-04-17, cwd `/Users/leofitz/.openclaw/workspace-venus/zeus`, fresh pytest 14/14 + fresh git diff on `rebuild_calibration_pairs_v2.py` confirming `spec=` landed at L206/L217-222/L389.
*Supersedes*: `critic_beth_fixpack_wide_review.md` (ITERATE draft from pre-revert cycle).
