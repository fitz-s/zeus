# Phase 4 Architect Pre-read — critic-alice

Date: 2026-04-16
Role: architect (not critic) — shaping Phase 4 entry before exec-bob/exec-carol touch code.

## Executive summary

Phase 4 implements the GRIB→`ensemble_snapshots_v2`→`calibration_pairs_v2`→`platt_models_v2` pipeline for the HIGH track only, and it is the **first Phase in this packet that materially writes to the v2 world tables**. Phase 2 created the schema at 0 rows. Every hazard in Phase 4 reduces to one pattern: the v2 schema is metric-aware but *every predecessor writer is metric-blind*, so the first real write is the moment every stale high-only mental model collides with dual-track identity. The hardest architectural commitment is NOT the GRIB parse or Platt fit — it is drawing a hard line at the seam where `ensemble_snapshots`(legacy) reads feed `calibration_pairs_v2` writes so the legacy asymmetry cannot leak in. The second-hardest is declaring what numeric parity between legacy-high-track-Platt and v2-high-track-Platt means BEFORE the diff exists, because after the fact any discrepancy can be explained away. Before exec-bob touches code, main-thread must resolve one live contract collision (§1.1 below), declare the parity threshold (§2), and confirm that Phase 4's writer must refuse legacy `ensemble_snapshots` writes silently (INV-15 doctrine).

---

## 1. Architectural risks in implementing task #53

### 1.1 CRITICAL blocker — canonical `data_version` tag string is inconsistent across the codebase today
- `src/types/metric_identity.py:82` → `tigge_mx2t6_local_calendar_day_max_v1`
- `docs/authority/zeus_dual_track_architecture.md:59` → `tigge_mx2t6_local_calendar_day_max_v1`
- `docs/operations/data_rebuild_plan.md:113` → `tigge_mx2t6_local_calendar_day_max_v1`
- `src/contracts/ensemble_snapshot_provenance.py:25, 120` → `tigge_mx2t6_local_peak_window_max_v1`
- `scripts/ingest_grib_to_snapshots.py:19` (stub docstring) → `tigge_mx2t6_local_peak_window_max_v1`

Two tag strings with different physical semantics: **calendar_day_max** vs **peak_window_max**. The type system and authority law say one, the ingestor contract docstring and quarantine contract say the other. Phase 4 MUST pick one before exec-bob writes; otherwise the 420 GRIB files land with a tag that fails either the `assert_data_version_allowed` guard (if it hardcodes the other name) or the `MetricIdentity.from_raw()` guard downstream.

**Recommendation:** `tigge_mx2t6_local_calendar_day_max_v1` is authoritative per §8 and the type system. Phase 4A's first commit must update `ensemble_snapshot_provenance.py` docstring + error message + the stub docstring to match. This is a 3-line fix that must land BEFORE the GRIB writer is written, not as part of it.

### 1.2 Commit boundaries / recoverable ingest
The right atomicity unit for GRIB ingest is **one (file, city) snapshot** inside one SAVEPOINT, with the file-level batch ending in one COMMIT. This mirrors `daily_obs_append.py:562-655` SAVEPOINT pattern and gives the same guarantee: partial ingest of a file leaves no half-written snapshot + half-written manifest_hash. Do NOT commit per (cycle, member) — GRIB parse cost and DB cost both scale at that level, and a crash mid-cycle re-parses the file on restart.

**DT#1 enforcement points:**
- Snapshot INSERT must COMMIT before any `manifest_hash` derived file is written.
- If Phase 4 writes a parallel `rebuild_manifest.json` (recommended for operator audit), that JSON must be produced from a post-commit `SELECT` over the v2 table, NOT from an in-memory dict populated during ingest. Anything else violates DT#1.

### 1.3 INV-15 runtime-only fallback gating at the writer
Every `ensemble_snapshots_v2` INSERT must set `training_allowed` correctly:
- `issue_time IS NULL` → `training_allowed = 0`, `causality_status = 'RUNTIME_ONLY_FALLBACK'`. Never silently `1`.
- `data_version` in QUARANTINED_* → reject at write, don't set `training_allowed = 0` and continue.
- `manifest_hash IS NULL` → `training_allowed = 0`. The v2 schema DEFAULT 1 on this column is a trap: the writer must explicitly compute and pass the flag, never rely on DEFAULT.

The schema's DEFAULT 1 on `training_allowed` (v2_schema.py:133) is a Phase 2 decision I would now reverse — defaults should be 0 for safety invariants. Flag as MODERATE for backlog; too late to change without a migration.

---

## 2. Parity definition — "Phase 4 high canonical cutover parity"

