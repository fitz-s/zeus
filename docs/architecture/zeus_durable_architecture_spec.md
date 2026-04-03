# Zeus Durable Architecture Spec

Version: 2026-04-02  
Status: Principal-architecture implementation spec  
Audience: human lead, Codex/GPT/Claude class coding agents, reviewers, Venus/OpenClaw operators

---

## 0. Executive verdict

Zeus should proceed with the architecture upgrade, but only in **compressed form**.

The repo has already crossed the threshold where the correct center exists: `Position`, monitor-first `CycleRunner`, decision-time snapshot carry-through, and semantic type boundaries are real runtime assets rather than document claims. The uploaded internal architecture analysis is therefore directionally correct: the next decisive gap is not signal math but the institutionalization of **single authority**, **single learning spine**, and **single protective spine**. The uploaded external research summary reaches the same conclusion from public trading/workflow/event-sourcing systems: the right move is not to make Zeus larger, but to make it **harder, narrower, and more explicit**. The current repo structure also shows the precise constraints that must be solved first: `CycleRunner` still performs local close on exit decisions, state writes are split across JSON and DB surfaces, `control_plane` still has command-theater characteristics, `riskguard` is still mostly portfolio-level, `strategy_tracker.json` is shadow persistence, and reconciliation still flattens lifecycle states. 

This spec therefore defines:

1. **P0 = bearing-capacity layer**. These are not feature tasks. They are the conditions under which later work becomes true instead of theatrical.
2. **P1+ = productized architecture workstreams**. These implement canonical authority, execution truth, strategy-aware protection, learning facts, lifecycle grammar, and migration.
3. **Atomic coding discipline** for humans and LLMs.
4. **Natural-language-to-code landing system** so that “vibe coding” cannot silently mutate architecture intent into local patches.

If Zeus follows this spec, the system can bear the upgrade. If Zeus instead expands surfaces, taxonomies, and async mirrors, the same upgrade will turn into higher-order self-deception.

---

## 1. Source basis and architectural facts

This spec is grounded in four sources:

### 1.1 Internal architecture judgment
The uploaded internal report argues that Zeus has already become meaningfully position-centric and now needs institutionalization around authority, learning, and protection rather than more signal complexity. It explicitly identifies multi-truth surfaces, taxonomy drift, phase-vs-mode tension, and protective-loop lag as the next system bottlenecks.

### 1.2 External architecture validation
The uploaded external research summary states that public trading and workflow systems repeatedly converge on the same pattern: explicit execution lifecycle, authoritative append history plus current projection, point-in-time learning, executable risk policy, and bounded finite state machines. It also warns against making the system “larger” rather than “harder”.

### 1.3 Current repo reality
Current repo facts that drive this spec:

- `run_cycle()` still closes positions locally on monitor exit decisions instead of expressing exit intent and exit execution lifecycle first.  
- `executor.py` only posts **BUY** orders in live mode, even though a durable architecture requires live exit semantics as first-class execution paths.  
- `portfolio.py` still describes positions as the source of truth and persists them to `positions.json`, while the cycle also writes `decision_log`, `chronicle`, `strategy_tracker.json`, and `status_summary.json`.  
- `control_plane.py` stores runtime control state in process memory and exposes commands that are not all truly enacted.  
- `riskguard.py` computes recent portfolio-level metrics from settlement records and `load_portfolio()` but does not yet operate on a true strategy policy substrate.  
- `strategy_tracker.py` is a separate persisted tracker and even falls back to `opening_inertia` as a default bucket when attribution is not exact.  
- `chain_reconciliation.py` still rewrites multiple states back to `holding`, and quarantine positions are inserted as `direction="unknown"` while still living in normal holding-like state space.  
- `status_summary.py` is still a file-based operator view derived from `positions.json` rather than a projection of canonical authority.  
- The workspace map itself shows multiple persistent surfaces in `state/` rather than a single canonical lifecycle ledger.  
- Cross-module invariant tests already exist and are one of the strongest existing architectural assets.

### 1.4 Data-plane realism
Backfill and daemon logs show substantial ENS fetch gaps and repeated 429 rate-limit failures, which means learning infrastructure must explicitly model **missing**, **stale**, and **unavailable** opportunity cases rather than pretending the observed opportunity universe is complete.

---

## 2. Architectural intent

### 2.1 The north star
Zeus is not evolving into a generalized workflow platform. Zeus is evolving into a **durable, position-governed trading runtime** with:

- one canonical lifecycle authority,
- one canonical strategy governance key,
- one point-in-time learning chain,
- one executable protective policy substrate,
- one bounded lifecycle grammar,
- one operator-facing derived surface,
- one coding discipline that prevents LLMs from turning architecture into whack-a-mole patchwork.

### 2.2 Explicit non-goals
The following are out of scope for this phase:

- generalized distributed event bus,
- asynchronous projector fabric,
- new strategy taxonomies,
- new discovery modes,
- expanding signal sophistication before governance hardening,
- rebuilding the research stack or backtest engine,
- UI expansion beyond minimal operator surfaces,
- keeping parallel truth surfaces “temporarily” without a deletion plan.

---

## 3. Architectural invariants

These invariants are spec authority. Any patch that breaks one is invalid unless the spec is explicitly revised.

### INV-01. Exit is not local close.
A monitor decision may produce `EXIT_INTENT`. It may not directly imply economic closure or lifecycle completion.

### INV-02. Settlement is not exit.
Economic exit and final market settlement are separate lifecycle events.

### INV-03. Canonical authority is append-first.
Lifecycle truth is represented as canonical events plus a deterministic current projection.

### INV-04. `strategy_key` is the sole governance key.
`edge_source`, `discovery_mode`, `entry_method`, and scheduler mode are metadata, not competing governance centers.

### INV-05. Risk must change behavior.
If a risk or control command cannot alter evaluator/sizing/execution outcome, it is theater.

### INV-06. Point-in-time truth beats hindsight truth.
Learning data must preserve what was knowable at decision time, not what is visible later.

### INV-07. Lifecycle grammar is finite.
States exist only if they change governance, execution, or reconciliation semantics.

### INV-08. Every write path has one transaction boundary.
For canonical lifecycle writes, event append and current projection update must occur in the same SQLite transaction.

### INV-09. Missing data is first-class truth.
Unavailable or rate-limited upstream data must be represented explicitly in learning and diagnostics.

### INV-10. LLM output is never authority.
Spec, invariants, tests, and evidence are authority. Generated code is only a proposal until validated.

---

## 4. Priority structure

- **P0** = bearing-capacity prerequisites.
- **P1** = canonical lifecycle authority.
- **P2** = execution truth and exit lifecycle.
- **P3** = strategy-aware protective spine.
- **P4** = learning spine and data availability truth.
- **P5** = lifecycle phase engine.
- **P6** = operator/control/observability compression.
- **P7** = migration, parity, cutover, deletion.
- **P8** = human/LLM coding operating system.

Everything else is subordinate to this ordering.

---

# P0 — Bearing-capacity prerequisites

P0 exists because the repo currently cannot safely absorb P1/P2/P3 if we treat them like ordinary features.

## P0.1 Fix execution truth semantics before ledger work

### Decision
Introduce explicit exit lifecycle semantics before canonical ledger rollout.

