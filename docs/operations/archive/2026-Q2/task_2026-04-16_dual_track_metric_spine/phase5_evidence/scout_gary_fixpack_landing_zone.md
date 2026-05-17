# Scout Gary — Fix-Pack Landing-Zone Verification

# Lifecycle: created=2026-04-17; last_reviewed=2026-04-17; last_reused=never
# Purpose: Disk-verify current state of all 9 fix-pack targets before R-letter drafting and implementation.
# Reuse: Read alongside team_lead_handoff.md §"Phase 5B-fix-pack scope". All claims backed by fresh bash grep.

Author: scout-gary (sonnet, fresh)
Date: 2026-04-17
Branch: data-improve (HEAD: post-5B)

---

## Summary verdict

All 9 targets verified. **4 are RED (current state does NOT match expected/fixed state — fix required).** 5 are in expected pre-fix state (confirmed unfixed, matches handoff description). No unexpected drift found.

---

## Target-by-target results

### 1. `classify_boundary_low` behavioral coverage — RED (unresolved, expected)

**Expected**: R-AG only tests importability (critic MAJOR-1). R-AP behavioral tests not yet written.

**Disk result**: CONFIRMED. `tests/test_phase5b_low_historical_lane.py` contains exactly one test referencing `classify_boundary_low`:

```
168:    def test_classify_boundary_low_is_importable(self):
169:        """R-AG (acceptance): classify_boundary_low must be importable..."""
170:        from scripts.extract_tigge_mn2t6_localday_min import classify_boundary_low  # noqa: F401
```

Zero behavioral assertions. No polarity tests. No Tokyo/LA causality case. No west-coast step-horizon case. **R-AP is unwritten** — this is the expected pre-fix state but testeng-hank must draft it before any extractor executor work.

---

### 2. `mode=None` in `read_mode_truth_json` — RED (unresolved, fix required)

**Expected fix**: explicit `None` should be rejected, not silently accepted.

**Disk result**: CONFIRMS the bug still present. `src/state/truth_files.py:153`:

```
153: def read_mode_truth_json(filename: str, *, mode: str | None = None) -> ...:
156:     if mode is not None:
```

The signature accepts `mode=None` and the body only runs the `ModeMismatchError` check inside `if mode is not None`. A `mode=None` caller silently bypasses the guard. **Bug confirmed unfixed.**

---

### 3. Quarantined member `value_native_unit` — RED (unresolved, fix required)

**Expected fix**: when `training_allowed=False` (boundary ambiguous), `value_native_unit` should be `None`, not `inner_min`.

**Disk result**: CONFIRMS silent trap still present. `scripts/extract_tigge_mn2t6_localday_min.py:288`:

```
288:     if any_boundary_ambiguous:
289:         # Whole snapshot quarantined; don't emit effective_min as if clean
290:         val_k = clf.inner_min  # still emit inner_min for diagnostics
...
293:     members_out.append({"member": m, "value_native_unit": val_k})
```

When `any_boundary_ambiguous=True` (`training_allowed=False`), `value_native_unit` is set to `clf.inner_min` (non-null). A downstream consumer reading `value_native_unit` without checking `training_allowed` gets wrong data. **Bug confirmed unfixed.**

---

### 4. DST step-horizon in `_compute_required_max_step` — RED (unresolved, fix required)

**Expected fix**: use target-date local offset, not point-in-time issue_utc offset.

**Disk result**: CONFIRMS bug still present. `scripts/extract_tigge_mn2t6_localday_min.py:522-531`:

```
522: def _compute_required_max_step(issue_utc, target_date, city_utc_offset_hours):
528:     fixed_tz = timezone(timedelta(hours=city_utc_offset_hours))
529:     next_day = target_date + timedelta(days=1)
530:     local_day_end_local = datetime.combine(next_day, dt_time.min, tzinfo=fixed_tz)
531:     local_day_end_utc = local_day_end_local.astimezone(timezone.utc)
```

