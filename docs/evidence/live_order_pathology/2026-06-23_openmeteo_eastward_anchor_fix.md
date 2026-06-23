# Open-Meteo eastward anchor blackout — root cause + tested fix (2026-06-23)

- Created: 2026-06-23
- Authority basis: live `zeus-forecast-live.err` + on-disk raw manifests; replacement
  Bayesian-fusion forecast prior (IFS9 anchor) pipeline.
- Verdict: ROOT-CAUSED + TESTED. DO NOT deploy/commit from here — orchestrator verifies.

## TL;DR

The blackout is NOT a timezone-window bug in the extractor and NOT an API horizon clip.
It is a **manifest-selection bug**: the seed discoverer (`_latest_manifest`) selected a
**partial-horizon (24h) anchor payload** for a target date that payload physically cannot
serve, because the payload's manifest is mislabeled `forecast_hours=120` and the selector
trusted the declared horizon over the payload's actual time coverage. The 24h payload has
ZERO hourly samples on the wanted local day → `extract_openmeteo_ecmwf_ifs9_localday_anchor`
raises `insufficient Open-Meteo hourly samples inside target local day` → the whole posterior
fails for that city-date → eastward discovery blackout.

A healthy 120h sibling payload (same cycle, same city) that DOES cover the day exists on
disk, but `_latest_manifest`'s `max((source_cycle_time, source_available_at, captured_at))`
tiebreak picked the broken neighbor when those stamps tied.

## Traced failing call path (file:line)

1. Live worker subprocess: `scripts/materialize_replacement_forecast_live.py:165`
   `extract_openmeteo_ecmwf_ifs9_localday_anchor(openmeteo_payload, city_timezone=..., target_local_date=target_date, source_cycle_time=...)`
   — called with DEFAULTS `min_hourly_samples=1`, `require_full_localday=False`. So the only
   way it raises "insufficient … samples" is **zero** samples on `target_local_date`.
2. The extractor raise: `src/data/openmeteo_ecmwf_ifs9_anchor.py:454-455`
   (`if len(contributing_temperatures_c) < min_hourly_samples: raise ValueError("insufficient …")`).
3. The queue re-emits the subprocess stderr as the logged WARNING:
   `src/data/replacement_forecast_live_materialization_queue.py:103` (`materialize[%s] %s`).
   The subprocess returncode 2 + stderr JSON is captured in the failed sidecar.
4. The `openmeteo_payload_json` the subprocess reads is bound by the SEED, produced by
   `src/data/replacement_forecast_seed_discovery.py` — `_latest_manifest()` (selection) →
   `build_replacement_forecast_materialization_seed(... openmeteo_payload_json=...)`
   (`replacement_forecast_seed_discovery.py:424-465`, pre-fix line numbers).
5. The on-disk payload itself is produced by
   `scripts/download_replacement_forecast_current_targets.py:360-400` via
   `build_anchor_request(... forecast_hours=120)` → `_resolve_anchor_payload` (ladder:
   rung-1 single-runs → rung-2 meta-stamped standard → rung-3 S3 bucket). The manifest is
   stamped `forecast_hours=120`, `openmeteo_endpoint=single_runs_api` UNCONDITIONALLY by
   `build_openmeteo_ecmwf_ifs9_anchor_artifact_manifest` (`openmeteo_ecmwf_ifs9_anchor.py:201-213`),
   regardless of how many hours the response actually carried.

An anchor extraction failure fails the WHOLE posterior for the city-date (the fusion's
have_anchor prior cannot form without the anchor), not just one model — confirmed by the
subprocess returncode 2 / failed sidecar and the absence of fresh `forecast_posteriors`.

## Decisive on-disk evidence (Beijing/Chengdu vs Miami)

Live state, cycle dir `state/replacement_forecast_live/raw_manifests/20260622T120000Z/`,
target **2026-06-24**, tz Asia/Shanghai (UTC+8):

| file | n_total hours | first | samples ON 2026-06-24 local |
|---|---|---|---|
| `openmeteo_Chengdu_2026-06-22_high_...json` | 120 | 2026-06-22T20:00 | 24 (OK) |
| `openmeteo_Chengdu_2026-06-23_high_...json` | **24** | 2026-06-23T00:00 | **0 (FAILS)** |
| `openmeteo_Chengdu_2026-06-24_high_...json` | 120 | 2026-06-22T20:00 | 24 (OK) |
| `openmeteo_Chengdu_2026-06-25_high_...json` | 120 | 2026-06-22T20:00 | 24 (OK) |

