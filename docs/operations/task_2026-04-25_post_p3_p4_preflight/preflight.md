# Post-P3 / P4 Preflight

Date: 2026-04-25
Branch: `midstream_remediation`
Authority status: packet evidence, not durable law

## Read-Only Facts Checked

Commands:

```bash
sqlite3 state/zeus-world.db "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('market_events_v2','settlements_v2','ensemble_snapshots_v2','calibration_pairs_v2','observation_instants_v2') ORDER BY name;"
sqlite3 state/zeus-world.db "SELECT 'market_events_v2', COUNT(*) FROM market_events_v2 UNION ALL SELECT 'settlements_v2', COUNT(*) FROM settlements_v2 UNION ALL SELECT 'ensemble_snapshots_v2', COUNT(*) FROM ensemble_snapshots_v2 UNION ALL SELECT 'calibration_pairs_v2', COUNT(*) FROM calibration_pairs_v2 UNION ALL SELECT 'observation_instants_v2', COUNT(*) FROM observation_instants_v2;"
find state raw data -maxdepth 3 \( -iname '*tigge*' -o -iname '*market*rule*' -o -iname '*market_events*' -o -iname '*settlement*rule*' \) 2>/dev/null | sort | head -120
printenv WU_API_KEY >/dev/null && echo WU_API_KEY_present || echo WU_API_KEY_missing
```

Observed:

| Surface | Current result | Interpretation |
|---------|----------------|----------------|
| `market_events_v2` | table exists, 0 rows | 4.6.A remains blocked; no market identity FK surface exists. |
| `settlements_v2` | table exists, 0 rows | Canonical settlement v2 population has not started. |
| `ensemble_snapshots_v2` | table exists, 0 rows | 4.6.B remains blocked; no verified forecast snapshot rows exist. |
| `calibration_pairs_v2` | table exists, 0 rows | 4.6.C remains blocked until settlement and forecast v2 inputs exist. |
| `observation_instants_v2` | 1,813,662 rows | Obs_v2 data exists; P3 B-lite gates are now reader-side. |
| TIGGE local target dirs | not found under `state/` | 4.7 rsync remains operator-owned and incomplete locally. |
| market-rule artifacts | not found by local `state/raw/data` scan | 4.6.A market-rule source question remains unanswered locally. |
| `WU_API_KEY` shell env | missing | Operator 4.8.A remains unresolved in this shell. |
| `state/scheduler_jobs_health.json::k2_daily_obs` | `FAILED`, reason says `WU_API_KEY` missing | Runtime evidence matches 4.8.A. |
| `state/scheduler_jobs_health.json::k2_forecasts_daily` | `OK` at 2026-04-25T13:34:30Z | H5 one-off forecast path appears improved, but acceptance still needs the explicit row-count probe. |
| `state/auto_pause_failclosed.tombstone` | present, content `auto_pause:ValueError` | 4.8.C remains an operator decision; do not clear from this packet. |
| `state/status_summary.json::risk.level` | `GREEN`; infrastructure level `YELLOW` | Runtime is not cleanly green because infrastructure issues remain. |

## Remaining Mainline Decisions

### 4.5.B-full: obs_v2 metric-layer decision

Blocked until an operator/design decision selects one of these branches:

| Branch | Meaning | Files likely affected after approval |
|--------|---------|--------------------------------------|
| instant-level metric identity | Hourly instants carry high/low metric semantics at ingest | `src/data/observation_instants_v2_writer.py`, active obs_v2 producers, `scripts/verify_truth_surfaces.py`, tests, schema policy if required |
| daily-aggregate metric identity | Hourly instants remain physical readings and high/low identity belongs to daily aggregate rows | `daily_observations_v2` design, consumers/readiness checks, possible rollback/deprecation policy for premature instant columns |

Stop if implementation requires assigning high/low identity without that
decision.

### 4.6.A: settlements_v2 population

Blocked until:

- `market_events_v2` is populated with market identity.
- Null-`market_slug` market-rule source files are identified and accepted.
- A market-rule acceptance contract exists with source URL/file, station/source,
  finalization policy, rule version, metric, unit, and bin identity.

Stop if the only join key is city/date or if market rules must be inferred from
legacy settlement labels.

### 4.6.B: forecast/ensemble v2 population

Blocked until:

- Operator rsyncs both TIGGE localday JSON tracks into local paths.
- Integrity manifests or hashes prove cloud/local parity.
- `scripts/ingest_grib_to_snapshots.py` can run with verified `issue_time` and
  `available_at`, not reconstructed hindsight timestamps.

Stop if source-verified timestamps are unavailable.

### 4.6.C: calibration rebuild

Blocked until:

- 4.6.A and 4.6.B are complete.
- P0-P3 gates remain green.
- `calibration_pairs_v2` dry run reaches the minimum decision-group threshold
  per metric.

Stop if either settlement labels or forecast snapshots are evidence-only or
empty.

## Operator-Owned Parallel Lane

These are not safe agent-side code changes without operator action:

- 4.7 TIGGE cloud-to-local rsync plus manifest verification.
- 4.8.A WU_API_KEY launch environment repair and scheduler restart.
- 4.8.B explicit one-off `k2_forecasts_daily` row-count verification.
- 4.8.C auto-pause tombstone clear/accept decision.

## Topology Boot Matrix

| Future packet | Required boot profile | Additional reads |
|---------------|-----------------------|------------------|
| 4.5.B-full | `hourly_observation_ingest` | `docs/operations/current_source_validity.md`, `architecture/fatal_misreads.yaml`, obs_v2 writer/router docs |
| 4.6.A | `settlement_semantics` | `docs/reference/zeus_market_settlement_reference.md`, `docs/reference/zeus_data_and_replay_reference.md`, market-rule evidence packet |
| 4.6.B | `calibration` plus TIGGE asset handoff | `docs/artifacts/tigge_data_training_handoff_2026-04-23.md`, ingest script docs |
| 4.6.C | `calibration` | 4.6.A/B receipts, training readiness output |
| Source routing/Hong Kong | `source_routing` | Fresh `current_source_validity.md` replacement; Hong Kong caution path |

## Next Safe Packet

If operator evidence is still unavailable, the next useful code packet is a
read-only readiness checker that emits these blockers as machine-readable
status. It must not mutate DB rows, clear tombstones, edit launch env, infer
market rules, or decide hourly metric placement.
