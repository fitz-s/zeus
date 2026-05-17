# zeus-deep-alignment-audit — Run History

| Run | Date | Branch @ HEAD | Tracks | Findings delta | Notes |
|---|---|---|---|---|---|
| Run #14 | 2026-05-17 | `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `b973ece` | A: market_events triple-write decision; B: F46+F48 root causes; C: alias lint; D: daemon supervision | **+8** (F85–F92) | Verdict A2 (forecasts-authoritative); F87 forecast-live DAEMON DOWN flagged Karachi-HOT |

> Prior runs (#1 – #13) tracked in session journals / commit history; this file
> begins with Run #14 (first run to formalize history index).
| Run #15 T1 | 2026-05-17 | `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `7fb380c5` | Track 1: F90 deep dive (jobs.json vs crontab vs launchd) | **+5** (F90a/b/c + F93/F94/F95); F90 REFRAMED SEV-1→SEV-3 | Run #14 F90 premise disproven (crontab=24 active, not 2; jobs.json IS scheduled by openclaw-node). True SEV-1: F90a (3 enabled jobs failing every tick on `payload.model` reject). |
| Run #15 T3 | 2026-05-17 | `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ pending-commit | F91 heartbeat consumer trace + F86 SIGTERM forensic | F91 AMBIGUOUS→CONFIRMED-NO-WIRE; F86 NEW→CONFIRMED; +3 (F99, F100, F101) | 5 HB writers / 1 functional consumer / 4 NO-WIRE; 3 live-money daemons exit -15 with zero forensic trace = exactly the 3 without SIGTERM handlers |
| Run #15 T2 | 2026-05-17 | `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `7fb380c59d` | F48 second-pass on `monitor_refresh._check_persistence_anomaly` | **+3** (F102, F103, F104) + F48→HOT-FIX-SPEC | Proved Run #14 1-liner is no-op (bare-name binds to MAIN trades.db); spec'd schema-qualified `forecasts.settlements_v2` SELECT + `PERSISTENCE_FALLBACK_TRIGGERED` counter + antibody test. Surfaced F102: `temp_persistence` empty everywhere → secondary blocker. |
