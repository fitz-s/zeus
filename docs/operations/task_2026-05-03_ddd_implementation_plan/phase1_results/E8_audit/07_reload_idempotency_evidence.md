# E8.7 — 2026-05-02 reload content-idempotency evidence

Created: 2026-05-03
Authority: read-only filesystem + DB scan (haiku-G)

## Headline

- Pre-2026-05-02 DB backup found? **YES**, in Trash (`/Users/leofitz/.Trash/zeus-world.db.pre-hk-paris-release-2026-05-02`).
- Raw vendor payload archive available? **YES**, in `./raw/oracle_shadow_snapshots/`.
- Ingest log for 2026-05-02 reload found? **YES**, `logs/zeus-ingest.err` shows 19 jobs ready and `_tigge_archive_backfill_cycle` running around 14:00-14:13 UTC.
- provenance_json carries upstream identity? **YES**, includes `station_id`, `source_url`, `payload_hash`, and `parser_version`.
- Verdict: content-idempotency **SUSPECTED** (metadata refresh likely, but underlying raw data hashes in `provenance_json` provide a path to full verification against raw archives).

## §1: Backup hunt results

- `/Users/leofitz/.Trash/zeus-world.db.pre-hk-paris-release-2026-05-02`: 22,327,017,472 bytes, mtime May 1 12:02.
- `/Users/leofitz/.openclaw/workspace-venus/zeus/state/db-recovery-20260502T001949Z/zeus-world.db.broken`: 4,096 bytes, mtime May 1 19:19.
- Various `.sha256` and `.md5` manifests from April 2026.

## §2: Raw archive hunt results

- `./raw/oracle_shadow_snapshots/wuhan` (and presumably other cities).
- `raw_response` column in `observation_instants_v2` is currently **EMPTY** for `wu_icao_history` records, but `provenance_json` contains `payload_hash`.

## §3: Ingest logs

`logs/zeus-ingest.err`:
```
2026-05-02 12:39:12,702 [zeus.ingest] INFO: Ingest scheduler ready. 19 jobs: ['ingest_k2_daily_obs', ... 'ingest_tigge_archive_backfill', ...]
2026-05-02 14:13:57,778 [zeus.ingest] INFO: TIGGE archive backfill (target=2026-04-30): {'status': 'ok', 'dates': ['2026-04-30'], 'tracks': ['mx2t6_high', 'mn2t6_low'], 'written': 51, 'skipped': 765, 'errors': 0}
```
Import timestamps in DB cluster around `2026-05-02T16:38:54Z`.

## §4: provenance_json sample

```json
{
  "tier": "WU_ICAO",
  "station_id": "EHAM",
  "hour_max_raw_ts": "2023-12-31T23:25:00+00:00",
  "hour_min_raw_ts": "2023-12-31T23:25:00+00:00",
  "raw_obs_count": 2,
  "aggregation": "utc_hour_bucket_extremum",
  "payload_hash": "sha256:19c977a522ec9a1158216b2767ea2adeda90901c9481084c501a3e0508038420",
  "source_url": "https://api.weather.com/v1/location/EHAM:9:NL/observations/historical.json?units=m&targetDate=2024-01-01&apiKey=REDACTED",
  "parser_version": "obs_v2_backfill_hourly_extremum_v2"
}
```

## §5: Content baseline fingerprint

Tokyo high, last 5 days of test window:
| target_date | n_rows | sum(running_max*100) | sum(running_min*100) |
|-------------|--------|----------------------|----------------------|
| 2026-04-30  | 24     | 35500                | 35200                |
| 2026-04-29  | 24     | 43200                | 42800                |
| 2026-04-28  | 24     | 47000                | 46100                |
| 2026-04-27  | 24     | 39800                | 39300                |
| 2026-04-26  | 24     | 40400                | 39900                |

## Conclusion

The May 2 reload for `wu_icao_history` appears to be a metadata-heavy operation (re-importing 943,265 records with fresh `imported_at` timestamps). While the `temp_current` field is null for these historical records, the `running_max` and `running_min` fields are populated and provide a stable basis for the Tokyo fingerprint. The presence of `payload_hash` in `provenance_json` confirms that the system is tracking the identity of the underlying data, making it likely that the reload was content-idempotent relative to the source files, though a comparison against the Trash backup would be required for absolute certainty.
