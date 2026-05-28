# ENS New-City Data Parity Audit — 2026-05-24
# Created: 2026-05-24
# Authority basis: Read-only audit of live state/zeus-world.db, state/zeus-forecasts.db,
# data/oracle_error_rates.json, config/*.json, src/oracle/ddd_artifacts/*.json.
# No DB writes were made.

## Scope

Jinan and Zhengzhou vs. 52 established cities. Every per-city artifact that sits on the live trade-decision path. Decision path traced via `src/engine/evaluator.py` + `src/engine/ddd_wiring.py` + `src/strategy/`.

---

## 1. Data-Parity Matrix

Legend — **covered for 52?**: "full" = all 52 canonical cities present (or handled by tested fallback). "partial" = some of the 52 are also absent, noted.

| # | Artifact | Key / DB table+filter | How populated | Covered for 52? | Jinan present? | Zhengzhou present? |
|---|---|---|---|---|---|---|
| 1 | `config/cities.json` | `.cities[].name` | Config-authored (operator) | full — 52 entries | **ABSENT** | **ABSENT** |
| 2 | `config/city_monthly_bounds.json` | `.cities[city_name]` | `scripts/generate_monthly_bounds.py` reads `ensemble_snapshots` (v1, TIGGE) | partial — 46/52 (missing: Amsterdam, Guangzhou, Helsinki, Karachi, Manila, Qingdao) | **ABSENT** | **ABSENT** |
| 3 | `config/city_correlation_matrix.json` | `.matrix[city_name]` | `scripts/build_correlation_matrix.py` reads `ensemble_snapshots` (v1, TIGGE); fail-open to haversine fallback when absent | partial — 46/52 (same 6 missing as monthly_bounds) | **ABSENT** | **ABSENT** |
| 4 | `src/oracle/ddd_artifacts/v2_city_floors.json` | `.per_city[city]` | Derived: `max(p05_directional_coverage_train, 0.35)` over 2025 H2 `observation_instants_v2` data. Operator hand-authored per-city entries from offline script (RERUN_PLAN_v2 §C1). Status flags: 4 cities have `NO_TRAIN_DATA` / null floor (Hong Kong, Istanbul, Moscow, Tel Aviv) | partial — 51/52 (Paris present post-workstream-A resync; 4 have NO_TRAIN_DATA status which fail-closes DDD) | **ABSENT** | **ABSENT** |
| 5 | `src/oracle/ddd_artifacts/v2_nstar.json` | `.per_city_metric[city_high/city_low]` | Derived from `calibration_pairs_v2` ECE analysis (RERUN_PLAN_v2 §C4). N_star = smallest N where ECE std < 0.02 over 100-date sliding window. total_N_dates range: 350–840 across 51 cities (102 entries) | partial — 51/52 (same cities as v2_city_floors) | **ABSENT** | **ABSENT** |
| 6 | `data/oracle_error_rates.json` | dict key `[city_name][metric]` | Unique writer: `scripts/bridge_oracle_to_calibration.py`. Compares WU ICAO oracle snapshots (`raw/oracle_shadow_snapshots/`) to settled PM bins. Requires raw snapshot files captured at market-resolution time. | partial — 51/52 cities. Qingdao n=23 (CAUTION-threshold: p95=0.117, active). Note: 4 cities (Hong Kong, Istanbul, Moscow, Tel Aviv) have thin n (42–67). | **ABSENT** (status → MISSING, mult=0.5) | **ABSENT** (status → MISSING, mult=0.5) |
| 7 | `state/zeus-forecasts.db :: platt_models_v2` | keyed by `(metric, city, season, data_version)` | `scripts/refit_platt.py --no-dry-run --force` | **0 rows live** (table empty — platt_models_v2 not yet seeded for any city) | **ABSENT** | **ABSENT** |
| 8 | `state/zeus-forecasts.db :: calibration_pairs_v2` | `city` column | Archive TIGGE backfill → `scripts/rebuild_calibration_pairs_canonical.py` | full — all 52 cities present. London leads (2.6M rows). Qingdao thin (4,488 rows) | **ABSENT** | **ABSENT** |
| 9 | `state/zeus-world.db :: model_bias` (v1) | `city` column | Legacy; not on active decision path | partial — 52 cities but only 2/city rows (recent) | **ABSENT** | **ABSENT** |
| 10 | `model_bias_ens_v2` | DB table in zeus-forecasts.db; `city` column | `src/calibration/ens_bias_repo.py::write_bias_model()` from calibration_pairs_v2 + ensemble_snapshots_v2 | **Table does not exist in live zeus-forecasts.db** (schema not yet migrated; SCHEMA_FORECASTS_VERSION=7) | **ABSENT** | **ABSENT** |
| 11 | `state/zeus-forecasts.db :: ensemble_snapshots_v2` | `city` column | TIGGE archive backfill (ingest daemon) | full — all 52 cities. Qingdao thin (375 rows). Hong Kong thin (188 rows) | **ABSENT** | **ABSENT** |
| 12 | `state/zeus-world.db :: observation_instants_v2` | `city` column | WU-daily + OpenMeteo archive backfill | full — all 52 cities. Qingdao thin (522 rows), Hong Kong thin (188 rows) | **ABSENT** | **ABSENT** |
| 13 | `state/zeus-forecasts.db :: settlements_v2` | `city` column | PM settlement capture (live markets only) | full — all 52 cities present. Range: Lagos 35, London 500. Qingdao 23 (thin) | **ABSENT** | **ABSENT** |
| 14 | `state/zeus-world.db :: settlements` | `city` column | PM settlement capture (legacy; mirrored from forecasts) | full — all 52 cities | **ABSENT** | **ABSENT** |
| 15 | `src/data/tier_resolver.py :: TIER_SCHEDULE` | dict key `city_name` | Config-authored in `tier_resolver.py` source code | full — 52 cities | **ABSENT** | **ABSENT** |
| 16 | `config/reality_contracts/data.yaml` | `SETTLEMENT_SOURCE_{CITY_UPPER}` keys | Config-authored (operator), verified by `scripts/verify_reality_contracts_2026-05-17.py` | partial — 16 cities (NYC/Chicago/Atlanta/Miami/Dallas/Austin/Houston/Seattle/LA/SF/Denver + London/Paris/Seoul/Shanghai/Tokyo). 36/52 absent — these are WU-native non-contracted cities | **ABSENT** | **ABSENT** |
| 17 | `src/strategy/kelly.py :: DEFAULT_CITY_KELLY_MULTIPLIERS` | dict key `city_name` | Config-authored in kelly.py source. Fail-OPEN to 1.0× for unknown cities | partial — only Denver (0.7) and Paris (0.7) have explicit entries; all others default 1.0 | **1.0× (default)** | **1.0× (default)** |
| 18 | `config/settings.json :: sizing.city_kelly_multipliers` | dict key `city_name` | Operator override layer; absent for all cities currently | **empty** | **N/A** | **N/A** |
| 19 | `state/zeus-world.db :: diurnal_curves` | `city` column | `scripts/onboard_cities.py` step `diurnal_curves` | full — all 52 cities (96 rows/city) | **ABSENT** | **ABSENT** |
| 20 | `state/zeus-world.db :: forecast_skill` | `city` column | `scripts/onboard_cities.py` step `forecast_skill` | partial — all 52 cities; row counts vary (Shenzhen 350, Lagos 35) | **ABSENT** | **ABSENT** |
| 21 | `state/zeus-world.db :: temp_persistence` | `city` column | `scripts/onboard_cities.py` step `temp_persistence` | full — all 52 cities | **ABSENT** | **ABSENT** |
| 22 | `state/zeus-forecasts.db :: market_events_v2` | `city` column | PM market event capture (live markets); `scripts/onboard_cities.py` step `market_events` | full — all 52 cities | **ABSENT** | **ABSENT** |
| 23 | `state/zeus-world.db :: solar_daily` | `city` column | `scripts/onboard_cities.py` step `solar_daily` | full — all 52 cities | **ABSENT** | **ABSENT** |

