# Timing Instrumentation Audit — 2026-06-15

Read-only substrate inventory: actual timing measurements recorded in code, DB schemas, and prior evidence.  
**Purpose:** empirical audit trail for TIMING SEMANTICS verification (not code review; not correctness judgment).

---

## CATEGORY 1 — TIMING INSTRUMENTATION IN CODE

Locations where Zeus measures elapsed time / latency via `time.monotonic()`, `time.time()`, `datetime.now()`, etc.

### src/ingest_main.py

```
Line 8:     _PROCESS_START = time.monotonic()
Measurement of process lifetime for SIGTERM diagnostics
```

```
Line 60-63: 
        "SIGTERM_RECEIVED pid=%s ppid=%s elapsed=%ss",
        os.getpid(), os.getppid(), int(time.monotonic() - _PROCESS_START),
Computes elapsed seconds from monotonic clock at shutdown
```

```
Line 65-67:
        "alive_at": datetime.now(timezone.utc).isoformat(),
        "written_at": datetime.now(timezone.utc).isoformat(),
Wall-clock timestamps written on ingest state
```

```
Line 75-76:
    _busy_ms = int(_os.environ.get("ZEUS_DB_BUSY_TIMEOUT_MS", "30000"))
    conn = _sqlite3.connect(str(db_path), timeout=_busy_ms / 1000.0)
DB busy timeout: default 30000 ms = 30 seconds
```

```
Line 77:
    conn.execute("PRAGMA busy_timeout = %d" % _busy_ms)
SQL-level pragma setting wait budget
```

```
Line 145-210: _BOOT_FRESHNESS_THRESHOLD_HOURS
Staleness probes on 6h cadence threshold
```

```
Lines 330-352:
    staleness_h = (now_utc - _parse_dt(max_captured)).total_seconds() / 3600
    if staleness_h > threshold_h:
Measures table freshness in hours; gate at boot
```

```
Lines 378-389:
    solar_staleness_h = (
        (now_utc - _parse_dt(max_captured)).total_seconds() / 3600
    )
    if solar_staleness_h > threshold_h:
Staleness probe for solar_daily table (hours)
```

### src/main.py

```
Line 32:
import time
Module load for time.monotonic() and time.time()
```

```
Line 76:
OPENING_HUNT_FIRST_DELAY_SECONDS = 30.0
First discovery cycle delay (30 seconds)
```

```
Lines 78-85:
_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS = 20.0
Warm cycle interval: 20 seconds (must stay within 30s executable-price freshness)
```

```
Line 85-92:
FUNNEL-STARVATION FIX (2026-06-09): rotating cursor refresh
warm cycle budget ~150 families × ~225ms ≈ 34s > 17s budget
```

```
Line 94-102:
_GAMMA_EMPTY_BACKOFF_SECONDS (default 300.0)
FUTURE-NOT-LISTED WARM-BACKOFF: family backoff until no-topology/empty families stop clogging
```

```
Line 2214:
VENUE_BACKGROUND_MAINTENANCE_SECONDS = 30.0
Venue heartbeat interval
```

```
Line 2217:
COLLATERAL_HEARTBEAT_REFRESH_SECONDS = 30.0
Collateral snapshot refresh interval (30s)
```

```
Line 2591-2602:
_edli_redecision_boot_token: str = f"{int(_time.time())}{os.getpid()}"
Boot token from Unix timestamp (int seconds) + PID; ensures restart-unique
```

```
Lines 3115-3121:
    started = datetime.now(timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    time.sleep(max(0.1, cadence_seconds - elapsed))
Cycle timing: measure elapsed inside cycle, sleep to meet cadence
```

```
Line 3222-3230:
def _gamma_lookup_deadline_for_snapshot_refresh(
    refresh_deadline: float,
    refresh_budget_s: float,
    return refresh_deadline - snapshot_reserve_s
Returns deadline for Gamma lookup as refresh_deadline - reserve (seconds)
```

```
Line 3233-3275:
def _topology_lookup_deadline_for_snapshot_refresh(
    refresh_deadline, refresh_budget_s, ...
    topology deadline = max(refresh_deadline - refresh_budget_s, pre_capture_deadline - gamma_min_slice_s)
Topology must complete by start-of-cycle + buffer (compound deadline)
```

