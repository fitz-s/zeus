# F44 Investigation — observation_instants_v2 writer dead since 2026-05-10

**Investigator**: executor (sonnet, 2026-05-17)
**Branch**: fix/f44-observation-writer-2026-05-17
**Hypothesis trail**: H1 → H2 → source-tier constraint → fix shape

---

## Probe results

### Probe 1 — Staleness confirmation

```
sqlite3 state/zeus-world.db    "SELECT MAX(target_date), COUNT(*) FROM observation_instants_v2"
→ 2026-05-10 | 1,835,645

sqlite3 state/zeus-forecasts.db "SELECT MAX(target_date), COUNT(*) FROM observation_instants_v2"
→ NULL | 0
```

World.db is the canonical home (confirmed). Forecasts.db has a ghost shell (0 rows), consistent
with K1 split leaving empty CREATE TABLE stubs in forecasts.db. Table is world-class per
`architecture/db_table_ownership.yaml`.

### Probe 2 — H1 (K1 repointed connection): ELIMINATED

- K1 PR `eba80d2b9d` did NOT modify `src/data/hourly_instants_append.py` at all.
- K1 PR did NOT add/remove any `observation_instants_v2_writer` calls anywhere in `src/`.
- `ingest_main.py` (the new daemon introduced by K1) has zero references to
  `wu_hourly_client`, `ogimet_hourly_client`, `hko_ingest_tick`, or
  `observation_instants_v2_writer`.

**H1 verdict: ELIMINATED.** No connection was ever repointed because no runtime connection
to `observation_instants_v2` existed before or after K1.

### Probe 3 — H2 (invocation removed from cycle): CONFIRMED (with nuance)

Full `src/` callsite scan for `insert_rows` / `observation_instants_v2_writer`:

```
src/contracts/ensemble_snapshot_provenance.py   — comment reference only
src/state/schema/v2_schema.py                   — comment reference only
src/data/observation_instants_v2_writer.py       — definition only
src/engine/ddd_wiring.py                         — READS from v2 (SELECT), never writes
```

`scripts/` callsites that actually invoke `insert_rows`:
- `scripts/backfill_obs_v2.py`      — one-time historical backfill, CLI only
- `scripts/fill_obs_v2_dst_gaps.py` — DST gap filler, CLI only
- `scripts/fill_obs_v2_meteostat.py`— meteostat bulk fill, CLI only
- `scripts/hko_ingest_tick.py`      — HKO live tick, but no cron/plist scheduling

**The v2 writer was never wired into any live daemon or cron job.** The table was populated
solely by one-time backfill scripts. The last backfill run completed ~2026-05-10, explaining
the exact cutoff. This is not a regression from K1 — it is a **design omission**: the
migration plan (obs-migration-iter3.md) created the writer contract and backfill tooling but
never wired a live-refresh path.

**H2 verdict: CONFIRMED.** Root cause is missing live-tick invocation. There is no K1 PR
commit to cite as "the commit that removed it" because it was never present.

### Probe 4 — H3 (uncaught exception): ELIMINATED

Zero log entries in `logs/zeus-ingest.log` and `logs/zeus-ingest.err` for any v2 writer
activity. No exception in any log because the writer is never called.

### Probe 5 — H4 (upstream source stopped): NOT APPLICABLE

Not applicable — upstream clients are never invoked. WU/Ogimet are healthy (oracle uses
them via `daily_obs_append`).

### Probe 6 — Source-tier constraint (critical fix-shape blocker)

```python
from src.data.tier_resolver import allowed_sources_for_city, tier_for_city
# Karachi: WU_ICAO, allowed=['meteostat_bulk_opkc', 'ogimet_metar_opkc', 'wu_icao_history']
# Hong Kong: HKO_NATIVE, allowed=['hko_hourly_accumulator']
# London: WU_ICAO, allowed=['meteostat_bulk_eglc', 'ogimet_metar_eglc', 'wu_icao_history']
```

`hourly_instants_append.py::SOURCE = "openmeteo_archive_hourly"` is NOT in the allowed set
for any city. A naive dual-write from `hourly_instants_append._write_row` would be
**rejected by A2** for every city before any row reaches the DB.

