# Zeus AGENTS

Zeus is a position-managed weather-probability trading runtime on Polymarket.
It converts ECMWF 51-member ensemble forecasts into calibrated probabilities, selects statistically significant edges via FDR control, sizes positions with fractional Kelly, and manages a full lifecycle from entry to settlement.

Your job is to change only what the active work packet allows while protecting kernel law, truth contracts, and zone boundaries.

## 1. How Zeus Works (domain model)

### What Zeus is

Zeus is a **fully automated weather-probability trading runtime** on Polymarket. It runs as a daemon (paper or live mode), executing trading cycles every ~30 minutes. Each cycle: fetch forecast data → compute probabilities → find statistical edges → size positions → execute orders → manage lifecycle → report status.

**What it trades**: Polymarket weather markets — binary options on questions like "Will the daily high in Dallas exceed 85°F on April 15?" Zeus trades across ~16 cities, each with multiple temperature bins and two directions (buy_yes / buy_no).

**Where the data comes from**: ECMWF 51-member ensemble forecasts (primary), Weather Underground daily observations (settlement source), Polymarket CLOB (market prices + execution), Open-Meteo (hourly observations for diurnal/solar).

**Where the money flows**: Entry via limit orders on Polymarket CLOB → positions held → exit via triggers or settlement → P&L recorded in DB.

**Key entry points in code**: `src/main.py` (daemon), `src/engine/cycle_runner.py` (cycle orchestrator), `src/engine/evaluator.py` (signal→strategy→sizing pipeline).

### The probability chain

```
51 ENS members → per-member daily max → Monte Carlo (sensor noise + rounding) → P_raw
P_raw → Extended Platt (A·logit + B·lead_days + C) → P_cal
P_cal + P_market → α-weighted fusion → P_posterior
P_posterior - P_market → Edge (with double-bootstrap CI)
Edges → BH FDR filter (220 hypotheses) → Selected edges
Selected → Fractional Kelly (dynamic mult) → Position size
```

**Where each step lives in code**:
- ENS fetch: `src/data/ecmwf_open_data.py` → `src/data/ensemble_client.py`
- Monte Carlo P_raw: `src/signal/ensemble_signal.py`
- Platt calibration: `src/calibration/platt.py` + `src/calibration/manager.py`
- α-weighted fusion: `src/strategy/market_fusion.py`
- Edge + bootstrap CI: `src/strategy/market_analysis.py`
- FDR filter: `src/strategy/fdr_filter.py`
- Kelly sizing: `src/strategy/kelly.py`
- Order execution: `src/execution/executor.py`

### Why settlement is integer

Polymarket weather markets settle on Weather Underground's reported daily high. WU reports whole degrees (°F or °C). A real temperature of 74.45°F rounds to 74°F; 74.50°F rounds to 74°F (banker's rounding). This means probability mass concentrates at bin boundaries in ways that mean-based models miss entirely. Zeus's Monte Carlo explicitly simulates: atmosphere → NWP member → ASOS sensor noise (σ ≈ 0.2-0.5°F) → METAR rounding → WU integer display. The `SettlementSemantics` contract enforces this — every DB write of a settlement value MUST go through `assert_settlement_value()`.

### Why calibration uses temporal decay

Raw ensemble probabilities are systematically biased — overconfident at long lead times, underconfident near settlement. The Extended Platt model includes `B·lead_days` as a direct input feature (not a bucket dimension), which automatically discounts forecast skill as it decays. Without this, the system overtrades stale forecasts. Maturity gates: n<15 → use P_raw directly, 15-50 → strong regularization (C=0.1), 50+ → standard fit.

### Why FDR filtering exists

Each cycle evaluates ~220 simultaneous hypotheses (cities × bins × directions). At α=0.10 without FDR control, random chance produces ~22 spurious "edges." Benjamini-Hochberg controls the false discovery rate across all hypotheses, not just per-test significance.

### The truth hierarchy

```
Chain (Polymarket CLOB) > Chronicler (event log) > Portfolio (local cache)
```