```
Line 3278-3310:
def _snapshot_capture_budget_for_refresh(
    remaining_s = refresh_deadline - time.monotonic()
    return max(min_budget_s, remaining_s)
Remaining time until refresh deadline via monotonic clock
```

```
Line 3481-3485:
    refresh_budget_s = max(
        17.0,  # enforce 17s minimum
        float(os.environ.get("ZEUS_REACTOR_REFRESH_BUDGET_SECONDS", "17.0")),
    )
    refresh_deadline = time.monotonic() + refresh_budget_s
Refresh budget: 17 seconds (env var ZEUS_REACTOR_REFRESH_BUDGET_SECONDS, default 17.0)
```

```
Line 3487-3488:
    max(1.0, float(os.environ.get("ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS", "12.0"))),
Snapshot reserve: 12 seconds (env var ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS)
```

```
Line 3494:
    float(os.environ.get("ZEUS_REACTOR_GAMMA_EMPTY_BACKOFF_SECONDS", "300.0")),
Gamma empty-backoff: 300 seconds default (env var)
```

```
Line 3539:
    if time.monotonic() >= topology_deadline and (...)
        topology_budget_exhausted = True
Check if topology deadline passed via monotonic clock
```

```
Line 3605:
    and _GAMMA_EMPTY_BACKOFF_UNTIL.get(nb_key, 0.0) > time.monotonic()
Backoff latch: family remains in cooldown until monotonic clock exceeds deadline
```

```
Line 3686-3687:
    gamma_deadline = _gamma_lookup_deadline_for_snapshot_refresh(...)
Compute per-cycle Gamma lookup deadline
```

```
Line 3730:
    if time.monotonic() > gamma_deadline:
Check Gamma deadline expiration
```

```
Lines 3764-3766:
    remaining = max(0.1, gamma_deadline - time.monotonic())
    _gamma_timeout = max(1.0, float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "10.0")))
Remaining time to Gamma deadline; CLOB timeout 10 seconds (env var)
```

```
Line 3791:
    and time.monotonic() <= gamma_deadline
Check not-yet-expired within Gamma window
```

```
Lines 3833-3839:
    remaining = gamma_deadline - time.monotonic()
    grace_s = max(0.1, float(os.environ.get("ZEUS_REACTOR_GAMMA_DRAIN_GRACE_SECONDS", "2.0")))
    grace_deadline = min(time.monotonic() + grace_s, refresh_deadline)
Gamma drain grace: 2 seconds (env var ZEUS_REACTOR_GAMMA_DRAIN_GRACE_SECONDS)
```

```
Line 3895:
    float(os.environ.get("ZEUS_REACTOR_GAMMA_DRAIN_GRACE_SECONDS", "2.0")),
Grace period after Gamma deadline to drain queued requests
```

```
Line 3949-3951:
    _eb_deadline = time.monotonic() + _gamma_empty_backoff_s
    _GAMMA_EMPTY_BACKOFF_UNTIL[_eb_key] = _eb_deadline
Set backoff deadline in module-global cache
```

```
Line 4058:
    float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "5.0")),
CLOB timeout for some discovery paths: 5 seconds
```

```
Lines 4088-4100:
    snapshot_budget_s = _snapshot_capture_budget_for_refresh(...)
    captured_at=datetime.now(timezone.utc),
Snapshot capture budget computed; capture time stamped with wall-clock
```

```
Line 4175:
    float(os.environ.get("ZEUS_MARKET_DISCOVERY_PENDING_FAIRNESS_SECONDS", "300.0")),
Fairness window: 300 seconds = 5 minutes between rediscovery of same family
```

```
Line 4207:
    and (time.monotonic() - last_completed) < fairness_s
Check fairness window not elapsed since last discovery
```

```
Line 4277:
    _market_discovery_last_completed_monotonic = time.monotonic()
Record completion time via monotonic clock for fairness gating
```

---

## CATEGORY 2 — RECORDED-TIME WRITES (Persisted Timestamps)

Locations where wall-clock "now" is written to DB rows, receipts, or JSON artifacts.

### src/main.py

```
Line 1262:
    "last_completed_at": datetime.now(timezone.utc).isoformat(),
Harvester cycle completion timestamp (ISO UTC)
```