**Consequence**: the fix is NOT a one-line `insert_rows()` call from `hourly_instants_append`.
It requires wiring the WU/Ogimet/HKO clients — the same sources the v2 table was populated
with during backfill — into a live-tick path.

### Probe 7 — Actual v2 sources

```
wu_icao_history        | 951,973 rows | MAX 2026-05-10
ogimet_metar_*         |  20,000+     | MAX 2026-05-10
meteostat_bulk_*       |  19,000+     | MAX 2026-03-15
```

The dominant source is `wu_icao_history` from `src/data/wu_hourly_client.py`. That client
is backfill-only (no `tick` / `catch_up_missing` API). `scripts/hko_ingest_tick.py` handles
HK via accumulator projection and was designed for hourly cron but has no scheduling entry
in any plist or `cron/jobs.json`.

---

## Root cause verdict

**H2 confirmed — structural design omission, not a K1 regression.**

`observation_instants_v2` was designed with a complete backfill path but an incomplete
live-refresh path. The migration plan (obs-migration-iter3.md) specified the writer contract
and source-tier rules but never produced a live-tick daemon or cron job for WU/Ogimet cities.
The 2026-05-10 cutoff coincides with what we believe was the last manual backfill run
(no backfill log was probed to confirm this exact date; it is inferred from the MAX(target_date)
gap and is marked unverified per Fitz Constraint #4).

The F44 discovery doc's hypothesis that "K1 broke a prior invocation" is incorrect: the
K1 PR never touched any v2 writer invocation path. The omission predates K1.

---

## Fix shape

**What is required** (NOT in this commit — operator approval needed):

1. **`scripts/obs_v2_live_tick.py`** (new script, ~150 LOC)
   - Takes `--days-back N` (default 7) for rolling catch-up window.
   - Per-city: resolves tier → dispatches `wu_hourly_client.fetch_wu_hourly()` (WU_ICAO tier)
     or `ogimet_hourly_client.fetch_ogimet_hourly()` (OGIMET tier).
   - Builds `ObsV2Row` with `data_version='v1.wu-native'`, `authority='VERIFIED'`.
   - Calls `insert_rows(conn, rows)` via `get_world_connection()`.
   - HK cities: delegate to existing `hko_ingest_tick` projection logic (or import from it).
   - Safe for hourly cron: idempotent via `UNIQUE(city, source, utc_timestamp)`.

2. **Wire into `ingest_main.py`** as `ingest_k2_obs_v2_tick`:
   - `@_scheduler_job("ingest_k2_obs_v2_tick")`
   - `scheduler.add_job(..., "cron", minute=15, ...)` (offset from existing ticks at :00, :07)
   - Uses `acquire_lock("obs_v2")` advisory lock pattern (same as `_k2_hourly_instants_tick`).
   - `get_world_connection(write_class="bulk")`.

3. **OR** wire as standalone cron via `com.zeus.data-ingest.plist` override (lower risk for
   initial deploy; avoids `ingest_main.py` restart requirement).

**What is BLOCKED on this fix:**
- F35 (oracle penalty calibration) — bridge queries v2 for `target_date >= 2026-05-11`, gets 0 rows
- F33 (persistent oracle MISSING) — downstream of F35
- All cities fall back to 0.5× Kelly conservative until v2 freshness is restored

**Conservative path** (operator approval required):
- Deploy `obs_v2_live_tick.py` as standalone script.
- Wire into existing `com.zeus.data-ingest.plist` via `com.zeus.obs-v2-ingest.plist` (new).
- After 24h of successful writes: integrate into `ingest_main.py` scheduler for unified governance.

---

## F21 sequencing

Per WAVE_2_PLAN §F21: legacy `INSERT INTO observation_instants` at `hourly_instants_append.py:229`
**must NOT be removed** until v2 has a live writer. `observation_instants` is still the
freshness source for several downstream readers. Removing it before v2 is live would
create a second dead-writer category. F21 cleanup is blocked on F44 resolution.

---

## Backfill gap (7-day hole)

`observation_instants_v2` is missing 2026-05-11 through 2026-05-17 (~7 days × 46 cities ×
~20 hours/day ≈ 6,440 rows of WU history). Backfill plan in `F44_BACKFILL_PLAN.md`.
