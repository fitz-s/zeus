# Phase 7 Microplan — Metric-Aware Rebuild Cutover + Naming-Pass Chores

**Status**: DRAFT (planner consultant output). **Authority**: team-lead rules on open scope questions below.
**Predecessor**: Phase 6 COMPLETE at `413d5e0`; B070 merged at `b74d026`. Branch `data-improve`.
**Zero-Data Golden Window**: STILL ACTIVE. v2 tables empty until Phase 8 shadow.

## 1. Scope stratification

### CORE P7 (must land)

1. **`rebuild_v2` METRIC_SPECS iteration** — `scripts/rebuild_calibration_pairs_v2.py` L316–L430. Currently `spec = METRIC_SPECS[0]` (HIGH only). Iterate both HIGH + LOW within a single SAVEPOINT, per-spec stats, single refusal gate on aggregate hard-failures. Un-xfail parity tests if any remain (R-AZ-1/2 already GREEN per `ecf50bd`, confirm).
2. **`remaining_member_maxes_for_day0` alias removal** (`src/signal/day0_window.py:76–90`). 4 test callers to migrate (`test_day0_window.py`×3, `test_execution_price.py`×1, `test_fdr.py`×3, `test_runtime_guards.py`×2 — **9 total**, not 4; re-grep confirms). Mechanical rename, no src/ production callers (evaluator + monitor_refresh already on new name).
3. **`_tigge_common.py` extraction** — 15 shared-name helpers (handoff says 12; exec should recount). Safe mechanical subset: `_ceil_to_next_6h`, `_local_day_bounds_utc`, `_overlap_seconds`, `_issue_utc_from_fields`, `_now_utc_iso`, `_city_slug`, `_file_sha256`, `_output_path`, `_get_city_config`, `_cross_validate_city_manifests`, `_load_cities_config`, `_parse_steps_from_filename`, `_parse_dates_from_dirname`, `_find_region_pairs`, `_iter_overlap_local_dates`. **Semantics-sensitive / DO NOT merge blind**: `compute_required_max_step`/`_compute_required_max_step`, `compute_manifest_hash`/`_compute_manifest_hash`, `compute_causality` vs `_compute_causality_low` — public/private name split + LOW-specific causality variant. Extract only the 15 pure-mechanical helpers; leave the 3 compute_* variants in place with a TODO.
4. **Script manifest registration** — 5 unregistered scripts confirmed by `test_topology_scripts_mode_covers_all_top_level_scripts`: `extract_tigge_mx2t6_localday_max.py`, `extract_tigge_mn2t6_localday_min.py`, `migrate_b070_control_overrides_to_history.py`, `rebuild_calibration_pairs_v2.py`, `refit_platt_v2.py`. Add to `scripts:` section L253+ with correct classes (`etl_writer` for tigge extractors, `repair` for migrate_b070, `etl_writer`/`repair` for rebuild/refit).

### P7 CO-LANDING chores

- Run `tests/test_topology_doctor.py` navigation/docs-to-do-list tests after manifest registration — they may self-clear if same-root cause. If still failing, co-land doc/rationale gap fixes (<20 LOC).
- `tests/test_truth_surface_health.py::TestGhostPositions` — live-DB-assumption test; triage on whether it auto-clears with P7 scope or needs xfail marker (co-landing minor).

### DEFER to P8/later

- **B093 half-2 (replay migration to `historical_forecasts_v2`)** — **DEFER**. Rationale below (§2 open question). `src/engine/replay.py:242–264` `_forecast_rows_for` still SELECTs from legacy `forecasts` table. The P7 file-header TODO at L245–L247 explicitly notes "Phase 7: migrate…"; **but v2 table is empty until Phase 8 shadow activation**. Migrating the query now produces empty result sets with no fallback → silent replay failure. Safe landing requires either (a) Phase 8 backfill-first or (b) dual-read with source-tag precedence. Both are P8-scoped.
- 137 pre-existing-failure triage → separate post-P7 chore.
- `cycle_runner.py:180–181` DT#6 rewiring + `Day0LowNowcastSignal.p_vector` proper impl → Phase 8/9 (per handoff forward-log).

## 2. Open scope question for team-lead (B093 half-2 ruling)

