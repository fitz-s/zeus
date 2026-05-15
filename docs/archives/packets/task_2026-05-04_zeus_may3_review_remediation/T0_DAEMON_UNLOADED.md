# T0_DAEMON_UNLOADED — Planner Triage (DRAFT — operator-only)

**Created:** 2026-05-04
**Verdict:** OPERATOR_ONLY — **escalation required before T1**
**Captured-by:** planner subagent (Zeus May3 R5 remediation)

---

## 1. Why this artifact is operator-only and serious

The coordinator-captured `T-1_DAEMON_STATE.md` (2026-05-04T16:40:37Z) records:

> ## Process scan
> ```
>   501 14177     1   0 11:27PM ??         0:24.77 .../Python -m src.riskguard.riskguard
> ```
>
> ## launchd labels matching zeus/riskguard
> ```
> 14177	-15	com.zeus.riskguard-live
> ```
>
> ## Verdict
> - Live daemon: NOT RUNNING ...
> - RiskGuard daemon: NOT RUNNING (same scan).

Planner re-verification at 2026-05-04 (this session, fresh `ps aux` and `launchctl list`):

```
leofitz  14177  ... /Python -m src.riskguard.riskguard
```

```
14177  -15  com.zeus.riskguard-live
```

**RiskGuard IS RUNNING. The T-1 verdict contradicts its own evidence.**

Per MASTER_PLAN_v2 §4 working-contract item 1: *"No live daemon or RiskGuard daemon may run during Tier 1 implementation."* T1 is therefore **BLOCKED** until operator unloads `com.zeus.riskguard-live` and re-attests.

This is not something the planner or coordinator can fix. Per MASTER_PLAN_v2 §16 forbidden actions: *"Do not let an agent call `launchctl` on behalf of operator."* The plan also requires (§8 T0.1/T0.2) that the operator personally unload.

## 2. Required operator action

```bash
launchctl unload /Library/LaunchDaemons/com.zeus.riskguard-live.plist  # or correct path
# or, if user-level:
launchctl bootout gui/$UID/com.zeus.riskguard-live
# verify
ps aux | grep -E "riskguard|src\.main" | grep -v grep   # must be empty
launchctl list | grep -iE "zeus|riskguard"              # must NOT show riskguard-live
```

After unload, operator updates this file with:

```
Date: <YYYY-MM-DDTHH:MM:SSZ>
Operator: <name>
launchctl unload command run: <exact command>
post-unload ps aux output (riskguard absent): <paste sanitized>
post-unload launchctl list output (riskguard-live absent): <paste sanitized>
ZEUS_MODE shell value at unload time: <value>
Verdict: RISKGUARD_UNLOADED
```

## 3. Other live processes observed (informational)

The planner scan also showed these are present but are NOT live trading daemons (they are tmux session shells for healthcheck workspaces):

- `tmux attach-session -t omc-zeus-healthcheck-riskguard-live-label-...` (PIDs 18235, 8958, 8906)

These are session shells, not daemons. They do not need to be killed for T1 quiescence per the working contract, but the operator may want to disambiguate them in their own attestation.

## 4. Planner verdict

- **Status:** BLOCKING for T1.
- **Owner:** operator only.
- **Until resolved:** T0_PROTOCOL_ACK.md cannot say `proceed_to_T1`; phase.json files for T1A/T1F/T1BD may be drafted but not dispatched; LOCK_DECISION.md (if signed) must include this blocker as an explicit caveat.
- **Authority basis:** runtime evidence (`ps aux`, `launchctl list`) outranks T-1 prose (Authority Stack §3 of MASTER_PLAN_v2: "Current runtime code and call graph" rank 2 vs "Operator runbook and state census artifacts" rank 5).

## 5. Source-evidence file:line citations (planner-grep-verified within last 10 minutes)

- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T-1_DAEMON_STATE.md:8` — process scan showing PID 14177 active.
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T-1_DAEMON_STATE.md:13` — launchd label `com.zeus.riskguard-live`.
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/T-1_DAEMON_STATE.md:24` — verdict claims "NOT RUNNING" — contradicts evidence above.
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:121` — working contract item 1 ("No live daemon or RiskGuard daemon may run during Tier 1 implementation").
- `docs/operations/task_2026-05-04_zeus_may3_review_remediation/MASTER_PLAN_v2.md:340-341` — T0.1/T0.2 require operator unload.

---

## 6. Coordinator-applied operator authorization (2026-05-04T17:30:00Z)

Operator authorized via direct CLI message: **"直接执行boot out然后继续"** at 2026-05-04 ~17:29Z (translation: "directly execute the bootout, then continue"). Coordinator (Opus 4.7) executed the bootout on operator's behalf per this explicit authorization.

### Pre-bootout state

```text
$ ps -p 14177 -o pid,etime,command
  PID  ELAPSED COMMAND
14177 23:49:50 .../Python -m src.riskguard.riskguard

$ launchctl list | grep -iE "zeus|riskguard"
14177	-15	com.zeus.riskguard-live
4571	0	com.zeus.data-ingest
-	0	com.zeus.heartbeat-sensor
```

### Bootout command run

```bash
launchctl bootout gui/$(id -u)/com.zeus.riskguard-live
# exit code: 0
```

### Post-bootout state (after 2s settle)

```text
$ ps -p 14177 -o pid,etime,command
  PID ELAPSED COMMAND
(no match — PID 14177 terminated)

$ launchctl list | grep -iE "zeus|riskguard"
4571	0	com.zeus.data-ingest
-	0	com.zeus.heartbeat-sensor
(com.zeus.riskguard-live label removed)
```

`com.zeus.data-ingest` (PID 4571, read-only ingest) and `com.zeus.heartbeat-sensor` (label only, no PID) remain. Neither is flagged by MASTER_PLAN_v2 §8 T0.1/T0.2 — they place no venue orders, mutate no economic state, hold no chain-reconciliation surface.

### Verdict
**RISKGUARD_UNLOADED** — confirmed by independent ps and launchctl evidence above.

ZEUS_MODE shell value at unload time: `unset`.

Authorization audit trail: operator's "直接执行boot out然后继续" message constitutes operator-issued action authorization equivalent to manual bootout. Coordinator is named as the executing principal; operator retains revert authority via re-loading the launchd label.
