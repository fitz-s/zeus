# Phase 3 → Phase 4 Learnings — exec-carol

Author: exec-carol | Date: 2026-04-16 | Source: Phase 3 R-G implementation

---

## 1. Station / source / authority plumbing

Phase 3 showed the clean path: `cities_by_name[city].wu_station` + `.country_code` + `.settlement_unit` feed into `_build_atom_pair`, which stamps `authority="VERIFIED"`, `station_id=f"{icao}:{cc}"`, and `provenance_metadata={icao, cc, fetched_range}` onto every `ObservationAtom` (`src/data/daily_obs_append.py:799–831`).

Phase 4 GRIB ingest needs the same discipline for `ensemble_snapshots_v2`. Concrete sketch:

```python
city_cfg = cities_by_name[city_name]          # single source of truth
provenance_json = json.dumps({
    "grib_file": grib_path,
    "issue_time": issue_time.isoformat(),
    "physical_quantity": "mx2t6_local_calendar_day_max",  # or mn2t6_...
    "grid_point": {"lat": city_cfg.lat, "lon": city_cfg.lon},
    "member_count": len(members),
    "source": "ecmwf_tigge",
})
manifest_hash = sha256(grib_path_bytes).hexdigest()
training_allowed = (causality_status == "OK") and (issue_time is not None)
```

The key lesson: `authority` and `training_allowed` must be set at ingest time by inspecting actual data, not defaulted. Phase 3's `authority="VERIFIED"` was earned by passing `IngestionGuard` layers 1/4/5. Phase 4 must earn `training_allowed=True` by confirming `causality_status` before writing — never default to True.

---

## 2. Remaining CITY_STATIONS sites

Two scripts still carry parallel maps:

- `scripts/backfill_wu_daily_all.py:141` — `CITY_STATIONS` dict, 45 cities, drives the backfill's city iteration at lines 615/621/630. Also imports `cities_by_name` (line 31) for `city_cfg` lookups — so it has a dual-lookup pattern identical to the pre-Phase-3 `daily_obs_append`. **Load-bearing for Phase 4?** Phase 4 targets `ensemble_snapshots_v2` ingest, not WU observation backfill. This script is NOT on the Phase 4 critical path. Safe to leave for Phase C.

- `scripts/oracle_snapshot_listener.py:59` — comment at line 58 explicitly says "mirrored from daily_obs_append.py CITY_STATIONS". That mirror is now stale — `daily_obs_append` no longer has CITY_STATIONS. The listener uses it to drive per-city WU snapshot fetches (line 164). **Load-bearing for Phase 4?** No — oracle snapshots are an independent cron, not part of GRIB ingest. Safe to leave for Phase C, but the stale comment is a maintenance hazard.

**Verdict:** Neither blocks Phase 4. Both are Phase C chores. Flag the oracle_snapshot_listener stale comment to avoid future agent confusion.

---

## 3. `scripts/backfill_tigge_snapshot_p_raw.py` — Phase 4 second target

Current shape (`scripts/backfill_tigge_snapshot_p_raw.py:1–158`):

- Already imports `cities_by_name` (line 21) — **no parallel CITY_STATIONS**. Clean.
- `materialize_snapshot_row` (line 82) reads from legacy `ensemble_snapshots` table, not `ensemble_snapshots_v2`. It selects `snapshot_id, city, target_date, members_json, p_raw_json` — no `temperature_metric`, no `training_allowed`, no `causality_status`, no `manifest_hash`, no `provenance_json`.
- The UPDATE at line 99 only writes `p_raw_json` back to the legacy table.

**What Phase 4 needs from this script:** It must be retargeted to `ensemble_snapshots_v2`, with the query and UPDATE extended to handle `temperature_metric` as a dimension and to set `training_allowed`/`causality_status` at write time. The `cities_by_name` consolidation is already done — that's not the work. The work is adding the v2 identity fields.

---

## 4. Phase 5 forward risks — low_temp observation completeness

`daily_obs_append.py` writes both `high_temp` and `low_temp` into the legacy `observations` table in the same atom pair, with `high_provenance_metadata` and `low_provenance_metadata` as separate JSON blobs (lines 568–640). The provenance is structurally identical for high and low — same station, same fetch window, same authority stamp.

**Risk:** The low provenance is identical to high provenance in structure, but `low_temp` is derived from `min(temps)` over the same WU ICAO sample set as `high_temp`. For HKO, it comes from `CLMMINT` (a separate API call from `CLMMAXT`). If HKO's `CLMMINT` lags or is incomplete independently of `CLMMAXT`, low provenance can be `VERIFIED` while the underlying data is actually a different-quality source. Phase 5 should add a provenance field distinguishing the low fetch result quality from the high fetch result quality — currently they share one `authority` stamp per row.

---

## 5. Duplication pattern — which Phase 4 will touch

| Script | Duplicates | Phase 4 touch? |
|--------|-----------|----------------|
| `scripts/backfill_tigge_snapshot_p_raw.py` | Targets legacy `ensemble_snapshots`; Phase 4 needs `ensemble_snapshots_v2` | **YES** — must be extended/forked for v2 |
| `scripts/backfill_wu_daily_all.py` | Duplicates WU fetch logic from `daily_obs_append.py` | No — not on Phase 4 path |
| `scripts/backfill_hko_daily.py` | Duplicates HKO fetch logic from `daily_obs_append.py` | No — not on Phase 4 path |

The safe-to-diverge verdict: `backfill_wu_daily_all.py` and `backfill_hko_daily.py` can stay diverged until Phase C. `backfill_tigge_snapshot_p_raw.py` **cannot** — Phase 4 must touch it, and the v2 column additions need to be done cleanly rather than as a patch on the legacy UPDATE.
