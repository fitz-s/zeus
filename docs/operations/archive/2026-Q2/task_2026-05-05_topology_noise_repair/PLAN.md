# Topology Noise Repair Plan

Date: 2026-05-05

## Scope

Repair two topology-entry defects that add noise to pipeline-impacting work:

1. `python3 scripts/topology_doctor.py --task-boot-profiles` fails because
   the `agent_runtime` profile requires a missing archived plan path.
2. The `add or change script` route describes `scripts/<script>.py` as an
   allowed file but does not admit real script files under the executable
   matcher, so new long-lived scripts such as
   `scripts/evaluate_calibration_transfer_oos.py` require avoidable
   scope-expansion handling.

## Constraints

- No live venue/account mutation.
- No canonical live/world/risk DB writes.
- No archive reconstruction or silent historical rewrite.
- No promotion of packet evidence into current truth.
- Keep the repair in topology/routing surfaces and focused topology tests.

## Repair Shape

- Replace the missing `agent_runtime.required_reads` archive-body reference
  with a current, existing topology authority surface.
- Convert the script-route placeholder allowlist into executable glob patterns
  that admit top-level reusable scripts and their matching tests while keeping
  existing forbidden surfaces blocked.
- Regenerate the derived digest-profile mirror if canonical
  `architecture/topology.yaml` changes.

## Verification

- `python3 scripts/topology_doctor.py --task-boot-profiles`
- `python3 scripts/topology_doctor.py --navigation --task "add or change script: evaluate_calibration_transfer_oos OOS calibration transfer evidence writer" --intent "add or change script" --write-intent edit --files scripts/evaluate_calibration_transfer_oos.py tests/test_evaluate_calibration_transfer_oos.py architecture/script_manifest.yaml architecture/naming_conventions.yaml`
- `python3 scripts/digest_profiles_export.py --check`
- Focused topology tests covering the boot-profile path and script admission.

## Verification Result

- `python3 scripts/topology_doctor.py --task-boot-profiles` -> `topology check ok`.
- `python3 scripts/topology_doctor.py --navigation --task "add or change script: evaluate calibration transfer OOS dry-run script manifest and route verification" --intent "add or change script" --task-class repair --write-intent edit --files scripts/evaluate_calibration_transfer_oos.py architecture/script_manifest.yaml tests/test_evaluate_calibration_transfer_oos.py tests/test_topology_doctor.py` -> admitted.
- `python3 scripts/digest_profiles_export.py --check` -> pass.
- `pytest -q -p no:cacheprovider tests/test_topology_doctor.py -k 'script_route_admits_real_script_and_matching_test_paths or task_boot_profiles_mode_validates_semantic_profiles or cli_json_parity_for_task_boot_profiles_mode or backfill_outcome_fact'` -> `6 passed`.
- `pytest -q -p no:cacheprovider tests/test_evaluate_calibration_transfer_oos.py` -> `45 passed`.
- `python3 scripts/topology_doctor.py --planning-lock --changed-files architecture/task_boot_profiles.yaml architecture/topology.yaml architecture/digest_profiles.py architecture/naming_conventions.yaml architecture/script_manifest.yaml scripts/evaluate_calibration_transfer_oos.py tests/test_evaluate_calibration_transfer_oos.py tests/test_topology_doctor.py docs/operations/task_2026-05-05_topology_noise_repair/PLAN.md docs/operations/AGENTS.md --plan-evidence docs/operations/task_2026-05-05_topology_noise_repair/PLAN.md` -> `topology check ok`.

Residual:
- `python3 scripts/topology_doctor.py --scripts --json` still reports 28 pre-existing global script manifest/naming issues. The repaired OOS script route and `backfill_outcome_fact.py` are not among the current issues.
