# Phase 4A — critic-alice verdict

Date: 2026-04-16
Scope: Phase 4A.0 + 4A.1 + 4A.2 + 4A.3 + 4A.4 bundle.
Diff: 5 files, +202/-18. Disk-verified present before review.

## Verdict: **PASS with MODERATE findings**

Phase 4A lands the structural foundation cleanly. R-I/R-J/R-K/R-N/R-P all green, no Phase 0-3 regressions, and the key `MetricIdentity` / `training_allowed` / `members_unit` / quarantine seams are wired. I have zero CRITICAL and zero MAJOR findings. Four MODERATE items and several LOW/INFO items — none of which block commit, but exec-bob/exec-carol should address a subset before 4B/4C start because they will bite there.

## L0-L5 standing checklist

| Check | Result |
|---|---|
| L0 authority still loaded | PASS — re-verified with `grep` on current zeus_current_architecture.md §13-§22 and zeus_dual_track_architecture.md §2/§5/§6/§8. `git status --short` confirms all 5 changed files on disk; no phantom risk on this review. |
| L1 INV/FM scoped compliance | PASS — INV-13 `require_provenance("kelly_mult")` unchanged (Phase 9 scope); INV-14/15/16 respected; INV-17 schema commit ordering structural via v2_schema explicit BEGIN/COMMIT (`v2_schema.py:33`). |
| L2 Forbidden Moves | PASS — no mixing of high/low rows in a Platt fit (separate `model_key` per `temperature_metric`, R-N green); no silent degrade to high track path. |
| L3 NC-03 / NC-08 silent default | 1 MODERATE — see M1 below. |
| L4 source authority preservation | PASS — `source`, `data_version`, `authority`, `snapshot_id` all propagated in the new v2 write path. |
| L5 phase-leak | PASS — Phase 5 low-track code NOT shipped; low identity `LOW_LOCALDAY_MIN` only used as a type-system fixture in tests, not executed in writes. `tigge_mn2t6_local_calendar_day_min_v1` is documented in the ingest stub but no writer exists. |

## Narrow verification

### V1: R-I/R-J/R-K/R-N/R-P pytest
```
WU_API_KEY=dummy python -m pytest \
  tests/test_phase4_foundation.py tests/test_phase4_rebuild.py::TestINV15SourceWhitelistGate \
  tests/test_phase4_parity_gate.py tests/test_phase4_platt_v2.py tests/test_schema_v2_gate_a.py -v

=============== 29 passed, 1 skipped, 7 subtests passed in 0.23s ===============
```
- The 1 skipped test (`test_ingest_grib_to_snapshots_calls_assert_data_version_before_insert`) is correctly skipped — Phase 4B has not started. Skip is intentional.

### V2: Phase 0-3 regression spot
```
WU_API_KEY=dummy python -m pytest tests/test_metric_identity_spine.py tests/test_fdr_family_scope.py \
  tests/test_dt1_commit_ordering.py tests/test_dt4_chain_three_state.py \
  tests/test_phase3_observation_closure.py tests/test_phase3_source_registry_single_truth.py -q

=============== 56 passed in 1.66s ===============
```
Zero regression.

### V3: disk verification (amended compact protocol)
```
$ git status --short
 M scripts/ingest_grib_to_snapshots.py
 M src/calibration/store.py
 M src/contracts/ensemble_snapshot_provenance.py
 M src/state/schema/v2_schema.py
 M tests/test_schema_v2_gate_a.py
?? tests/test_phase4_foundation.py
?? tests/test_phase4_ingest.py
?? tests/test_phase4_parity_gate.py
?? tests/test_phase4_platt_v2.py
?? tests/test_phase4_rebuild.py
```
Not phantom. +202/-18 matches team-lead's reported diff stat.

## Team-lead-requested specific checks

### Q1. Does `_resolve_training_allowed` correctly implement INV-15? Can a source name slip through case-sensitively / whitespace-wise?
Partially — see MODERATE-1 + MODERATE-2 below.

`store.py:110-123` implements a two-signal check:
```python
dv_ok = any(data_version.startswith(s) for s in _TRAINING_ALLOWED_SOURCES) if data_version else False
src_ok = (source in _TRAINING_ALLOWED_SOURCES) if source else True
if not (dv_ok and src_ok):
    return False
return requested
```

