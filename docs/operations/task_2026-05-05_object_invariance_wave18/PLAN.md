# Wave 18 Object-Meaning Invariance: calibration-transfer OOS evidence time basis

Date: 2026-05-05

## Route Evidence

- Root `AGENTS.md` read in-thread. Scoped reads: `src/AGENTS.md`, `src/data/AGENTS.md`, `docs/reference/modules/data.md`, `docs/reference/zeus_math_spec.md`, `docs/operations/current_data_state.md`.
- Semantic boot: `python3 scripts/topology_doctor.py semantic-bootstrap --task-class calibration --task "object-meaning calibration transfer OOS evidence time-basis must not authorize live calibration from row-modulo pseudo-OOS" --files scripts/evaluate_calibration_transfer_oos.py src/data/calibration_transfer_policy.py tests/test_calibration_transfer_policy_with_evidence.py tests/test_evaluate_calibration_transfer_oos.py --json` -> ok.
- Initial navigation returned generic/advisory because no profile admitted the OOS writer plus `src/data` policy reader and tests.
- Topology repair route: `modify topology kernel` admitted `architecture/topology.yaml`, `architecture/digest_profiles.py`, and `tests/test_digest_profile_matching.py`.
- New route: `object meaning calibration transfer oos evidence` admits this packet, the OOS writer, policy reader, and focused tests.

## Phase 0 Map Delta

Money path segment:

`calibration_pairs_v2 + platt_models_v2 -> scripts/evaluate_calibration_transfer_oos.py -> validated_calibration_transfers -> evaluate_calibration_transfer_policy_with_evidence() -> evaluator/entry readiness -> live/shadow calibration-transfer eligibility`

Authority surfaces:
- `docs/reference/zeus_math_spec.md`: OOS/CV must split chronologically by `decision_group`, not random/row-index shuffle.
- `calibration_pairs_v2`: target calibration-pair evidence with source/target route identity, `forecast_available_at`, `target_date`, `authority`, `causality_status`, `training_allowed`, and outcome.
- `platt_models_v2`: source Platt model identity and authority.
- `validated_calibration_transfers`: OOS transfer evidence row; not live authority unless the reader validates time basis, route identity, source Platt authority, target cohort, economics, staleness, and feature-gate state.

Canonical hierarchy:

Current source/calibration facts and executable validation outrank script docstrings. OOS evidence is derived statistical evidence; it must not become live calibration transfer authority unless the split semantics match the math law and the reader can revalidate them.

## Phase 1 Boundary Selection

Candidate: forecast/model evidence -> calibrated belief transfer gate.
- Live-money effect: can change an entry-forecast candidate from `SHADOW_ONLY`/`BLOCKED` to `LIVE_ELIGIBLE`.
- Material values: `pair_id`, `forecast_available_at`, `target_date`, `p_raw`, `outcome`, `lead_days`, route identity fields, `brier_source`, `brier_target`, `brier_diff`, `status`, `evaluated_at`.
- Consumers: policy reader, evaluator, entry readiness writer, status/report tests.
- Stale/bypass paths: legacy `live_promotion_approved`, direct legacy policy calls, same-domain fast path, OOS script dry-run/apply route.
- Safe scoped repair: yes, if no schema migration, DB mutation, retrain, source-routing change, or flag flip is required.

Selected boundary: OOS evidence time basis -> calibration-transfer eligibility.

## Phase 2 Material Value Lineage

| Value | Real object denoted | Origin | Authority/evidence | Unit/space/side | Time basis | Transform | Persistence | Consumers | State |
|---|---|---|---|---|---|---|---|---|---|
| `forecast_available_at` | decision-group chronological availability time | `calibration_pairs_v2` | target cohort evidence | UTC time | available/decision-group time | split ordering key | read only | OOS writer/reader | ambiguous in current writer |
| `pair_id` | row identifier, not time or independence unit | `calibration_pairs_v2` | row identity only | integer id | insertion/order artifact | current `pair_id % 5` split | none | OOS writer/reader | broken if used as OOS time basis |
| target held-out cohort | future/held-out calibration transfer cohort | `calibration_pairs_v2` | derived statistical evidence | raw probability/outcome | OOS validation interval | select last chronological block | `validated_calibration_transfers` summary | policy reader | to repair |
| `brier_target`/`brier_diff` | OOS transfer loss/economic eligibility statistic | OOS script | derived statistical evidence | Brier score | evidence window/evaluated_at | Platt apply + Brier | `validated_calibration_transfers` | policy reader | broken if row-modulo cohort |
| `status` | transfer safety classification | OOS script | derived statistical evidence | enum | evaluated_at | threshold comparison | `validated_calibration_transfers.status` | policy/evaluator | broken if based on pseudo-OOS |

UNKNOWN: existing production `validated_calibration_transfers` rows are not inspected or relabeled in this wave.

## Phase 3 Findings

