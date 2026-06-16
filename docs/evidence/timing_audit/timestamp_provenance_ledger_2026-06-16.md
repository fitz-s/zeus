# Timestamp provenance ledger — "every replacement time has a reason" (2026-06-16)

```
Created: 2026-06-16
Source: workflow wo79nccpv (6 area agents + synthesis). Full per-site list (115 sites): tasks/wo79nccpv.output.
Purpose: the operator's root-cause audit — classify the BASIS of every persisted/compared timestamp write.
```

## Headline (the operator's thesis, quantified)
**79 of 115 persisted/compared timestamp sites carry a FABRICATED basis** — 16 GUESS + 32 SYNTHETIC_NOW +
31 NAIVE_CURRENT_TS. Only 36 are sound (15 REAL_SOURCE + 21 DERIVED_JUSTIFIED). ~69% of the system's timestamps
are written by guessing, not derived from a justified basis. This is why "everything doesn't work properly."

## CORRECTION to the master plan: it is NOT one money-path class
The funnel/capstone traced fetch→evaluate→place→fill and concluded "only C1 touches the money path." The
basis-lens finds money-path guesses in the **grading, exit, and expiry** paths the funnel never traversed. Ranked:

| # | site | target | basis | justified basis (the reason it should have) | money-path consequence |
|---|---|---|---|---|---|
| 1 | `harvester.py:1440` | `settlements.settled_at` (→ `dispatch_era_basis`) | SYNTHETIC_NOW | `obs_row['observation_time']` (station-reported extreme time = the settlement event); absent→NULL+`authority=QUARANTINED`. `recorded_at` stays now() as a SEPARATE var | **grades every position's P&L/ERA against the cron clock, not the observation time. Settlement=truth, on a guess.** |
| 2 | `replacement_forecast_materializer.py:1504-1508` | `forecast_posteriors.source_available_at` (FSR `available_at`) | GUESS | `max(source_run.fetch_finished_at)` across roles (the real download-complete wall-clock); NULL if unrecorded | **~8.35h early for AIFS → markets become SELECTION-eligible ~8h before the forecast is in hand** (the C1 root, exact site) |
| 3 | `fill_tracker.py:1089` | `position_current.entered_at` | SYNTHETIC_NOW | venue matchtime from the WS fill message; absent→NULL | `entered_at`→`hours_since_open`→`compute_alpha`→**biases every live exit** |
| 4 | `monitor_refresh.py:1187,1643` | `hours_since_open` fallback `48.0h` | GUESS | NULL/NaN → `compute_alpha` sees MISSING AUTHORITY and refuses; never a magic age | **inflates alpha confidence on exactly the broken-timing positions; drives exits** |
| 5 | `ecmwf_open_data.py:912,1355-1357,1461,1521,882-883` | `source_run.source_release_time/source_available_at` | GUESS | actual provider publication time (GRIB HTTP `Last-Modified`/first-fetch confirm); else NULL — NOT the calendar lag, NOT cycle_time | C1 avail-clock; fusion arrival-guard + readiness now()-compares; calendar-absent fallback writes cycle_time (~14h early for AIFS) |
| 6 | `ecmwf_open_data.py:936` + `entry_readiness_writer.py:186` | `readiness_state/source_run_coverage.expires_at` (+24h / +3h) | GUESS | `source_cycle_time + source_cycle_max_age_hours()` (calendar `max_source_lag_seconds` ~30h), matching the correct `replacement_forecast_materializer.py:2181` | **`expires_at` gates LIVE_ELIGIBLE — the +3h prematurely expires valid fusion triggers → SUPPRESSES real trades**; inconsistent windows across writers |
| 7 | `edli_position_bridge.py:911,978-979` | `execution_fact.posted_at`+`filled_at`; `entered_at` | SYNTHETIC_NOW | `matched_at/confirmed_at` from the EDLI WS fill payload; absent→NULL | latency + entered_at fed from bridge-run time, not fill time (real ts discarded in payload) |
| 8 | `executor.py:3063,4146,4171` | `venue_*_facts.venue_timestamp` (=ack_time) | SYNTHETIC_NOW | `matchTime` from the Polymarket REST/WS ack payload; absent→NULL; keep ack-receipt as LABELLED `observed_at` | anchors fill-truth ordering + provenance to Zeus's post-ack clock not the venue match event |
| 9 | `db.py:2089/:3422, :2132/:3476` | `readiness_state.recorded_at`, `market_topology_state.recorded_at` (DEFAULT CURRENT_TIMESTAMP) | NAIVE_CURRENT_TS | drop DEFAULT; caller supplies tz-aware ISO | **both COMPARED against tz-aware `expires_at` in the staleness gate controlling LIVE_ELIGIBLE + tradeable markets** — naive-vs-aware breaks the live gate on the Chicago host |
| 10 | `v2_schema.py:382` | `forecast_posteriors.recorded_at` (DEFAULT CURRENT_TIMESTAMP) | NAIVE_CURRENT_TS | drop DEFAULT; caller supplies tz-aware ISO | **`forecast_posteriors` is the q-authority every edge/buy reads** — naive recorded_at mis-ranks/mis-stales the posterior that sizes live trades |

Plus the ingest COLLECTION-plane collapse: `fetch_started_at = fetch_finished_at = captured_at = imported_at = computed_at`
(one `now()` at authority-chain write) — so the "proof-of-possession" basis the C1 fix wants is itself synthetic; the
real fix captures distinct events (pre-download / file-write-complete / post-commit). `expires_at=+24h`, `retrieved_at`
stamped pre-HTTP-call, `run_init_dt` fallback `now()` instead of `source_cycle_time` — all FIX.

## Prevention — extend the EXISTING BasisKind antibody to EVERY persisted timing value (4 enforced layers)
1. **BasisKind REQUIRED at the write boundary** — `log_settlement` / `append_order_fact` / `append_trade_fact` /
   `append_position_lot` / `log_execution_fact` / readiness writers take a mandatory `basis: BasisKind` arg beside
   each timestamp; persisting a time without one is a type error.
2. **GUESS BANNED on the money/decision path** — CI test: no site feeding `settled_at` / `entered_at` /
   `source_available_at` / `available_at` / `expires_at` / `venue_timestamp` / q / grade carries `BasisKind.GUESS`
   or a bare `datetime.now()` stamped as an event time (allowed only on telemetry-LABELLED fields).
3. **UNKNOWN → NULL, never back-fill** — writers pass NULL (+ authority QUARANTINED/UNVERIFIED) when the real basis
   is absent (mirrors `day0_nowcast_store` refusal + `bayes_precision_fusion_download.py:909`); a lint forbids
   `or datetime.now(` / `or _now_iso()` fallbacks on persisted event-time columns.
4. **NAIVE CI guard** — a schema test greps all DDL (`db.py` + `state/schema/*.py`) and FAILS on any
   `DEFAULT CURRENT_TIMESTAMP` / naive strftime on a timestamp column.

This is the C6 no-guessing gate made total: a persisted timing write lacking a documented, machine-checked basis fails the build.
```
