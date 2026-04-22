# Gate F Data Backfill — Step 2: Phase 0 Pilot Plan (5 cities)

Created: 2026-04-21
Last reused/audited: 2026-04-21
Authority basis: `.omc/plans/observation-instants-migration-iter3.md` (ralplan v3, APPROVED with patches C1/C2/C3 inline); ground truth `.omc/plans/city_truth_sweep.md` (51-city literal-citation sweep). Phase -1 DRIFT cleanup landed as commit `d9c998f` (2026-04-21).

## Scope

Phase 0 of the `observation_instants_v2` tiered-migration plan. Implement the minimal authorship surface (WU hourly client, Ogimet hourly client, typed v2 writer, deterministic tier resolver, backfill driver, audit script) and pilot-backfill **5 cities** (Chicago, London, Tokyo, Sao Paulo, Moscow) from 2024-01-01 → 2026-04-21 under `data_version='v1.wu-native.pilot'`. No cutover; legacy `observation_instants` remains the source of truth. Gate 0→1 = row counts ±5%, zero Tier-4 rows, zero `authority=UNVERIFIED`.

## Upstream state (Step 1 / Step 1b)

- `state/zeus-world.db` is authoritative; `observation_instants_v2` exists (commit `be05fe7`, authority/data_version/provenance_json columns present) with 0 rows.
- `observation_instants` (legacy, 859k rows, 46 cities, 2024-01-01 → 2026-04-12, `openmeteo_archive_hourly` single-source) stays untouched.
- `zeus_meta` table and `observation_instants_current` VIEW do NOT exist yet — Phase 0 creates them.
- DRIFT cleanup (commit `d9c998f`) removed 4 stale backfill entries; post-state: `CITY_STATIONS` = 47 wu_icao cities, `OGIMET_TARGETS` = {Istanbul, Moscow, Tel Aviv} matches noaa-sstype cities.

## Pilot city selection

| City | Tier | Settlement source | ICAO/station | Region |
|---|---|---|---|---|
| Chicago | 1 (WU) | wu_icao_history | KORD | US |
| London | 1 (WU) | wu_icao_history | EGLC | EU |
| Tokyo | 1 (WU) | wu_icao_history | RJTT | Asia |
| Sao Paulo | 1 (WU) | wu_icao_history | SBGR | SA |
| Moscow | 2 (Ogimet) | ogimet_metar_uuww | UUWW | EU (tests Tier 2) |

Rationale: 4 WU + 1 Ogimet exercises both live tier code paths; 5 distinct regions catches per-geography latency/rate quirks; no HK (Tier 3 is fleet-only, no pilot).

## File inventory (dependency-ordered)

### Create

| # | Path | LOC | Purpose | Depends on |
|---|---|---:|---|---|
| 1 | `src/data/tier_resolver.py` | ~60 | Deterministic `tier_schedule: dict[city_name, Tier]` from `config/cities.json`; also `(city, target_date) → Tier` API for future date-range schedules. HK→Tier 3, noaa→Tier 2, rest→Tier 1. Tier 4 rejected. | cities.json only |
| 2 | `src/state/schema/v2_schema.py` *(modify)* | +20 | Add `CREATE TABLE IF NOT EXISTS zeus_meta` + `INSERT ... ('observation_data_version', 'v0')` + `CREATE VIEW IF NOT EXISTS observation_instants_current` | existing v2 schema |
| 3 | `src/data/observation_instants_v2_writer.py` | ~100 | Typed writer `insert_rows(conn, rows: list[ObsV2Row])` with pre-INSERT CHECK (A1, A2, A6); `ObsV2Row` dataclass enforces required fields at construction; raises `IntegrityError` on missing provenance/source/authority/data_version | schema (v2_schema.py), tier_resolver |
| 4 | `src/data/wu_hourly_client.py` | ~200 | `fetch_wu_hourly(icao, date_utc, country_code, unit) → list[HourlyObs]` via `api.weather.com/v1/location/{ICAO}:9:{CC}/observations/historical.json`; reuses `_WU_PUBLIC_WEB_KEY` pattern from `observation_client.py`; returns top-of-hour snapshots (WU cadence is ~30min; plan TBD re cadence normalization) | `proxy_health` + WU key module |
| 5 | `src/data/ogimet_hourly_client.py` | ~150 | `fetch_ogimet_metar_hourly(station, date_utc) → list[HourlyObs]`; reuses HTTP + SYNOP-avoidance bug-fix logic already codified in `scripts/backfill_ogimet_metar.py:~200-220` (extracted, not duplicated) | shared HTTP + backoff helpers |
| 6 | `scripts/backfill_obs_v2.py` | ~300 | Multi-tier driver: for each city, resolve tier via `tier_resolver`, call matching client, write via `observation_instants_v2_writer` with `data_version='v1.wu-native.pilot'`. Per-window HTTP status log → `state/obs_v2_backfill_log.jsonl`. Exponential backoff on 429/403 (2s→4s→8s). | 1,3,4,5 |
| 7 | `scripts/audit_observation_instants_v2.py` | ~150 | Row-count audit per (city, tier, authority); emits JSON for CI/nightly. `--json` flag returns `{tier_violations, source_tier_mismatches, per_city_rowcounts, orphan_rows}` | 1, schema |
| 8 | `tests/test_tier_resolver.py` | ~50 | A3 antibody: resolver output matches cities.json ssytpe for every city; rejects unknown city | 1 |
| 9 | `tests/test_obs_v2_writer.py` | ~80 | A1 + A2 antibodies: reject rows missing provenance/authority/data_version; reject source not in tier's allowed set | 3 |
| 10 | `tests/test_hk_rejects_vhhh_source.py` | ~40 | A6 antibody: writer rejects any HK row with `source != 'hko_hourly_accumulator'` (NO VHHH path) | 3 |
| 11 | `tests/test_backfill_scripts_match_live_config.py` | ~60 | A7 antibody: `set(backfill_wu_daily_all.CITY_STATIONS.keys()) == {c.name for c in cities if sstype=="wu_icao"}` AND `set(backfill_ogimet_metar.OGIMET_TARGETS.keys()) == {c.name for c in cities if sstype=="noaa"}` | none (runtime import only) |

