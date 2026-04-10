# Zeus AGENTS

Zeus is a position-managed weather-probability trading runtime on Polymarket.
It converts ECMWF 51-member ensemble forecasts into calibrated probabilities, selects statistically significant edges via FDR control, sizes positions with fractional Kelly, and manages a full lifecycle from entry to settlement.

Your job is to change only what the active work packet allows while protecting kernel law, truth contracts, and zone boundaries.

## 1. How Zeus Works (domain model)

### The probability chain

```
51 ENS members → per-member daily max → Monte Carlo (sensor noise + rounding) → P_raw
P_raw → Extended Platt (A·logit + B·lead_days + C) → P_cal
P_cal + P_market → Bayesian fusion → P_posterior
P_posterior - P_market → Edge (with double-bootstrap CI)
Edges → BH FDR filter (220 hypotheses) → Selected edges
Selected → Fractional Kelly (dynamic mult) → Position size
```

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
| Governance | Changes manifests, constitutions, AGENTS, decision registers, migrations, control-plane semantics | Any file in `architecture/`, `docs/governance/`, `migrations/` |

A math change BECOMES architecture/governance if it touches: lifecycle states, strategy_key grammar, unit semantics, point-in-time snapshot rules, control-plane behavior, DB truth contracts, or supervisor contracts.

## 6. Planning lock (must stop and plan if touching)

- `architecture/**`
- `docs/governance/**`
- `migrations/**`
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
- **Packet discipline** (program/packet/slice, closure, pre/post-closeout, capability proof, waivers): `docs/governance/zeus_packet_discipline.md`
- **Micro-event logging** (format, when to log, template): `docs/governance/zeus_micro_event_logging.md`
- **Autonomy gates** (post-P0.5 rule, team mode entry, escalation): `docs/governance/zeus_autonomy_gates.md`
- **Team policy** (team mode usage rules): `docs/governance/team_policy.md`
- **Change control** (deep packet governance): `docs/governance/zeus_change_control_constitution.md`
- **Autonomous delivery** (constitution): `docs/governance/zeus_autonomous_delivery_constitution.md`

### External boundary
OpenClaw, Venus, and workspace-level docs are outside repo authority. Zeus exposes typed contracts outward. External tools must not mutate repo truth.

### Write style for agents

Keep edits delta-shaped. Patch authority drift instead of rewriting everything. If you add a new surface, say what it harmonizes, what it supersedes, and why it does not create parallel authority.

## 8. File placement rules

| Type | Location | Naming |
|------|----------|--------|
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

### Naming rules
- All `.md` files: `lower_snake_case.md` (exceptions: `AGENTS.md`, `README.md`)
- No generic names: ❌ `plan.md`, `progress.md` → ✅ `<topic>_plan.md`
- No spaces in filenames or directory names
- Date prefixes only for time-bound reports

## 9. What to read next (zone-keyed)

After this file, read the scoped `AGENTS.md` in the directory you are editing. Then read the code.

If you need deeper context:

| If your work is in... | Also read |
|---|---|
| K3 math/data (signals, calibration, strategy) | `docs/reference/zeus_domain_model.md` for probability chain details |
| K0/K1 architecture (state, lifecycle, riskguard) | `docs/architecture/zeus_durable_architecture_spec.md` + `architecture/kernel_manifest.yaml` |
| Governance (constitutions, packets, authority) | `docs/governance/zeus_autonomous_delivery_constitution.md` + `docs/control/current_state.md` |
| Data improvement (`data-improve` branch) | `docs/DATA_IMPROVEMENT_PLAN.md` + `docs/strategy/data_inventory.md` |
| Target-state / endgame decisions | `docs/zeus_FINAL_spec.md` (Part II: P9-P11, Part III: endgame clause) |
| First time in repo | `docs/reference/repo_overview.md` for technical orientation |
| File/directory structure | `docs/reference/workspace_map.md` for placement rules and directory guide |

### Current active work
Check `docs/control/current_state.md` for the current packet and branch.

## 10. Conditional references (loaded on demand, not by default)

These files contain specialized content and should NOT be read unless your task requires them:

- `docs/governance/zeus_change_control_constitution.md` — Deep packet governance rules (Chinese language)
- `docs/reference/model_routing.md` — Codex/GPT model routing policy (Claude/Gemini agents: skip entirely)
- `docs/governance/team_policy.md` — Team mode usage rules (only when entering team mode)
- `docs/known_gaps.md` — Active operational gap register (when investigating runtime issues)
- `docs/archives/**` — Historical only, never authoritative
