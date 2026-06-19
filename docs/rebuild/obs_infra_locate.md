# Existing Observation Infrastructure Locate Report
*Generated 2026-06-17 via read-only codebase search*

## 1. EXISTING DAY-0 OBSERVATION / INTRADAY INGESTION

### Primary Source Files

#### src/data/day0_fast_obs.py
- **Function**: `fast_obs_source_for_city()` — registry lookup (returns optional FastObsSource)
- **Purpose**: Resolves free METAR feed (aviationweather.gov) for wu_icao cities
- **Source ID**: `aviationweather_metar` (FAST_OBS_SOURCE_ID constant)
- **Function**: `fetch_metar_reports()` — HTTP fetch from aviationweather.gov API
- **Function**: `running_extremes_for_local_day()` — computes high_so_far/low_so_far over local date
- **Function**: `filter_plausible_values()` — quarantines implausible METAR prints (climatology band + spike rule)
- **Function**: `get_fast_obs_emitter()` — process-wide emitter singleton (throttle + monotone memo)
- **Class**: `Day0FastObsEmitter` — stateful emitter: prefetch (HTTP) → emit (DB writes); split-memo (KILL + LIVE)
- **Event emission**: `DAY0_EXTREME_UPDATED` events to opportunity_events table

#### src/data/daily_obs_append.py
- **Function**: `append_wu_city()` — fetch specific date set for one WU city (wu_icao only)
- **Function**: `append_hko_months()` — fetch HKO CLMMAXT/CLMMINT pair per month
- **Function**: `daily_tick()` — daemon-facing per-hour entrypoint (WU peak+4h window, HKO current+prior month)
- **Function**: `catch_up_missing()` — boot entrypoint: fills MISSING/retry-ready FAILED via data_coverage
- **Source support**: WU ICAO history (v1/location/{ICAO}:9:{CC}/observations/historical.json), HKO Open Data API
- **Unit law**: C cities consume whole-C; F cities require T-group (tenths-C for exact conversion)

#### src/data/observation_instants_writer.py
- **Purpose**: Writes observation atoms to observations table with provenance
- **Callers**: daily_obs_append.py, backfill scripts

#### src/data/daily_observation_writer.py
- **Function**: `write_daily_observation_with_revision()` — observaations + daily_observation_revisions write path

#### src/data/observation_client.py
- **Class**: `Day0ObservationContext` (dataclass, slots=True) — typed observation snapshot
- **Fields**: current_temp, high_so_far, low_so_far, source, observation_time, unit, causality_status, station_id, sample_count, first_sample_time, last_sample_time, coverage_status, **observation_available_at**, provider_reported_time
- **Sources**: wu_api (live WU), wu_icao_history (backfill), iem_asos, openmeteo_archive, aviationweather_metar

#### src/data/day0_observation_reader.py
- **Purpose**: Queries observations table for (city, date, metric) to feed Day0 conditioner

#### src/data/day0_oracle_anomaly.py
- **Purpose**: WU-vs-METAR cross-check for oracle anomalies (Paris CDG tampering class)
- **Function**: `city_metar_settlement_faithful()` — checks measured WU-METAR divergence (config/wu_metar_divergence.json)
- **Function**: `note_metar_quarantine()` — notifies anomaly detector of quarantined prints

### Data Structures

#### src/types/observation_atom.py
- **Class**: `ObservationAtom` (frozen dataclass) — the ONLY write path into observations table
- **Provenance fields**: source, station_id, api_endpoint, fetch_utc, local_time, collection_window_start_utc/end_utc, rebuild_run_id, data_source_version, authority (VERIFIED/UNVERIFIED/QUARANTINED), provenance_metadata (JSON)
- **Unit contract**: raw_value/raw_unit may differ from value/target_unit; conversion explicit
- **Temporal fields**: fetch_utc (HTTP response completion), local_time (observation time, may be window midpoint)
- **Geographic/seasonal**: hemisphere (N/S), season (DJF/MAM/JJA/SON), month
- **Validation**: ObservationAtom.__post_init__() raises IngestionRejected for invalid_pass=False or authority/validation_pass mismatch
- **Contract**: Instances with validation_pass=False cannot be constructed

