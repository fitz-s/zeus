# Zeus P1–P8 Implementation Spec

Version: 2026-04-02 (extracted 2026-04-10)
Source: `docs/authority/zeus_durable_architecture_spec.md` (original 1937 lines)
Status: Reference — P0-P8 installed; preserved for decision rationale and target-state schemas

> **Reading note**: The trimmed architecture spec (`zeus_durable_architecture_spec.md`) contains canonical tables, events, phases, zones, DB guarantees, and negative constraints. This file preserves the full implementation detail: WHY/WHY NOT decisions for each priority, target-state fact layer schemas, migration plan, coding OS, and anti-vibe checklist.

---

## P1 — Canonical lifecycle authority

### P1.1 Decision
Adopt **append-only lifecycle events + deterministic current projection** as the canonical truth model.

**Why**: Minimum architecture that solves audit, replayability, and runtime query speed simultaneously. `chronicle` is append-only but too thin; `positions.json` is fast but over-authoritative; `decision_log` captures why but not canonical lifecycle truth.

**Why not**:
- ❌ **Make `decision_log` canonical.** Cycle-artifact-centric, not lifecycle-centric.
- ❌ **Upgrade `chronicle` only.** Chronology without deterministic current projection leaves runtime state ambiguous.
- ❌ **Keep `positions.json` as authority.** File authority too weak for multi-surface lifecycle governance.

### P1.4–P1.8 Module changes and rules

- `ledger.py` owns canonical writes (`append_event_and_project()`)
- `projection.py` owns fold logic (`fold_event()`)
- JSON surfaces (`positions.json`, `status_summary.json`) reclassified as **export-only** — they may never be read back as authority
- **Transaction rule**: Event append + projection update in same SQLite transaction. No eventual consistency.

Tables and events: see `zeus_durable_architecture_spec.md` §P1.2 and §P1.3.

---

## P2 — Execution truth and exit lifecycle

### P2.1 Lifecycle split
Monitor exit creates `EXIT_INTENT`, not terminal closure. Exit has its own order lifecycle: `EXIT_ORDER_POSTED → EXIT_ORDER_FILLED / EXIT_ORDER_VOIDED / EXIT_ORDER_REJECTED`.

**Why**: Without this split, the system collapses intent and execution into a single moment, making it impossible to represent pending exits, execution latency, or partial fills.

### P2.4 CycleRunner behavior change
After P2, `run_cycle()` monitor exit path becomes:
1. Evaluate exit → `should_exit=True`
2. Create `EXIT_INTENT` event
3. Post exit order → `EXIT_ORDER_POSTED` event
4. Await fill → `EXIT_ORDER_FILLED` event
5. Only then transition to `economically_closed`

Paper mode uses same event semantics but short-circuits to immediate fill.

### P2.5 `portfolio.py` surgery
`load_portfolio()` must stop reading from `positions.json` as authority. It reads from `position_current` (DB projection). `positions.json` becomes a derived export.

### P2.6 Paper/live parity
Paper and live must share event semantics. The only difference is execution latency and fill simulation.

---

## P3 — Strategy-aware protective spine

### P3.1 Decision
Make riskguard strategy-aware with per-strategy policy resolution.

**Why**: Current riskguard operates at portfolio level only. Strategy-specific risk (e.g., gate `center_buy` while allowing `settlement_capture`) requires `strategy_key` as a first-class policy dimension.

**Why not**:
- ❌ **Keep portfolio-level only.** Cannot express strategy-specific risk policy.
- ❌ **Add mode-based risk.** Discovery mode is not a governance key.

### P3.4 New tables

**`strategy_health`**:
| Column | Type | Notes |
|--------|------|-------|
| `strategy_key` | TEXT PK | |
| `active_position_count` | INTEGER | |
| `total_exposure_usd` | REAL | |
| `recent_pnl` | REAL | |
| `recent_win_rate` | REAL | |
| `recent_edge_mean` | REAL | |
| `health_status` | TEXT | green/yellow/red |
| `updated_at` | TEXT | |

**`strategy_policy`**:
| Column | Type | Notes |
|--------|------|-------|
| `policy_id` | TEXT PK | |
| `strategy_key` | TEXT | |
| `policy_type` | TEXT | threshold, gate, limit |
| `parameter_json` | TEXT | |
| `effective_from` | TEXT | |
| `effective_until` | TEXT | |
| `is_active` | BOOLEAN | |

