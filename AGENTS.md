# Zeus AGENTS

Zeus is a live-only, position-managed weather-probability trading runtime on
Polymarket. It converts ECMWF ensemble forecasts and Weather Underground
settlement observations into calibrated probabilities, selects statistically
defensible edges, sizes positions, executes orders, manages exits/settlement,
and exposes typed state to Venus/OpenClaw.

This file is the first read for a zero-context agent. Its job is to give the
durable mental model and operating contract, not to be the full manual. Use
`python scripts/topology_doctor.py --navigation --task "<task>" --files <files>`
for the default route; use `digest` when you only need the task map.

## How Zeus Works

Every live cycle is roughly:

`fetch data -> compute probability -> compare market -> select edge -> size -> execute -> monitor -> exit/settle -> report`.

Discovery modes shape that cycle: `opening_hunt` finds fresh markets,
`update_reaction` responds after forecast updates, and `day0_capture` handles
near-settlement observation-heavy decisions. The mode index is
`architecture/runtime_modes.yaml`.

The core probability chain is:

`51 ENS members -> per-member daily max -> sensor/noise + settlement rounding -> P_raw -> Platt calibration -> market_fusion.py Bayesian blend (P_cal ⊗ P_market -> P_posterior) -> edge + bootstrap CI -> FDR -> Kelly sizing`.

The main runtime path is:

- `src/main.py` starts the daemon.
- `src/engine/cycle_runner.py` owns the cycle.
- `src/engine/evaluator.py` turns candidate markets into decisions.
- `src/execution/executor.py` places orders.
- `src/engine/monitor_refresh.py` and `src/execution/exit_triggers.py` monitor positions.
- `src/execution/harvester.py` handles settlement/learning follow-through.

The main truth path is:

`chain/CLOB facts -> canonical DB/events -> projections/status -> derived reports`.

Derived JSON, CSV, backtest DBs, status summaries, strategy trackers, and
archives are never canonical truth unless a typed authority path says so.

## Why The Rules Exist

Zeus is protecting against its own development process: multi-agent edits,
partial context, stale assumptions, and local optimization across boundaries.
The system has repeatedly paid for these classes of errors:

- Weather settlement is discrete, city/unit-specific, and WMO-rounded. A normal
  Python rounding helper can corrupt probability, calibration, and DB truth.
- Backtest is powerful but diagnostic. A contaminated replay result is worse
  than no replay result because it can move live strategy in the wrong direction.
- Risk/control outputs must change behavior. A RED/YELLOW/ORANGE label that is
  merely advisory is not a safety system.
- Calibration and data rebuilds are not trustworthy because rows exist; rows
  must carry provenance, authority, and relationship-level validation.
- Semantic Provenance Guards exist to expose provenance fields to static checks
  at probability/edge seams; `scripts/semantic_linter.py` is the checker.
- Natural-language design intent decays across sessions. Durable intent should
  be encoded as types, tests, contracts, manifests, and lore cards.

When in doubt, load the relevant lore with `topology_doctor digest` rather than
reading archives or guessing from local code shape.

## Authority Order

- System/developer/user instructions outrank this file.
- Machine-checkable authority wins over prose: `architecture/invariants.yaml`,
  `architecture/zones.yaml`, `architecture/negative_constraints.yaml`,
  `architecture/topology.yaml`, `architecture/source_rationale.yaml`,
  `architecture/history_lore.yaml`, and `architecture/code_idioms.yaml`.
- Scoped `AGENTS.md` files govern their directories and children.
- Current packet/branch state lives in `docs/operations/current_state.md`; do
  not encode short-lived package state as permanent root law.
- `zones.yaml` defines zone grammar/package boundaries; `source_rationale.yaml`
  defines file-level roles/hazards/write routes for `src/**`.
- K-zone legend: K0 = kernel/contracts/lifecycle truth, K1 = governance/risk/control, K2 = runtime/execution/operator read models, K3 = math/signal/calibration/strategy, K4 = experimental/ad hoc.
- High-risk zero-context work reads `architecture/self_check/authority_index.md` directly, then follows `architecture/self_check/zero_context_entry.md`.

## Default Navigation

1. Read this file.
2. Read `workspace_map.md` only for directory-level orientation.
3. Run navigation for the task, for example: `python scripts/topology_doctor.py --navigation --task "fix settlement rounding in replay" --files src/engine/replay.py`
4. Read the scoped `AGENTS.md` for the directory you will touch.
5. Read code and targeted tests.

Default-read budget: source/script tasks may need about six pre-code reads
(`AGENTS.md`, `workspace_map.md`, scoped `AGENTS.md`, one machine manifest,
navigation/digest, and one targeted reference). If you need more, justify and narrow.

When delegating to a zero-context subagent, include the `topology_doctor --navigation` command in the task prompt.

## Durable Boundaries

- Live may act. Backtest may evaluate. Shadow is observe-only instrumentation
  and is not paper mode.
- Backtest output is `diagnostic_non_promotion`; it cannot authorize live DB or strategy changes.
- Canonical DB/event truth outranks derived files and reports.
- Harvester/learning paths must preserve decision-time truth; updating old
  decisions with hindsight forecasts violates point-in-time learning.
- Settlement values must flow through `SettlementSemantics`.
- WU settlement rounding is WMO asymmetric half-up: `floor(x + 0.5)`, not banker rounding.
- Bin contract kind (`point`, `finite_range`, `open_shoulder`) is mandatory for
  settlement, calibration, uncertainty, and edge math.