### Why
Current `CycleRunner` closes positions locally immediately after `Position.evaluate_exit()` returns `should_exit`, while live execution currently only posts BUY orders. If the system keeps “closing” locally before there is an exit order lifecycle, any future ledger will persist false closure semantics. This is the deepest pre-ledger corruption risk.

### Why not the alternatives
- **Not chosen: keep current close behavior and let chain reconciliation repair it later.** Reconciliation can detect mismatch, but it cannot represent intent, pending exit, or execution latency truth.
- **Not chosen: separate paper/live exit semantics.** External trading frameworks explicitly reward shared research/live execution semantics. Divergence here recreates paper/live split-brain.

### Required changes
1. Add `ExitIntent` concept at engine/execution boundary.
2. Add exit order execution path in `executor.py`.
3. Stop calling `close_position()` from monitor directly.
4. Create explicit events: `EXIT_INTENT`, `EXIT_ORDER_POSTED`, `EXIT_ORDER_FILLED`, `EXIT_ORDER_VOIDED`, `EXIT_REJECTED`.
5. Only convert position to terminal economic phase after exit fill or true settlement.

### Atomic coding tasks
- Add new order-side semantics to `executor.py` (`BUY` and `SELL`, or venue-equivalent native token unwind semantics).
- Add `execute_exit_order(position, ...)`.
- Replace direct `close_position()` path in `run_cycle()` with exit intent + execution call.
- Add tests for paper/live parity of exit lifecycle.

### Acceptance
- Monitor exit creates exit intent, not terminal closure.
- Live mode can represent pending exit without deleting the position.
- Paper mode uses same event semantics but can short-circuit to immediate fill.

---

## P0.2 Freeze and simplify the attribution grammar

### Decision
Freeze `strategy_key` as the unique governance identity before changing risk or learning logic.

### Why
Current repo lets `strategy`, `edge_source`, `discovery_mode`, `entry_method`, and mode all behave like partial attribution surfaces. `strategy_tracker.py` already demonstrates the danger by falling back to `opening_inertia` when attribution is imperfect. If governance is built before attribution is frozen, all later strategy-aware logic becomes polluted.

### Why not the alternatives
- **Not chosen: make `edge_source` canonical.** It is closer to provenance than governance.
- **Not chosen: keep several keys and “interpret consistently”.** That depends on discipline, not architecture.
- **Not chosen: rename everything immediately.** Massive rename churn before semantics freeze is noise.

### Required changes
1. Add `strategy_key` to all future decision/position/event records.
2. Restrict allowed values to the four canonical strategies:
   - `settlement_capture`
   - `shoulder_sell`
   - `center_buy`
   - `opening_inertia`
3. Relegate other taxonomy fields to metadata-only roles.
4. Reject or quarantine trades that cannot be assigned a legal `strategy_key`.

### Atomic coding tasks
- Add enum/constant set for `strategy_key`.
- Update evaluator to emit `strategy_key` directly.
- Stop downstream reclassification from inferring strategy when already set.
- Remove default-bucket fallback behavior from strategy tracking code during cutover phase.

### Acceptance
- Every new trade/position/event has valid `strategy_key`.
- No downstream component invents strategy when the evaluator already assigned it.
- No fallback-to-opening_inertia for malformed attribution.

---

## P0.3 Define the canonical transaction boundary

### Decision
Canonical lifecycle writes must be **single-DB, single-transaction, synchronous**.

### Why
Current cycle writes are split across `positions.json`, `decision_log`, `strategy_tracker.json`, `status_summary.json`, and DB state. External event-sourcing guidance only helps if history and current state are committed together. For Zeus at SQLite/single-node scale, async projection is premature and dangerous.

### Why not the alternatives
- **Not chosen: async event projector.** Too much dual-write/eventual-consistency risk for current maturity.
- **Not chosen: keep JSON authority and only add DB mirrors.** That preserves multi-truth instead of removing it.
- **Not chosen: pure event replay without projection.** Too operationally expensive for frequent runtime queries.

### Required changes
1. `position_events` append and `position_current` mutation occur within same transaction.
2. Derived tables may be updated sync-in-transaction or rebuilt offline, but canonical state cannot depend on eventual consistency.
3. JSON outputs become projections/exports only.

### Acceptance
- There is one authoritative write path for lifecycle state.
- DB crash cannot leave event history updated while current projection remains stale, or vice versa.

---

## P0.4 Make data availability explicit truth

### Decision
Model upstream data unavailability as a first-class fact before building learning analytics.

### Why
Logs show missing ENS backfill coverage and repeated 429 failures. Learning derived only from available cases will overestimate system coherence and under-measure opportunity loss.

### Why not the alternatives
- **Not chosen: treat missing data as incidental logs only.** That hides opportunity attrition and selection bias.
- **Not chosen: wait until learning layer later.** Then historical analytics become retroactively contaminated.

### Required changes
1. Introduce explicit decision outcomes for `DATA_UNAVAILABLE`, `DATA_STALE`, `RATE_LIMITED`, `CHAIN_UNAVAILABLE`, etc.
2. Add them to opportunity facts, not just logs.
3. Ensure diagnostics can separate “no edge” from “no reliable data”.

### Acceptance
- Opportunity reports can distinguish skipped-for-data from rejected-for-risk from rejected-for-edge.

---

## P0.5 Install the implementation operating system

### Decision
Before large refactors, install a coding workflow that converts architecture intent into atomic, testable work packets.

### Why
The user explicitly identified the natural-language-to-vibe gap. Repo reality confirms the risk: the architecture is subtle, while code generation systems naturally collapse nuanced intent into local edits, omitted invariants, or surface-level implementations. Without an implementation OS, even a correct spec will be executed as vibe patches.

### Why not the alternatives
- **Not chosen: rely on long prompts.** Long prompts do not create execution discipline.
- **Not chosen: trust individual LLM competence.** The failure mode is not intelligence but translation loss, omission, and shallow local optimization.

### Required changes
1. Define work packet template.
2. Require evidence bundle with every patch.
3. Enforce atomic patch boundaries.
4. Make tests and invariant references mandatory inputs.
5. Forbid broad “implement the whole spec” prompts to coding agents.

### Acceptance
- Every coding task can be executed independently with clear invariants, touched files, expected outputs, and rollback condition.

---

# P1 — Canonical lifecycle authority

## P1.1 Canonical model

### Decision
Adopt **append-only lifecycle events + deterministic current projection** as the canonical truth model.

### Why
This is the minimum architecture that solves audit, replayability, and runtime query speed simultaneously. Current repo state surfaces are fragmented. `chronicle` is append-only but too thin; `positions.json` is fast but over-authoritative; `decision_log` captures why but not canonical lifecycle truth.

### Why not the alternatives
- **Not chosen: make `decision_log` canonical.** It is cycle-artifact-centric, not lifecycle-centric.
- **Not chosen: upgrade `chronicle` only.** Chronology without deterministic current projection leaves runtime state ambiguous.
- **Not chosen: keep `positions.json` as authority.** File authority is too weak for multi-surface lifecycle governance.

## P1.2 Canonical tables

### Table A: `position_events`
Append-only domain events.

