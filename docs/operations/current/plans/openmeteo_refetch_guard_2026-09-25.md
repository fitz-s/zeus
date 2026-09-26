# Open-Meteo refetch guard (2026-09-25)

Status: IMPLEMENTED on branch `claude/agent-a4be3119c42b833fc`, not deployed.
Owner surface: `src/data/openmeteo_client.py::fetch`, `src/data/openmeteo_response_store.py`.
checked=2026-W39; basis=logs 09-24..09-26, state/openmeteo_quota.json, meta.json probes; until=first full UTC day after deploy.

## 1. Problem

The free tier allows 10,000 units/day; the tracker's priority lane stops at
`PRIORITY_DAILY_LIMIT` = 9,000 (`src/data/openmeteo_quota.py`). It was crossed
every day (09-23 11:56Z, 09-24 05:22Z, 09-25 16:05Z). After that, every newly
published run is refused (`source_clock_quota_abort:cooldown_seconds=...`) until
00:00Z: the dominant forecast-latency tail.

This is the tenth instance of one bug class. Every earlier fix
(memory `auction-scope-collapse-is-openmeteo-quota-starvation`, rounds 1-7; 09-24
horizon gate; 09-25 cursor ledger) closed one caller. The shared shape: a caller
decides "do I need this?" by checking a local artifact (DB row, memo, cursor), and
anything that keeps the artifact from existing (200 with nulls, past-horizon tail,
failed persist, restart, replica flip) turns into an unbounded metered
re-request of the same answer.

### Why the existing single-runs payload cache missed

`_single_runs_payload_cache` (`src/data/bayes_precision_fusion_download.py`) only
admits a payload whose target-day slices parse complete
(`_single_runs_payload_has_reusable_hourly_axis`). Short-horizon regionals never
satisfy that for their last target date: NAM 61 h, DMI 60 h, JMA MSM 40 h, HRRR
19 h, HRDPS 49 h (meta.json `data_end_time - init`, probed 09-26). A request
covering such a date is never admitted, or its entry is rejected for that date on
lookup, so it re-fetches every poll. Reproduced: NAM 06Z payload in cache is
reusable for 09-25/26 and not for 09-27; a live URL for it was fetched 81 times on
09-25. The cache was also keyed per caller, per process memory (reloaded 30 s),
and capped at 400 entries. Measured repeat units on 09-25 by model set:
DMI 997, NAM 885, HRRR+HRDPS 775, JMA MSM 474.

## 2. Law

A metered request is sent only when its answer can differ from one already held.

An exact-run answer is a function of provider run state, so it is held with the
state it was fetched under and replayed at zero quota until that state changes:

- single-runs `run=R`: while R is superseded (and the held answer reflected R's
  final modification), or while R is the latest run and its
  `last_run_modification_time` has not advanced;
- previous-runs with explicit dates: until any requested model publishes a new run
  or modifies its latest one;
- a gap in the answer (horizon nulls, missing model) is part of the answer;
- the provider's typed refusal "the requested model run is not available"
  (HTTP 400, `run_not_published`) is also an answer once the run's availability
  plus the consistency wait has passed (off-grid knmi/nbm runs never flipped
  400->200 in 09-23..09-26 logs: 143 identities, 0 flips).

The rule is enforced at the one choke point every metered call passes through, so
no new caller can reopen the class.

## 3. Design

- Choke point: every metered Open-Meteo HTTP call in `src/` goes through
  `openmeteo_client.fetch()` (audit below). `fetch()` classifies the request with
  `exact_request()`, re-reads stale run state via the unmetered
  `/data/{slug}/static/meta.json` (itself through `fetch`, `count_toward_quota=False`),
  looks up the store, and only then asks the tracker for a lease.
- Run state: pinned by run identity per meta slug, forward-only (max init, and per
  run max modification and availability). Replicas disagree
  (memory `provider-metadata-replicas-disagree-pin-by-run-identity`), so a reading
  refreshes the freshness confirmation only when it equals the pinned maximum on
  every stamp (init, modification AND availability); a lagging replica neither
  regresses the pin nor vouches for it.
- Per-model proof: an answer for N models stores N proofs. Each model's part holds
  only on its own evidence: its requested run superseded by its own newer run, or
  its (run, modification) unchanged and confirmed within 60 s. One model's
  supersession never covers another's part (review 2026-09-26; antibody
  `test_one_models_supersession_never_covers_another`). An answer is
  provable only if modification <= availability and now >= availability + 10 min
  (`SOURCE_AVAILABILITY_CONSISTENCY_WAIT_MINUTES`). A latest-run answer is served
  only while its state was confirmed within 60 s; superseded runs need no re-read.
