# Capture / Reactor Stall — Root-Cause of STALE Forecast Posteriors (2026-06-16)

```
# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: read-only diagnosis vs LIVE state/zeus-forecasts.db (immutable=1);
#   BAYES_PRECISION_FUSION_SPEC §6 F1; replacement_forecast_production.py capture lane;
#   replacement_forecast_materializer.py q-path (BLOCKER 5).
```

Read-only diagnosis. All DBs opened `?immutable=1`. No code changed (fix proposed, not applied).

---

## TL;DR

The q-path does NOT fetch providers; it reads a **pre-persisted `single_runs`
multi-provider capture** keyed on the EXACT `(city, metric, target_date,
source_cycle_time)` natural key. When that capture row is absent for a scope,
`replacement_forecast_materializer.py:966-975` returns `None` →
`replacement_q_mode=BAYES_PRECISION_FUSION_CAPTURE_MISSING` →
`q_shape=aifs_member_votes_soft_anchor` (the known-bad legacy shape).

**Today 56.4% (288/511) of LIVE posteriors are STALE_HISTORY_ONLY.** The root
cause is **not** an upstream-data delay and **not** a single hung query. It is a
CODE bug compounded by an OPS-level scheduler backlog:

1. **PRIMARY (CODE):** the self-healing re-run gate `_extras_cycle_incomplete`
   (`replacement_forecast_production.py:340-374`, threshold `_EXTRAS_COMPLETE_THRESHOLD=200`)
   counts **total rows for the cycle, blind to per-`target_date`/per-city
   coverage**. The near-day (`lead=0`) leg alone is ~382 rows for one cycle,
   which exceeds 200, so the gate declares the whole cycle "complete" and
   **skips the extras fan-out** while the `lead+1`/`lead+2` city targets are
   still un-captured. Those scopes are then permanently stranded for that cycle.
   The skip fired **318×** in the available ingest-log window (15× already today).

