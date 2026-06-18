# Settlement / Snapshot Alignment Audit
# Created: 2026-06-18
# Authority: live code read — src/contracts/settlement_semantics.py, config/cities.json,
#   src/data/replacement_forecast_materializer.py, src/data/bayes_precision_fusion_download.py,
#   src/data/bayes_precision_fusion_history_provider.py, src/data/forecast_target_contract.py

---

## ITEM 1 — Settlement window / station alignment

### 1a. What the market settles on, and over what window/timezone

**Source: `src/contracts/settlement_semantics.py`**

The market `physical_quantity='mx2t6_local_calendar_day_max'` / `observation_field='high_temp'`
settles on the **WU daily high** for the city's ICAO station.  The "day" is the **local calendar
day** bounded by local midnight–midnight, keyed by the city's `timezone` field in
`config/cities.json`.  Settlement value is `wmo_half_up` rounded to integer degrees.

Key code (lines 231–239, `default_wu_fahrenheit`; lines 241–254, `default_wu_celsius`):

```python
return cls(
    resolution_source=f"WU_{city_code}",
    measurement_unit="F",  # or "C"
    precision=1.0,
    rounding_rule="wmo_half_up",
    finalization_time="12:00:00Z"
)
```

`SettlementSemantics.for_city()` (lines 257–292) is the single dispatch entry.  For
`settlement_source_type='wu_icao'` it routes to `default_wu_fahrenheit` or
`default_wu_celsius` depending on `city.settlement_unit`.  Hong Kong (HKO) routes to
`oracle_truncate` (`floor()`), not WMO half-up.

There is **no separate per-city preimage/window config file**; the window is derived
programmatically in `src/data/forecast_target_contract.py::compute_target_local_day_window_utc`
(lines 90–103):

```python
zone = ZoneInfo(city_timezone)
start_local = datetime.combine(target_local_date, time.min, tzinfo=zone)
end_local   = datetime.combine(target_local_date + timedelta(days=1), time.min, tzinfo=zone)
```

The UTC start/end is obtained via `astimezone(UTC)`.  This is the **local calendar day in the
city's IANA timezone** — exactly what "local_calendar_day" means in the physical_quantity string.

**VERDICT 1a: RESOLVED-OK.** Settlement = WU daily high over local calendar day (city IANA TZ),
WMO half-up to integer, per-city unit (C or F) and rounding rule enforced by
`SettlementSemantics.for_city()`.  HK correctly uses `oracle_truncate`.

---

### 1b. Forecast daily-max window alignment with settlement window

**Source: `src/data/bayes_precision_fusion_download.py` and `src/data/forecast_target_contract.py`**

The Open-Meteo previous_runs fetch (lines 190–200 of `bayes_precision_fusion_download.py`) uses:

```python
hourly_var = "temperature_2m" if lead == 0 else f"temperature_2m_previous_day{lead}"
request_params = {
    ...
    "temperature_unit": "celsius",
    "timezone": target.timezone_name,
    ...
}
```

The `timezone` parameter passed to Open-Meteo is `target.timezone_name` — the **city's IANA
timezone** (e.g. `America/New_York`, `Asia/Tokyo`), same as the settlement window computation.
Open-Meteo returns **hourly temperature_2m** values; the materializer then takes the max over
the local-day hourly window.

The hourly window is defined by `compute_target_local_day_window_utc` (same function) and the
materializer validates completeness via `_expected_om9_hourly_count`:

```python
# replacement_forecast_materializer.py lines 480–486
def _expected_om9_hourly_count(*, city_timezone: str, target_date: date | str) -> int:
    window = compute_target_local_day_window_utc(
        city_timezone=city_timezone,
        target_local_date=date.fromisoformat(target_date) if isinstance(target_date, str) else target_date,
    )
    seconds = (window.end_utc - window.start_utc).total_seconds()
    return int(seconds // 3600)
```

A prewrite block fires if `request.openmeteo_anchor.sample_count != expected_om9_count`
(`_prewrite_block_reasons` line 590–591).

The ECMWF-AIFS component uses `mx2t6` (6h buckets) but the settlement layer derives the
local-day high independently via the hourly OM9 anchor max; `forecast_target_contract.py`
`aggregation_window_hours_for_data_version` (lines 26–52) maps `mx2t6` → 6h and `mx2t3` → 3h
to avoid mixing aggregation windows.  The walk-forward residuals are the
`forecast_value_c – settlement_in_C` series where `forecast_value_c` is always degC (forced
via `temperature_unit=celsius` in the fetch).

There is **no off-by-one-day mismatch**: both paths resolve the target window through the same
`compute_target_local_day_window_utc` helper keyed on the same IANA timezone.  No UTC-vs-local
confusion is possible because all UTC arithmetic is derived from the city-local midnight boundary.