Three reconciliation rules:
1. Local + chain match → SYNCED
2. Local exists, NOT on chain → VOID immediately (local state is a hallucination)
3. Chain exists, NOT local → QUARANTINE 48h (unknown asset, forced exit eval)

### Lifecycle states

9 states: `pending_entry → active → day0_window → pending_exit → economically_closed → settled`. Terminal: `voided`, `quarantined`, `admin_closed`. Transitions are enforced by `LEGAL_LIFECYCLE_FOLDS` — illegal transitions raise errors. The lifecycle manager is the ONLY state authority (INV-01).

### Risk levels change behavior (INV-05)

GREEN = normal. YELLOW = no new entries. ORANGE = no entries, exit at favorable prices. RED = cancel all, exit all immediately. Advisory-only risk is explicitly forbidden — if a risk level doesn't change behavior, it violates INV-05.

### External boundaries

Zeus operates within the OpenClaw/Venus ecosystem:
- **Venus** = supervisor agent (reads Zeus state via `src/supervisor_api/contracts.py`, writes via `src/control/control_plane.py`)
- **OpenClaw** = workspace orchestrator (manages Zeus + Venus + Rainstorm)
- Zeus exposes typed contracts outward. External tools must not mutate repo truth.

For full domain model with worked examples: `docs/reference/zeus_domain_model.md`

## 2. Zone system

Zeus uses 5 zones (K0-K4). You can only import downward. You can only write in your assigned zone.

| Zone | What | Key directories | Planning lock? |
|------|------|-----------------|----------------|
| K0 | Kernel: lifecycle, state schema, canonical truth | `src/state/`, `src/contracts/` | Always |
| K1 | Protective: risk enforcement, control plane | `src/riskguard/`, `src/control/` | Always |
| K2 | Execution: supervisor, CLOB interaction | `src/supervisor_api/`, `src/execution/` | Packet required |
| K3 | Math/Data: signals, calibration, strategy, analysis | `src/signal/`, `src/calibration/`, `src/strategy/`, `src/engine/` | Only if touching lifecycle/governance semantics |
| K4 | Extension: monitoring, reporting, observability | `src/observability/`, `src/analysis/` | No |

Import rule: K4 may import K3, K3 may import K2, etc. Never upward.

Zone definitions with full import rules: `architecture/zones.yaml`

## 3. Invariants (break one = rejected change)

| ID | Rule | WHY |
|----|------|-----|
| INV-01 | Lifecycle is the only state authority | Prevents parallel truth surfaces from diverging |
| INV-02 | strategy_key is the sole governance key | Attribution must trace to exactly one strategy, never defaulted |
| INV-03 | DB is the canonical truth surface | JSON/CSV exports are derived, never promoted back to truth |
| INV-04 | Point-in-time learning only | No future data leakage into training or evaluation |
| INV-05 | Risk levels must change behavior | Advisory-only risk is theater — every level enforces real constraints |
| INV-06 | Settlement semantics are typed contracts | Rounding/precision drift is a fatal error, not a minor bug |
| INV-07 | No zone boundary violations | K3 math code cannot redefine K0 lifecycle semantics |
| INV-08 | Lifecycle grammar is bounded and frozen | No ad-hoc state strings — only `LifecyclePhase` enum values |
| INV-09 | Attribution must be exact, never defaulted | If attribution doesn't exist, the system must fail, not guess |
| INV-10 | Packet discipline for non-trivial changes | Prevents "while here" drift and scope creep |

Full invariant definitions: `architecture/invariants.yaml`

## 4. Forbidden moves

- Promote JSON/CSV exports back to canonical truth (violates INV-03)
- Let math code (K3) write or redefine lifecycle/control semantics (K0/K1) (violates INV-07)
- Invent governance keys beyond `strategy_key` (violates INV-02)
- Add strategy fallback defaults when exact attribution exists or should exist (violates INV-09)
- Assign lifecycle phase strings ad hoc outside `LifecyclePhase` enum (violates INV-08)
- Suppress type errors with `as any`, `@ts-ignore`, or equivalent
- Commit without explicit request
- Rewrite broad authority files in one unbounded patch

