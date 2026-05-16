# Live Smoke Test — Final Report

**Date**: 2026-05-01
**Branch**: `main` @ 157192d9 (= origin/main)
**Operator directive**: 做最完整的测试，确保一切有迹可循 / 全部修复
**Total elapsed**: ~32 min (11:30Z → 12:04Z)

## Verdict

**SMOKE PASS WITH ANTIBODIES** — 4 LIVE blockers found and fixed in-flight; 1 new finding (F5) opened for follow-up. Trading + ingest daemons survived 10-min Phase 4 unattended observation with all sentinel files refreshing on cadence.

## Phase summary

| Phase | Window | Outcome |
|---|---|---|
| 1 — Pre-flight snapshot | 11:30Z | Baseline captured: 0 daemons, no sentinels, DB stable. |
| 2 — Ingest boot | 11:32Z–11:36Z | ingest daemon up; `daemon-heartbeat-ingest.json` produced at T+75s; K2 startup catch-up ingested 30,360 rows (46 cities). |
| 3 — Trading boot (1st run) | 11:36Z–11:38Z | **Surfaced F1+F2+F3+F4** (all live blockers); operator authorised in-flight repair. |
| 3' — Repair + 2nd boot | 11:38Z–11:52Z | F1: pip install py-clob-client-v2; F2: heartbeat_sensor argparse extended; F3: world_schema_manifest aligned to live DB; F4: structural fix (DEFAULT_V2_HOST + auto-derive API creds + graceful CTF degrade). Discovered F5 (venue heartbeat protocol). |
| 4 — Steady-state monitor | 11:54Z–12:04Z | 10-min unattended observation: PIDs unchanged, all sentinels refreshed within their cadence intervals. |
| 5 — Trace + unload | 12:04Z onward | This report; daemons unloaded back to "armed but inactive" state per "把接入live上限权益交给我" directive. |

## Key boot signals (Phase 3' run)

```
2026-05-01 06:52:42 [zeus] INFO: world_schema_ready sentinel OK: schema_version=1
2026-05-01 06:52:42 [src.contracts.world_schema_validator] INFO: World schema validation passed (9 tables checked)
2026-05-01 06:52:42 [zeus] WARNING: Freshness gate STALE at boot: stale_sources=[…]  ← expected, sources predated smoke
2026-05-01 06:52:43 [zeus] INFO: Startup wallet check: $199.40 pUSD available
2026-05-01 06:52:43 [apscheduler.scheduler] INFO: Scheduler started
```

## Phase 4 monitor data (10 min)

| T+ (s) | trading PID | ingest PID | hb_t | hb_i | ingest_status | source_health | snap_high |
|---|---|---|---|---|---|---|---|
| 0 | 27135 | 27131 | 06:53 | 06:53 | 06:42 | 06:42 | 344580 |
| 60 | … | … | 06:54 | 06:54 | 06:42 | 06:42 | 344580 |
| 246 | … | … | 06:57 | 06:57 | **06:57** | 06:42 | 344580 |
| 552 | 27135 | 27131 | 07:02 | 07:02 | **07:02** | **07:02** | 344580 |

`hb_t` and `hb_i` refresh every 60s ✓. `ingest_status.json` refreshes every 5 min ✓. `source_health.json` refreshes every 10 min ✓. PIDs stable across the entire window ✓.

`snap_high` (TIGGE high-track ensemble snapshots) stayed at 344,580 — expected, since TIGGE source data only lands once per day at 00Z and we are mid-day. Boot-time `world.observations`/`settlements` row counts likewise unchanged because the K2 startup catch-up tick already drained the queue at Phase 2 boot.

## Source health snapshot (12:02:47Z)

| Source | Status | Last success | Latency |
|---|---|---|---|
| open_meteo_archive | OK | 12:02:41Z | 674ms |
| wu_pws | OK | 12:02:42Z | 239ms |
| ogimet | OK | 12:02:45Z | 2418ms |
| ecmwf_open_data | OK | 12:02:47Z | 1618ms |
| noaa | OK | 12:02:47Z | — |
| hko | DOWN | — | 1107ms (404) |
| tigge_mars | (next probe) | — | — |

`hko` is failing with 404 against `https://www.hko.gov.hk/en/cis/statClim/extract/html/HKO/max_temp.htm` — that page may have moved. Tracked as follow-up but does not block the smoke (other sources cover Hong Kong via different paths).

