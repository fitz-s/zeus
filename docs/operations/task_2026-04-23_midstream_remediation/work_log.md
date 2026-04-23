# Midstream Remediation — Work Log

## W0 — 2026-04-23 — packet open

- Packet opened: `docs/operations/task_2026-04-23_midstream_remediation/`.
- Authority source: `docs/to-do-list/zeus_midstream_fix_plan_2026-04-23.md`
  (36-slice v2 plan, signed by pro-vega + con-nyx).
- Executor: team-lead (this session).
- Long-lasting critic: `con-nyx`.
- Upstream co-tenant: parallel agent working data-readiness
  (`docs/operations/task_2026-04-23_data_readiness_remediation/`).
- Initial state verified by independent probe:
  - Production `position_current`: 31 columns, canonical (no drift).
  - `config/provenance_registry.yaml`: already exists (516 lines, real
    content) — T1.b scope shifts from "create" to "audit + skipif removal".
  - `src/strategy/selection_family.py:92` — `make_family_id()` is
    deprecated; zero non-deprecated call sites in `src/` or `scripts/`.
  - `src/engine/cycle_runtime.py:703` — `execute_discovery_phase(..., *, env: str, deps)` canonical.
  - `src/engine/cycle_runtime.py:209` — `materialize_position(..., *, state: str, env: str, ...)` canonical.
  - Zeus venv at `.venv/` — `yaml 6.0.3`, `pytest 9.0.2`.

## Slice rows (appended per slice)

| Slice | Status | Commit | Critic | Date |
|---|---|---|---|---|
| W0 packet open | closed | `ec78c2f` | skipped (doc-only, planning-lock GREEN) | 2026-04-23 |
| T4.0 persistence design rev2 | closed | `9365b20` | surrogate critic CLEAR (Option E); con-nyx informed | 2026-04-23 |
| T7.b AST-walk guard | closed | pending | 1/1 pass on first run; zero pre-state violators (grep-verified) | 2026-04-23 |

## T4.0 — PROPOSAL notes (2026-04-23, rev2)

### Premise correction
Fix-plan v2 T4.0 row states "`decision_log` row keyed on
`decision_snapshot_id` (option b; no schema migration)". Grep-verified
at `src/state/db.py:528-536` shows `decision_log` has 6 columns
(`id, mode, started_at, completed_at, artifact_json, timestamp`) with
**no `decision_snapshot_id`**. Premise is false.

### Revision history
- **rev1 (earlier today)**: Picked Option B (new column
  `decision_evidence_json TEXT` on `trade_decisions`). Reviewed by
  surrogate critic (code-reviewer@opus).
- **rev2 (this entry)**: Integrates surrogate critic findings F1-F3
  + missed Option E. Recommendation flipped to Option E.

### Surrogate critic findings integrated
- **F1 HIGH**: rev1 cited `evaluator.py:724, 1307` as Decision handoff
  sites. Actual: FDR-filter logic. `EdgeDecision` (not `Decision`)
  is constructed at L753, 778, 803, 815, 832, 842, 866, 882, 901, 912.
- **F2 MED**: rev1 cited `db.py:2320, 2468` as INSERT sites. Actual:
  VALUES-tuple entries; INSERTs at L2325, L2473.
- **F3 HIGH**: rev1 asserted atomicity between `trade_decisions`
  INSERT and `position_events` append. Grep-verified WRONG via
  `cycle_runtime.py:1115-1140`'s explicit SAVEPOINT-then-dual-write
  comment. `trade_decisions` INSERT lives inside
  `SAVEPOINT sp_candidate_*`; `position_events` append
  (`append_many_and_project`) runs after SAVEPOINT release with its
  own `with conn:` sub-transaction. **Separate transaction boundaries;
  torn-state window exists today for `epistemic_context_json` and
  `edge_context_json`.** Option B inherits this window.
- **Missed Option E**: Piggyback on existing
  `ENTRY_ORDER_POSTED.payload_json` — `payload_json` NOT NULL TEXT
  column at `position_events` col 17 already exists (PRAGMA-verified).
  Atomic with canonical append. No schema change. No INV-07 expansion.

### Recommendation flipped: Option E

Option E is structurally superior on three axes:
1. Atomicity: evidence lands in the same canonical
   `append_many_and_project` transaction.
2. Zero schema migration: `payload_json` already exists.
3. Category immunity per Fitz C1: future decision-contract evidence
   extends the same payload-sidecar pattern.

### Planning-lock classification
REQUIRED for T4.1 per delivery.md §5. "Additive/non-breaking" is
not an exemption. Option E keeps T4.1 out of `src/state/db.py` schema
but still touches `src/engine/evaluator.py` + `src/state/lifecycle_events.py`
(or equivalent emission helper) — both under planning-lock trigger
zones.

### contract_version tag
ADOPT. `DecisionEvidence.to_json` emits
`{"contract_version": 1, "fields": {...}}`.
`from_json` raises `UnknownContractVersionError` on unknown versions.

### Open questions for con-nyx (durable critic)
1. Idempotency when `ENTRY_ORDER_POSTED` is retried: key on
   `decision_id`?
2. Helper location: `decision_chain.py` vs new
   `decision_evidence_persistence.py`?
3. Does `query_position_events` accept `runtime_trade_id` or
   `position_id`?
