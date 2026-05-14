# Created: 2026-05-14
# Last reused or audited: 2026-05-14
# Authority basis: DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md; AGENTS.md money path; topology route for packet review evidence.

# Critic Approval - Data Daemon Live Efficiency Refactor Plan

Reviewed artifact:
`docs/operations/task_2026-05-14_data_daemon_live_efficiency/DATA_DAEMON_LIVE_EFFICIENCY_REFACTOR_PLAN.md`

Verdict: APPROVE

Approval scope: plan-only. This does not approve deployment, production DB
mutation, live venue behavior, calibration refit, TIGGE activation, DB cleanup,
or source-routing change.

## Attack Findings

### C1 - "New daemon" could be complexity theater

Risk: adding `forecast-live-daemon` while leaving old OpenData jobs in
`ingest_main.py` would add another moving part without solving contention.

Verdict: PASS. The plan requires same-phase removal or mutual exclusion of old
OpenData schedules. A new daemon without old-owner retirement is defined as
failure.

### C2 - HTTP 429 could be patched locally but still slow the cycle

Risk: honoring `Retry-After` by globally reducing request rate would protect
the provider but damage live freshness across unrelated steps.

Verdict: PASS. The plan keeps the token bucket as burst guard, handles
`Retry-After` per step, forbids global throttling from one 429, and requires
tests for no final-attempt sleep.

### C3 - The live path may still depend on evaluator-side data production

Risk: if evaluator continues to write entry readiness at evaluation time, the
new daemon only moves producer readiness, not the full live input contract.

Verdict: APPROVE_WITH_PHASE_CONDITION. Phase 3 must either prewrite entry
readiness in the daemon or explicitly remove the reader's need for evaluator
entry-readiness writes. Compatibility is allowed only as a temporary phase
bridge with tests.

### C4 - The plan could overclaim seconds-level efficiency

Risk: "highest efficiency" is narrative unless measured at fetch/extract/DB
stages.

Verdict: PASS_WITH_CONDITION. The plan requires fetch/extract/ingest/commit
timing fields and retry sleep accounting. Final efficiency claims are blocked
until those tests and measurements exist.

### C5 - Cross-module topology could become a broad bypass

Risk: a combined profile might admit unrelated state/execution/risk changes.

Verdict: PASS. The profile forbids executor, venue, risk, settlement,
calibration promotion, DB files, and config changes. It is narrow to
forecast-live producer, OpenData, readiness, and tests.

### C6 - Operational launch could be silently changed

Risk: modifying launchd/supervisor behavior from repo code could affect live
machine state.

Verdict: PASS. The plan separates repo runbook/script updates from actual
operator launch application. External launch changes require explicit operator
authorization.

## Approval Conditions

1. Phase 1 must land relationship tests before or with implementation.
2. Phase 2 must prove old/new OpenData scheduler mutual exclusion.
3. Phase 3 must remove or explicitly quarantine evaluator-side entry-readiness
   write ownership.
4. No final "done" claim without end-to-end smoke from mocked fetch through
   executable forecast reader.
5. Every phase with source/runtime edits gets critic review; `REVISE` is not
   accepted as pass.

## Final Verdict

APPROVE

The plan is not merely additive. It defines removal/mutual-exclusion gates,
measured timing outputs, fail-closed readiness semantics, and phase critic
checks. It is suitable as the implementation control artifact.