---

## 2. Bootstrap Classification: Obtainable-Now vs Requires-Accumulated-History

### OBTAINABLE-NOW via archive backfill (no live PM market history required)

| Artifact | Why obtainable | Key source |
|---|---|---|
| `observation_instants_v2` | WU-daily + OpenMeteo archive covers years of historical ASOS/METAR data | archive ingest scripts |
| `ensemble_snapshots_v2` | ECMWF TIGGE archive available for past dates | TIGGE backfill daemon |
| `calibration_pairs_v2` | Derived from ensemble_snapshots_v2 + settlements_v2 — once settlements exist, pairs build from archive | `rebuild_calibration_pairs_canonical.py` |
| `model_bias_ens_v2` | Derived from calibration_pairs_v2 + ensemble_snapshots_v2 — computable after calibration_pairs built | `ens_bias_repo.write_bias_model()` |
| `platt_models_v2` | Derived from calibration_pairs_v2 — computable once pairs exist | `refit_platt.py --no-dry-run --force` |
| `diurnal_curves` / `temp_persistence` / `solar_daily` / `forecast_skill` | Derived from observation backfill | `onboard_cities.py` steps |
| `config/city_monthly_bounds.json` | Reads `ensemble_snapshots` — regenerable once backfill complete | `generate_monthly_bounds.py` |
| `config/city_correlation_matrix.json` | Reads `ensemble_snapshots` — regenerable once backfill complete | `build_correlation_matrix.py` |
| `config/cities.json` entry | Operator-authored | manual |
| `src/data/tier_resolver.py :: TIER_SCHEDULE` | Operator-authored code | manual |
| `v2_city_floors.json :: per_city` | Derived from `observation_instants_v2` directional-coverage distribution (p05). Requires ~6+ months archive observation data to estimate the tail | `offline floor estimation script` over archive |
| `v2_nstar.json :: per_city_metric` | Derived from `calibration_pairs_v2` ECE analysis. Requires ≥110 settled target-date training samples (minimum N_star seen). **~110 calibration_pairs rows requires ~110 settled PM markets** | offline ECE sweep |
| `data/oracle_error_rates.json` | Requires `raw/oracle_shadow_snapshots/` captured at market-resolution time. Each entry needs snapshot comparisons to settled PM bins — NOT derivable from archive alone. | `bridge_oracle_to_calibration.py` |

