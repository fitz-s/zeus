# F2 work record — calibration retrain loop wiring

Date: 2026-04-27
Branch: plan-pre5
Task: R3 F2 calibration retrain loop wiring — operator-gated trigger + frozen-replay antibody

Changed files:
- docs/AGENTS.md / docs/README.md / docs/operations/AGENTS.md / architecture/AGENTS.md / workspace_map.md / architecture/docs_registry.yaml / docs/reference/AGENTS.md
- architecture/topology.yaml / architecture/source_rationale.yaml / architecture/test_topology.yaml / architecture/module_manifest.yaml
- docs/reference/modules/calibration.md / src/calibration/AGENTS.md
- src/calibration/retrain_trigger.py
- tests/test_calibration_retrain.py / tests/test_digest_profile_matching.py
- docs/operations/current_state.md / docs/operations/task_2026-04-26_ultimate_plan/r3/_phase_status.yaml
- docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F2.yaml
- docs/operations/task_2026-04-26_ultimate_plan/r3/boot/F2_codex_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/evidence/F2_work_record_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/receipt.json
- docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F2_pre_close_2026-04-27.md
- docs/operations/task_2026-04-26_ultimate_plan/r3/reviews/F2_post_close_2026-04-27.md

Summary:
Implemented the F2 retrain trigger seam without enabling live retraining. `src/calibration/retrain_trigger.py` now exposes `status()`, `arm()`, `load_confirmed_corpus()`, and `trigger_retrain()` around a dormant operator-gated calibration promotion path. The gate requires `ZEUS_CALIBRATION_RETRAIN_ENABLED`, an operator evidence artifact on the approved packet route, and a signed operator token (`v1.<operator_id>.<nonce>.<hmac_sha256>` using `ZEUS_CALIBRATION_RETRAIN_OPERATOR_TOKEN_SECRET`). The corpus loader delegates to the U2 `load_calibration_trade_facts` seam and rejects any non-CONFIRMED request before reading. Promotion is atomic with the version-history insert/retire operation and calls `save_platt_model_v2` only after frozen replay PASS; frozen replay FAIL records an audit row and blocks promotion.

Safety notes:
- No `calibration_retrain_decision_*.md` live-go artifact was created in the repo, so the default project status remains DISABLED even if code is present.
- No Platt formula, calibration manager, ensemble signal read path, TIGGE activation, settlement/source routing, production DB, or live venue behavior changed.
- The implementation adds route validation for operator evidence files so arbitrary local docs cannot satisfy `arm()`.

Verification:
- `python3 scripts/topology_doctor.py --navigation --task "R3 F2 Calibration retrain loop ..." ...` -> navigation ok, profile `r3 calibration retrain loop implementation`.
- `python3 -m py_compile src/calibration/retrain_trigger.py` -> ok.
- `pytest -q -p no:cacheprovider tests/test_calibration_retrain.py tests/test_provenance_5_projections.py::test_calibration_training_filters_for_CONFIRMED_only tests/test_digest_profile_matching.py::test_r3_f2_calibration_retrain_loop_routes_to_f2_profile_not_heartbeat` -> 10 passed.
- `python3 docs/operations/task_2026-04-26_ultimate_plan/r3/scripts/r3_drift_check.py --phase F2` -> GREEN=10 YELLOW=0 RED=0.
- F2 closeout with receipt changed_files -> PASS; nonblocking warnings only for code-review-graph freshness/context-budget.
- Pre-close critic Noether initial BLOCK -> remediated -> rerun PASS.
- Pre-close verifier Pauli initial BLOCK -> remediated -> rerun PASS.
- Post-close critic Meitner -> PASS.
- Post-close verifier Hubble initial procedural BLOCK -> artifact updated; verifier Carson rerun PASS.

Pre-close blocker remediation:
- Critic Noether found that frozen-replay PASS promotion could hit the existing `platt_models_v2` active-row uniqueness constraint because F2 inserted the new audit row and then called `save_platt_model_v2` without first deleting/replacing the exact live model key.
- Remediation: the PASS path now calls `deactivate_model_v2` for the same `(metric_identity, cluster, season, data_version, input_space)` inside the same transaction before `save_platt_model_v2`; if the save fails, the delete/audit retire/audit insert roll back together.
- Regression: `tests/test_calibration_retrain.py::test_frozen_replay_pass_replaces_existing_live_platt_row` seeds an existing active row and proves PASS promotion replaces it while preserving a promoted F2 audit row.

Next:
F2 is COMPLETE with pre-close and post-close critic+verifier PASS. F-series is complete; M2 remains held until the M1 INV-29 amendment gate is resolved.


Second-round hardening note:
- Post-G1 security review found the original non-empty operator token gate too forgeable for a retrain-go surface. `arm()` now requires a signed v1 operator token and tests cover missing secret, bad format, and signature mismatch.