#### src/data/day0_fast_obs.py classes
- **FastObsSource**: source_id, station_id, authority, notes
- **MetarReport**: station_id, obs_time (UTC), receipt_time (publication clock), temp_c, metar_type, raw
- **FastObsExtremes**: city, station_id, target_date, unit, high_so_far, low_so_far, current_temp, first_obs_time, last_obs_time, last_receipt_time, sample_count, skipped_unit_law, quarantined_implausible
- **FastObsPrefetch**: eligible (tuple of city/source/date), reports (tuple), freshness_status, cache_age_s, decision_time
- **FastObsEmitter**: fetcher (callable), min_fetch_interval_s, _last_kill_memo_rounded, _last_live_emitted_rounded (SPLIT MEMO P0-1), _lock

---

## 2. EXISTING OBSERVATION DB TABLES

### observations (src/state/db.py:1437)
```sql
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    source TEXT NOT NULL,
    high_temp REAL,
    low_temp REAL,
    unit TEXT NOT NULL,
    station_id TEXT,
    fetched_at TEXT,
    -- K1 additions: raw value/unit contract
    high_raw_value REAL,
    high_raw_unit TEXT CHECK (high_raw_unit IN ('F', 'C', 'K')),
    high_target_unit TEXT CHECK (high_target_unit IN ('F', 'C')),
    low_raw_value REAL,
    low_raw_unit TEXT CHECK (low_raw_unit IN ('F', 'C', 'K')),
    low_target_unit TEXT CHECK (low_target_unit IN ('F', 'C')),
    -- Temporal provenance
    high_fetch_utc TEXT,
    high_local_time TEXT,
    high_collection_window_start_utc TEXT,
    high_collection_window_end_utc TEXT,
    low_fetch_utc TEXT,
    low_local_time TEXT,
    low_collection_window_start_utc TEXT,
    low_collection_window_end_utc TEXT,
    -- DST context
    timezone TEXT,
    utc_offset_minutes INTEGER,
    dst_active INTEGER CHECK (dst_active IN (0, 1)),
    is_ambiguous_local_hour INTEGER CHECK (is_ambiguous_local_hour IN (0, 1)),
    is_missing_local_hour INTEGER CHECK (is_missing_local_hour IN (0, 1)),
    -- Geographic/seasonal
    hemisphere TEXT CHECK (hemisphere IN ('N', 'S')),
    season TEXT CHECK (season IN ('DJF', 'MAM', 'JJA', 'SON')),
    month INTEGER CHECK (month BETWEEN 1 AND 12),
    -- Run provenance
    rebuild_run_id TEXT,
    data_source_version TEXT,
    -- Authority + extensibility
    authority TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (authority IN ('VERIFIED', 'UNVERIFIED', 'QUARANTINED')),
    high_provenance_metadata TEXT,  -- JSON
    low_provenance_metadata TEXT,  -- JSON
    UNIQUE(city, target_date, source)
);
```

### daily_observation_revisions (src/state/db.py:1483)
```sql
CREATE TABLE IF NOT EXISTS daily_observation_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT NOT NULL,
    target_date TEXT NOT NULL,
    source TEXT NOT NULL,
    natural_key_json TEXT NOT NULL DEFAULT '{}',
    existing_row_id INTEGER NOT NULL,
    existing_combined_payload_hash TEXT,
    incoming_combined_payload_hash TEXT NOT NULL,
    existing_high_payload_hash TEXT,
    existing_low_payload_hash TEXT,
    incoming_high_payload_hash TEXT NOT NULL,
    incoming_low_payload_hash TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (
        reason IN ('payload_hash_mismatch', 'missing_existing_payload_hash')
    ),
    writer TEXT NOT NULL,
    existing_row_json TEXT NOT NULL,
    incoming_row_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);
```