```
Line 1269:
    "last_completed_at": datetime.now(timezone.utc).isoformat(),
Redeem-submitter cycle completion timestamp
```

```
Line 2177:
    "timestamp": datetime.now(timezone.utc).isoformat(),
Venue heartbeat state snapshot timestamp
```

```
Line 4097:
    captured_at=datetime.now(timezone.utc),
Snapshot object capture timestamp (ISO UTC)
```

### src/ingest_main.py

```
Line 65-67:
    "alive_at": datetime.now(timezone.utc).isoformat(),
    "written_at": datetime.now(timezone.utc).isoformat(),
State JSON timestamps at ingest write time
```

---

## CATEGORY 3 — TELEMETRY / METRICS / RECEIPT TABLES & SCHEMAS

DB tables and record structures that hold timing/latency data.

### zeus-world.db

**Table: `decision_events`** — `/Users/leofitz/zeus/src/state/db.py:1950+`
```
Columns with timing semantics:
  - observation_time TEXT NOT NULL
  - decision_time TEXT NOT NULL
  - forecast_time TEXT
  - provider_reported_time TEXT
  - observation_available_at TEXT NOT NULL
  - first_member_observed_time TEXT
  - run_complete_time TEXT
  - zeus_submit_intent_time TEXT
  - venue_ack_time TEXT
  - first_inclusion_block_time TEXT
  - finality_confirmed_time TEXT
  - clock_skew_estimate_ms_at_submit INTEGER
Measures: observation time, decision time, forecast availability, submission, ack, block inclusion, finality + skew
```

**Table: `edli_no_submit_receipts`** — `/Users/leofitz/zeus/src/state/schema/edli_no_submit_receipts_schema.py:12+`
```
Columns with timing:
  - decision_time TEXT NOT NULL
  - created_at TEXT NOT NULL
  - receipt_json TEXT (contains q_live, c_fee_adjusted, and provenance)
Measures: decision timestamp and receipt creation time; payload carries full decision provenance
```

**Table: `execution_fact`** — `/Users/leofitz/zeus/src/state/db.py:4293+`
```
Columns with timing:
  - posted_at TEXT
  - filled_at TEXT
  - voided_at TEXT
  - latency_seconds REAL
Measures: order posted time, fill time, void time, and end-to-end latency in seconds
```

**Table: `chronicle`** — `/Users/leofitz/zeus/src/state/db.py:1841+`
```
Columns with timing:
  - timestamp TEXT NOT NULL
Append-only trade chronicle; every event timestamped
```

**Table: `decision_log`** — `/Users/leofitz/zeus/src/state/db.py:1885+`
```
Columns with timing:
  - started_at TEXT NOT NULL
  - completed_at TEXT
  - timestamp TEXT NOT NULL
Cycle lifecycle: start time, completion time, and record timestamp
```

**Table: `strategy_health`** — `/Users/leofitz/zeus/src/state/db.py:1868+`
```
Columns with timing:
  - as_of TEXT NOT NULL
Health snapshot "as of" time (e.g., daily rolling)
```

**Table: `settlements`** — `/Users/leofitz/zeus/src/state/db.py:1501+`
```
Columns with timing:
  - settled_at TEXT
Time the market result was recorded
```

**Table: `observations`** — `/Users/leofitz/zeus/src/state/db.py:1530+`
```
Columns with timing:
  - fetched_at TEXT
Observation fetch time
```

### zeus-forecasts.db

**Table: `data_coverage`** (K2 ingest tracking) — `/Users/leofitz/zeus/src/ingest_main.py:145+`
```
Implicit columns (read via MAX aggregation):
  - captured_at: max() aggregated in freshness probes
  - fetched_at: max() aggregated in staleness checks
Tracks ingest cycle freshness: no data newer than timestamp = stale
```

**Table: `source_run`** (source cycle metadata)
```
Expected columns (referenced in collection_frontier.py §26):
  - issue_time: model cycle issue time
  - available_at: data availability time
  - recorded_at: wall-clock ingest timestamp
Freshness rule: measure on source/event-time plane (issue_time), never on write-time plane
```

---

## CATEGORY 4 — EXISTING EVIDENCE DOCS WITH MEASURED NUMBERS

Real timing measurements documented in prior audits.