**Operational definition:** for the intersection of rows valid in BOTH legacy `ensemble_snapshots` and v2 `ensemble_snapshots_v2` (same city, same target_date, same lead_hours bucket, both `authority='VERIFIED'` and `training_allowed=1`):
1. `|p_raw_v2 - p_raw_legacy|` median ≤ **0.005** (50 bps), p99 ≤ **0.02** (200 bps).
2. Per-bucket (season × cluster) Brier score v2 ≤ Brier score legacy × **1.02** (no more than 2% regression; improvements OK and expected since members geometry is fixed).
3. Platt `(A, B, C)` parameter drift: `|ΔA| + |ΔB|` ≤ **0.10** on a per-bucket basis; if any bucket exceeds, it must be explained by known geometry fix.

**Authority-level reasoning:** §8 dual-track arch says high is re-canonicalized onto `mx2t6_local_calendar_day_max_v1` *because the old lane was wrong* (param_167 point-forecast vs daily-max consumer). A perfect match would mean the fix didn't fix anything. A large unexplained drift means the v2 writer has an independent bug. The threshold above is tight enough to catch a silent bug (e.g. unit mismatch: °C vs °F would blow past 0.02 trivially) and loose enough to admit the known mx2t6 geometry correction.

**Corollary:** the parity diff is itself a Phase 4 deliverable. `docs/operations/task_2026-04-16_dual_track_metric_spine/phase4_evidence/parity_diff.md` should contain the three numbers above, per bucket, with each failure explained. Do not let Phase 4 close on "tests green" without this diff.

---

## 3. Phase boundary hazards

### 3.1 Leaks INTO Phase 5 (low lane) if not gated
- Any helper extracted from `rebuild_calibration_pairs_canonical.py` that accepts `temperature_metric: str` MUST reject `low` in Phase 4. Declare it (not silently accept) via `if temperature_metric != 'high': raise NotImplementedError("Phase 5 scope")`. Otherwise Phase 5 inherits an untested code path.
- `observations` query at `rebuild_calibration_pairs_canonical.py:189` today selects `high_temp`. Scout flagged this. Phase 4 must refactor to `observation_field` column lookup (`high_temp` vs `low_temp` depending on metric identity), but **for Phase 4 the branch must hardcode high_temp and assert metric == high**. Delay the low branch to Phase 5.

### 3.2 Regresses Phase 1-3 if not careful
- `src/calibration/store.py::add_calibration_pair` (scout called out): adding `temperature_metric` parameter is fine, but it must NOT become a `kwarg` with default `"high"` — that silently reintroduces the single-track assumption. Make it positional and required.
- `evaluator.py:803, 815-828` now reads `Day0ObservationContext` attributes. If Phase 4's calibration-pairs writer re-wires the observation query, the chain evaluator → calibration-pairs lookup → observation must preserve the `observation_field` selector. Test that `MetricIdentity.HIGH_LOCALDAY_MAX.observation_field == "high_temp"` is honored at every SELECT that used to hardcode `high_temp`.
- `src/contracts/ensemble_snapshot_provenance.py::assert_data_version_allowed` must keep refusing `param_167` and `step024` families (unchanged). Phase 4 is opening a WRITE lane onto the guarded table; the guard must fire on every INSERT, not just on library entry.

### 3.3 File:line hazards to watch
- `scripts/rebuild_calibration_pairs_canonical.py:167-173` — SELECT from `ensemble_snapshots` (legacy). Phase 4A reads legacy, Phase 4B+ reads v2. Make this path switchable via CLI flag, not env-var magic.
- `src/calibration/manager.py::season_from_date` — already called by ingest paths. No metric awareness needed (season is geographic/temporal, not metric-keyed). Do NOT add `temperature_metric` here.
- `src/state/schema/v2_schema.py:133` — `training_allowed` DEFAULT 1. Writer must pass explicit value.

---

## 4. R-invariants for testeng-emma (Phase 4)

