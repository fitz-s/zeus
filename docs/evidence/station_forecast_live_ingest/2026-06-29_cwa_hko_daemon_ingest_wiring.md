# CWA/HKO station-forecast live ingest — the missing daemon wiring

**Date:** 2026-06-29
**Authority basis:** operator directive "加数据" (add CWA/HKO station data to the live forecast cycle); `src/data/station_forecast_adapter.py` single_runs persist contract; `config/station_forecast_sources.json` `adapter_kind` dispatch seam.
**Money-path stage:** source truth → forecast signal (UPSTREAM data side).

## TL;DR (corrected — the real root cause was one layer deeper than the ingest)

The ingest wiring below was necessary but **NOT sufficient**. After it landed cwa/hko in
`raw_model_forecasts`, the data still never reached the fusion: `read_current_instrument_values`
(the single serving authority, registry #10) **excluded station sources** because they carry their
OWN provider cycle clock (e.g. cwa issued `06:22`) which is newer than the gridded freshness ceiling
(`2026-06-28T18:00`); the four gridded passes serve `source_cycle_time <= ceiling`, dropping the
station row. So `persisted_current` never contained cwa/hko → the precision fusion never saw them.

Complete fix (three changes, on top of the ingest):
1. **`read_current_instrument_values`** gains an opt-in `include_station_sources` (default False →
   the four other consumers — seed_discovery, completeness, fusion-upgrade-trigger — stay
   byte-identical). When on, a station pass serves `cwa_*`/`hko_*` by their own latest single_runs
   row, free of the gridded ceiling.
2. **The materializer's override** opts in, so cwa/hko enter `persisted_current` → the dynamic
   precision fusion (`raw_second_moment_weights`) weights them at their **initial-precision prior**
   (`low_n_prior_weighted_models`), recorded in `used_models`. This is the operator's "fusion
   初始数学配比" — the fusion's own math, never a hand-set weight.
3. **Reverted the scheme CSV** (my earlier mistake): I had added cwa/hko to the frozen
   `city_one_scheme_grid_aware.csv` at a hand-computed weight (0.4633) — exactly the hard-coded
   valve the operator rejected ("你设计的清除掉"). The external `_station_live_omitted` edit wants a
   live station source ABSENT from the frozen scheme so the scheme is skipped and the dynamic
   fusion serves it. Restored the gridded-only original from `.pre_add_data.bak`.

cwa/hko are family-neutral (`decorrelated_provider_families_of` unchanged), so the upgrade-trigger /
seed-discovery / completeness consumers correctly need no change.

**Live confirmation (2026-06-29T06:42Z, post-restart, new code):** Hong Kong 2026-07-01 posterior —
`used_models: [ecmwf_ifs, icon_global, ukmo_global_deterministic_10km, hko_fnd]`,
`source_clock_one_scheme: null` (frozen scheme skipped → dynamic fusion), `method: T2_BAYES`,
`low_n_prior_weighted_models: ['hko_fnd']` (hko entered at its initial-precision prior). The daemon
also self-re-ingested cwa (rows refreshed 06-29..07-05), so the CWA key resolves in the daemon and
the pipeline runs autonomously. Taipei serves cwa identically on its next recompute.

**Tests:** `tests/data/test_station_source_current_value_serving.py` (serving opt-in: station served
by own cycle, gridded byte-identical when off, prefix + latest-row), plus the ingest-wiring suite —
56 passed across all touched + adjacent surfaces.

**Lesson (the durable one):** a serve-logic unit check, or even a fix at one layer, is not the
daemon producing the result. The discriminator is the **persisted output's `used_models` /
provenance**, read after a real recompute — not that a function returns the right thing when you
call it by hand. The gap hid one layer below where the first fix looked.

---

## The gap (why the prior "deploy" did not warm anything)

The prior session verified the **serve logic** in isolation — `scheme_for_city` →
`fixed_weight_center_from_values` returns a cwa-weighted center when handed a cwa value —
and declared the deploy done. But the **live daemon never produced that result.** The latest
live Taipei posterior (computed `2026-06-29T04:41:14Z`, just before this fix) proved it:

| evidence | value |
|---|---|
| `posterior_method` | `openmeteo_ecmwf_ifs9_bayes_fusion` (NOT `SOURCE_CLOCK_FIXED_WEIGHT`) |
| `bayes_precision_fusion.used_models` | `[ecmwf_ifs, icon_global, ukmo_global_deterministic_10km]` — **3 gridded only** |
| `bayes_precision_fusion.source_clock_one_scheme` | `null` |
| `anchor_value_c` (served center) | **32.99** (cold gridded; CWA's own forecast is ~35) |
| `provenance_json` contains `cwa_township` | **False** |
| `raw_model_forecasts` rows for `cwa_township_single_runs` / `hko_fnd_single_runs` since 06-28 23:39 | **0** (only a one-time manual ingest existed) |

Root cause: **there was no daemon-side station ingest at all.** The function the notepad
believed existed (`_ingest_station_forecasts_if_needed`) had **zero call sites** and did not
exist. The adapter held all the pieces (`fetch_cwa_township_payload`, `ingest_cwa_township_live`,
`ingest_hko_fnd_live`, `persist_station_forecast_rows`) but nothing on the live download lane
called them. `config/station_forecast_sources.json` even documented this verbatim:
_"live production does not ingest this file."_ With no raw cwa/hko row at the served cycle,
`fixed_weight_center_from_values(allow_incomplete=False)` returned `None`, the scheme silently
fell back, and Taipei/HK served the cold gridded basket.

## The fix (TDD, 10 tests green)

1. **Config-driven dispatcher** — `station_forecast_adapter.ingest_enabled_station_sources_live(conn)`:
   reads the config, and for each **enabled** source routes by `adapter_kind`
   (`cwa_township_json` → `ingest_cwa_township_live`, `hko_fnd_json` → `ingest_hko_fnd_live`),
   passing city/metric/endpoint/location from the spec. **Per-source fail-soft**: one provider's
   network/parse error is logged and skipped, never aborting the others. No hard-coded per-source
   call list (operator anti-hard-coding law); future siblings are config + a dispatch entry.

2. **Cycle helper + wiring** — `replacement_forecast_production._ingest_station_forecasts_live(cfg)`
   opens the forecast DB **autocommit** (tiny per-row self-commit so no write lock is held across the
   provider network fetch — avoids the forecast-DB "database is locked" contention the heavy capture
   guards against with BEGIN IMMEDIATE), delegates to the dispatcher, fail-soft. **Wired into
   `_replacement_forecast_download_cycle`** after the gridded + bayes capture, so station data lands
   on the SAME lane and cadence as the gridded raw inputs (publish-time cron + boot catch-up at +90s).

3. **Config flip** — `cwa_township` + `hko_fnd` → `enabled: true`, `status: "live"`; `_doc` rewritten
   to describe the live ingest path.

4. **Key-casing silent-no-op fix** — `resolve_cwa_api_key` read the secret file key as `cwa_api_key`
   (lowercase, the documented contract) but the file had been written with `CWA_API_KEY` (uppercase,
   the env-var name) → CWA resolved no key → silent 0-row no-op. Fixed the file to the documented
   lowercase key AND hardened the resolver to accept **either casing** so it can never silently
   disable CWA again.

Tests: `tests/test_station_forecast_live_ingest_wiring.py` — dispatch routing, enabled-gating,
city/metric passthrough, per-source fail-soft, unknown-adapter skip, cycle-helper delegation +
fail-soft, and CWA key casing tolerance (both). **10 passed.**

## Validation (real network, throwaway temp DB — zero live-DB risk)

```
DISPATCH REPORT: {'cwa_township': 6, 'hko_fnd': 9}
cwa_township_single_runs: Taipei 2026-06-30..07-02  = 35.0, 35.0, 35.0 °C
hko_fnd_single_runs:      Hong Kong 2026-06-30..07-02 = 32.0, 33.0, 33.0 °C
```

CWA's own MOS reads **35°C** for Taipei vs the gridded basket's **~33** — exactly the warm,
settlement-aligned (RCSS-district 松山區) signal the deploy targets. Registry eligibility re-verified
(`cwa_township` / `hko_fnd`: tier=experimental, enabled_by_default=True, degradation=OK,
entry_primary=True). All four live modules import clean.

## Deploy

`com.zeus.forecast-live` restarted via `launchctl kickstart -k` (preflight substantive checks pass;
the lone preflight FAIL is the trading-daemon `src.main`-already-running guard, irrelevant to a
forecast-daemon restart). New PID 65837 booted clean. Boot catch-up (+90s) runs the wired station
ingest; the 5-min materialize then applies the per-city scheme.

## Live confirmation (pending → monitor `bkt9kgb3v`)

Watching for: fresh `cwa_township_single_runs` / `hko_fnd_single_runs` rows post-restart, then a
fresh Taipei posterior whose provenance contains `cwa_township` and a fresh HK posterior containing
`hko_fnd`. The monitor also emits a fresh-posterior-WITHOUT-cwa signal so a missed gate is not
silently invisible.

## Lesson

A serve-logic unit check ≠ the daemon invoking that path. The discriminator is the **persisted
output's method/provenance**, not that the function works when you call it by hand. Reinforces
`verify-live-decision-source-matches-validation-source` and the "confirm by making it RUN" antidote.
