# Zeus Durable Architecture Spec

Version: 2026-04-02 (trimmed 2026-04-10)
Status: Principal present-tense architecture authority
Original: `docs/archives/architecture/zeus_durable_architecture_spec_original.md` (1937 lines)

> **Reading note**: This file is trimmed to reference-weight. What remains: P0 decision records (WHY/WHY NOT), invariants, DB schema, canonical events, phase vocabulary, zone model, DB guarantees, and negative constraints. For P1–P8 full implementation detail (fact layer schemas, migration plan, coding OS, anti-vibe checklist), see `docs/architecture/zeus_p1_p8_implementation_spec.md`. For domain intuition, read `docs/reference/zeus_domain_model.md` first.

---

## §0 Executive verdict

Zeus should proceed with the architecture upgrade, but only in **compressed form**.

The repo has already crossed the threshold where the correct center exists: `Position`, monitor-first `CycleRunner`, decision-time snapshot carry-through, and semantic type boundaries are real runtime assets rather than document claims. The next decisive gap is not signal math but the institutionalization of **single authority**, **single learning spine**, and **single protective spine**. External research from public trading/workflow/event-sourcing systems reaches the same conclusion: the right move is not to make Zeus larger, but to make it **harder, narrower, and more explicit**.

The current repo structure shows the precise constraints that must be solved: `CycleRunner` still performs local close on exit decisions, state writes are split across JSON and DB surfaces, `control_plane` still has command-theater characteristics, `riskguard` is still mostly portfolio-level, `strategy_tracker.json` is shadow persistence, and reconciliation still flattens lifecycle states.

This spec therefore defines:

1. **P0 = bearing-capacity layer**. These are not feature tasks. They are the conditions under which later work becomes true instead of theatrical.
2. **P1+ = productized architecture workstreams**. These implement canonical authority, execution truth, strategy-aware protection, learning facts, lifecycle grammar, and migration.
3. **Atomic coding discipline** for humans and LLMs.
4. **Natural-language-to-code landing system** so that "vibe coding" cannot silently mutate architecture intent into local patches.

If Zeus follows this spec, the system can bear the upgrade. If Zeus instead expands surfaces, taxonomies, and async mirrors, the same upgrade will turn into higher-order self-deception.

---

## §1 Source basis and architectural facts

This spec is grounded in four sources:

### 1.1 Internal architecture judgment
The internal report argues that Zeus has already become meaningfully position-centric and now needs institutionalization around authority, learning, and protection rather than more signal complexity. It explicitly identifies multi-truth surfaces, taxonomy drift, phase-vs-mode tension, and protective-loop lag as the next system bottlenecks.

### 1.2 External architecture validation
External research states that public trading and workflow systems repeatedly converge on the same pattern: explicit execution lifecycle, authoritative append history plus current projection, point-in-time learning, executable risk policy, and bounded finite state machines. It also warns against making the system "larger" rather than "harder".

### 1.3 Current repo reality
Current repo facts that drive this spec:

- `run_cycle()` still closes positions locally on monitor exit decisions instead of expressing exit intent and exit execution lifecycle first.
- `executor.py` only posts **BUY** orders in live mode, even though a durable architecture requires live exit semantics as first-class execution paths.
- `portfolio.py` still describes positions as the source of truth and persists them to `positions.json`, while the cycle also writes `decision_log`, `chronicle`, `strategy_tracker.json`, and `status_summary.json`.
- `control_plane.py` stores runtime control state in process memory and exposes commands that are not all truly enacted.
- `riskguard.py` computes recent portfolio-level metrics from settlement records and `load_portfolio()` but does not yet operate on a true strategy policy substrate.
- `strategy_tracker.py` is a separate persisted tracker and even falls back to `opening_inertia` as a default bucket when attribution is not exact.
- `chain_reconciliation.py` still rewrites multiple states back to `holding`, and quarantine positions are inserted as `direction="unknown"` while still living in normal holding-like state space.
- Cross-module invariant tests already exist and are one of the strongest existing architectural assets.