### observation_instants (zeus_trades.db, src/state/db.py:3119)
```sql
CREATE TABLE IF NOT EXISTS observation_instants (
    ...observation_available_at TEXT NOT NULL,
    ... (columns for instant-level observations for day0 hard-fact decisioning)
);
```

### day0_nowcast_runs (zeus_world.db, src/state/db.py:3773)
- Columns: observation_available_at (TEXT), observatory_id, decision_time, etc.
- Purpose: Tracks the day0 nowcast lane state per decision cycle

---

## 3. DAY0 Q PATH (Day0 Conditioner)

### src/forecast/day0_conditioner.py (AUTHORITATIVE SOURCE, lines 1-338)

**Public functions:**
- `probability_high_day0_bin(obs_high, lo, hi, normal_cdf)` — settlement-conditioned probability mass for HIGH-market bin
  - Returns 0.0 if hi <= obs_high (impossible bin)
  - Returns normal_cdf(hi) if lo <= obs_high < hi (all remaining-distribution mass below hi settles into observed bin)
  - Returns normal_cdf(hi) - normal_cdf(lo) otherwise (ordinary Normal interval)

- `probability_low_day0_bin(obs_low, lo, hi, normal_cdf)` — settlement-conditioned probability mass for LOW-market bin
  - Returns 0.0 if lo >= obs_low (impossible bin)
  - Returns 1.0 - normal_cdf(lo) if lo < obs_low <= hi
  - Returns normal_cdf(hi) - normal_cdf(lo) otherwise

- `day0_bin_preimage_native(bin_low, bin_high, *, rounding_rule, half_step=0.5)` — wraps `settlement_preimage_offsets()` to expand bin label to PREIMAGE bounds

- `condition_day0(*, metric, obs: Day0ObservationState, center_before_native: float)` → Day0Conditioning
  - Applies support clamp: mu_after = max(mu_before, obs_high) for HIGH; min(mu_before, obs_low) for LOW
  - Returns Day0Conditioning with active/observed_extreme_native/support_lower/support_upper/center_before/center_after/status

**Data structures:**
- `Day0ObservationState`: observed (bool), station_id, source, samples_count, latest_observed_at_utc, observed_high_native, observed_low_native, observed_extreme_native, raw_observation_hash
- `Day0Conditioning`: active (bool), observed_extreme_native, support_lower_native, support_upper_native, center_before_native, center_after_native, status (NO_DAY0/HIGH_CLAMPED/LOW_CLAMPED/OBS_SOURCE_MISSING_REFUSED)

**Calls to:**
- `src.contracts.settlement_semantics.settlement_preimage_offsets()` — THE single declarative source of per-city settlement PREIMAGE convention

### Settlement Rounding Contract
- `settlement_preimage_offsets(rounding_rule, half_step=0.5)` returns (low_offset, high_offset):
  - "wmo_half_up": (-half_step, +half_step) — symmetric
  - "oracle_truncate" / "floor": (0.0, +2·half_step) — asymmetric (HKO floor, "28.7 → 28")
  - "ceil": (-2·half_step, 0.0) — asymmetric opposite

### Cross-engine Day0 consumption
- src/engine/event_reactor_adapter.py — day0 absorbing-mask lane
- src/forecast/predictive_distribution_builder.py — calls day0_conditioner functions
- src/signal/day0_signal.py — day0 extreme centering/binning
- src/probability/joint_q.py — day0 probability integration

---

## 4. SETTLEMENT PREIMAGE / RULE RECONCILIATION

### src/contracts/settlement_semantics.py

**Main functions:**
- `settlement_preimage_offsets(rounding_rule, half_step=0.5)` — THE single declaration source (line 57-104)
  - Per-rule preimage bounds for bin label expansion
  - Authority: ensemble_signal.analytic_p_raw_vector_from_maxes preimage derivation

