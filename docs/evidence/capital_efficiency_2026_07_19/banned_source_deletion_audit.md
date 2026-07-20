# Banned-source deletion feasibility audit — ECMWF OpenData mx2t3/mn2t3 and AIFS

```
# Created: 2026-07-20
# Method: read-only (sqlite3 -readonly), git log/blame, rg over src/tests/scripts, grep over
#         config, tail over logs/*.log. No code edited.
# Prior art: docs/evidence/mx2t3_decouple/{carrier_decouple_plan,consumer_classification,
#            download_chain,minimal_decouple_patch}.md (2026-06-17, READ-ONLY DESIGN at the time)
# Operator law: ECMWF OpenData mx2t3/mn2t3 ENS (0.25°) and AIFS — COMPLETELY DELETED, never
#            to be used again (reaffirmed 2026-07-20).
```

## 0. Headline verdict

**mx2t3/mn2t3 (ECMWF OpenData) is NOT deletable today. It is live, running right now, and one
lifecycle wire (day0 fail-closed fallback) still structurally depends on it.**

**AIFS is deletable today with zero lifecycle impact.** It is not scheduled anywhere, has not
written a row in over a month, the registry already marks it `tier=disabled` /
`trade_authority_status=BLOCKED`, and every residual module is a self-contained cluster with
callers only from offline scripts and its own tests.

