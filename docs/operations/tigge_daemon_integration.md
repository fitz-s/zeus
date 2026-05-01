# TIGGE retrieval inside the data-ingest daemon

**Created**: 2026-05-01
**Authority basis**: Operator directive 2026-05-01 — TIGGE retrieval must run inside
the ingest daemon so trading does not go stale even when ingest is healthy.
**Structural fix**: Fitz Constraint #1 (one decision: TIGGE belongs to ingest)
replacing N patches across cron entries / manual runs / ad-hoc backfills.

## Why

Before this change, ingest and trading were separated, but the TIGGE pipeline
was never moved into the ingest daemon. TIGGE downloads ran via manual scripts
in `51 source data/scripts/` or ad-hoc cron entries, so TIGGE forecasts went
stale (4/29-5/1 gap as of 2026-05-01) even when the ingest daemon was running
healthy. Trading then traded on stale ensemble data while the supervision lights
all stayed green — a textbook "dead loop."

## Architecture

The pipeline is a three-stage cycle, orchestrated by `src.data.tigge_pipeline`:

| Stage | Mechanism | Lives in |
|---|---|---|
| 1. Download | subprocess to `tigge_(mx\|mn)2t6_download_resumable.py` | `51 source data/scripts/` |
| 2. Extract  | subprocess to `extract_tigge_(mx\|mn)2t6_localday_(max\|min).py` | `51 source data/scripts/` |
| 3. Ingest   | in-process import of `scripts/ingest_grib_to_snapshots.ingest_track` | `zeus/scripts/` |

The download/extract scripts have a different release cadence (manifest changes,
region tweaks, ECMWF API evolution) and are reused by the parallel manual
backfill workflow. Wholesale migration would balloon the change and create a
duplicate maintenance surface for ~6 scripts. They keep their lifecycle.

The ingester is in-process because (a) it must run inside the zeus venv where
`TiggeSnapshotPayload` and `apply_v2_schema` live, and (b) `ingest_track` is a
clean importable function that already enforces the canonical-write contract.

## Source role: backfill, not live trading

**Updated 2026-05-01**: The TIGGE public archive (`class=ti dataset=tigge`)
has a **48-hour public embargo**. Confirmed verbatim on
<https://confluence.ecmwf.int/>. This means TIGGE CANNOT serve same-day
trading; the prior documented "TIGGE posts by 10:00 UTC" claim was wrong.

The structurally correct arrangement (Fitz Constraint #1 — one decision: the
"live forecast" and "training backfill" responsibilities are separated, not
patched):

| Lane | Source | Latency | Used for |
|---|---|---|---|
| Live trading (same-day forecasts) | ECMWF Open Data ENS (`mx2t6` / `mn2t6`) | ~6-8 hours | Decision-time ensemble vectors, calendar-day high/low markets |
| Training / backfill (T-2 issue date and older) | TIGGE archive (`mx2t6` / `mn2t6`, 50 pf + 1 cf) | 48-hour embargo | Platt training set, historical audit trail |

Both lanes write to `ensemble_snapshots_v2` with distinct `data_version`
values; the schema's `UNIQUE(city, target_date, temperature_metric,
issue_time, data_version)` constraint allows both rows to coexist for the
same (city, target_date, metric). Readers prefer the freshest source via
`src.data.ecmwf_open_data.data_version_priority_for_metric(metric)`.

### data_version values

- `ecmwf_opendata_mx2t6_local_calendar_day_max_v1` — Open Data live, HIGH track
- `ecmwf_opendata_mn2t6_local_calendar_day_min_v1` — Open Data live, LOW track
- `tigge_mx2t6_local_calendar_day_max_v1` — TIGGE archive, HIGH track
- `tigge_mn2t6_local_calendar_day_min_v1` — TIGGE archive, LOW track

All four are in `CANONICAL_ENSEMBLE_DATA_VERSIONS`
(`src/contracts/ensemble_snapshot_provenance.py`).

## Schedule

Four scheduler jobs are registered in `src/ingest_main.py::main()` (post 2026-05-01):

| Job ID | Trigger | Purpose |
|---|---|---|
| `ingest_opendata_daily_mx2t6` | cron `hour=7, minute=30` UTC | Daily fetch of today's 00Z run from Open Data, HIGH track (`mx2t6`). |
| `ingest_opendata_daily_mn2t6` | cron `hour=7, minute=35` UTC | Daily fetch of today's 00Z run from Open Data, LOW track (`mn2t6`). 5-minute offset spaces out downloads. |
| `ingest_tigge_archive_backfill` | cron `hour=14, minute=0` UTC | Daily backfill of (today − 2)'s 00Z run from TIGGE archive, both tracks. Targets a date the 48-hour embargo has already lifted. |
| `ingest_opendata_startup_catch_up` | `date` (fires once at boot) | Pulls the freshest available run for both tracks at daemon start so a fresh ingest does not wait until the next cron tick. |
| `ingest_tigge_startup_catch_up` | `date` (fires once at boot) | Fills any missed TIGGE archive issue dates between `MAX(issue_time)` in `ensemble_snapshots_v2` and yesterday, capped at `MAX_LOOKBACK_DAYS=7`. |

Both jobs are wrapped in `@_scheduler_job` so any exception is logged + recorded
in `scheduler_jobs_health.json` without crashing the daemon.

## Pause mechanism

The cycle honours the `paused_sources` directive in `state/control_plane.json`,
the same control-plane channel the operator already uses for `ecmwf_open_data`.

**To pause**:

```python
from src.control.control_plane import set_pause_source
set_pause_source("tigge_mars", True)
```

The next `_tigge_daily_cycle` tick (and the immediate startup catch-up) will
short-circuit with `paused_by_control_plane` and emit no MARS traffic.

**To resume**:

```python
set_pause_source("tigge_mars", False)
```

### Auto-pause on credential failure

If `~/.ecmwfapirc` is missing, malformed, or has empty fields, the cycle:

1. Logs CRITICAL with the exact remediation hint.
2. Calls `set_pause_source("tigge_mars", True)` so the next tick short-circuits.
3. Returns a `paused_mars_credentials` status — no exception propagates.

This is the antibody for the failure mode the operator named: a single bad
credential file must NOT crash the ingest daemon (which would also stop K2
ticks, observations, solar, ECMWF Open Data, etc.). It's a fail-closed pause,
not a fail-fast crash.

The operator restores credentials by:

1. Confirming `~/.ecmwfapirc` is valid JSON with `url`, `key`, `email` (the
   ECMWF SDK convention — NOT the macOS Keychain). Reference:
   <https://confluence.ecmwf.int/display/WEBAPI/Access+ECMWF+Public+Datasets>
2. Running `set_pause_source("tigge_mars", False)`.
3. The next 11:00 UTC tick (or a manual `python -c "from src.data.tigge_pipeline
   import run_tigge_daily_cycle; run_tigge_daily_cycle()"`) will resume.

