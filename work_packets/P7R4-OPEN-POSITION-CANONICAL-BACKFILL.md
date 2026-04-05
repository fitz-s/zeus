# P7R4-OPEN-POSITION-CANONICAL-BACKFILL

```yaml
work_packet_id: P7R4-OPEN-POSITION-CANONICAL-BACKFILL
packet_type: feature_packet
objective: Seed canonical event+projection state for currently open legacy paper positions so parity no longer reports an empty canonical open side against a non-empty legacy export, without claiming DB-first cutover or deleting legacy surfaces.
why_this_now: P7R3 removed the legacy `position_events` schema collision. The next truthful parity blocker is now concrete data mismatch: current runtime parity reports canonical open positions as zero while `state/positions-paper.json` still reports twelve open `opening_inertia` positions. The next honest move is to seed those still-open legacy positions into canonical authority through append-first writes.
why_not_other_approach:
  - Freeze a DB-first/cutover packet now | parity still shows concrete open-position mismatch, so cutover would overclaim readiness
  - Leave canonical open side empty longer | preserves a known parity failure after the event-authority collision is already repaired
  - Backfill projection only without canonical events | violates append-first authority discipline
  - Do broad migration cleanup first | widens beyond the current blocker before parity is narrowed
truth_layer: Existing open legacy paper positions must gain canonical representation through append-first entry event batches plus deterministic projection updates, not through projection-only fabrication.
control_layer: This packet is limited to a bounded canonical backfill path for currently open legacy paper positions, the minimum builder/db/script support needed for that path, targeted parity tests, and slim control surfaces. It must not cut reads over and must not delete legacy exports.
evidence_layer: targeted backfill/parity pytest output, work-packet grammar output, kernel-manifest check output, runtime parity-before/after evidence, rollback note, and explicit p7.5-readiness note.
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
  - src/state/portfolio.py
  - src/engine/lifecycle_events.py
  - scripts/replay_parity.py
  - state/positions-paper.json
  - tests/test_architecture_contracts.py
  - work_packets/P7.3-M1-OPEN-POSITION-CANONICAL-BACKFILL.md
  - work_packets/P7R3-LEGACY-POSITION-EVENTS-COLLISION-REPAIR.md
files_may_change:
  - work_packets/P7R4-OPEN-POSITION-CANONICAL-BACKFILL.md
  - src/state/db.py
  - src/engine/lifecycle_events.py
  - scripts/**
  - tests/test_architecture_contracts.py
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
files_may_not_change:
  - AGENTS.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/control/**
  - src/observability/**
  - src/riskguard/**
  - src/execution/**
  - src/supervisor_api/**
  - src/state/portfolio.py
  - src/state/ledger.py
  - src/state/projection.py
  - tests/test_pnl_flow_and_audit.py
  - tests/test_runtime_guards.py
  - tests/test_riskguard.py
  - tests/test_healthcheck.py
  - .github/workflows/**
  - .claude/CLAUDE.md
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - targeted backfill/parity tests
parity_required: true
replay_required: true
rollback: Revert the P7R4 canonical-backfill edits and paired slim control-surface updates together; repo returns to the accepted P7R3 boundary with canonical open-side parity still empty.
acceptance:
  - Currently open legacy paper positions gain canonical event+projection representation on the touched backfill path.
  - Replay/parity output advances beyond the current empty-canonical-open-side mismatch on the touched seam.
  - No DB-first read cutover or legacy-surface deletion is mixed into this packet.
evidence_required:
  - targeted pytest output
  - work-packet grammar output
  - kernel-manifest check output
  - runtime parity-before/after note
  - rollback note
  - p7.5-readiness note
```

## Notes

- Team remains closed by default for this packet.
- This packet is about canonical seeding/backfill for existing open legacy paper positions, not general migration cleanup or cutover.
- If implementation evidence shows the mismatch still depends on a deeper blocker, reopen or supersede honestly instead of fabricating projection-only parity.

## Closeout readiness notes

- P7.5-readiness note: if this packet lands cleanly and its post-close gate passes, the next lawful move is reassessing whether parity evidence now supports a later DB-first/cutover-prep packet.