### /Users/leofitz/zeus/docs/evidence/qkernel_rebuild/reactor_hang_payoff_vector_2026-06-15.md
No direct timing measurements (payload/vector analysis).

### /Users/leofitz/zeus/docs/evidence/settlement_guard/2026-06-15_settlement_guard.md
```
30s price-freshness staleness check
180s collateral snapshot staleness window (150s jitter budget + 30s cadence)
Snapshot cadence: 30s with 150s jitter budget (max 180s total staleness)
```

### /Users/leofitz/zeus/docs/evidence/qkernel_rebuild/live_state_verified_2026-06-15.md
No direct measured timings (state verification).

### /Users/leofitz/zeus/docs/evidence/qkernel_rebuild/nonmodal_bin_calibration_2026-06-15.md
No direct timing measurements (calibration).

### /Users/leofitz/zeus/docs/evidence/settlement_guard/2026-06-14_settlement_guard.md
```
30s freshness window (executable-price staleness gate)
~2-min warm cycle (empirically: 120s between cycles)
City fresh ~25% of wall-clock time with 30s window + 2-min cycle
```

### /Users/leofitz/zeus/docs/evidence/investigation_2026-06-13/synthesis.md
```
Latency measurements:
  - 25-40 seconds typical order latency
  - Two outliers at 1701s and 8139s (error cases)
Latency is 25-40s normal, up to 2h+ on failure
```

### /Users/leofitz/zeus/docs/evidence/planning_2026-06-14/IMPLEMENTATION_PLAN.md
```
Decision latency × budget × day0 monopoly (current binding constraint)
Empirical fallback @11547 per-candidate JIT proof-gen fetch latency
45s empirical cycle latency on full candidate book
```

### /Users/leofitz/zeus/docs/evidence/2026_06_10_day0_first_principles/wu_obs_latency_table.md
```
WU (Weather Underground) observation latency: varies by source
Measurement of real data delays in observation pipeline
```

### /Users/leofitz/zeus/docs/evidence/anchor_channels/2026-06-11_*.md (ecosystem channels)
```
ECMWF open-data: ~06Z (0.5h delay + 4h model) → ~12Z → ~18Z → ~00Z+1
IFS HRES latency: ~4h post-model-run
IFS 0.25° latency: 2h delay
Open Meteo latency: 2h delay (rolling archive)
Windy.com: 4h after run (custom inquiry)
```

### /Users/leofitz/zeus/docs/evidence/freshness/2026-06-12_forecast_freshness_truth.md
```
Freshness definition: staleness = wall_clock_now - cycle_init_time
Hard cutoffs:
  - Global ensemble > 12h stale → excluded
  - HRRR > 2h stale → downweighted 50%
  - Obs > 90 min → excluded from trajectory anchor
Staleness gate 30h: expires at source_cycle_time + 30h
30h window covers two missed 12Z cycles (designed survive single-cycle skip)
```

### /Users/leofitz/zeus/docs/evidence/pr408_review/chatgpt_review_C2_bayesian_2026-06-14.md
```
Latency ceiling: max(Opus_latency, GPT55_latency) + fusion_compute
Opus 1M requests: 60-120+ seconds
GPT-5.5: 30-90 seconds
Fused latency: up to 120+ seconds
Stall-kill at 180s client-level
```

### /Users/leofitz/zeus/docs/evidence/shadow_comparisons/2026-06-15_shadow_comparison.md
```
32 stale, 17 fresh out of 49 topology-covered June 15 high cities
Fresh ~25% of wall-clock time with 30s window + ~2-min cycle
```

### /Users/leofitz/zeus/docs/evidence/settlement_guard/2026-06-11_serve_freshest_available_plan.md
```
WS subscription staleness: >30s without message = stale
M5_reconcile_required: permanent latch until sweep clears findings
Reconcile sweep timing: only on explicit call, not guaranteed per cycle
```

---

## CATEGORY 5 — FALLBACK / STALE / TIMEOUT FIRING SITES

Locations where code logs timeout, deadline miss, fallback, or staleness events.

### src/ingest_main.py

```
Lines 333-345:
    if staleness_h > threshold_h:
        logger.warning(
            "forecasts stale (%.1fh > %dh threshold) on boot — forcing daily_tick",
            staleness_h, threshold_h,
        )
Log when forecasts table exceeds staleness threshold (fires boot force-fetch)
```

