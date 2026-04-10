# Zeus Workspace Map

> Orientation guide for file placement, directory purpose, and naming rules.
> For operating rules, see root `AGENTS.md`. For domain model, see `docs/reference/zeus_domain_model.md`.

## Root

| Item | Purpose |
|------|---------|
| `AGENTS.md` | Root operating brief — read first, always |
| `pytest.ini` | Test configuration |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Git exclusions |
| `.importlinter` | Zone boundary enforcement (import rules) |

## `src/` — Source Code (by zone)

| Package | Zone | Purpose |
|---------|------|---------|
| `contracts/` | K0 | Semantic types, settlement semantics, typed contracts |
| `state/` | K0 | Portfolio, lifecycle manager, chronicler, chain reconciliation, DB truth |
| `control/` | K1 | Control plane (runtime commands from Venus/OpenClaw) |
| `riskguard/` | K1 | Independent risk process, risk level enforcement |
| `supervisor_api/` | K2 | Supervisor contracts for external systems |
| `execution/` | K2 | Executor, exit triggers, fill tracker, harvester |
| `engine/` | K3 | CycleRunner orchestrator, evaluator, monitor refresh |
| `signal/` | K3 | EnsembleSignal (Monte Carlo), Day0Signal, model agreement |
| `calibration/` | K3 | Extended Platt, calibration store, drift detection |
| `strategy/` | K3 | Market analysis, α-weighted fusion, FDR filter, Kelly sizing |
| `data/` | K3 | External data clients (ECMWF, Polymarket, WU, observations) |
| `analysis/` | K4 | Analysis utilities |
| `observability/` | K4 | Status summary (written every cycle for Venus) |
| `types/` | — | Temperature, TemperatureDelta (unit safety) |

Each `src/` package has (or should have) its own `AGENTS.md` with zone-specific rules.

## `docs/` — Documentation

### Mesh design principle

`docs/` uses a **flat mesh architecture**: each first-level subdirectory contains only files that are actively referenced by the mesh network (rooted at `AGENTS.md`). Everything else lives in `docs/archives/`. This keeps agent context loading fast — agents read only what's linked, not what's adjacent.

### Architecture (active authority)

| Path | Purpose |
|------|---------|
| `docs/architecture/zeus_durable_architecture_spec.md` | Architecture spec — DB schema, event spine, truth surfaces, P0 decisions |
| `docs/architecture/zeus_p1_p8_implementation_spec.md` | P1-P8 implementation detail — fact layer, migration, coding OS |
| `docs/architecture/zeus_discrete_settlement_support_amendment.md` | Settlement support as architecture authority |

### Reference (load on demand)

| Path | Purpose |
|------|---------|
| `docs/reference/zeus_domain_model.md` | "Zeus in 5 minutes" — probability chain, WHY explanations |
| `docs/reference/repo_overview.md` | Technical/runtime orientation |
| `docs/reference/workspace_map.md` | This file |
| `docs/reference/model_routing.md` | Codex/GPT model routing (Claude/Gemini: skip) |
| `docs/reference/quantitative_research.md` | Calibration math, Kelly, sample sizes (Chinese) |
| `docs/reference/market_microstructure.md` | Edge thesis, participant types, entry timing (Chinese) |
| `docs/reference/statistical_methodology.md` | Three σ, instrument noise, FDR, data versioning (Chinese) |

### Governance (active authority)

| Path | Purpose |
|------|---------|
| `docs/governance/zeus_change_control_constitution.md` | Packet governance rules (Chinese) |
| `docs/governance/zeus_autonomous_delivery_constitution.md` | Delivery and runtime-governance authority |
| `docs/governance/team_policy.md` | Team mode usage rules |
| `docs/governance/zeus_packet_discipline.md` | Packet discipline — closure, pre/post-closeout, waivers |
| `docs/governance/zeus_micro_event_logging.md` | Micro-event logging format and rules |
| `docs/governance/zeus_autonomy_gates.md` | Post-P0.5 autonomy rule, team mode entry |
| `docs/governance/zeus_openclaw_venus_delivery_boundary.md` | Zeus ↔ Venus ↔ OpenClaw boundary law |
| `docs/governance/zeus_top_tier_decision_register.md` | Auditable register for irreversible choices |

### Live documents (docs root)