2. **SECONDARY (OPS):** the ingest daemon's APScheduler is running **~4.5 hours
   behind wall-clock** (poll at log-time `00:57:15` reports `now=05:27:31`). The
   00Z extras capture's HTTP work did not finish persisting until `captured_at
   09:22Z` (~9h after the 00Z cycle). During that long window every materialize
   tick on `lead+1` 00Z scopes correctly found no capture and stamped STALE. This
   is the same single-threaded backlog the latency audit measured as 65%
   `FORECAST_SNAPSHOT_READY` event expiry.

The two faults share the same disease: **capture throughput cannot keep up, and
the only "catch-up" mechanism (the incomplete-gate) is coverage-blind so it stops
re-trying before the slow lanes (`lead+1`/`lead+2`) finish.**

---

## The pipeline (verified)

`single_runs` capture (producer) and the q (consumer) are **decoupled**:

- **Producer:** `_replacement_availability_poll_tick` (ingest daemon, `interval
  minutes=5`, `src/ingest_main.py:932,1721`) →
  `_replacement_cycle_availability_poll_if_needed`
  (`replacement_forecast_production.py:403`) →
  `_download_bayes_precision_fusion_extra_raw_inputs_if_needed:262` →
  `download_bayes_precision_fusion_extra_raw_inputs`
  (`bayes_precision_fusion_download.py:858`) → INSERTs `single_runs` +
  `previous_runs` rows into `raw_model_forecasts` (zeus-forecasts.db). The same
  capture is ALSO wired into the trading daemon's `replacement_forecast_download`
  job (`replacement_forecast_production.py:720,738`) which is the lane that writes
  the `bayes_precision_fusion_capture` scheduler-health key.
- **Consumer (q-path):** `replacement_forecast_materializer.py:950-975` calls
  `read_current_instrument_values` (`replacement_current_value_serving.py:108`)
  to read the **persisted** rows on the SAME connection. **No network fetch in
  the q path** (BLOCKER 5). Empty `persisted_current` → `return None` →
  single-anchor fallback → legacy `aifs_member_votes_soft_anchor` shape.

Trigger/cadence: the producer is **probe-driven** on a 5-min poll. The extras
fan-out only runs when `_should_run_extras` is True
(`replacement_forecast_production.py:520-528`): (a) a fresh anchor/AIFS leg was
fetched this tick, OR (b) `_extras_cycle_incomplete(cfg)` returns True.

---

## Symptom, quantified (LIVE forecast_posteriors)

Today (`computed_at >= 2026-06-16`, n=511), from `provenance_json`:

| replacement_q_mode | capture_status | q_shape | n | % |
|---|---|---|---|---|
| BAYES_PRECISION_FUSION_CAPTURE_MISSING | STALE_HISTORY_ONLY | aifs_member_votes_soft_anchor | 288 | **56.4%** |
| FUSED_NORMAL_PARTIAL | PARTIAL_CURRENT | fused_normal_direct | 163 | 31.9% |
| FUSED_NORMAL_FULL | FULL_CURRENT | fused_normal_direct | 60 | 11.7% |

Chronic + regressing (STALE share by computed-day):

| Day | n | STALE | PARTIAL | FULL |
|---|---|---|---|---|
| 06-10 | 590 | 37% | 3% | 60% |
| 06-11 | 431 | 27% | 50% | 23% |
| 06-12 | 644 | 22% | 0% | 78% |
| 06-13 | 723 | 24% | 0% | 76% |
| **06-14** | 1101 | **71%** | 25% | 4% |
| **06-15** | 1129 | **72%** | 24% | 4% |
| 06-16 | 519 | 57% | 31% | 12% |

A clear regression around **06-14** (STALE 24%→71%).

---

## The smoking gun: STALE is concentrated at the lead+1 cycle whose capture truncated

STALE-by-lead today (`lead = target_date − source_cycle_date`):

| lead | total posteriors | stale | stale % |
|---|---|---|---|
| 0 | 3 | 3 | 100% |
| **1** | 314 | **293** | **93%** |
| 2 | 202 | 0 | 0% |

`single_runs` city-coverage per `(cycle, target_date)`:

| cycle | td=06-16 | td=06-17 (lead+1) | td=06-18 (lead+2) |
|---|---|---|---|
| **00Z (06-16T00)** | 49 cities | **9 cities** | 0 |
| 18Z (06-15T18) | 49 cities | **49 cities** | 33 cities |

- `lead=2` posteriors (0% STALE) consume the **18Z** cycle, which has full
  lead+1/+2 coverage → FUSED.
- `lead=1` posteriors (93% STALE) consume the **00Z** cycle, whose extras pass
  captured only **9/49** cities at lead+1 → CAPTURE_MISSING → legacy shape.

Per-posterior natural-key probe (STALE 00Z scopes computed AFTER the 00Z capture
landed at 09:22Z): Lucknow/Madrid/Manila/Mexico City/Miami/Moscow, all
`target_date=2026-06-17`, return **0** `single_runs` rows at the exact key. The
capture genuinely never wrote those rows — this is a coverage hole, not a key
typo (when the rows exist, the q fuses; the 18Z/lead+2 path proves the join works).

00Z `single_runs` `captured_at` histogram: **all 445 rows stamped a single
instant `09:22:51Z`** — the 00Z extras pass completed (partially) in one late
burst ~9h after the cycle, then the gate skipped further re-tries.

---

## Competing hypotheses — evidence for / against

**H1 — Capture self-healing gate is coverage-blind (PRIMARY, CODE). CONFIRMED.**
- FOR: `_extras_cycle_incomplete` counts `COUNT(*) WHERE source_cycle_time=?`
  (`replacement_forecast_production.py:365-370`), target_date/city-blind;
  threshold 200. The 00Z cycle has 445 rows (>200) yet only 9/49 lead+1 cities.
  `EXTRAS_CURRENT_CYCLE_COMPLETE_SKIPPED` logged 318× (15× today). Once the
  near-day rows push the count past 200, lead+1/+2 targets are never retried.
- AGAINST: none. The threshold's own docstring assumes "~50 cities × 2
  target_dates × N models × 2 metrics" but the gate never checks that shape.

**H2 — Scheduler/reactor backlog (SECONDARY, OPS). CONFIRMED, contributory.**
- FOR: ingest poll logs `now` ~4.5h behind real time (`00:57:15` line →
  `now=05:27:31`); 00Z extras not persisted until `captured_at 09:22Z` (~9h
  post-cycle). Matches the latency audit's 65% FORECAST_SNAPSHOT_READY expiry and
  4045s avg expiry lag. During the long capture window, every lead+1 00Z
  materialize correctly stamped STALE.
- AGAINST: backlog alone would self-heal once the capture lands — but H1's gate
  prevents the re-materialization from ever seeing a complete capture, so the
  STALE rows persist. H2 widens the window; H1 makes it permanent.

**H3 — Upstream OM `single_runs` data late/unavailable. PARTIAL / not dominant.**
- FOR: `bayes_precision_fusion_capture` scheduler health = FAILED, last_success
  02:25Z, last_failure 08:41Z, reason = a model list
  `['ecmwf_ifs','gfs_global','icon_global','icon_seamless','jma_seamless','ncep_nbm_conus','ukmo_global_deterministic_10km']`
  (the `global_models_unavailable` marker, `replacement_forecast_production.py:733-738`).
- AGAINST: **0** occurrences of "GLOBAL model(s) single_runs UNAVAILABLE" in the
  ingest log window; 11 models DID land for 00Z near-day (49 cities). The FAILED
  flag is from the trading-daemon lane on a transient global drop, not the cause
  of the lead+1 coverage hole. Openmeteo's ~10h publish lag is a floor on
  freshness but does not explain the 9-vs-49 lead+1 split within one cycle.

**H4 — Process restarts drop in-flight captures. MITIGATED (not current cause).**
- The capture already persists **per-chunk** with lock-retry
  (`bayes_precision_fusion_download.py:798-855,1152-1157`,
  "CHUNKED-DURABILITY 2026-06-11"), bounding loss to the in-flight chunk. The
  ingest daemon is restart-quiet by design (`src/ingest_main.py:935-941`). So
  restart-loss is not the dominant mechanism today; the gate-skip (H1) is.

**H5 — db_writer_lock contention serializes capture. MINOR.**
- `database is locked` appears in health for `edli_command_recovery`/
  `edli_event_reactor`, and the capture persist has a 6× 20s lock-retry
  (`bayes_precision_fusion_download.py:842-852`). Contention slows the pass
  (feeds H2) but the persist eventually succeeds (rows landed at 09:22Z); not the
  root.

**Ranked:** H1 (coverage-blind gate, CODE) ≫ H2 (scheduler backlog, OPS) >
H3 (transient upstream global drop) > H4/H5 (mitigated/minor).

---

## CODE vs OPS

- **H1 is CODE — fixable here.** It is the dominant, permanent contributor.
- **H2 is OPS** (the ingest daemon scheduler running hours behind) but is
  *amplified* by H1 and by capture being a serial per-city batched fetch across
  49 cities × 2-3 target_dates per tick. Decoupling capture demand from the
  coverage-blind gate (H1 fix) removes the permanence; throughput work (below)
  shrinks the window.

---

## Proposed fix (CONCRETE — not yet applied)

### Fix 1 (PRIMARY, CODE) — make the re-run gate coverage-aware, not row-count-aware

`src/data/replacement_forecast_production.py:340-374` `_extras_cycle_incomplete`.

Replace the flat `COUNT(*) < 200` probe with a **per-(target_date, city)
coverage** probe against the SAME target plan the capture builds from
(`build_replacement_forecast_current_target_plan`, already imported at :284).
Concretely: for the probe-resolved cycle, the gate returns True (run the extras)
when ANY planned `(city, metric, target_date)` scope lacks a `single_runs` row
(or a `previous_runs` substitute admissible per `read_current_instrument_values`)
for the **decorrelated provider set** at that exact natural key. Pseudocode:

```python
# replacement_forecast_production.py:_extras_cycle_incomplete
plan = build_replacement_forecast_current_target_plan(Path(forecast_db))
cycle_iso = cycle.astimezone(utc).isoformat()
# set of (city, metric, target_date) that already have ANY single_runs row at this cycle
have = {(r[0], r[1], r[2]) for r in conn.execute(
    "SELECT DISTINCT city, metric, target_date FROM raw_model_forecasts "
    "WHERE source_cycle_time=? AND endpoint='single_runs'", (cycle_iso,))}