Recommended columns:
- `event_id TEXT PRIMARY KEY`
- `position_id TEXT NOT NULL`
- `event_version INTEGER NOT NULL`
- `sequence_no INTEGER NOT NULL`
- `event_type TEXT NOT NULL`
- `occurred_at TEXT NOT NULL`
- `phase_before TEXT`
- `phase_after TEXT`
- `strategy_key TEXT NOT NULL`
- `decision_id TEXT`
- `snapshot_id TEXT`
- `order_id TEXT`
- `command_id TEXT`
- `caused_by TEXT`
- `idempotency_key TEXT`
- `venue_status TEXT`
- `source_module TEXT NOT NULL`
- `payload_json TEXT NOT NULL`
- unique index on `(position_id, sequence_no)`
- unique index on `(idempotency_key)` when present

### Table B: `position_current`
Current materialized lifecycle projection.

Recommended columns:
- `position_id TEXT PRIMARY KEY`
- `phase TEXT NOT NULL`
- `trade_id TEXT`
- `market_id TEXT`
- `city TEXT`
- `cluster TEXT`
- `target_date TEXT`
- `bin_label TEXT`
- `direction TEXT`
- `size_usd REAL`
- `shares REAL`
- `cost_basis_usd REAL`
- `entry_price REAL`
- `p_posterior REAL`
- `last_monitor_prob REAL`
- `last_monitor_edge REAL`
- `last_monitor_market_price REAL`
- `decision_snapshot_id TEXT`
- `entry_method TEXT`
- `strategy_key TEXT`
- `edge_source TEXT`
- `discovery_mode TEXT`
- `chain_state TEXT`
- `order_id TEXT`
- `order_status TEXT`
- `updated_at TEXT NOT NULL`

### Table C: `lifecycle_commands` (optional but recommended)
Command-side persistence for explicit operator/engine intent.

Recommended columns:
- `command_id TEXT PRIMARY KEY`
- `position_id TEXT`
- `command_type TEXT`
- `issued_by TEXT`
- `issued_at TEXT`
- `reason TEXT`
- `payload_json TEXT`
- `status TEXT`

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

## P1.4 Module changes

### New modules
- `src/state/ledger.py`
- `src/state/projection.py`
- `src/engine/lifecycle_events.py` (or co-located event builder)

### Modified modules
- `src/state/db.py`
- `src/engine/cycle_runner.py`
- `src/execution/harvester.py`
- `src/state/chain_reconciliation.py`
- `src/observability/status_summary.py`
- `src/riskguard/riskguard.py`

## P1.5 Core API surface

```python
# src/state/ledger.py
@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    position_id: str
    event_version: int
    sequence_no: int
    event_type: str
    occurred_at: str
    phase_before: str | None
    phase_after: str | None
    strategy_key: str
    decision_id: str | None = None
    snapshot_id: str | None = None
    order_id: str | None = None
    command_id: str | None = None
    caused_by: str | None = None
    idempotency_key: str | None = None
    venue_status: str | None = None
    source_module: str = ""
    payload: dict = field(default_factory=dict)


def append_event_and_project(conn, event: LifecycleEvent) -> None: ...
def append_many_and_project(conn, events: list[LifecycleEvent]) -> None: ...
def load_position_current(conn, position_id: str) -> sqlite3.Row | None: ...
def load_open_positions(conn) -> list[sqlite3.Row]: ...
def rebuild_projection(conn, position_id: str | None = None) -> None: ...
```

## P1.6 Transaction rule

Canonical transaction skeleton:

```python
def append_event_and_project(conn, event):
    with conn:
        _insert_position_event(conn, event)
        current = _load_current_for_update(conn, event.position_id)
        new_current = fold_event(current, event)
        _upsert_position_current(conn, new_current)
```

The `with conn:` boundary is mandatory for canonical writes.

## P1.7 How JSON surfaces are reclassified

- `positions.json` → export/cache only
- `status_summary.json` → derived operator report only
- `control_plane.json` → command ingress only, not durable state
- `strategy_tracker.json` → transitional compatibility cache only, then deleted

## P1.8 Tests

### Unit
- event append idempotency
- sequence number monotonicity
- projection fold correctness per event type

### Integration
- create trade → entry pending → fill → monitor → exit fill → settlement
- replay from `position_events` reproduces `position_current`

### Failure injection
- mid-transaction failure leaves neither partial projection nor orphaned event
- duplicate idempotency key is ignored/rejected deterministically

---

# P2 — Execution truth and exit lifecycle

## P2.1 Lifecycle split

### Decision
Split entry, active holding, exit, and settlement into separate lifecycle semantics.

### Why
Current repo conflates exit and settlement and locally closes positions before exit execution exists. A durable trading runtime must match venue truth.

### Why not the alternatives
- **Not chosen: keep `close_position()` as the universal terminalizer.** It destroys lifecycle observability.
- **Not chosen: encode everything in status strings alone.** Strings without phase discipline produce drift.

## P2.2 Order model

### New `OrderIntent`
```python
@dataclass
class OrderIntent:
    intent_id: str
    position_id: str | None
    order_role: Literal["entry", "exit"]
    side: Literal["BUY", "SELL"]
    token_id: str
    price: float
    shares: float
    mode: str
    timeout_seconds: int
    strategy_key: str
    decision_id: str | None
    reason: str | None = None
```

### `OrderResult` expansion
Extend current `OrderResult` with:
- `intent_id`
- `order_role`
- `external_order_id`
- `venue_status`
- `idempotency_key`

## P2.3 Execution API

```python
def execute_entry_order(intent: OrderIntent, clob: PolymarketClient) -> OrderResult: ...
def execute_exit_order(intent: OrderIntent, clob: PolymarketClient) -> OrderResult: ...
def reconcile_order_status(intent: OrderIntent, clob: PolymarketClient) -> OrderResult: ...
```

## P2.4 CycleRunner behavior

### Entry path
1. evaluator returns trade decision
2. build entry intent
3. execute entry intent
4. append event(s)
5. if pending, position phase = `pending_entry`
6. if filled, position phase = `active`

### Exit path
1. monitor emits exit decision
2. build exit intent
3. execute exit intent
4. append event(s)
5. if pending, position phase = `pending_exit`
6. if filled, position phase = `active_closed_pending_settlement` **or** terminal economic phase depending on chosen phase vocabulary
7. settlement later emits `SETTLED`

## P2.5 `portfolio.py` surgery

### Decision
Stop letting `close_position()` be the main lifecycle primitive.

### Why
`close_position()` currently both removes state and computes P&L. That is too much semantic power for one helper.

### Replacement functions
```python
def compute_economic_close(position: Position, exit_price: float, exit_reason: str) -> Position: ...
def compute_settlement_close(position: Position, settlement_price: float) -> Position: ...
def mark_admin_void(position: Position, reason: str) -> Position: ...
```

The ledger projection, not mutable in-memory deletion, becomes the terminalizer.

## P2.6 Paper/live parity

### Rule
Paper mode may shortcut fill timing, but it may not shortcut lifecycle semantics.

### Acceptance
A paper exit still emits:
- `EXIT_INTENT`
- `EXIT_ORDER_POSTED` (synthetic immediate-post)
- `EXIT_ORDER_FILLED`

## P2.7 Tests

- live entry pending then fill
- live exit pending then fill
- paper entry immediate fill with same event order
- paper exit immediate fill with same event order
- settlement after economic exit remains distinguishable from exit itself

