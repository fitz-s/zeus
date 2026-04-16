# Zeus AGENTS

Zeus is a weather-probability trading runtime on
Polymarket. It converts ECMWF ensemble forecasts and Weather Underground
settlement observations into calibrated probabilities, selects statistically
defensible edges, sizes positions, executes orders, manages exits/settlement,
and exposes typed state to OpenClaw.

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

The core probability chain is **dual-track** (see `docs/authority/zeus_dual_track_architecture.md`). The legacy single-track description below is **high track only**:

`51 ENS members -> per-member daily max -> sensor/noise + settlement rounding -> P_raw -> Platt calibration -> market_fusion.py Bayesian blend (P_cal ⊗ P_market -> P_posterior) -> edge + bootstrap CI -> FDR -> Kelly sizing`.

The low track shares local-calendar-day geometry with the high track but has its own physical quantity (`mn2t6_local_calendar_day_min`), its own `observation_field` (`low_temp`), its own Day0 causality law, and its own calibration family. Never describe Zeus as a single-track daily-high system.

The main runtime path is:

- `src/main.py` starts the daemon.
- `src/engine/cycle_runner.py` owns the cycle.
- `src/engine/evaluator.py` turns candidate markets into decisions.
- `src/execution/executor.py` places orders.
- `src/engine/monitor_refresh.py` and `src/execution/exit_triggers.py` monitor positions.
- `src/execution/harvester.py` handles settlement/learning follow-through.

The main truth path is:

`chain/CLOB facts -> canonical DB/events -> projections/status -> derived reports`.

## Market Structure

Zeus trades temperature bins on Polymarket. Bin structure is city/unit-specific:

- **°F cities** (NYC, Chicago): 2°F range bins (e.g. "40-41°F"). `finite_range` contract kind.
- **°C cities** (London, Paris, Seoul, Shanghai, Tokyo): 1°C point bins (e.g. "10°C"). `point` contract kind.
- **Shoulder bins**: open-ended (e.g. "≤39°F" or "≥50°F"). `open_shoulder` contract kind. These stay in raw probability space — no width-normalized density.
- **Settlement**: WMO asymmetric half-up rounding: `floor(x + 0.5)`. Not Python round, not banker rounding.
- **DST**: NYC, Chicago, London, Paris have DST transitions. Tokyo, Seoul, Shanghai do not. Runtime is DST-aware via `ZoneInfo`; historical aggregates may still contain pre-fix data.
- **P_raw scale**: differs by bin width. A 2°F range bin captures ~2× the ensemble members of a 1°C point bin. Platt calibration must account for this.
- **Bin topology invariant**: every market's bin set must cover all integer settlement values exactly once — no gaps, no overlaps.

Derived JSON, CSV, backtest DBs, status summaries, strategy trackers, and
archives are never canonical truth unless a typed authority path says so.

## Dual-Track Forecast Truth

Canonical historical forecast truth is a dual-track system:

- **High track** — `temperature_metric=high`, `physical_quantity=mx2t6_local_calendar_day_max`, `data_version=tigge_mx2t6_local_calendar_day_max_v1`.
- **Low track** — `temperature_metric=low`, `physical_quantity=mn2t6_local_calendar_day_min`, `data_version=tigge_mn2t6_local_calendar_day_min_v1`.

The two tracks share local-calendar-day time geometry and share nothing else. On the same `(city, target_date)` two distinct legitimate temperature truths must be representable. Any table, model, or runtime class that conflates them is structurally incomplete. Full law is in `docs/authority/zeus_dual_track_architecture.md`.

### Snapshot import law

Canonical snapshot rows must carry `temperature_metric`, `physical_quantity`, `observation_field`, `data_version`, `training_allowed`, and `causality_status`. Rows missing a usable `issue_time` may still serve runtime degrade paths but are not canonical training evidence.

### Daily low Day0 law

