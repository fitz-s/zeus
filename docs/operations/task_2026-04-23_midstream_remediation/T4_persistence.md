# T4.0 — DecisionEvidence Persistence Design (rev2)

Slice ID: T4.0
Parent: `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md` (Wave 1)
Created: 2026-04-23
Revision: rev2 — integrates surrogate-critic findings (F1-F3 + missed
Option E) from read-only review 2026-04-23; all citations re-verified
via fresh grep 2026-04-23 per memory rule L20. Recommendation flipped
from Option B to Option E.
Status: PROPOSAL — pending critic review by con-nyx

## Problem

T4 (D4 closure — entry/exit epistemic symmetric `DecisionEvidence` in
production) requires that exit authority, at decision time, reads the
prior entry-time `DecisionEvidence` for the same position and invokes
`DecisionEvidence.assert_symmetric_with(exit_evidence)`. Today:

- `DecisionEvidence` (`src/contracts/decision_evidence.py:21-50`) is a
  frozen dataclass with no persistence method.
- `src/engine/evaluator.py` constructs `EdgeDecision` (not `Decision`)
  at multiple return sites (e.g. `src/engine/evaluator.py:753,778,803,
  815,832,842,866,882,901,912`); none write `DecisionEvidence`.
- `src/execution/exit_triggers.py` never reads prior entry evidence;
  `evaluate_exit_triggers` consumes only current-cycle `EdgeContext`.
- A new persistence-and-read path is therefore required before T4.1
  (entry-wire) and T4.2-Phase1 (exit-wire) can land.

## Fix-plan premise correction

The joint fix-plan v2 (`T4.0` row) pins:
> "Persistence-mechanism decision pinned: `decision_log` row keyed on
> `decision_snapshot_id` (option b; no schema migration)."

Grep-verified 2026-04-23: `decision_log` at `src/state/db.py:528-536`
has six columns — `id, mode, started_at, completed_at, artifact_json,
timestamp` — and **no `decision_snapshot_id` column**. `artifact_json`
carries cycle-mode decision-chain summaries (`src/state/decision_chain.py`),
not per-candidate evidence. A schema migration **is** required if
`decision_log` is the target, OR the target must differ. rev2 picks a
different target.

## Requirements

1. **Write at entry**: `evaluate_candidate` must persist a populated
   `DecisionEvidence(evidence_type="entry", statistical_method="bootstrap_ci_bh_fdr",
   sample_size=bootstrap_ci.n_samples, confidence_level=0.10,
   fdr_corrected=True, consecutive_confirmations=1)` keyed by a stable
   coordinate the position carries forward.
2. **Read at exit**: exit authority at
   `src/execution/exit_triggers.py:49,158,218` must retrieve the entry
   evidence for the position in O(1) before constructing exit evidence
   and calling `assert_symmetric_with`.
3. **Append-first discipline preserved** per `src/state/AGENTS.md`:
   canonical writes go through `position_events` append + projection
   fold in one transaction. Evidence persistence must not invent a
   parallel truth surface.
4. **No torn-state window** between writing evidence and writing the
   position event. (rev1 missed this; rev2 addresses.)

## Current transaction topology (rev2 correction)

**rev1 claimed** "trade_decisions INSERT already occurs in the same
transaction as the entry event append". That is false.

`cycle_runtime.py:1115-1140` verbatim reads:

```python
sp_name = f"sp_candidate_{str(d.decision_id).replace('-', '_')}"
conn.execute(f"SAVEPOINT {sp_name}")
try:
    log_trade_entry(conn, pos)
    log_execution_report(conn, pos, result, decision_id=d.decision_id)
    conn.execute(f"RELEASE SAVEPOINT {sp_name}")
except Exception:
    conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
    conn.execute(f"RELEASE SAVEPOINT {sp_name}")
    raise
# Dual-write runs outside the SAVEPOINT guard because it uses
# `with conn:` internally (commits its own sub-transaction).
# Placing it inside would release the SAVEPOINT on commit,
# breaking the ROLLBACK path on subsequent errors.
_dual_write_canonical_entry_if_available(conn, pos, ...)
```

