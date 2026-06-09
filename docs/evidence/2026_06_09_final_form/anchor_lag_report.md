# Root Cause Report: `deterministic_forecast_anchors` stuck at 2026-06-08T18

Investigated 2026-06-09 ~18:00 UTC. READ-ONLY: no files edited, no daemons touched.

---

## (i) Producer Module + Scheduling Chain

**Table writer:** `/Users/leofitz/zeus/src/data/replacement_forecast_materializer.py:371`
`INSERT OR IGNORE INTO deterministic_forecast_anchors (...)` — the anchor row is written during
the light seed→materialize pass, from an `OpenMeteoIfs9LocalDayAnchor` (source_id
`openmeteo_ecmwf_ifs_9km`, defined `/Users/leofitz/zeus/src/data/openmeteo_ecmwf_ifs9_anchor.py:17`;
product `openmeteo_ecmwf_ifs9_deterministic_anchor_v1`, fetched from
`https://single-runs-api.open-meteo.com/v1/forecast?...models=ecmwf_ifs&run=<cycle>` — line 16).

**Freshness owner (raw-input download):** the anchor can only be as fresh as the raw artifact
manifests in `state/replacement_forecast_shadow/raw_manifests/`. Those are produced by:

- `/Users/leofitz/zeus/src/data/replacement_forecast_production.py:229`
  `_replacement_forecast_download_cycle`
- → `production.py:122` `_download_replacement_forecast_current_targets_if_needed`
- → `/Users/leofitz/zeus/scripts/download_replacement_forecast_current_targets.py`
  `download_current_target_raw_inputs` (writes manifests + `raw_forecast_artifacts` rows).

**Scheduling (daemon = com.zeus.forecast-live):**
`/Users/leofitz/zeus/src/ingest/forecast_live_daemon.py:973-1027`
`_register_replacement_forecast_production_jobs`:

| Job | Trigger | Lane |
|---|---|---|
| `replacement_forecast_download` (heavy, incl. the ifs9km anchor fetch) | cron `hour='14,2', minute=10` UTC (daemon.py:988-998); hours derived at :959-966 from `download_release_lag_hours=14.0` → 00Z fires at 14:10 UTC, 12Z at 02:10 UTC | `replacement_download` |
| `replacement_forecast_download_startup_catch_up` | one-shot, start+90s (:1001-1010) | `replacement_download` |
| `replacement_forecast_shadow_materialize` (writes the anchor table) | interval 5 min (:1012-1021) | `replacement_production` |

**Cycle selection:** `scripts/download_replacement_forecast_current_targets.py:53-66`
`_parse_cycle`: `cutoff = now − release_lag_hours(=14h)`, rounded DOWN to nearest {00,06,12,18}Z.

**Flag gating (all ON — flags are NOT the problem):**
- `config/settings.json:296` `"openmeteo_ecmwf_ifs9_aifs_soft_anchor_shadow_enabled": true`
- `config/settings.json:312` `"download_current_targets_enabled": true`
- `config/settings.json:315` `"download_release_lag_hours": 14.0`

**Bookkeeping note:** the anchor producer does NOT use the `source_run` table (it contains only
`ecmwf_open_data`). Its bookkeeping is `raw_forecast_artifacts` rows + manifest files.

---

## (ii) Timeline of Producer Activity (log timestamps local, UTC−5)

Only **three** `current-target download report` lines exist in the entire
`logs/zeus-forecast-live.log`:

1. Line 402694:
   `2026-06-08 21:02:14,856 [zeus.replacement_forecast_production] INFO: replacement forecast current-target download report: {'status': 'CURRENT_TARGET_RAW_INPUTS_DOWNLOADED', 'cycle': '2026-06-08T06:00:00+00:00', ... 'written_manifest_count': 99 ...}`

2. Line 407295 (boot catch-up fired 03:49 local / 08:49 UTC; `_parse_cycle(08:49−14h)` → 06-08T18):
   `2026-06-09 04:02:55,927 ... 'status': 'CURRENT_TARGET_RAW_INPUTS_DOWNLOADED', 'cycle': '2026-06-08T18:00:00+00:00', 'written_manifest_count': 156`

3. Line 411697 (boot catch-up after the 05:25 local daemon restart; 10:26 UTC − 14h → 06-08T18):
   `2026-06-09 05:42:50,468 ... 'cycle': '2026-06-08T18:00:00+00:00', 'written_manifest_count': 156`