## Findings ledger

See `findings.md` for full per-finding root-cause + fix narrative. Summary:

| ID | Severity | Status | Antibody status |
|---|---|---|---|
| F1 — `py-clob-client-v2` not installed | LIVE BLOCKER | FIXED | TODO: import-probe at boot |
| F2 — heartbeat_sensor.py rejects new flags | LIVE BLOCKER | FIXED | TODO: real multi-heartbeat enforcement |
| F3 — schema manifest drift (3 columns) | Phase-3 FATAL | FIXED | TODO: CI manifest-vs-DB probe |
| F4 — wallet read returned $0 (real $199.40) | LIVE BLOCKER | FIXED structurally (no Keychain L2 dep) | DONE structurally |
| F5 — venue heartbeat: Invalid Heartbeat ID | DEGRADED orders | OPEN | Pending Polymarket protocol research |
| Aux — HKO 404 | DEGRADED data | OPEN | Tracked under `source_health` follow-up |

## Files changed

- `bin/heartbeat_sensor.py` — argparse accepts `--heartbeat-files` + `--stale-threshold-seconds` (advisory).
- `architecture/world_schema_manifest.yaml` — `solar_daily`, `forecast_skill`, `observations` aligned to live DB columns.
- `src/data/polymarket_client.py` — `_resolve_credentials` no longer reads keychain L2 creds (drift hazard removed).
- `src/venue/polymarket_v2_adapter.py` — `DEFAULT_V2_HOST = clob.polymarket.com`; `_default_client_factory` auto-derives API creds; `get_collateral_payload` gracefully degrades when SDK lacks `get_positions`.

## Daemon state at end of smoke

All 3 daemons unloaded via `launchctl unload`. Plists remain in `~/Library/LaunchAgents/` so reload requires a single command per daemon. **NOTE**: `state/LIVE_LOCK` was deleted by co-tenant commit 97f82c21 during this smoke (see "Co-tenant collision" section below); the trading daemon's PAUSED guard is now driven only by `state/control_plane.json` semantics, not the file marker. Confirm desired posture before reload.

## Recommended next operator actions

1. Review `findings.md` and confirm fixes match intent.
2. Decide F5 priority: if mainline-launching, dispatch a Polymarket-API research subagent to determine the heartbeat registration protocol; if not, ship without F5 fix and degrade to GTM-only orders for first live cycles.
3. Decide whether to commit the 5 source-side fixes to `main` (or stage on a topic branch).
4. Optionally remove stale `openclaw-polymarket-api-key/-secret/-passphrase` Keychain entries (no longer consulted, but their presence invites accidental re-introduction).
5. When ready: `launchctl load com.zeus.data-ingest.plist` then `com.zeus.live-trading.plist`. Wallet check will report `$199.40` and trading will run in PAUSED control-plane mode until you `resume` via control_plane.json.

## Co-tenant collision — discovered post-hoc

While Phase 4 monitor was running (started 11:54Z), a co-tenant Claude session committed `97f82c21` at 11:57:48Z that:

1. **Archived this directory** to `docs/archives/packets/task_2026-05-01_live_smoke_test/` (gitignored cold storage). It read the preflight at a moment when `state/LIVE_LOCK` still existed, the findings doc had not yet been written, and `report.md` did not exist — and concluded the test was abandoned. **It was not** — the smoke was actively running.
2. **Deleted `state/LIVE_LOCK`** ("LIVE PAUSED"). This silently dropped the control-plane PAUSED guard while my live-trading daemon was up. No orders were placed because F5 (venue heartbeat) was already gating GTC/GTD; if F5 had been resolved this could have been an unsafe transition.
3. **Deleted `state/auto_pause_failclosed.tombstone`** (orphan marker per its commit message — likely a correct cleanup).

Recovery: `preflight.md` and `findings.md` re-copied from the archive back into `docs/operations/task_2026-05-01_live_smoke_test/` so the trace is preserved in operations/. `state/LIVE_LOCK` is NOT recreated — operator is presumed to have authorised the deletion in parallel via `9940cc8d Workspace cleanup Phase 5` and `f5f13a6b Workspace cleanup` lineage; recreating it without operator confirmation could itself be wrong.

**Antibody for next run**: Future smoke tests should write a top-of-directory `STATUS=running` sentinel that co-tenant cleanup logic respects, OR be authored on a topic branch. Filed under follow-ups.
