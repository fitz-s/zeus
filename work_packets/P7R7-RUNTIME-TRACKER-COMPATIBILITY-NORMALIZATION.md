# P7R7-RUNTIME-TRACKER-COMPATIBILITY-NORMALIZATION

```yaml
work_packet_id: P7R7-RUNTIME-TRACKER-COMPATIBILITY-NORMALIZATION
packet_type: feature_packet
objective: Normalize the live runtime `strategy_tracker-paper.json` compatibility metadata so the persisted file matches the compatibility-only law already enforced by code/tests, without widening into M4 retirement or broader runtime redesign.
why_this_now: After P7.7 code-level hardening landed, runtime truth still showed `state/strategy_tracker-paper.json` advertising `tracker_role = attribution_surface`. That later contradiction means the accepted boundary must be reopened explicitly and repaired rather than patched quietly.
why_not_other_approach:
  - Ignore the live runtime drift because load/save now normalize it | leaves an active runtime file contradicting the accepted repo-law claim about the persisted compatibility surface
  - Jump to M4 retirement/delete work | crosses into retirement/destructive territory before the still-live compatibility file is normalized
  - Reopen P7.7 silently without a repair packet | violates the explicit reopen/repair discipline for later truth contradictions
truth_layer: The persisted tracker runtime file itself must say compatibility-only truth plainly and consistently; in-memory normalization alone is not enough once the accepted boundary claimed persisted alignment.
control_layer: This packet is limited to runtime tracker-file normalization via the existing rebuild/save path, packet-bounded evidence capture, and slim control surfaces. It must not widen into delete/retirement work and must not introduce new schema or reader changes.
evidence_layer: work-packet grammar output, kernel-manifest check output, explicit runtime before/after metadata note, rollback note, and p7.8-readiness note.
zones_touched:
  - K1_governance
  - K2_runtime
invariants_touched:
  - INV-04
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
  - src/state/strategy_tracker.py
  - scripts/rebuild_strategy_tracker_current_regime.py
  - work_packets/P7.7-M3-STRATEGY-TRACKER-COMPATIBILITY-HARDENING.md
files_may_change:
  - work_packets/P7R7-RUNTIME-TRACKER-COMPATIBILITY-NORMALIZATION.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
runtime_artifacts_touched:
  - state/strategy_tracker-paper.json
  - state/strategy_tracker-paper-history.json
files_may_not_change:
  - AGENTS.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - src/**
  - tests/**
  - scripts/**
  - migrations/**
  - .github/workflows/**
  - .claude/CLAUDE.md
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - none
parity_required: false
replay_required: false
rollback: Restore the prior runtime tracker file snapshots if needed and revert the slim control-surface updates together; repo returns to the accepted P7.7 code boundary while the runtime-file contradiction remains open.
acceptance:
  - the live runtime tracker file advertises compatibility-only metadata consistent with repo law
  - the packet does not delete `strategy_tracker.json` or claim M4 retirement
  - the repair is recorded explicitly as a reopened runtime-file normalization packet, not silently folded into P7.7
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - explicit runtime before/after metadata note
  - rollback note
  - p7.8-readiness note
```

## Notes

- Team remains closed by default for this packet.
- This is a runtime normalization repair packet, not a new code-change packet.
- If current runtime truth later drifts again, reopen explicitly rather than overclaiming that code-path hardening alone guarantees file-state convergence.

## Closeout readiness notes

- P7.8-readiness note: if this repair packet lands cleanly and its post-close gate passes, reassess whether any bounded non-destructive pre-retirement packet remains before M4 decisions.

## Evidence log

- work-packet grammar output: `python3 scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `python3 scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- explicit runtime before/after metadata note: `.omx/artifacts/p7r7-runtime-normalization-note-20260405T000000Z.md`
- rollback note: restore the prior runtime tracker file snapshots if needed and revert the slim control-surface updates together; repo returns to the accepted P7.7 code boundary while the runtime-file contradiction remains open
- p7.8-readiness note: after a clean P7R7 closeout, reassess whether any bounded non-destructive pre-retirement packet remains before M4 decisions