**`risk_actions`**:
| Column | Type | Notes |
|--------|------|-------|
| `action_id` | TEXT PK | |
| `strategy_key` | TEXT | |
| `action_type` | TEXT | gate, ungate, tighten, loosen |
| `reason` | TEXT | |
| `issued_by` | TEXT | |
| `issued_at` | TEXT | |
| `effective_until` | TEXT | |
| `is_active` | BOOLEAN | |

**`control_overrides`**:
| Column | Type | Notes |
|--------|------|-------|
| `override_id` | TEXT PK | |
| `scope_type` | TEXT | global, strategy, position |
| `scope_key` | TEXT | |
| `override_type` | TEXT | |
| `parameter_json` | TEXT | |
| `issued_by` | TEXT | |
| `issued_at` | TEXT | |
| `effective_until` | TEXT | |
| `is_active` | BOOLEAN | |
| `reason` | TEXT | |

### P3.5 Policy resolution
Evaluator calls `resolve_strategy_policy(strategy_key)` before final decision. Resolution order:
1. Active manual overrides (most recent first)
2. Active risk actions for this strategy
3. Base strategy policy

### P3.6–P3.7 Evaluator + RiskGuard changes
- Evaluator receives resolved policy as input, not hardcoded thresholds
- RiskGuard computes per-strategy health, emits risk actions to `risk_actions` table
- Evaluator applies policy; RiskGuard does not directly block trades

---

## P4 — Learning spine and data availability truth

### P4.1 Decision
Learning is represented as three derived fact layers plus explicit data-availability facts.

**Why**: Opportunity evaluation, execution quality, and final outcome are different analytical bases. Folding them into one tracker or one JSON artifact destroys diagnostic precision.

**Why not**:
- ❌ **Keep `strategy_tracker.json` and enrich it.** Already shadow persistence and wrong as learning substrate.
- ❌ **Parse `decision_log` blobs ad hoc.** Brittle and non-canonical.
- ❌ **Create only outcome table.** Hides where opportunity died.

### P4.4 Fact layer schemas

**`opportunity_fact`** — one row per evaluated candidate-direction attempt:
- `decision_id`, `candidate_id`, `city`, `target_date`, `range_label`, `direction`
- `strategy_key`, `discovery_mode`, `entry_method`
- `snapshot_id`, `p_raw`, `p_cal`, `p_market`, `alpha`, `best_edge`, `ci_width`
- `rejection_stage`, `rejection_reason_json`
- `availability_status` (ok | missing | stale | rate_limited | unavailable)
- `should_trade`, `recorded_at`

**`execution_fact`** — one row per order lifecycle:
- `intent_id`, `position_id`, `decision_id`, `order_role`, `strategy_key`
- `posted_at`, `filled_at`, `voided_at`
- `submitted_price`, `fill_price`, `shares`, `fill_quality`, `latency_seconds`
- `venue_status`, `terminal_exec_status`

**`outcome_fact`** — one row per economically completed position:
- `position_id`, `strategy_key`, `entered_at`, `exited_at`, `settled_at`
- `exit_reason`, `admin_exit_reason`, `decision_snapshot_id`
- `pnl`, `outcome`, `hold_duration_hours`, `monitor_count`, `chain_corrections_count`

**`availability_fact`** — one row per data/infrastructure failure:
- `availability_id`, `scope_type` (cycle | city-target_date | candidate | order | chain)
- `scope_key`, `failure_type` (ens_missing | rate_limited | chain_unavailable | observation_missing)
- `started_at`, `ended_at`, `impact` (skip | degrade | retry | block), `details_json`

### P4.5 Point-in-time requirements
- Snapshot context must resolve from `decision_snapshot_id`
- Later snapshots may never be substituted for learning truth
- Missing decision snapshot → label gap explicitly, never silently fall back to latest

---

## P5 — Lifecycle phase engine

Phase vocabulary and transition engine: see `zeus_durable_architecture_spec.md` §P5.4 and §P5.5.

### P5.6 Quarantine rule
Quarantine positions: may not enter standard monitoring economics, may not participate in strategy performance metrics, may not be treated as `holding` or `active`, require dedicated investigation or forced liquidation semantics.

### P5.7 Day0 rule
`day0_window` becomes authoritative only when lifecycle conditions are met, not because scheduler mode is `day0_capture`. A normal position may enter `day0_window` regardless of its original discovery mode.

---

## P6 — Operator, control, and observability compression

### P6.4 Surface reclassification
| Surface | New role |
|---------|----------|
| `status_summary.json` | Export-only operator report |
| `control_plane.json` | Ingress-only command queue |
| `risk_state.db` | Temporary operator visibility during migration |
| `positions.json` | Export/cache only |
| `strategy_tracker.json` | Delete after parity |
| `chronicle` | Fold into `position_events` or reclassify as audit mirror |