need = {(row.city, row.temperature_metric, row.target_date) for row in plan.rows}
return bool(need - have)   # incomplete iff any planned scope is uncovered
```

This makes the self-healing re-run continue across ticks until **every planned
lead's** scopes are captured — directly closing the lead+1/+2 hole. Fail-open
(any probe error → True) is preserved. The downloader is already per-row
idempotent (`bayes_precision_fusion_download.py:930-957,1094-1095`), so re-running
costs only the genuinely-missing fetches. Delete `_EXTRAS_COMPLETE_THRESHOLD`
(line 352) — it is the source of the coverage-blindness.

Touch point: `src/data/replacement_forecast_production.py:340-374` (rewrite body),
called at `:522`.

### Fix 2 (SECONDARY, throughput — reduce the OPS window)

The extras fan-out is serial per `(city, target_date)`
(`bayes_precision_fusion_download.py:968` loop, one batched HTTP pair per city).
49 cities × 2-3 target_dates per pass on a backlogged scheduler is the ~9h
window. Two options (lowest-risk first):

- **2a (preferred):** raise the extras fan-out's effective per-tick budget /
  parallelism by running the per-`(city,target_date)` batched fetches
  concurrently (bounded pool), so one pass covers all planned scopes within a
  tick instead of dribbling across ticks. The per-chunk persist
  (`_persist_chunk_with_lock_retry`, :798) already isolates DB writes, so only
  the HTTP `fetch` calls need a thread pool. Touch:
  `bayes_precision_fusion_download.py:968-1157`.
- **2b (OPS, no code):** investigate why the ingest APScheduler runs ~4.5h
  behind (poll `now` lag). Likely a long-running job on the same executor
  starving the 5-min poll (max_instances/coalesce interaction) or a blocking
  fetch holding the worker. This is the same root as the latency audit's 65%
  FORECAST_SNAPSHOT_READY expiry; fixing it shrinks every lane's window. (Out of
  scope for a code patch here — flagged for ops.)

**Fix 1 alone is expected to collapse the STALE share for any cycle that has been
live long enough for the re-run to complete (i.e. all lead+1/+2 scopes), because
it removes the permanence.** Fix 2 shrinks the transient window during which a
fresh cycle's lead+1 is briefly STALE.

### Optional Fix 3 (observability) — fix the FAILED-flag false signal

`replacement_forecast_production.py:733` flips `bayes_precision_fusion_capture`
to FAILED whenever `global_models_unavailable` is non-empty for a SINGLE tick.
A transient global drop on one tick should not latch a FAILED health status that
masks the real (coverage) problem. Consider requiring N consecutive global-drop
ticks before FAILED, or reporting coverage% instead. Touch: `:730-741`.

---

## References

- `src/data/replacement_forecast_materializer.py:950-975` — q-path reads persisted
  capture; empty → `return None` → STALE fallback (the symptom site).
- `src/data/replacement_current_value_serving.py:108-211` — `read_current_instrument_values`,
  the exact natural-key (`city, metric, target_date, source_cycle_time`) join.
- `src/data/replacement_forecast_production.py:340-374` — `_extras_cycle_incomplete`,
  **coverage-blind gate (ROOT CAUSE, Fix 1)**; `:352` threshold; `:520-528` call site.
- `src/data/replacement_forecast_production.py:262-329` — extras download wrapper +
  target plan; `:720-741` health-key writer (Fix 3).
- `src/data/bayes_precision_fusion_download.py:858-1201` — the download/persist job;
  `:798-855,1152-1157` per-chunk durability; `:968` serial per-city loop (Fix 2a).
- `src/ingest_main.py:932-955,1721-1723` — ingest-daemon 5-min poll registration.
- LIVE evidence: `state/zeus-forecasts.db` (immutable) — `forecast_posteriors.provenance_json`,
  `raw_model_forecasts`; `state/scheduler_jobs_health.json` (`bayes_precision_fusion_capture`
  FAILED); `logs/zeus-ingest.log` (318× `EXTRAS_CURRENT_CYCLE_COMPLETE_SKIPPED`,
  poll `now` ~4.5h behind wall-clock).
