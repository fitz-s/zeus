File: docs/authority/zeus_openclaw_venus_delivery_boundary.md
Disposition: NEW
Authority basis: docs/authority/zeus_current_delivery.md; docs/authority/zeus_current_architecture.md; current repo boundary surfaces (src/supervisor_api/contracts.py, src/control/control_plane.py, scripts/healthcheck.py, scripts/audit_architecture_alignment.py, scripts/venus_autonomy_gate.py).
Supersedes / harmonizes: docs/architecture/venus_operator_architecture.md; docs/architecture/venus_zeus_audit_integration_plan.md as active boundary law.
Why this file exists now: Zeus needs one narrow repo-local statement of what Venus and OpenClaw may and may not do.
Current-phase or long-lived: Long-lived.

# Zeus OpenClaw / Venus Delivery Boundary

## 0. Boundary verdict

- **Zeus owns inner runtime truth and repo law.**
- **Venus owns outer supervision, audit, and narrow control requests.**
- **OpenClaw owns workspace/runtime hosting, memory injection, notifications, and optional gateway routing.**
- Neither Venus nor OpenClaw is allowed to become Zeus's architectural authority.

## 1. Canonical contract paths

### Current phase
- Typed contract definitions: `src/supervisor_api/contracts.py`
- Command ingress: `src/control/control_plane.py`
- Operator health/observation export: `src/observability/status_summary.py` and `scripts/healthcheck.py`
- Audit/repo-host alignment check: `scripts/audit_architecture_alignment.py`
  - repo-local architecture drift may be reported as blocking
  - external workspace / host-surface assumptions must remain advisory in the current phase

### End state
- Typed contract definitions remain repo-local
- Derived operator surfaces should be projected from canonical DB authority
- Venus still consumes contracts and derived exports, not direct DB ownership

## 2. Current-phase reality vs target

### Current phase
Venus can currently:
- read status/health exports
- read typed contract objects
- issue narrow control-plane commands
- run audit and autonomy checks

Current phase limitations:
- status surfaces are derived from mixed runtime truth
- canonical `position_current` projection exists and is used by harvester, riskguard, replay, and status_summary; legacy `position_events_legacy` coherence boundary tests remain skipped pending Phase2 legacy elimination
- repo audit still assumes external workspace surfaces that were not supplied in this session
- external host/workspace checks may inform operator confidence, but they do not outrank repo-local authority or become merge/cutover blockers by themselves

### End state
Venus should:
- consume projection-backed status
- trigger audits, packets, and narrow safety actions
- never own canonical truth, schema, or repo authority

## 3. OpenClaw position

OpenClaw is the outer workspace/runtime host.

It may provide:
- workspace boot files
- memory injection
- notification gateway
- optional command gateway
- session-level automation

It may not:
- redefine repo authority order
- bypass packet and gate rules
- treat workspace memory as canonical repo truth
- directly mutate DB truth or architectural law

## 4. Control-plane narrow autonomy

### Allowed autonomous now
- `request_status`
- `pause_entries` with evidence and TTL
- `tighten_risk` with evidence and TTL

### Advisory only now
- `set_strategy_gate`
- `acknowledge_quarantine_clear`

### Human gate required now
- `resume`
- any expansion of command vocabulary
- any change that re-enables risk after a safety-triggered pause
- any permanent change of strategy gating policy

## 5. Actions that must not be automated now

- schema migration
- live cutover
- direct DB writes
- direct write to positions/portfolio files
- merge / deploy / archive of authority files by outer host hooks
- automatic widening of strategy eligibility
- automatic disabling of gates because a tool or provider failed

## 6. Venus boundary by function

| Function | Zeus | Venus | OpenClaw |
|---|---|---|---|
| canonical runtime truth | owns | reads derived only | no |
| operator status export | produces | reads / summarizes | may deliver notifications |
| control commands | executes validated commands | proposes / writes narrow ingress only | may host gateway but not widen authority |
| architecture authority | owns | no | no |
| audit | receives findings | owns outer audit loop | may host reminders/notifications |
| workspace memory | no | may read outputs | owns host/workspace memory surfaces |

## 7. Hook policy

### OMX / OMC hooks are acceptable as:
- notifications
- callback triggers
- session summaries
- advisory reminders
- stop/resume helpers

### Hooks are not acceptable as:
- silent policy writers
- schema mutators
- hidden merge agents
- authority routers

## 8. Current skeleton vs long-term end state

### Current skeleton
- file-derived status
- narrow control plane
- typed contracts
- outer audit scripts
- host workspace assumptions still visible in repo audit

### Long-term end state
- projection-backed status
- narrower and more explicit autonomous control policy
- repo/host boundary checks that are deliberate and versioned
- no ambiguous workspace docs treated as shadow law

## 9. Required evidence before any boundary expansion

- contract diff
- operator impact note
- safety analysis
- rollback note
- human approval if expansion affects live control behavior

## 10. Special uncertainty note

This boundary note is actionable for repo design now.
It is not complete verification of the user’s actual workspace host setup because the external workspace files referenced by the repo were not provided in this session.

## 11. Audit interpretation rule

`scripts/audit_architecture_alignment.py` must distinguish:

- **repo-local blocking drift**
  - replay guard loss
  - decision-reference loss
  - clear repo-runtime architecture regressions

- **external advisory assumptions**
  - workspace operator surfaces
  - OpenClaw host configuration
  - host/runtime enablement outside repo control

Current-phase rule:
- external host/workspace checks may be reported
- they may not silently become repo-authority blockers
- a green audit-policy result does not claim full external-environment verification
