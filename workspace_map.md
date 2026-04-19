# Zeus Workspace Map

This is the root directory router for zero-context agents. It is not a system
encyclopedia and does not override `AGENTS.md`, scoped `AGENTS.md` files, or
machine manifests.

Use this file to choose the next scoped entry point, then run:

`python scripts/topology_doctor.py --navigation --task "<task>" --files <optional files>`

## Default Route

1. Read root `AGENTS.md`.
2. Read this map for directory-level orientation.
3. Run a task digest.
4. Read the scoped `AGENTS.md` for the directory you will touch.
5. Read code/tests only after the route is narrow.

If the pre-code navigation set grows beyond the task budget in
`architecture/context_budget.yaml`, stop and narrow with a digest or packet
plan.

## Directory Router

| Path | Role | Next Read |
|------|------|-----------|
| `src/` | Runtime source code | `src/AGENTS.md`, then package `AGENTS.md` |
| `tests/` | Regression and law gates | `tests/AGENTS.md`; machine view in `architecture/test_topology.yaml` |
| `scripts/` | Operator, ETL, audit, repair, and enforcement tools | `scripts/AGENTS.md`; machine view in `architecture/script_manifest.yaml` |
| `docs/authority/` | Current governance and architecture law | `docs/authority/AGENTS.md` |
| `docs/reference/` | Conditional domain/math/data references | `docs/reference/AGENTS.md` |
| `docs/operations/` | Active packet/control pointers | `docs/operations/AGENTS.md` |
| `docs/runbooks/` | Operator runbooks | `docs/runbooks/AGENTS.md` |
| `docs/to-do-list/` | Active checklist workbooks, not authority | `docs/to-do-list/AGENTS.md` |
| `docs/artifacts/` | Active evidence artifacts, not authority | `docs/artifacts/AGENTS.md` |
| `docs/archives/` | Historical evidence only | local ignored archive route; not reviewer-visible and never default-read |
| `.agents/skills/` | Repo-local workflow skills and handoff guidance | read the target skill `SKILL.md`; AI handoff route starts at `docs/runbooks/task_2026-04-19_ai_workflow_bridge.md` |
| `architecture/` | Machine-checkable authority and topology | `architecture/AGENTS.md` |
| `config/` | Runtime settings and external reality contracts | `config/AGENTS.md` |
| `.github/workflows/` | CI/advisory gates | `.github/workflows/AGENTS.md` |
| `state/` | Runtime DB/cache artifacts, mostly gitignored | classify before treating as truth |
| `data/` | Oracle error rates, PM settlement truth JSON | `data/oracle_error_rates.json` |
| `raw/` | Raw external evidence captures | `raw/AGENTS.md` |

## Source Packages

The canonical file-level zone map is `architecture/zones.yaml`; package labels
below are navigation only.

| Package | Main Role |
|---------|-----------|
| `src/contracts/` | Typed semantic contracts: settlement, execution price, alpha, hold value, provenance |
| `src/state/` | Mixed truth/runtime surfaces; file-level zone map wins |
| `src/riskguard/` | Risk process and behavior-changing risk levels |
| `src/control/` | Venus/OpenClaw control plane |
| `src/execution/` | CLOB execution, exits, settlement harvesting |
| `src/supervisor_api/` | Typed Zeus/Venus boundary contracts |
| `src/engine/` | Cycle orchestration, evaluator, replay/backtest |
| `src/signal/` | Probability generation, Day0, diurnal/model signals |
| `src/calibration/` | Platt calibration and sample/authority logic |
| `src/strategy/` | Edge, fusion, FDR, Kelly, correlation |
| `src/data/` | ECMWF, Polymarket, WU/Open-Meteo ingestion |
| `src/observability/` | Derived status summary for operators/Venus |
| `src/types/` | Unit-safe domain types |

## Machine Manifests

Prefer these over hand-maintained prose when they exist:

