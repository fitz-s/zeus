# Phase 4B — critic-alice verdict

Date: 2026-04-16
Scope: `scripts/ingest_grib_to_snapshots.py` (stub → 355 LOC) + Phase 4A fold-in fixes in `src/calibration/store.py` and `src/contracts/ensemble_snapshot_provenance.py`.
exec-carol's 4D work (in-progress) is explicitly out of scope for this review.

## Final verdict (post-iterate): **PASS**

## Round 1 verdict: ITERATE (historical — preserved below)

## Round 1 summary: one MAJOR test-design gap + one MAJOR architectural gap; LOWs deferrable

Phase 4B's structural code is well-written. R-L and R-O tests are green, INV-15/DT#1 plumbing is present, M1/M2/M3 MODERATEs from my 4A verdict are all folded (I disk-verified). However, two MAJOR findings emerged that I could not have flagged in 4A:

- MAJOR-1: R-L tests bypass the actual `ingest_json_file` function — they insert via a direct SQL fixture. So the "ingest writes all fields" assertion is structurally *not proved against the ingest code* today.
- MAJOR-2: The ingest script's upstream dependency (extracted JSON subdirectory at `51 source data/raw/tigge_ecmwf_ens_mx2t6_localday_max/`) does **not exist on disk**. Phase 4B will run successfully on a shell prompt but process zero files.

Neither is a code bug in what exec-bob wrote — they are gaps in what Phase 4B can actually accomplish today. Team-lead should decide whether to close these before 4B commits or defer and document.

## L0 authority + disk verification

