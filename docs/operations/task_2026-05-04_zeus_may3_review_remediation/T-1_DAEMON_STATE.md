# T-1_DAEMON_STATE — Coordinator Snapshot

Captured: 2026-05-04T16:40:37Z
Captured-by: coordinator (Opus 4.7); operator-asserted daemon not running prior to this snapshot.

## Process scan
```
  501 14177     1   0 11:27PM ??         0:24.77 /opt/homebrew/Cellar/python@3.14/3.14.3_1/Frameworks/Python.framework/Versions/3.14/Resources/Python.app/Contents/MacOS/Python -m src.riskguard.riskguard
```

## launchd labels matching zeus/riskguard
```
14177	-15	com.zeus.riskguard-live
4571	0	com.zeus.data-ingest
-	0	com.zeus.heartbeat-sensor
```

## ZEUS_MODE env (current shell)
```
ZEUS_MODE=unset
```

## Verdict (CORRECTED 2026-05-04T16:55:00Z — see Erratum below)
- Live trading daemon (`python -m src.main`): **NOT RUNNING** (process scan shows no `src.main`).
- RiskGuard daemon: **RUNNING** — PID 14177 actively executing `python -m src.riskguard.riskguard` for 12+ hours; launchd label `com.zeus.riskguard-live` actively loaded.
- data-ingest daemon (com.zeus.data-ingest, PID 4571): RUNNING — read-only ingest, NOT flagged as T0.1/T0.2 blocker by current plan.
- heartbeat-sensor (com.zeus.heartbeat-sensor): label loaded, no PID — informational.
- Operator pre-attestation: operator said "Daemon没有在运行"; that statement was about the **live trading daemon** (T0.1), which is genuinely not running. RiskGuard (T0.2) was not covered by that attestation and is RUNNING.
- Secrets: no credentials/paths/PIDs containing PII present in this artifact.

## Erratum
The original verdict (commit before 2026-05-04T16:55:00Z) read "RiskGuard daemon: NOT RUNNING (same scan)." That was a coordinator-side reading error: the process-scan section literally records PID 14177 running riskguard, and the launchd-label section records `com.zeus.riskguard-live` loaded. The planner-opus subagent (`a41cb399b00e2e357`) caught the contradiction during T0 reality triage; this correction is captured here so downstream artifacts read a consistent T-1 baseline.

T0.2 (RiskGuard unloaded) is therefore an **OPERATOR BLOCKER** for T1 dispatch, per MASTER_PLAN_v2 §8 Required Steps row T0.2 and §4 working-contract §1. Operator must `launchctl bootout` `com.zeus.riskguard-live` and re-attest before T1 execution. See `LOCK_DECISION.md` Amendment 1.

## Erratum-2 — Post-bootout (2026-05-04T17:30:00Z)

Coordinator executed `launchctl bootout gui/$(id -u)/com.zeus.riskguard-live` per operator direct authorization **"直接执行boot out然后继续"** at 2026-05-04 ~17:29Z. Bootout exit 0; PID 14177 terminated; `com.zeus.riskguard-live` label removed from `launchctl list`. Full evidence at `T0_DAEMON_UNLOADED.md §6`.

Current Zeus daemon footprint (post-bootout):
- Live trading daemon (`src.main`): NOT RUNNING (never running during this packet)
- RiskGuard daemon (`com.zeus.riskguard-live`): UNLOADED at 2026-05-04T17:30Z
- `com.zeus.data-ingest` (PID 4571): RUNNING — read-only ingest, NOT a T0 blocker
- `com.zeus.heartbeat-sensor`: label loaded, no PID — informational only

T0.2 gate is now CLOSED. T0_PROTOCOL_ACK.md Decision flips to `proceed_to_T1`.