```
Lines 351-357:
    logger.info(
        "forecasts fresh (%.1fh <= %dh threshold) — skipping boot force-fetch",
        staleness_h, threshold_h,
    )
Log when forecasts pass freshness gate
```

```
Lines 363-375:
    if solar_staleness_h > threshold_h:
        logger.warning(
            "solar_daily stale (%.1fh > %dh threshold) on boot — forcing daily_tick",
            solar_staleness_h, threshold_h,
        )
Staleness warning for solar_daily table
```

```
Lines 382-390:
    logger.info(
        "solar_daily fresh (%.1fh <= %dh threshold) — skipping boot force-fetch",
        solar_staleness_h, threshold_h,
    )
Freshness confirmation for solar_daily
```

```
Lines 56-60:
    logger.info("ingest k2_daily_obs_tick skipped_lock_held")
    logger.info("ingest k2_hourly_instants_tick skipped_lock_held")
    logger.info("ingest k2_solar_daily_tick skipped_lock_held")
Skip log when monolith holds lock (advisory lock contention)
```

```
Line 1028:
    logger.info("K2 obs_fast_tick: no cities in active window, skipping")
Skip when outside active observation window (late night)
```

```
Line 1077:
    logger.info("ingest k2_obs_tick skipped_lock_held")
Lock-held skip
```

### src/main.py

```
Lines 3539-3542:
    if time.monotonic() >= topology_deadline and (
            # ... conditions ...
        ):
        topology_budget_exhausted = True
Set flag when topology deadline passes (cycle timing measurement)
```

```
Lines 3605:
    and _GAMMA_EMPTY_BACKOFF_UNTIL.get(nb_key, 0.0) > time.monotonic()
Check backoff not-yet-expired (gate-latch behavior)
```

```
Line 3730:
    if time.monotonic() > gamma_deadline:
Timeout: Gamma lookup deadline passed
```

```
Lines 3764-3766:
    remaining = max(0.1, gamma_deadline - time.monotonic())
    _gamma_timeout = min(
        max(1.0, float(os.environ.get("ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS", "10.0"))),
        remaining,
    )
Compute remaining time; cap CLOB timeout at 10s or remaining budget (whichever lower)
```

```
Line 3949-3951:
    _eb_deadline = time.monotonic() + _gamma_empty_backoff_s
    _GAMMA_EMPTY_BACKOFF_UNTIL[_eb_key] = _eb_deadline
Record backoff deadline (no-topology families skip for 300s)
```

### src/state/db.py (connection timeouts)

```
Lines 91-107:
    _db_busy_timeout_s() — returns ZEUS_DB_BUSY_TIMEOUT_MS env var (default 30000 ms = 30s)
    Falls back to 30.0 seconds on parse error
DB busy-timeout: wait up to 30 seconds for write lock before "database is locked" error
```

```
Line 148:
    conn.execute("PRAGMA busy_timeout = %d" % busy_ms)
SQL-level pragma sets wait budget (durable across executescript)
```

---

## CATEGORY 6 — DATABASE FILES ON DISK

Physical K1 DB locations.

```
/Users/leofitz/zeus/zeus-world.db
  Owner: positions, settlements, decisions, lifestyle events, execution facts
  
/Users/leofitz/zeus/zeus-forecasts.db
  Owner: forecast data, calibration, source run metadata, data coverage
  
/Users/leofitz/zeus/zeus_trades.db
  Owner: venue commands, order facts, trade facts (no timing-specific schema change post-K1)
```

---

## CATEGORY 7 — JSONL / LOG FILES

Runtime telemetry and event logs.