### REQUIRES ACCUMULATED LIVE/SETTLED HISTORY (cannot be bootstrapped from archive)

| Artifact | Minimum threshold | Why not archivable |
|---|---|---|
| `data/oracle_error_rates.json` | ≥1 snapshot/settlement pair; meaningful n ≥35 (see: Guangzhou, Manila, Lagos) | Requires `oracle_shadow_snapshot` files captured at real PM oracle resolution time. WU-ICAO archive data differs from what PM oracle reads at settlement moment — the bridge is explicitly comparing "what oracle saw" vs "what we recorded." Archive ASOS/METAR has different time-of-fetch semantics. |
| `settlements_v2` rows | ≥1 settled PM market for the city | PM must have listed and resolved a market for the city |
| `v2_nstar.json` (indirect) | ≥110 settled target-dates | N_star calibration requires `calibration_pairs_v2` which requires `settlements_v2` rows |
| `config/reality_contracts/data.yaml` entry | N/A | Operator contract decision — which settlement source to trust. Not algorithmic. |

---

## 3. Detailed Bootstrap Question: How Did the 52 Get Their First Entries?

**v2_city_floors**: The floor = `max(p05_directional_coverage, 0.35)` over 2025 H2 `observation_instants_v2`. For new cities with archive, you can derive the floor from the existing hourly observation backfill — no settled PM market needed. Minimum useful archive: ~180 days to compute a stable p05.

**v2_nstar**: Derived from `calibration_pairs_v2` ECE analysis (RERUN_PLAN_v2 §C4). Minimum N_star seen is 110 training target_dates. This means ~110 settled PM markets with valid ensemble snapshots are needed before N_star can be derived. DDD reference doc §2.2 explicitly notes "Shenzhen-class cities can be onboarded with shorter warmup than previously assumed" (citing Platt L2 borrowing strength), but 110 settled markets is still required. No archive-only path.

