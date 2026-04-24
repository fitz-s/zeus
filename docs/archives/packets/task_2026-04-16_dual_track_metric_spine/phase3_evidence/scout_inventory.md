# Phase 3 Scout Inventory — Observation Layer

## 1. Provider Path Map

All providers live in `src/data/observation_client.py`. Each returns a dict with keys: `high_so_far`, `current_temp`, `source`, `observation_time`, `unit`. **CRITICAL: None produce `low_so_far` today.**

| Provider | Fetch Function | File:Line | Return Contract | Low Production |
|----------|---|---|---|---|
| **WU API (Priority 1)** | `_fetch_wu_observation()` | `observation_client.py:150-206` | `{high_so_far: float, current_temp: float, source: "wu_api", observation_time: raw_time, unit: city.settlement_unit}` | ❌ None |
| **IEM ASOS (Priority 2)** | `_fetch_iem_asos()` | `observation_client.py:209-263` | `{high_so_far: float, current_temp: float, source: "iem_asos", observation_time: local_valid, unit: "F"}` | ❌ None |
| **Open-Meteo (Priority 3)** | `_fetch_openmeteo_hourly()` | `observation_client.py:266-328` | `{high_so_far: float, current_temp: float, source: "openmeteo_hourly", observation_time: raw_time, unit: city.settlement_unit}` | ❌ None |
| **Entrypoint** | `get_current_observation()` | `observation_client.py:115-147` | Tries priority 1→2→3; raises `ObservationUnavailableError` if all fail. | ❌ None |

## 2. CITY_STATIONS / Parallel Registry Inventory

**Primary parallel map found:**

| Map Name | File:Line | Map Size | Purpose | Scope |
|----------|---|---|---|---|
| `CITY_STATIONS` | `daily_obs_append.py:111-171` | 45 cities | WU ICAO backfill + live append. Dict: `{city_name: (ICAO, country_code, unit)}` | Backfill + daemon live append |

**Config source (authoritative):**
- `src/config.py:212-214` — loads `config/cities.json` and builds `City` dataclass with `wu_station` field.
- `src/config.py:242` — exports `cities_by_name: dict[str, City]`.

**Dead-code flags:**
- `wu_daily_collector.py:24` — hardcoded WU API key literal (deprecated, unregistered in `src/main.py`). Phase C removes.

## 3. Consumers of observation_client return value

All callers read the dict returned by `get_current_observation()`. **No caller reads `low_so_far` successfully because it doesn't exist.**

| Caller | File:Line | Behavior | Low_So_Far Handling |
|--------|---|---|---|
| **monitor_refresh** | `monitor_refresh.py:237-239` | Wraps `get_current_observation()` in `_fetch_day0_observation()` | N/A — returns `None` dict if fetch fails |
| **evaluator** (primary consumer) | `evaluator.py:800` | Reads `candidate.observation.get("low_so_far")` for low-metric rejection gate | ❌ **Rejects low-track immediately if missing** (line 800-809) |
| **evaluator** (secondary reads) | `evaluator.py:812-829` | Passes to `Day0Signal` constructor; reads `high_so_far`, `current_temp`, `source`, `observation_time` | ✅ Handles None at line 814-816 but should never reach—line 800 rejects first |

**Rejection pattern (evaluator.py:794-809):**
```python
if temperature_metric.is_low() and candidate.observation.get("low_so_far") is None:
    return [EdgeDecision(False, rejection_stage="OBSERVATION_UNAVAILABLE_LOW")]
```

## 4. Dead Code & Simplification Candidates

**Category A: Deprecated collector**
- `wu_daily_collector.py` (200 lines) — replaced by `daily_obs_append.py`. Has hardcoded WU key (removed from `daily_obs_append.py`). Unregistered in `src/main.py`. Safe to delete Phase C.