There is **no** `openclaw-ecmwf-mars-key` Keychain entry in this design. The
ECMWF SDK reads `~/.ecmwfapirc` directly; introducing a parallel Keychain copy
would create the second-source-of-truth drift hazard that already burned us
once on 2026-05-01 (F4: stale Keychain Polymarket API creds).

## Boot-time catch-up bound

`MAX_LOOKBACK_DAYS = 7` (in `src.data.tigge_pipeline`). Rationale:

- TIGGE is a 7-day forecast horizon; older issue dates are rarely useful.
- Bounded lookback means a 30-day daemon outage will NOT trigger a 30-day MARS
  download storm on restart (which could exhaust the operator's MARS quota).
- Dates beyond the cap need a deliberate operator action (see "One-off backfill"
  below) to avoid silent runaway backfills.

## One-off backfill via the same code path

The daemon's code path is also the operator's backfill path. Do **not** reach
back into `51 source data/scripts/` for routine backfills.

```bash
cd /Users/leofitz/.openclaw/workspace-venus/zeus
.venv/bin/python -c "
from src.data.tigge_pipeline import run_tigge_daily_cycle
result = run_tigge_daily_cycle(target_date='2026-04-29')
print(result)
"
```

This invocation:

- Honours the `paused_sources` directive (so a paused source genuinely stops
  even one-off backfills — operators must explicitly resume first).
- Uses the same MARS credential check, the same subprocess invocation, and the
  same in-process ingest as the daemon's daily tick.
- Skips already-ingested rows via the `UNIQUE(city, target_date,
  temperature_metric, issue_time, data_version)` constraint on
  `ensemble_snapshots_v2`. Re-runs are safe.

For multi-day backfills outside the 7-day cap, loop over the dates in shell —
the function takes a single ISO date.

## Health probe

`src/data/source_health_probe.py::_probe_tigge_mars` is now a real probe (not
the `MANUAL_OPERATOR` stub). On every 10-minute probe tick it:

1. Calls `check_mars_credentials()` (cheap, just file IO + JSON parse).
2. Reads `state/scheduler_jobs_health.json` to check whether the most recent
   `ingest_tigge_daily` run was FAILED. A persistent FAILED status surfaces in
   `state/source_health.json` so the freshness gate can react.

The probe never invokes MARS itself — MARS retrieval is minutes-scale and would
dominate the 10-minute probe cadence. Authority for "is the source live" comes
from the credentials being present + the daily cycle's recorded outcome.

## Failure modes & antibodies

| Failure mode | Antibody |
|---|---|
| Bad MARS credentials → daemon crash | `tigge_pipeline` returns `paused_mars_credentials`; `@_scheduler_job` swallows; auto-pause prevents repeat crashes. Tested in `tests/test_tigge_daily_ingest.py::test_run_cycle_pauses_on_missing_credentials`. |
| Stale TIGGE while ingest "healthy" | `_probe_tigge_mars` now treats FAILED `ingest_tigge_daily` as source-degradation, surfacing in `source_health.json`. |
| Schema drift between extract and ingest | `TiggeSnapshotPayload` (antibody #16). Frozen by this change; we don't touch the contract. |
| Runaway 30-day backfill on long outage | `MAX_LOOKBACK_DAYS=7` cap in `determine_catch_up_dates`. Tested in `tests/test_tigge_daily_ingest.py::test_determine_catch_up_dates_caps_at_max_lookback`. |
| Re-run inserts duplicates | `UNIQUE(city, target_date, temperature_metric, issue_time, data_version)` already enforced by `ensemble_snapshots_v2`. Tested in `tests/test_tigge_daily_ingest.py::test_run_cycle_idempotent_re_run`. |

## Future work (out of scope here)

- The download stage uses the conda-base python (where `ecmwfapi` is installed).
  Migrating the SDK into the zeus venv would let us drop the `_conda_python()`
  helper and the cross-env subprocess hop. Tracked as follow-up.
- Real MARS-side health probe (a tiny dry-run query) instead of the indirect
  signal via `scheduler_jobs_health.json`. Requires careful rate-limiting
  against MARS quota.
