# FOUNDATION-TEAM-GATE

```yaml
work_packet_id: FOUNDATION-TEAM-GATE
packet_type: feature_packet
objective: Freeze the team-opening gate that turns post-P0.5 packet-by-packet team autonomy from eligible to actually allowed, with explicit staffing, lane ownership, verification, and cleanup rules.
why_this_now: P0.5 is complete and pushed, so the next safe step is not “open team now” but to freeze the exact gate that governs when team execution becomes allowed for later packets.
why_not_other_approach:
  - Open team from momentum now | would violate the implementation-OS rule that P0.5 does not self-authorize team execution
  - Treat local Day0/K3 edits as part of the active mainline packet | would silently mix packet families and corrupt mainline control state
truth_layer: Team autonomy becomes allowed only through an explicit post-P0.5 gate packet, not through implicit momentum, local dirt, or chat memory.
control_layer: This packet defines staffing, lane ownership, verification, and shutdown/rollback/cleanup conditions for future team usage; it does not launch team execution itself.
evidence_layer: Team-opening gate note, staffing map, lane ownership map, verification path, cleanup/rollback path, and explicit out-of-scope packet-family boundary note.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - work_packets/FOUNDATION-MAINLINE-PLAN.md
  - work_packets/P0.5-IMPLEMENTATION-OS.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/zones.yaml
  - AGENTS.md
  - architects_progress.md
  - architects_task.md
files_may_change:
  - work_packets/FOUNDATION-TEAM-GATE.md
  - architects_progress.md
  - architects_task.md
files_may_not_change:
  - AGENTS.md
  - src/**
  - migrations/**
  - architecture/**
  - docs/governance/**
  - docs/architecture/**
  - .github/workflows/**
  - .claude/CLAUDE.md
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - planning review only
parity_required: false
replay_required: false
rollback: Revert the team-gate packet and paired Architects ledger updates together; revoke any downstream packet's claim to team eligibility that depended on this gate; team remains blocked until a new gate packet is accepted.
acceptance:
  - Staffing map is explicit.
  - Lane ownership is explicit.
  - Verification path is explicit.
  - Shutdown / rollback / cleanup path is explicit.
  - Actual team opening is still blocked until this packet is executed, accepted, pushed, and then used by a later frozen packet.
  - Day0/K3 local edits are explicitly treated as a separate packet family and remain out of scope here.
evidence_required:
  - staffing map
  - lane ownership map
  - verification path note
  - cleanup / rollback path note
  - out-of-scope packet-family note
  - dependency proof note
```

## Notes

- This packet is the post-P0.5 gate, not the actual team launch.
- It must not silently absorb local Day0/K3 feature-family edits.
- If later team execution is approved, it must still remain one frozen packet at a time.

## Staffing Map

This is a **recommended operating contract**, not a hardcoded requirement that every future packet must use all lanes.

- **Leader / owner lane**
  - model: `gpt-5.4 xhigh`
  - responsibility:
    - freeze packets
    - decide staffing
    - own final acceptance
    - own stop/escalate calls

- **Read-only scout lane(s)**
  - model: `gpt-5.3-codex-spark low`
  - responsibility:
    - repo mapping
    - symbol/file lookup
    - narrow relationship tracing
  - steady-state headcount:
    - preferred `1-2`
    - do not exceed `3` unless the packet explicitly justifies it

- **Verifier / bounded synthesis lane**
  - model: `gpt-5.4-mini high`
  - responsibility:
    - evidence compression
    - contradiction extraction
    - packet-scoped verification
    - pre-critique synthesis

- **Critic / adversarial review lane**
  - model: `gpt-5.4 high|xhigh`
  - responsibility:
    - attack hidden assumptions
    - challenge false completion
    - challenge scope drift and rollback weakness

## Lane Ownership Map

- **Named owner**
  - `Architects mainline lead`

- Leader owns:
  - packet boundary
  - acceptance gate
  - final merge/closeout judgment

- Scout lanes own:
  - read-only lookup only
  - no packet authority, no writes to control surfaces

- Verifier lane owns:
  - packet-scoped evidence review
  - no authority to widen scope

- Critic lane owns:
  - attack review only
  - no authority verdict by itself

### Critic veto rule

- A HIGH-severity contradiction from the critic lane must be resolved, narrowed, or explicitly blocked before team launch.
- The leader may not ignore a HIGH-severity critic finding for convenience.

## Verification Path

Before any future team packet can be accepted:

1. packet scope must be frozen
2. owner and lane ownership must be explicit
3. required tests/gates must run and be read
4. explicit adversarial review must run
5. final leader/architect acceptance must be recorded
6. the future packet must still survive with a clean/reviewable tree before team launch

## Operational Gate Rubric

- **Current-phase closure work**
  - any packet in the tribunal/current-phase closure family
  - any direct reconciliation packet whose purpose is to finish or repair that family

- **Genuinely parallelizable**
  - disjoint write scopes, or
  - one write lane plus read-only verifier/critic/scout lanes
  - not “multiple files involved” by itself

- **Destructive / cutover / final-transition**
  - live cutover timing
  - archive/delete transitions
  - irreversible migration switches
  - authority-stack deletion/demotion that changes active law

## Dependency Proof

This gate depends on the following already-completed packet family:

- `FOUNDATION-MAINLINE-PLAN`
- `P0.5-IMPLEMENTATION-OS`

This gate does not widen or override them.
It converts their “later team autonomy is eligible” rule into an operational gate only after:
- staffing is explicit
- lane ownership is explicit
- verification is explicit
- cleanup/rollback is explicit

## Shutdown / Rollback / Cleanup Path

Before shutting down a future team run:

1. confirm no in-progress packet task is being abandoned silently
2. record packet state in `architects_progress.md`
3. record any remaining open slice in `architects_task.md` or successor packet
4. verify git cleanliness or explicitly record residual out-of-scope dirt

If a future team run fails:

1. stop widening scope
2. preserve packet ownership
3. isolate or discard dirty lanes before continuing
4. fall back to single-owner execution if needed
5. do not treat a half-finished multi-lane run as packet completion

If this gate itself is found flawed after a later packet relied on it:

1. freeze further team eligibility immediately
2. revert the gate packet and paired ledgers
3. mark the affected downstream packet as blocked until a superseding team gate is accepted
4. do not retroactively treat the old gate as still valid just because team already started once

## Exact Post-P0.5 Team-Autonomy Conditions

Post-P0.5 packet-by-packet team autonomy becomes **allowed** only when all of the following are true:

1. the packet is already frozen
2. the packet is not current-phase closure work
3. the packet has explicit owner, allowed files, forbidden files, acceptance, blocker policy, and verification path
4. the work is genuinely parallelizable into disjoint lanes
5. the packet is not a destructive/cutover/final-transition packet

Meeting these conditions makes team autonomy **eligible** for that later packet.
It does not auto-launch team mode; the future packet owner must still choose and launch it explicitly.

## Out-of-Scope Packet-Family Rule

- Day0/K3 feature-family edits remain outside the active mainline packet unless separately packetized.
- Local out-of-scope work must not be silently mixed into team-gate or later mainline packet claims.
- A future team packet must start from either:
  - a clean tree, or
  - an explicitly recorded out-of-scope list that is frozen before launch