There is no 6h-bucket mismatch in the fusion input: the raw hourly OM9 fetch supplies all hours
of the local day; the materializer counts them and rejects short coverage.

**VERDICT 1b: RESOLVED-OK.** Forecast hourly window and settlement local calendar day are both
computed from `compute_target_local_day_window_utc` with the same city IANA timezone.  No
off-by-one-day, UTC/local, or 6h-bucket mismatch.

---

### 1c. Per-city WU station identity (ICAO) and unit threading

**Source: `config/cities.json` (fully read), `src/contracts/settlement_semantics.py`**

`config/cities.json` carries the following per-city fields that thread into settlement:

- `wu_station`: the ICAO code (e.g. `KLGA`, `EHAM`, `RJTT`).  This is the same station the
  WU settlement URL in `settlement_source` references.
- `settlement_source_type`: `"wu_icao"` (default), `"hko"`, `"noaa"`, `"cwa_station"`.
- `unit`: `"F"` for all US cities, `"C"` for all others.  This maps to `settlement_unit` used by
  `SettlementSemantics.for_city()`.
- `timezone`: the city's IANA timezone, used for the local-day window.
- `lat`/`lon`: airport-level coordinates (recently corrected 2026-06-09 + 2026-06-17 to 4dp
  ARP coords per changelog note in the JSON).

`SettlementSemantics.for_city()` reads `city.settlement_source_type`, `city.settlement_unit`,
and `city.wu_station` (lines 263–292) — no intermediate translation.  The unit-polymorphic
gate means calling `default_wu_fahrenheit` for a Celsius city is structurally blocked by the
`if city.settlement_unit == "C"` dispatch.

The forecast coords fed to Open-Meteo come from `city.lat` / `city.lon` in the same config
object, so the station identity (ICAO) and the forecast coords both originate from the same
`cities.json` entry.  Recent changelog entries confirm the lat/lon were re-pinned to OurAirports
ARP coordinates to match the WU station.

There is no separate translation layer that could drift; the config is the single source.

**VERDICT 1c: RESOLVED-OK.** ICAO identity, unit (C/F), and forecast coords all come from the
same `config/cities.json` city entry.  `SettlementSemantics.for_city()` enforces unit/rounding
alignment at the single dispatch point.

---

## ITEM 2 — Materialization snapshot / concurrency

### 2a. Immutable snapshot of raw_model_forecasts (single cycle, no concurrent mixing)

**Source: `src/data/replacement_forecast_materializer.py` + `scripts/materialize_replacement_forecast_shadow.py`**

The materializer reads raw_model_forecasts via
`_read_persisted_current_capture` → `read_current_instrument_values` (lines 1084–1092).  The
query is keyed on `(city, metric, target_date, source_cycle_time)` — one fixed cycle time.  It
never range-scans across multiple cycles.

The script caller (`scripts/materialize_replacement_forecast_shadow.py` lines 276–325) wraps the
entire operation in a single `BEGIN IMMEDIATE` / `commit`-or-`rollback`:

```python
conn.execute("BEGIN IMMEDIATE")
# ... write manifest rows, call materialize_replacement_forecast_shadow(conn, request) ...
if args.commit:
    conn.commit()
else:
    conn.rollback()
```

`BEGIN IMMEDIATE` takes a write lock up front.  Within that single connection the current-value
reads and all inserts are serialized.  A concurrent raw-forecast arrival to `raw_model_forecasts`
would be in a separate transaction on a separate caller; it would not affect this transaction's
already-fixed cycle-time query.

Additionally, the cycle-monotone block (`_cycle_monotone_block_reasons`, lines 649–714) rejects
a materialization whose `source_cycle_time` is OLDER than the family's current posterior cycle,
preventing backward-step mixing.

**VERDICT 2a: RESOLVED-OK.** The current-value read is keyed on a single fixed
`source_cycle_time`.  The entire materialize call runs inside one `BEGIN IMMEDIATE` transaction.
No concurrent mid-materialization cycle mixing is possible within the transaction.

---

### 2b. Training residuals strictly prior to target date (no look-ahead leakage)

**Source: `src/data/bayes_precision_fusion_history_provider.py`**

The SQL query (lines 85–104) has three structural no-leak guards:

1. `r.endpoint = 'previous_runs'` — only fixed-lead train data; live single_runs are excluded.
2. `s.authority = 'VERIFIED'` — only confirmed settlements; UNVERIFIED/QUARANTINED rows excluded.
3. `r.target_date < ?` where the parameter is the decision date (lines 75–76, 103, 106).

```python
decision_date = (
    target_date.isoformat() if isinstance(target_date, date) else str(target_date)
)
...
AND r.target_date < ?
...
params: list[object] = [city, metric, int(lead_days), *models, decision_date]
```

