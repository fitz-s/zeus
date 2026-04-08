# VERIFY-ETL-RECALIBRATE-CONTAMINATION

```yaml
work_packet_id: VERIFY-ETL-RECALIBRATE-CONTAMINATION
packet_type: verification_repair_packet
objective: Prove the shared ETL/recalibrate chain stays on shared truth surfaces and repair the discovered TIGGE multi-step collapse so upstream calibration data is not silently contaminated or mislabeled.
why_this_now: BUG-CANONICAL-CLOSURE-TRACEABILITY is accepted and its post-close gate passed. The session-leftovers re-audit still identifies ETL/recalibrate contamination as the highest-risk open external-data seam. Fresh repo truth now shows a concrete blocker inside that family: `src/main.py::_etl_recalibrate()` still lacks packet-bounded proof that its subprocess chain stays venv-rooted and import-safe, and `scripts/etl_tigge_calibration.py` currently collapses each date directory to the last TIGGE step file while hard-coding `lead_hours = 24.0`, which corrupts shared `ensemble_snapshots` / `calibration_pairs` truth.
why_not_other_approach:
  - Jump directly to position/settlement trace convergence | upstream shared calibration truth is still an open higher-layer contamination source and now has a concrete data-loss bug
  - Accept the isolation migration map as proof by itself | documentation says the chain is migrated, but current runtime code still contains an untested subprocess seam and a real step-processing defect
  - Widen into every ETL script at once | too broad; keep this packet on the weekly recalibrate chain plus the discovered TIGGE multi-step seam
truth_layer: weekly recalibration scripts and TIGGE calibration ETL must bind shared-world data through `get_shared_connection()`, preserve per-step TIGGE lead semantics, and remain import-safe when launched as subprocesses from outside repo cwd; they must not silently collapse multi-step source vectors into one mislabeled shared snapshot.
control_layer: limit the change to `src/main.py`, `scripts/etl_tigge_calibration.py`, packet-bounded representative ETL/recalibrate tests, and control surfaces. Do not widen into trade/lifecycle/risk/status fixes, broader migration cleanup, or strategy diagnosis.
evidence_layer: work-packet grammar output, kernel-manifest check output, targeted ETL/recalibrate pytest output, a subprocess import-path/shared-binding note, and a TIGGE multi-step truth note showing pre-fix loss and post-fix preservation.
zones_touched:
  - K2_runtime
  - K3_extension
invariants_touched:
  - INV-03
  - INV-06
  - INV-09
required_reads:
  - architecture/self_check/authority_index.md
  - docs/governance/zeus_autonomous_delivery_constitution.md
  - docs/architecture/zeus_durable_architecture_spec.md
  - docs/governance/zeus_change_control_constitution.md
  - architecture/kernel_manifest.yaml
  - architecture/invariants.yaml
  - architecture/zones.yaml
  - architecture/negative_constraints.yaml
  - AGENTS.md
  - scripts/AGENTS.md
  - tests/AGENTS.md
  - architects_state_index.md
  - architects_task.md
  - architects_progress.md
  - /tmp/zeus_session_note_reaudit/docs/session_2026_04_07_leftovers_reaudit.md
  - docs/isolation_migration_map.md
  - src/main.py
  - scripts/etl_observation_instants.py
  - scripts/etl_diurnal_curves.py
  - scripts/etl_temp_persistence.py
  - scripts/refit_platt.py
  - scripts/etl_tigge_ens.py
  - scripts/etl_tigge_calibration.py
  - scripts/run_replay.py
  - tests/test_observation_instants_etl.py
  - tests/test_run_replay_cli.py
files_may_change:
  - work_packets/VERIFY-ETL-RECALIBRATE-CONTAMINATION.md
  - architects_progress.md
  - architects_task.md
  - architects_state_index.md
  - src/main.py
  - scripts/etl_tigge_calibration.py
  - tests/test_observation_instants_etl.py
  - tests/test_run_replay_cli.py
  - tests/test_etl_recalibrate_chain.py
files_may_not_change:
  - AGENTS.md
  - .claude/CLAUDE.md
  - docs/governance/**
  - docs/architecture/**
  - architecture/**
  - migrations/**
  - src/control/**
  - src/execution/**
  - src/observability/**
  - src/riskguard/**
  - src/state/db.py
  - src/state/lifecycle_manager.py
  - src/state/portfolio.py
  - .github/workflows/**
  - zeus_final_tribunal_overlay/**
schema_changes: false
ci_gates_required:
  - python3 scripts/check_work_packets.py
  - python3 scripts/check_kernel_manifests.py
tests_required:
  - .venv/bin/pytest -q tests/test_observation_instants_etl.py
  - .venv/bin/pytest -q tests/test_run_replay_cli.py
  - .venv/bin/pytest -q tests/test_etl_recalibrate_chain.py
parity_required: false
replay_required: false
rollback: Revert the ETL/recalibrate verification-repair edits and paired tests/control-surface updates together; repo returns to the current accepted closure-packet boundary with ETL contamination proof and the TIGGE multi-step loss still explicitly open.
acceptance:
  - `_etl_recalibrate()` has packet-bounded proof that it launches the expected representative scripts through the repo venv and absolute script paths
  - representative shared ETL/recalibrate scripts have packet-bounded proof that they remain import-safe from outside repo cwd and still bind `get_shared_connection()`
  - `etl_tigge_calibration.py` preserves every TIGGE step file per date directory and records the correct per-step lead metadata instead of silently collapsing to the last step
  - targeted tests pass and the control surfaces record the packet boundary truthfully
evidence_required:
  - work-packet grammar output
  - kernel-manifest check output
  - targeted ETL/recalibrate pytest output
  - subprocess import-path/shared-binding note
  - TIGGE multi-step truth note
```

