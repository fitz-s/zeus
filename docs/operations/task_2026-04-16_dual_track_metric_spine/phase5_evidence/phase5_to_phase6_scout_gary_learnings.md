# Phase 5 → Phase 6 Scout Learnings

# Lifecycle: created=2026-04-17; last_reviewed=2026-04-17; last_reused=never
# Purpose: Knowledge extraction before team retirement — structural changes, latent issues, Phase 6 preview.
# Reuse: Fresh Phase 6 scout reads §4-5 first. Team-lead reads §1-3.

Author: scout-gary (sonnet, retiring at Phase 5 close)
Date: 2026-04-17
Covers: fix-pack + Phase 5C reconnaissance. Commit range: fix-pack `3f42842` → 5C `821959e` → hotfix `59e271c`.

---

## 1. Codebase structural changes across Phase 5

### New modules landed

**`src/contracts/snapshot_ingest_contract.py`** (Phase 5B) — K0 boundary gate for low-track snapshot ingest. Three-law quarantine: boundary_ambiguous, causality=N/A_CAUSAL_DAY_ALREADY_STARTED, absent issue_time. Public entry: `validate_snapshot_contract(payload, metric)`. Carries CLAUDE.md global provenance header (Created/Last reused/Authority basis). This module is the canonical trust gate for all future low-track ingest paths — do not duplicate its logic elsewhere.

**`scripts/extract_tigge_mn2t6_localday_min.py`** (Phase 5B) — low-track GRIB extractor (~835 LOC). Sibling to `extract_tigge_mx2t6_localday_max.py`. Owns `classify_boundary_low`, `build_low_snapshot_json`, `_compute_required_max_step`. Carries Zeus-local Lifecycle/Purpose/Reuse header. Has 4+ utility bodies duplicated from the high extractor — extraction candidate `_tigge_common.py` (see §4).

**Phase 5C typed-status fields in `src/engine/replay.py`** — `_forecast_reference_for` now returns structured dicts with `decision_reference_source`, `decision_time_status`, `agreement` fields (Literals, not sentinel strings). Metric-conditional value read (`forecast_high` vs `forecast_low`) wired. `_decision_ref_cache` key now includes `temperature_metric`. Gate D test at `tests/test_phase5_gate_d_low_purity.py` asserts no cross-metric leakage in `calibration_pairs_v2`.

**`tests/test_phase5_fixpack.py`** — R-AP..R-AU antibodies covering fix-pack work. R-AP: classify_boundary_low polarity + Tokyo/LA causality cases. R-AU: positive-allowlist check for data_version in rebuild (unknown version rejected, cross-track version rejected).

### Dead code removed

- `_extract_causality_status` function deleted from `scripts/ingest_grib_to_snapshots.py` (was defined at L107, zero call sites post-5B contract wiring).
- `src/data/wu_daily_collector.py` deleted + `src/main.py:73` lazy-import guard cleaned.
- Fix-pack also: `mode=None` bypass closed in `read_mode_truth_json`, `value_native_unit` null-for-quarantined enforced, DST offset fixed to target-date, `observation_client.py` SystemExit moved to callsite, contract rejection log level raised to ERROR, rebuild data_version strengthened to positive-allowlist-per-spec.

---

## 2. Latent-issue flags — new observations from Phase 5C

**`replay.py::_forecast_reference_for` SQL filter hotfix (`59e271c`)**: Phase 5C added `AND temperature_metric = ?` to the legacy `forecasts` table query, then a follow-up commit removed it. This suggests the legacy `forecasts` table does not carry a `temperature_metric` column (it predates the dual-track world). The query correctly reads `forecast_high` vs `forecast_low` via metric-conditional branching — but the table itself is metric-blind. Phase 7 (`historical_forecasts_v2` migration) will be the first time the SQL filter can actually gate on the column. Fresh Phase 6 executors should not assume the legacy `forecasts` table is metric-aware.

**`monitor_refresh.py:306` second Day0Signal callsite** — same `member_mins_remaining=remaining_member_maxes` dead-code-but-live assignment as `evaluator.py:825`. Both callsites pass the MAX array as the MIN input. Both are guarded by the `NotImplementedError` in `Day0Signal.__init__` for low metrics. Both MUST be fixed in the same Phase 6 commit that removes the guard — not just `evaluator.py:825`. Scout-finn's learnings only named `evaluator.py:825`; the monitor path is equally hazardous. **This is a new flag.**

**`_decision_ref_cache` key pattern** — 5C wired `temperature_metric` into the cache key. The cache type is `dict` with tuple keys. If the cache grows unbounded across a long replay run (thousands of city/date/metric triples), memory pressure could manifest. Not a correctness issue; note for Phase 7 when replay volume increases with real low-track data.

---

## 3. Day0 signal landscape — Phase 6 preview

### Current shape of `src/signal/day0_signal.py`