### 1.4 Data-plane realism
Backfill and daemon logs show substantial ENS fetch gaps and repeated 429 rate-limit failures, which means learning infrastructure must explicitly model **missing**, **stale**, and **unavailable** opportunity cases rather than pretending the observed opportunity universe is complete.

---

## §P0 Bearing-capacity prerequisites — decision records

P0 exists because the repo cannot safely absorb P1+ if treated like ordinary features. These are the foundational decisions that make later work true instead of theatrical.

> **Status**: All P0 items are installed. These records are preserved as decision rationale — the WHY and WHY NOT chains that prevent future agents from re-litigating settled decisions.

### P0.1 Fix execution truth semantics before ledger work

**Decision**: Introduce explicit exit lifecycle semantics before canonical ledger rollout.

**Why**: `CycleRunner` closed positions locally immediately after `evaluate_exit()` returned `should_exit`, while live execution only posted BUY orders. If the system keeps "closing" locally before an exit order lifecycle exists, any future ledger persists false closure semantics. This is the deepest pre-ledger corruption risk.

**Why not the alternatives**:
- ❌ **Keep current close + let chain reconciliation repair later.** Reconciliation can detect mismatch, but cannot represent intent, pending exit, or execution latency truth.
- ❌ **Separate paper/live exit semantics.** External trading frameworks explicitly reward shared semantics. Divergence recreates paper/live split-brain.

**Result**: `ExitIntent` concept at engine/execution boundary. Exit events: `EXIT_INTENT`, `EXIT_ORDER_POSTED`, `EXIT_ORDER_FILLED`, `EXIT_ORDER_VOIDED`, `EXIT_REJECTED`. Monitor exit creates exit intent, not terminal closure.

### P0.2 Freeze and simplify the attribution grammar

**Decision**: Freeze `strategy_key` as the unique governance identity before changing risk or learning logic.

**Why**: `strategy`, `edge_source`, `discovery_mode`, `entry_method`, and mode all behaved like partial attribution surfaces. `strategy_tracker.py` fell back to `opening_inertia` when attribution was imperfect. If governance is built before attribution is frozen, all later strategy-aware logic becomes polluted.

**Why not the alternatives**:
- ❌ **Make `edge_source` canonical.** Closer to provenance than governance.
- ❌ **Keep several keys and "interpret consistently".** Depends on discipline, not architecture.
- ❌ **Rename everything immediately.** Massive rename churn before semantics freeze is noise.

**Result**: `strategy_key` is sole governance key. Four canonical values: `settlement_capture`, `shoulder_sell`, `center_buy`, `opening_inertia`. Other taxonomy fields relegated to metadata. Trades without legal `strategy_key` are rejected or quarantined.

### P0.3 Define the canonical transaction boundary

**Decision**: Canonical lifecycle writes must be **single-DB, single-transaction, synchronous**.

**Why**: Cycle writes were split across `positions.json`, `decision_log`, `strategy_tracker.json`, `status_summary.json`, and DB state. External event-sourcing guidance only helps if history and current state are committed together. At SQLite/single-node scale, async projection is premature and dangerous.

**Why not the alternatives**:
- ❌ **Async event projector.** Too much dual-write/eventual-consistency risk for current maturity.
- ❌ **Keep JSON authority + add DB mirrors.** Preserves multi-truth instead of removing it.
- ❌ **Pure event replay without projection.** Too operationally expensive for frequent runtime queries.

**Result**: `position_events` append and `position_current` mutation occur within same SQLite transaction. JSON outputs became projections/exports only.

### P0.4 Make data availability explicit truth

**Decision**: Model upstream data unavailability as a first-class fact before building learning analytics.

**Why**: Logs showed missing ENS backfill coverage and repeated 429 failures. Learning derived only from available cases overestimates system coherence and under-measures opportunity loss.