- L0 authority still loaded. Re-grep on `zeus_current_architecture.md §13–§22` + `zeus_dual_track_architecture.md §2/§5/§6/§8`: present. Both TIGGE plans (`TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md`, `TIGGE_MN2T6_LOCALDAY_MIN_REMEDIATION_PLAN.md`) in memory from Phase 4 opener.
- Disk verified:
  - `git log --oneline -1` → `dcf6ca3 Phase 4A: foundation commits …` (4A committed).
  - `git status --short` shows `M scripts/ingest_grib_to_snapshots.py` (4B diff present).
  - `wc -l scripts/ingest_grib_to_snapshots.py` → 355 (matches team-lead's report).
  - `M src/calibration/store.py` + `M src/contracts/ensemble_snapshot_provenance.py` — the fold-in fixes landed.

## Tests run (my own, not relying on exec-bob's self-report)

```
tests/test_phase4_ingest.py -v              → 14 passed, 0 failed, 0 skipped
Full Phase 4 + 0-3 regression battery        → 96 passed, 7 subtests passed
```

All R-I, R-J, R-K, R-L, R-N, R-O, R-P green on the paths they actually test.

## 4A MODERATE fold-in verification

| From 4A verdict | Status today | Evidence |
|---|---|---|
| MODERATE-1/2: `_resolve_training_allowed` case/whitespace hardening | RESOLVED | `store.py:118-119` — `(data_version or "").strip().lower()` and `(source or "").strip().lower()`. |
| MODERATE-3: `QUARANTINED_DATA_VERSION_PREFIXES` peak_window prefix | RESOLVED | `provenance.py:95` — `"tigge_mx2t6_local_peak_window"` is in the tuple. Team-lead's intel was stale. |
| MODERATE-4: `members_unit DEFAULT 'degC'` silent trap | DEFERRED per my recommendation | Still in v2_schema.py:161. `validate_members_unit()` helper is the live antibody — see M1 below about test coverage. |

## L1-L5 wide pass

| Check | Result |
|---|---|
| L1 INV/FM scoped | PASS — INV-14 (metric identity spine), INV-15 (fallback source gate), INV-17 (DT#1 commit-before-export via `commit_then_export` wrapper), NC-12 (data_version quarantine refusal) all structurally honored. |
| L2 Forbidden Moves | PASS — no high/low mix in Platt fit, no JSON-before-commit pattern, no legacy `ensemble_snapshots` write. |
| L3 NC-03 / NC-08 silent defaults | 1 MODERATE — see MODERATE-5. |
| L4 source authority preservation | PASS — `manifest_hash`, `provenance_json`, `data_version`, `source` (implicit via data_version), `issue_time`, `available_at` all populated per-row. |
| L5 phase-leak | MIXED — `_TRACK_CONFIGS` contains `mn2t6_low` as a *configured* track. Plan §4B says "high only"; the low track entry is pre-wired but accessible via `--track mn2t6_low`. See MODERATE-6. |

## Team-lead-requested architectural checks

### Q1. GRIB→JSON extraction boundary: is "ingest_grib_to_snapshots" reading JSON correct?
**PASS on design, MAJOR on dependency.** The TIGGE dual-track plan §7 explicitly says `raw/tigge_ecmwf_ens_mx2t6_localday_max/` is the extractor's output path. Having a separate extraction script produce JSON and the ingest script consume JSON is correct separation-of-concerns — the extractor deals with GRIB parsing (eccodes, manifold geometry, boundary classification), the ingest deals with DB writes. This is good architecture.

**However**: the filename `ingest_grib_to_snapshots.py` is misleading. It does not touch GRIB. A future reader will assume it calls eccodes or pygrib. The docstring is clear, but the filename is not. Flag as INFO-1.

**MAJOR-2 below covers the dependency gap** (the JSON files do not exist yet).

### Q2. `commit_then_export` usage: per-file vs batch?
**Per-file, acceptable but sub-optimal.** `commit_then_export(conn, db_op=_db_op)` is called once per JSON file (line 235) inside `ingest_json_file`. For 420 files × (assume ~50 cities × 7 lead days) = ~147,000 INSERTs, this is 147,000 commits. SQLite with WAL handles this fine (main.py sets `journal_mode=WAL` at line 334), but the wall-clock cost is 10-30× higher than batch commits.

**Not blocking.** DT#1 correctness (commit-before-export) is preserved either way. The `json_exports` tuple on `commit_then_export` call is empty at line 235 — so there is no export step for the writer to violate DT#1 on. That is correct; DT#1 is relevant when there IS a paired JSON export, which this writer does not have.

### Q3. Race-safety of the INSERT-before-exists check
**Race-safe via UNIQUE constraint.** `ensemble_snapshots_v2.UNIQUE(city, target_date, temperature_metric, issue_time, data_version)` on v2_schema.py:152 is the authoritative race barrier. The `SELECT 1 ... WHERE ...` at line 176-180 is a fast-path skip, not a safety check. Even if two operators race past the SELECT, `INSERT OR IGNORE` (line 219) silently drops the duplicate.

But see LOW-1 about `--overwrite` interaction with `INSERT OR IGNORE`.

### Q4. Dynamic step horizon pre-wiring for Phase 5
**PARTIALLY PRESENT.** The ingest reads the `lead_day` field from the JSON payload (line 141: `float(payload.get("lead_day", 0)) * 24.0`). The TIGGE remediation plan §4 requires `required_max_step = ceil_to_next_6h(local_day_end_utc - issue_utc)` — but **that computation lives in the extractor, not the ingest**. The ingest just trusts the JSON. For Phase 5 low track, the same pre-wiring works — as long as the extractor emits `lead_day` correctly, the ingest does not need changes.

This is correct architectural division. Flag for Phase 5 critic review: the extractor must be verified independently.

### Q5. R-L GREEN — actual pytest evidence
```
tests/test_phase4_ingest.py::TestIngestGribWritesFullProvenanceFields (10 tests) → PASSED
tests/test_phase4_ingest.py::TestMembersJsonKelvinGuard (4 tests) → PASSED
14 passed in 0.20s
```
**But see MAJOR-1** — the R-L tests use a direct-INSERT fixture, not `ingest_json_file`. So R-L proves the SQL schema is correctly typed, NOT that `ingest_json_file` populates every field.

### Q6. R-O Kelvin guard actually triggers
**GREEN for the helper, MAJOR-1 for the writer.** `validate_members_unit("K")` raises `MembersUnitInvalidError` — confirmed (test_kelvin_unit_string_is_rejected green). But the writer test path does not include a case that feeds `ingest_json_file` a Kelvin `unit="K"` payload and asserts it raises. See MAJOR-1.

### Q7. INV-17 DT#1 ordering
**Structural.** `commit_then_export` at line 235 is the choke point. `canonical_write.py:38-39` does `db_op(); conn.commit()` then runs exports — exports are empty here so the ordering question is moot, but the pattern is correct. No code path writes JSON before commit.

### Q8. NC-12 high+low cross-metric collision
**PASS.** `UNIQUE(city, target_date, temperature_metric, issue_time, data_version)` includes `temperature_metric`, so same (city, target_date, issue_time) can carry both `high` and `low` without collision. The existence-check at line 176-180 also includes `temperature_metric`, so a low-track row will not be confused with a high-track row during the skip-check. Good.

## Findings

### MAJOR-1 — R-L tests verify SQL schema, not the ingest function
**File:line:** `tests/test_phase4_ingest.py:39-91` (`_write_test_snapshot`).

**What's happening:** The R-L test class `TestIngestGribWritesFullProvenanceFields` uses a private helper `_write_test_snapshot` that constructs a fully populated dict and does a direct `conn.execute("INSERT ... VALUES (...)")` — exactly the same INSERT SQL that `ingest_json_file` does, but with values hand-crafted in the fixture. None of the 10 `test_*` methods call `ingest_json_file(conn, path, ...)`.

**Why MAJOR:** The R-L invariant was supposed to prove that *the ingest function* emits all 7 provenance fields. What the tests actually prove is that *when you manually pass all 7 fields into an INSERT, they get stored*. A future regression where `ingest_json_file` silently drops `causality_status` or `manifest_hash` from its SQL parameter dict would NOT fail R-L. The antibody the plan promised is a test fixture, not a structural gate on the writer.

**Why not CRITICAL:** The structural gate exists independently in the v2 schema — `CHECK (causality_status IN ...)` on v2_schema.py:137-143 and `NOT NULL` on `provenance_json`, `temperature_metric`, `data_version` etc. An actual ingest that drops one of these fields would IntegrityError at write time. So the risk is "silent truncation of *nullable* fields" (`manifest_hash` is nullable per v2_schema.py:147; `ambiguous_member_count` has a DEFAULT 0; `training_allowed` has DEFAULT 1). These are exactly the fields MODERATE-4 flagged as silent-default risks — and they are not structurally guarded.

**Fix (exec-bob, ~25 lines):** Add an integration test that writes a realistic payload JSON to a temp file, calls `ingest_json_file(conn, tmppath, metric=HIGH_LOCALDAY_MAX, model_version="ecmwf_ens", overwrite=False)`, then asserts all 7 fields are populated non-trivially in the DB row. Also add a negative test: construct a payload with `unit="K"` (Kelvin), call `ingest_json_file`, assert `MembersUnitInvalidError` raised and no row written.

### MAJOR-2 — Upstream JSON extraction dependency does not exist
**File:line:** `scripts/ingest_grib_to_snapshots.py:45` + `:50-55` (JSON subdirs expected under `51 source data/raw/`).

**What's happening:**
```
$ ls "/Users/leofitz/.openclaw/workspace-venus/51 source data/raw/"
ecmwf_open_ens  solar  tigge_ecmwf_ens_regions_mn2t6  tigge_ecmwf_ens_regions_mx2t6
```
Expected: `tigge_ecmwf_ens_mx2t6_localday_max/` and `tigge_ecmwf_ens_mn2t6_localday_min/` (per TIGGE_LOCAL_CALENDAR_DAY_DUAL_TRACK_PLAN.md §7). Present: `tigge_ecmwf_ens_regions_mx2t6/` (raw GRIB, not extracted JSON).

**Impact:** `ingest_track` at line 254 logs "JSON root not found" and returns `{"error": ..., "written": 0, ...}`. Phase 4B will commit, exec-bob will report "ready for commit", but the pipeline produces zero `ensemble_snapshots_v2` rows on the real data path. **Phase 4C rebuild_calibration_pairs_v2 will subsequently have zero input rows**, and Phase 4E parity_diff.md will have zero comparisons, silently "passing" Gate C.

**Why MAJOR:** This is the silent-wrong pattern from four-constraints #4 (data provenance). Code is structurally correct; the data dependency is missing. Scout's 4B inventory did not flag this — likely because the gap is between the TIGGE plan's description and current on-disk reality.

**Fix options (team-lead decision):**
- (a) Defer: accept 4B as a structural commit, flag dependency for the downstream workflow. Requires someone (presumably exec-bob or a new scout lane) to build `extract_tigge_mx2t6_localday_max.py` before 4C runs meaningfully.
- (b) Block: do not commit 4B until the extractor lands and produces the expected JSON subdir. Prevents an "empty-v2-table-passes-Gate-C" silent failure.
- (c) Add a loud guard: at the top of `ingest_track`, refuse to run (exit code 2) if `subdir.exists()` is False, AND update R-L to include a "zero-file run returns error" test. This converts the silent no-op into a loud failure. Cheapest structural option.

**Recommend (c) + (a).** Add a `--require-files` flag that defaults True and makes zero-file runs fail-loud, so future operators cannot silently get "written: 0" and assume success. Defer the extractor itself to a proper scoped ticket.

### MODERATE-5 — `INSERT OR IGNORE` defeats `--overwrite`
**File:line:** `scripts/ingest_grib_to_snapshots.py:219`.

**What's wrong:** When `--overwrite=True`, the duplicate-skip check at line 175-182 is bypassed. But the subsequent SQL uses `INSERT OR IGNORE` (line 219). If the row already exists via the UNIQUE key, `INSERT OR IGNORE` silently does nothing. So `--overwrite=True` effectively means "don't skip-check" but the existing row is still not overwritten.

**Why MODERATE (not MAJOR):** `--overwrite` is an operator-facing option, not a hot-path. Operators who set it will see `written: N` but DB rows unchanged. Also: function returns `"written"` string (line 236) regardless of whether the INSERT OR IGNORE actually wrote, so `ingest_track`'s `counters["written"]` over-counts.

**Fix (exec-bob, ~5 lines):**
```python
if overwrite:
    sql = "INSERT OR REPLACE INTO ensemble_snapshots_v2 ..."
else:
    sql = "INSERT OR IGNORE INTO ensemble_snapshots_v2 ..."
```
And capture `cursor.rowcount` to distinguish actual write from silent drop; return `"written"` only when rowcount >= 1.

### MODERATE-6 — `mn2t6_low` track pre-wired in `_TRACK_CONFIGS`
**File:line:** `scripts/ingest_grib_to_snapshots.py:47-58`.

Plan §4B explicitly says "high only". The `mn2t6_low` entry in `_TRACK_CONFIGS` is reachable today via `--track mn2t6_low`. If an operator passes that flag, the script would happily (a) find no JSON files in `tigge_ecmwf_ens_mn2t6_localday_min/` (which does not exist), and (b) write low-track rows with no boundary-quarantine handling. Low track has substantially different semantics per TIGGE_MN2T6 §6 (boundary leakage).

**Why MODERATE:** no caller is expected to pass `--track mn2t6_low` today; 4B is high-only. But the track is advertised in `choices=sorted(_TRACK_CONFIGS)` at line 307, so it shows up in `--help`. A future operator could try it without reading the plan.

**Fix (exec-bob, ~3 lines):** Either (a) remove `mn2t6_low` from `_TRACK_CONFIGS` (add back in Phase 5), or (b) add a guard at the top of `ingest_track`: `if track == "mn2t6_low": raise NotImplementedError("Phase 5 scope — low track requires boundary quarantine logic not yet implemented")`.

Recommend (b) — keeps the config visible for architectural clarity but structurally blocks premature use. Pattern from my preread §3.1.

### ~~MODERATE-7~~ — WITHDRAWN (already resolved, disk-verified post-verdict)
Disk-verified after initial verdict: `tests/test_phase4_parity_gate.py:94` already contains `test_peak_window_version_bump_is_still_quarantined` covering `_v2`/`_v3` via prefix loop (lines 108-109), testing both `is_quarantined()` and `assert_data_version_allowed()` branches (line 117). `8 passed in 0.07s`. My review-time grep missed it (searched for an imagined function name rather than topic keyword). testeng-emma has zero work on this phase. Process lesson added to my L0 checklist: grep by topic keyword, not remembered function name.

### LOW-1 — `--overwrite` flag claim in CLI help
**File:line:** `scripts/ingest_grib_to_snapshots.py:320`.

Help text says "Re-ingest rows that already exist". Combined with MODERATE-5 (`INSERT OR IGNORE` defeats overwrite), the help text is misleading. Fix with MODERATE-5 or update the help to "skip existence-check; upstream writes still no-op on duplicate".

### LOW-2 — Filename `ingest_grib_to_snapshots.py` does not ingest GRIB
**File:line:** script name.

The script reads JSON. A more honest name is `ingest_tigge_json_to_snapshots.py` or `ingest_localday_json_to_snapshots.py`. The TIGGE plan §4 uses the name `ingest_grib_to_snapshots.py` so this is external plan alignment — but the disagreement between name and behavior is a minor readability cost. Not actionable in 4B.

### LOW-3 — ambiguous_member_count cast from arbitrary payload
**File:line:** `scripts/ingest_grib_to_snapshots.py:128`.

`int(bp.get("ambiguous_member_count", 0))` will raise `ValueError` if the JSON has a non-numeric value (e.g. `"N/A"`). That would crash the ingest for ONE row, but `ingest_track` at line 285 does not catch exceptions — so one bad file crashes the whole run. Consider wrapping in try/except with a recoverable "parse_error" status.

### INFO-1 — Filename clarity (LOW-2 context)
See LOW-2. Not actionable this phase.

### INFO-2 — `_normalize_unit` accepts only 'C'/'F' but uppercase
**File:line:** line 60-68.

`_UNIT_MAP = {"C": "degC", "F": "degF"}`. If the JSON payload has `"unit": "c"` (lowercase) or `"Celsius"`, `_normalize_unit` raises `ValueError`. This is fail-closed, which is correct. But be aware the 51 source data extractor must emit `"C"` / `"F"` exactly. Document the contract, or tolerate case-insensitive input via `.upper()`.

## Pre-mortem (2-week silent failure hunt)

My Phase 4A pre-mortem was Kelvin drift. The `validate_members_unit` helper is now the antibody for that, but MAJOR-1 observes that the antibody is not tested at the writer seam. **Updated pre-mortem:** in 2 weeks, someone refactors `ingest_json_file` and accidentally removes the `validate_members_unit(members_unit, ...)` call (or moves it above `_normalize_unit` where the value is still raw `"C"`). No test catches it because R-L/R-O tests don't call `ingest_json_file`. 147K rows land with correct `members_unit='degC'` strings BUT the member float values are in Kelvin because the extractor did not convert. Platt fit against observations off by +273. Brier insample fine (same train/test unit); runtime (observation in C/F, forecast in K) diverges by a non-physical amount, but might just look like "Platt is bad" rather than "unit drift".

**Test that would catch this:** construct a JSON payload with `members=[295.5, 296.2, ...]` (Kelvin values) + `unit="C"` (claim degC), call `ingest_json_file`, assert the extractor-side physical-plausibility check catches it. Zeus does not have a physical-plausibility gate on member values today. That is a Phase 4C+ target, not 4B — but R-L should at least assert that implausible values (members > 100 for degC-tagged row) are refused by some writer-side sanity check.

Flag: **Phase 4B does not structurally guard "members_unit is correct *for* the member values stored"**. The unit string is validated; the consistency between string and numbers is not. This is the four-constraints #4 data-provenance failure mode waiting to happen.

## Dispatch

**ITERATE.** Fixes routed to exec-bob + testeng-emma. Re-verify after their return. If team-lead wants to accept as PASS and defer MAJOR-1 / MAJOR-2 to follow-up tickets, that is a scope decision; I am flagging them, not vetoing.

- **exec-bob (~40 lines across ingest + 1 test file):**
  - MAJOR-1: add integration tests that call `ingest_json_file` with a temp payload file (happy path + Kelvin-unit-rejected negative).
  - MAJOR-2 (option c): add `--require-files` flag defaulting True; fail-loud on zero-file runs.
  - MODERATE-5: fix `--overwrite` ↔ `INSERT OR IGNORE` interaction.
  - MODERATE-6: raise NotImplementedError on `--track mn2t6_low`.
- **testeng-emma:** ~~MODERATE-7~~ withdrawn (already resolved at `parity_gate.py:94`). No work.
- **Backlog (team-lead):**
  - LOW-1/-2/-3, INFO-1/-2.
  - Members-value plausibility check (4C+ ticket — surfaced by updated pre-mortem).
  - MAJOR-2: track the GRIB→JSON extractor ticket; without it, Phase 4C has zero input data.

## Big-picture paragraph

Phase 4B shipped a structurally correct writer with correct seams (INV-15 gate live, DT#1 wrapper, Kelvin guard helper). The concerning gap is the one I could not have caught in 4A: the tests that carry the R-L name prove the DDL contract, not the ingest function — so the antibodies that Phase 4A promised ("validate_members_unit at every writer seam", "manifest_hash written by every ingest path") are structurally unenforced against future refactors of the ingest code itself. Combined with the upstream extractor not existing yet, 4B lands a working-but-empty pipeline. This is exactly the four-constraints #2 pattern: design intent (R-L, R-O) encoded at the wrong abstraction layer. A bad refactor 2 weeks from now will not fail any test. The fix is small (integration tests calling `ingest_json_file`) and worth doing before commit — it converts `validate_members_unit` from a helper function into a regression-guarded contract. I am flagging ITERATE but the judgment call is team-lead's: if the GRIB extractor timeline is far away and 4B is "structural commit only", accepting MAJOR-1 as test-debt backlog is reasonable. If 4B is supposed to produce data before 4C starts, MAJOR-2 needs a scope ruling.

---

## Round 2 — re-verification (all fixes landed)

**Final verdict: PASS.** Phase 4B ready for commit.

### Team-lead's MAJOR-2 scope ruling
MAJOR-2 split into (a) loud-fail guard (exec-bob, in 4B) + (b) GRIB→JSON extractor (new Phase 4.5, fresh executor exec-dan). That matches exactly the architectural argument I raised — extractor is a different job deserving its own R-invariants.

### Fix verification (disk + pytest)

| Finding | Status | Evidence |
|---|---|---|
| MAJOR-1: `ingest_json_file` integration test | RESOLVED | `tests/test_phase4_ingest.py:260` `TestIngestJsonFileIntegration` — 2 tests call real `ingest_json_file(conn, path, ...)` with temp JSON. First asserts all 7 provenance fields populated non-trivially; second asserts Kelvin unit in payload raises `MembersUnitInvalidError`. A refactor dropping any of the 7 INSERT params OR removing `validate_members_unit` now fails these. |
| MAJOR-2a: loud-fail on zero-file runs | RESOLVED | `scripts/ingest_grib_to_snapshots.py:250` `require_files=True` default. Line 266 raises when subdir missing; line 274 raises when subdir exists but zero JSON files. `--no-require-files` flag at line 372 for test scenarios. |
| MAJOR-2b: GRIB→JSON extractor | DEFERRED | New Phase 4.5 (team-lead ruling). Out of 4B scope. |
| MODERATE-5: `--overwrite` ↔ `INSERT OR IGNORE` | RESOLVED | Line 216 `insert_verb = "INSERT OR REPLACE" if overwrite else "INSERT OR IGNORE"`. Line 221 interpolates `{insert_verb}` into SQL. |
| MODERATE-6: `mn2t6_low` track leak | RESOLVED | Line 253-256 raises `NotImplementedError("Phase 5 scope — mn2t6_low track requires boundary quarantine logic …")` at top of `ingest_track`. |
| MODERATE-7: peak_window prefix test | WITHDRAWN | Already resolved at `tests/test_phase4_parity_gate.py:94` — my review-time grep missed it. |

### Final test battery

```
tests/test_phase4_ingest.py -v               → 16 passed, 0 failed
Full Phase 4 + Phase 0-3 regression battery  → 100 passed, 7 subtests passed
```

Integration tests (2 new at `test_phase4_ingest.py:260`) exercise the real ingest path with realistic payload (51 members, proper manifest_sha256, valid issue_time, target_date_local, lead_day, etc.). Kelvin test constructs `unit="K"` payload and asserts raise.

### Structural assessment

Phase 4B now has the proper antibody layers:
- DDL layer: v2 schema CHECK constraints + NOT NULL + UNIQUE (Phase 2).
- Writer layer: `validate_members_unit` + `assert_data_version_allowed` helpers (Phase 4A).
- Integration test layer: `ingest_json_file` called end-to-end with real payload (Phase 4B round 2, MAJOR-1 fix).
- Operational guard: zero-file runs fail-loud (Phase 4B round 2, MAJOR-2a fix).

A future refactor that drops the `validate_members_unit` call, omits `manifest_hash` from the INSERT dict, or silently changes the `training_allowed` propagation will now fail `TestIngestJsonFileIntegration`. That is exactly the structural antibody pattern from four-constraints #2.

### Deferred (carried forward intentionally)

- MAJOR-2b (GRIB→JSON extractor) → Phase 4.5, exec-dan.
- Members-value physical plausibility check (members_unit string validated; member float consistency with string NOT validated) → Phase 4.5 scope.
- LOW-1 / LOW-2 / LOW-3 / INFO-1 / INFO-2 → backlog.

Phase 4B PASS. Ready for commit.