- **R-I (writer metric closure):** every INSERT into `ensemble_snapshots_v2` by `ingest_grib_to_snapshots.py` sets `temperature_metric`, `physical_quantity`, `observation_field`, and `data_version` to values that together pass `MetricIdentity.from_raw()` validation. Bare `"high"` string without the accompanying `physical_quantity` is an insert-time reject.
- **R-J (provenance closure):** every v2 snapshot row has `manifest_hash IS NOT NULL` AND `provenance_json` contains keys `{grib_source_path, cycle_time, member_count, parse_version}`. Missing any → reject.
- **R-K (training_allowed integrity):** rows with `issue_time IS NULL` OR `causality_status != 'OK'` OR `data_version` matching a quarantine prefix MUST have `training_allowed = 0`. Assertion: no row exists with `training_allowed = 1 AND issue_time IS NULL`.
- **R-L (rebuild metric purity):** `calibration_pairs_v2` rows where `temperature_metric = 'high'` join only to `observations.high_temp` (not `low_temp`) and only to `ensemble_snapshots_v2` rows where `temperature_metric = 'high'`. Cross-metric joins are schema-impossible if FKs are set, but the test asserts it empirically on a seeded fixture.
- **R-M (DT#1 commit ordering):** any JSON export produced by the Phase 4 pipeline (parity_diff.md tables, manifest) is derived from a post-commit SELECT, not from in-memory state. Test: mutate the in-memory dict after commit, regenerate the export, confirm it reflects committed state not mutated state.
- **R-N (parity bound):** on the seeded parity fixture, `|p_raw_v2 - p_raw_legacy|` stays under threshold §2. This is the numeric antibody that will catch the Day0 London DST-class silent bug Fitz's methodology §4 warns about.

---

## 5. Pre-mortem — most likely silent failure mode

**Scenario:** Phase 4 lands. Tests green. Two weeks later, Kelly sizing on high-track positions is systematically 5% too aggressive. Nobody notices because P&L noise >> 5%.

**Root cause (predicted):** `ingest_grib_to_snapshots.py` parses GRIB member values in Kelvin but writes `members_json` in Celsius without explicit unit metadata. The v2 schema has no `members_unit` column (v2_schema.py:113 — see `src/contracts/calibration_bins.py:297` — existing antibody for the legacy table). Platt Fits on Celsius training, but runtime `p_raw_vector_from_maxes` reads back in whatever unit `members_json` serialized as, and unit drift silently enters. Brier insample is fine (same-unit train/test); runtime diverges.

**Test that would have caught it:** unit consistency check on the seeded fixture — assert that the `(city, target_date)` quintet has `abs(mean(members_json) - observations.high_temp_in_members_unit) < 30` (crude but catches a 273-unit Kelvin/Celsius offset). Add as R-O.

**Broader pre-mortem lesson:** the four-constraints #4 (data provenance) says every data source needs `source` and `authority`. The v2 schema has `authority`. It does NOT have `members_unit` or `members_precision`. Flag for Phase 4 decision: either add it to schema (small migration) or enforce unit consistency via writer contract.

---

## 6. Simplification targets

- **Drop dead ingest stubs** (Phase 4 bundle with task #53 work):
  - `scripts/generate_calibration_pairs.py` (scout flagged, ~400 LOC) — supplanted by canonical rebuild. Delete in the same PR that adds the v2 writer, so operators see one writer not two.
  - `wu_daily_collector.py` in `src/main.py:46-50, :84-94` — still registered. Scout noted dual-write window can close. Phase 4 is the natural point to disable the legacy collector since Phase 3 has 2 weeks of stability.
- **Dedupe the tag name** (§1.1): 2-line fix, high-value.
- **Fold the CITY_STATIONS scripts-tier divergence** (my Phase 3 MODERATE-1) into Phase 4 if the writer already refactors `backfill_wu_daily_all.py`. Otherwise defer to Phase C.

---

## Main-thread decisions needed

1. **Q1 (blocker):** Which data_version tag is canonical — `tigge_mx2t6_local_calendar_day_max_v1` or `tigge_mx2t6_local_peak_window_max_v1`? Must answer before exec-bob writes. (Recommend: calendar_day_max, per §8 authority + type system.)
2. **Q2 (parity):** Adopt §2 thresholds (0.005 median / 0.02 p99 on `p_raw`, Brier regression ≤ 2%, `|ΔA|+|ΔB|` ≤ 0.10 per bucket)? Or tighter/looser?
3. **Q3 (members_unit):** Add `members_unit` + `members_precision` columns to `ensemble_snapshots_v2` now (small migration, Phase 4A first commit) or defer and enforce via writer contract only? (Recommend: add now; §5 pre-mortem depends on it.)
4. **Q4 (scope of parity deliverable):** Is the parity_diff.md a Phase 4 gate (no PASS without it) or a post-Phase-4 backlog item? (Recommend: gate; parity-without-measurement is a dead-letter promise.)
5. **Q5 (bundle with Phase 4?):** Include the dead-code drops (`generate_calibration_pairs.py`, `wu_daily_collector` unregistration, tag-name dedup) in Phase 4's PR, or spin them out? (Recommend: bundle; Phase 4 is already touching these adjacencies.)

---

## Top 3 insights

1. The real hazard isn't GRIB parsing or Platt fit — it's that v2 schema is metric-aware and everything upstream isn't, so the first real write is the moment the silent single-track assumption gets exposed. Phase 4A's first commit should be the 3-line tag-name fix (§1.1), not the ingestor.
2. Parity must be defined numerically BEFORE the diff exists, because post-hoc any delta can be explained away. §2 gives specific thresholds with authority reasoning.
3. Pre-mortem predicts a Kelvin/Celsius unit drift as the most probable silent failure (§5). Either add `members_unit` to v2 schema (Q3) or accept a writer-contract antibody. Don't skip this decision.