| Path | Purpose |
|------|---------|
| `docs/zeus_FINAL_spec.md` | Target-state spec (P9-P11, endgame clause) |
| `docs/DATA_IMPROVEMENT_PLAN.md` | Data-improve branch work plan |
| `docs/known_gaps.md` | Active operational gap register |

### Control, strategy, planning

| Path | Purpose |
|------|---------|
| `docs/control/current_state.md` | Current active work packet pointer |
| `docs/strategy/data_inventory.md` | Current data source status |
| `docs/strategy/data_strategy.md` | Data utilization strategy |
| `docs/strategy/unused_data_inventory.md` | Unused data opportunities |
| `docs/work_packets/` | Current live work packet(s) |
| `docs/plans/` | Execution plans |

### Archives (NEVER active authority)

`docs/archives/**` — Historical handoffs, audits, findings, sessions, specs, work packets, old architecture, old governance, overlay packages, reality crisis docs.

Archive subdirectories: `architecture/`, `artifacts/`, `audits/`, `control/`, `designs/`, `findings/`, `governance/`, `handoffs/`, `investigations/`, `math/`, `memory/`, `migration/`, `overlay_packages/`, `plans/`, `reality_crisis/`, `reference/`, `reports/`, `research/`, `results/`, `rollout/`, `sessions/`, `specs/`, `traces/`, `work_packets/`.

Do not treat archived files as live control surfaces unless a work packet explicitly promotes them.

## `architecture/` — Machine-Checkable Authority

| File | Purpose |
|------|---------|
| `kernel_manifest.yaml` | Kernel file ownership and protection rules |
| `invariants.yaml` | 10 invariant definitions |
| `zones.yaml` | Zone definitions with import rules |
| `negative_constraints.yaml` | 10 negative constraint definitions |

## `config/` — Runtime Parameters

| File | Purpose |
|------|---------|
| `settings.json` | All runtime parameters (single source of truth) |
| `cities.json` | 16 cities with coordinates, stations, peak hours, units |

## `state/` — Persistent State (gitignored)

| File/Dir | Purpose |
|----------|---------|
| `zeus-paper.db` / `zeus-live.db` | Mode-specific trade databases |
| `zeus-shared.db` | Shared world-data database |
| `risk_state-*.db` | Mode-specific RiskGuard state |
| `positions-*.json` | Mode-qualified position files |
| `ensemble-log/` | ENS snapshots (append-only) |

## `tests/` — Test Suite

| Path | Purpose |
|------|---------|
| `tests/contracts/` | Spec-owned validation manifests |
| `tests/test_cross_module_invariants.py` | Cross-module invariant tests (break these = code is wrong) |
| `tests/test_pnl_flow_and_audit.py` | P&L data flow chain invariants |

## `scripts/` — One-Time Operations

ETL scripts, migration scripts, healthcheck, baseline experiments. Not part of the runtime.

---

## File Placement Rules (MANDATORY)

| Type | Location | Naming pattern |
|------|----------|---------------|
| Active work packets | `docs/work_packets/` | `<PACKET-ID>.md` |
| Completed work packets | `docs/archives/work_packets/` | same name |
| Progress snapshots | `docs/progress/` | `<topic>_progress.md` |
| Plans | `docs/plans/` | `<topic>_plan.md` |
| Strategy docs | `docs/strategy/` | `<topic>_strategy.md` |
| Architecture specs | `docs/architecture/` | `zeus_<topic>_spec.md` |
| Governance docs | `docs/governance/` | `zeus_<topic>_constitution.md` |
| Reference material | `docs/reference/` | `<topic>.md` |
| Generated reports | `docs/reports/` | `<date>_<topic>.md` |
| Archives | `docs/archives/<type>/` | original name |
| Control surfaces | `docs/control/` | `current_state.md` only |
| Agent micro-logs | `.omx/context/` | `<packet>-worklog.md` |

## Naming Rules (MANDATORY)

- All `.md` files: `lower_snake_case.md`
- Exceptions: `AGENTS.md`, `README.md`
- No generic names: ❌ `plan.md`, `progress.md` → ✅ `<topic>_plan.md`
- No spaces in filenames or directory names
- Date prefixes only for time-bound reports

## What Does NOT Belong Here

- Rainstorm source code → `workspace-venus/rainstorm/`
- TIGGE raw data → `workspace-venus/51 source data/`
- Session prompts, code review docs → `project level docs/archive/`
- OpenClaw central config → `~/.openclaw/openclaw.json`
- Venus agent identity files → `workspace-venus/` root