| Manifest | Use |
|----------|-----|
| `architecture/self_check/authority_index.md` | High-risk zero-context authority order |
| `architecture/invariants.yaml` | Invariant IDs and enforcement intent |
| `architecture/zones.yaml` | Canonical file-level zone ownership |
| `architecture/negative_constraints.yaml` | Forbidden moves and failure modes |
| `architecture/topology.yaml` | Coverage roots, state/root classification, digest profiles |
| `architecture/source_rationale.yaml` | Per-file `src/**` rationale, hazards, write routes |
| `architecture/test_topology.yaml` | Test categories, law gates, high-sensitivity skips |
| `architecture/script_manifest.yaml` | Script lifecycle, authority scope, read/write targets |
| `architecture/naming_conventions.yaml` | Canonical file/function naming and script/test freshness rules |
| `architecture/data_rebuild_topology.yaml` | Data rebuild certification and non-promotion gates |
| `architecture/history_lore.yaml` | Dense historical failure lessons and antibodies |
| `architecture/artifact_lifecycle.yaml` | Artifact classes and minimum work-record contract |
| `architecture/context_budget.yaml` | Entry-map budget and maintenance cadence |
| `architecture/context_pack_profiles.yaml` | Task-shaped generated context-pack profiles |
| `architecture/change_receipt_schema.yaml` | High-risk route receipt contract and required receipt fields |
| `architecture/code_idioms.yaml` | Intentional non-obvious code shapes and their owner gates |
| `architecture/core_claims.yaml` | Proof-backed semantic claims for generated topology views |
| `architecture/runtime_modes.yaml` | Discovery mode index and shared CycleRunner path |
| `architecture/reference_replacement.yaml` | Reference-doc replacement evidence and deletion eligibility |
| `architecture/map_maintenance.yaml` | Added/deleted-file companion registry rules |
| `raw/AGENTS.md` | Raw evidence capture routing for local/generated input artifacts |

## High-Signal Commands

| Need | Command |
|------|---------|
| Default navigation | `python scripts/topology_doctor.py --navigation --task "<task>" --files <files>` |
| Task digest only | `python scripts/topology_doctor.py digest --task "<task>" --files <files>` |
| Packet prefill | `python scripts/topology_doctor.py packet --packet-type refactor --scope <path> --task "<task>"` |
| Context pack | `python scripts/topology_doctor.py context-pack --pack-type package_review\|debug --task "<task>" --files <files>` |
| Work record | `python scripts/topology_doctor.py --work-record --changed-files <files> --work-record-path <record>` |
| Impact summary | `python scripts/topology_doctor.py impact --files <files>` |
| Core map | `python scripts/topology_doctor.py core-map --profile probability-chain --json` |
| Invariant slice | `python scripts/topology_doctor.py --invariants --zone K3_extension --json` |
| Context budget | `python scripts/topology_doctor.py --context-budget --json` |
| Source rationale | `python scripts/topology_doctor.py --source --json` |
| Test topology | `python scripts/topology_doctor.py --tests --json` |
| Script safety | `python scripts/topology_doctor.py --scripts --json` |
| Naming/freshness map | `python scripts/topology_doctor.py --naming-conventions --json` |
| Code graph cache health | `python scripts/topology_doctor.py --code-review-graph-status --json` |

## Do Not Default-Read

- `docs/archives/**` — historical evidence only.
- `docs/reports/` — generated report sink from declared writers only; not a default route.
- `.omx/context/**` — micro-logs/evidence breadcrumbs, not governing law.
- `.code-review-graph/**` — local derived code-impact cache; not authority or default-read context.
- `docs/known_gaps.md` — load only when investigating active blockers.
- Long math references such as `docs/reference/statistical_methodology.md` and `docs/reference/zeus_math_spec.md` — load when digest/lore routes you there.

## Maintenance Rule

When adding, renaming, or deleting a file:

1. Update the scoped `AGENTS.md` in that directory.
2. Update this map only if directory-level structure changes.
3. Update the relevant machine manifest when one owns the registry.

Run `python scripts/topology_doctor.py --context-budget --json` after every five completed packets or when an entry/default-read file grows materially.
