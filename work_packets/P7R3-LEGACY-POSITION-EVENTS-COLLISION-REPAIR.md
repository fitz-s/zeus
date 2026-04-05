# P7R3-LEGACY-POSITION-EVENTS-COLLISION-REPAIR

```yaml
work_packet_id: P7R3-LEGACY-POSITION-EVENTS-COLLISION-REPAIR
packet_type: schema_packet
objective: Resolve the legacy `position_events` schema collision that blocks append-first canonical seeding, so later canonical backfill can land honestly without bypassing event authority.
why_this_now: After P7R2, parity no longer fails on missing tables; it now shows canonical open side empty. Implementation evidence then showed the immediate blocker is the legacy `position_events` table shape, which prevents canonical event+projection seeding through the normal append-first path.
why_not_other_approach:
  - Keep trying to seed canonical open positions through the current event table | fails because the legacy event schema rejects canonical append/project helpers
  - Backfill projection only | violates append-first authority discipline
  - Jump to DB-first cutover | still bypasses the unresolved event-authority collision
truth_layer: The event-authority collision must be repaired explicitly before canonical backfill or cutover claims. This packet is about making the canonical event path possible on the touched runtime/bootstrap seam, not about switching read authority.
control_layer: This packet is limited to the event-schema collision seam, the minimum runtime/bootstrap or migration support needed to repair it, and packet-bounded architecture tests. It must not perform DB-first read cutover and must not delete legacy runtime truth wholesale.
evidence_layer: targeted schema/bootstrap pytest output, work-packet grammar output, kernel-manifest check output, rollback note, and explicit p7.4-readiness note.
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
  - src/state/db.py
  - src/state/ledger.py
  - migrations/2026_04_02_architecture_kernel.sql
  - tests/test_architecture_contracts.py
  - scripts/replay_parity.py
  - work_packets/P7.3-M1-OPEN-POSITION-CANONICAL-BACKFILL.md
files_may_change:
  - work_packets/P7R3-LEGACY-POSITION-EVENTS-COLLISION-REPAIR.md
  - src/state/db.py
  - src/state/ledger.py
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
  - src/control/**
  - src/observability/**
  - src/riskguard/**
  - src/engine/**
  - src/execution/**
  - src/supervisor_api/**
  - src/state/portfolio.py
  - src/state/projection.py
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
rollback: Revert the P7R3 event-collision repair edits and paired slim control-surface updates together; repo returns to the accepted P7R2 boundary with canonical open-side parity still blocked at the event schema.
acceptance:
  - The touched runtime/bootstrap path no longer blocks append-first canonical writes solely because of the legacy `position_events` schema shape.
  - The packet does not claim DB-first read cutover or legacy-surface deletion.
  - A later canonical backfill packet becomes technically possible on the touched seam.
evidence_required:
  - targeted pytest output
  - work-packet grammar output
  - kernel-manifest check output
  - rollback note
  - p7.4-readiness note
```

## Notes

- Team remains closed by default for this packet.
- This packet is about resolving the event-authority collision, not about seeding all open positions itself.
- If implementation evidence shows a different collision seam, reopen or supersede honestly.

## Closeout readiness notes

- P7.4-readiness note: if this packet lands cleanly and its post-close gate passes, the next lawful move is reassessing a bounded canonical open-position backfill packet.