**Category B: Duplication across backfill lanes**
- `daily_obs_append.py:198-250` (`_fetch_wu_icao_daily_highs_lows`) duplicates `scripts/backfill_wu_daily_all.py:217-330` exactly.
- `daily_obs_append.py:341-390` (`_fetch_hko_month`) duplicates `scripts/backfill_hko_daily.py:145-280` exactly.
- **Phase C plan** (noted in docstring `daily_obs_append.py:31-36`) extracts to shared clients (`wu_icao_client.py`, `hko_client.py`).

**Category C: Observation client design**
- No `Day0ObservationContext` dataclass exists. All three providers return bare dicts with the **same** contract but no type validation at seam.
- No defensive check that both `high_so_far` and `low_so_far` exist in returned dict (R-F invariant missing).

## 5. Forward Risk for Phase 4+

**Blocker for Phase 4 schema v2 migration:**
- `daily_obs_append.py` only writes to legacy `observations` table schema (high_temp, unit, source, fetched_at). Phase 4 must target v2 schema columns — check alignment with world DB v2 structure before exec-carol refactors.

**Risk: Half-wired low-metric**
- Evaluator line 800 already **rejects** cities with missing `low_so_far` for low-metric edge decisions.
- If Phase 3 ships without provider-side `low_so_far` production, all low-track evaluation halts at rejection gate — no data flows through to Day0Signal.
- R-F must enforce: provider raises exception (fail-closed) rather than returning `low_so_far=None`. Current pattern allows None.

**Risk: Temperature-unit consistency**
- WU returns `city.settlement_unit`; IEM ASOS hardcodes `"F"`; Open-Meteo returns `city.settlement_unit`. If IEM ASOS is used for non-F city, unit mismatch silent.
- No type constraint on unit field in dict contract.

---

**Inventory complete. Key anchors for exec-bob & exec-carol:**
- WU/IEM/Open-Meteo return contracts: `observation_client.py:150, 209, 266`
- Parallel CITY_STATIONS: `daily_obs_append.py:111`
- Evaluator low rejection gate: `evaluator.py:800`
- Consumer calls: `monitor_refresh.py:237`

---

## 6. Authority Hazards

**INV-09 violation risk (Data Availability First-Class Truth):**
- `observation_client.py:147` raises `ObservationUnavailableError` on all-provider failure ✅ correct.
- But no `availability_fact` entry written for missing/rate-limited observations. Phase 3 executors must not silently skip — record every rejection as `DataAvailabilityFact` per K3 zone rule.

**INV-14..INV-22 & FM-09 violation risk (MetricIdentity & Dual-Track Structure):**
- Phase 3 adds `low_so_far` field to observation dict. **FORBIDDEN:** bare dict with mixed `high_so_far` + `low_so_far` at seam.
- **REQUIRED:** exec-bob must wrap in typed `Day0ObservationContext` dataclass with explicit `temperature_metric` field. INV-14 (metric identity mandatory) applies at observation seam.
- FM-09 (no bare strings) blocks observation_client from returning `{..., temperature_metric: "high", ...}` dict — must be typed object.

**DT#1 violation risk (Truth commit ordering):**
- If `daily_obs_append.py` writes observations before committing, on crash recovery observations are stale. Phase C refactor must not move observation write before DB transaction completes.

**Phase 4 forward risk (SD-2 / World DB v2):**
- `observations` table unique key is `(city, target_date)`, not `(city, target_date, temperature_metric)`.
- Phase 3 can write both highs and lows to legacy table **only if** future Phase 4 cutover has explicit consolidation plan to v2 schema.
- Check with architect before exec-carol writes dual-track observations to legacy table without v2 migration path confirmed.

**Top 3 hazards:**
1. **INV-14 type seam** — bare dict forbidden; requires `Day0ObservationContext` wrapper
2. **INV-09 availability** — missing observations must record facts, not silent skip
3. **Phase 4 legacy table conflict** — dual fields in non-v2 `observations` table need v2 cutover guarantee
