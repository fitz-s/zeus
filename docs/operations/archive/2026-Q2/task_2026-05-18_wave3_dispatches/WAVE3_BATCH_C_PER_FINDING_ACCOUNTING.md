# WAVE-3 Batch C — Per-Finding Accounting

Created in response to phase critic finding "Per-finding accounting incomplete (F91/F92/F105 silent gaps)".

| Finding | Verdict | Rationale |
|---|---|---|
| F85 Daemon stdout/stderr inversion | SHIP | Dual-handler logging across 4 daemons (main.py:1373-1395, ingest_main.py:1182-1198, forecast_live_daemon.py:780-797, riskguard.py:1449-1462). Sed-break verified. |
| F86 SIGTERM no audit trail | SHIP | SIGTERM_RECEIVED forensic line in 3 daemons + healthcheck.py. `(never exited)` exclusion correct. **Asymmetry note**: data-ingest + forecast-live had pre-existing SIGTERM handlers logging `"received SIGTERM"`; new handlers use `SIGTERM_RECEIVED` token. Operator grep tooling will match 3 of 5 daemons. Unification of the two log-string conventions is deferred to a separate trivial-batch PR. |
| F87 forecast-live exit code 1 | DEFER-AUTHORITY-RETRACT | Per `RUN_16_track_A.md` line 45: `F87 → CLOSED-FALSE-ALARM`. No code action required. Correctly silent. |
| F89 heartbeat-sensor launchctl | SHIP-PARTIAL | StartCalendarInterval plist probe shipped (Probe 1). **Probe 2 deleted in this batch** — was tautological (`KEEPALIVE_PID_MEANS_CRASHED = True; assert ... is True`), failed antibody contract per `feedback_antibody_recursion_metaverify_essential`. Probe 1 catches the actual regression class. |
| F91 heartbeat alert path | **DEFER** | Authority RUN_15 line 177: "AMBIGUOUS → CONFIRMED-NO-WIRE for 4 of 5 surfaces". The remediation (close alert loop via folding staleness into healthcheck.py) requires touching healthcheck.py beyond F86's SIGTERM trail and risks coupling with F99/F100 fix. **Deferred to a follow-up "heartbeat alert-loop closure" PR**, scoped separately. Tracked as new task item. |
| F92 riskguard `auth/api-key` 400 | **DEFER** | Original report RUN_15 track 3 cites a 400 response on a riskguard auth probe. Currency check shows the auth surface has since been touched by PR #137 + #140. Cannot reproduce the 400 from current main without a live test environment. **Deferred** to a riskguard-auth-probe PR where reproduction infrastructure is set up. Tracked as new task item. |
| F99 heartbeat write/read asymmetry | **SHIPPED-PARTIAL (corrected from "SHIPPED")** | Commit `714aa0fdcd` body claimed "F99 already SHIPPED in origin/main via b2d534a9c7" — that's **overstated**. `b2d534a9c7` registers F99 via PENDING token + paired-existence test. The RUN_15 priority-3 fix ("fold staleness checks into healthcheck.py") is **NOT** wired. The PENDING gap is documented and CI-visible, not silent — but the alerting loop remains open. Reclassified: SHIPPED-PARTIAL. Alert-loop closure deferred with F91. |
| F100 daemon-heartbeat-ingest.json zero readers | **SHIPPED-PARTIAL (corrected from "SHIPPED")** | Same as F99 — registered as PENDING in `b2d534a9c7`, alert loop open. Reclassified SHIPPED-PARTIAL. |
| F101 schema drift across heartbeat payloads | SHIP-PARTIAL | `HEARTBEAT_SCHEMA_REGISTRY` registers 5 schemas. Sed-break verified the antibody catches **registry-internal** corruption. Does NOT load actual runtime payloads against the registry — drift between code and registry is undetected. Unification deferred per RUN_15 LOW priority. Runtime-payload comparison antibody deferred to a follow-up PR. |
| F105 EXIT_ORDER_REJECTED false phase log | **DEFER** | Authority describes the false phase log as a Tier-0 execution surface concern (`src/execution/`). This batch is HARD-EXCLUDED from `src/execution/` per `feedback_pr_unit_of_work_not_loc` (would dilute the observability/heartbeat coherence). **Deferred** to a focused exit-lifecycle-logging PR. Tracked as new task item. |

## Summary
- **SHIP**: 4 (F85, F86, F89, F101 — last two partial with deferred enhancements)
- **SHIPPED-PARTIAL (corrected)**: 2 (F99, F100 — claim downgraded from "already SHIPPED" to "PENDING token + alert loop deferred")
- **DEFER**: 3 (F91, F92, F105 — explicit reason + follow-up batch noted)
- **AUTHORITY-RETRACT**: 1 (F87 → false alarm per RUN_16_track_A)

## Carry-forward task items
1. heartbeat alert-loop closure (F91 + F99 + F100 alert-path wiring; healthcheck.py)
2. riskguard auth-probe reproduction PR (F92)
3. EXIT_ORDER_REJECTED false phase log fix (F105 — Tier-0 execution surface, separate review)
4. F101 runtime-payload registry comparison antibody (low priority)
5. SIGTERM log-string unification across all 5 daemons (`SIGTERM_RECEIVED` vs `"received SIGTERM"`)