Full negative constraint list: `architecture/negative_constraints.yaml`

## 5. Change classification

| Class | Definition | Examples |
|-------|-----------|----------|
| Math | Stays inside existing semantic contracts | Scoring formulas, calibration logic, signal thresholds, feature generation |
| Architecture | Changes canonical write/read paths, lifecycle grammar, truth-surface ownership, zone boundaries | DB schema, state authority, event projection, truth contracts |
| Governance | Changes manifests, constitutions, AGENTS, decision registers, control-plane semantics | Any file in `architecture/`, `docs/authority/` |

A math change BECOMES architecture/governance if it touches: lifecycle states, strategy_key grammar, unit semantics, point-in-time snapshot rules, control-plane behavior, DB truth contracts, or supervisor contracts.

## 6. Planning lock (must stop and plan if touching)

- `architecture/**`
- `docs/authority/**`
- `.github/workflows/**`
- `src/state/**` truth ownership, schema, projection, or lifecycle write paths
- `src/control/**`
- `src/supervisor_api/**`
- Cross-zone edits
- More than 4 files
- Anything described as canonical truth, lifecycle, governance, or control

## 7. Working discipline

### Before editing, answer these questions:
- What zone am I in?
- Which invariants apply?
- Is this math, architecture, or governance?
- What is the canonical truth surface here?
- What files am I allowed to change?

If you cannot answer, stop and plan.

### Translation loss law

Natural language → code translation has systematic information loss. Functions and types survive sessions at 100%. Design philosophy and architecture rationale survive at ~20%. This is not fixable — it is a physical property of attention allocation across context boundaries.

**Consequence**: Every session should encode insights as code structure (types, tests, contracts), not docs. `Bin.unit`, `SettlementSemantics.for_city()`, and `test_celsius_cities_get_celsius_semantics()` are executable forms of design intent — they enforce correctness without being understood. Docs that explain *why* are valuable but fragile; code that *prevents* errors is durable.

**Relationship tests before implementation**: Before writing a new module, write tests for its relationships with existing modules — not "does this function return the right value" but "when this function's output flows into the next function, what properties must hold?" If you cannot express a cross-module relationship as a pytest assertion, you do not yet understand that relationship. Go back and understand it before coding.

### Commit discipline

**Agents must commit after each verified batch of changes.** Uncommitted work is one `git checkout .` away from total loss.

- Commit after completing and verifying a batch of related edits
- Never leave more than ~10 files uncommitted at once
- Never run `git checkout .`, `git restore .`, `git reset --hard`, or `git stash pop` without explicit human approval
- After every edit, verify the edit persisted (grep/read) before proceeding
- If an edit appears lost, investigate before re-applying — another agent may have overwritten it

> **Historical lesson**: A 2026-04-07 session lost multiple edits across 50+ files due to zero commits over 12+ hours of work. This rule is paid for in real loss.

### Evidence before completion
- Changed files listed
- Tests/gates run (or waived with explanation)
- Rollback note
- Unresolved uncertainty stated plainly
- A waived gate is acceptable only when the gate is explicitly staged/advisory or unavailable for a recorded reason — never for convenience

### Governance references (mesh network)

Detailed rules for these topics are extracted to dedicated files:
- **Packet discipline** (program/packet/slice, closure, pre/post-closeout, capability proof, waivers): `docs/authority/zeus_packet_discipline.md`
- **Micro-event logging** (format, when to log, template): `docs/authority/zeus_micro_event_logging.md`
- **Autonomy gates** (post-P0.5 rule, team mode entry, escalation): `docs/authority/zeus_autonomy_gates.md`
- **Team policy** (team mode usage rules): `docs/authority/team_policy.md`
- **Change control** (deep packet governance): `docs/authority/zeus_change_control_constitution.md`
- **Autonomous delivery** (constitution): `docs/authority/zeus_autonomous_delivery_constitution.md`

