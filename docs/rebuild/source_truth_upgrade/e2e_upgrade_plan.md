# Zeus Polyweather Source-Truth + Day0 Revaluation Upgrade Plan

Generated: 2026-06-17

## Executive decision

This upgrade wires the audited city/source overlay into Zeus as a first-class `SourceTruthOverlay` plus a separate `Day0ObservationLikelihood` layer. It does **not** make every source an equal Bayesian model. The settlement source is the truth target; forecast models form the prior/fusion posterior; live observations on day 0 impose censoring and likelihood constraints on the final daily high/low.

## Hard laws

1. Active Polymarket market resolution rules are the single final source of truth.
2. Exact settlement source/station beats table priority. If a table row conflicts with current market rules or `config/cities.json`, it is shadow-only.
3. Standard cities use settlement airport geometry. Special city-center markets, currently Hong Kong/HKO, use official city-center geometry.
4. No network fetch is permitted in the q path. Fetchers persist raw artifacts and source_run proof-of-possession; q reads only persisted facts.
5. Source weights are learned from walk-forward residual covariance and latency variance. No operator-picked weights.
6. Day0 observations update the posterior through a high/low extreme likelihood; they do not replace the forecast posterior before the day is complete.

## New modules

- `src/source_truth/overlay_manifest.py`
- `src/source_truth/source_truth_loader.py`
- `src/source_truth/market_rule_reconciler.py`
- `src/data/day0_observation_fetcher.py`
- `src/data/observation_fact_repo.py`
- `src/forecast/day0_extreme_likelihood.py`
- `src/forecast/observation_precision_fusion.py`
- `src/engine/day0_revaluation_scheduler.py`
- `scripts/build_source_truth_overlay.py`
- `tests/test_polyweather_source_truth_overlay.py`
- `tests/test_day0_extreme_likelihood.py`

## Database additions

```sql
CREATE TABLE IF NOT EXISTS city_source_overlay (
  overlay_id TEXT PRIMARY KEY,
  city_key TEXT NOT NULL,
  city_name TEXT NOT NULL,
  source_family TEXT NOT NULL,
  role_layer TEXT NOT NULL,
  station_or_product_id TEXT,
  endpoint_url TEXT,
  geometry_policy TEXT NOT NULL,
  settlement_source_type TEXT NOT NULL,
  max_live_role TEXT NOT NULL,
  delay_minutes_target REAL,
  access_status TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  valid_from TEXT,
  valid_to TEXT
);

CREATE TABLE IF NOT EXISTS observation_facts (
  observation_id TEXT PRIMARY KEY,
  city_key TEXT NOT NULL,
  target_date TEXT NOT NULL,
  metric TEXT NOT NULL CHECK(metric IN ('high','low')),
  source_family TEXT NOT NULL,
  station_id TEXT NOT NULL,
  valid_time_utc TEXT NOT NULL,
  observed_temp_c REAL NOT NULL,
  observed_temp_native REAL,
  native_unit TEXT NOT NULL,
  captured_at_utc TEXT NOT NULL,
  proof_available_at_utc TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  raw_artifact_sha256 TEXT NOT NULL,
  settlement_geometry_distance_km REAL NOT NULL,
  observation_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_latency_facts (
  source_run_id TEXT PRIMARY KEY,
  source_family TEXT NOT NULL,
  station_id TEXT,
  nominal_valid_time_utc TEXT,
  fetch_started_at_utc TEXT NOT NULL,
  fetch_finished_at_utc TEXT NOT NULL,
  proof_available_at_utc TEXT NOT NULL,
  latency_seconds REAL NOT NULL,
  http_status INTEGER,
  artifact_sha256 TEXT
);

CREATE TABLE IF NOT EXISTS settlement_preimage_facts (
  fact_id TEXT PRIMARY KEY,
  market_id TEXT NOT NULL,
  city_key TEXT NOT NULL,
  target_date TEXT NOT NULL,
  metric TEXT NOT NULL,
  rule_source_url TEXT NOT NULL,
  station_id TEXT NOT NULL,
  settlement_unit TEXT NOT NULL,
  rounding_rule TEXT NOT NULL,
  finalization_status TEXT NOT NULL,
  final_temp_native REAL,
  final_temp_c REAL,
  captured_at_utc TEXT NOT NULL,
  evidence_hash TEXT NOT NULL
);
```

## Mathematical update

Forecast layer already produces `X ~ N(mu*, sigma_pred^2)` in Celsius. Day0 observations transform this into final high/low distribution:

- high: `Y = max(H_t, X_future_remaining)`
- low: `Y = min(L_t, X_future_remaining)`

where `H_t` is the maximum trusted observed value so far and `L_t` is the minimum trusted observed value so far after mapping through the settlement source transform. For each source `j`:

`σ_eff,j² = σ_sensor,j² + σ_rounding,j² + σ_lag,j²(Δt) + σ_station_mismatch,j² + σ_provider_transform,j²`

The observation precision is `τ_j = 1 / σ_eff,j²`. Correlated observations are fused through a shrink-to-diagonal covariance estimator, not summed independently. Bins impossible under observed extremes are zeroed before normalization: high bins below observed high-so-far become zero; low bins above observed low-so-far become zero.

## E2E execution flow

1. Build overlay manifest from audited CSV/XLSX.
2. Reconcile each active market against its live Polymarket rules: city, date, metric, unit, bin topology, source URL, station.
3. Fetch forecast model artifacts and persist source_run availability.
4. Fetch day0 observations on a 1–5 minute cadence by source family, respecting source rate limits.
5. Write observation_facts with raw artifact hashes and proof-of-possession availability.
6. Materializer reads persisted forecast current rows and computes existing replacement posterior.
7. Day0 updater reads observation_facts available at `computed_at`, computes source-specific likelihood and impossible-bin censoring.
8. Rebuild q through the existing settlement integrator and q_lcb/q_ucb bootstrap.
9. Run live gates: source exactness, proof-of-possession, latency, monotone cycle/effective time, FDR, Kelly.
10. Emit a revaluation receipt containing source rows, weights, covariance, latency, and changed q/edge.
11. Execution layer only adjusts positions if new edge survives the same FDR/Kelly/riskguard path as a normal candidate.
12. Harvester stores final settlement preimage and updates source residual/latency histories without hindsight leakage.

## Rollout

- Phase 0: shadow only, no execution. Build overlay + observation facts.
- Phase 1: replay last 90 settled markets/cells; require lower Brier/logloss and no semantic source violation.
- Phase 2: canary with max Kelly multiplier 0.05 and restricted cities with exact public sources.
- Phase 3: live for exact airport/HKO/NOAA classes, while restricted sources remain excluded.
- Phase 4: continuous monthly market-rule drift audit.
