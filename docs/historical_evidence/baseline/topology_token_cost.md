# Topology Token Cost Baseline

Created: 2026-05-06
Last reused or audited: 2026-05-06
Authority basis: ULTIMATE_DESIGN sunset 2027-05-06; IMPLEMENTATION_PLAN Phase 0.A

## Method

Token counts via tiktoken cl100k_base (project venv). Each task sum reflects the
files an agent must read to answer "can I touch file X?" under the current
topology-doctor regime:

- All write-capable tasks (Tasks 1-3): topology.yaml + invariants.yaml +
  source_rationale.yaml + task_boot_profiles.yaml + digest_profiles.py +
  7 topology_doctor modules (main, cli, core_map, source_checks, context_pack,
  policy_checks, digest) = full bootstrap set.
- Read-only audit (Task 4): topology.yaml + invariants.yaml + task_boot_profiles.yaml
  + topology_doctor main/cli/core_map (no write-route files needed).
- Doc-only (Task 5): topology.yaml + invariants.yaml + topology_doctor_cli +
  topology_doctor_docs_checks.

## Individual file token counts (cl100k_base)

| File | Tokens |
|---|---|
| architecture/topology.yaml | 78,151 |
| architecture/invariants.yaml | 7,761 |
| architecture/source_rationale.yaml | 20,909 |
| architecture/digest_profiles.py | 69,387 |
| architecture/task_boot_profiles.yaml | 3,999 |
| scripts/topology_doctor.py | 24,825 |
| scripts/topology_doctor_cli.py | 7,314 |
| scripts/topology_doctor_core_map.py | 4,118 |
| scripts/topology_doctor_source_checks.py | 1,976 |
| scripts/topology_doctor_context_pack.py | 11,532 |
| scripts/topology_doctor_policy_checks.py | 8,570 |
| scripts/topology_doctor_digest.py | 11,663 |
| **TOTAL (all files)** | **250,205** |

## Per-task bootstrap token cost

| Task | Files required | Tokens |
|---|---|---|
| 1. calibration_persistence_write (src/calibration/manager.py) | Full set | 250,205 |
| 2. settlement_write (src/execution/harvester.py) | Full set | 250,205 |
| 3. live_executor_refactor (src/execution/executor.py + exit_triggers.py) | Full set | 250,205 |
| 4. read_only_audit (src/contracts/world_view/) | No write routes | 126,168 |
| 5. doc_only_task (docs/operations/AGENTS.md) | Topology + inv + cli + docs_checks | 100,306 |

**Mean: 195,417 tokens**
**Max: 250,205 tokens**
**Min: 100,306 tokens**

## Briefing target

Post-cutover target: ≤30,000 tokens (briefing §9). Current baseline is 6-8x above
target for write tasks, ~3x above for doc-only tasks.
