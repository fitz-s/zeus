# Per-City Forecast Stall — 2026-06-23 ~04:32 UTC
# Root-cause audit: Moscow/Seoul 2026-06-24.high stale vs Warsaw/Shenzhen fresh

**Observation**: forecast_posteriors for target_date=2026-06-24 high at ~04:32 UTC:
- Warsaw computed_at 04:25, Shenzhen 04:10 — FRESH
- Moscow last computed_at 01:43, Seoul 02:08 — 2-3h STALE

---

## Q1. How per-city materialize requests are generated; what makes a city eligible

**Pipeline summary** (code path, all in one cycle):

1. `_replacement_forecast_materialize_job` fires every 5 min via APScheduler (`replacement_production` executor — log line `apscheduler.executors.replacement_production ... _replacement_forecast_materialize_job`).
2. It calls `process_replacement_forecast_live_materialization_queue` → `_process_replacement_forecast_live_materialization_queue_locked` (`src/data/replacement_forecast_live_materialization_queue.py:638`).
3. That first calls `discover_replacement_forecast_materialization_seeds` (`src/data/replacement_forecast_seed_discovery.py:322`). Discovery:
   a. Loads raw manifests from `state/replacement_forecast_live/raw_manifests/`.
   b. Opens `zeus-forecasts.db`.
   c. Calls `build_replacement_forecast_current_target_plan` to get the current scopes.
   d. Runs `_candidate_targets` (`src/data/replacement_forecast_seed_discovery.py:235`) which SELECTs from `source_run_coverage` WHERE `source_id='ecmwf_open_data'` AND `target_local_date >= today`, **filtered by `skip_covered_sql`** (`src/data/replacement_forecast_seed_discovery.py:250-288`).
4. `skip_covered_sql` marks a (city, target_date, metric) as **covered** — and therefore **skipped** — when **BOTH** conditions hold:
   - `EXISTS` a tradeable-grade posterior in `forecast_posteriors` with `dependency_source_run_ids_json.baseline_b0 = c.source_run_id` (the max ecmwf_open_data source_run_id for that scope)
   - `EXISTS` a non-expired `readiness_state` row with the same `baseline_b0` dependency and `expires_at > now()`
5. If a scope passes through (not covered), a seed JSON is written to `state/replacement_forecast_live/seeds/`. Seed→request→materialize subprocess (`scripts/materialize_replacement_forecast_live.py`).

**Eligible = NOT (has tradeable posterior with current baseline_b0 AND has live readiness with same baseline_b0).**

Source: `src/data/replacement_forecast_seed_discovery.py:243-311`.

---

## Q2. Why Moscow + Seoul dropped out while Warsaw/Shenzhen kept getting seeds

**Root cause: Moscow and Seoul materialized successfully at 01:43 and 02:08 UTC respectively, writing a tradeable posterior + live readiness row keyed to `baseline_b0 = ecmwf_open_data:mx2t6_high:2026-06-22T12Z`. That coverage entry — expiring at 18:00 UTC — caused every subsequent seed_discovery tick (01:43 through 18:00) to skip them as "already covered". Warsaw and Shenzhen had NOT yet been successfully materialized at the 12Z baseline when seed_discovery ran at 04:10/04:25 UTC, so they were eligible and got new seeds that succeeded.**

**Detailed evidence chain:**

### Step 1 — 18:38 UTC Jun 22: cycle_advance enqueues ALL 4 cities at 12Z
`cycle_advance_enqueues` (zeus-forecasts.db):
```
Moscow  high 2026-06-24 target_cycle=2026-06-22T12Z enqueued_at=2026-06-22T18:38:06
Seoul   high 2026-06-24 target_cycle=2026-06-22T12Z enqueued_at=2026-06-22T18:38:06
Shenzhen high 2026-06-24 target_cycle=2026-06-22T12Z enqueued_at=2026-06-22T18:43:06
Warsaw  high 2026-06-24 target_cycle=2026-06-22T12Z enqueued_at=2026-06-22T18:43:06
```

### Step 2 — 19:17–19:32 UTC Jun 22: ALL 4 materialize requests fail

All 4 cities had their T183804/T184305 seeds converted to requests and run through the materializer. **All failed with BLOCKED (returncode=1)**:

Moscow: `state/replacement_forecast_live/failed/Moscow.2026-06-24.high.20260622T183804Z.20260622T191734Z.json.receipt.json`
```
"stderr": "persisted current single_runs capture MISSING for Moscow high 2026-06-24 lead=2 cycle=2026-06-22T12:00:00+00:00"
"stdout": {"status": "BLOCKED", "reason_codes": ["REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET"]}
```
Seoul: `state/replacement_forecast_live/failed/Seoul.2026-06-24.high.20260622T183804Z.20260622T192236Z.json.receipt.json` — same BLOCKED reason, lead=1.
Shenzhen: `state/replacement_forecast_live/failed/Shenzhen.2026-06-24.high.20260622T184305Z.20260622T192240Z.json.receipt.json` — same BLOCKED reason, lead=1.
Warsaw: `state/replacement_forecast_live/failed/Warsaw.2026-06-24.high.20260622T184305Z.20260622T193237Z.json.receipt.json` — same BLOCKED reason, lead=2.

**The raw_model_forecasts single_runs rows for the 2026-06-22T12Z cycle had NOT arrived yet** when these ran (captured_at = 2026-06-23T01:15:58 per DB; requests ran at ~19:17-19:32 Jun 22, ~6h before capture).

### Step 3 — 01:13 UTC Jun 23: raw_model_forecasts captured for all cities

DB confirms: `raw_model_forecasts WHERE city IN (Moscow,Seoul,Warsaw,Shenzhen) AND target_date=2026-06-24 AND source_cycle_time LIKE '2026-06-22T12%'` — all rows have `captured_at = "2026-06-23T01:15:58.105852+00:00"`. Same timestamp for all 4.

### Step 4 — 01:38 UTC Jun 23: materialize job fires; seed_discovery writes Moscow + Seoul seeds (budget=10)

At 01:38 UTC, `_replacement_forecast_materialize_job` fires. Seed_discovery runs `_candidate_targets`:
- max(source_run_coverage.computed_at) for all 4 cities on 2026-06-24.high = `ecmwf_open_data:mx2t6_high:2026-06-22T12Z` (computed 2026-06-23T01:13).
- skip_covered check: `forecast_posteriors` for Moscow/Seoul/Shenzhen/Warsaw with baseline_b0=2026-06-22T12Z? — **NONE** (all 4 prior posteriors have baseline_b0=2026-06-18T12Z). → All 4 are ELIGIBLE.
- But seed_discovery limit=10 per tick. With ~114 total scopes, only 10 are written. The sort is `(target_date ASC, city ASC, metric ASC)` (`src/data/replacement_forecast_seed_discovery.py:398-403`). Target 2026-06-24 comes after many earlier dates. Moscow and Seoul happen to fall within the budget; Shenzhen and Warsaw may not (depends on total eligible).

Seeds written at 01:38 tick include Moscow.2026-06-24.high (file: `seeds_processed/Moscow.2026-06-24.high.20260622T183804Z...` and the cycle-advance seed `Moscow.2026-06-24.high.20260623T014331Z`).

### Step 5 — 01:43 UTC Jun 23: Moscow materialize SUCCEEDS; 02:08 UTC Seoul SUCCEEDS

`state/replacement_forecast_live/processed/...Moscow.2026-06-24.high.20260623T014331Z.20260623T014333Z.json.receipt.json` receipt status: READY, returncode 0.
Posterior written: `forecast_posteriors WHERE city=Moscow AND target_date=2026-06-24 AND computed_at=2026-06-23T01:43:31 AND dependency baseline_b0=ecmwf_open_data:mx2t6_high:2026-06-22T12Z` (DB confirmed, q_lcb_basis=fused_center_bootstrap_p05).
Readiness written: `readiness_state WHERE city=Moscow AND target_date=2026-06-24 AND dep_baseline=2026-06-22T12Z AND expires_at=2026-06-23T18:00:00+00:00` (DB confirmed).

Seoul at 02:08 UTC analogously: `forecast_posteriors computed_at=2026-06-23T02:08:31`.

### Step 6 — 02:10–04:10 UTC: seed_discovery skips Moscow/Seoul; eventually writes Shenzhen/Warsaw

