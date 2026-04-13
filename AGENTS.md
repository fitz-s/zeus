# Zeus AGENTS

Zeus is a position-managed weather-probability trading runtime on Polymarket.
It converts ECMWF 51-member ensemble forecasts into calibrated probabilities, selects statistically significant edges via FDR control, sizes positions with fractional Kelly, and manages a full lifecycle from entry to settlement.

Your job is to change only what the active work packet allows while protecting kernel law, truth contracts, and zone boundaries.
The machine-checkable invariant set lives in `architecture/invariants.yaml`, and the machine-checkable zone map lives in `architecture/zones.yaml`. If this file ever disagrees with those YAMLs, the YAMLs win.

## 1. How Zeus Works (domain model)

### What Zeus is

Zeus is a **fully automated weather-probability trading runtime** on Polymarket. It runs as a live-only daemon, executing trading cycles every ~30 minutes. Each cycle: fetch forecast data → compute probabilities → find statistical edges → size positions → execute orders → manage lifecycle → report status.

**What it trades**: Polymarket weather markets — binary options on questions like "Will the daily high in Dallas exceed 85°F on April 15?" Zeus trades across ~16 cities, each with multiple temperature bins and two directions (buy_yes / buy_no).

**Where the data comes from**: ECMWF 51-member ensemble forecasts (primary), Weather Underground daily observations (settlement source), Polymarket CLOB (market prices + execution), Open-Meteo (hourly observations for diurnal/solar).

**Where the money flows**: Entry via limit orders on Polymarket CLOB → positions held → exit via triggers or settlement → P&L recorded in DB.

**Key entry points in code**: `src/main.py` (daemon), `src/engine/cycle_runner.py` (cycle orchestrator), `src/engine/evaluator.py` (signal→strategy→sizing pipeline).

### Backtest lanes are derived, not authority

Zeus has two diagnostic backtest lanes:

- `wu_settlement_sweep`: high-volume WU settlement-value scoring. WU `settlement_value` is the only weather outcome truth; stored `settlements.winning_bin` is not used for scoring.
- `trade_history_audit`: real trade-history audit over canonical `position_id` subjects. It compares actual trade outcome/PnL with WU-derived outcome and reports divergences without mutating settlement or trade truth.

Backtest output is written to `state/zeus_backtest.db`, a derived reporting surface with `authority_scope='diagnostic_non_promotion'`. It must never be promoted into `zeus-world.db` or `zeus_trades.db`, and it must not authorize live strategy changes by itself. Existing `replay_results` remains compatibility storage for legacy `audit/counterfactual/walk_forward` replay modes only.

### How to use backtest without poisoning decisions

Backtest is Zeus's main mathematical weapon. Treat it as an evidence system with hard eligibility gates, not as a scoreboard. A wrong backtest result is worse than no backtest result because it pushes model, calibration, and strategy work in the wrong direction.

Use the lanes in this order:

1. **Forecast skill first**:
   `python scripts/run_replay.py --mode wu_settlement_sweep --start YYYY-MM-DD --end YYYY-MM-DD`
   This answers whether the weather probability chain is coherent against WU `settlement_value`. It does not answer whether the strategy made money.
2. **Replay fidelity next**:
   `python scripts/audit_replay_fidelity.py`
   This answers whether enough point-in-time forecast references, parseable bins, compatible vectors, and market-price links exist for deeper replay.
3. **Trading economics only after price linkage**:
   `python scripts/run_replay.py --mode audit --allow-snapshot-only-reference --start YYYY-MM-DD --end YYYY-MM-DD`
   Interpret PnL only when the report says market price linkage exists. If PnL prints `N/A`, do not coerce it to zero and do not infer profitability.
4. **Real trade audit separately**:
   `python scripts/run_replay.py --mode trade_history_audit --start YYYY-MM-DD --end YYYY-MM-DD`
   This compares canonical `position_id` trade outcomes with WU-derived outcomes. It is a divergence audit, not simulated strategy PnL.