Current daemon (started 08:51 local / 13:51 UTC):

- Line 415225: `2026-06-09 08:51:12,781 [zeus.forecast_live] INFO: replacement-forecast production jobs registered (download cron hour=14,2 min=10 + boot catch-up; materialize interval=5min; lane=replacement_production; shadow_enabled=True)`
- Line 415244: boot catch-up runs `08:52:42` local (13:52 UTC; `_parse_cycle` → still 06-08T18). Completed 09:16:52 with **no** download report (gate — see iii).
- Line 416601: **the 00Z cron fired**:
  `2026-06-09 09:16:52,237 [apscheduler.executors.replacement_download] INFO: Running job "_replacement_forecast_download_job (trigger: cron[hour='14,2', minute='10'], next run at: 2026-06-10 02:10:00 UTC)" (scheduled at 2026-06-09 14:10:00+00:00)`
  `_parse_cycle(14:10 UTC − 14h = 00:10)` → **cycle = 2026-06-09T00** (arithmetic verified with the venv interpreter).
- Immediately after, the U0R multi-model leg in the SAME job correctly fetched the new cycle:
  `2026-06-09 09:16:53,401 [httpx] INFO: HTTP Request: GET https://single-runs-api.open-meteo.com/v1/forecast?latitude=36.362&longitude=120.087&hourly=temperature_2m&models=ecmwf_ifs&run=2026-06-09T00%3A00&forecast_hours=120... "HTTP/1.1 200 OK"`
  → proves Open-Meteo HAS the 06-09T00 run and the API is healthy.
- Line 418742: `2026-06-09 09:45:53,608 ... Job "_replacement_forecast_download_job (trigger: cron...)" executed successfully`
  — cron completed; between 416601 and 418742 there is **zero** `current-target download report`
  line and zero ifs9km anchor fetches for 06-09T00. `.err` shows no traceback for this job.
- The 5-min materializer is healthy and busy throughout (e.g. `12:27:21 ... materialization queue processed: {'status': 'PROCESSED', 'processed_count': 7 ...}`) — but it can only re-consume 06-08T18 manifests, so every new posterior is stamped with the stale cycle.

**DB confirmation** (read-only sqlite3 on `state/zeus-forecasts.db`):

- `raw_forecast_artifacts` for `openmeteo_ecmwf_ifs_9km`: latest cycle
  `('2026-06-08T18:00:00+00:00', 308, '2026-06-09T10:26:52.562402+00:00')`;
  query for `source_cycle_time >= '2026-06-09T00'` → **empty**.
- `deterministic_forecast_anchors`: `('2026-06-08T18:00:00+00:00', 96)` is MAX.
- `forecast_posteriors`: `('2026-06-08T18:00:00+00:00', 321)` rows covering 96 distinct
  city/target/metric combos.
- `raw_model_forecasts` (U0R capture, separate path): all models AT cycle `2026-06-09T00:00:00+00:00`
  — confirming the same cron successfully ingested 00Z data on the non-anchor leg.

---

## (iii) Root Cause Verdict: **(d) producer ran but a gate rejected the work**
### — structural scheduling defect, NOT transient upstream availability

Proximate mechanism — `_download_replacement_forecast_current_targets_if_needed`
(`/Users/leofitz/zeus/src/data/replacement_forecast_production.py:137-142`):

```python
plan = build_replacement_forecast_current_target_plan(Path(str(forecast_db)))
if plan.ready:
    return {
        "status": "CURRENT_TARGETS_ALREADY_COVERED",
        "coverage": plan.as_dict(),
    }
```

and the caller (`production.py:249-253`) **suppresses logging** for exactly that status —
which is why the cron run is silent in the log:

```python
if download_report is not None and download_report.get("status") not in {
    "CURRENT_TARGETS_ALREADY_COVERED",
    "CURRENT_TARGETS_HAVE_RAW_MANIFESTS",
}:
    logger.info("replacement forecast current-target download report: %s", download_report)
```

`plan.ready` ⇔ `status == "CURRENT_TARGETS_COVERED"`
(`/Users/leofitz/zeus/src/data/replacement_forecast_current_target_plan.py:88-90`),
and per-target coverage is (`current_target_plan.py:35-37`):

```python
@property
def covered(self) -> bool:
    return self.posterior_count > 0 and self.readiness_count > 0
```