---

# P3 — Strategy-aware protective spine

## P3.1 Decision
Replace portfolio-only risk daemon + memory-state control plane with a **durable policy substrate** that the evaluator actually consumes.

## P3.2 Why
Current `riskguard.py` computes portfolio-level health and recent settlement metrics; `control_plane.py` stores mutable state in `_control_state`; `set_strategy_gate` is not fully enacted. This means protection can observe but not govern. External architecture consensus strongly supports risk as a behavior-changing stage, not an advisory sidecar.

## P3.3 Why not the alternatives
- **Not chosen: add more RiskGuard metrics first.** More metrics without policy actuation is theater.
- **Not chosen: let control plane rewrite config files.** That obscures provenance, precedence, and expiry.
- **Not chosen: keep per-portfolio stop as main mechanism.** Internal architecture already recognizes strategy-specific degradation as decisive.

## P3.4 New tables

### `strategy_health`
Derived health view or periodically refreshed table.

Recommended fields:
- `strategy_key`
- `as_of`
- `open_exposure_usd`
- `settled_trades_30d`
- `realized_pnl_30d`
- `unrealized_pnl`
- `win_rate_30d`
- `brier_30d`
- `fill_rate_14d`
- `edge_trend_30d`
- `risk_level`
- `execution_decay_flag`
- `edge_compression_flag`

### `risk_actions`
Durable policy outputs from RiskGuard.

Recommended fields:
- `action_id TEXT PRIMARY KEY`
- `strategy_key TEXT NOT NULL`
- `action_type TEXT NOT NULL`  # gate, allocation_multiplier, threshold_multiplier, exit_only
- `value TEXT NOT NULL`
- `issued_at TEXT NOT NULL`
- `effective_until TEXT`
- `reason TEXT NOT NULL`
- `source TEXT NOT NULL`  # riskguard/manual/system
- `precedence INTEGER NOT NULL`
- `status TEXT NOT NULL`

### `control_overrides`
Operator overrides.

Recommended fields:
- `override_id TEXT PRIMARY KEY`
- `target_type TEXT NOT NULL`
- `target_key TEXT NOT NULL`
- `action_type TEXT NOT NULL`
- `value TEXT NOT NULL`
- `issued_by TEXT NOT NULL`
- `issued_at TEXT NOT NULL`
- `effective_until TEXT`
- `reason TEXT NOT NULL`
- `precedence INTEGER NOT NULL`

## P3.5 Policy resolution

Create one policy resolver:

```python
@dataclass(frozen=True)
class StrategyPolicy:
    strategy_key: str
    gated: bool
    allocation_multiplier: float
    threshold_multiplier: float
    exit_only: bool
    sources: list[str]


def resolve_strategy_policy(conn, strategy_key: str, now: datetime) -> StrategyPolicy: ...
```

Resolution order:
1. hard safety kill-switch
2. active manual override
3. active risk action
4. default policy

## P3.6 Evaluator changes

Evaluator must read policy **before** final decision emission.

### Sequence
1. compute edge
2. compute Kelly base size
3. read `StrategyPolicy`
4. if `gated` → `RISK_REJECTED`
5. apply `threshold_multiplier`
6. apply `allocation_multiplier`
7. continue with risk limits and emit decision

This keeps risk in the capital/decision lane rather than polluting signal math.

## P3.7 RiskGuard changes

`RiskGuard.tick()` becomes:

1. compute portfolio health
2. compute per-strategy health
3. generate/expire risk actions
4. persist risk actions
5. persist overall risk state for operator visibility

It no longer merely writes an overall level; it writes executable governance artifacts.

## P3.8 Tests

- gate one strategy without stopping the others
- shrink only one strategy’s allocation multiplier
- manual override precedence over automatic action
- expired override removal restores automatic policy

---

# P4 — Learning spine and data availability truth

## P4.1 Decision
Learning is represented as three derived fact layers plus explicit data-availability facts.

## P4.2 Why
Opportunity evaluation, execution quality, and final outcome are different analytical bases. Folding them into one tracker or one JSON artifact destroys diagnostic precision.

## P4.3 Why not the alternatives
- **Not chosen: keep `strategy_tracker.json` and enrich it.** It is already shadow persistence and wrong as learning substrate.
- **Not chosen: parse `decision_log` blobs ad hoc.** Blob parsing is brittle and non-canonical.
- **Not chosen: create only outcome table.** That hides where opportunity died.

## P4.4 Fact layers

### `opportunity_fact`
One row per evaluated candidate-direction attempt.

Fields:
- `decision_id`
- `candidate_id`
- `city`
- `target_date`
- `range_label`
- `direction`
- `strategy_key`
- `discovery_mode`
- `entry_method`
- `snapshot_id`
- `p_raw`
- `p_cal`
- `p_market`
- `alpha`
- `best_edge`
- `ci_width`
- `rejection_stage`
- `rejection_reason_json`
- `availability_status`  # ok, missing, stale, rate_limited, unavailable
- `should_trade`
- `recorded_at`

### `execution_fact`
One row per order lifecycle.

Fields:
- `intent_id`
- `position_id`
- `decision_id`
- `order_role`
- `strategy_key`
- `posted_at`
- `filled_at`
- `voided_at`
- `submitted_price`
- `fill_price`
- `shares`
- `fill_quality`
- `latency_seconds`
- `venue_status`
- `terminal_exec_status`

### `outcome_fact`
One row per economically completed position.

Fields:
- `position_id`
- `strategy_key`
- `entered_at`
- `exited_at`
- `settled_at`
- `exit_reason`
- `admin_exit_reason`
- `decision_snapshot_id`
- `pnl`
- `outcome`
- `hold_duration_hours`
- `monitor_count`
- `chain_corrections_count`

### `availability_fact`
One row per data or infrastructure failure affecting an opportunity or cycle.

Fields:
- `availability_id`
- `scope_type`  # cycle, city-target_date, candidate, order, chain
- `scope_key`
- `failure_type`  # ens_missing, rate_limited, chain_unavailable, observation_missing
- `started_at`
- `ended_at`
- `impact`  # skip, degrade, retry, block
- `details_json`

## P4.5 Point-in-time requirements

- snapshot context must always resolve from `decision_snapshot_id`
- later snapshots may never be substituted for learning truth
- if decision snapshot is missing, label that gap explicitly instead of silently falling back to latest

## P4.6 Tests

- opportunity rejected due to data unavailability lands in `availability_fact` and `opportunity_fact`
- harvester uses decision-time snapshot, not latest snapshot
- analytics can separate edge insufficiency from data unavailability

---

# P5 — Lifecycle phase engine

## P5.1 Decision
Introduce a bounded authoritative lifecycle phase machine. Do not continue using free-form string mutation.

## P5.2 Why
Current reconciliation rewrites multiple states back to `holding`, and quarantine enters the system as holding-like state despite unknown direction. This proves current phase strings do not hold authority.

## P5.3 Why not the alternatives
- **Not chosen: keep more states and rely on discipline.** The repo already demonstrated phase flattening.
- **Not chosen: let discovery mode continue to imply lifecycle.** Scanner mode and lifecycle are not the same concept.

## P5.4 Phase vocabulary