Consequences:

- `log_trade_entry` (which INSERTs into `trade_decisions`) runs inside
  SAVEPOINT `sp_candidate_<decision_id>`.
- `_dual_write_canonical_entry_if_available` (which appends to
  `position_events` via `append_many_and_project`
  `src/state/ledger.py:163,187`) runs **after SAVEPOINT release**, in
  its own `with conn:` sub-transaction.
- The two writes are in **separate transaction boundaries**. A
  process crash between them leaves `trade_decisions` committed
  without the corresponding `position_events` append — already an
  accepted torn-state window for `trade_decisions.epistemic_context_json`
  and `trade_decisions.edge_context_json`.

Any persistence design that lands evidence in `trade_decisions`
inherits this torn-state window. A design that lands evidence via
`position_events` does not (it runs in the canonical append-first path
with the projection fold).

## Options considered (rev2)

### Option A — reuse `trade_decisions.epistemic_context_json` (zero migration)

Serialize `DecisionEvidence` as a nested key inside the existing
`trade_decisions.epistemic_context_json` TEXT column
(`src/state/db.py:374`, ALTER at L796). Join via
`position_current.decision_snapshot_id` (col 18) → `trade_decisions.runtime_trade_id`.

**Pros:** zero migration, reuses existing persistence surface.
**Cons:** overloads an existing JSON column (semantic-overload risk);
inherits the F3 torn-state window; readers must parse and tolerate
legacy rows without the `decision_evidence` key.

### Option B — new `decision_evidence_json TEXT` column on `trade_decisions` (one-column migration)

Add `decision_evidence_json TEXT` via ALTER TABLE pattern used at
`src/state/db.py:796` (the precedent for `epistemic_context_json`).
Read/write via same `runtime_trade_id` join as Option A.

**Pros:** dedicated column, clean separation, NULL on legacy rows is
unambiguous "no evidence".
**Cons:** requires additive schema migration (planning-lock required
per delivery.md §5 for any `src/state/db.py` change); still inherits
the F3 torn-state window because `trade_decisions` INSERT is inside
SAVEPOINT and position_events append is outside.

### Option C — new `DECISION_EVIDENCE_RECORDED` `event_type` on `position_events`

Previously considered as the append-first path. **rev2 retracts this
framing**: Option C as originally stated (adding a new `event_type`)
is not necessary and would needlessly expand the INV-07 lifecycle
grammar.

### Option E — piggyback on existing `ENTRY_ORDER_POSTED` / `ENTRY_ORDER_FILLED` event's `payload_json`

The `position_events` table has a NOT NULL `payload_json TEXT` column
(grep-verified 2026-04-23 via
`sqlite3 state/zeus-world.db "PRAGMA table_info(position_events)"`:
col 17). The event_type enum at production DB already includes
`ENTRY_ORDER_POSTED` and `ENTRY_ORDER_FILLED` (col 4 enum values).
Both are emitted at entry time during `_dual_write_canonical_entry_if_available`
and thus land inside the canonical append-first transaction boundary.

Design:
- At entry, evaluator constructs `DecisionEvidence(evidence_type="entry", ...)`
  and passes it to the `EdgeDecision` → `_dual_write_canonical_entry_if_available`
  path (or a thin helper it calls).
- The payload for `ENTRY_ORDER_POSTED` (or `ENTRY_ORDER_FILLED`) is
  extended to include a `decision_evidence` nested key:
  ```json
  {"existing_fields": "...",
   "decision_evidence": {
     "contract_version": 1,
     "fields": {"evidence_type":"entry","statistical_method":"bootstrap_ci_bh_fdr","sample_size":5000,"confidence_level":0.10,"fdr_corrected":true,"consecutive_confirmations":1}}}
  ```