From 01:43 UTC onward, every seed_discovery tick checks Moscow 2026-06-24.high:
- `forecast_posteriors` has a row with baseline_b0=2026-06-22T12Z (computed 01:43) ✓
- `readiness_state` has a live row with same baseline_b0, expires_at=18:00:00 UTC > now ✓
→ **BOTH conditions met → COVERED → SKIPPED** (`src/data/replacement_forecast_seed_discovery.py:254-288`).

Same for Seoul from 02:08 UTC.

Shenzhen and Warsaw: No posterior with baseline_b0=2026-06-22T12Z yet (their T184305 seeds failed). So they remain eligible across subsequent ticks. Budget contention (10 per tick across ~114 scopes) means they may not be written until a tick where they fall within the top-10 by (target_date ASC, city ASC).

Shenzhen seed written at 04:10 UTC: `seeds_processed/Shenzhen.2026-06-24.high.20260623T041020Z.20260623T041023Z.json` (computed_at=2026-06-23T04:10).
Warsaw seed written at 04:25 UTC: `seeds_processed/Warsaw.2026-06-24.high.20260623T042520Z.20260623T042523Z.json` (computed_at=2026-06-23T04:25).
Both succeed: `processed/Shenzhen..20260623T041020Z.20260623T041032Z.json.receipt.json` returncode=0; `processed/Warsaw..20260623T042520Z.20260623T042532Z.json.receipt.json` returncode=0.

**Moscow and Seoul remain LOCKED OUT until 18:00 UTC when the readiness row expires.**

---

## Q3. Live queue dirs — Moscow/Seoul in failed/ absent from requests/

`state/replacement_forecast_live/requests/` at time of observation: **empty** (2 entries, both `.` and `..`). Moscow/Seoul have no pending requests.

Moscow 2026-06-24.high files in `failed/`:
```
Moscow.2026-06-24.high.20260622T041843Z.20260622T041852Z.json.receipt.json  (Jun 21 23:18) — returncode 2, TypeError
Moscow.2026-06-24.high.20260622T042343Z.20260622T083359Z.json.receipt.json  (Jun 22 03:33) — returncode 2, TypeError
Moscow.2026-06-24.high.20260622T183804Z.20260622T191734Z.json.receipt.json  (Jun 22 14:17) — returncode 1, BLOCKED persisted capture MISSING
```
Seoul 2026-06-24.high:
```
Seoul.2026-06-24.high.20260622T183804Z.20260622T192236Z.json.receipt.json   (Jun 22 14:22) — returncode 1, BLOCKED persisted capture MISSING
```

The earliest Moscow failures (returncode=2) show a different error: `"float() argument must be a string or a real number, not 'NoneType'"` — this is a `TypeError` from the materialize script at `scripts/materialize_replacement_forecast_live.py:159` (`float(payload["latitude"])` or `float(payload["longitude"])`), suggesting a different seed format issue on Jun 21 (pre-manifest-based seeds), not the capture issue.

The Jun 22 18:38 UTC failures are all `BLOCKED` / `REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET` — confirmed capture-missing at 2026-06-22T12Z cycle.

**No Moscow/Seoul 2026-06-24 files in `seeds_failed/`** — seeds generated fine; failures are in the materialize subprocess.

---

## Q4. Forecast-live log: exact reason Moscow/Seoul materialize stopped

From `logs/zeus-forecast-live.log`, the materialize failure receipts (stderr) for Moscow and Seoul at cycle 18:38 UTC Jun 22:

**Moscow**:
```
replacement_0_1 BAYES_PRECISION_FUSION fusion: persisted current single_runs capture MISSING
for Moscow high 2026-06-24 lead=2 cycle=2026-06-22T12:00:00+00:00
-> single-anchor fallback (no network fetch in q path)
```
Result: `status=BLOCKED`, `posterior_id=null`, `readiness_id=null`.
Source code: `src/data/replacement_forecast_materializer.py:1188-1197` — when `conn is not None and not persisted_current`, the function logs this warning and returns `None`, which propagates to `REPLACEMENT_LIVE_POSTERIOR_REQUIREMENTS_NOT_MET` at line 2860.

**Seoul**:
```
replacement_0_1 BAYES_PRECISION_FUSION fusion: persisted current single_runs capture MISSING
for Seoul high 2026-06-24 lead=1 cycle=2026-06-22T12:00:00+00:00
-> single-anchor fallback (no network fetch in q path)
```
Identical BLOCKED outcome.

