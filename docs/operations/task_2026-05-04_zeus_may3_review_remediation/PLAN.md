# Plan: Zeus May3 Round-5 Review Remediation
> Created: 2026-05-04 | Status: LOCK_CANDIDATE

## Goal
Make the Round-5 corrected-live remediation plan routable and executable by orchestrated-delivery without granting implementation authority before operator T-1/T0 gates.

## Canonical Plan Artifact

The detailed final plan is `MASTER_PLAN_v2.md` in this directory.

The orchestrator execution wrapper is `ORCHESTRATOR_RUNBOOK.md` in this directory. It translates the detailed plan into coordinator prompts, role assignments, idle boot, phase scopes, critic review, verifier receipts, and co-tenant-safe staging rules.

This `PLAN.md` exists because the Zeus topology profile `operation planning packet` admits `docs/operations/task_*/PLAN.md` as the routeable planning artifact. It is the topology entrypoint; `MASTER_PLAN_v2.md` is the detailed payload.

## Current Status

- `MASTER_PLAN_v2.md` is a LOCK candidate, not yet implementation authority.
- `ORCHESTRATOR_RUNBOOK.md` is a LOCK-candidate companion, not yet implementation authority.
- `scope.yaml` is plan-finalization scope only and forbids source/script/test/workflow changes.
- This packet is restored on latest `main` and registered through `docs/operations/AGENTS.md`; `docs/operations/current_state.md` is intentionally unchanged until the operator freezes an active execution packet.
- Implementation remains blocked until `LOCK_DECISION.md`, T-1 artifacts, and T0 artifacts exist.

## Required Next Artifacts

- [ ] `LOCK_DECISION.md` naming the locked plan artifact.
- [ ] `PLAN_LOCKED.md` or explicit byte-for-byte lock pointer.
- [ ] `T-1_GIT_STATUS.md`
- [ ] `T-1_DAEMON_STATE.md`
- [ ] `T-1_SCHEMA_SCAN.md`
- [ ] `T-1_COMPAT_SUBMIT_SCAN.md`
- [ ] `T-1_KNOWN_GAPS_COVERAGE.md`
- [ ] `T-1_TOPOLOGY_ROUTE.md`
- [ ] `T0_PROTOCOL_ACK.md`
- [ ] `T0_DAEMON_UNLOADED.md`
- [ ] `T0_VENUE_QUIESCENT.md`
- [ ] `T0_SQLITE_POLICY.md`
- [ ] `T0_D6_FIELD_LOCK.md`
- [ ] `T0_HARVESTER_POLICY.md`
- [ ] `T0_ALERT_POLICY.md`

## Execution Order After LOCK

```text
T-1 -> T0 -> T1A -> T1F -> T1BD -> T1C -> T1E -> T1G -> T1H -> T2 -> T3 -> T4
```

## Hard Stop

No executor may start Tier 1 from this file alone. Read `MASTER_PLAN_v2.md`, then require `LOCK_DECISION.md`, all T-1 artifacts, and all T0 artifacts.
