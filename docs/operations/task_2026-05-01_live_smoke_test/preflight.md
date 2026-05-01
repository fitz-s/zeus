# Live Smoke Test — Preflight Snapshot (T0)

**Time (UTC)**: 2026-05-01T11:30:56Z
**Branch**: main @ 157192d9 (= origin/main)
**Operator directive**: 做最完整的测试，确保一切有迹可循
**Trace dir**: `docs/operations/task_2026-05-01_live_smoke_test/`

## Process state (T0)

No zeus daemons running.

```
launchctl list | grep zeus →
  -  0  com.zeus.heartbeat-sensor   (loaded but not running, last status = 0)
```

`com.zeus.data-ingest`, `com.zeus.live-trading`, `com.zeus.riskguard-live` plists exist but **not loaded**.

## DB row counts (T0)

| Table | Rows | Max issue / target |
|---|---|---|
| ensemble_snapshots_v2 (high) | 344,580 | 2026-04-28 |
| ensemble_snapshots_v2 (low)  | 344,532 | 2026-04-28 |
| world.observations | 42,749 | — |
| world.settlements  | 1,609  | — |

## Sentinel files (T0)

| File | State |
|---|---|
| state/freshness_verdict.json | **MISSING** (never produced) |
| state/ingest_status.json     | **MISSING** (never produced) |
| state/source_health.json     | **MISSING** (never produced) |
| state/heartbeat.json         | **MISSING** (never produced) |
| state/daemon-heartbeat.json  | exists, mtime 2026-04-29T18:27 (stale, pre-Phase-1.5 layout) |
| state/LIVE_LOCK              | content = `LIVE PAUSED` |

## Safety profile

- `state/LIVE_LOCK = "LIVE PAUSED"` — trading control plane explicitly paused. Cycles will run but order placement is gated.
- All Phase 1+1.5+2+3 sentinel readers have hard-fail-on-absent semantics, so first ingest tick must produce sentinels before trading boot is meaningful.
- `state/auto_pause_failclosed.tombstone` (50B) present from 2026-05-01T05:49 — fail-closed marker; needs review if trading exits with that reason.

## Plist contracts (verified)

- `com.zeus.data-ingest`: KeepAlive=**true**, ThrottleInterval=30, RunAtLoad=true, runs `python -m src.ingest_main`
- `com.zeus.live-trading`: KeepAlive=**false** (Q1 RESOLVED), RunAtLoad=true, ZEUS_MODE=live, runs `python -m src.main`
- `com.zeus.heartbeat-sensor`: monitors both `daemon-heartbeat.json` + `daemon-heartbeat-ingest.json`, 5-min threshold, fires at MM=28,58 + RunAtLoad

## Acceptance criteria for smoke test pass

| Phase | Pass condition |
|---|---|
| 2 (ingest boot) | source_health.json + ingest_status.json + daemon-heartbeat-ingest.json appear within ~3 min; no ERROR/Traceback in zeus-ingest.err |
| 3 (trading boot) | freshness_gate verdict written; first cycle reaches evaluator without SystemExit; daemon-heartbeat.json refreshed |
| 4 (15-min run) | At least one ingest tick + one trading cycle observable in logs; DB rows for high/low snapshots either grow or stay constant; no duplicate-source contamination errors |

## Rollback plan

If any Phase fails:
1. Immediate `launchctl unload` for all loaded daemons
2. Capture last 200 lines of each .err log to `logs/<phase>_failure.err`
3. Diagnose without re-loading until operator authorization