Both were run at ~18:38-19:22 UTC Jun 22, before `raw_model_forecasts` rows for the 12Z cycle arrived (captured_at = 2026-06-23T01:15:58 UTC — ~6h later).

---

## Q5. Is upstream source data fresh for Moscow/Seoul vs Warsaw/Shenzhen at 2026-06-22T12Z?

**All 4 cities have identical `raw_model_forecasts` presence at the 12Z cycle** (DB query):
```
Moscow   target_date=2026-06-24 source_cycle_time=2026-06-22T12Z models=[ecmwf_ifs, icon_eu, icon_global, ukmo] captured_at=2026-06-23T01:15:58 forecast_value_c=non-null
Seoul    target_date=2026-06-24 source_cycle_time=2026-06-22T12Z models=[ecmwf_ifs, icon_global, ukmo] (×2 rows each — high+low) captured_at=2026-06-23T01:15:58 non-null
Shenzhen target_date=2026-06-24 source_cycle_time=2026-06-22T12Z models=[ecmwf_ifs, icon_global, ukmo] captured_at=2026-06-23T01:15:58 non-null
Warsaw   target_date=2026-06-24 source_cycle_time=2026-06-22T12Z models=[ecmwf_ifs, icon_eu, icon_global, ukmo] captured_at=2026-06-23T01:15:58 non-null
```

All 4 cities also have `source_run_coverage` entries for `ecmwf_open_data:mx2t6_high:2026-06-22T12Z` computed at 2026-06-23T01:13. No city is missing upstream data.

**The stall is NOT caused by missing upstream source data for Moscow/Seoul.** All 4 cities had identical data availability; the divergence is entirely downstream in the coverage-skip gate.

---

## Root Cause

**Category (b): seed-discovery eligibility gate — specifically the `skip_covered_sql` in `_candidate_targets`.**

**The mechanism**: Moscow succeeded in materializing (with 12Z baseline) at 01:43 UTC; Seoul at 02:08 UTC. Each success wrote a `forecast_posteriors` row with `baseline_b0 = ecmwf_open_data:mx2t6_high:2026-06-22T12Z` and a `readiness_state` row with `expires_at = 2026-06-23T18:00:00`. The skip_covered_sql (`src/data/replacement_forecast_seed_discovery.py:254-288`) then marks those scopes as **COVERED** for every tick from 01:43–18:00 UTC — a 16h window. No new seeds are generated for them regardless of elapsed time since the posterior.

**Why Moscow/Seoul specifically**: They were first in alphabetical sort order at the 01:38 UTC tick when seed_discovery had budget to write seeds; they succeeded. Shenzhen/Warsaw, also eligible that tick, did not get a budget slot until later ticks (04:10, 04:25 UTC). When they materialized successfully, they were NOT yet covered, so subsequent ticks could not mark them covered — until THEY too succeeded and fell into the same trap.

**The fundamental design tension**: `readiness_state.expires_at = 18:00 UTC` is a fixed 3h TTL keyed to the settlement window, not to forecast cycle refresh cadence. A city that materializes early in the day (01:43 UTC) is locked out of re-materialization for ~16h, even though a new OM9 anchor (or updated bayes precision capture) could produce a better posterior in between. The reactor re-decides using the 01:43 posterior for those 16h.

**Fix seam**: `src/data/replacement_forecast_seed_discovery.py:276-287` — the `readiness_state` arm of `skip_covered_sql`. The `expires_at > strftime('%Y-%m-%dT%H:%M:%S', 'now')` check is the gate. A shorter TTL on `readiness_state` (e.g. 3h from materialize time, not 3h before settlement) would cause the coverage to lapse sooner, making the scope eligible for re-seed earlier. Alternatively, the `_candidate_targets` query could be extended with a max-age-since-posterior guard so cities whose posterior is older than N minutes are re-eligible regardless of readiness.

Secondary fix seam: `src/data/replacement_forecast_materializer.py:2838-2860` — the `expires_at` value written to `readiness_state`. Currently hard-coded to `18:00 UTC` (settlement window boundary). Shortening this to e.g. `computed_at + 3h` would make coverage lapse ~3h after materialization, allowing normal 5-min refresh cadence to re-materialize.