Design is sound: if either signal mismatches, fail-closed. The `if source else True` default on line 120 is correct — empty string means the caller did not provide a source, so we fall through to data_version-only check.

**But:** both checks are case-sensitive and whitespace-sensitive. See M1/M2.

### Q2. Does `add_calibration_pair_v2` route metric correctly? Silent legacy fallback?
PASS. `add_calibration_pair_v2` (store.py:126-179) writes to `calibration_pairs_v2` via a parameterized INSERT; no code path would silently route to the legacy `calibration_pairs` table. The legacy `add_calibration_pair` (store.py:63-107) still exists but is structurally disjoint — different function name, different table, no `metric_identity` parameter. Existing callers (`src/execution/harvester.py:810`, `scripts/rebuild_calibration_pairs_canonical.py:331`) still use the legacy function, which is correct for Phase 4A (Phase 4C will migrate the rebuild script).

### Q3. Does `save_platt_model_v2` enforce the correct UNIQUE family?
PASS. `store.py:350-390` builds `model_key` as `{temperature_metric}:{cluster}:{season}:{data_version}:{input_space}` (line 373-375) — matches the v2 schema's `UNIQUE(temperature_metric, cluster, season, data_version, input_space, is_active)`. No `city` or `target_date` pollution. Test `test_model_row_has_no_city_or_target_date_column` green.

One NIT — see INFO-1.

### Q4. `QUARANTINED_DATA_VERSIONS` update — sufficient for peak_window, or missing derivative catch?
PARTIAL. See MODERATE-3. The exact-match set (`provenance.py:75-81`) catches `tigge_mx2t6_local_peak_window_max_v1`, but a hypothetical `tigge_mx2t6_local_peak_window_max_v2` (version bump, same peak-window semantics) would slip through. The prefix set (`provenance.py:91-95`) catches `tigge_step*`, `tigge_param167*`, `tigge_2t_instant*` — none of these match a `tigge_mx2t6_local_peak_window*` prefix. If someone re-runs the old extractor with a bumped `_v2` suffix, the guard does not fire.

### Q5. Schema migration idempotency — running `apply_v2_schema()` twice?
PASS. The new ALTER TABLE block at `v2_schema.py:159-168` wraps each ALTER in try/except and swallows `"duplicate column"` errors case-insensitively. Re-running is safe. Pre-migration DBs without the columns get them added on first call.

Tested empirically: the first `test_ensemble_snapshots_v2_has_members_unit_and_precision` invocation executes `apply_v2_schema(conn)` which creates the table AND runs the ALTER (duplicate on fresh create means the ALTER fails with "duplicate column" and is swallowed). Green.

### Q6. Phase boundary leak?
CLEAN. No Phase 5/6 concern leaked into 4A. `LOW_LOCALDAY_MIN` is referenced only as a type constant; no writer emits it. `causality_status` column exists on `ensemble_snapshots_v2` and `calibration_pairs_v2` but is always `"OK"` in 4A (as the plan specifies). `Day0LowNowcastSignal` is untouched.

## Findings

### MODERATE-1 — `_resolve_training_allowed` is case-sensitive on `source` and `data_version`
**File:line:** `src/calibration/store.py:118, 120`

**Issue:** `data_version.startswith("tigge")` rejects `"TIGGE_mx2t6_..."` and `"Tigge_..."`. Similarly `source in {"tigge", "ecmwf_ens"}` rejects `"Tigge"`. The Zeus codebase uses lowercase tags consistently today (`src/types/metric_identity.py:82,89`), so in-scope callers never mismatch — but the gate is the *refusal point* and needs to be tolerant of operator-provided values (e.g. `source="TIGGE"` from a manual backfill script).

**Why MODERATE (not MAJOR):** no actual in-scope caller passes non-lowercase source/data_version today. But 4B/4C will invoke from more sites, and a silent case mismatch downgrades training_allowed to False with no loud error — which is fail-closed (safe) but observationally indistinguishable from a legitimate non-whitelisted source. Operator debugging a "why is training_allowed=0?" will not find the cause.

**Fix (~4 lines, exec-bob):**
```python
def _resolve_training_allowed(source: str, data_version: str, requested: bool) -> bool:
    source_n = (source or "").strip().lower()
    data_version_n = (data_version or "").strip().lower()
    dv_ok = any(data_version_n.startswith(s) for s in _TRAINING_ALLOWED_SOURCES) if data_version_n else False
    src_ok = (source_n in _TRAINING_ALLOWED_SOURCES) if source_n else True
    if not (dv_ok and src_ok):
        return False
    return requested
```
If the fix is accepted, add a case-insensitivity test to R-J.