## Notes

- This packet stays on the highest-priority leftover family only: ETL/recalibrate contamination proof plus the concrete TIGGE multi-step defect discovered during fresh re-analysis.
- Degraded-path rule for this packet: verification code may prove import/shared-binding via subprocess import probes without executing the full ETL body against live state; if a script cannot be safely probed that way, the packet must fail loud or freeze a narrower follow-up instead of fabricating proof.
- If implementation shows the fix needs shared-schema redesign, replay-engine contract changes, or trade-db/lifecycle edits, stop and freeze a new packet instead of widening this one.

## Evidence log

- work-packet grammar output: `.venv/bin/python scripts/check_work_packets.py` -> `work packet grammar ok`
- kernel-manifest check output: `.venv/bin/python scripts/check_kernel_manifests.py` -> `kernel manifests ok`
- targeted ETL/recalibrate pytest output: `.venv/bin/pytest -q tests/test_observation_instants_etl.py tests/test_run_replay_cli.py tests/test_etl_recalibrate_chain.py` -> `15 passed`
- subprocess import-path/shared-binding note:
  - new `tests/test_etl_recalibrate_chain.py::test_etl_recalibrate_launches_expected_scripts_via_repo_venv` proves `_etl_recalibrate()` launches the expected representative scripts through a repo-local `.venv` interpreter when present and otherwise falls back to the current interpreter
  - new `tests/test_etl_recalibrate_chain.py::test_representative_shared_scripts_import_from_outside_repo_cwd[...]` proves representative scripts remain import-safe from outside repo cwd and still bind `get_shared_connection`
- TIGGE multi-step truth note:
  - fresh synthetic reproduction before the repair processed `vectors_processed = 2` but stored only one `ensemble_snapshots` row (`tigge_cal_v3_step048`) with `lead_hours = 24.0`
  - `tests/test_etl_recalibrate_chain.py::test_etl_tigge_calibration_preserves_all_steps_and_lead_hours` now proves two rows are preserved (`step024`, `step048`) with `lead_hours = 24.0` / `48.0` and `new_pairs = 22`
- independent pre-close critic artifact: `.omx/artifacts/claude-verify-etl-recalibrate-preclose-critic-20260408T073113Z.md` -> `PASS`
- pre-close verifier note:
  - primary Gemini verifier path failed locally due certificate/auth issues (`.omx/artifacts/gemini-verify-etl-recalibrate-preclose-verifier-20260408T073244Z.md`)
  - fallback verifier artifact: `.omx/artifacts/claude-verify-etl-recalibrate-preclose-verifier-fallback-20260408T073355Z.md` -> `PASS`
