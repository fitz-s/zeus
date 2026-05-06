# Zeus Data Architecture Reference

Durable reference for database topology, data tables, provenance contracts,
ingestion mechanics, coverage tracking, and dual-track identity.

Authority: executable source, tests, machine manifests, and authority docs win
on disagreement with this document.

---

## 1. Database Topology

### 1.1 Three-DB split

| Database | Physical path | What it stores | Who writes |
|----------|---------------|---------------|------------|
| **zeus_trades.db** | `state/zeus_trades.db` | position_events, position_current, trade_decisions, chronicle, shadow_signals, risk_actions, strategy_health, selection facts | cycle_runner, executor, harvester, riskguard |
| **zeus-world.db** | `state/zeus-world.db` | ensemble_snapshots, calibration_pairs, platt_models, settlements, observations, observation_instants, forecasts, model_bias, solar_daily, diurnal_curves, data_coverage | data ingest, harvester, calibration |
| **risk_state.db** | `state/risk_state.db` | risk_state (level history) | riskguard only |

Connection helpers:
- `get_trade_connection()` → zeus_trades.db
- `get_world_connection()` → zeus-world.db
- `get_trade_connection_with_world()` → zeus_trades.db with ATTACH zeus-world.db as "world" for cross-DB joins

All connections use WAL mode and foreign keys ON.

The legacy `zeus.db` path still exists but is not the canonical data surface.

### 1.2 Authority hierarchy

```
Chain (Polymarket CLOB)
  > position_events (append-only ledger)
    > position_current (projected view)
      > JSON portfolio exports (derived cache)
```

DB commits precede JSON export writes. JSON is never truth — it is a
cache for fast reads that may be stale.

---

## 2. Core Data Tables

### 2.1 Ensemble snapshots

```sql
ensemble_snapshots (
    city, target_date, issue_time, valid_time, available_at, fetch_time,
    lead_hours, members_json, p_raw_json, spread, is_bimodal,
    model_version, data_version, authority, temperature_metric,
    UNIQUE(city, target_date, issue_time, data_version)
)
```

`members_json`: JSON array of 51 per-member daily max/min values. Used by
offline calibration rebuilds (`p_raw_vector_from_maxes()`) to recompute
P_raw from stored member extrema without needing hourly context.

4-timestamp constraint: `issue_time` (model run), `valid_time` (forecast
target), `available_at` (when data became usable), `fetch_time` (when Zeus fetched it).

### 2.2 Calibration pairs

```sql
calibration_pairs (
    city, target_date, range_label, p_raw, outcome,
    lead_days, season, cluster, forecast_available_at,
    settlement_value, decision_group_id,
    bias_corrected, authority, bin_source
)
```

Generated during settlement harvest: one pair per bin per settled market.
Winner bin gets `outcome=1`, others get `outcome=0`. These pairs feed Platt
model fitting via `bucket_key = f"{cluster}:{season}"`.

Authority gate: only `VERIFIED` calibration pairs are used for Platt fitting
in live inference. The authority filter is applied at query time in
`get_pairs_for_bucket()`.

### 2.3 Platt models

```sql
platt_models (
    bucket_key, param_A, param_B, param_C,
    bootstrap_params_json, n_samples,
    brier_insample, fitted_at, is_active,
    input_space, authority
)
```

`bootstrap_params_json`: JSON array of 200 `(A, B, C)` tuples for σ_parameter
in double-bootstrap CI. `input_space` is either `"raw_probability"` or
`"width_normalized_density"` — controls whether predict-time inputs are
divided by bin width.

### 2.4 Observation instants (hourly)

```sql
observation_instants (
    city, target_date, source, timezone_name, local_hour,
    local_timestamp, utc_timestamp, utc_offset_minutes,
    dst_active, is_ambiguous_local_hour, is_missing_local_hour,
    time_basis, temp_current, running_max, delta_rate_per_h,
    temp_unit, station_id, observation_count, raw_response,
    source_file, imported_at,
    UNIQUE(city, source, utc_timestamp)
)
```