Read `wu_settlement_sweep` as follows:

- `settlement_value` is the only weather truth. Stored `winning_bin` is evidence only and may be wrong or absent.
- `forecast-bin rows` are binary bin rows. They are heavily class-imbalanced; compare threshold accuracy to `majority_baseline`.
- `positive prediction precision`, `Brier`, `log_loss`, and `Brier skill score vs climatology` are more useful than raw accuracy.
- `top-bin` and `top-3` are multiclass-style diagnostics. They are valid only for probability groups that pass integrity checks.
- `probability groups` must be checked before interpreting top-k metrics. If invalid groups exist, inspect reasons such as `duplicate_labels`, `p_sum_not_one`, and `yes_count_not_one` before changing model math.
- A healthy probability group has unique bin labels, total probability mass near 1, and exactly one WU-derived YES bin. If this fails, the issue is data/bin topology before it is strategy math.
- When invalid probability groups exist, prefer `valid-group binary skill` for clean-subset Brier/log-loss and treat all-row Brier/log-loss as contaminated diagnostics until the group integrity defect is fixed.
- If `mean_p_raw_on_actual_yes` is lower than `mean_p_raw_on_actual_no`, assume bin alignment or probability generation is broken until proven otherwise.
- High-probability buckets with low actual rates indicate calibration/bin mapping failure. Do not tune Kelly, FDR, or alpha to compensate for this.

Mandatory backtest hygiene before changing mathematical/statistical logic:

- Run `wu_settlement_sweep` on a recent representative window and save the run id.
- Run `audit_replay_fidelity.py` and confirm there are no temporal violations.
- Verify `probability_group_integrity` before interpreting top-bin/top-3 results.
- Verify PnL is not being reported from synthetic/uniform market prices. `N/A` is the correct result when market prices are missing.
- Check per-city outliers before changing global calibration or strategy thresholds.
- If the report reveals data topology defects, fix data/bin/vector alignment before changing model formulas.

### The probability chain