### External boundary
OpenClaw, Venus, and workspace-level docs are outside repo authority. Zeus exposes typed contracts outward. External tools must not mutate repo truth.

### Write style for agents

Keep edits delta-shaped. Patch authority drift instead of rewriting everything. If you add a new surface, say what it harmonizes, what it supersedes, and why it does not create parallel authority.

### Mesh topology maintenance (MANDATORY)

Zeus uses a mesh topology for agent navigation: `workspace_map.md` (root) → directory-level `AGENTS.md` files → individual files. Every directory has an `AGENTS.md` with a **file registry** listing all files and their purposes.

**When you add, rename, or delete a file, you MUST**:
1. Update the `AGENTS.md` in that file's directory (add/remove from file registry)
2. Update `workspace_map.md` if the change affects directory-level structure
3. If the file is cross-referenced by other `AGENTS.md` files, update those too

This is non-negotiable. An unregistered file is invisible to other agents.

## 8. File placement rules

| Type | Location | Naming |
|------|----------|--------|
| Authority docs (specs, constitutions, boundary law) | `docs/authority/` | `zeus_<topic>.md` |
| Reference material (domain model, data inventory) | `docs/reference/` | `<topic>.md` |
| Operations (control, plans, work packets) | `docs/operations/` | varies |
| Completed work packets | `docs/archives/work_packets/` | same name |
| Archives | `docs/archives/<type>/` | original name |
| Agent micro-logs | `.omx/context/` | `<packet>-worklog.md` |

### Naming rules
- All `.md` files: `lower_snake_case.md` (exceptions: `AGENTS.md`, `README.md`)
- **New files**: Use `task_YYYY-MM-DD_name.md` format — task prefix identifies the program/packet, date is creation date. Example: `datafix_2026-04-10_tigge_backfill_status.md`
- No single-word prefixes: ❌ `data_plan.md` → ✅ `datafix_2026-04-10_improvement_plan.md`
- No generic names: ❌ `plan.md`, `progress.md` → ✅ `<task>_<date>_<topic>.md`
- No spaces in filenames or directory names
- Existing files keep current names (no retroactive renames)
- Date prefixes only for time-bound reports

## 9. What to read next (zone-keyed)

After this file, read `workspace_map.md` (repo root) for the full directory and file topology. Then read the scoped `AGENTS.md` in the directory you are editing. Then read the code.

If you need deeper context:

| If your work is in... | Also read |
|---|---|
| K3 math/data (signals, calibration, strategy) | `docs/reference/zeus_domain_model.md` for probability chain details |
| K0/K1 architecture (state, lifecycle, riskguard) | `docs/authority/zeus_durable_architecture_spec.md` + `architecture/kernel_manifest.yaml` |
| Governance (constitutions, packets, authority) | `docs/authority/zeus_autonomous_delivery_constitution.md` + `docs/operations/current_state.md` |
| Data improvement (`data-improve` branch) | `docs/reference/data_inventory.md` for current data status |
| Target-state / endgame decisions | `docs/authority/target_state_spec.md` (Part II: P9-P11, Part III: endgame clause) |
| First time in repo | `docs/reference/repo_overview.md` for technical orientation |
| File/directory structure | `workspace_map.md` (repo root) for placement rules and directory guide |

### Current active work
Check `docs/operations/current_state.md` for the current packet and branch.

## 10. Conditional references (loaded on demand, not by default)

These files contain specialized content and should NOT be read unless your task requires them:

- `docs/authority/zeus_change_control_constitution.md` — Deep packet governance rules (Chinese language)
- `docs/reference/model_routing.md` — Codex/GPT model routing policy (Claude/Gemini agents: skip entirely)
- `docs/authority/team_policy.md` — Team mode usage rules (only when entering team mode)
- `docs/known_gaps.md` — Active operational gap register (when investigating runtime issues)
- `docs/archives/**` — Historical only, never authoritative