**Question**: Can B093 half-2 land in P7 with Zero-Data Golden Window still active?

**Planner recommendation**: **DEFER to Phase 8**. Two-of-three sub-options all fail cleanly in P7 scope:
- Option A (migrate query, accept empty results): breaks replay fallback — unacceptable.
- Option B (dual-read with `forecasts`→`historical_forecasts_v2` precedence + authority tag): `forecasts` is the *only* populated table pre-Phase-8 → dual-read is a no-op until P8. Zero test coverage value; adds complexity.
- Option C (backfill `historical_forecasts_v2` first): violates Zero-Data Golden Window without user approval + expands P7 to batch ETL.

**Team-lead ruling required**: confirm DEFER, or approve Option B as "plumbing-now-activation-later" if P8 scope clarity demands it.

## 3. Shipping strategy (P2 compliance)

**Recommendation: Strategy A — SINGLE COMMIT** for all CORE P7 items.

Per operating-contract P2 (single-commit atomicity) + P7 aggressive-deferral, CORE P7 items are **cohesive**: all four serve the "metric-aware cutover + naming debt pay-down" theme. Commit boundary:

```
commit "Phase 7: metric-aware rebuild + naming/manifest debt (R-BH..R-BL)"
  M scripts/rebuild_calibration_pairs_v2.py       # METRIC_SPECS iteration
  M src/signal/day0_window.py                     # remove alias (-15 LOC)
  M tests/test_day0_window.py, test_execution_price.py, test_fdr.py, test_runtime_guards.py  # alias→new name (9 sites)
  A scripts/_tigge_common.py                      # 15 shared helpers
  M scripts/extract_tigge_mx2t6_localday_max.py   # imports from _tigge_common
  M scripts/extract_tigge_mn2t6_localday_min.py   # imports from _tigge_common
  M architecture/script_manifest.yaml             # register 5 scripts
  A tests/test_phase7_metric_cutover.py           # R-BH..R-BL
```

**Why not split**: individual items are <200 LOC each; splitting creates 4 commits with cross-dependencies (manifest registration references tigge extractors whose helpers just moved). P2 single-commit is the right boundary unless critic flags divergent risk profiles.

**Why not bundle B093 half-2**: it's a distinct structural decision gated by Zero-Data window. Keeping it separate honors P7 aggressive-deferral.

## 4. R-letter allocation

Last locked: R-BG (Phase 6). Available from R-BH.

Proposed (≤8):
- **R-BH**: `rebuild_v2` iterates all METRIC_SPECS; per-spec counts in `RebuildStatsV2.per_metric`; aggregate refusal gate still single.
- **R-BI**: HIGH-only invocation path (`spec=METRIC_SPECS[0]` kwarg explicit) still functions (backward-compat for CLI flag if any).
- **R-BJ**: `remaining_member_maxes_for_day0` symbol does not exist in `src/signal/day0_window.py` (AST assertion).
- **R-BK**: `_tigge_common.py` exports the 15 helpers; mx2t6 + mn2t6 import from it (AST walk); no duplicate function defs remain.
- **R-BL**: `architecture/script_manifest.yaml` contains entries for all 5 previously-unregistered scripts; topology test GREEN.

Total: 5 R-letters. Budget remains within ≤8 for future revisions.

## 5. Three load-bearing verification gates

**Gate P7-α (pre-implementation)**: `python -m pytest tests/test_topology_doctor.py::test_topology_scripts_mode_covers_all_top_level_scripts -x` currently FAILS with 5-missing list. Freeze that baseline; any deviation mid-implementation means an executor touched the wrong thing.

**Gate P7-β (mid-implementation, post-alias-removal)**: `grep -rn "remaining_member_maxes_for_day0" src/ tests/` returns ZERO hits. If ≥1, a caller was missed → roll back stage.

**Gate P7-γ (pre-commit)**: dry-run `python scripts/rebuild_calibration_pairs_v2.py` (no `--force`) prints per-spec snapshot counts for HIGH AND LOW; both non-negative; `stats.refused == False` path reachable. Full regression delta: ≤ P6 baseline (138 failed / 1801 passed). **Zero new failures** is the bar.

## 6. Three risks with mitigations