Preferred finite set:
- `pending_entry`
- `active`
- `day0_window`
- `pending_exit`
- `economically_closed`
- `settled`
- `voided`
- `quarantined`
- `admin_closed`

### Why this set
- `entered` and `holding` are collapsed into `active` because they currently do not create distinct governance semantics.
- `economically_closed` preserves separation between exit and settlement.
- `quarantined` is its own terminally special lifecycle space.

## P5.5 Transition engine

Create:

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

## P5.6 Quarantine rule

Quarantine positions:
- may not enter standard monitoring economics,
- may not participate in strategy performance metrics,
- may not be treated as `holding` or `active`,
- require dedicated investigation or forced liquidation semantics.

## P5.7 Day0 rule

`day0_window` becomes authoritative only when lifecycle conditions are met, not because scheduler mode is `day0_capture`. A normal position may enter `day0_window` regardless of its original discovery mode.

## P5.8 Tests

- `day0_window` not flattened by reconciliation
- `quarantined` never appears in regular active exposure totals
- settlement cannot occur before economic open/close semantics exist

---

# P6 — Operator, control, and observability compression

## P6.1 Decision
Operator surfaces must be derived, durable where necessary, and minimal.

## P6.2 Why
Current repo has a wide surface area: `status_summary.json`, `control_plane.json`, `risk_state.db`, `positions.json`, `strategy_tracker.json`, `decision_log`, `chronicle`. Not all of these have runtime teeth, and several are competing views.

## P6.3 Why not the alternatives
- **Not chosen: build more status/control views.** Surface area without authority is theater.
- **Not chosen: remove operator views entirely.** The operator still needs derived visibility.

## P6.4 Surface definitions

### Keep
- `status_summary.json` as export-only operator report
- `control_plane.json` as ingress-only command queue
- `risk_state.db` temporarily for operator visibility during migration

### Replace or compress
- `positions.json` → export/cache
- `strategy_tracker.json` → delete after parity
- `chronicle` → fold into `position_events` semantics or clearly reclassify as audit mirror

## P6.5 Status summary source

`status_summary.py` must stop reading primary truth from `load_portfolio()` and instead read from `position_current`, `strategy_health`, and `risk_actions`.

## P6.6 Control plane command model

`control_plane.json` commands become ingress only. Processing a command must write durable rows to `control_overrides` or `lifecycle_commands`, not mutate `_control_state` memory.

### Command examples
- `pause_entries`
- `resume_entries`
- `gate_strategy`
- `ungate_strategy`
- `tighten_strategy_threshold`
- `set_allocation_multiplier`
- `force_status_write`

### Mandatory metadata
- `issued_by`
- `issued_at`
- `reason`
- `effective_until`
- `precedence`

## P6.7 Tests

- command restart survival
- status summary parity vs DB projection
- expired manual control override disappears correctly

---

# P7 — Migration plan

Migration must follow **dual-write → parity → cutover → delete**.

## P7.1 Why this migration and not big-bang

### Why
Repo currently has active scheduler jobs, harvester, riskguard, and live/paper operational paths. Big-bang changes would create opaque failure planes.

### Why not the alternatives
- **Not chosen: permanent dual write.** Permanent dual write is permanent dual truth.
- **Not chosen: direct cutover without parity.** Too risky for lifecycle correctness.

## P7.2 Migration phases

### Phase M0 — schema add only
- add new tables
- no behavior change
- smoke tests only

### Phase M1 — canonical dual-write
- cycle runner writes old state and new ledger/projection
- harvester writes old state and new ledger/projection
- reconciliation writes old state and new ledger/projection

### Phase M2 — parity reporting
- compare open positions between JSON and projection
- compare strategy summaries between tracker JSON and derived strategy metrics
- compare operator status between old and new sources
- no cutover until parity is stable across replay and live paper cycles

### Phase M3 — DB-first reads
- `load_portfolio()` becomes DB-first with JSON fallback only for emergency compatibility
- status summary reads DB-first
- riskguard reads DB-first

### Phase M4 — old surface retirement
- delete `strategy_tracker.json`
- demote `positions.json` to export-only
- optionally freeze/remove `chronicle` once event ledger fully subsumes it

## P7.3 Rollback rule

Every migration phase must have:
- enable flag
- disable flag
- parity dashboard/report
- rollback command

No migration phase may be merged without documented rollback behavior.

---

# P8 — Human + LLM coding operating system

This section is mandatory. It exists because the user explicitly identified the failure mode: natural language intent does not reliably land as architecture-preserving code, especially under LLM “vibe coding”.

## P8.1 Problem statement

The systemic failure is not merely that LLMs make mistakes. The deeper failure is:

1. macro architecture intent is expressed in natural language,
2. the model compresses it into a local implementation guess,
3. omitted invariants are never made explicit,
4. code appears plausible,
5. the repo gains another advanced-looking subsystem that does not actually serve the principal architecture.

This is how correct ideas become wrong code without anyone “obviously” violating the plan.

## P8.2 Design principle

**Natural language is not executable architecture.**  
It must pass through a deterministic decomposition layer before code generation.

## P8.3 Required workflow

### Stage A — Intent capture
A human lead writes the architecture intent in macro form.

### Stage B — Spec compiler
A human or reasoning model converts the intent into a **work packet**.

### Stage C — Atomic patch implementation
A coding model implements only that packet.

### Stage D — Evidence bundle
The implementer returns code + tests + before/after behavior proof.

### Stage E — Reviewer merge gate
A reviewer checks invariant preservation and parity results.

LLMs are only allowed at Stage C unless explicitly tasked with Stage B.

## P8.4 Work packet template

Every coding task must be expressed in this form:

```yaml
work_packet_id: P1.3.a
objective: Add position_events schema and transactional append API
why_this_now: Canonical authority cannot exist without append-first lifecycle storage
why_not_other_approach:
  - JSON authority preserves multi-truth surfaces
  - async projection adds premature consistency risk
invariants_touched:
  - INV-03
  - INV-08
files_may_change:
  - src/state/db.py
  - src/state/ledger.py
files_may_not_change:
  - src/engine/evaluator.py
  - src/strategy/*
required_reads:
  - src/state/db.py
  - src/engine/cycle_runner.py
  - tests/test_cross_module_invariants.py
inputs:
  - existing zeus.db schema
outputs:
  - new table definitions
  - append_event_and_project API
atomic_steps:
  - add schema
  - add dataclass
  - add insert helper
  - add projection upsert helper
tests_required:
  - unit test for idempotency
  - integration test for event+projection transaction
acceptance:
  - one transaction writes event and projection
  - duplicate idempotency key does not create second event
evidence_required:
  - pytest output
  - schema diff
  - example inserted event row
rollback:
  - schema additions remain backward compatible
```

## P8.5 Hard rules for LLM coding

### Rule 1
No task may ask an LLM to “implement P1” or “do the ledger refactor”. Tasks must be packetized.

### Rule 2
An LLM must read every file in `required_reads` before editing.

### Rule 3
If a task changes truth surfaces, the patch must begin with a one-paragraph statement of:
- what surface becomes more authoritative,
- what surface becomes less authoritative,
- what invariant is being protected.

### Rule 4
If a task changes lifecycle semantics, the patch must include a transition table.

