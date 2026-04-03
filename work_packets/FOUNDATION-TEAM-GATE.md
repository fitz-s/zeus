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
rollback: Revert the team-gate packet and paired Architects ledger updates together; team remains blocked until a new gate packet is accepted.
acceptance:
  - Staffing map is explicit.
  - Lane ownership is explicit.
  - Verification path is explicit.
  - Shutdown / rollback / cleanup path is explicit.
  - Actual team opening is still blocked until this packet is executed and accepted.
  - Day0/K3 local edits are explicitly treated as a separate packet family and remain out of scope here.
evidence_required:
  - staffing map
  - lane ownership map
  - verification path note
  - cleanup / rollback path note
  - out-of-scope packet-family note
```

## Notes

- This packet is the post-P0.5 gate, not the actual team launch.
- It must not silently absorb local Day0/K3 feature-family edits.
- If later team execution is approved, it must still remain one frozen packet at a time.
