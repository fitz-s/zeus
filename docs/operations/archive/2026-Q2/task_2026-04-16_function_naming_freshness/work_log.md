# Function Naming and Freshness Metadata Work Log

Date: 2026-04-16
Branch: data-improve
Task: Add a small governance package for function naming and script/test reuse freshness.
Changed files:
- `AGENTS.md`
- `workspace_map.md`
- `architecture/AGENTS.md`
- `architecture/naming_conventions.yaml`
- `scripts/AGENTS.md`
- `tests/AGENTS.md`
- `architecture/script_manifest.yaml`
- `architecture/topology.yaml`
- `architecture/topology_schema.yaml`
- `scripts/topology_doctor.py`
- `scripts/topology_doctor_cli.py`
- `scripts/topology_doctor_closeout.py`
- `scripts/topology_doctor_digest.py`
- `scripts/topology_doctor_freshness_checks.py`
- `scripts/topology_doctor_script_checks.py`
- `tests/test_topology_doctor.py`
- `docs/operations/AGENTS.md`
- `docs/operations/current_state.md`
- `docs/operations/task_2026-04-16_function_naming_freshness/plan.md`
- `docs/operations/task_2026-04-16_function_naming_freshness/work_log.md`
- `docs/operations/task_2026-04-16_function_naming_freshness/receipt.json`
Summary: Added changed-file enforcement for script/test freshness headers and centralized file/function naming rules in `architecture/naming_conventions.yaml`. `script_manifest.yaml` now points to the naming map instead of redefining naming/freshness policy. The rule is intentionally changed-files-only so old scripts/tests become review-required before reuse without creating a mass historical cleanup.
Verification: `python -m py_compile scripts/topology_doctor.py scripts/topology_doctor_cli.py scripts/topology_doctor_closeout.py scripts/topology_doctor_digest.py scripts/topology_doctor_freshness_checks.py scripts/topology_doctor_script_checks.py`; `python scripts/topology_doctor.py --naming-conventions --summary-only`; `python scripts/topology_doctor.py --freshness-metadata --changed-files ... --summary-only`; `python -m pytest -q tests/test_topology_doctor.py -k 'naming_conventions or freshness_metadata or script_digest_routes_agents_to_lifecycle_law or scripts_mode_rejects_long_lived_one_off_script_name or closeout_compiles_selected_lanes or closeout_filters_repo_global_lane_noise or cli_json_parity_for_closeout_command'`; `python scripts/topology_doctor.py closeout --plan-evidence docs/operations/task_2026-04-16_function_naming_freshness/plan.md --work-record-path docs/operations/task_2026-04-16_function_naming_freshness/work_log.md --receipt-path docs/operations/task_2026-04-16_function_naming_freshness/receipt.json --summary-only`.
Next: After this package lands, future cleanup can add an advisory inventory of old scripts/tests without turning that inventory into a blocking default gate.
