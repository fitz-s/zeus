# Schema Packet Template

## Front matter (copy and complete)

```yaml
work_packet_id: SCH-001
packet_type: schema_packet
objective: ""
why_this_now: ""
why_not_other_approach:
  - ""
truth_layer: ""
control_layer: ""
evidence_layer: ""
zones_touched: []
invariants_touched: []
required_reads: []
files_may_change: []
files_may_not_change: []
schema_changes: false
ci_gates_required: []
tests_required: []
parity_required: false
replay_required: false
rollback: ""
acceptance:
  - ""
evidence_required:
  - ""
```

## Implementation notes
- Keep the patch atomic.
- Do not expand scope beyond the listed files.
- If a forbidden file becomes necessary, stop and rewrite the packet.
- If this packet touches K0, include a manifest diff and schema diff.

## Schema-specific questions
- Which enum/constraint/trigger is being introduced or changed?
- What replay/parity evidence is required?
- How is append-only / idempotency preserved?