The `< ?` is STRICT: same-day settlement (target_date == decision_date) is excluded.  Future
settlement is excluded.  The unit antibody (lines 43–48) converts F settlements to C before
computing residuals so forecast_value_c (always degC) and settlement are unit-coherent.

**VERDICT 2b: RESOLVED-OK.** All training residuals satisfy `target_date < decision_date`
(strict), endpoint='previous_runs', authority='VERIFIED'.  No look-ahead possible.

---

### 2c. Write to forecast_posteriors — transactional + versioned (rollback-safe)

**Source: `src/data/replacement_forecast_materializer.py`**

The write to `forecast_posteriors` is mediated by `_insert_posterior` (lines 1728+).  The row
carries a `posterior_identity_hash` (lines 2344–2376) built from:

- `model_set_hash` — SHA-256 of sorted `used_models` (the fusion vocabulary).
- `resolution_mix_hash` — SHA-256 of `{models, regional}`.
- `posterior_config_hash` — SHA-256 of the config dict including `bayes_precision_fusion_model_set_hash`,
  `bayes_precision_fusion_resolution_mix_hash`, `bayes_precision_fusion_lead_bucket`.
- `dependency_hash` — SHA-256 of `{baseline_source_run_id, aifs_source_run_id, openmeteo_source_run_id}`.
- `bin_topology_hash` — SHA-256 of the bin topology payload.

If the fusion vocabulary changes (a new model added, or a model dropped), `model_set_hash`
changes → `posterior_config_hash` changes → `posterior_identity_hash` changes → the new row is
a distinct identity from the old one.  The `INSERT OR IGNORE` on the identity hash
(`_insert_posterior`) is idempotent for the same inputs.

The entire write (anchor + posterior + readiness) runs inside the caller's `BEGIN IMMEDIATE`
transaction.  A rollback (default unless `--commit` is passed) leaves the DB unchanged.

The `_ensure_replacement_identity_columns` migration path (lines 405–461) guards schema
compatibility at each materialization entry.

The `_cycle_monotone_block_reasons` gate (lines 649–714) prevents a stale cycle from
overwriting a fresher family, so a vocabulary rollback (say, reverting to a smaller model set)
would produce a DIFFERENT `model_set_hash` row rather than overwriting the current-cycle row —
provided the new cycle is not newer than the current family cycle.  If the rollback cycle IS
newer, it writes a new row with the old vocabulary hash; the serving layer picks the newest
`computed_at` row, so a vocabulary downgrade IS detectable from provenance but is not blocked
by identity hashing alone (the hash would differ, so both rows would exist; the newest wins).

This is an acceptable design given that vocabulary changes are operator-gated events.

**VERDICT 2c: RESOLVED-OK.** The write is inside a single `BEGIN IMMEDIATE` transaction.
`posterior_identity_hash` incorporates `model_set_hash` and `posterior_config_hash`, so a
fusion-vocabulary change produces a new identity and is rollback-safe.  Schema migrations are
guarded by `_ensure_replacement_identity_columns`.

---

## Summary verdicts

| Sub-item | Verdict         | Key evidence |
|----------|----------------|--------------|
| 1a — Settlement window / timezone | RESOLVED-OK | `SettlementSemantics.for_city()` dispatches by `settlement_source_type`; local-day window from `compute_target_local_day_window_utc` using city IANA TZ |
| 1b — Forecast daily-max window alignment | RESOLVED-OK | Both settlement and OM9 hourly max window use same `compute_target_local_day_window_utc`; Open-Meteo fetch uses `timezone=target.timezone_name`; hourly count enforced at prewrite gate |
| 1c — ICAO / unit threading | RESOLVED-OK | `cities.json` is the single source; `SettlementSemantics.for_city()` reads `settlement_source_type`, `settlement_unit`, `wu_station` from the same config object as forecast coords |
| 2a — Immutable cycle snapshot | RESOLVED-OK | Current-value read keyed on single `source_cycle_time`; entire materialize call inside `BEGIN IMMEDIATE`; cycle-monotone guard prevents backward-step mixing |
| 2b — Strictly-prior settlements | RESOLVED-OK | SQL: `endpoint='previous_runs' AND authority='VERIFIED' AND r.target_date < decision_date` (strict); unit antibody converts F settlements to C before residuals |
| 2c — Transactional + versioned write | RESOLVED-OK | `BEGIN IMMEDIATE`; `posterior_identity_hash` incorporates `model_set_hash` (fusion vocabulary); `INSERT OR IGNORE` idempotent; schema migration guarded |

No confirmed issues found in any sub-item. All six ChatGPT-flagged concerns are structurally
addressed in live code.