**Why not the alternatives**:
- ❌ **Treat missing data as incidental logs only.** Hides opportunity attrition and selection bias.
- ❌ **Wait until learning layer later.** Historical analytics become retroactively contaminated.

**Result**: Explicit decision outcomes for `DATA_UNAVAILABLE`, `DATA_STALE`, `RATE_LIMITED`, `CHAIN_UNAVAILABLE`. Diagnostics can separate skipped-for-data from rejected-for-risk from rejected-for-edge.

### P0.5 Install the implementation operating system

**Decision**: Before large refactors, install a coding workflow that converts architecture intent into atomic, testable work packets.

**Why**: The natural-language-to-vibe gap was explicitly identified. The architecture is subtle; code generation systems naturally collapse nuanced intent into local edits, omitted invariants, or surface-level implementations. Without an implementation OS, even a correct spec gets executed as vibe patches.

**Why not the alternatives**:
- ❌ **Rely on long prompts.** Long prompts do not create execution discipline.
- ❌ **Trust individual LLM competence.** The failure mode is not intelligence but translation loss, omission, and shallow local optimization.

**Result**: Work packet template, evidence bundle requirement, atomic patch boundaries, mandatory tests and invariant references, no broad "implement the whole spec" prompts. See `docs/governance/zeus_packet_discipline.md`.

---

## §2 Architectural intent

### The north star
Zeus is not a generalized workflow platform. It is a durable trading runtime with:
- one canonical lifecycle authority,
- one canonical strategy governance key (`strategy_key`),
- one point-in-time learning chain,
- one executable protective policy substrate,
- one bounded lifecycle grammar,
- one operator-facing derived surface,
- one coding discipline that prevents architecture decay.

### Explicit non-goals
- generalized distributed event bus
- asynchronous projector fabric
- new strategy taxonomies or discovery modes
- expanding signal sophistication before governance hardening
- rebuilding research/backtest infrastructure
- UI expansion beyond minimal operator surfaces
- parallel truth surfaces without deletion plan

---

## §3 Architectural invariants

These are spec authority. Any patch that breaks one is invalid unless the spec is explicitly revised.

### INV-01. Exit is not local close.
A monitor decision may produce `EXIT_INTENT`. It may not directly imply economic closure or lifecycle completion.

### INV-02. Settlement is not exit.
Economic exit and final market settlement are separate lifecycle events.

### INV-03. Canonical authority is append-first.
Lifecycle truth = canonical events + deterministic current projection.

### INV-04. `strategy_key` is the sole governance key.
`edge_source`, `discovery_mode`, `entry_method`, and scheduler mode are metadata, not governance.

### INV-05. Risk must change behavior.
If a risk command cannot alter evaluator/sizing/execution outcome, it is theater.

### INV-06. Point-in-time truth beats hindsight truth.
Learning data must preserve what was knowable at decision time.

### INV-07. Lifecycle grammar is finite.
States exist only if they change governance, execution, or reconciliation semantics.

### INV-08. Every write path has one transaction boundary.
Event append and current projection update occur in the same SQLite transaction.

### INV-09. Missing data is first-class truth.
Unavailable/rate-limited upstream data must be represented explicitly in learning and diagnostics.

### INV-10. LLM output is never authority.
Spec, invariants, tests, and evidence are authority. Generated code is only a proposal until validated.

---

## §4 Priority structure

> Full implementation detail for each priority: `docs/architecture/zeus_p1_p8_implementation_spec.md`

- **P0** = bearing-capacity prerequisites (installed) — decision records in §P0 above
- **P1** = canonical lifecycle authority (installed)
- **P2** = execution truth and exit lifecycle (installed)
- **P3** = strategy-aware protective spine (installed)
- **P4** = learning spine and data availability truth (installed)
- **P5** = lifecycle phase engine (installed)
- **P6** = operator/control/observability compression (installed)
- **P7** = migration plan (R1–R7 complete; M4 deferred)
- **P8** = human/LLM coding operating system (installed)