- At exit, `query_position_events(conn, runtime_trade_id)` at
  `src/state/db.py:2658` retrieves the entry event(s); exit authority
  extracts `decision_evidence` from the payload, rehydrates via
  `DecisionEvidence.from_json`, and calls `assert_symmetric_with`.

**Pros:**
- No schema migration — `payload_json` already exists as a NOT NULL
  TEXT column on every `position_events` row.
- No `event_type` CHECK-constraint change — INV-07 lifecycle grammar
  preserved.
- **Atomic with canonical event append** — the write lands in the same
  `append_many_and_project` transaction as the lifecycle event; the F3
  torn-state window does not apply.
- Reads via existing `query_position_events` helper; no new table or
  join path.
- Strictly structural per Fitz C1: solves the category (any future
  decision-contract evidence can extend `payload_json` similarly)
  rather than the instance.
- Reusable pattern: future contracts (exit_evidence, monitor_evidence,
  etc.) can use the same payload-sidecar idiom.

**Cons:**
- Semantic layering: evidence sits as a sidecar within a lifecycle
  event's payload. Readers must know the convention
  (`payload_json["decision_evidence"]`).
- If `ENTRY_ORDER_POSTED` is later emitted twice (retry path) the
  evidence needs idempotency — mitigable by keying on `decision_id`
  (present at `position_events.decision_id` col 9).
- Touches `src/engine/evaluator.py` + `src/state/lifecycle_events.py`
  (or equivalent event-emission helper) — still planning-lock
  territory per delivery.md §5, but no schema change.

## Recommendation (rev2): Option E

Option E is structurally superior on three axes:

1. **Atomicity.** Evidence lands in the same transaction as the
   canonical `position_events` append, via the existing
   `append_many_and_project` path. Inherits no torn-state window.
2. **Zero schema migration.** `payload_json` NOT NULL TEXT column
   already exists; no ALTER TABLE, no CREATE TABLE, no INV-07 event
   enum expansion.
3. **Category immunity (Fitz C1).** Any future decision-contract
   evidence (exit, monitor, symmetry-audit) extends the same
   payload-sidecar convention. No schema drift.

Option B's single-column migration is tempting but still inherits the
F3 torn-state window because `trade_decisions` is in a separate
transaction boundary from `position_events`. Option A has the same
problem plus semantic overload.

## contract_version tag

**Adopt.** `DecisionEvidence.to_json` emits
`{"contract_version": 1, "fields": {...}}`. `DecisionEvidence.from_json`
raises `UnknownContractVersionError` on unknown versions. Zero cost
now; prevents silent schema drift when `DecisionEvidence` fields
evolve (already happened once with `consecutive_confirmations`).

## Planning-lock classification

**REQUIRED for T4.1.** Per `docs/authority/zeus_current_delivery.md §5`,
planning-lock triggers on `src/state/**` and `src/engine/**` changes
regardless of additive/non-breaking status. "Additive, non-breaking"
is not an exemption. Before T4.1 ships:

```
.venv/bin/python scripts/topology_doctor.py --planning-lock \
  --changed-files src/engine/evaluator.py src/state/lifecycle_events.py \
    src/contracts/decision_evidence.py \
  --plan-evidence docs/operations/task_2026-04-23_midstream_remediation/T4_persistence.md \
  --json
```

Option E keeps T4.1 out of `src/state/db.py` schema, which reduces the
planning-lock surface vs Option B but does not eliminate it.

## Concrete T4.1 surface (derived from Option E)

- `src/contracts/decision_evidence.py` — add `to_json(self) -> str`
  and `@classmethod from_json(cls, payload: str) -> "DecisionEvidence"`
  with `contract_version` gating.