Daily low Day0 is not a mirror image of daily high Day0. Slots marked `N/A_CAUSAL_DAY_ALREADY_STARTED` must not route through a historical forecast Platt lookup; they go through a nowcast path driven by `low_so_far`, `current_temp`, `hours_remaining`, and remaining forecast hours. Missing `low_so_far` is a clean reject, not a silent degrade to high path.

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
- `MetricIdentity` is mandatory for every temperature-market family; bare `"high"` / `"low"` strings are allowed only at serialization boundaries.
- High and low rows must not mix in calibration pairs, Platt fitting, bin lookup for replay `p_raw`, or settlement rebuild identity.
- DB authority commits must precede derived JSON export writes; on recovery DB wins and JSON is rebuilt.
- RED risk must cancel pending AND sweep active positions; advisory-only RED is forbidden (extends INV-05).
- Chain-truth state is three-valued: `CHAIN_SYNCED`, `CHAIN_EMPTY`, `CHAIN_UNKNOWN`. Void decisions require `CHAIN_EMPTY`.
- Authority-loss must degrade the monitor/exit lane to read-only rather than kill the entire cycle (DT#6).

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
- Open daily-low live trading before Gate F of `zeus_dual_track_architecture.md`.
- Write a daily-low row on the legacy `settlements` (non-v2) table.
- Route a `causality_status != 'OK'` Day0 slot through a historical Platt lookup.
- Mix high and low rows in any single Platt model, bin lookup, or calibration family.
- Call `kelly_size()` with a bare static `entry_price` at a cross-layer seam (INV-13 / DT#5).
- Write a JSON export before the corresponding DB commit returns (DT#1).

## Work Discipline

Before editing, run planning-lock when relevant, classify the change
(math/architecture/governance), identify the canonical truth surface, and state
the downstream relationship test or gate. If the relationship cannot be tested,
the boundary is not understood.

Before closeout, non-trivial repo-changing work updates a short work record;
check with `python scripts/topology_doctor.py --work-record --changed-files <files> --work-record-path <record>`.
High-risk closeout also carries a machine-readable route receipt; check with
`python scripts/topology_doctor.py --change-receipts --changed-files <files> --receipt-path <receipt>`
or use the compiled
`python scripts/topology_doctor.py closeout --changed-files <files> --plan-evidence <plan> --work-record-path <record> --receipt-path <receipt>`.

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

## Oracle Penalty System

Zeus applies per-city oracle error rate penalties to Kelly sizing. The system
detects discrepancies between WU/HKO/NOAA API observations and PM settlement
outcomes.

### Data Flow

```
oracle_snapshot_listener.py (cron 10:00 UTC)
    → raw/oracle_shadow_snapshots/{city}/{date}.json
bridge_oracle_to_calibration.py
    → data/oracle_error_rates.json
oracle_penalty.py
    → Kelly multiplier: OK(1.0) / INCIDENTAL(1.0) / CAUTION(1-rate) / BLACKLIST(0.0)
evaluator.py
    → ORACLE_BLACKLISTED gate + penalty_multiplier on km
```

### Blacklisted Cities

| City | Error Rate | Status |
|------|-----------|--------|
| Shenzhen | 40% | BLACKLIST — no trading |

### Files

| File | Purpose |
|------|---------|
| `src/strategy/oracle_penalty.py` | Load error rates, classify, compute penalty multiplier |
| `data/oracle_error_rates.json` | Per-city error rates with status |
| `scripts/oracle_snapshot_listener.py` | Daily WU/HKO capture at settlement window |
| `scripts/bridge_oracle_to_calibration.py` | Compare snapshots vs PM, update error rates |

## Settlement Truth Pipeline

Harvester now writes `winning_bin` to the `settlements` table when PM markets
settle. Previously only `calibration_pairs` and `position_stage_events` were
written.

```
Gamma API → _fetch_settled_events() → _find_winning_bin()
    → _write_settlement_truth() → settlements table (winning_bin, market_slug, settled_at)
```

`settlement_value` (precise temperature) comes from `observations` table via
`daily_obs_append.py`, not from PM.

## round_fn Injection (SettlementSemantics Polymorphism)

All MC simulation `_settle()` callsites use `round_fn` from
`SettlementSemantics.for_city(city).round_values` instead of hardcoded
`round_wmo_half_up_values`. This ensures HKO cities use `oracle_truncate`
(floor) while 49 WU cities use `wmo_half_up` (floor(x+0.5)).

### Threaded callsites

| File | Constructor | round_fn source |
|------|------------|-----------------|
| `src/engine/evaluator.py` | `Day0Signal(round_fn=...)` | `settlement_semantics.round_values` |
| `src/engine/evaluator.py` | `MarketAnalysis(round_fn=...)` | `settlement_semantics.round_values` |
| `src/engine/replay.py` | `MarketAnalysis(round_fn=...)` | `SettlementSemantics.for_city(city).round_values` |
| `src/engine/monitor_refresh.py` | `round_wmo_half_up_value` | Intentional WMO fallback (directional delta only) |