### Rule 5
If a task adds a field, the patch must specify:
- canonical meaning,
- who writes it,
- who reads it,
- whether it is metadata, authority, policy, or audit.

### Rule 6
No patch may touch more than one of the following categories at once unless the packet explicitly allows it:
- authority
- execution
- learning
- protection
- phase grammar
- operator surfaces

### Rule 7
All LLM code patches must produce tests or parity outputs before merge.

### Rule 8
Any generated fallback bucket, silent default, or “best effort” inference on governance keys is forbidden unless the packet explicitly authorizes it.

## P8.6 Anti-vibe checklist

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

## P8.7 Mandatory evidence bundle

For each merged packet:
- diff summary
- touched files
- invariant list
- test output
- parity output if migration-related
- one concrete row example if DB-related
- one lifecycle example if execution-related
- one rollback note

## P8.8 Natural-language landing layer

The macro failure is that human intent often names *what must be true* while coding models act on *what is easiest to change*. To close that gap, every spec section must be translated into three layers before code:

1. **Truth-layer statement** — what becomes authoritative?
2. **Control-layer statement** — who can change behavior because of it?
3. **Evidence-layer statement** — how will we know this is true in runtime?

Example:

> “Make risk strategy-aware.”

Must be rewritten as:
- Truth layer: `strategy_key` is canonical governance key; `risk_actions` stores durable per-strategy policy.
- Control layer: evaluator consumes resolved policy before final decision emission.
- Evidence layer: gating `center_buy` blocks only that strategy and leaves the other three alive.

Until that translation exists, the task is not ready for coding.

## P8.9 Review roles

### Principal architect
Approves packet boundaries and invariant interpretation.

### Coding agent
Implements the packet only.

### Integration reviewer
Checks runtime evidence and parity.

### Operator reviewer
Checks that control/observability behavior remains usable.

One model may assist multiple roles, but the roles themselves must remain conceptually separate.

---

# 9. Atomic coding standards

## 9.1 Function-level rules

- Prefer pure fold/transform functions for projection and policy resolution.
- Do not hide lifecycle mutation inside large orchestration functions.
- No function may both decide and silently persist without explicit naming.
- Functions that mutate authority must include `append`, `project`, `apply`, or `resolve` in their names.

## 9.2 File-level rules

- `cycle_runner.py` remains orchestration only.
- `evaluator.py` remains signal + decision logic, not persistence coordinator.
- `ledger.py` owns canonical writes.
- `projection.py` owns fold logic.
- `lifecycle_manager.py` owns phase legality.
- `riskguard.py` computes and emits policy, but evaluator applies it.
- `status_summary.py` is read-only/export-only.

## 9.3 Commit rules

Recommended commit prefixes:
- `P0:`
- `P1:`
- `P2:`
- `P3:`
- `P4:`
- `P5:`
- `MIG:`
- `TEST:`
- `OPS:`

Every commit message should include one architecture sentence, e.g.:

`P2: split monitor exit intent from terminal economic close to preserve execution truth`

## 9.4 Patch size rule

Default maximum atomic patch:
- <= 4 files changed for normal packets
- <= 2 authority-bearing files changed for canonical write packets

Larger patches require explicit justification in the packet.

---

# 10. Test strategy

## 10.1 Existing test asset to preserve

`tests/test_cross_module_invariants.py` already encodes valuable cross-module truths. This style must expand, not be bypassed.

## 10.2 New test classes

### A. Authority tests
- event append/projection parity
- replay determinism
- idempotency

### B. Execution tests
- entry pending/fill lifecycle
- exit pending/fill lifecycle
- settlement after exit
- chain reconciliation against partial fills and size corrections

### C. Protection tests
- per-strategy gate
- allocation multiplier
- threshold multiplier
- override precedence and expiry

### D. Learning tests
- no-trade reason capture
- availability fact capture
- point-in-time snapshot preservation
- outcome linkage across entry/exit/settlement

### E. Migration tests
- old/new parity
- DB-first read parity vs JSON export
- tracker-derived summary parity

### F. LLM packet tests
- packet completeness validator
- forbidden broad-task detector (optional but recommended)

## 10.3 Replay suite

Add a deterministic replay suite over recorded cycle artifacts and order/chain states. Purpose:
- verify projection rebuild,
- verify policy resolution,
- verify no side effects during replay,
- verify parity reports.

---

# 11. Rollout order

## P0 sequence
1. P0.2 attribution freeze
2. P0.1 exit semantics split design + packetization
3. P0.3 transaction boundary schema design
4. P0.4 data availability fact design
5. P0.5 implementation OS install

## P1 sequence
1. add schema
2. add append/project API
3. dual-write in cycle runner, harvester, reconciliation
4. projection parity tests

## P2 sequence
1. executor exit path
2. cycle runner exit intent path
3. pending exit handling
4. economic close vs settlement separation

## P3 sequence
1. strategy policy tables
2. policy resolver
3. evaluator consumption
4. riskguard emission
5. manual override precedence

## P4 sequence
1. opportunity facts
2. availability facts
3. execution facts
4. outcome facts
5. analytics smoke queries

## P5 sequence
1. lifecycle phase enum
2. fold legality
3. remove string mutation hot spots
4. quarantine semantics hardening

## P6 sequence
1. status_summary DB-derived
2. control_plane durable override writes
3. strategy_tracker deletion path

## P7 sequence
1. dual-write
2. parity
3. DB-first
4. cutover
5. delete shadow surfaces

---

# 12. Explicit do-not-touch list

Do not spend this phase on:

- rewriting the signal core,
- redesigning semantic type contracts already protecting native/held-side boundaries,
- expanding discovery modes,
- reworking bootstrap/FDR/Kelly unless strictly required by policy insertion points,
- building a richer UI,
- porting large backtest infrastructure,
- adding new strategies,
- converting Zeus into a generalized event/workflow framework.

The system wins this phase by governing itself better, not by looking more advanced.

---

# 13. Minimal spec-owned query examples

These queries are part of the definition of success.

## Q1. Rebuild one position
> Given `position_id`, reconstruct entry, monitor, exit, settlement, and chain corrections in order.

## Q2. Strategy gate effect
> Show that gating `center_buy` blocked only `center_buy` decisions in the next cycle window.

## Q3. Opportunity loss truth
> Count opportunities skipped because of ENS unavailability vs risk rejection vs insufficient edge over last 7 days.

## Q4. Execution decay
> Compare fill rate and fill latency by `strategy_key` over last 14 days.

## Q5. Projection parity
> Compare DB-derived open positions against exported `positions.json` after dual-write.

---

# 14. Final implementation verdict

Zeus is ready for a durable architecture phase, but only if the work starts with **bearing-capacity corrections** rather than feature enthusiasm.

The correct immediate move is not “build the ledger”, “upgrade riskguard”, or “finish analytics” in isolation. The correct immediate move is:

1. freeze attribution,
2. split exit truth from local close,
3. define one transaction boundary,
4. install the implementation operating system,
5. then build canonical authority and the rest on top.

That is the difference between a real system hardening and a new layer of advanced-looking self-deception.

---

# 15. First executable packet set

These are the **first four** packets that should exist immediately after this spec.

## Packet A — P0 attribution freeze
- add `strategy_key`
- evaluator emits it directly
- downstream stops inventing strategy
- tests for invalid/missing attribution rejection