- Identity: `request_identity(url, params)` (exact bytes of the request), plus the
  per-slug proof stored with the answer. Models without a readable meta slug
  (best_match, legacy families) are never guarded.
- Medium: one SQLite file `state/openmeteo_response_store.db`, WAL, autocommit
  statements, 2 s busy timeout; shared by ingest, forecast-live and live daemons.
  Not a canonical DB (no truth, no `db_table_ownership.yaml` row); allowlisted in
  `SQLITE_CONNECT_ALLOWLIST` with that reason. Every public store entry point is
  wrapped fail-soft: a SQLite/OS/codec error returns the network-path default and
  logs at most every 5 min; no store error reaches a fetch caller.
- Codec: compact JSON, zstd-3, BLOB (5.6x on the 400 live cache payloads, mean
  3.2 KB -> 0.57 KB). Law: memory `zlib9-base64-text-is-slower-and-bigger-than-zstd3-blob`.
- Retention by reachability: `expires_at` = run + forecast window + 2 days (the last
  target local day inside the answer can be live that long), previous-runs =
  end_date + 3 days. Evicted every 10 min on an index. Size bound: at ~1,300 distinct
  answers/day x 0.6 KB x ~7 days, under 10 MB.
- Concurrency: the tracker's existing per-request lease is the single-flight gate.
  A caller refused with `REQUEST_IN_FLIGHT` for an exact request whose run state is
  provable waits (poll 0.25 s, at most min(20 s, timeout)) for the twin's answer
  instead of raising; it pays only if the twin fails.
- Burn alarm: a durable day-keyed per-(hour, job) unit ledger (metered, served from
  store, reissued = metered for an identity that already succeeded that UTC day).
  Each metered send checks at most once a minute; when today's units plus the
  2 h mean rate x hours left exceeds 9,000, one WARNING per hour (claimed in the
  shared store, so one per mesh) names the top 3 jobs by current rate with their
  same-day reissued units. 2 h, not 1 h: a 1 h rate also fired on the guarded
  00Z boot burst of 09-25.

### Bypass audit (metered HTTP not via `fetch()`)

All daemon paths route through `fetch()`. Direct `httpx`/`urlopen` calls to
Open-Meteo hosts remain only in: `src/data/openmeteo_ecmwf_ifs9_bucket_transport.py`
`_default_http_get` (S3 bucket manifests, unmetered; and `capture_city_target_elevation`,
a one-time per-city elevation capture cached in state, called from
`scripts/download_replacement_forecast_current_targets.py`), and operator scripts
(`scripts/backfill_*openmeteo*`, `onboard_cities.py`, `build_grid_representativeness.py`,
`probe_model_cell_distance.py`, `verify_reality_contracts_2026-05-17.py`,
`backfill_bayes_precision_fusion_promoted_model_history.py`). The replacement
availability probe's injected `urlopen` path is test-only. None is a recurring
metered loop; left as is.

## 4. Replay (logs + tracker)

Replayed every logged GET through the guard (first 200/typed-400 per exact identity
metered; previous-runs re-metered per model update interval). Log-derived units
undercount the tracker (~1.4x, memory) but the ratio holds.

| Window | Logged units | Under guard | Saved |
|---|---|---|---|
| 09-25 00-24Z (pre cursor fix, as-is) | 6,180 | 2,089-2,299 | 3,881-4,091 (63-66%) |
| 09-26 04:17-08:25Z (cursor fix live: ingest restarted 04:17Z on b89bfa2d7) | 2,543 | 813-915 | 1,628-1,730 (64-68%) |

Tracker cross-check: `day_count` 4,949 at 08:25Z on 09-26, so the cursor fix alone
still projects past 9,000 today.

Range: low end allows one more metered send per identity (one modification advance).

- The coordinator's 00Z repro (608-target reports, priority probe icon_eu
  Madrid/Tel Aviv/Warsaw 09-27, 6 actionable, 0 rows) ran 22:35-01:18Z with the
  pre-deploy ingest; 232 such reports in that window. Units in the window: 567
  logged, 442 under guard. Its icon_eu identities were each fetched once per run
  (not the leak); the leak there was ecmwf_ifs/DMI/JMA/HRRR+HRDPS repeats.