- `src/engine/evaluator.py` (entry path) — at the `EdgeDecision`
  construction sites that lead into
  `_dual_write_canonical_entry_if_available`, construct
  `DecisionEvidence(evidence_type="entry", ...)` and attach to the
  decision. Exact line range tied to the live handoff path in
  `cycle_runtime.py:1131-1137` and its upstream `EdgeDecision`
  producers; T4.1 does the grep to pin the exact attachment point.
- `src/state/lifecycle_events.py` (or event-emission helper) — extend
  `ENTRY_ORDER_POSTED` payload construction to include
  `decision_evidence` sidecar key when present.
- `src/execution/exit_triggers.py:49,158,218` — add a
  `load_entry_evidence(conn, runtime_trade_id)` helper that scans the
  position_events stream for the earliest `ENTRY_ORDER_POSTED` with
  `decision_evidence` and rehydrates. Wire into
  `evaluate_exit_triggers` audit-only per T4.2-Phase1.
- `tests/test_entry_exit_symmetry.py` extensions land under T4.3/T4.3b.

## Concrete T4.2-Phase1 surface

- `src/execution/exit_triggers.py:49,158,218` — audit-only wrapper:
  `try: assert_symmetric_or_stronger(entry_ev, exit_ev); except EvidenceAsymmetryError as e: log_audit_event("exit_evidence_asymmetry", ...); continue`.
- Emit `audit_log_false_positive_rate` metric over the next 7+ days.
  Gating metric for T4.2-Phase2 (`≤ 0.05`).

## Rollback

Option E rollback is cheap: stop attaching `decision_evidence` key at
entry; exit-side `load_entry_evidence` returns None on absent key;
fallback to pre-T4 EdgeContext-only decision path. No schema to roll
back.

## Surrogate-critic findings preserved

From the read-only surrogate-critic review 2026-04-23 (code-reviewer
agent, model=opus):

- **F1:** rev1 cited `evaluator.py:724, 1307` as Decision handoff.
  Grep-verified: those are FDR-filter lines. Correct handoff uses
  `EdgeDecision` constructed at
  `evaluator.py:753,778,803,815,832,842,866,882,901,912`. rev2
  rewrites the T4.1 derivation to avoid hardcoding line numbers and
  instead says "the live handoff path in `cycle_runtime.py:1131-1137`
  and its upstream `EdgeDecision` producers".
- **F2:** rev1 cited `db.py:2320, 2468` as INSERT sites. Grep-verified:
  those are VALUES-tuple entries inside `log_trade_entry` (L2275) and
  `log_trade_exit` (L2436); actual INSERTs at L2325, L2473. rev2 does
  not rely on these citations.
- **F3:** rev1 asserted atomicity between `trade_decisions` INSERT and
  `position_events` append. Grep-verified wrong via
  `cycle_runtime.py:1115-1140`'s explicit SAVEPOINT-then-dual-write
  comment. rev2 inverts the recommendation to Option E to sidestep the
  torn-state window.
- **F4 / DecisionLink:** rev2 notes that `src/state/decision_chain.py`
  contains `CycleArtifact`, `NoTradeCase`, `SettlementRecord`,
  `MonitorResult`, `ExitRecord` — **no `DecisionLink` class exists**.
  Not a candidate surface.

## Open questions for con-nyx (as durable critic)

1. Is Option E's idempotency story sufficient if `ENTRY_ORDER_POSTED`
   is emitted twice on a retry? My answer: key the payload on
   `position_events.decision_id` (col 9); later events with the same
   decision_id carry identical evidence. Preferred pattern?
2. Should `load_entry_evidence` be a helper in
   `src/state/decision_chain.py` (existing evidence-adjacent module)
   or in a new `src/state/decision_evidence_persistence.py`? Module
   boundary preference?
3. Does `query_position_events` at `src/state/db.py:2658` accept
   `runtime_trade_id` or does it require `position_id`? If the
   latter, T4.1 needs a lookup step; flag me.

Signed by executor (team-lead). Awaiting con-nyx critic review per
packet protocol. Surrogate critic provided structural cross-check.
