# Phase 5B → 5C Learnings: exec-emma (trust-boundary + contract specialist)

**Written**: 2026-04-17, post Phase 5B commit `c327872`, pre-retirement.
**Scope**: Phase 5A owner (PortfolioState.authority, ModeMismatchError, view layer) + Phase 5B owner (validate_snapshot_contract, METRIC_SPECS refactor, B078 truth-files).

---

## 1. Latent system issues — things I saw but didn't flag

### Trust-boundary weaknesses

**`ingest_grib_to_snapshots.py::ingest_json_file` — silent return string, not loud log.**
When `decision.accepted is False`, the function logs at WARNING level and returns a string like `"contract_rejected: METRIC_MISMATCH"`. The caller (`ingest_track`) increments a counter bucket keyed on the return string but only prints a summary at end-of-run. An operator watching live output will see no loud ERROR-level line. For an accepted ingest this is fine; for a rejected ingest, the rejection is easy to miss in bulk runs. Prefer `logger.error` (not `logger.warning`) on rejection.

**`rebuild_calibration_pairs_v2.py::_build_calibration_pair` — `data_version` sourced from snapshot row, not from `MetricIdentity`.**
At `scripts/rebuild_calibration_pairs_v2.py` the `data_version` written to `calibration_pairs_v2` comes from `row["data_version"]` (the snapshot's self-reported field), not from `spec.allowed_data_version`. If a snapshot row somehow has a mismatched `data_version` that passed an old ingest without the contract gate, rebuild will propagate the wrong version silently. The fix is a post-read assertion: `assert row["data_version"] == spec.allowed_data_version`.

**`choose_portfolio_truth_source` — no mode field cross-check.**
`src/state/portfolio.py::choose_portfolio_truth_source` selects between canonical DB and degraded path but never verifies the truth file's embedded `mode` tag matches the runtime mode. `read_mode_truth_json` does raise `ModeMismatchError` if the file's mode tag disagrees — but only when `mode` is explicitly passed. If the caller passes `mode=None`, the check is silently skipped (`truth_files.py:135`). Any future caller that omits `mode` gets a silent live-vs-obsolete non-live runtime collision risk.

**`annotate_truth_payload` in `status_summary.py` — no `temperature_metric` passed.**
`src/observability/status_summary.py:399` calls `annotate_truth_payload(..., authority="VERIFIED")` without `temperature_metric`. This is a high-track-only file, so the fail-closed in `build_truth_metadata` correctly does NOT downgrade (because the filename isn't in `_LOW_LANE_FILES`). But if a future status summary file for low-track is added and the caller forgets `temperature_metric`, the fail-closed guard will silently save it as UNVERIFIED rather than raising. A loud assert would be better.

**`_extract_causality_status` is now dead code on the main ingest path** (as of CRITICAL-1 wire). It's still defined at `ingest_grib_to_snapshots.py:106`. No caller reaches it on the hot path. Leave it as documentation or delete — but it will mislead a future reader into thinking causality extraction happens via that helper.

### v2 table evolution hazards

**`ensemble_snapshots_v2.temperature_metric` DEFAULT `'high'`.**
The column DDL at `architecture/2026_04_02_architecture_kernel.sql` has `DEFAULT 'high'`. Any INSERT that omits `temperature_metric` silently writes a high-track row. The ingest currently always binds `temperature_metric=metric.temperature_metric` explicitly, so this is safe today. But if a new caller (e.g., a migration script or a test helper) uses positional INSERT without naming the column, it will silently default to high. Recommend adding a CHECK trigger or an assertion in `apply_v2_schema` that fires if `temperature_metric` is missing from any row at schema validation time.

**`calibration_pairs_v2` has no FK to `ensemble_snapshots_v2`.**
Orphaned pairs (snapshot deleted, pairs remain) are possible. Low-risk now (zero-data window), but the rebuild script selects directly from `ensemble_snapshots_v2` and writes to `calibration_pairs_v2` in one pass — if a snapshot is partially written (crash mid-commit), rebuild could produce pairs for a partial snapshot. The SAVEPOINT in rebuild guards the pairs table write, but not the snapshot table itself.

### Calibration family cross-contamination

**Bucket key namespace collision if `temperature_metric` values ever overlap.**
`bucket_key = f"{metric_identity.temperature_metric}:{cluster}:{season}:{data_version}"` — if two metrics somehow share the same `temperature_metric` string (impossible now, but possible if a third track is added without a unique identifier), `deactivate_model_v2` would deactivate the wrong track's rows. The bucket key should include `physical_quantity` as a secondary discriminator, not just `temperature_metric`.

**Platt model lookup in runtime (Phase 5C risk).**
At inference time, the code will call something like `load_platt_model_v2(metric_identity=...)`. If the lookup uses only `temperature_metric` and omits `data_version`, a high-track model could serve a low-track bucket or vice versa if someone manually inserts a row with the wrong version. The fetch WHERE clause must always include all three triad fields.

### `setdefault` pattern weaknesses

The `contract_payload.setdefault("causality", {"status": "OK"})` bridge for Phase 4 payloads creates a silent forward-compatibility gap: new extractors MUST emit a causality field, but if they don't, the ingest will silently accept them with `causality_status="OK"`. The bridge is correct for Phase 4 legacy payloads but should be logged at DEBUG level so operators can audit how many payloads hit the default path.

---

## 2. Cross-phase patterns

The meta-pattern across Phase 5A → 5B is **authority inversion at seam boundaries**:

- 5A: `PortfolioState.authority` moves FROM being a payload-self-reported field TO being stamped by the DB writer at read time — the seam is the DB write path.
- 5B: `validate_snapshot_contract` moves FROM being a test-only invariant TO being a runtime gate — the seam is the ingest writer.
- The inversion is: **trust nothing the payload says about itself; compute authority from the authoritative source (DB schema, MetricIdentity, contract).**

The 5C pattern should follow: `_forecast_reference_for` typed-status fields should be computed from `MetricIdentity` at the point of ensemble consumption, not from whatever string the snapshot emits.

---

## 3. Forward hazards for 5C / Phase 6 / Phase 7

**5C — replay callers and typed-status fields.**
`_forecast_reference_for` currently returns a plain dict or tuple. If 5C adds a typed status enum, any replay caller that pattern-matches on the raw dict will silently see the old shape. Recommend adding a `data_class_version` field to the replay output so callers can assert they're reading the right shape.

**Phase 6 — Day0 split.**
`Day0LowNowcastSignal` needs its own truth-authority contract because the causality law for low-track Day0 is different: `N/A_CAUSAL_DAY_ALREADY_STARTED` is an expected outcome, not an anomaly. A shared Day0 contract that treats this as a rejection would block valid low-track Day0 observations. Write separate contracts for high and low Day0 signals.

**Phase 7 — v1 table transition.**
The v1 `platt_models` table is still present. During metric-aware rebuild cutover, if both v1 and v2 readers are active simultaneously, a calibration consumer that falls back to v1 will serve un-metric-scoped models to low-track positions. Recommend a tombstone on v1 tables (same pattern as `ensure_legacy_state_tombstone`) before any v2 cutover, not after.

---

## 4. Fresh executor inheritance

**Triad invariant**: every row in `ensemble_snapshots_v2`, `calibration_pairs_v2`, `platt_models_v2` must be identified by the full triad: `(data_version, temperature_metric, physical_quantity)`. Never filter on just one field.

**METRIC_SPECS pattern**: `scripts/rebuild_calibration_pairs_v2.py` and `scripts/refit_platt_v2.py` both accept `metric_identity: MetricIdentity = HIGH_LOCALDAY_MAX`. The `METRIC_SPECS` tuple in `rebuild_calibration_pairs_v2.py` allows iterating over both tracks. Any new script that touches v2 tables should follow this pattern — never hardcode `'high'`.

**`_LOW_LANE_FILES` fail-closed**: `src/state/truth_files.py` has `_LOW_LANE_FILES: frozenset[str]`. If a new low-lane truth file is added, it must be added to both `LEGACY_STATE_FILES` AND reflected in `_LOW_LANE_FILES` (currently derived automatically). The derivation logic uses `"platt_models_low" in f or "calibration_pairs_low" in f` — a new file with a different naming pattern would be silently excluded. Consider deriving from a suffix convention instead.

**`mode=None` check gap**: any call to `read_mode_truth_json` without explicit `mode=` bypasses the ModeMismatchError guard. Phase 5C callers must always pass `mode=get_mode()` explicitly.

---

## 5. Tooling observations

**Smoke-testing contract rejections without polluting DB**: use `validate_snapshot_contract(payload)` directly in a Python REPL with a crafted payload dict — no DB connection needed. This is faster than writing a temp JSON file and running ingest. Good for debugging `METRIC_MISMATCH` or `MISSING_CAUSALITY_FIELD` issues.

**v2 table layout introspection**: `SELECT sql FROM sqlite_master WHERE type='table' AND name='ensemble_snapshots_v2'` shows the full DDL including DEFAULT values and CHECK constraints. Use this as the first step before writing any migration script.

**Pytest conftest shape**: Phase 5B tests use `tmp_path` fixture + inline DB creation (no shared conftest fixture for the v2 schema). This means each test class independently calls `init_schema` + `apply_v2_schema`. If the v2 schema changes, tests fail at DB creation, not at assertion — which is the correct failure mode but can be confusing to read.

**The `ingest_track` counters** (`written`, `skipped_exists`, `parse_error`, `other`) don't have a `contract_rejected` bucket — the return string `"contract_rejected: {reason}"` falls into `other`. A future operator looking at run summaries won't see a breakdown of rejection reasons. Add `contract_rejected` as an explicit counter key in `ingest_track`.