### MODERATE-2 — whitespace tolerance in `_resolve_training_allowed`
**File:line:** same as M1.

`data_version=" tigge_..."` (leading space) and `data_version="tigge_ "` (trailing space) both fail the startswith check today. Fix is folded into M1's `.strip().lower()`.

### MODERATE-3 — `QUARANTINED_DATA_VERSIONS` peak_window entry is too narrow
**File:line:** `src/contracts/ensemble_snapshot_provenance.py:80`

Only `tigge_mx2t6_local_peak_window_max_v1` is listed. A version bump (`_v2`) or a stepType variant (`tigge_mx2t6_local_peak_window_max_v1_variant`) would pass. The old peak-window physical quantity is *semantically wrong* regardless of version suffix — so the safe fix is a prefix match.

**Fix (exec-carol, 1 line):** add `"tigge_mx2t6_local_peak_window"` to `QUARANTINED_DATA_VERSION_PREFIXES` (provenance.py:91-95). Keep the exact-match entry for clarity. After this, `is_quarantined("tigge_mx2t6_local_peak_window_max_v2")` correctly returns True.

Add a test to R-P: `assert_data_version_allowed("tigge_mx2t6_local_peak_window_max_v2")` raises.

### MODERATE-4 — ensemble_snapshots_v2 `members_unit` DEFAULT 'degC' is a silent-default trap
**File:line:** `src/state/schema/v2_schema.py:161`

Phase 2 lesson (my verdict, MODERATE): v2 schema's `training_allowed DEFAULT 1` is a trap because the writer is expected to pass explicit values; `DEFAULT 1` silently whitelists anything not passed. Same pattern here: `members_unit TEXT NOT NULL DEFAULT 'degC'` means a writer that forgets to pass `members_unit` gets it silently stamped as degC — even if the GRIB parse produced Kelvin values and the unit conversion was skipped.

The pre-mortem (plan §9) predicted exactly this: Kelvin stored as if it were degC, silent +273 bias. Having a DEFAULT defeats the structural antibody.

**But I'm flagging this MODERATE not CRITICAL because:** the new `validate_members_unit` function (`provenance.py:142-160`) IS the antibody, and it correctly rejects None/empty/Kelvin. As long as the 4B ingest calls `validate_members_unit` before every INSERT, the DEFAULT never gets used on a wrong-unit row. But the DEFAULT means a direct raw SQL INSERT that forgets `members_unit` silently lands as degC — the antibody is caller-responsibility, not structural.

**Options for exec-carol:**
- (a) Accept the DEFAULT as-is, rely on `validate_members_unit` at every writer seam. Document the trap clearly so future writers do not bypass it.
- (b) Drop the DEFAULT and make `members_unit` required on INSERT. More invasive because the test fixture at `test_phase4_foundation.py:114-130` (`test_members_unit_insert_without_value_uses_default_degc`) explicitly tests the default. Dropping the default would break that test — which is fine, it encodes the wrong semantics.

**Recommend (a) for this phase, but backlog (b) as a Phase 4B-entry hardening ticket.** The test at `test_phase4_foundation.py:114-130` asserts the default works — so the default is doctrinal today. Fine to leave; just document the antibody responsibility.

### LOW-1 — `validate_members_unit` not wired into any writer yet
**File:line:** `src/contracts/ensemble_snapshot_provenance.py:142-160`

The helper exists and tests appear to cover it (via R-K / R-O in test_phase4_foundation.py), but no current writer calls it. This is correct for 4A (4B implements the GRIB writer). Flag as tracking item for 4B critic review: when exec-bob writes `ingest_grib_to_snapshots.py`, `validate_members_unit` must be called on every row before INSERT, otherwise MODERATE-4's DEFAULT trap activates.

### LOW-2 — ingest stub docstring mentions `tigge_mn2t6_local_calendar_day_min_v1` as "Phase 5"
**File:line:** `scripts/ingest_grib_to_snapshots.py:20`

This is correct per the plan, but exposes a Phase boundary risk: when Phase 4B implements the ingestor, exec-bob may read the stub docstring and accidentally include low-track handling. The comment is helpful for architectural clarity but the 4B brief should explicitly say "HIGH ONLY — do not implement low track writing in this phase."