Single class `Day0Signal`, ~270 LOC. Key landmarks:
- `__init__:87` — `NotImplementedError` guard blocks low-metric construction. **This is the Phase 6 gate.**
- `p_vector:145` — Monte Carlo simulation, calls `_settle`. Shared between high and low once split.
- `legacy_upper_envelope_mean:212`, `observation_weight:217`, `_temporal_closure_weight:242`, `obs_dominates:251`, `forecast_context:258` — all are high-track semantics; low-track nowcast needs different implementations for `observation_weight` and `obs_dominates` at minimum.

### Callers that need rewiring for the split

| File | Line | What it does | Phase 6 action |
|---|---|---|---|
| `src/engine/evaluator.py` | ~814 | Constructs `Day0Signal`, passes `member_mins_remaining=remaining_member_extrema` (MAX array) | Replace with router: `Day0HighSignal` or `Day0LowNowcastSignal` based on `temperature_metric`; fix the MAX→MIN dead code simultaneously |
| `src/engine/monitor_refresh.py` | ~306 | Same construction + same MAX→MIN dead code | Same rewire; must co-land with evaluator fix (NEW flag — not in prior scout learnings) |

`src/signal/day0_window.py::remaining_member_maxes_for_day0` already handles both tracks (`is_low()` branch returns `slice_data.min(axis=1)`). Phase 6 executor should NOT rewrite or fork it — rename only if desired. The function name is misleading but the math is correct.

The DT-v2 package spec (`04_CODE_SNIPPETS/day0_signal_router.py`, 117 LOC) has the canonical router shape. Read it before designing the Phase 6 split.

### Co-landing imperative (hardened by this session)

`evaluator.py:825` + `monitor_refresh.py:306` MAX→MIN fix + `Day0Signal.__init__` NotImplementedError guard removal must all land in ONE commit. Any decoupling is a death trap.

---

## 4. `_tigge_common.py` extraction — timing

**Still in backlog.** The 12 shared helpers (confirmed by exec-dan's learnings + this session's reads) are:
`_compute_manifest_hash`, `_now_utc_iso`, `_city_slug`, `_overlap_seconds`, `_local_day_bounds_utc`, `_issue_utc_from_fields`, `_parse_steps_from_filename`, `_parse_dates_from_dirname`, `_find_region_pairs`, `_iter_overlap_local_dates`, `_cross_validate_city_manifests`, `_load_cities_config`.

**Recommended timing: Phase 7, not Phase 6.** Rationale: Phase 6 (Day0 split) does not touch either GRIB extractor. Extracting `_tigge_common.py` during Phase 6 adds cross-file risk for zero Phase 6 benefit. Phase 7 (metric-aware rebuild cutover) DOES touch the extractor pipeline, making it the natural moment to consolidate. However: any DST-related fix to `_local_day_bounds_utc` or `_compute_required_max_step` before Phase 7 must be applied to BOTH extractors manually — the divergence risk is real.

---

## 5. Fresh-scout inheritance — Phase 6 navigation

**Files to read first for Phase 6:**

1. `AGENTS.md` root (law + mental model)
2. `docs/authority/zeus_dual_track_architecture.md` §4 (Day0 split SD-4) + §6 (DT#6 graceful-degradation)
3. `src/signal/day0_signal.py` — read the full file (~270 LOC, manageable). Understand the guard at L87 and all methods before designing the split.
4. `zeus_dual_track_refactor_package_v2_2026-04-16/04_CODE_SNIPPETS/day0_signal_router.py` — the canonical router spec.
5. `zeus_dual_track_refactor_package_v2_2026-04-16/04_CODE_SNIPPETS/day0_observation_context.py` — `Day0ObservationContext` dataclass with `low_so_far`.
6. `src/engine/evaluator.py` L800-835 — Day0 routing block; understand the current construction before rewiring.
7. `src/engine/monitor_refresh.py` L295-325 — second Day0Signal callsite (new flag; not in prior learnings).

**Anti-patterns to avoid entering Phase 6:**

- Do not read `day0_signal.py` from the top and assume the whole class survives the split. `observation_weight`, `obs_dominates`, and `_temporal_closure_weight` have high-track semantics baked in. Low nowcast needs independent implementations.
- Do not treat `monitor_refresh.py` as already handled. Prior learnings only named `evaluator.py:825` — the monitor callsite at L306 has the identical MAX→MIN hazard and must co-land.
- Do not use `remaining_member_maxes_for_day0` from `day0_window.py` as the low-track min supplier without checking the `is_low()` branch at L67 — it already does the right thing (`slice_data.min(axis=1)`), but the name will confuse a reader who hasn't seen the dual-track comment at L32.
- Do not read `src/data/wu_daily_collector.py` — deleted in fix-pack.
- Legacy `forecasts` table is metric-blind. Do not add `AND temperature_metric = ?` to queries against it — the column does not exist (lesson from `59e271c` hotfix).

**Navigation shortcuts:**

- `evaluator.py` Day0 block starts at ~L784. Metric guard at L800. Don't read above L750 for Phase 6 work.
- `day0_signal.py` is 270 LOC — read fully, it's worth it.
- `day0_window.py` is the ensemble-slice helper; the dual-track comment at L32 is load-bearing.
- `scripts/topology_doctor.py --navigation --task "Day0 split" --files src/signal/day0_signal.py` routes entry correctly from the repo's own nav tool.