DST-aware hourly timeline. Each row is one physical observation at one UTC
instant. The `is_ambiguous_local_hour` and `is_missing_local_hour` flags
capture DST transition semantics: ambiguous = fall-back (2 possible UTC
times for the same local hour), missing = spring-forward (local hour does
not exist).

### 2.5 Settlements

```sql
settlements (
    city, target_date, market_slug, winning_bin,
    settlement_value, settlement_source, settled_at,
    authority,
    UNIQUE(city, target_date)
)
```

Settlement truth = Polymarket settlement result. `winning_bin` is the market
outcome label. `settlement_value` is the WU/HKO/CWA temperature value
(observation authority, separate from market authority).

### 2.6 Position events (canonical ledger)

Governed by `src/state/ledger.py`. Append-only: rows are never updated or
deleted. Each event records a lifecycle transition with full typed columns.
`position_current` is a projected view rebuilt from the event stream.

### 2.7 Trade decisions

Full audit trail per trade: `p_raw`, `p_calibrated`, `p_posterior`, `edge`,
`ci_lower`, `ci_upper`, `kelly_fraction`, plus attribution fields
(`strategy`, `edge_source`, `discovery_mode`, `entry_method`,
`selected_method`), exit fields (`exit_trigger`, `exit_reason`,
`exit_divergence_score`), and domain object snapshots as JSON
(`settlement_semantics_json`, `epistemic_context_json`, `edge_context_json`).

---

## 3. Data Ingestion

### 3.1 Hourly instants (`hourly_instants_append.py`)

Source: Open-Meteo Archive API (`archive-api.open-meteo.com`).

Ingestion flow:
1. Chunk date range into 90-day windows (API soft limit)
2. Fetch `temperature_2m` hourly in city's settlement unit
3. Per-row validation:
   - Layer 1: unit consistency + earth records check (rejects sentinel values
     like 99999)
   - Layer 5: DST boundary check (rejects spring-forward nonexistent hours)
4. Write to `observation_instants` (INSERT OR REPLACE on UNIQUE constraint)
5. Coverage rollup: if ≥ expected hours for a local date → mark WRITTEN in
   `data_coverage`

Two daemon entry points:
- `hourly_tick()`: per-hour sweep, rolling 3-day window per city with
  per-city dynamic end_date (respects timezone)
- `catch_up_missing()`: boot-time entrypoint, fills MISSING/FAILED rows
  from `data_coverage`

### 3.2 Coverage tracking (`data_coverage` table)

```sql
data_coverage (
    data_table, city, data_source, target_date, sub_key,
    status, reason, fetched_at, expected_at, retry_after,
    PRIMARY KEY (data_table, city, data_source, target_date, sub_key)
)
```

Status values:
- `WRITTEN` — data exists and passes threshold
- `LEGITIMATE_GAP` — expected absence (HKO incomplete days, new city onboard)
- `FAILED` — fetch attempted but failed (has `retry_after` for backoff)
- `MISSING` — expected data not found by scanner

This is the data-immune-system's memory: live appenders flip rows to WRITTEN,
scanners write MISSING for unrecorded expected rows, known exceptions pinned
as LEGITIMATE_GAP.

### 3.3 `IngestionGuard` validation layers

Five validation layers, not all applied to all data types:

| Layer | What it checks | When skipped |
|-------|---------------|-------------|
| 1. Unit consistency | Settlement unit matches declared, earth records bounds | Never |
| 2. Physical bounds | Value within city seasonal p01-p99 range | Hourly (night readings below daily max p01) |
| 3. Seasonal plausibility | — | Deleted (2026-04-13) |
| 4. Collection timing | Observation after target date | Archive backfill (trivially true) |
| 5. DST boundary | Local hour exists (no spring-forward ghost) | Never |

---

## 4. Provenance & Authority

### 4.1 Authority ladder

Three levels: `VERIFIED`, `UNVERIFIED`, `QUARANTINED`.

- `VERIFIED`: provenance chain complete, eligible for live inference
- `UNVERIFIED`: data exists but provenance incomplete — excluded from
  Platt fitting, alpha computation refuses to proceed