```
51 ENS members → per-member daily max → Monte Carlo (sensor noise + rounding) → P_raw
P_raw → Extended Platt (A·logit + B·lead_days + C) → P_cal
P_cal + P_market → α-weighted fusion → P_posterior
P_posterior - P_market → Edge (with double-bootstrap CI)
Edges → BH FDR over active tested candidate family → Selected edges
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

Each live cycle can encounter a macro pool on the order of cities × bins × directions, but the active control unit is the tested candidate/market/snapshot family, not one whole-cycle batch. Benjamini-Hochberg is applied over every hypothesis in that active tested family, including hypotheses that fail the positive-edge prefilter. Zeus does not currently claim whole-cycle BH control.

### The truth hierarchy

```
Chain (Polymarket CLOB) > Chronicler (event log) > Portfolio (local cache)
```

Three reconciliation rules:
1. Local + chain match → SYNCED
2. Local exists, NOT on chain → VOID immediately (local state is a hallucination)
3. Chain exists, NOT local → QUARANTINE 48h (unknown asset, forced exit eval)

### Lifecycle states

9 states: `pending_entry → active → day0_window → pending_exit → economically_closed → settled`. Terminal: `voided`, `quarantined`, `admin_closed`. Transitions are enforced by `LEGAL_LIFECYCLE_FOLDS` — illegal transitions raise errors. The lifecycle manager is the ONLY state authority. See `architecture/invariants.yaml`.

### Risk levels change behavior

GREEN = normal. YELLOW = no new entries. ORANGE = no entries, exit at favorable prices. RED = cancel all, exit all immediately. Advisory-only risk is explicitly forbidden — risk outputs must change behavior.

### External boundaries

Zeus operates within the OpenClaw/Venus ecosystem:
- **Venus** = supervisor agent (reads Zeus state via `src/supervisor_api/contracts.py`, writes via `src/control/control_plane.py`)
- **OpenClaw** = workspace orchestrator (manages Zeus + Venus + Rainstorm)
- Zeus exposes typed contracts outward. External tools must not mutate repo truth.

For full domain model with worked examples: `docs/reference/zeus_domain_model.md`

## 2. Zone system

The canonical zone map lives in `architecture/zones.yaml`.
Use this file as a navigation aid only. It may summarize packages, but it must not redefine ownership or compete with the YAML.

In particular, treat `src/state` as a mixed navigation cluster when reading docs. File-level ownership there is determined by `architecture/zones.yaml`, not by any blanket package label in this guide.

## 3. Invariants (break one = rejected change)

The canonical invariant definitions live in `architecture/invariants.yaml`.
Use this file as a reminder only; do not treat any prose summary here as a competing source of truth or a place to rename IDs.

Full invariant definitions: `architecture/invariants.yaml`

## 4. Forbidden moves

- Promote JSON/CSV exports back to canonical truth
- Let math code (K3) write or redefine lifecycle/control semantics (K0/K1)
- Invent governance keys beyond `strategy_key`
- Add strategy fallback defaults when exact attribution exists or should exist
- Assign lifecycle phase strings ad hoc outside `LifecyclePhase` enum
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
- **Packet discipline** (program/packet/slice, closure, pre/post-closeout, capability proof, waivers, market-math requirements, micro-event logging): `docs/authority/zeus_packet_discipline.md`
- **Autonomy gates** (destructive-ops human gate, team mode entry/restrictions, one-packet-at-a-time rule): `docs/authority/zeus_autonomy_gates.md`
- **Change control** (deep packet governance): `docs/authority/zeus_change_control_constitution.md`
- **Current delivery law** (authority order, planning lock, packet routing, completion protocol): `docs/authority/zeus_current_delivery.md`

### Current-phase rule (Phase 1 complete)

Zeus is live-only. Paper mode was decommissioned in Phase 1. Any code, test, field, or doc that presupposes paper as a peer mode is a violation. Three execution contexts exist with strict boundaries:

- **Live** may act (execute orders, mutate canonical DB truth)
- **Backtest** may evaluate (report metrics, compare strategies) but NOT authorize live changes
- **Shadow** may observe (collect instrumentation facts) but NOT gate live execution

Full boundary rules: `docs/authority/zeus_live_backtest_shadow_boundary.md`

Backtest coverage requires more than settlement rows. A WU settlement sample may cover all configured cities, while strategy/PnL replay may cover fewer cities because it also requires decision-time forecast references, vector-compatible `ensemble_snapshots.p_raw_json`, and parseable typed bin labels. Do not interpret WU settlement coverage as strategy replay coverage.

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
| Completed work packets | `docs/archives/<program>/` | same name, grouped by program |
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
| K0/K1 architecture (state, lifecycle, riskguard) | `docs/authority/zeus_current_architecture.md` + `architecture/kernel_manifest.yaml` |
| Governance (current delivery / packet / authority) | `docs/authority/zeus_current_delivery.md` + `docs/operations/current_state.md` |
| Data improvement (`data-improve` branch) | `docs/reference/data_inventory.md` for current data status |
| First time in repo | `docs/reference/repo_overview.md` for technical orientation |
| File/directory structure | `workspace_map.md` (repo root) for placement rules and directory guide |

### Current active work
Check `docs/operations/current_state.md` for the current packet and branch. Check `docs/known_gaps.md` for present-tense runtime blockers. Use `docs/authority/zeus_current_architecture.md` and `zeus_current_delivery.md` for active law. Historical design files live under `docs/archives/`.

## 10. Conditional references (loaded on demand, not by default)

These files contain specialized content and should NOT be read unless your task requires them:

- `docs/authority/zeus_change_control_constitution.md` — Deep packet governance rules (Chinese language)
- `docs/known_gaps.md` — Active operational gap register (when investigating runtime issues)
- `docs/archives/**` — Historical only, never authoritative