### Modify (minimal)

- `architecture/source_rationale.yaml` — register new `src/data/*.py` files per AGENTS.md mesh-maintenance rule.
- `architecture/script_manifest.yaml` — register new `scripts/*.py`.
- `architecture/test_topology.yaml` — register 4 new test files.

### Do NOT touch in Phase 0

- Legacy `observation_instants` table (read-only compat during migration).
- `src/signal/diurnal.py`, `src/engine/monitor_refresh.py`, `scripts/etl_diurnal_curves.py`, etc. — those consume `observation_instants_current`, but the view returns 0 rows while `zeus_meta.observation_data_version='v0'`, so no downstream behavior changes in Phase 0. Reader modifications land in Phase 1.
- Running daemon processes / launchd / crons — pilot backfill is a one-shot, not a daemon job.

## Ordering (implementation sequence)

1. **Schema extension** (file #2) — single commit adding `zeus_meta` + VIEW; idempotent `IF NOT EXISTS`.
2. **Tier resolver + A3 test** (files #1, #8) — one commit; foundation for writer.
3. **V2 writer + A1/A2/A6 tests** (files #3, #9, #10) — one commit; ship before any writes.
4. **A7 DRIFT antibody test** (file #11) — separate commit; runtime-import-only, doesn't depend on 2/3.
5. **WU hourly client** (file #4) — one commit with light pilot test (live HTTP; network-gated).
6. **Ogimet hourly client** (file #5) — one commit, same pattern.
7. **Backfill driver** (file #6) — one commit; pilot execution lives in its log, not in git.
8. **Audit script** (file #7) — one commit; pre-gate-0 check.
9. **Pilot execution**: `python scripts/backfill_obs_v2.py --cities Chicago London Tokyo Sao_Paulo Moscow --start 2024-01-01 --end 2026-04-21 --data-version v1.wu-native.pilot` — logs to `state/obs_v2_backfill_log.jsonl`; NOT a commit, just a run.

Steps 1-4 can all commit before any live HTTP is made, preserving roll-forward ability.

## Gate 0→1 (from plan v3)

All must be true before Phase 1 starts:

- Pilot row count per city within ±5% of expected (**20,208** hourly observations per city at 24h × **842 inclusive days**; prior plan versions incorrectly stated 19,968 / 832 days — corrected 2026-04-22 per critic M6)
- Per-day `COUNT(DISTINCT utc_timestamp)` must be in `[22, 25]` for every non-HK (city, target_date) — 22=DST spring-forward, 25=DST fall-back. Anything outside signals a WU upstream hole or chunk-boundary clip. (Added 2026-04-22 per critic C3 — original per-city ±5% tolerance hid 22-hour daily holes that passed as 0.1% of the city total.)
- Zero rows where `source LIKE '%openmeteo%'` (no Tier 4)
- Zero rows where `authority = 'UNVERIFIED'` or `'QUARANTINED'`
- Moscow pilot writes `source='ogimet_metar_uuww'`, Chicago/London/Tokyo/Sao Paulo write `source='wu_icao_history'`
- `python scripts/audit_observation_instants_v2.py --json | jq .tier_violations` == 0
- All 4 antibody tests green (`pytest -q tests/test_tier_resolver.py tests/test_obs_v2_writer.py tests/test_hk_rejects_vhhh_source.py tests/test_backfill_scripts_match_live_config.py`)

## Rollback (if Phase 0 fails)

Single SQL: `DELETE FROM observation_instants_v2 WHERE data_version='v1.wu-native.pilot';` — pilot data is isolated by data_version, legacy table untouched, no daemon changes to revert.

## Pre-mortem (condensed, full version in plan v3 L134-152)

- **S1** WU rate-limit during pilot: 5 cities ≤ 4200 requests is well below prior live-daemon daily volume; low likelihood. Mitigation: exponential backoff 2→4→8s in driver.
- **S2** Post-cutover Brier regression: not applicable in Phase 0 (no cutover). Measured in Phase 3.
- **S3** HK empty diurnal: not applicable in Phase 0 (HK not piloted).

## Out of scope (deferred)

- Phase 1 fleet backfill (45 remaining cities), Phase 2 atomic cutover, Phase 3 downstream rebuild, Phase 4 legacy deprecation — see plan v3.
- `scripts/backfill_ogimet_metar.py` module docstring (L5-41) stale Taipei/SYNOP references — flagged as a docs-only follow-up, no functional risk.
- Monthly PM market-text audit (14 HK-pattern-risk cities) — separate packet.
- HK signal-layer fleet-average fallback (AC11) — lands in Phase 3, before Phase 2 cutover.

## Open questions

None blocking Phase 0. All v3 open-questions resolved in plan v3 L207-222.
