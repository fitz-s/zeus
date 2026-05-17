# zeus-deep-alignment-audit — Run History

| Run | Date | Branch @ HEAD | Tracks | Findings delta | Notes |
|---|---|---|---|---|---|
| Run #14 | 2026-05-17 | `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `b973ece` | A: market_events triple-write decision; B: F46+F48 root causes; C: alias lint; D: daemon supervision | **+8** (F85–F92) | Verdict A2 (forecasts-authoritative); F87 forecast-live DAEMON DOWN flagged Karachi-HOT |

> Prior runs (#1 – #13) tracked in session journals / commit history; this file
> begins with Run #14 (first run to formalize history index).
| Run #15 T1 | 2026-05-17 | `fix/wave-2-lineage-and-k1-cleanup-2026-05-17` @ `7fb380c5` | Track 1: F90 deep dive (jobs.json vs crontab vs launchd) | **+5** (F90a/b/c + F93/F94/F95); F90 REFRAMED SEV-1→SEV-3 | Run #14 F90 premise disproven (crontab=24 active, not 2; jobs.json IS scheduled by openclaw-node). True SEV-1: F90a (3 enabled jobs failing every tick on `payload.model` reject). |