---

## P1.2 Canonical tables

### Table A: `position_events`
Append-only domain events.

| Column | Type | Notes |
|--------|------|-------|
| `event_id` | TEXT PK | |
| `position_id` | TEXT NOT NULL | |
| `event_version` | INTEGER NOT NULL | |
| `sequence_no` | INTEGER NOT NULL | unique per position_id |
| `event_type` | TEXT NOT NULL | |
| `occurred_at` | TEXT NOT NULL | |
| `phase_before` | TEXT | |
| `phase_after` | TEXT | |
| `strategy_key` | TEXT NOT NULL | |
| `decision_id` | TEXT | |
| `snapshot_id` | TEXT | |
| `order_id` | TEXT | |
| `command_id` | TEXT | |
| `caused_by` | TEXT | |
| `idempotency_key` | TEXT | unique when present |
| `venue_status` | TEXT | |
| `source_module` | TEXT NOT NULL | |
| `payload_json` | TEXT NOT NULL | |

### Table B: `position_current`
Current materialized lifecycle projection.

| Column | Type | Notes |
|--------|------|-------|
| `position_id` | TEXT PK | |
| `phase` | TEXT NOT NULL | |
| `trade_id` | TEXT | |
| `market_id` | TEXT | |
| `city` | TEXT | |
| `cluster` | TEXT | |
| `target_date` | TEXT | |
| `bin_label` | TEXT | |
| `direction` | TEXT | |
| `size_usd` | REAL | |
| `shares` | REAL | |
| `cost_basis_usd` | REAL | |
| `entry_price` | REAL | |
| `p_posterior` | REAL | |
| `last_monitor_prob` | REAL | |
| `last_monitor_edge` | REAL | |
| `last_monitor_market_price` | REAL | |
| `decision_snapshot_id` | TEXT | |
| `entry_method` | TEXT | |
| `strategy_key` | TEXT | |
| `edge_source` | TEXT | |
| `discovery_mode` | TEXT | |
| `chain_state` | TEXT | |
| `order_id` | TEXT | |
| `order_status` | TEXT | |
| `updated_at` | TEXT NOT NULL | |

### Table C: `lifecycle_commands` (optional)
| Column | Type |
|--------|------|
| `command_id` | TEXT PK |
| `position_id` | TEXT |
| `command_type` | TEXT |
| `issued_by` | TEXT |
| `issued_at` | TEXT |
| `reason` | TEXT |
| `payload_json` | TEXT |
| `status` | TEXT |

## P1.3 Canonical events

Required minimum event vocabulary:
- `POSITION_OPEN_INTENT`
- `ENTRY_ORDER_POSTED`
- `ENTRY_ORDER_FILLED`
- `ENTRY_ORDER_VOIDED`
- `ENTRY_ORDER_REJECTED`
- `CHAIN_SYNCED`
- `CHAIN_SIZE_CORRECTED`
- `CHAIN_QUARANTINED`
- `MONITOR_REFRESHED`
- `EXIT_INTENT`
- `EXIT_ORDER_POSTED`
- `EXIT_ORDER_FILLED`
- `EXIT_ORDER_VOIDED`
- `EXIT_ORDER_REJECTED`
- `SETTLED`
- `ADMIN_VOIDED`
- `MANUAL_OVERRIDE_APPLIED`

---

## P5.4 Phase vocabulary

Finite set:
- `pending_entry` — order submitted, not yet filled
- `active` — filled, holding position
- `day0_window` — settlement day, special monitoring
- `pending_exit` — exit order submitted, not yet filled
- `economically_closed` — exit filled, awaiting settlement
- `settled` — market resolved, P&L final
- `voided` — cancelled before fill
- `quarantined` — unknown chain asset, isolated from normal flow
- `admin_closed` — manual intervention