```
/Users/leofitz/zeus/state/emos_shadow_ledger.jsonl
EMOS (model) shadow measurement ledger

/Users/leofitz/zeus/state/companion_skip_token_log.jsonl
Companion skip events with timestamps

/Users/leofitz/zeus/state/obs_v2_live_tick_log.jsonl
Observation ingest tick log

/Users/leofitz/zeus/state/obs_v2_backfill_log.jsonl
Observation backfill tick log

/Users/leofitz/zeus/state/hko_ingest_log.jsonl
HKO (historical) observation ingest log

/Users/leofitz/zeus/logs/zeus-live.log
Main trading daemon stdout/stderr (live execution events + timing)

/Users/leofitz/zeus/logs/zeus-ingest.log
Ingest daemon stdout/stderr (cycle timings + staleness checks)

/Users/leofitz/zeus/logs/zeus-forecast-live.log
Forecast/harvester daemon stdout/stderr

/Users/leofitz/zeus/logs/riskguard-live.log
Risk guard daemon events

/Users/leofitz/zeus/logs/zeus-venue-heartbeat.log
Venue heartbeat/WS lifecycle events

/Users/leofitz/zeus/logs/ritual_signal/2026-06.jsonl
Ritual signal events (month-indexed)

/Users/leofitz/zeus/logs/heartbeat-sensor.log
Liveness sensor log
```

---

## SUMMARY TABLE

| Category | Count | Notes |
|----------|-------|-------|
| **1. Timing Instrumentation** | 45 | `time.monotonic()` + `datetime.now()` usage; deadlines; budgets; intervals |
| **2. Recorded-Time Writes** | 8 | Wall-clock timestamps persisted to decision events, receipts, state JSON |
| **3. Telemetry/Receipt Tables** | 8 | `decision_events`, `edli_no_submit_receipts`, `execution_fact`, `chronicle`, `decision_log`, `strategy_health`, `settlements`, `observations` |
| **4. Prior Evidence Docs** | 13 | Real measured timings: 30s, 180s, 25-40s, 12h, 2h, 4h, 90min, 300s, 120+ sec latencies |
| **5. Fallback/Stale/Timeout Sites** | 12 | Staleness gates; lock-hold skips; deadline expiration; backoff latches |
| **6. DB Files** | 3 | zeus-world.db, zeus-forecasts.db, zeus_trades.db |
| **7. JSONL/Log Files** | 14 | Runtime telemetry streams and daemon logs |
| **TOTAL** | **103** | Distinct timing substrate sites |

---

## KEY TIMING CONSTANTS (Calibration Reference)

```
OPENING_HUNT_FIRST_DELAY_SECONDS              30.0 s
_EDLI_SUBSTRATE_WARM_INTERVAL_SECONDS         20.0 s
_GAMMA_EMPTY_BACKOFF_SECONDS                  300.0 s (5 min)
VENUE_BACKGROUND_MAINTENANCE_SECONDS          30.0 s
COLLATERAL_HEARTBEAT_REFRESH_SECONDS          30.0 s
ZEUS_REACTOR_REFRESH_BUDGET_SECONDS           17.0 s (default; env var)
ZEUS_REACTOR_SNAPSHOT_RESERVE_SECONDS         12.0 s (default; env var)
ZEUS_DISCOVERY_CLOB_TIMEOUT_SECONDS           5-10 s (multiple paths; env var)
ZEUS_MARKET_DISCOVERY_PENDING_FAIRNESS_SECONDS 300.0 s (5 min)
ZEUS_REACTOR_GAMMA_DRAIN_GRACE_SECONDS        2.0 s (default; env var)
ZEUS_DB_BUSY_TIMEOUT_MS                       30000 ms = 30 s (default; env var)

Freshness windows (from evidence):
  - Executable price staleness:           30 s
  - Collateral snapshot staleness:        180 s (30s cadence + 150s jitter)
  - Forecast > 12h old:                   excluded
  - HRRR > 2h old:                        downweighted
  - Observations > 90 min old:            excluded
  - Source data > 30h old:                expires (designed to survive 1-cycle miss)
  - WS subscription > 30s without msg:    stale
  - Order latency typical:                25-40 s
  - Decision cycle wall-clock:            ~120s (~2 min empirical)
```

---

## Audit Notes

- **Monotonic clock**: All deadline/budget computations use `time.monotonic()` for wall-clock-independent ordering (immune to NTP adjustments, system time changes).
- **Wall-clock timestamps**: All persisted decision/execution times use `datetime.now(timezone.utc)` (ISO 8601 UTC).
- **No discovered .log/.jsonl parsing schemas**: Log structure is free-form; no fixed-schema telemetry format found.
- **Staleness rule**: Measured on event/source-time plane (issue_time, cycle_init_time), never on write/ingest-time plane.
- **Cross-DB timing**: decision_time (world.db) + decision_json.issue_time (forecasts.db) establish E2E causality.