**oracle_error_rates**: The oracle rate requires `oracle_shadow_snapshots/` captured in real-time at PM resolution. These are WU API responses captured at 10:00 UTC daily by `oracle_snapshot_listener.py` — the exact window PM oracle fetches. Archive METAR data was not captured at that window. Therefore oracle_error_rates CANNOT be bootstrapped from archive; it accumulates one comparison per settled market day starting from the day the city goes live with oracle_snapshot_listener running. For Jinan/Zhengzhou, n=0 → MISSING → mult=0.5 penalty indefinitely until live comparisons accumulate.

---

## 4. Summary of Jinan/Zhengzhou Status

Every row in the matrix above is **ABSENT** for both cities. The most critical gaps:

| Gap | Severity | Blocks trading? |
|---|---|---|
| Not in `cities.json` | P0 | Yes — city unknown to entire system |
| Not in `TIER_SCHEDULE` | P0 | Yes — `UnsupportedTierError` on any tier lookup |
| No `observation_instants_v2` rows | P0 | Yes — DDD has no coverage data; fails closed |
| No `ensemble_snapshots_v2` rows | P0 | Yes — no forecast signal |
| No `calibration_pairs_v2` rows | P0 | Yes — platt/ens_bias/nstar cannot be computed |
| No `settlements_v2` rows | P0 | Yes — no settled PM market history |
| No `v2_city_floors` entry | P0 | Yes — DDDFailClosed raised (city missing from floors config) |
| No `v2_nstar` entry | P0 | Yes — DDD uses None fallback → likely DDDFailClosed |
| `oracle_error_rates` MISSING | warning | No — MISSING = mult 0.5, not hard block |
| No `reality_contracts` entry | warning | No (only 16/52 have contracts; not a hard gate for all cities) |

---

## 5. Verdict (≤1 paragraph)

Jinan and Zhengzhou genuinely cannot trade yet, and several of their missing artifacts cannot be obtained by running archive-backfill scripts alone. The archive-obtainable artifacts — observation_instants_v2, ensemble_snapshots_v2, calibration_pairs_v2, model_bias_ens_v2, platt_models_v2, diurnal_curves, v2_city_floors — can be backfilled once the cities are registered in cities.json and tier_resolver, since ECMWF TIGGE and WU archive data exists for both. However, **v2_nstar requires ≥110 settled PM target-dates** (calibration_pairs rows), and **oracle_error_rates requires real-time oracle shadow snapshots** captured at PM resolution time — both accumulate only after live markets are listed and begin settling. Until both cities have ~110 settled markets (roughly 4+ months of active PM trading), v2_nstar will be absent (DDD may fail-closed or use a conservative default) and oracle_error_rates will sit at MISSING (0.5× Kelly penalty). The 14-day BLACKLIST shadow period is the minimum gate; the true parity horizon for full DDD confidence is ~110+ settled markets.

---

## 6. Known Gaps in the 52 (Discovered During Audit)

The following artifacts are NOT full-coverage even for the existing 52 cities:

| Artifact | Missing cities |
|---|---|
| `city_monthly_bounds.json` | Amsterdam, Guangzhou, Helsinki, Karachi, Manila, Qingdao |
| `city_correlation_matrix.json` | Amsterdam, Guangzhou, Helsinki, Karachi, Manila, Qingdao |
| `v2_city_floors` / `v2_nstar` | Qingdao, plus 4 with `NO_TRAIN_DATA` (Hong Kong, Istanbul, Moscow, Tel Aviv) |
| `platt_models_v2` | **ALL 52** — table exists but 0 rows (seeding not yet run) |
| `model_bias_ens_v2` | **ALL 52** — table does not exist in live zeus-forecasts.db |
| `oracle_error_rates` | Qingdao is very thin (n=23, p95=0.117 — at CAUTION boundary) |
| `reality_contracts` | Only 16/52 have explicit contracts; 36 are WU-native without contracts |