### Why this set
- `entered` and `holding` collapsed into `active` (no distinct governance semantics)
- `economically_closed` preserves exit vs settlement separation
- `quarantined` is terminally special lifecycle space

## P5.5 Transition engine

```python
class LifecyclePhase(Enum):
    PENDING_ENTRY = "pending_entry"
    ACTIVE = "active"
    DAY0_WINDOW = "day0_window"
    PENDING_EXIT = "pending_exit"
    ECONOMICALLY_CLOSED = "economically_closed"
    SETTLED = "settled"
    VOIDED = "voided"
    QUARANTINED = "quarantined"
    ADMIN_CLOSED = "admin_closed"

def fold_event(current: CurrentPosition | None, event: LifecycleEvent) -> CurrentPosition: ...
```

No module may directly assign string phase outside this fold logic.

---

## §17 Kernel / zone model

### K0 — Frozen Kernel
Semantic atoms and truth law: unit semantics, probability-space semantics, canonical lifecycle grammar, append+projection write path, point-in-time snapshot semantics, governance-key vocabulary, schema constraints.
**Rules**: no broad edits, no multi-zone refactor without packet, strongest review burden.

### K1 — Governance Layer
Durable control and protection: risk actions, control overrides, strategy policy resolution, risk actuation.
**Rules**: may consume K0, may influence K2/K3 behavior, may not redefine K0 semantics.

### K2 — Runtime Layer
Orchestration, execution lifecycle, reconciliation, projection-backed runtime reads.
**Rules**: may consume K0/K1, may not invent new truth surfaces, may not backdoor mutate canonical truth.

### K3 — Extension Layer
Math, signal, analytics, non-governing domain logic: calibration, signal transforms, market analysis.
**Rules**: may consume K0 contracts/types, may not write canonical lifecycle truth, may not become governance source.

### K4 — Experimental / Disposable
Notebooks, temporary scripts, spike prototypes.
**Rules**: no canonical writes, no policy writes, no import into K0/K1/K2 without promotion packet.

---

## §19 DB / schema semantic guarantees

### Append-only truth
`position_events`: no updates, no deletes. Unique `(position_id, sequence_no)`, unique `idempotency_key` when present.

### Constrained vocabularies
DB must constrain: `strategy_key`, `phase_before`, `phase_after`, `event_type`, `direction`, `unit`.

### Terminality guarantees
Terminal phases may not be silently reopened. Reopen requires explicit events and new phase transitions.

### Transaction guarantee
Canonical append + projection update in one SQLite transaction.

### Authority classification
Derived exports (`positions.json`, `status_summary.json`) cannot be mistaken for authority by write-path code.

### Replay requirement
Projection rebuild from `position_events` must deterministically reproduce `position_current`.

---

## §20 Negative constraints / forbidden moves

### FM-01
No broad prompt may edit K0 and K3 in the same patch unless the packet explicitly justifies cross-zone impact.

### FM-02
No JSON surface may be promoted back to authority (includes `positions.json`, `status_summary.json`).

### FM-03
No governance key may be re-inferred downstream when the evaluator already wrote one.

### FM-04
No lifecycle terminalization through helper shortcuts. Direct local close from orchestration is forbidden.

### FM-05
No fallback from missing decision snapshot to latest available snapshot for learning truth.

### FM-06
No memory-only runtime control state may represent durable policy.

### FM-07
No direct raw string phase assignment outside lifecycle fold/manager/projection path.

### FM-08
No ad hoc unit assumption (`F` default, `C` default) in semantic code paths.

### FM-09
No probability complement shortcuts across architecture boundaries when semantic conversion helpers exist.

### FM-10
No new shadow persistence surface without explicit deletion or demotion plan.

### Enforcement
Every FM must map to at least one of: schema constraint, AST/semgrep rule, import-boundary check, invariant test, replay/parity harness, or packet-review rejection.