- Residual once cursors advance: the 09-26 04:17Z+ window is the post-cursor-fix
  mix. It still bleeds ~14,800 units/day at its rate; the guard cuts it to
  4,800-5,400/day (top savers: DMI 765, JMA MSM 255, NAM 216, KNMI 110 units in
  4.1 h). The remaining metered units are first-seen
  answers, not repeats: 23 URL shapes per run (single vs multi-location batches,
  `cell_selection` absent vs `land`, `past_hours` 0 vs 24) fetch the same
  (location, model, run) cell; 11 of 78 ecmwf_ifs cells in the 00Z hour came in
  more than one shape. Cell-granular dedupe would save only a further ~13%.
- Burn alarm on the shipped ledger: 09-25 as-is fires at 01:22Z naming single-runs
  (reissued_same_day=108); 09-24 as-is fires at 02:30Z (single-runs reissued 276);
  both days silent under the guard.

## 5. Zero-waste demand model

One fetch per (model, served run, in-domain city); one-location 120 h request =
1 unit. Runs/day = run hours that answered 200 on single-runs in the logs;
cities = the download's own `_model_in_domain` over the 54 configured cities.

| Model | Runs/day | Cities | Units/day |
|---|---|---|---|
| ecmwf_ifs | 4 | 54 | 216 |
| icon_global | 4 | 54 | 216 |
| ukmo_global_deterministic_10km | 4 | 54 | 216 |
| ncep_nbm_conus | 14 | 12 | 168 |
| icon_eu | 8 | 12 | 96 |
| gfs_hrrr | 6 | 12 | 72 |
| icon_d2 | 8 | 5 | 40 |
| gem_hrdps_continental | 3 | 13 | 39 |
| nam_conus | 3 | 12 | 36 |
| dmi_harmonie_europe | 5 | 6 | 30 |
| jma_msm | 6 | 3 | 18 |
| meteofrance_arome_france_hd | 8 | 2 | 16 |
| met_nordic / ukmo_uk_2km / knmi / icon_2i | 7/6/6/2 | 1 each | 21 |
| kma_gdps, kma_ldps | 0 served | | 0 |

- Single-runs, one request per (model, run, city): **1,184 units/day**. If every
  model sharing a (city, run hour) rides one request (`models=` is unmetered): **393/day**.
- Measured first-seen units on 09-25 for the other families: BPF previous-runs 254,
  standard meta-stamped 99, archive hourly instants 85, solar 54, health probe 1
  (it re-fetched one fixed 2025-01-01 London point 76 times; see below).
- Zero-waste total: about **1,700 units/day** (1,184 + 254 + 99 + 85 + 54 + ~10
  day0/ensemble), well under the 8,500 maintenance ceiling. A paid tier is not
  needed on this demand; the overrun is waste, not demand.

Not guarded, left for callers (outside this law's scope): the 10-minute
`source_health_open_meteo_archive` probe re-fetches the same archive point (2,606
reissued units in the tracker on 09-26 by 05:30Z are this one identity's attempts
counter, accumulated across the 24 h state TTL, not today's spend; logs show 76
GETs on 09-25) and the multi-shape fetch of one cell (above).

## 6. Live acceptance

The first full UTC day after deploy:

1. zero `source_clock_quota_abort` in `logs/zeus-ingest.log`;
2. `state/openmeteo_quota.json` `day_count` below 8,500 at 23:59Z;
3. `Open-Meteo burn alarm` silent, or naming a real new instance (a job whose
   `reissued_same_day` grows);
4. `sqlite3 state/openmeteo_response_store.db "SELECT SUM(served), SUM(metered) FROM unit_ledger WHERE hour LIKE '<day>%'"`
   shows served > 0 and the store file under 50 MB.

The landing day is not the verdict day (memory rule).

## 7. Rollback

Revert the branch commits. The store is additive: with it absent, `fetch()`
behaves exactly as before (`response_store is None` path). Emergency off without a
code change: make `state/openmeteo_response_store.db` unopenable (for example
`chmod 000`): every store call fails soft to the network path. Delete the file to
drop all held answers. Nothing canonical reads it.

## 8. Files, tests

- `src/data/openmeteo_response_store.py` (new): classification, run-state pins,
  answer store, unit ledger, burn alarm.
- `src/data/openmeteo_client.py`: store lookup before the lease, twin wait,
  proof capture before send, answer/refusal put after.
- `src/data/openmeteo_quota.py`: `request_in_flight()` quiet read.
- `src/state/db_writer_lock.py`: allowlist entry.
- `tests/data/test_openmeteo_refetch_guard.py`: antibodies (a)-(f) and edges.
  Run: `.venv/bin/python -m pytest -q tests/data/test_openmeteo_refetch_guard.py`.
