# P7R2-DELTA-05-INIT-SCHEMA-ADDITIVE-CANONICAL-TABLES

```yaml
work_packet_id: P7R2-DELTA-05-INIT-SCHEMA-ADDITIVE-CANONICAL-TABLES
packet_type: schema_packet
objective: Resolve DELTA-05 by upgrading the runtime bootstrap seam in `src/state/db.py::init_schema()` so fresh/current runtime DBs gain the additive canonical support tables (`position_current` and related additive canonical tables) without pretending that legacy runtime event helpers are already cut over.
why_this_now: The frozen P7R migration-only packet could not actually solve DELTA-05 because the concrete blocker lives in the runtime bootstrap seam (`init_schema()` still provisions only the legacy runtime DB shape). The packet must be superseded onto the real fix surface.
why_not_other_approach:
  - Keep working inside a migration-only packet | cannot repair the live runtime bootstrap path that actually creates the local DB shape
  - Switch fully to canonical init now | would likely break legacy runtime helpers and would overreach into cutover
  - Freeze a DB-first packet instead | still contradicts parity evidence while `position_current` is absent in runtime reality
truth_layer: The touched runtime bootstrap path must create the additive canonical support tables in current runtime DB reality. This packet is about bootstrap substrate presence, not cutover.
control_layer: This packet is limited to `src/state/db.py`, migration/schema references as needed, targeted architecture tests, and slim control surfaces. It must not change runtime read authority to DB-first and must not delete legacy surfaces.
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
  - src/state/db.py
  - migrations/2026_04_02_architecture_kernel.sql
  - tests/test_architecture_contracts.py
  - scripts/replay_parity.py
  - work_packets/P7R-DELTA-05-RUNTIME-POSITION-CURRENT-BOOTSTRAP.md
files_may_change:
  - work_packets/P7R2-DELTA-05-INIT-SCHEMA-ADDITIVE-CANONICAL-TABLES.md
  - src/state/db.py
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
  - src/engine/**
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
schema_changes: true
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - targeted schema/bootstrap tests
parity_required: true
replay_required: true
rollback: Revert the P7R2 bootstrap edits and paired slim control-surface updates together; repo returns to the completed P7.2 reporting boundary with DELTA-05 still explicitly open.
acceptance:
  - `init_schema()` or the touched runtime bootstrap path can produce a DB containing `position_current` and the additive canonical support tables.
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

- Team remains closed by default for this repair packet.
- This packet may add canonical support tables to the legacy runtime bootstrap, but it must not silently switch legacy helpers onto canonical event semantics.
- If implementation evidence shows the right fix is broader than `src/state/db.py`, reopen or supersede honestly instead of freelancing outside the frozen boundary.

## Closeout readiness notes

- P7.3-readiness note: if this packet lands cleanly and its post-close gate passes, the next lawful move is reassessing whether parity evidence now supports a later DB-first/cutover-prep packet.