- Risk/control outputs must change behavior; advisory-only risk labels are not safety.
- Current FDR law controls the active tested candidate/market/snapshot family.
  Whole-cycle BH is not claimed without a strategy-math packet and tests.
- Lifecycle transitions belong to lifecycle authority; do not invent phase strings.
- `strategy_key` is the governance identity; do not create a competing key.
- Venus/OpenClaw are external boundary surfaces. Zeus exposes typed contracts outward.

Representative lore cards: `WMO_ROUNDING_BANKER_FAILURE`,
`DIAGNOSTIC_BACKTEST_NON_PROMOTION`,
`VERIFIED_AUTHORITY_IS_CONTRACT_NOT_STAMP`,
`CANONICAL_DB_TRUTH_OUTRANKS_JSON_FALLBACK`,
`DATA_REBUILD_LIVE_MATH_CERTIFICATION_BLOCKED`.

## Planning Lock

Stop and plan before touching:

- `architecture/**`
- `docs/authority/**`
- `.github/workflows/**`
- `src/state/**` truth ownership, schema, projection, or lifecycle write paths
- `src/control/**`
- `src/supervisor_api/**`
- cross-zone changes
- more than 4 changed files
- anything described as canonical truth, lifecycle, governance, control, or DB authority

Machine check: `python scripts/topology_doctor.py --planning-lock --changed-files <files...> --plan-evidence <plan file>`.

## Change Classification

- **Math**: stays inside existing semantic contracts.
- **Architecture**: changes canonical read/write paths, lifecycle grammar, truth ownership, DB/schema, point-in-time semantics, or zone boundaries.
- **Governance**: changes manifests, authority docs, constitutions, AGENTS, decision registers, or control-plane semantics.

A math change becomes architecture/governance if it touches lifecycle states,
`strategy_key`, unit semantics, point-in-time snapshots, control-plane behavior,
DB truth contracts, or supervisor contracts.

## Forbidden Moves

- Promote derived reports, exports, replays, or caches into canonical truth.
- Bypass typed contracts for settlement, execution price, alpha, lifecycle, or authority.
- Let math/data code redefine lifecycle, control-plane, or DB truth semantics.
- Add fallback defaults where exact attribution exists or should exist.
- Rewrite broad authority surfaces in one unbounded patch.
- Hide uncertainty under polished prose.

## Work Discipline

Before editing, run planning-lock when relevant, classify the change
(math/architecture/governance), identify the canonical truth surface, and state
the downstream relationship test or gate. If the relationship cannot be tested,
the boundary is not understood.

Before closeout, non-trivial repo-changing work updates a short work record;
check with `python scripts/topology_doctor.py --work-record --changed-files <files> --work-record-path <record>`.

Git safety is summarized by lore card `UNCOMMITTED_AGENT_EDIT_LOSS`: never run
destructive git commands or overwrite others' dirty work without explicit human
approval.

## Mesh Maintenance

Zeus uses a mesh topology: `workspace_map.md` -> scoped `AGENTS.md` -> files,
plus machine manifests.

When adding, renaming, or deleting a file:

1. Update the relevant machine manifest when one owns the registry.
2. Update the scoped `AGENTS.md` when local route/rules or non-manifest registry entries change.
3. Update `workspace_map.md` when directory-level structure or architecture surfaces change.

Unregistered files are invisible to future agents.

Registry route: `src/**` -> `source_rationale.yaml`; `scripts/*` -> `script_manifest.yaml`; `tests/test_*.py` -> `test_topology.yaml`; `config/*` -> `config/AGENTS.md`; `config/reality_contracts/*` -> local `AGENTS.md`; `docs/reference/*` -> `docs/reference/AGENTS.md` + `reference_replacement.yaml`.

During active refactors, subagents report map delta; the owner resolves it at
slice/packet closeout. Machine check: `python scripts/topology_doctor.py --map-maintenance --map-maintenance-mode advisory|precommit|closeout`; omit `--changed-files` to use git status (staged, unstaged, untracked, deleted), pass files only to narrow a mixed workspace.

## Context Budget

The entry map should stay small enough to read, but not so thin that it loses
the Zeus mental model. Run:

`python scripts/topology_doctor.py --context-budget --json`

If the budget warns, prefer moving detail into digest-routed lore, scoped
AGENTS, or reference docs over adding default-read prose.

## What To Read By Task

Use digest first. Then load only relevant scoped sources:

- Source edits: scoped `src/**/AGENTS.md`, `architecture/source_rationale.yaml`, code, targeted tests.
- K0/K1/truth/lifecycle: `docs/authority/zeus_current_architecture.md`, `architecture/kernel_manifest.yaml`, relevant source rationale.
- Cross-module relationship tests: `tests/contracts/spec_validation_manifest.py`
  plus the digest-routed target tests.
- Delivery/governance: `docs/authority/zeus_current_delivery.md`, `docs/operations/current_state.md`.
- K0/K1/schema/governance entry: `architecture/self_check/authority_index.md`, then `architecture/self_check/zero_context_entry.md`.
- Math/data/backtest: digest-routed lore; `docs/reference/zeus_domain_model.md` only when deeper domain context is needed.
- Data rebuild: `architecture/data_rebuild_topology.yaml`; live math remains blocked until certification criteria are proven.
- Historical extraction: `architecture/history_lore.yaml`; archives and progress logs are evidence sources, not default context.

Conditional, not default:

- `docs/known_gaps.md` for active operational blockers.
- `docs/reference/statistical_methodology.md` and `docs/reference/zeus_math_spec.md` for deep math facts.
- `docs/archives/**` only when routed by a specific investigation.