The failed seed (`seeds_processed/Chengdu.2026-06-24.high.20260623T083020Z…json`) bound
`openmeteo_payload_json = …/openmeteo_Chengdu_2026-06-23_high_20260622T120000Z.json` for
**target_date 2026-06-24** — the 24h file with 0 samples on 06-24. Reproduced exactly:

```
openmeteo_Chengdu_2026-06-23_high_...json -> RAISES ValueError insufficient Open-Meteo hourly samples inside target local day
openmeteo_Chengdu_2026-06-24_high_...json -> OK high=28.80 low=22.20 n=24
```

Failed-sidecar stderr (verbatim):
`{"error": "insufficient Open-Meteo hourly samples inside target local day", "error_type": "ValueError", "status": "ERROR"}` (returncode 2).

The 24h-vs-120h split is NOT clean eastward/westward: same-tz Shanghai/Chongqing/Qingdao got
120h while Chengdu/Beijing got 24h (and Los Angeles, UTC-7, also got 24h). All 24h files
start at exactly `<target>T00:00` and span one local day. They are **partial-horizon
single-runs captures** taken while the provider's 12Z run was only partly published at fetch
time (mtimes ~12:47 on 2026-06-22, minutes apart from the 120h siblings). The
`payload_path.exists()` guard (`download_…_current_targets.py:377`) then never re-fetches the
24h file, so it persists; a later manifest re-stamp (avail/captured = 2026-06-23T08:10:15)
left the same 24h bytes but a 120h-claiming manifest.

### API is healthy now (cause is the stale partial file, not the live API)

Re-fetching cycle 2026-06-22T12:00 with the EXACT repo params
(`run`, `forecast_hours=120`, `models=ecmwf_ifs`, `timezone`, `cell_selection=land`,
`temperature_unit=celsius`, `hourly=temperature_2m`) via the repo's own `fetch` returns a
full **120h** payload for ALL cities incl. Chengdu/Beijing/London/Los Angeles. So the API
default window DOES cover eastward local days; the on-disk 24h files are a publication-race
snapshot frozen by the no-refetch guard.

## Proven mechanism

`_manifest_horizon_allows_target_date` (`replacement_forecast_seed_discovery.py:160-194`)
admits a manifest for any `start <= wanted <= start + ceil(forecast_hours/24)` days, trusting
the DECLARED `forecast_hours=120`. So the 06-23 manifest (24h bytes, 120h-claimed) is admitted
for the 06-24 target. `_latest_manifest` then chose among the admitted siblings purely by
`max((source_cycle_time, source_available_at, captured_at))`. Because the 06-23 and 06-24
manifests carry IDENTICAL cycle/avail/captured stamps (both re-stamped 2026-06-23T08:10:15),
the tiebreak is order-dependent and returned the broken 24h neighbor over the covering 120h
file. The comment at the horizon check even says "materialization still fails closed if the
payload lacks the requested local day" — which is exactly what happened, and is the blackout.

## Fix (smallest correct; respects all constraints)

`src/data/replacement_forecast_seed_discovery.py`:

1. New helper `_manifest_payload_covers_target_local_day(manifest, *, city_timezone, target_date)`
   — reads the manifest's ON-DISK payload and returns True iff ≥1 hourly sample parses
   (via the extractor's own `_parse_openmeteo_time`) onto the wanted local day. Fail-OPEN on
   any read/parse error (the extractor stays the fail-closed backstop). NO daily extreme is
   computed — coverage is an in-day sample COUNT only, so `require_full_localday`'s intent is
   untouched (no fabricated clipped extreme).
2. `_latest_manifest` gains a `city_timezone` kwarg and a PRIMARY sort key = "does the payload
   actually cover the day?", with the existing
   `(source_cycle_time, source_available_at, captured_at)` recency key as the secondary
   discriminator. A covering manifest now always outranks a partial-horizon sibling at the same
   stamps. When no tz is available OR no candidate covers, the recency key alone decides
   (prior behaviour preserved — never returns None where it previously returned a manifest).
3. The live discovery loop resolves `city_timezone` from the canonical `cities_by_name`
   registry (the SAME source the seed builder uses) and passes it through.

No magic constants (forecast_days/window unchanged; selection is driven by actual payload
coverage). No caps/throttles/allowlists. Fail-soft preserved (fail-open coverage + extractor
backstop). The downloader's partial-capture root (publication-race + no-refetch guard) is
left as-is; the selector now routes around it by preferring a covering sibling, which is the
minimal change that restores coverage today without a network re-fetch.

Files changed:
- `src/data/replacement_forecast_seed_discovery.py` (new `_manifest_payload_covers_target_local_day`;
  `_latest_manifest` coverage-aware selection + `city_timezone` kwarg; loop wires tz from registry).
- `tests/test_replacement_forecast_seed_discovery.py` (3 new tests, TDD).

## Validation against REAL live manifests (read-only)

With the fix, replaying `_latest_manifest` over the 7,262 live manifests for target 2026-06-24
now selects the covering 120h payload and extracts cleanly for every previously-failing city,
and the working westward cities are unchanged:

```
Beijing   -> openmeteo_Beijing_2026-06-24_high_...json   OK high=31.2 n=24
Chengdu   -> openmeteo_Chengdu_2026-06-24_high_...json   OK high=28.8 n=24
Busan     -> openmeteo_Busan_2026-06-24_high_...json     OK high=22.7 n=24
Helsinki  -> openmeteo_Helsinki_2026-06-24_high_...json  OK high=15.9 n=24
London    -> openmeteo_London_2026-06-24_high_...json    OK high=35.1 n=24
HongKong  -> openmeteo_Hong_Kong_2026-06-24_high_...json OK high=31.2 n=24
Guangzhou -> openmeteo_Guangzhou_2026-06-24_high_...json OK high=34.8 n=24
Miami     -> openmeteo_Miami_2026-06-24_high_...json     OK high=31.5 n=24  (westward unaffected)
MexicoCity-> openmeteo_Mexico_City_2026-06-24_high_...json OK high=21.8 n=24 (westward unaffected)
```

## Test evidence (failing → passing)

New TDD test `test_latest_manifest_skips_partial_horizon_payload_for_eastward_target`:

- RED (pre-fix): `AssertionError: must select the manifest whose payload covers 2026-06-24,
  not the 24h partial` — it picked `_2026-06-23` (24h) over `_2026-06-24` (120h).
- GREEN (post-fix): passes.

Plus two no-regression tests:
- `test_latest_manifest_unchanged_when_westward_payload_already_covers` (both cover → fresher
  recency wins, unchanged behaviour).
- `test_latest_manifest_falls_back_to_recency_when_no_candidate_covers` (no covering candidate
  → recency decides, never None — extractor stays the backstop).

Suite results (writer-lock antibody bypassed via `ZEUS_DISABLE_WRITER_LOCK_ANTIBODY=1` — it
flags 5 UNTRACKED scripts unrelated to this change):
- `tests/test_replacement_forecast_seed_discovery.py`: **14 passed** (11 prior + 3 new).
- `tests/test_cycle_monotone_materialization.py`: 24 passed.
- AST parse OK for the 3 touched/requested files.

Failure-count parity proof (base vs. with-change, identical → zero new failures introduced):
- `test_openmeteo_ecmwf_ifs9_anchor.py`: 1 failed / 8 passed BOTH (the 1 failure
  `test_anchor_artifact_manifest_rejects_bad_metric_or_pre_available_capture` is pre-existing
  base breakage, unrelated).
- `test_replacement_forecast_materialization_request_builder.py`: 3 failed / 2 passed BOTH
  (pre-existing).
- 6 collection-error files in the broad `-k` selection (e.g. `_QLCB_SOFT_ANCHOR_BASIS`
  ImportError) are pre-existing base breakage; verified present on the clean base with my
  change stashed.

## Risk to westward cities

None observed. Westward cities already select correctly (their freshest manifest covers the
day), and the coverage key is a no-op tie-breaker there (it ranks all covering manifests
equally, so the recency key still decides exactly as before). Confirmed on the live manifests
(Miami/Mexico City select the 06-24 file and extract OK) and by the dedicated no-regression
test.