## Packet B — P0 exit semantics RFC patch
- introduce `ExitIntent` and exit event vocabulary
- no runtime behavior change yet except scaffolding
- tests for event model legality

## Packet C — P1 schema addition
- add `position_events`, `position_current`, indexes
- no cutover yet
- add transactional append/project API

## Packet D — P2 cycle runner exit path cutover
- replace direct `close_position()` on monitor exit with exit intent + order execution path
- add pending-exit tests
- preserve paper/live semantic parity

These packets are intentionally narrow. They begin the durable architecture without letting any coding agent “implement the whole thing” via vibes.

---

# 16. Authority stack and document precedence

This section is architecture law. It exists because mature projects fail when several “good” documents compete as partial authorities.

## 16.1 Principal authority stack

### A. Principal architecture authority
`docs/architecture/zeus_durable_architecture_spec.md`

This file owns:
- system shape,
- lifecycle grammar,
- canonical truth model,
- migration order,
- architecture priorities,
- CI/gate expectations,
- what must be true before any large refactor is considered valid.

### B. Change-control authority
`docs/governance/zeus_change_control_constitution.md`

This file owns:
- how humans and coding agents are allowed to modify Zeus,
- packet grammar,
- negative permissions,
- zero-context routing,
- cross-zone change discipline,
- evidence requirements.

### C. Machine-checkable semantic authority
`architecture/kernel_manifest.yaml`  
`architecture/invariants.yaml`  
`architecture/zones.yaml`  
`architecture/negative_constraints.yaml`  
`architecture/maturity_model.yaml`

These files own:
- frozen enums and semantic atoms,
- zone classification,
- rule IDs,
- gate-stage status,
- reviewer routing,
- which files/modules belong to which class of change.

### D. Operator brief / implementation quick reference
`.claude/CLAUDE.md`

This file is not principal architecture authority. It is a short operational brief that must point back to A, B, and C.

### E. Historical / explanatory references
- `docs/architecture/zeus_blueprint_v2.md`
- `docs/reference/zeus_first_principles_rethink.md`
- `docs/progress/zeus_progress.md`

These remain useful, but they are no longer allowed to compete with A, B, or C on system law.

## 16.2 Conflict resolution

If two artifacts disagree, precedence is:

1. machine-checkable semantic authority when a rule is explicitly encoded there
2. principal architecture authority
3. change-control authority
4. operator brief
5. historical/explanatory references
6. generated code
7. LLM explanation

LLM output never outranks the authority stack.

## 16.3 Required workspace update

`.claude/CLAUDE.md` must update its “Design Authority” section to point to:
- principal architecture authority
- change-control authority
- machine manifests
and demote old blueprint docs to historical rationale.

---

# 17. Kernel / outer-ring constitution

This section defines the mature-project zone map. Every file belongs to exactly one primary zone.

## 17.1 Zone model

### K0 — Frozen Kernel
Purpose: semantic atoms and truth law.

Owns:
- unit semantics,
- probability-space semantics,
- canonical lifecycle grammar,
- canonical append + projection write path,
- point-in-time snapshot semantics,
- governance-key vocabulary,
- schema-level lifecycle/event/strategy constraints.

Rules:
- no broad edits,
- no multi-zone refactor without packet,
- no implicit fallback semantics,
- strongest review and evidence burden.

### K1 — Governance Layer
Purpose: durable control and protection.

Owns:
- risk actions,
- control overrides,
- strategy policy resolution,
- risk actuation,
- operator durable commands.

Rules:
- may consume K0,
- may influence K2/K3 behavior,
- may not redefine K0 semantics.

### K2 — Runtime Layer
Purpose: orchestration, execution lifecycle, reconciliation, projection-backed runtime read paths.

Owns:
- cycle orchestration,
- entry/exit order intent generation,
- monitor refresh,
- chain reconciliation,
- canonical projections,
- operator read models.

Rules:
- may consume K0/K1,
- may not invent new truth surfaces,
- may not backdoor mutate canonical truth outside ledger path.

### K3 — Extension Layer
Purpose: math, signal, analytics, non-governing domain logic.

Owns:
- calibration,
- signal transforms,
- market analysis,
- model agreement,
- optional feature modules that do not redefine lifecycle or truth.

Rules:
- may consume K0 contracts/types,
- may not write canonical lifecycle truth,
- may not become a governance source.

### K4 — Experimental / Disposable Layer
Purpose: isolated experiments and one-off diagnostics.

Owns:
- notebooks,
- temporary scripts,
- ad hoc reports,
- spike prototypes.

Rules:
- no canonical writes,
- no policy writes,
- no import path into K0/K1/K2 without explicit promotion packet.

## 17.2 Zone ownership implications

For a mature Zeus, every change request must answer:
- which zone is the highest-sensitivity zone touched?
- what evidence burden follows from that zone?
- which zones are forbidden to be edited in the same packet?

---

# 18. Machine-checkable semantic manifest

## 18.1 Decision

Architecture intent must be restated in machine-checkable manifests so that future coding agents do not rely on approximate prose retrieval.

## 18.2 Required semantic atoms

The following are manifest-owned atoms:

- `unit`: `F | C`
- `direction`: `buy_yes | buy_no | unknown`
- `probability_space`: `yes_side | no_side | held_side | native_side`
- `strategy_key`: `settlement_capture | shoulder_sell | center_buy | opening_inertia`
- `phase`: `pending_entry | active | day0_window | pending_exit | economically_closed | settled | voided | quarantined | admin_closed`
- `event_type`: canonical lifecycle events only
- `truth_surface_kind`: `canonical_event | canonical_projection | derived_export | ingress_command | experimental`

## 18.3 Manifest responsibilities

`architecture/kernel_manifest.yaml` must declare:
- allowed enum values,
- canonical write modules,
- canonical read models,
- explicitly non-authoritative surfaces,
- which atoms are frozen.

`architecture/invariants.yaml` must declare:
- invariant ID,
- invariant statement,
- why it exists,
- how it is enforced,
- which tests/scripts/constraints map to it.

`architecture/zones.yaml` must declare:
- zone membership,
- default packet class,
- forbidden import edges,
- required evidence per zone.

`architecture/negative_constraints.yaml` must declare:
- forbidden moves,
- which checks enforce them,
- gate stage (`immediate`, `warn`, `strict_after_P1`, etc.).

`architecture/maturity_model.yaml` must declare:
- current stage,
- target stage,
- criteria for promotion.

## 18.4 Rule

If a semantic atom is important enough to mention in prose but not important enough to encode in manifest/tests/schema/gates, it is not mature-project law yet.

---

# 19. DB / schema semantic guarantees

## 19.1 Decision

The most important lifecycle and governance semantics must descend into DB/schema constraints so they survive refactors and weak local implementations.

## 19.2 Required guarantees

### Append-only truth
`position_events` must be append-only:
- no updates,
- no deletes,
- unique `(position_id, sequence_no)`,
- unique `idempotency_key` when present.

### Constrained vocabularies
The DB must constrain:
- `strategy_key`,
- `phase_before`,
- `phase_after`,
- `event_type`,
- `direction`,
- `unit` (where applicable).

