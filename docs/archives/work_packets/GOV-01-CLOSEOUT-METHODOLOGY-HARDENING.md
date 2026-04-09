# GOV-01-CLOSEOUT-METHODOLOGY-HARDENING

```yaml
work_packet_id: GOV-01-CLOSEOUT-METHODOLOGY-HARDENING
packet_type: governance_packet
objective: Harden repo methodology so closure claims are defeasible by repo truth, post-closeout user-found issues are treated as review failure rather than normal follow-up, and pre-closeout review explicitly requires multi-lane independent checking before architectural closeout claims are allowed.
why_this_now: The recent P2 repair showed the prior closeout method was too weak: packet completion was mistaken for bottom-layer convergence, control surfaces overclaimed closure, and the human could still trivially find additional blocker-level issues after acceptance.
why_not_other_approach:
  - Leave the method implicit in chat memory | would let the same closure-review failure recur
  - Patch only AGENTS or only the constitution | would leave repo-local operating law split across surfaces
truth_layer: A closure claim is only valid if repo truth, independent review, and control surfaces all agree; when they do not, repo truth wins and the claim must reopen.
control_layer: This packet updates only methodology/governance instruction surfaces and paired control surfaces; it does not change runtime code, schema, or packet-family implementation order.
evidence_layer: Narrow doc diff, contradiction-to-rule mapping note, rollback note, and explicit no-runtime-change note.
zones_touched:
  - K1_governance
invariants_touched:
  - INV-10
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/governance/zeus_change_control_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - AGENTS.md
files_may_change:
  - work_packets/GOV-01-CLOSEOUT-METHODOLOGY-HARDENING.md
  - AGENTS.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
files_may_not_change:
  - src/**
  - tests/**
  - migrations/**
  - docs/architecture/**
  - architecture/**
  - .github/workflows/**
  - .claude/CLAUDE.md
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
tests_required:
  - review only
parity_required: false
replay_required: false
rollback: Revert the methodology packet and paired control-surface updates together if the wording proves too broad or contradictory.
acceptance:
  - AGENTS.md encodes closure-reopen doctrine and pre-closeout independent review requirements.
  - The autonomous delivery constitution encodes the same doctrine at governance level.
  - The updated critic/review rule makes user-found post-closeout defects a process failure signal, not a normal extension of critic scope.
  - No runtime or schema change is mixed into this packet.
evidence_required:
  - doc diff
  - contradiction-to-rule mapping note
  - rollback note
  - no-runtime-change note
```