Not actionable for 4A; forward-tracking note for 4B.

### LOW-3 — `save_platt_model_v2` uses `INSERT` not `INSERT OR REPLACE`
**File:line:** `src/calibration/store.py:377`

Legacy `save_platt_model` (line 339) uses `INSERT OR REPLACE` so refits overwrite. `save_platt_model_v2` uses plain `INSERT`. A refit will trip the UNIQUE constraint. Test `test_duplicate_high_model_raises_integrity_error` confirms this is intentional (asserts IntegrityError). But Phase 4D's `refit_platt_v2.py` will need a different approach: either `INSERT OR REPLACE` or explicit `UPDATE is_active = 0` → `INSERT`.

**Nothing to fix in 4A.** Flag for 4D design: decide the refit semantics. Recommend `INSERT OR REPLACE` on `model_key` to match legacy ergonomics, or document explicit "deactivate-then-insert" pattern.

### INFO-1 — `save_platt_model_v2` missing `bucket_key` column population
**File:line:** `src/calibration/store.py:377-390`

The v2 schema has a `bucket_key TEXT` column (v2_schema.py:229), likely carried over for legacy compat. The new `save_platt_model_v2` does not populate it. Fine for now (column is nullable). If it's dead, drop it at Phase 4D entry; if it's meaningful, populate it. Not blocking.

### INFO-2 — docstring inconsistency in `ingest_grib_to_snapshots.py`
**File:line:** Lines 28-29 say "`tigge_mx2t6_local_peak_window_max_v1` (NOW QUARANTINED — peak-window semantics ≠ local-calendar-day; superseded by calendar_day_max_v1)" — correct. But line 30-31 then list the prefix forbidden families without noting that peak_window is now in `QUARANTINED_DATA_VERSIONS` (exact match), not the prefix set. Minor clarity thing.

## Dispatch

**Not blocking commit.** But for 4B/4C entry, recommend exec-bob + exec-carol address M1, M2, M3 (~10 lines total across 2 files) before their next sub-phase. These are all fail-closed tightenings that compound if deferred.

- **exec-bob (optional, ~5 lines):** MODERATE-1 + MODERATE-2 — add `.strip().lower()` normalization to `_resolve_training_allowed` plus one case-insensitive R-J test.
- **exec-carol (optional, ~2 lines):** MODERATE-3 — add `"tigge_mx2t6_local_peak_window"` to `QUARANTINED_DATA_VERSION_PREFIXES` plus one R-P prefix-catch test.
- **Backlog (team-lead):** LOW-3 (Phase 4D refit semantics), INFO-1 (bucket_key dead-or-live), INFO-2 (stub docstring polish).

## Big-picture paragraph

Phase 4A delivers exactly what the plan promised: the foundation commits that let 4B/4C/4D be pure implementation without further contract changes. The shape of the work is refreshingly boring — no architectural surprises, no hidden coupling, no phase-leaks. The `_resolve_training_allowed` two-signal design is the right antibody structure (fail-closed on either mismatch), and the `validate_members_unit` + `assert_data_version_allowed` pair gives future ingest writers two independent structural gates to trip before silent-wrong data lands. The four-constraints pattern I'd call out: Phase 4A moves the INV-15 check from *caller-discipline* (every caller remembers to set training_allowed=False) to *writer-discipline* (the writer forces it regardless of caller intent). That's a concrete antibody — a structural change that makes the whole category of silent fallback contamination impossible. Exactly the move §2 of the four constraints demands. Commit, then dispatch 4B with the three MODERATE fixes folded in as Phase 4B.0 (1 commit) or at the start of 4B itself.

## Final test battery summary

| Suite | Result |
|---|---|
| test_phase4_foundation.py + test_phase4_rebuild.py::TestINV15 + test_phase4_parity_gate.py + test_phase4_platt_v2.py + test_schema_v2_gate_a.py | **29 passed, 1 skipped** (skip is intentional — 4B not started) |
| test_metric_identity_spine.py + test_fdr_family_scope.py + test_dt1_commit_ordering.py + test_dt4_chain_three_state.py + test_phase3_observation_closure.py + test_phase3_source_registry_single_truth.py | **56 passed** (Phase 0-3 regression clean) |

Phase 4A PASS. Ready for commit.