W18-F1 (S1/S0 if gate enabled): `scripts/evaluate_calibration_transfer_oos.py` uses `pair_id % 5 == 0` and documents that as OOS held-out evidence. `docs/reference/zeus_math_spec.md` requires OOS/CV code to split chronologically by `decision_group`, never row-random/row-index shuffle. A row-id modulo cohort can become `LIVE_ELIGIBLE` calibration transfer evidence without proving time-forward generalization.

W18-F2 (S1/S0 if gate enabled): `src/data/calibration_transfer_policy.py::target_transfer_cohort_evidence_valid()` recomputes the same `pair_id % 5` cohort, so the reader validates the wrong evidence object rather than rejecting legacy pseudo-OOS rows.

W18-F3 (S1): `validated_calibration_transfers` has no split-method field. Existing rows cannot be silently relabeled as time-blocked OOS truth. Repair must be executable-reader validation over source rows, not DB relabeling.

## Phase 4 Repair Plan

Restored invariant: calibration-transfer OOS evidence can authorize live eligibility only when writer and reader agree on a chronological target cohort that preserves decision-group/time basis.

Repair:
- Replace row-modulo held-out selection with deterministic chronological last-block selection per route, ordered by `forecast_available_at`, `target_date`, and `pair_id`.
- Use the same cohort function in writer and reader, so `validated_calibration_transfers` rows written under old pseudo-OOS semantics fail closed unless recomputed from the current chronological cohort.
- Preserve dry-run default and no production DB mutation.
- Add tests that prove recent chronological rows, not `pair_id % 5`, define OOS evidence.

Implementation:
- `src/data/calibration_transfer_policy.py::select_time_blocked_transfer_pairs()` is the shared writer/reader cohort selector.
- `scripts/evaluate_calibration_transfer_oos.py` imports that selector, requires non-empty `decision_group_id`, and writes evidence only from the latest chronological 20% decision groups.
- `target_transfer_cohort_evidence_valid()` recomputes the same time-blocked cohort and rejects rows whose stored `n_pairs`/Brier values do not match that object.
- Existing `validated_calibration_transfers` rows are not relabeled or backfilled.

## Phase 5 Verification Plan

- `pytest -q -p no:cacheprovider tests/test_evaluate_calibration_transfer_oos.py tests/test_calibration_transfer_policy_with_evidence.py -k 'oos or transfer or time or cohort or policy'`
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_object_meaning_calibration_transfer_oos_evidence_routes_to_wave18_profile`
- `python3 -m py_compile scripts/evaluate_calibration_transfer_oos.py src/data/calibration_transfer_policy.py tests/test_evaluate_calibration_transfer_oos.py tests/test_calibration_transfer_policy_with_evidence.py`
- `python3 scripts/digest_profiles_export.py --check`
- `python3 scripts/topology_doctor.py --schema`
- Planning-lock, freshness, map-maintenance, and `git diff --check`.

## Phase 5 Verification Results

- `python3 -m py_compile scripts/evaluate_calibration_transfer_oos.py src/data/calibration_transfer_policy.py tests/test_evaluate_calibration_transfer_oos.py tests/test_calibration_transfer_policy_with_evidence.py` -> pass.
- `pytest -q -p no:cacheprovider tests/test_evaluate_calibration_transfer_oos.py` -> `47 passed`.
- `pytest -q -p no:cacheprovider tests/test_calibration_transfer_policy_with_evidence.py` -> `44 passed`.
- `pytest -q -p no:cacheprovider tests/test_digest_profile_matching.py::test_object_meaning_calibration_transfer_oos_evidence_routes_to_wave18_profile` -> pass.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files ... --plan-evidence docs/operations/task_2026-05-05_object_invariance_wave18/PLAN.md` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --freshness-metadata --changed-files ...` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --map-maintenance --changed-files ...` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --schema` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --naming-conventions` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --task-boot-profiles` -> `topology check ok`.
- `python3 scripts/topology_doctor.py semantic-bootstrap --task-class calibration ... --json` -> ok.
- `python3 scripts/topology_doctor.py --scripts --json` -> still globally fails on 28 pre-existing script manifest/naming issues; `scripts/evaluate_calibration_transfer_oos.py` is not among issues.
- `git diff --check` -> pass.

Relationship tests added:
- `test_time_blocked_holdout_uses_latest_decision_groups_not_pair_id_modulo`
- `test_missing_decision_group_cannot_write_transfer_evidence`
- `test_pseudo_oos_target_evidence_fails_closed_against_time_blocked_cohort`

Critic review:
- Verdict: `APPROVE`.
- Sweep covered OOS writer, policy reader, live evaluator readiness writer, entry shadow gate, transfer-sigma path, schema/table references, script manifest/topology, and full-repo references to `validated_calibration_transfers`, `live_promotion_approved`, and `compute_transfer_logit_sigma`.
- No Wave18 findings remain. Residual global `--scripts --json` failures are pre-existing unrelated manifest/naming issues and do not include `scripts/evaluate_calibration_transfer_oos.py`.
