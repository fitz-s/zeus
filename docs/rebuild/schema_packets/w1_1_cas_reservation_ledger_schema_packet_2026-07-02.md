# W1.1 — CAS Reservation Ledger (K0 schema packet, 2026-07-02)

## ORCHESTRATOR DECISIONS — RESOLVED 2026-07-02

1. **CONFIRMED: guarded single-statement CAS** (`INSERT ... SELECT ... WHERE spendable >= :amount`,
   rowcount 0 → `CollateralInsufficient`). Statement-level atomicity holds in all three connection
   modes; `BEGIN IMMEDIATE` breaks caller-owned mid-transaction conns. The belt-and-braces
   `AFTER INSERT` over-reserve trigger stays (defense in depth on the sell-side path where a single
   statement cannot check JSON-derived balances).
2. **CONFIRMED: NEW TABLE `collateral_unsettled_proceeds`.** A4 identity needs an auditable
   per-command bucket; a snapshot column loses provenance.
3. **CONFIRMED with ordering:** implementer MUST search migration history for a prior
   CHECK-widening precedent FIRST; the 12-step table-rebuild recipe below is the fallback proposal,
   not established law. Whichever recipe: one transaction + fingerprint refresh same commit.
4. **CONFIRMED: decrement inside `venue_command_repo.append_order_fact`'s savepoint.** The venue
   fact and the money adjustment it justifies must commit atomically; `fill_tracker` is the derived
   plane and gets zero collateral coupling.

## ORIGINAL DECISION REQUESTS (record)