The 2026-06-17 decouple plan (`carrier_decouple_plan.md`) WAS implemented — commit `8ab08b792`
("fix(live): remove replacement shadow authority from money path", 2026-06-17) landed all three
prescribed edits (A: FSR readiness fork onto `forecast_posteriors`; B: causal-cycle pin parses the
neutral `rmf-...` id; C: no-submit cert forecast authority forks onto
`_forecast_authority_payload_from_posterior`). This closed the FORECAST-lane dependency on
`ensemble_snapshots`/`source_run`/`source_run_coverage`. **But the DAY0 lane was explicitly scoped
out of that decouple** (per the plan's own §4 edge-case note), and day0 is live
(`edli_live_scope=forecast_plus_day0`). Day0 still falls back to `ensemble_snapshots.members_json`
whenever the raw_model_forecasts multimodel seed has fewer than 3 members — which makes mx2t3 a
**live safety-net dependency**, not a dead one.

Separately, and not identified in the June prior art: the live replacement/belief product itself
changed identity since June. The June docs called `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1`
(AIFS-anchored) the "neutral carrier." **That product is now stale (last computed 2026-06-18,
32 days ago) and is no longer the live product.** The current live product — read by the FSR, the
adapter, `staleness_cancel.py`, receipt provenance, and source-run identity — is
`openmeteo_ecmwf_ifs9_bayes_fusion_v1` (41,322 rows, computed as recently as 2026-07-20T00:45,
zero AIFS input in its materializer). This is good news for AIFS deletion (confirmed independently
below) but means the June plan's specific "neutral carrier" citation is stale documentation, not a
correctness problem — the actual A/B/C fork code reads `forecast_posteriors` generically (any
product row satisfying the fork's WHERE clause), not hard-coded to the AIFS-anchor product id.

---

## 1. Runtime liveness

### mx2t3 / mn2t3 (ECMWF OpenData): **LIVE, ACTIVELY RUNNING**

- `state/zeus-forecasts.db` (read-only), `ensemble_snapshots` grouped by `(dataset_id, model_version)`:
  - `ecmwf_opendata_mx2t3_local_calendar_day_max` / `ecmwf_ens`: max `available_at`
    2026-07-19T20:05:34Z, max `fetch_time` 2026-07-19T20:24:29Z, 45,730 rows.
  - `ecmwf_opendata_mn2t3_local_calendar_day_min` / `ecmwf_ens`: max `available_at`
    2026-07-19T20:25:34Z, max `fetch_time` 2026-07-19T20:28:05Z, 44,156 rows.
- `source_run` (source_id=`ecmwf_open_data` only, confirmed): latest `recorded_at`
  2026-07-19 20:28:10 (246 rows total in the table).
- `source_run_coverage` (source_id=`ecmwf_open_data` only, confirmed): latest `recorded_at`
  2026-07-19 20:28:19 (87,280 rows total).
- `logs/zeus-forecast-live.log`, 2026-07-19 20:28:13 onward: the **forecast_live daemon** (not
  `ingest_main`) currently owns OpenData ingest —
  `Added job "_opendata_mx2t6_cycle"`, `"_opendata_mx2t6_cycle_12z"`, `"_opendata_mn2t6_cycle"`
  (12z variant implied), `"_opendata_safe_cycle_poll"` (runs every 5 min),
  `"_opendata_startup_catch_up"`. Every 5-minute poll logs
  `forecast-live OpenData mx2t6_high current source cycle already journaled:
  ecmwf_open_data:mx2t6_high:2026-07-19T12Z` — i.e. it is actively checking and had already
  journaled the 12Z cycle today. `src/ingest/forecast_live_daemon.py:861-989` defines these five
  job functions and their registration; `_opendata_mx2t6_cycle_12z` / `_opendata_mn2t6_cycle_12z`
  are a **more frequent cadence than the `src/ingest_main.py` 07:30/07:35 UTC daily cron** described
  in the June `download_chain.md` — the daemon has grown MORE opendata scheduling since June, not
  less.
- Config: the `disable_legacy_opendata_forecast_live_jobs` key that `download_chain.md` cited as
  the kill-switch was **deleted from `config/settings.json`** by commit `12fb0b2dc`
  ("refactor(gate-0): strip legacy bias maze ... remove 6 dead config flags") as part of an
  unrelated bias-maze cleanup — collateral removal of the only documented disable path, not a
  deliberate re-enable. With the key gone and `ZEUS_FORECAST_LIVE_OWNER` defaulting to
  `forecast_live` in this deployment (per the live logs), the daemon's own opendata jobs run
  unconditionally.

**Conclusion: mx2t3/mn2t3 ingest was never stopped. It is fetching, parsing, and writing new rows
into `ensemble_snapshots`/`source_run`/`source_run_coverage` right now, on a 5-minute poll cadence,
independent of and in addition to the FSR/spine/no-submit decoupling that was landed for the
FORECAST lane.**

### AIFS: **NOT SCHEDULED, NOT WRITING, STALE OVER A MONTH**

- Zero AIFS references in `src/main.py`, `src/ingest_main.py`, or any `src/ingest/*.py` scheduler
  registration (`rg -n "aifs" -g '*.py'` on all three — zero hits).
- `forecast_posteriors` product `openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1` (the only
  product with a populated `aifs_source_run_id` — 3,610/3,610 rows non-null): max `computed_at`
  **2026-06-18T11:52:38Z — 32 days stale** relative to today (2026-07-20). No table in
  `state/zeus-forecasts.db` has "aifs" in its name (`.tables` grep: zero).
- `src/data/forecast_source_registry.py:321-329`: the `ecmwf_aifs_ens` source spec is already
  `tier="disabled"`, `enabled_by_default=False`, `kind="experimental_ingest"`,
  `allowed_roles=("diagnostic",)`, `degradation_level="DIAGNOSTIC_NON_EXECUTABLE"`. The product spec
  (`:517-533`, label `"A1"`) is `trade_authority_status="BLOCKED"`, `training_allowed=False`. The
  registry has already self-quarantined AIFS; this predates today's audit.
- The live belief product `openmeteo_ecmwf_ifs9_bayes_fusion_v1` (materialized by
  `src/data/replacement_forecast_materializer.py`, `posterior_method="openmeteo_ecmwf_ifs9_bayes_fusion"`
  at lines 4388/4868) has **zero AIFS references anywhere in the materializer file** (`rg -n "aifs"
  -i` on the whole file: zero hits). AIFS cannot leak into the live q/σ through this path.

**Conclusion: AIFS has no scheduled job, no fresh writes, an already-BLOCKED registry status, and
zero presence in the live belief materializer. It is dead code today, not merely dormant.**

---

## 2. The three wires today (per `carrier_decouple_plan.md` §3, §6)

All three landed in commit **`8ab08b792`** (2026-06-17, "fix(live): remove replacement shadow
authority from money path"), confirmed by `git log -S` on each wire's signature symbol, and
`8ab08b792` is an ancestor of current HEAD.

| # | Wire | June-2026-06-17 state | Today's state | Evidence |
|---|---|---|---|---|
| 1 | FSR family-readiness (`forecast_snapshot_ready.py::scan_committed_snapshots`) | Riding on `source_run_coverage ⋈ source_run ⋈ ensemble_snapshots` (mx2t3-only) | **Re-sourced.** Forks on `forecast_posteriors` table existence (`forecast_snapshot_ready.py:841-861`): if present, reads the `ranked_posterior` CTE (`:1086` `JOIN forecast_posteriors AS fp`) and mints a neutral `rmf-<city>\|<target>\|<metric>\|<cycle_date>` snapshot id (`_POSTERIOR_SNAPSHOT_ID_PREFIX = "rmf-"`, `:44`). The legacy `ensemble_snapshots` JOIN (`:1225`) and `_FORECAST_TABLES = ("source_run","source_run_coverage","ensemble_snapshots")` (`:1542`) remain as the `else:` fallback only. | `src/events/triggers/forecast_snapshot_ready.py:35-44, 841-861, 933-951, 1086, 1157-1242, 1542` |
| 2 | Causal-cycle pin (`_spine_multimodel_members_for_event` → `_bound_forecast_snapshot_row_for_spine`) | Pinned exclusively on `ensemble_snapshots.snapshot_id = event.causal_snapshot_id` | **Re-sourced (B1+B2).** `_SPINE_RMF_SNAPSHOT_ID_PREFIX = "rmf-"` (`event_reactor_adapter.py:30676`); when `causal_snapshot_id` starts with `rmf-`, the cycle date is parsed directly from the id (`:30880-30883`) — no ensemble row touched. Falls to the legacy `_bound_forecast_snapshot_row_for_spine` ensemble pin only for legacy-shaped (pre-cutover) ids (`:30884-30889`), and to a B2 `raw_model_forecasts` MAX-cycle fallback if even that misses (`:30890-30894`). `_bound_forecast_snapshot_row_for_spine` itself (`:31411-31480`) still exists and still queries `ensemble_snapshots`, but is now reached only on the legacy-id branch. | `src/engine/event_reactor_adapter.py:30676, 30819-30914, 31411-31480` |
| 3 | No-submit cert forecast authority (`_forecast_authority_payload_and_clock` → `read_executable_forecast`) | Hard-bound `ensemble_snapshots.members_json` for every decision | **Re-sourced.** `_forecast_authority_payload_from_posterior` (`:20489-20489+`) builds the cert payload from `forecast_posteriors` + `raw_model_forecasts`; called at `:21054` inside `_forecast_authority_payload_and_clock` (`:21028`) on the non-day0 forecast-decision fork. The ensemble-backed path (`members_json_source="ensemble_snapshots.daily_extrema"`) remains for day0/legacy/flag-off. | `src/engine/event_reactor_adapter.py:20489, 21028, 21054` |

**Flag gating all three:** `_replacement_authority_enabled()` (`event_reactor_adapter.py:27883-27888`)
now reads `feature_flags.openmeteo_ecmwf_ifs9_bayes_fusion_live_enabled` (renamed from the June
`..._aifs_soft_anchor_trade_authority_enabled`) — confirmed **`true`** in
`config/settings.json:262`. All three forks are live-active, not dormant.

### The wire NOT covered — day0

`consumer_classification.md`'s ruling stands unchanged: `_market_analysis_from_event_snapshot` /
`_snapshot_members` reads `ensemble_snapshots.members_json` as the **day0 forecast-base seed**, and
day0 is live (`config/settings.json:74`, `"edli_live_scope": "forecast_plus_day0"`). The
`minimal_decouple_patch.md` re-sourcing (§1, "DAY0 q seed") made this a **fail-closed fallback**
only — day0 first tries a multimodel seed off `raw_model_forecasts`
(`_day0_seed_members_multimodel`), and only reads `_snapshot_members(snapshot)` (the cold
`ensemble_snapshots` row) when that multimodel seed has fewer than 3 members. That fallback branch
is the one live dependency that keeps mx2t3 non-deletable: if `ensemble_snapshots` stops updating
and a day0 family's multimodel seed ever comes up short, day0 has no seed to fail back to and the
family goes `SPINE_INPUTS_UNAVAILABLE`/no-decide instead of degrading gracefully. This was not
re-verified quantitatively in this audit (how often does the raw_model_forecasts seed actually
fall below 3 members for a live city?) — that frequency, not the code path, is the actual
pre-condition for a safe stop.

---

## 3. Belief path (forecast lane q/μ*/members)

Confirmed **zero** mx2t3/AIFS input on the live forecast-decision route:

- `_spine_multimodel_members_for_event` (`event_reactor_adapter.py:30819`) sources members from
  `raw_model_forecasts` exclusively (open-meteo multi-model: `ecmwf_ifs`, `gfs_global`,
  `icon_global`, `gem_global`, `jma_seamless`, `ukmo_*`, `ncep_nbm_conus`, `meteofrance_arome`, per
  the June `download_chain.md` census — re-confirmed unchanged by the materializer grep in §1).
  `data_versions` accepted: none named by data_version — the query keys on
  `(city, metric, target_date, source_cycle_time date)` against the native `raw_model_forecasts`
  schema, not a data_version allowlist.
- `qkernel_spine_bridge._served_predictive_inputs` reads only the `_edli_spine_*` keys that
  `_spine_multimodel_members_for_event` unconditionally overwrites — an ensemble-derived value can
  never reach a spine decision (per `consumer_classification.md` §CRITICAL RULING, re-verified: the
  overwrite call site and the bridge's read-only-those-keys behavior are unchanged in current tree).
- The live posterior product feeding the FSR/no-submit-cert forks is `openmeteo_ecmwf_ifs9_bayes_fusion_v1`
  (`replacement_forecast_readiness.py:17`, `staleness_cancel.py:57`, `forecast_snapshot_ready.py:31`,
  `event_reactor_adapter.py:20398`) — computed by `replacement_forecast_materializer.py`, zero AIFS
  references (§1).
- Day0 belief (`_market_analysis_from_event_snapshot`) now seeds from the same
  `raw_model_forecasts` multimodel pool via `_day0_seed_members_multimodel`
  (`minimal_decouple_patch.md` §1) with the cold-ensemble seed as fail-closed fallback only (§2
  above).

**No live q/μ*/members reader in either lane takes mx2t3 or AIFS as its primary input.** mx2t3
survives only as (a) the day0 fallback seed and (b) the legacy-id causal-cycle pin for in-flight
pre-cutover events.

---

## 4. AIFS residue — module-by-module

| Module | LOC | Production callers | Test surface |
|---|---|---|---|
| `src/data/ecmwf_aifs_grib_identity.py` | 165 | `ecmwf_aifs_grib_samples.py` only (intra-cluster) | `tests/test_ecmwf_aifs_grib_identity.py` (86) |
| `src/data/ecmwf_aifs_grib_samples.py` | 166 | `scripts/cycle_phase_offline_study.py:291` (deferred import inside a function) | `tests/test_ecmwf_aifs_grib_samples.py` (109) |
| `src/data/ecmwf_aifs_ens_request.py` | 222 | `scripts/cycle_phase_offline_study.py:521` (deferred import) | `tests/test_ecmwf_aifs_ens_request.py` (132) |
| `src/data/ecmwf_aifs_sampled_2t_localday.py` | 198 | intra-cluster only (imported by the other 3 data modules + the strategy module) | `tests/test_ecmwf_aifs_sampled_2t_localday.py` (128) |
| `src/strategy/ecmwf_aifs_sampled_2t_probabilities.py` | 435 | `scripts/validate_member_vote_smoothing_3way.py:58`, `scripts/cycle_phase_offline_study.py:259` (both deferred imports) | `tests/test_ecmwf_aifs_sampled_2t_probabilities.py` (303) |
| `src/strategy/openmeteo_ecmwf_ifs9_aifs_soft_anchor.py` | 277 | none found beyond its own `PRODUCT_ID` constant re-used by `replacement_forecast_calibration_block.py` as an identity string (not a functional call) | none dedicated found |

**Zero `INSERT`/`CREATE TABLE`/`execute(` writes in `ecmwf_aifs_grib_identity.py` or
`ecmwf_aifs_grib_samples.py`** — these are pure GRIB-parsing library functions with no DB
footprint; nothing to orphan.

**Every production (non-test, non-script) caller is `scripts/*` — none is `src/main.py`,
`src/ingest_main.py`, or any daemon.** `scripts/cycle_phase_offline_study.py` (1,011 LOC) and
`scripts/validate_member_vote_smoothing_3way.py` (591 LOC) are offline research/validation tools
that import the AIFS modules lazily inside functions (`# noqa: PLC0415`), consistent with
"run manually for analysis," not a scheduled job. `scripts/measure_fusion_aifs_drop_performance.py`
(133 LOC) is a standalone backtest/measurement script — confirmed to have no scheduler entry.

**Non-functional residue (identity strings only, no data flow):**
`src/data/replacement_forecast_calibration_block.py:17-18,24-25` defines `AIFS_SOURCE_ID` /
`AIFS_PRODUCT_ID` and folds them into the `_REPLACEMENT_SOURCE_IDS`/`_REPLACEMENT_PRODUCT_IDS`
allow-sets used by a generic "calibration authority can't reuse baseline lineage" guard. It never
reads AIFS data — it only recognizes the AIFS product id as one of three historically-valid
"replacement family" identities for validation purposes. Deleting the two constants requires
narrowing this guard's allow-set to the two still-live identities
(`REPLACEMENT_PRODUCT_ID`/`OPENMETEO_ANCHOR_PRODUCT_ID`) — trivial, but touches shared code.

`src/data/forecast_source_registry.py:321-329` (source spec) and `:517-533` (product spec, label
`"A1"`) are registry rows already flagged disabled/blocked (§1). Deleting them removes two dict
entries; confirm no test asserts on registry completeness/count (`test_no_internal_version_suffixes.py`
and the registry's own tests should be checked at delete time — not scoped in this audit).

---

## 5. Calendar / config

`config/source_release_calendar.yaml` has two `ecmwf_open_data` entries
(`ecmwf_open_data_mx2t6_high` at line 3, `ecmwf_open_data_mn2t6_low` at line 50) — these are the
mx2t3/mn2t3 tracks (the "144h honesty fix" the task brief references, at `download_release_lag_hours`
and step-hour comments lines 29-31 / 76-78). **No AIFS entry exists in this calendar** — confirmed,
zero AIFS hits in the file.

Since wire 1-3 (FORECAST lane) are re-sourced but **the day0 fallback (§2) is not**, the calendar
entry + the two `_opendata_*_cycle`/`_opendata_*_cycle_12z` fetch jobs + `STEP_HOURS`/
`OPENDATA_MAX_STEP_HOURS` machinery in `src/data/ecmwf_open_data.py` **must stay live** until the
day0 multimodel-seed floor is verified to never fall below 3 members for any live city, or day0 is
given its own non-ensemble fallback. **They do not join the deletion list today.**

---

## 6. The deletion checklist (dependency order)

### Can delete TODAY, zero lifecycle impact (AIFS)

1. **`src/data/ecmwf_aifs_grib_identity.py`** (165 LOC) + **`tests/test_ecmwf_aifs_grib_identity.py`** (86 LOC).
   Delete `ecmwf_aifs_grib_samples.py`'s import of it in the same change (see #2).
2. **`src/data/ecmwf_aifs_grib_samples.py`** (166 LOC) + **`tests/test_ecmwf_aifs_grib_samples.py`** (109 LOC).
   Requires updating `scripts/cycle_phase_offline_study.py:291` (drop the deferred import + whatever
   analysis branch consumes it) in the same change, or the script breaks at that call site.
3. **`src/data/ecmwf_aifs_ens_request.py`** (222 LOC) + **`tests/test_ecmwf_aifs_ens_request.py`** (132 LOC).
   Same caveat: `scripts/cycle_phase_offline_study.py:521` needs its deferred import removed.
4. **`src/data/ecmwf_aifs_sampled_2t_localday.py`** (198 LOC) + **`tests/test_ecmwf_aifs_sampled_2t_localday.py`** (128 LOC).
   Delete last in the data-module group — it's the shared base the other three import.
5. **`src/strategy/ecmwf_aifs_sampled_2t_probabilities.py`** (435 LOC) +
   **`tests/test_ecmwf_aifs_sampled_2t_probabilities.py`** (303 LOC). Requires updating
   `scripts/validate_member_vote_smoothing_3way.py:58` and
   `scripts/cycle_phase_offline_study.py:259` (both deferred imports, both function-local).
6. **`src/strategy/openmeteo_ecmwf_ifs9_aifs_soft_anchor.py`** (277 LOC). No caller found beyond its
   own module constant. Verify with one more targeted grep at delete time
   (`rg "openmeteo_ecmwf_ifs9_aifs_soft_anchor" --type py`, excluding the file itself) before
   removing, since this audit did not exhaustively trace every reference to its internal functions.
7. **`scripts/measure_fusion_aifs_drop_performance.py`** (133 LOC) — standalone, unscheduled,
   verify zero other script imports it (not checked in this audit) then delete outright.
8. **Registry entries**: `src/data/forecast_source_registry.py:321-329` (`ecmwf_aifs_ens` source
   spec) and `:517-533` (`"A1"` product spec). Check `test_no_internal_version_suffixes.py` and any
   registry-completeness test before removing — not confirmed safe in this audit, only that the
   entries themselves are inert.
9. **`src/data/replacement_forecast_calibration_block.py:17-18,24-25`** — narrow
   `_REPLACEMENT_SOURCE_IDS`/`_REPLACEMENT_PRODUCT_IDS` to drop `AIFS_SOURCE_ID`/`AIFS_PRODUCT_ID`.
   Touches a shared guard used by the still-live replacement calibration path — do this LAST in the
   AIFS batch, after confirming no calibration row in flight still carries the AIFS identity
   (check `forecast_posteriors` rows with `aifs_source_run_id` non-null are all ≥32 days old and
   none are mid-flight in an open decision — the 32-day staleness already found in §1 makes this
   very likely safe, but wasn't exhaustively cross-checked against `open_positions`/pending events
   in this audit).
10. **`scripts/cycle_phase_offline_study.py`** (1,011 LOC) and
    **`scripts/validate_member_vote_smoothing_3way.py`** (591 LOC) themselves are NOT AIFS-only —
    they do broader cycle-phase / member-vote analysis and import AIFS as one of several inputs.
    Do not delete the scripts; only remove their AIFS-specific branches when items 2/3/5 land.

**Batch size: ~1,463 LOC of `src/`, ~758 LOC of dedicated tests, 3 shared-file edits
(calibration_block, registry, 2 offline scripts), 1 standalone script deletion (133 LOC). No DB
migration needed — zero tables/rows to orphan (§4).**

### Needs a re-source FIRST (mx2t3/mn2t3) — do not delete yet

1. **Quantify the day0 multimodel-seed floor.** Before touching anything: measure, across live
   day0 families over some trailing window, how often `_day0_seed_members_multimodel` returns
   fewer than 3 members (which trips the `_snapshot_members(snapshot)` ensemble fallback in
   `_market_analysis_from_event_snapshot`). This number — not the code shape — is the actual
   go/no-go gate. Not measured in this audit (would require a DB query correlating day0 decision
   receipts against the multimodel seed count; out of scope for a read-only structural audit).
2. **If the floor is empirically never hit live:** the fallback is dead-in-practice and can be
   removed from `_market_analysis_from_event_snapshot` (fail-closed to `SPINE_INPUTS_UNAVAILABLE`
   instead of the ensemble seed), which then fully severs day0 from `ensemble_snapshots.members_json`
   as belief. At that point `_bound_forecast_snapshot_row_for_spine` (`event_reactor_adapter.py:31411-31480`)
   and `_forecast_snapshot_row_for_event`'s `allow_latest=True` day0 branch become dead code too, and
   `ensemble_snapshots`/`source_run`/`source_run_coverage` writers can be cut.
3. **If the floor IS occasionally hit:** day0 needs a non-ensemble degrade path (e.g. widen the
   multimodel lookback window, or accept a documented "day0 goes no-decide on thin multimodel
   coverage" behavior change) before mx2t3 can stop. This is an operator/product decision, not a
   mechanical deletion — flagged here, not resolved.
4. Only after (2) or (3) resolves: stop the scheduler jobs
   (`_opendata_mx2t6_cycle`, `_opendata_mx2t6_cycle_12z`, `_opendata_mn2t6_cycle`,
   `_opendata_mn2t6_cycle_12z`, `_opendata_safe_cycle_poll`, `_opendata_startup_catch_up` in
   `src/ingest/forecast_live_daemon.py:861-989`, and the `ingest_main.py` equivalents at
   `:1727-1751` if `ZEUS_FORECAST_LIVE_OWNER` ever reverts to `ingest_main`), delete
   `src/data/ecmwf_open_data.py` (the `collect_open_ens_cycle` chain, ~1,941 lines per the June
   file-map), `scripts/extract_open_ens_localday.py`, `scripts/ingest_grib_to_snapshots.py`'s
   opendata call path, the two `config/source_release_calendar.yaml` entries, and the ~19 dedicated
   opendata tests enumerated in `download_chain.md` §3. TIGGE writes to the same
   `ensemble_snapshots` table under different `data_version` strings and is unaffected either way
   (separate writer, per `download_chain.md` §2) — do not conflate the two when scoping the delete.

---

## 7. DB-artifact list (report only — no deletion of data performed)

Tables that become orphaned once mx2t3 ingest actually stops (not today):

| Table | Current row count / freshness | Becomes |
|---|---|---|
| `ensemble_snapshots` (opendata rows: `dataset_id IN (ecmwf_opendata_mx2t3_local_calendar_day_max, ecmwf_opendata_mn2t3_local_calendar_day_min, ecmwf_opendata_mx2t6_local_calendar_day_max, ecmwf_opendata_mn2t6_local_calendar_day_min)`) | 45,730 + 44,156 + 1,342 + 508 rows, fresh as of 2026-07-19T20:2x | Frozen at last-fetched row; TIGGE-sourced rows in the same table (`tigge_*` data_versions, 384,970+384,186+348,690+16+16+100 rows) keep writing independently |
| `source_run` (`source_id='ecmwf_open_data'`) | 246 rows, latest 2026-07-19 20:28:10 | Frozen; sole writer stops |
| `source_run_coverage` (`source_id='ecmwf_open_data'`) | 87,280 rows, latest 2026-07-19 20:28:19 | Frozen; sole writer stops |
| `forecast_posteriors` (`product_id='openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1'`, AIFS-anchored) | 3,610 rows, **already frozen since 2026-06-18** | No change from an AIFS delete — already dead data; historical row only |

No AIFS-specific table exists (§1) — nothing to orphan on the AIFS side beyond the already-frozen
`forecast_posteriors` rows above, which predate this audit and are not proposed for deletion here
(data deletion is operator-visible per the task brief; this is a report-only line item).

---

## 8. What this audit did not verify (explicit gaps)

- Did not measure day0's actual multimodel-seed-floor hit rate (§6, item needing a re-source) —
  this is the single number that gates the mx2t3 deletion, and it was out of scope for a read-only
  structural pass. Recommend a follow-up query against `source_health`/day0 decision receipts.
- Did not exhaustively trace every internal function reference for
  `openmeteo_ecmwf_ifs9_aifs_soft_anchor.py` (item 6 in the can-delete-today list) — flagged for a
  final grep at delete time.
- Did not check whether `test_no_internal_version_suffixes.py` or other registry-completeness tests
  assert on the AIFS registry entries' presence — flagged in §6 item 8.
- Did not check `open_positions`/in-flight pending events for any row still carrying the AIFS
  provenance identity before recommending the calibration-block constant removal (§6 item 9) — the
  32-day staleness makes it very likely safe but wasn't cross-checked against open exposure.
