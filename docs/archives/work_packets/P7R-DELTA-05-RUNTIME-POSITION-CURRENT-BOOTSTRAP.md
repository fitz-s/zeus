# P7R-DELTA-05-RUNTIME-POSITION-CURRENT-BOOTSTRAP

```yaml
work_packet_id: P7R-DELTA-05-RUNTIME-POSITION-CURRENT-BOOTSTRAP
packet_type: schema_packet
objective: Resolve DELTA-05 by making the canonical projection substrate (`position_current` and compatible runtime bootstrap path) actually present in current runtime DB reality, without pretending DB-first reads or cutover are already authorized.
why_this_now: P7.2 passed, and its real parity output showed the current blocker explicitly: `position_current` is still absent in the local runtime DB state. A DB-first/cutover-prep packet would be dishonest until this target/runtime split is repaired.
why_not_other_approach:
  - Freeze a DB-first/cutover-prep packet now | contradicts the current parity evidence
  - Accept the missing projection as normal for longer | leaves parity reporting truthful but blocks actual migration progress
  - Hide the problem under another reporting packet | avoids the concrete runtime/schema contradiction
truth_layer: The target/runtime split around `position_current` must be repaired explicitly. This packet is about making the canonical projection substrate present in runtime reality; it is not a cutover packet.
control_layer: This packet is limited to migration/bootstrap mechanics for the canonical projection substrate and the minimum contract/smoke tests needed to prove it exists on the touched runtime path. It must not switch read authority to DB-first and must not delete legacy surfaces.
evidence_layer: targeted schema/bootstrap pytest output, work-packet grammar output, kernel-manifest check output, rollback note, and explicit p7.3-readiness note.
zones_touched:
  - K0_frozen_kernel
  - K2_runtime
invariants_touched:
  - INV-03
  - INV-08
  - INV-10
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/governance/zeus_change_control_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - src/state/AGENTS.md
  - tests/AGENTS.md
  - migrations/2026_04_02_architecture_kernel.sql
  - tests/test_architecture_contracts.py
  - scripts/replay_parity.py
  - work_packets/P7.2-M2-PARITY-REPORTING.md
files_may_change:
  - work_packets/P7R-DELTA-05-RUNTIME-POSITION-CURRENT-BOOTSTRAP.md
  - migrations/**
  - tests/test_architecture_contracts.py
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
files_may_not_change:
  - AGENTS.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - src/**
  - tests/test_pnl_flow_and_audit.py
  - tests/test_runtime_guards.py
  - tests/test_riskguard.py
  - tests/test_healthcheck.py
  - .github/workflows/**
  - .claude/CLAUDE.md
  - zeus_final_tribunal_overlay/**
schema_changes: true
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - targeted schema/bootstrap tests
parity_required: true
replay_required: true
rollback: Revert the P7R DELTA-05 migration/bootstrap edits and paired slim control-surface updates together; repo returns to the completed P7.2 reporting boundary with DELTA-05 still explicitly open.
acceptance:
  - The touched runtime/bootstrap path can produce a DB with `position_current` present.
  - The packet does not claim DB-first read cutover or deletion of legacy surfaces.
  - Replay/parity output can advance beyond `missing_tables = [position_current]` on the touched bootstrap path.
evidence_required:
  - targeted pytest output
  - work-packet grammar output
  - kernel-manifest check output
  - rollback note
  - p7.3-readiness note
```

## Notes

- Team remains closed by default for this repair/migration packet.
- If implementation evidence shows the right move is a different superseding migration packet shape, reopen this packet honestly instead of patching around the contradiction.
- This packet is substrate/bootstrapping only, not cutover.

## Closeout readiness notes

- P7.3-readiness note: if this packet lands cleanly and its post-close gate passes, the next lawful move is reassessing whether a DB-first/cutover-prep packet is now actually supported by parity evidence.

## Supersession note

- Implementation evidence showed the actual DELTA-05 repair seam is `src/state/db.py::init_schema()`, not migration SQL alone.
- This freeze is therefore superseded by a packet that can touch the runtime bootstrap seam directly.