**R1 — `_tigge_common.py` extraction imports the wrong variant.**
The `compute_causality` vs `_compute_causality_low` split is semantics-sensitive (LOW has different overlap-seconds rounding). Naïve merge = silent causality bug during Phase 8 LOW shadow.
*Mitigation*: Extract ONLY the 15 pure-mechanical helpers listed above. Leave all `compute_*`/`_compute_*` variants in their original files with explicit `# DELIBERATELY_NOT_EXTRACTED: LOW variant diverges` comment. scout-gary pre-extraction landing-zone scan recommended.

**R2 — METRIC_SPECS iteration doubles write volume; SAVEPOINT rollback semantics.**
With both HIGH + LOW in a single SAVEPOINT, a LOW-side `missing_city` failure rolls back HIGH work. Current single-spec code assumed one rollback unit.
*Mitigation*: Keep single SAVEPOINT (correct atomicity) but ensure per-spec stats accumulate before the aggregate `hard_failures` gate (L414). Add R-BH assertion: partial write on LOW failure rolls back HIGH too (no orphan rows).

**R3 — Test-side alias removal misses a monkeypatch site.**
9 test callers across 4 files (not 4 as handoff suggests). `tests/test_runtime_guards.py:892` + `:3185` use `monkeypatch.setattr(evaluator_module, "remaining_member_maxes_for_day0", ...)` — monkeypatch by string; the alias was already removed from evaluator imports (it imports `remaining_member_extrema_for_day0`). These monkeypatch sites are **already broken silently** (patching a non-existent attr may no-op depending on raising strictness). Surface as pre-existing bug or fix in-commit.
*Mitigation*: grep both string-form attr names; fix monkeypatch targets to current symbol. If this is the "pre-existing failure" root cause for any of the 137, bonus clearance.

## 7. P2 + aggressive-deferral compliance

- **P2 (single-commit atomicity)**: PASS. One commit covers all CORE P7; no cross-commit dependencies; no mid-work broken state.
- **P7 aggressive-deferral**: PASS. B093 half-2 explicitly deferred with structural rationale (Zero-Data Window). DT#6 rewiring and `Day0LowNowcastSignal.p_vector` proper impl remain in Phase 8/9 per handoff. No scope creep from accumulated-debt pile (navigation/ghost-positions test triage handled as co-landing only if mechanical).
- **Deviation**: NONE.

## 8. Team assignment proposal (team-lead ruling)

- **exec-juan**: rebuild_v2 iteration (extends his 5C rebuild_v2 spec-landing ownership).
- **exec-ida**: tigge helper extraction + alias removal + manifest registration (mechanical; extends her refactor discipline from fix-pack).
- **testeng-hank**: R-BH..R-BL drafting.
- **scout-gary**: pre-extraction tigge landing-zone scan (which helpers are truly safe to merge).
- **critic-beth**: wide review + L0.0 discipline.

---

## Executive summary (for return to team-lead)

**Top scope recommendation**: 4 CORE items land in ONE commit (rebuild_v2 METRIC_SPECS iteration + alias removal + tigge_common extraction + manifest registration). **DEFER B093 half-2 to Phase 8** — migrating to `historical_forecasts_v2` while Zero-Data Window is active means migrating TO an empty table; no safe landing exists in P7.

**Top risk**: `_tigge_common.py` naïve extraction sweeps the LOW-specific `_compute_causality_low` variant into the shared module. LOW causality semantics diverge from HIGH; silent bug surfaces in Phase 8 shadow. Mitigation: extract only the 15 pure-mechanical helpers; leave all `compute_*` / `_compute_*` variants untouched with explicit `DELIBERATELY_NOT_EXTRACTED` markers.

**Biggest unknown**: Whether the 9 test-side callers of `remaining_member_maxes_for_day0` (including 2 `monkeypatch.setattr` sites by string) are already silently broken against the P6-renamed symbol — may expose a pre-existing failure in the 137 baseline, or may be a genuine latent bug the alias was masking. Requires exec to `grep + pytest` verify before assuming "mechanical rename."

Plan file: `/Users/leofitz/.openclaw/workspace-venus/zeus/docs/operations/task_2026-04-16_dual_track_metric_spine/phase7_microplan.md`