`city_utc_offset_hours` is passed in from the call site at `L295` where it was computed via `ZoneInfo(city_tz).utcoffset(issue_utc)` — i.e., the offset at issue time, not at target_date. For DST-crossing pairs the step horizon will be 1h off. **Bug confirmed unfixed.**

---

### 5. `observation_client.py:87` module-level SystemExit — RED (unresolved, fix required)

**Expected**: `raise SystemExit(...)` at module import time when `WU_API_KEY` is absent.

**Disk result**: CONFIRMED still present. Lines 85-88:

```
85: WU_API_KEY = os.environ.get("WU_API_KEY", "")
86: if not WU_API_KEY:
87:     raise SystemExit("CRITICAL ERROR: WU_API_KEY environment variable is missing.")
```

Module-level raise is live. Any transitive importer in a dev env without `WU_API_KEY` crashes with `SystemExit` instead of `ImportError`. This blocks `test_phase6_causality_status.py` (testeng-grace confirmed all 3 tests fail at import). **Bug confirmed unfixed.**

---

### 6. Rebuild `data_version` assertion — STRENGTHEN (partially resolved, spec-side cross-check missing)

**Expected pre-fix**: no assertion; any data_version accepted.

**Disk result**: PARTIALLY RESOLVED. `scripts/rebuild_calibration_pairs_v2.py:216`:

```
211:     data_version = snapshot["data_version"] or ""
216:     assert_data_version_allowed(data_version, context="rebuild_calibration_pairs_v2")
```

`assert_data_version_allowed` IS called — it checks the global allow-list (is the version string in `{HIGH_LOCALDAY_MAX.data_version, LOW_LOCALDAY_MIN.data_version}`?). Both tracks are in METRIC_SPECS (L86-87). The eligibility query also filters `temperature_metric = ? AND data_version = ?` (L97-103).

**However**: the stronger check from exec-emma §1 is still absent — `assert row["data_version"] == spec.allowed_data_version` inside `_process_snapshot_v2`. Current gate asks "is this a known version?" not "does this row's version match the spec currently being rebuilt?" A snapshot with the valid HIGH version would pass through a LOW rebuild run without rejection. Fix-pack action: STRENGTHEN, not re-implement — add the spec-side cross-check to `_process_snapshot_v2`. [CRITIC-BETH refinement, disk-verified 2026-04-17]

---

### 7. Contract rejection log level — RED (unresolved, fix required)

**Expected fix**: log at ERROR not WARNING on contract rejection.

**Disk result**: CONFIRMS still at WARNING. `scripts/ingest_grib_to_snapshots.py:183-185`:

```
183:     if not decision.accepted:
184:         logger.warning(
185:             "ingest_json_file contract_rejected: path=%s reason=%s",
```

One-line change: `logger.warning` → `logger.error`. **Not yet applied.**

---

### 8. `_extract_causality_status` dead call site — GREEN (confirmed dead, delete pending)

**Expected**: defined but never called post-5B.

**Disk result**: CONFIRMED dead. Fresh repo-wide grep:

```
scripts/ingest_grib_to_snapshots.py:107: def _extract_causality_status(payload: dict) -> str:
scripts/ingest_grib_to_snapshots.py:176: # to {"status": "OK"} to match the pre-existing _extract_causality_status behavior.
```

Only one definition (L107) and one comment reference (L176). **Zero call sites.** Safe to delete. The fix-pack deletes the function body; the comment at L176 should be updated or removed simultaneously.

---

### 9. `wu_daily_collector.py` + `main.py:73` lazy import — GREEN (exists, lazy guard present, delete pending)

**Expected**: module exists; lazy guard still imports it.

**Disk result**: CONFIRMED. `src/data/wu_daily_collector.py` exists on disk. `src/main.py:73`:

```
73:     from src.data.wu_daily_collector import collect_daily_highs
```