1. **CAS mechanism choice.** Packet prescribes **guarded single-statement `INSERT ... SELECT ... WHERE spendable >= :amount`** (Option A) over `BEGIN IMMEDIATE` wrapping (Option B). Reason: `CollateralLedger` has THREE connection modes (in-memory conn, caller-owned conn possibly mid-transaction, `db_path` short-lived per call — dossier risk #5; riskguard reader `riskguard.py:118-123` and `post_trade_capital.py:138-141` use db_path mode). `BEGIN IMMEDIATE` inside a caller-owned open transaction raises `OperationalError: cannot start a transaction within a transaction`; a guarded single statement is atomic at statement level in every mode and serializes on SQLite's single writer lock. Confirm or override.
2. **Unsettled-proceeds bucket = NEW TABLE** `collateral_unsettled_proceeds` (prescribed) vs new aggregate column on `collateral_ledger_snapshots`. New table keeps per-command provenance and audit (who/when settled); column would lose it. New table ⇒ `domains.py` + `db_table_ownership.yaml` rows (listed below). Confirm.
3. **`exchange_reconcile_findings.kind` CHECK migration recipe.** SQLite CHECK constraints are not ALTERable; existing live DBs need the 12-step table-rebuild (create-new → copy → drop → rename → recreate indexes). The project's prior pattern for CHECK-constraint changes on live tables was NOT located in the dossier pass — implementer must find the precedent (search for prior kind/state CHECK widenings in migration history) before coding; if none exists, the rebuild recipe here is the proposal. Verify.
4. **Partial-fill decrement wiring point.** Prescribed: inside `venue_command_repo.append_order_fact` on the `PARTIALLY_MATCHED` write (same conn, same savepoint as the fact insert) — NOT in `fill_tracker.py` (which today has zero collateral coupling, dossier (d)). Alternative (fill_tracker) would split the truth write and the ledger adjustment across code planes and connections. Confirm.

## Front matter

```yaml
work_packet_id: SCH-W1.1-CAS-LEDGER
packet_type: schema_packet
objective: >
  Make collateral reservation aggregate-atomic (close the live cross-connection
  check-then-insert TOCTOU), convert-on-fill instead of release-on-fill (close the
  ~180s phantom-cash window), account partial fills against live reservations, add
  an unsettled-proceeds bucket, and enforce the A4 identity
  spendable = balance − reserved − pending_conversions with mismatch routed as an
  exchange_reconcile finding into the EXISTING RiskGuard RED.
why_this_now: >
  The race is live TODAY: reserve_pusd_for_buy (collateral_ledger.py:400-403) runs
  buy_preflight() [SELECT, :349-367] then _insert_reservation() [INSERT, :430-463]
  with no wrapping IMMEDIATE transaction; the daemon runs a 20-worker default pool
  plus a 2-worker reactor pool (main.py:11004-11069) whose order placements each use
  their own sqlite3.Connection; in WAL mode both preflights read the same committed
  snapshot, both pass, both insert — aggregate reserved exceeds spendable. Bugfix
  first, redesign prerequisite second.
why_not_other_approach:
  - "App-level mutex/serialization: does not cover db_path-mode callers in other processes; DB-level atomicity does."
  - "BEGIN IMMEDIATE wrapper: breaks caller-owned-transaction mode (nested transaction error); see decision 1."
  - "Fix-by-tightening-refresh-cadence: shrinks but does not close the phantom-cash window; conversion closes it in the same commit as the FILLED event."
truth_layer: "collateral_reservations + collateral_ledger_snapshots + NEW collateral_unsettled_proceeds (all Domain.TRADE = zeus_trades.db per src/state/domains.py:45-46; single-DB, INV-37 not triggered)"
control_layer: "reserve/release/convert API in src/state/collateral_ledger.py; terminal dispatch at venue_command_repo.py:1179-1185"
evidence_layer: "A4 identity checker + exchange_reconcile finding kind collateral_identity_mismatch + riskguard 7th component"
zones_touched: [K2_runtime]  # NOTE: directory-routed zone is K2 (zones.yaml:43-51) but this packet is DECLARED K0 by content (canonical money truth) — K0 review requirements apply regardless of directory default (dossier risk #4).
invariants_touched: [INV-05 (advisory-only risk forbidden — new component must gate, not advise), INV-29 (untouched, no CommandState change), INV-37 (verified not-triggered: all tables one physical DB)]
required_reads:
  - src/state/collateral_ledger.py:40-80,349-463,514-531,707-737
  - src/state/venue_command_repo.py:1080-1195
  - src/execution/executor.py:2675-2694,4430-4476,5700-5760
  - src/execution/exchange_reconcile.py:69-88,146-167,2403-2494,5710-5719
  - src/riskguard/riskguard.py:1954-1961,2091-2139,2173-2180,2374-2379
  - src/execution/fill_tracker.py:44-53,1134-1196
files_may_change:
  - src/state/collateral_ledger.py
  - src/state/venue_command_repo.py
  - src/state/domains.py
  - src/state/db.py            # duplicated collateral DDL literal :5513-5538 must stay in lockstep
  - src/execution/exchange_reconcile.py
  - src/riskguard/riskguard.py
  - architecture/db_table_ownership.yaml
  - tests/test_collateral_ledger.py
  - tests/test_collateral_ledger_global_path_backed.py
  - tests/test_exchange_reconcile.py
files_may_not_change:
  - src/execution/command_bus.py          # no state-grammar change in this packet
  - src/execution/executor.py             # reserve call sites keep their signature; CAS is inside the ledger API
  - src/riskguard/risk_level.py           # overall_level() signature is variadic-positional already? NO — it is fixed-arity; see riskguard.py edits; risk_level.py enum untouched
files_may_not_change_note: "If overall_level() signature must change arity, that edit is IN riskguard call sites + risk_level.py function — move risk_level.py to files_may_change at implementation time with reviewer ack; enum values must not change."
schema_changes: true
ci_gates_required:
  - "schema fingerprint: collateral DDL lives OUTSIDE the fingerprinted init_schema/init_schema_forecasts (it is trade-DB, scripts/check_schema_fingerprint.py:55-68 does not cover it) — add/extend a DDL-lockstep test instead (see tests_required)"
tests_required:
  - "CONCURRENCY PROOF (acceptance): ≥20 threads × N reserve attempts against one shared ledger with bounded balance — SUM(active reservations) never exceeds spendable at any commit point; zero over-reserve. Threads use separate connections (reproduces the live pool topology, main.py:11004-11069)."
  - "IDENTITY STORM (acceptance): simulated fill/partial-fill/cancel/settle storm — after every event, spendable + reserved + unsettled_proceeds reconstruction equals snapshot-derived total; A4 checker returns zero findings."
  - "convert-on-fill: FILLED terminal converts (release_reason='CONVERTED_ON_FILL', unsettled row created same txn) — spendable does NOT increase at conversion time (phantom-cash window closed)."
  - "partial fill: PARTIALLY_MATCHED fact decrements live reservation by matched notional in the same savepoint; remaining reservation releases/converts correctly at terminal."
  - "three connection modes: CAS correct under in-memory, caller-owned-conn (mid-transaction), and db_path modes."
  - "finding routing: unresolved collateral_identity_mismatch ⇒ overall RiskLevel == RED; resolve ⇒ level recovers."
  - "UPDATE existing tests asserting FILLED fully frees the reservation (dossier risk #1: release_reservation_* are the only mutation entry points; blast radius is narrow and enumerated)."
parity_required: false
replay_required: true   # identity storm doubles as replay evidence over synthetic event sequences
rollback: >
  Code revert restores check-then-insert (known race, pre-existing). New table and new
  columns are additive and inert without writers. exchange_reconcile CHECK rebuild has a
  reverse migration (rebuild back without the new kind) — only safe if zero
  collateral_identity_mismatch rows exist; otherwise resolve-then-migrate. Trigger dropped
  by name. Riskguard 7th component removed by reverting the two call sites + dict entry.
acceptance:
  - "concurrent reserve stress (≥20 threads) shows zero over-reserve"
  - "identity cash + reserved + unsettled = ledger holds across a simulated fill/cancel/settle storm"
  - "phantom-cash window: spendable never increases between FILLED event and settlement confirmation"
  - "unresolved A4 finding drives RiskGuard RED (sweep-capable, not advisory — INV-05)"
evidence_required:
  - "stress test output (thread count, iteration count, zero violations) committed with the packet"
  - "before/after trace of one FILLED lifecycle showing conversion not release"
  - "schema diff + manifest diff (K0 requirement, packet_templates/schema_packet.md:36)"
```

## Schema delta (exact)

### 1. `collateral_reservations` — additive columns (ALTER TABLE ADD COLUMN, both DDL copies: collateral_ledger.py:52-80 AND db.py:5513-5538)

```sql
ALTER TABLE collateral_reservations ADD COLUMN original_amount INTEGER;          -- backfill: = amount
ALTER TABLE collateral_reservations ADD COLUMN converted_amount INTEGER NOT NULL DEFAULT 0;
```

Semantics: `amount` becomes the LIVE remaining reservation (decremented on partial fills);
`original_amount` preserves the audit trail; `converted_amount` accumulates the portion converted
on (partial) fills. Existing rows: one-time backfill `original_amount = amount` in the same
migration. Release invariant: at terminal, `amount` reaches 0 via release (cancel-class) or
conversion (fill-class); `release_reason` distinguishes: existing free-text column gains the
convention `'CONVERTED_ON_FILL'` for fill-class terminals (no CHECK on release_reason today; keep
it that way — convention + test, not constraint).

### 2. NEW TABLE `collateral_unsettled_proceeds` (both DDL copies, + registries)

```sql
CREATE TABLE IF NOT EXISTS collateral_unsettled_proceeds (
  command_id TEXT PRIMARY KEY,
  reservation_type TEXT NOT NULL CHECK (reservation_type IN ('PUSD_BUY','CTF_SELL')),
  token_id TEXT,
  amount_micro INTEGER NOT NULL CHECK (amount_micro >= 0),
  created_at TEXT NOT NULL,
  settled_at TEXT,
  settle_reason TEXT,
  CHECK (
    (reservation_type = 'PUSD_BUY' AND token_id IS NULL)
    OR (reservation_type = 'CTF_SELL' AND token_id IS NOT NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_unsettled_open
  ON collateral_unsettled_proceeds (settled_at) WHERE settled_at IS NULL;
```

Rows are created at convert-on-fill (same transaction as the FILLED/partial fact), settled when
settlement/receipt truth confirms the proceeds (settlement lane hookup is enumerated caller work,
not schema). NOT spendable while `settled_at IS NULL`.

Registry deltas: `src/state/domains.py` CANONICAL_OWNER + `'collateral_unsettled_proceeds': Domain.TRADE`;
`architecture/db_table_ownership.yaml` new `db: trade` row (writer: collateral_ledger.py
convert path; reader: spendable computation + A4 checker). ALSO: fix/annotate the stale
"Ghost...Drop after 2026-08-09" note text on the existing collateral rows at
db_table_ownership.yaml:2316-2338 (contradicts domains.py:45-46 + main.py:3905 live wiring —
dossier risk #3) so a future reviewer does not delete live tables.

### 3. CAS reserve (code shape the schema must support — the guarded statement)

```sql
INSERT INTO collateral_reservations
  (command_id, reservation_type, token_id, amount, original_amount, converted_amount, created_at)
SELECT :command_id, 'PUSD_BUY', NULL, :amount, :amount, 0, :now
WHERE (
  (SELECT pusd_balance_micro FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1)
  - COALESCE((SELECT SUM(amount) FROM collateral_reservations
              WHERE reservation_type='PUSD_BUY' AND released_at IS NULL), 0)
  - COALESCE((SELECT SUM(amount_micro) FROM collateral_unsettled_proceeds
              WHERE reservation_type='PUSD_BUY' AND settled_at IS NULL AND 0), 0)
) >= :amount;
-- cursor.rowcount == 0  =>  raise CollateralInsufficient (existing exception, collateral_ledger.py:83)
```

(Third subquery shown for identity symmetry; PUSD unsettled proceeds do not reduce buy
spendability — they are simply not yet added to balance. Implementer drops the dead term; the A4
checker is where the bucket participates.) Sell-side (`reserve_tokens_for_sell`,
collateral_ledger.py:408-411) gets the same shape over `ctf_token_balances_json`-derived per-token
availability — the JSON-derived availability cannot be checked inside one SQL statement; sell-side
CAS therefore REQUIRES the belt-and-braces trigger below plus app-side re-read-after-insert
(insert, re-aggregate, delete-own-row-and-raise on violation — compensating CAS). This asymmetry
is acceptable: PUSD_BUY is the high-frequency race (every entry), CTF_SELL is per-position.

### 4. Belt-and-braces trigger (both DDL copies)

```sql
CREATE TRIGGER IF NOT EXISTS trg_reservations_no_overreserve
AFTER INSERT ON collateral_reservations
WHEN NEW.reservation_type = 'PUSD_BUY'
AND (
  (SELECT pusd_balance_micro FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1)
  - (SELECT COALESCE(SUM(amount),0) FROM collateral_reservations
     WHERE reservation_type='PUSD_BUY' AND released_at IS NULL)
) < 0
BEGIN
  SELECT RAISE(ABORT, 'COLLATERAL_OVERRESERVE');
END;
```

DB-level enforcement independent of app code path; fires only on the aggregate going negative.

### 5. `exchange_reconcile_findings.kind` CHECK widening (exchange_reconcile.py:146-167)

New kind `'collateral_identity_mismatch'`, context reuses `'periodic'`. THREE lockstep edits
(dossier (f)): `FindingKind` Literal (:69-76), `_FINDING_KINDS` frozenset (:79-88), DDL CHECK
(:149-152). Fresh DBs get the new CHECK from `_SCHEMA`; live DBs need the table-rebuild migration
(decision 3). The unique index `ux_findings_unresolved_subject (kind, subject_id, context)` is
additive-safe.

### 6. RiskGuard routing (no schema; enumerated for completeness)

`collateral_identity_level` = RED iff unresolved `collateral_identity_mismatch` findings exist,
GREEN otherwise; added as 7th arg at BOTH `overall_level()` call sites (riskguard.py:1954-1961,
:2374-2379), to `component_levels` (:2173-2180), and to the RED `failed_rules` block (:2091-2139)
so `alert_halt()` names it. NO new kill-switch — routes through the existing RED sweep (INV-05).

## Caller changes at the choke point (enumerated)

- `venue_command_repo.py:1179-1185`: terminal dispatch splits — fill-class (`FILLED`) →
  `convert_reservation_on_fill(conn, command_id, matched_notional)`; cancel-class
  (CANCELLED/EXPIRED/REJECTED/SUBMIT_REJECTED) → existing release. `_TERMINAL_RESERVATION_STATES`
  (collateral_ledger.py:40-42) splits into `_RELEASE_STATES` / `_CONVERT_STATES`.
- `venue_command_repo.append_order_fact` (:2914, INSERT :2980): on `PARTIALLY_MATCHED` fact,
  same-savepoint call `apply_partial_fill(conn, command_id, matched_delta_notional)` decrementing
  live `amount`, incrementing `converted_amount`, creating/topping the unsettled row.
- `executor.py` reserve call sites (:2675-2694, :4475, :5754-5759): signature unchanged; the CAS
  lives inside `reserve_pusd_for_buy`/`reserve_tokens_for_sell`.

## Implementation notes

- Keep the patch atomic; both DDL copies (collateral_ledger.py + db.py trade literal) in lockstep,
  plus a test asserting the two literals' parsed table defs match (closes the dual-copy drift class).
- K0: include manifest diff + schema diff in the packet evidence.
- The 30/150s snapshot refresh constants (collateral_ledger.py:43-48) are untouched — conversion
  removes the correctness dependence on refresh cadence; cadence becomes freshness hygiene only.

## Schema-specific questions (template)

- Enum/constraint introduced: reservation columns (additive), new table, new trigger, findings-kind
  CHECK widening. No closed string enum in src/contracts touched.
- Replay/parity evidence: identity-storm test output; no live-replay parity needed (additive truth).
- Append-only/idempotency: reservations keyed by command_id PK (idempotent one-per-command
  preserved); unsettled bucket keyed by command_id PK (idempotent); findings idempotent via
  existing ux_findings_unresolved_subject.