- `QUARANTINED`: known-bad data, excluded from all computation

### 4.2 Truth file metadata

`truth_files.py` manages JSON truth file provenance:

```python
build_truth_metadata(path, *, runtime_state, generated_at, authority,
                     temperature_metric, data_version):
    → {"runtime_state", "generated_at", "source_path", "stale_age_seconds",
       "deprecated", "authority", "temperature_metric", "data_version"}
```

Fail-closed: low-lane files (`platt_models_low.json`,
`calibration_pairs_low.json`) stamped VERIFIED without
`temperature_metric` are downgraded to UNVERIFIED.

Runtime-state safety: `read_runtime_truth_json()` validates that the file is live
runtime truth. Retired selector tags or diagnostic/backtest provenance in a
runtime truth file fail closed with `RuntimeStateMismatchError`.

### 4.3 Market scan provenance

`MarketSnapshot` carries explicit provenance:
- `VERIFIED` — fresh network fetch succeeded
- `STALE` — network failed, cached data returned (with `stale_age_seconds`)
- `EMPTY_FALLBACK` — network failed and no cache available
- `NEVER_FETCHED` — initial state

---

## 5. Dual-Track Identity

### 5.1 HIGH vs LOW separation

High and low temperature families share local-calendar-day geometry but
do not share:
- `temperature_metric` (`"high"` / `"low"`)
- Physical quantity (HIGH=daily max, LOW=daily min)
- `observation_field` (HIGH=`tmax`, LOW=`tmin`)
- `data_version` (HIGH=`v1`, LOW assigned per low-track schema)
- Day0 causality (HIGH peaks mid-afternoon, LOW occurs overnight/early AM)
- Calibration family (separate Platt models)

### 5.2 `MetricIdentity` type safety

```python
class MetricIdentity:
    temperature_metric: str    # "high" or "low"
    physical_quantity: str     # "daily_maximum_temperature" or "daily_minimum_temperature"
    observation_field: str     # "high_temp" or "low_temp"
    data_version: str          # "v1" or low-track version

HIGH_LOCALDAY_MAX = MetricIdentity("high", ...)
LOW_LOCALDAY_MIN  = MetricIdentity("low", ...)
```

`EnsembleSignal` rejects bare strings for `temperature_metric` — must be a
`MetricIdentity` instance. Callers convert via `MetricIdentity.from_raw()`.

### 5.3 Dual-track implications

- Calibration pairs carry `bias_corrected` flag (0/1). If bias correction
  is enabled for live signals, all calibration pairs must also have been
  computed with bias correction — enforced by cross-module test.
- LOW track uses `add_calibration_pair_v2()` with explicit `metric_identity`
  and `data_version`. HIGH track uses `add_calibration_pair()` (legacy).
- Platt models are bucketed by `cluster:season`. HIGH and LOW have
  separate buckets (never mixed in fitting).

---

## 6. Replay & Backtest

Replay output goes to `zeus_backtest.db` — a derived audit surface.

```python
get_backtest_connection():
    """This DB is a reporting/audit surface only. Live runtime execution
    must not read it as authority or write trade/world truth through it."""
```

Replay is diagnostic until it preserves:
- Decision-time truth (the information available when the trade was made)
- Point-in-time probability vectors (not reconstructed with hindsight)
- Causally correct Day0 observation windows

Until these conditions are met, replay status is `diagnostic_non_promotion`.

---

## 7. Cross-References

- Domain model: `docs/reference/zeus_domain_model.md`
- Math spec: `docs/reference/zeus_math_spec.md`
- Market/settlement: `docs/reference/zeus_market_settlement_reference.md`
- Current data state: `docs/operations/current_data_state.md`
- Current source validity: `docs/operations/current_source_validity.md`
- Source AGENTS:
  - `src/data/AGENTS.md` — data ingest domain rules
  - `src/state/AGENTS.md` — state/lifecycle domain rules
  - `src/calibration/AGENTS.md` — calibration domain rules