**SettlementSemantics class:**
- `round_values(values)` — apply settlement rounding via rule dispatch
- `round_single(value)` — mandatory gate for all settlement DB writes
- `assert_settlement_value(value, context)` — validate + round with SettlementPrecisionError on non-finite
- `for_city(city)` — SINGLE ENTRY POINT to construct SettlementSemantics from City object
  - Dispatches by settlement_source_type: wu_icao, hko, cwa, noaa
  - HKO special case: oracle_truncate (floor, "28.7 → 28"), NOT wmo_half_up

**Settlement rounding rules:**
- `round_wmo_half_up_values(values, precision)` — WMO asymmetric half-up (floor(x + 0.5))
- `apply_settlement_rounding(values, round_fn, precision)` — shared dispatch

**Type-encoded policies (SettlementRoundingPolicy ABC):**
- `WMO_HalfUp.round_to_settlement(raw_temp_c: CelsiusDecimal)` — accepts NewType over Decimal
- HKO_OracleTruncate (not shown but referenced)

**Related:**
- src/contracts/exceptions.py — SettlementPrecisionError
- src/types/temperature.py — Celsius, CelsiusDecimal, CelsiusBox NewTypes

---

## 5. SOURCE OVERLAY / REGISTRY

### Observed in src/ (no dedicated overlay/registry found yet)

**Fast-lane registry entry:**
- src/data/day0_fast_obs.py line 89-130: `fast_obs_source_for_city()` — returns FastObsSource or None
  - Policy: wu_icao cities → aviationweather METAR + settlement faithfulness gate
  - Uses config/wu_metar_divergence.json to exclude divergent stations

**Forecast source registry (parallel mechanism, NOT observation-specific):**
- src/data/forecast_source_registry.py — forecast tier/roles/gates, NOT observation sources

**Per-city configuration:**
- config/cities.json — settlement_source_type (wu_icao, hko, noaa, cwa), wu_station (ICAO), settlement_unit (F/C), timezone
- config/wu_obs_latency.json — per-city staleness_budget_min (report interval + publication delay)
- config/wu_metar_divergence.json — measured WU-vs-METAR integer divergence (Seoul RKSI class: ±1C on ~4.5%)
- config/city_monthly_bounds.json — per-city monthly climatology p01/p99 bands (unit-explicit per entry)

### Worktree percity-source-data
Located at .claude/worktrees/percity-source-data/
- Purpose: Per-city source-selection framework (operator mandate: "每个城市都应该有最好的天气预报")
- Related: scripts/per_city_model_mae.py — per-city per-model settlement-MAE validator for near-airport selection

---

## 6. LATENCY / PROOF-OF-POSSESSION

### Latency tracking

#### src/signal/day0_obs_latency.py
- `staleness_budget_minutes(city)` — max age (minutes) at which obs snapshot still reflects CURRENT running extreme
- Model source: config/wu_obs_latency.json
- Field: measured from observation_instants wu_icao_history raw METAR ts + settlement_day_observation_authority wu_api poll ages
- Consumers: src/engine/event_reactor_adapter.py day0 absorbing-mask lane
- Conservative default: 100 min (60 min cadence + 40 min delay)
- Plausible move rate: 2.5°C/h (C cities), 4.5°F/h (F cities); widening margin caps at 6h

### Proof-of-Possession / Availability Timing

#### observation_available_at (canonical field)
- **src/data/day0_fast_obs.py line 456-460:** "PUBLICATION CLOCK (PR#404 operator review P2)"
  - Source: extremes.last_receipt_time.astimezone(UTC).isoformat() (feed receiptTime, NOT fetch wall clock)
  - Falls back to observation_time (conservative lower bound) when receiptTime omitted
  - Mandatory for live authority (withheld if publication_clock_present = False)