### Terminality guarantees
Terminal phases must not be silently reopened by ad hoc update logic. If reopen semantics ever exist, they must be introduced as explicit events and explicit new phase transitions.

### Transaction guarantee
Canonical append + projection update happen in one SQLite transaction.

### Authority classification
Derived exports (`positions.json`, `status_summary.json`) cannot be mistaken for authority by write-path code.

## 19.3 Replay requirement

Projection rebuild from `position_events` must deterministically reproduce `position_current` for the same event stream.

## 19.4 Migration rule

Schema changes that touch K0 atoms require:
- schema packet,
- replay/parity evidence,
- manifest update,
- invariant update.

---

# 20. Negative constraints / forbidden moves

This section exists because mature systems are protected as much by what they ban as by what they endorse.

## 20.1 Forbidden moves

### FM-01
No broad prompt may edit K0 and K3 in the same patch unless the packet explicitly justifies cross-zone impact.

### FM-02
No JSON surface may be promoted back to authority.
This includes `positions.json`, `status_summary.json`, and any future convenience file export.

### FM-03
No governance key may be re-inferred downstream when the evaluator or canonical authority already wrote one.

### FM-04
No lifecycle terminalization may happen through helper shortcuts from orchestration code.
Direct local close from orchestration is forbidden.

### FM-05
No fallback from missing decision snapshot to latest available snapshot for learning truth.

### FM-06
No memory-only runtime control state may represent durable policy.

### FM-07
No direct raw string phase assignment outside the lifecycle fold/manager/projection path.

### FM-08
No ad hoc unit assumption (`F` default, `C` default) in semantic code paths.

### FM-09
No probability complement shortcuts across architecture boundaries when semantic conversion helpers/types exist.

### FM-10
No new shadow persistence surface without explicit deletion or demotion plan.

## 20.2 Enforcement

Every forbidden move must map to at least one of:
- schema constraint,
- AST/semgrep rule,
- import-boundary check,
- invariant test,
- replay/parity harness,
- packet-review rejection.

---

# 21. Self-check / zero-context agent protocol

## 21.1 Problem

A zero-context agent is dangerous not because it is weak, but because it may read something “close enough” and then confidently patch the wrong layer.

## 21.2 Mandatory zero-context entry sequence

Any agent with incomplete context must read in this order:

1. `architecture/self_check/zero_context_entry.md`
2. `architecture/self_check/authority_index.md`
3. `architecture/kernel_manifest.yaml`
4. `architecture/invariants.yaml`
5. `architecture/zones.yaml`
6. `docs/architecture/zeus_durable_architecture_spec.md` relevant section
7. only then the touched code files

## 21.3 Required routing questions

Before editing, the agent must answer:
- what truth surface is authoritative here?
- what zone am I touching?
- am I allowed to touch that zone in this packet?
- which invariant IDs are relevant?
- which files are forbidden reads/writes for this packet?
- what evidence must be produced?

## 21.4 Required-reads rule

No packet may be executed until every file listed in `required_reads` has been read.

## 21.5 Historical-doc guard

Historical docs may be read for rationale, but if they disagree with A/B/C authorities, they lose automatically.

---

# 22. Math isolation / framework stability

## 22.1 Decision

Math can evolve; framework law cannot be casually displaced by math changes.

## 22.2 Boundary

A change counts as a **math change** if it only affects:
- signal computation,
- calibration,
- bootstrap behavior,
- alpha/edge estimation,
- market posterior transforms,
- sizing heuristics within already-authorized decision structure.

A change counts as an **architecture change** if it affects:
- lifecycle phases,
- canonical write path,
- authority surfaces,
- strategy governance semantics,
- reconciliation semantics,
- learning fact model,
- protection actuation,
- zero-context routing,
- semantic atoms or constrained vocabularies.

## 22.3 Rule

Math changes may occur in K3 so long as they do not:
- change K0 enums,
- invent new phases,
- mutate canonical state directly,
- bypass point-in-time snapshot semantics,
- create new governance keys,
- create shadow truths.

## 22.4 Required proof for math changes

Math changes that affect live decisions must provide:
- replay/parity against captured snapshots,
- invariant preservation,
- no schema/authority diff,
- no change to lifecycle event stream shape unless explicitly packetized as architecture change.

---

# 23. One-shot completion doctrine

## 23.1 Statement

It is not realistic to expect any single broad prompt to correctly implement Zeus’s entire mature-project end state.

## 23.2 What can be one-shot

One-shot completion can validly target:
- kernel law definition,
- machine manifests,
- gate skeletons,
- packet grammar,
- schema skeleton,
- replay/parity harness scaffolding,
- narrow architecture packets.

## 23.3 What cannot be one-shot

The following must never be requested as one-shot vibe tasks:
- “implement the mature architecture”
- “refactor Zeus to the final form”
- “finish the ledger”
- “make RiskGuard strategy-aware end to end”
- “make the system LLM-safe”

These are multi-packet programs, not patches.

## 23.4 Correct formulation

The correct success criterion is not:
> the model writes the final system in one shot

It is:
> the model cannot violate the kernel while implementing one packet at a time under machine gates.

---

# 24. CI / gate matrix

## 24.1 Immediate gates

These may be enforced immediately or with minimal repo surgery:

1. manifest consistency check
2. negative-constraints presence check
3. semgrep AST checks for obvious forbidden patterns
4. module-boundary import check (equivalent to import-linter)
5. invariant tests
6. packet completeness check for any file under `work_packets/`
7. principal authority presence check

## 24.2 Staged gates

These start as skeleton or warn-only until P1/P2 land:

1. replay/parity harness over canonical ledger
2. projection rebuild equality checks
3. DB schema constraint smoke tests against new canonical tables
4. forbidden file-touch policy in PR packets for K0 edits
5. lifecycle event-sequence legality tests from real event streams

## 24.3 Required CI jobs

At minimum:
- `architecture-manifests`
- `semgrep-zeus`
- `module-boundaries`
- `kernel-invariants`
- `packet-grammar`
- `schema-smoke`
- `replay-parity` (warn-only until cutover)

## 24.4 Merge rule

A packet touching K0 or schema may not merge without:
- manifest updates,
- invariant references,
- schema diff,
- replay/parity evidence or explicit staged waiver,
- named rollback note.

---

# 25. Workspace files required by this spec

The following files are mandatory companion artifacts and are part of the architecture law:

- `docs/governance/zeus_change_control_constitution.md`
- `architecture/kernel_manifest.yaml`
- `architecture/invariants.yaml`
- `architecture/zones.yaml`
- `architecture/negative_constraints.yaml`
- `architecture/maturity_model.yaml`
- `architecture/packet_templates/*.md`
- `architecture/ast_rules/semgrep_zeus.yml`
- `architecture/ast_rules/forbidden_patterns.md`
- `architecture/self_check/authority_index.md`
- `architecture/self_check/zero_context_entry.md`
- `.github/workflows/architecture-gates.yml`
- `migrations/2026_04_02_architecture_kernel.sql`
- `scripts/check_kernel_manifests.py`
- `scripts/check_module_boundaries.py`
- `scripts/check_work_packets.py`
- `scripts/replay_parity.py`
- `tests/test_architecture_contracts.py`

A mature Zeus is not defined only by prose. It is defined by these artifacts plus the runtime they constrain.