### P6.6 Control plane command model
Commands become ingress only. Processing writes durable rows to `control_overrides` or `lifecycle_commands`, not in-memory `_control_state`.

Command examples: `pause_entries`, `resume_entries`, `gate_strategy`, `ungate_strategy`, `tighten_strategy_threshold`, `set_allocation_multiplier`, `force_status_write`.

Mandatory metadata: `issued_by`, `issued_at`, `reason`, `effective_until`, `precedence`.

---

## P7 — Migration plan

Migration follows **dual-write → parity → cutover → delete**.

**Why not big-bang**: Repo has active scheduler jobs, harvester, riskguard, and live/paper paths. Big-bang creates opaque failure planes.

### Phases

| Phase | What | Rollback |
|-------|------|----------|
| M0 — Schema add | Add new tables, no behavior change, smoke tests | Drop tables |
| M1 — Dual-write | Cycle/harvester/reconciliation write old + new | Disable flag |
| M2 — Parity | Compare JSON vs projection for positions, strategy, status | No cutover until stable |
| M3 — DB-first reads | `load_portfolio()` DB-first, JSON fallback for emergency | Revert read source |
| M4 — Retirement | Delete `strategy_tracker.json`, demote `positions.json`, freeze `chronicle` | Restore from backup |

**Rollback rule**: Every phase has enable flag, disable flag, parity dashboard, rollback command. No merge without documented rollback.

---

## P8 — Human + LLM coding operating system

### P8.1 Problem statement
The systemic failure is not that LLMs make mistakes. The deeper failure chain:
1. Macro architecture intent expressed in natural language
2. Model compresses it into a local implementation guess
3. Omitted invariants never made explicit
4. Code appears plausible
5. Repo gains another advanced-looking subsystem that doesn't serve the principal architecture

This is how correct ideas become wrong code without anyone "obviously" violating the plan.

### P8.2 Design principle
**Natural language is not executable architecture.** It must pass through a deterministic decomposition layer before code generation.

### P8.3 Required workflow
1. **Intent capture** — human writes architecture intent in macro form
2. **Spec compiler** — human or reasoning model converts intent into **work packet**
3. **Atomic patch** — coding model implements only that packet
4. **Evidence bundle** — code + tests + before/after behavior proof
5. **Reviewer merge gate** — invariant preservation and parity check

LLMs allowed at step 3 unless explicitly tasked with step 2.

### P8.4 Work packet template
See `docs/authority/zeus_packet_discipline.md` for the full template and rules.

### P8.5 Hard rules for LLM coding
1. No task may ask an LLM to "implement P1" or "do the ledger refactor" — must be packetized
2. LLM must read every file in `required_reads` before editing
3. Truth-surface changes require one-paragraph authority statement (what becomes more/less authoritative, what invariant is protected)
4. Lifecycle changes require transition table
5. New fields require: canonical meaning, who writes, who reads, classification (metadata | authority | policy | audit)
6. No patch touches more than one category (authority | execution | learning | protection | phase grammar | operator surfaces) unless packet explicitly allows
7. All patches must produce tests or parity outputs before merge
8. Fallback buckets, silent defaults, and "best effort" inference on governance keys are forbidden unless packet authorizes

### P8.6 Anti-vibe checklist
Every patch review must answer:
1. Did this patch shrink or expand truth surfaces?
2. Did this patch add real actuation or only another report/view?
3. Did this patch preserve point-in-time truth?
4. Did this patch reduce or increase attribution ambiguity?
5. Did this patch preserve paper/live semantic parity?
6. Did this patch create any new implicit state transition?
7. Did this patch encode missing-data truth or silently skip it?
8. Did this patch introduce a new shadow persistence surface?

If any answer is unfavorable, the patch is rejected or re-scoped.

### P8.8 Natural-language landing layer
The macro failure: human intent names *what must be true* while coding models act on *what is easiest to change*. Every spec section must be translated into three layers before code:

1. **Truth-layer statement** — what becomes authoritative?
2. **Control-layer statement** — who can change behavior because of it?
3. **Evidence-layer statement** — how will we know this is true in runtime?

Example: "Make risk strategy-aware" must be rewritten as:
- Truth: `strategy_key` is canonical governance key; `risk_actions` stores durable per-strategy policy
- Control: evaluator consumes resolved policy before final decision emission
- Evidence: gating `center_buy` blocks only that strategy, leaves the other three alive

Until that translation exists, the task is not ready for coding.