- **src/state/day0_nowcast_store.py line 150-270:** Day0NowcastRun dataclass
  - Field: observation_available_at (Optional[str], UTC ISO timestamp)
  - Purpose: Wall-clock time Zeus could query the observation that fed this nowcast run
  - Source: Day0ObservationContext.observation_available_at (= now()-at-fetch per comment)

- **src/state/decision_events.py line 68, 237, 312:** DecisionEvent
  - Field: observation_available_at (str, MANDATORY for live decisions)
  - Gated: unconditional WHEN slot constraint (execution_intent.py line 963)

- **src/data/observation_client.py line 60:** Day0ObservationContext dataclass
  - Field: observation_available_at (str, UTC ISO; harvester write-back time, MANDATORY)
  - Field: provider_reported_time (Optional[str], UTC ISO; None = source doesn't expose separate reported-at)

#### Source run identity
- **src/forecast/types.py line 65-67:** ForecastType dataclass
  - Fields: source_run_id, source_cycle_time_utc, available_at_utc (datetime)
  - Purpose: Raw forecast artifact identity

- **src/state/db.py line 1819, 3119, 3144, 3156, 3773, 4174:** observation_available_at columns
  - zeus_world.db: opportunity_events, decision_events (provisional), day0_nowcast_runs
  - zeus_trades.db: observation_instants
  - Migrations: ALTER TABLE day0_nowcast_runs ADD COLUMN observation_available_at TEXT (line 4174)

#### Captured/Fetched timestamps
- **src/main.py line 601, 647, 673, 919:** Various captured_at / fetched_at fields
  - forecasts table: captured_at (data_coverage ledger)
  - solar_daily: no captured_at (fallback to data_coverage.fetched_at)
  - Event payload: generated_at OR updated_at OR observed_at OR captured_at (line 919)

---

## Key Infrastructure Gaps / Overlaps Identified

### 1. **Split-memo resilience (KILL + LIVE)**
   - src/data/day0_fast_obs.py: `_last_kill_memo_rounded` (monotone hard-fact kill) vs `_last_live_emitted_rounded` (live event emission)
   - **Overlap**: Addresses the exact problem statement (stale-withheld event not suppressing later fresh event)
   - **Implementation**: Round-2 P0-1 split allows recovery from opportunity_events durably (restart-safe)

### 2. **Publication clock (receipTime, not fetch time)**
   - src/data/day0_fast_obs.py line 447-460: Fast-lane emitter enforces observation_available_at = feed.receiptTime
   - **Overlap**: Canonical proof-of-possession / source timing (already live)
   - **Coverage**: WU path may still use fetch time (review needed)

### 3. **Plausibility quarantine (climatology + spike rule)**
   - src/data/day0_fast_obs.py: `filter_plausible_values()` with monthly climatology band + corroboration delay
   - **Gap**: No corresponding filter on WU path (daily_obs_append.py)
   - **Existing**: Quarantine stored in observations.authority='QUARANTINED' + oracle_anomaly tracking

### 4. **Per-city settlement faithfulness gate**
   - src/data/day0_fast_obs.py line 112-123: `city_metar_settlement_faithful()` check vs config/wu_metar_divergence.json
   - **Gap**: WU path lacks equivalent authorization check
   - **Status**: METAR-fast-lane only; WU still serves obs without faithfulness validation

### 5. **Observation latency budget in day0 decisioning**
   - src/signal/day0_obs_latency.py: staleness_budget_minutes per city
   - **Overlap**: Already consumed by absorbing-mask lane
   - **Gap**: Not wired into intraday entry-gate (Option-B monitor fallback still checks fresh cache age < 15min)

### 6. **No dedicated per-city source overlay loader**
   - **Gap**: Fast-lane registry is ad-hoc in fast_obs_source_for_city()
   - **Worktree**: percity-source-data branch has per_city_model_mae.py (forecast-centric, not obs-centric)
   - **Config**: sources still scattered (cities.json, wu_obs_latency.json, wu_metar_divergence.json)