Inside `_wu_daily_collection()` function wrapped in `try/except Exception`. The guard is a function-scoped try/except (not a module-level `try/except ImportError`), meaning the module IS imported on each call to `_wu_daily_collection()` — it does not silently swallow `ImportError` at import time; it swallows any exception at runtime. **Module exists, lazy import present. DEAD_DELETE safe once the function body and its cron/call sites are cleaned.**

---

## Bonus: LEGACY_STATE_FILES low-lane entries

Files added to `LEGACY_STATE_FILES` in 5B for B078 (`src/state/truth_files.py:26-32`):

```
"platt_models_low.json"
"calibration_pairs_low.json"
```

`status_summary.json`, `positions.json`, `strategy_tracker.json` were pre-existing. The two new low-lane entries derive `_LOW_LANE_FILES` via the `"platt_models_low" in f or "calibration_pairs_low" in f` filter. Testeng-hank: these exact filenames are the R-letter anchor for any B078-related tests.

---

## Bonus: Provenance headers on 5B-committed files

| File | Status |
|---|---|
| `scripts/extract_tigge_mn2t6_localday_min.py` | PRESENT (L1-5: Zeus Lifecycle/Purpose/Reuse triad) |
| `src/contracts/snapshot_ingest_contract.py` | PRESENT (L1-3: CLAUDE.md global format: Created/Last reused/Authority basis) |
| `tests/test_phase5b_low_historical_lane.py` | PRESENT (L1-3: Zeus Lifecycle/Purpose/Reuse triad) |

**Correction** (critic-beth, disk-verified 2026-04-17): `src/contracts/snapshot_ingest_contract.py` carries the CLAUDE.md global header format at L1-3. Zeus-local `# Lifecycle:/Purpose:/Reuse:` triad from `naming_conventions.yaml §freshness_metadata.applies_to` only binds `scripts/*.py`, `scripts/*.sh`, `tests/test_*.py` — `src/contracts/*.py` is out of scope. Header is PRESENT; format mismatch only. MINOR consistency note for executor, not a compliance gap. Original scan applied wrong rule scope.

---

## Bonus: Additional STALE/DEAD candidates spotted

- `scripts/rebuild_calibration_pairs_canonical.py` — v1 legacy rebuild, scout-finn flagged for `DEAD_DELETE` at Phase 7. No new drift.
- `scripts/backfill_tigge_snapshot_p_raw.py` — v1 legacy backfill, same verdict.
- `src/signal/ensemble_signal.py::p_raw_vector_from_maxes` — misleading name (used for both high and low). Phase 7 rename pass, no urgency.
- `src/signal/day0_window.py::remaining_member_maxes_for_day0` — same naming drift issue. Phase 6 decision point.

---

## RED summary for critic-beth and testeng-hank

| # | Target | Verdict | Action needed |
|---|---|---|---|
| 1 | `classify_boundary_low` behavioral coverage | RED — unwritten | testeng-hank drafts R-AP before executor work |
| 2 | `mode=None` bypass in `read_mode_truth_json` | RED — bug present | fix-pack exec + R-AC regression test update |
| 3 | `value_native_unit` non-null when quarantined | RED — bug present | fix-pack exec + assertion + test |
| 4 | DST step-horizon uses issue-time offset | RED — bug present | fix-pack exec + DST-boundary R-letter |
| 5 | `observation_client.py:87` module-level SystemExit | RED — present | fix-pack exec: move guard to callsite/lazy |
| 6 | Rebuild `data_version` assertion | STRENGTHEN — allow-list check present; spec-side cross-check missing | add `assert row["data_version"] == spec.allowed_data_version` in `_process_snapshot_v2` |
| 7 | Contract rejection log WARNING not ERROR | RED — unresolved | one-line fix: warning → error |
| 8 | `_extract_causality_status` dead | GREEN — confirmed dead | delete function + update L176 comment |
| 9 | `wu_daily_collector.py` + main.py:73 | GREEN — exists, lazy guard present | delete module + clean function |