**There is no cycle comparison anywhere in this gate.** At 14:10 UTC the 321 posteriors
materialized from cycle 06-08T18 made every market target "covered," so the cron's anchor-download
leg returned ALREADY_COVERED without fetching the now-available 06-09T00 run. The gate conflates
"a posterior exists for this target" with "the posterior is built on the currently-available IFS
cycle." Once any cycle fully materializes, the gate stays satisfied for the whole target window —
the cron can never advance the anchor again.

**Eliminated alternatives:**
- (a) not scheduled / flag-disabled — cron registered and fired (line 416601); both flags true.
- (b) upstream unavailable — Open-Meteo served `run=2026-06-09T00:00` with 200 OK to the U0R leg
  inside the same job run.
- (c) crash — job logged "executed successfully"; no traceback in `zeus-forecast-live.err`.
- (e) intentional cadence wait — the 14:10 UTC cron exists precisely to fetch the 00Z release,
  and it ran on time.

---

## (iv) Proposed Minimal Fix (design only — no edits made)

One structural decision: **separate "posterior exists" from "current cycle's raw inputs exist."**

In `_download_replacement_forecast_current_targets_if_needed` (`production.py:122`), before
honoring `plan.ready`, add a cycle-currency check:

```python
available_cycle = _parse_cycle(None, now=datetime.now(UTC), release_lag_hours=release_lag_hours)
max_artifact_cycle = <SELECT MAX(source_cycle_time) FROM raw_forecast_artifacts
                      WHERE source_id='openmeteo_ecmwf_ifs_9km'>
if max_artifact_cycle is not None and available_cycle <= max_artifact_cycle:
    return {"status": "CURRENT_TARGETS_ALREADY_COVERED", ...}
# else: fall through to download_current_target_raw_inputs even if plan.ready
```

One read-only query, no schema change; the daemon picks up working-tree code without restart
(subprocess-per-cycle). Also log the ALREADY_COVERED skip at INFO with the computed
`available_cycle` (currently silently suppressed at `production.py:249-253`) — the silent skip is
what made this failure invisible.

**Antibody test (stage-1):** DB fixture with 06-08T18 posteriors/readiness covering all targets +
`now = 2026-06-09T14:10Z` ⇒ the function must NOT return ALREADY_COVERED and must request
`run=2026-06-09T00:00` manifests. Pins the invariant: *available_cycle > max anchor-artifact cycle
⇒ download fires regardless of posterior coverage.*

**Operator unblock NOW (no code change, no restart):**

```
PYTHONSAFEPATH=1 .venv/bin/python scripts/download_replacement_forecast_current_targets.py --cycle 2026-06-09T00:00:00Z
```

`--cycle` bypasses `_parse_cycle` and the plan.ready gate is still consulted only via the daemon
path — the script's `main` (script line 335) parses the explicit cycle and downloads directly.
The 5-min materializer then produces fresh anchors + posteriors automatically within one interval.

Without intervention the next automatic chance is the **2026-06-10 02:10 UTC** cron — and that run
will ALSO be gate-skipped while 06-08T18 coverage persists, so both the manual run and the code
fix are warranted. Classification for the operator: **structural scheduling defect** (recurs every
cycle), not transient upstream availability.

---

## (v) Confidence: **High**

Every causal link is directly evidenced:

1. Cron fired at 14:10 UTC (log line 416601) and `_parse_cycle` arithmetic resolves to 06-09T00
   (verified by executing the function logic in the project venv).
2. Upstream had the data — httpx `run=2026-06-09T00%3A00` 200 OK in the same job run.
3. Job exited "executed successfully"; no error in `.err`.
4. Zero download-report log line for that run — matching the suppressed ALREADY_COVERED branch.
5. Zero 06-09T00 rows in `raw_forecast_artifacts` for `openmeteo_ecmwf_ifs_9km` (SQL).
6. Anchors (96 rows) and posteriors (321 rows) both pinned at 06-08T18 (SQL).
7. The gate code (`current_target_plan.py:36-37`, `production.py:138-142`) contains no cycle
   comparison — read directly.

Only unverified micro-detail: whether the cron's plan returned ALREADY_COVERED vs
HAVE_RAW_MANIFESTS — both are log-suppressed identically and both skip the download, so the
verdict and fix are unchanged either way.
