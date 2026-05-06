# Zeus May3 Review Remediation — Active Truth Index

**Purpose:** Remediation decision record and protocol for Day0 daemon shutdown, pre-live safety checks, and authority lock (May 2-4, 2026). Directly informs `architecture/` and live system rollout.

**Status:** ACTIVE. Referenced by 13 items in `architecture/` (risk policies, alert gates, harvester doctrine, venue quiescence) and 1 in `src/`.

## Canonical Truth Documents (LOCK phase)

These files are cited in code and architecture and represent the authority basis for live decisions:

- **LOCK_DECISION.md** — Authority lock: final go/no-go verdict with risk bounds
- **MASTER_PLAN_v2.md** — Comprehensive remediation roadmap with T-N timeline
- **scope.yaml** — Soft-warn enforcement scope (referenced in `script_manifest.yaml`)
- **ORCHESTRATOR_RUNBOOK.md** — Operator playbook for coordinated system checks

## Policy Documents (Enforcement Basis)

Live system configuration and enforcement gates powered by these:

- **T0_SQLITE_POLICY.md** — DB busy-timeout and connection pool policy (cited in `src/state/db.py`)
- **T0_DAEMON_UNLOADED.md** — Daemon unload protocol and verification checklist
- **T0_HARVESTER_POLICY.md** — Settlement harvester behavior gates
- **T0_ALERT_POLICY.md** — Alert thresholds and suppression rules
- **T0_VENUE_QUIESCENT.md** — Venue entry freeze conditions
- **T0_PROTOCOL_ACK.md** — Pre-live protocol acknowledgments and sign-off
- **T0_D6_FIELD_LOCK.md** — Day 6 field semantics lock

## Evidence & Verification (Diagnostic Trail)

Forensic records that validate the lock decision:

- **T-1_COMPAT_SUBMIT_SCAN.md** — Compatibility submission audit
- **T-1_DAEMON_STATE.md** — Daemon state snapshot at lock time
- **T-1_SCHEMA_SCAN.md** — DB schema consistency audit
- **T-1_KNOWN_GAPS_COVERAGE.md** — Known gaps coverage analysis
- **T-1_GIT_STATUS.md** — Git repo state snapshot
- **T-1_TOPOLOGY_ROUTE.md** — System topology verification

## Process Records (Review Cycle)

Review and approval trail for the lock:

- **PLAN.md** — Initial remediation plan and scope
- **planner_output.md** — Automated planning agent output
- **critic_round5_response.md** — Final critic round (adversarial review)

---

**Guidance:** All truth-bearing items above are CANONICAL. Files in this directory may be consolidated into `architecture/` if they exceed 10KB or acquire dependencies; currently all fit in operations workspace. Scratchpad analysis beyond the canonical set may be safely archived when space becomes a concern.
