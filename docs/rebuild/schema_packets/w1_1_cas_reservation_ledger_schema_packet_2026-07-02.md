# W1.1 — CAS Reservation Ledger (K0 schema packet, 2026-07-02, rev 2 post-critic)

## CRITIC RULINGS APPLIED (2026-07-02, binding — verdict PASS-WITH-REQUIRED-FIXES; CAS design PASSES)

1. Partial-fill incremental decrement **REPLACED** with idempotent derivation from cumulative
   `matched_size` (facts are cumulative per polymarket_user_channel.py:858; append_order_fact
   replays from WS+reconcile+recovery are deduped only for TERMINAL_ZERO,
   venue_command_repo.py:2943-2970 — arithmetic UPDATEs can double-fire; MAX() cannot).
2. `convert_reservation_on_fill(conn, command_id, state_after)` — NO notional param; converts the
   reservation at terminal time, deriving the filled portion internally.
3. A4 identity rewritten **TYPE-AWARE** (buy conversions reduce spendable; sell proceeds are
   expected-INCOMING, never uniform subtraction). Rev-1's internal contradiction (objective formula
   vs CAS third-subquery note) resolved.
4. False-RED protections specified: one-snapshot identity read, tolerance on the venue comparison,
   AUTO-RESOLVE on next clean check, settlement-coordinated clearing spelled out.
5. append_event-centrality invariant declared + carve-out guard for the
   exchange_reconcile.py:1651-1667 synthetic external-close direct write + A4 orphan sweep.
6. Concurrency acceptance test reproduces LIVE topology (insert_command → CAS, same conn, ≥20
   threads); contended failure must be CollateralInsufficient, never OperationalError.
7. MEDs: correctness rationale restated as write-lock-precedence dependency (+BUSY handling);
   trigger ABORT mapped to CollateralInsufficient at the API layer; required_reads cites fixed.

## ORCHESTRATOR DECISIONS — RESOLVED 2026-07-02

1. **CONFIRMED: guarded single-statement CAS** — critic ruling: race-free, PASSES; rationale
   amended per ruling 7 (see §3: write-lock precedence, not "atomic in every mode").
2. **CONFIRMED: NEW TABLE `collateral_unsettled_proceeds`** — now direction-aware per ruling 3.
3. **CONFIRMED with ordering:** implementer MUST search migration history for a prior
   CHECK-widening precedent FIRST; the table-rebuild recipe is the fallback proposal.
   Whichever recipe: one transaction + fingerprint refresh same commit.
4. ~~CONFIRMED: decrement inside `append_order_fact`'s savepoint~~ — **SUPERSEDED by critic
   ruling 1**: NO ledger write on partial facts at all; live remaining is DERIVED
   (`original − notional(MAX(matched_size))`). `fill_tracker` AND `append_order_fact` both stay
   collateral-free; the only ledger mutations are the CAS insert and the single terminal
   convert/release write.

## Front matter

```yaml
work_packet_id: SCH-W1.1-CAS-LEDGER
packet_type: schema_packet
objective: >
  Make collateral reservation aggregate-atomic (close the live cross-connection
  check-then-insert TOCTOU), convert-on-fill instead of release-on-fill (close the
  ~180s phantom-cash window), derive partial-fill accounting idempotently from
  cumulative fact matched_size (no arithmetic UPDATEs), add a direction-aware
  unsettled bucket (OUTGOING_DEDUCTION for buy spends pending balance reflection;
  INCOMING_PROCEEDS for sell proceeds), and enforce the type-aware A4 identity with
  mismatch routed as an auto-resolving exchange_reconcile finding into the EXISTING
  RiskGuard RED.
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
  - "Incremental partial-fill decrements (rev-1): matched_size is CUMULATIVE (polymarket_user_channel.py:858) and fact replays are deduped only for TERMINAL_ZERO (venue_command_repo.py:2943-2970) — any arithmetic UPDATE can double-fire on replay; MAX-based derivation is idempotent by construction (critic ruling 1, same max() semantics as order_truth_reducer.py:108)."
truth_layer: "collateral_reservations (+converted_amount, amount now IMMUTABLE) + collateral_ledger_snapshots + NEW collateral_unsettled_proceeds (all Domain.TRADE = zeus_trades.db per src/state/domains.py:45-46; single-DB, INV-37 not triggered)"
control_layer: "reserve/convert/release API in src/state/collateral_ledger.py; terminal dispatch at venue_command_repo.py:1179-1185 (SOLE terminalization seam — invariant below)"
evidence_layer: "type-aware A4 identity checker (one-snapshot read) + auto-resolving exchange_reconcile finding kind collateral_identity_mismatch + riskguard 7th component"
zones_touched: [K2_runtime]  # NOTE: directory-routed zone is K2 (zones.yaml:43-51) but this packet is DECLARED K0 by content (canonical money truth) — K0 review requirements apply regardless of directory default (dossier risk #4).
invariants_touched: [INV-05 (advisory-only risk forbidden — new component must gate, not advise), INV-29 (untouched, no CommandState change), INV-37 (verified not-triggered: all tables one physical DB), NEW terminalization-centrality invariant (declared + registered with this packet)]
required_reads:
  - src/state/collateral_ledger.py:40-80,349-463,514-531,707-737
  - src/state/venue_command_repo.py:1080-1195    # append_event + terminal dispatch seam (:1179-1185)
  - src/state/venue_command_repo.py:2914-2999    # append_order_fact writer; savepoint :2942; TERMINAL_ZERO-only dedup :2943-2970 (critic ruling 7c)
  - src/ingest/polymarket_user_channel.py:858    # matched_size is cumulative — basis of the derivation design
  - src/execution/executor.py:2675-2694,4430-4476,5700-5760
  - src/execution/exchange_reconcile.py:69-88,146-167,1651-1667,2403-2494,5710-5719
  - src/riskguard/riskguard.py:1954-1961,2091-2139,2173-2180,2374-2379
files_may_change:
  - src/state/collateral_ledger.py
  - src/state/venue_command_repo.py
  - src/state/domains.py
  - src/state/db.py            # duplicated collateral DDL literal :5513-5538 must stay in lockstep (normalized-DDL guard lands in W1.2, covers this pair too)
  - src/execution/exchange_reconcile.py
  - src/riskguard/riskguard.py
  - architecture/db_table_ownership.yaml
  - architecture/invariants.yaml   # terminalization-centrality registration
  - tests/test_collateral_ledger.py
  - tests/test_collateral_ledger_global_path_backed.py
  - tests/test_exchange_reconcile.py
files_may_not_change:
  - src/execution/command_bus.py          # no state-grammar change in this packet
  - src/execution/executor.py             # reserve call sites keep their signature; CAS is inside the ledger API
  - src/execution/fill_tracker.py         # ruling 1 removes partial-fill wiring entirely — fill_tracker stays collateral-free
  - src/riskguard/risk_level.py           # fixed-arity overall_level(); enum untouched
files_may_not_change_note: "If overall_level() signature must change arity, that edit is IN riskguard call sites + risk_level.py function — move risk_level.py to files_may_change at implementation time with reviewer ack; enum values must not change."
schema_changes: true
ci_gates_required:
  - "collateral DDL pair (db.py:5513-5538 vs collateral_ledger.py:53-79) covered by W1.2's NORMALIZED DDL-lockstep guard — the entire _TRADE_CLASS_DDL is outside the schema fingerprint (scripts/check_schema_fingerprint.py:55-68); do not rely on the fingerprint for anything in this packet"
tests_required:
  - "CONCURRENCY PROOF (acceptance, LIVE topology per critic ruling 6): each of ≥20 threads on its OWN connection performs insert_command (write lock acquired) THEN the CAS reserve on the SAME conn, against one shared bounded balance — SUM(live holds) never exceeds spendable at any commit point; zero over-reserve; every contended failure surfaces as CollateralInsufficient (rowcount 0), NEVER OperationalError."
  - "IDEMPOTENT DERIVATION: replay the same PARTIALLY_MATCHED fact stream (WS + reconcile + recovery duplicates) — derived live remaining invariant under replay; ZERO ledger writes on partial facts."
  - "convert-on-fill: FILLED terminal → convert_reservation_on_fill(conn, command_id, state_after) converts (release_reason='CONVERTED_ON_FILL', OUTGOING_DEDUCTION row same txn) — spendable does NOT increase at conversion time."
  - "partial-then-cancel: matched>0 then CANCELLED — filled-notional portion converts, unfilled remainder releases, ONE idempotent terminal write (WHERE released_at IS NULL guard)."
  - "type-aware identity: CTF_SELL proceeds recorded INCOMING_PROCEEDS, never reduce spendable_pusd; OUTGOING_DEDUCTION reduces spendable until cleared."
  - "clearing: balance snapshot with captured_at > converted_at + COLLATERAL_SNAPSHOT_CLOCK_SKEW_SECONDS settles older unsettled rows inside the refresh txn; identity holds across the refresh boundary."
  - "IDENTITY STORM (acceptance): fill/partial-fill/cancel/settle storm — after every event the type-aware reconstruction holds on a ONE-SNAPSHOT read; A4 checker zero findings."
  - "three connection modes: CAS correct under in-memory, caller-owned-conn (insert_command-first pattern), and db_path modes; BUSY/BUSY_SNAPSHOT handling per mode (ledger-owned txn: rollback + bounded retry on fresh snapshot; caller-owned: propagate, never auto-rollback the caller)."
  - "trigger mapping: RAISE(ABORT,'COLLATERAL_OVERRESERVE') (sqlite3.IntegrityError / SQLITE_CONSTRAINT) caught at the ledger API layer and re-raised as CollateralInsufficient (critic ruling 7b)."
  - "append_event-centrality guard: the direct terminal INSERT carve-out (exchange_reconcile.py:1651-1667) asserts NO live reservation exists for that command_id, else raises."
  - "A4 orphan sweep: live (unreleased, unconverted) reservation attached to a terminal command ⇒ collateral_identity_mismatch finding."
  - "finding lifecycle: unresolved collateral_identity_mismatch ⇒ overall RiskLevel == RED; next CLEAN check AUTO-RESOLVES (resolve_finding, resolution='auto_clean_recheck') and level recovers — transient mismatch never becomes a sticky halt; persistent mismatch keeps re-recording (idempotent via ux_findings_unresolved_subject) and keeps RED."
  - "UPDATE existing tests asserting FILLED fully frees the reservation (dossier risk #1; blast radius narrow and enumerated)."
parity_required: false
replay_required: true   # identity storm + duplicate-fact-stream replay double as replay evidence
rollback: >
  Code revert restores check-then-insert (known race, pre-existing). converted_amount column and
  the unsettled table are additive and inert without writers. exchange_reconcile CHECK rebuild has
  a reverse migration (rebuild back without the new kind) — only safe if zero
  collateral_identity_mismatch rows exist; otherwise resolve-then-migrate. Trigger dropped by
  name. Riskguard 7th component removed by reverting the two call sites + dict entry.
  Terminalization-centrality invariant registration reverted with the packet.
acceptance:
  - "concurrent reserve stress (≥20 threads, insert_command→CAS live topology) shows zero over-reserve and zero OperationalError surfacing"
  - "type-aware identity holds across a simulated fill/cancel/settle storm including the balance-refresh boundary"
  - "phantom-cash window: spendable never increases between FILLED event and clearing"
  - "unresolved A4 finding drives RiskGuard RED and auto-resolves on next clean check (INV-05 gating, no sticky halt)"
evidence_required:
  - "stress test output (thread count, iteration count, zero violations, zero OperationalError) committed with the packet"
  - "before/after trace of one FILLED lifecycle showing conversion + clearing, not release"
  - "replay-idempotence run output (duplicate fact stream)"
  - "schema diff + manifest diff (K0 requirement, packet_templates/schema_packet.md:36)"
```

## Money model (type-aware — critic ruling 3; replaces rev-1's uniform formula)

- **PUSD_BUY**: reserve `amount` = full cost at submit. Partial fills: **NO ledger writes** — the
  full hold stands (the money is committed; conservative and replay-idempotent). At terminal, the
  filled portion (derived below) CONVERTS to an `OUTGOING_DEDUCTION` unsettled row — it keeps
  reducing spendable until the venue balance snapshot reflects the spend; the unfilled remainder
  RELEASES. Rev-1 treated buy conversions as not-spendable-relevant in the CAS and
  spendable-reducing in the objective — that contradiction is resolved HERE: buy conversions
  REDUCE spendable until cleared.
- **CTF_SELL**: reserve token inventory. At terminal, the filled portion's PUSD proceeds are
  **INCOMING** — recorded as `INCOMING_PROCEEDS`, tracked for the identity but **never** part of
  `spendable_pusd` while unsettled (not yet in the balance; when the balance snapshot catches up
  they are inside `pusd_balance_micro` and the row settles). Never uniform subtraction.

**Identity (A4, type-aware):**

```
spendable_pusd = latest(pusd_balance_micro)
               − Σ amount       over live PUSD_BUY reservations (released_at IS NULL)
               − Σ amount_micro over unsettled OUTGOING_DEDUCTION rows
expected_incoming_pusd = Σ amount_micro over unsettled INCOMING_PROCEEDS rows   (tracked, never spendable)
```

**Derivation (critic ruling 1):** filled notional for a command =
`notional(MAX(matched_size) over its venue_order_facts stream)` — same max() semantics as
`order_truth_reducer.py:108`. Proportional conversion at terminal:
`converted = floor(amount × max_matched_size / original_order_size)` with rounding in the
CONSERVATIVE direction (round the released remainder down — keep more held, never less).

**Clearing (settlement-coordinated, critic ruling 4):** an unsettled row settles when a balance
snapshot exists with `captured_at > converted_at + COLLATERAL_SNAPSHOT_CLOCK_SKEW_SECONDS`
(collateral_ledger.py:49) — the venue applies fills to balance at fill time, so any later-captured
snapshot already contains the deduction/proceeds; settling then is safe by construction. Performed
inside the balance-refresh write transaction (`settle_reason='BALANCE_REFRESH_OBSERVED'`), same
conn — not deferred to unspecified "caller work".

## Schema delta (exact)

### 1. `collateral_reservations` — ONE additive column (both DDL copies: collateral_ledger.py:52-80 AND db.py:5513-5538)

```sql
ALTER TABLE collateral_reservations ADD COLUMN converted_amount INTEGER NOT NULL DEFAULT 0;
```

`amount` is **IMMUTABLE after insert** (rev-1's live-decrement semantics withdrawn per critic
ruling 1; `original_amount` column dropped — `amount` IS the original). `converted_amount` is
written exactly ONCE, at terminal conversion, in the same UPDATE that sets `released_at` +
`release_reason`, guarded by `WHERE released_at IS NULL` (single idempotent terminal write).
`release_reason` convention: `'CONVERTED_ON_FILL'` for fill-class terminals (no CHECK on
release_reason today; keep it that way — convention + test, not constraint).

### 2. NEW TABLE `collateral_unsettled_proceeds` — direction-aware (both DDL copies, + registries)

```sql
CREATE TABLE IF NOT EXISTS collateral_unsettled_proceeds (
  command_id TEXT PRIMARY KEY,
  direction TEXT NOT NULL CHECK (direction IN ('OUTGOING_DEDUCTION','INCOMING_PROCEEDS')),
  reservation_type TEXT NOT NULL CHECK (reservation_type IN ('PUSD_BUY','CTF_SELL')),
  token_id TEXT,
  amount_micro INTEGER NOT NULL CHECK (amount_micro >= 0),
  created_at TEXT NOT NULL,
  settled_at TEXT,
  settle_reason TEXT,
  CHECK (
    (reservation_type = 'PUSD_BUY' AND token_id IS NULL AND direction = 'OUTGOING_DEDUCTION')
    OR (reservation_type = 'CTF_SELL' AND token_id IS NOT NULL AND direction = 'INCOMING_PROCEEDS')
  )
);
CREATE INDEX IF NOT EXISTS idx_unsettled_open
  ON collateral_unsettled_proceeds (settled_at) WHERE settled_at IS NULL;
```

Rows are created at terminal conversion (same transaction as the terminal event) and settled by the
clearing rule (§ Money model) inside the balance-refresh transaction.

Registry deltas: `src/state/domains.py` CANONICAL_OWNER + `'collateral_unsettled_proceeds': Domain.TRADE`;
`architecture/db_table_ownership.yaml` new `db: trade` row (writers: collateral_ledger convert +
refresh-clearing paths; readers: spendable computation + A4 checker). ALSO: fix/annotate the stale
"Ghost...Drop after 2026-08-09" note on the existing collateral rows at
db_table_ownership.yaml:2316-2338 (contradicts domains.py:45-46 + main.py:3905 live wiring —
dossier risk #3) so a future reviewer does not delete live tables.

### 3. CAS reserve — the guarded statement (buy side)

```sql
INSERT INTO collateral_reservations
  (command_id, reservation_type, token_id, amount, converted_amount, created_at)
SELECT :command_id, 'PUSD_BUY', NULL, :amount, 0, :now
WHERE (
  (SELECT pusd_balance_micro FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1)
  - COALESCE((SELECT SUM(amount) FROM collateral_reservations
              WHERE reservation_type='PUSD_BUY' AND released_at IS NULL), 0)
  - COALESCE((SELECT SUM(amount_micro) FROM collateral_unsettled_proceeds
              WHERE direction='OUTGOING_DEDUCTION' AND settled_at IS NULL), 0)
) >= :amount;
-- cursor.rowcount == 0  =>  raise CollateralInsufficient (existing exception, collateral_ledger.py:83)
```

The third subquery is LIVE (not symbolic): unsettled buy-spends reduce spendability until cleared
(type-aware identity — rev-1's "implementer drops the dead term" note deleted as the resolved
contradiction).

**Correctness rationale (critic ruling 7a — replaces "atomic at statement level in every mode"):**
correctness rests on SQLite's single-writer lock precedence, not statement magic. A competing
writer serializes at write-lock acquisition; once this statement (or the caller's earlier
`insert_command` write on the same conn) holds the lock, the subqueries evaluate against a state
containing every previously COMMITTED reservation, and no other writer can interleave before this
transaction commits. In the live call pattern the conn is already the writer by CAS time
(executor.py inserts the command row first on the same conn — the exact topology the acceptance
test reproduces, ruling 6). **BUSY handling:** on `SQLITE_BUSY`/`SQLITE_BUSY_SNAPSHOT` (a
reader-snapshot conn failing to upgrade — possible in db_path / shared-cache modes): if the LEDGER
owns the transaction, roll back and retry on a fresh snapshot (bounded retries within the existing
30s busy_timeout, collateral_ledger.py:50); if the CALLER owns it, propagate — never auto-rollback
a caller's transaction. Persistent busy fails closed (no order) and is DISTINCT from
CollateralInsufficient.

Sell-side (`reserve_tokens_for_sell`, collateral_ledger.py:408-411): per-token availability lives
in `ctf_token_balances_json` — not checkable inside one SQL statement; sell-side uses compensating
CAS (insert → re-aggregate → delete-own-row-and-raise on violation) plus the trigger below.
Acceptable asymmetry: PUSD_BUY is the high-frequency race (every entry), CTF_SELL is per-position.

### 4. Belt-and-braces trigger (both DDL copies)

```sql
CREATE TRIGGER IF NOT EXISTS trg_reservations_no_overreserve
AFTER INSERT ON collateral_reservations
WHEN NEW.reservation_type = 'PUSD_BUY'
AND (
  (SELECT pusd_balance_micro FROM collateral_ledger_snapshots ORDER BY id DESC LIMIT 1)
  - (SELECT COALESCE(SUM(amount),0) FROM collateral_reservations
     WHERE reservation_type='PUSD_BUY' AND released_at IS NULL)
  - (SELECT COALESCE(SUM(amount_micro),0) FROM collateral_unsettled_proceeds
     WHERE direction='OUTGOING_DEDUCTION' AND settled_at IS NULL)
) < 0
BEGIN
  SELECT RAISE(ABORT, 'COLLATERAL_OVERRESERVE');
END;
```

DB-level enforcement independent of app code path. The resulting `sqlite3.IntegrityError`
(SQLITE_CONSTRAINT) is caught at the ledger API layer and re-raised as `CollateralInsufficient`
(critic ruling 7b) — no caller ever handles a raw trigger abort.

### 5. `exchange_reconcile_findings.kind` CHECK widening (exchange_reconcile.py:146-167)

New kind `'collateral_identity_mismatch'`, context reuses `'periodic'`. THREE lockstep edits
(dossier (f)): `FindingKind` Literal (:69-76), `_FINDING_KINDS` frozenset (:79-88), DDL CHECK
(:149-152). Fresh DBs get the new CHECK from `_SCHEMA`; live DBs need the table-rebuild migration
(decision 3). The unique index `ux_findings_unresolved_subject (kind, subject_id, context)` is
additive-safe.

### 6. A4 checker + RiskGuard routing (false-RED protections — critic ruling 4)

- **One consistent read snapshot:** the identity check runs on a SINGLE connection as one SELECT
  set (balance row + reservation aggregate + unsettled aggregate inside one read transaction) —
  never computed across the balance-refresh boundary.
- **Tolerance on the venue comparison:** the internal reconstruction must hold EXACTLY; the
  comparison against the venue-reported balance (inherently lagged) carries an explicit tolerance:
  unsettled rows younger than `COLLATERAL_SNAPSHOT_MAX_AGE_SECONDS` are excluded from the
  expected-venue-delta before comparing; only a mismatch beyond that tolerance records a finding.
- **Auto-resolve:** on the next CLEAN check, any unresolved `collateral_identity_mismatch` finding
  is resolved via the existing `resolve_finding` API (`resolution='auto_clean_recheck'`) — a
  transient mismatch never becomes a sticky trading halt; a persistent one keeps re-recording
  (idempotent via the unresolved-subject unique index) and keeps RED.
- Routing: `collateral_identity_level` = RED iff unresolved findings exist, GREEN otherwise; added
  as 7th arg at BOTH `overall_level()` call sites (riskguard.py:1954-1961, :2374-2379), to
  `component_levels` (:2173-2180), and to the RED `failed_rules` block (:2091-2139) so
  `alert_halt()` names it. NO new kill-switch — routes through the existing RED sweep (INV-05).

## NEW INVARIANT (declared here, registered in architecture/invariants.yaml with this packet)

**Terminalization centrality:** a reservation-bearing command reaches a terminal CommandState ONLY
through `append_event`'s dispatch (venue_command_repo.py:1179-1185) — the sole seam where
release/convert fires. **Sole carve-out:** the synthetic external-close direct write at
exchange_reconcile.py:1651-1667, guarded by a write-gate assertion that NO live reservation exists
for that command_id (an externally-closed foreign order was never reserve-backed by Zeus; if the
assertion fires, that is a real incident — fail loudly). The A4 sweep independently detects the
residue class: live reservation attached to a terminal command ⇒ finding (defense in depth if a
future terminalization path bypasses both).

## Caller changes at the choke point (enumerated)

- `venue_command_repo.py:1179-1185`: terminal dispatch splits —
  `convert_reservation_on_fill(conn, command_id, state_after)` for fill-class / existing release
  for zero-fill cancel-class. Signature carries NO notional (critic ruling 2): only `command_id` +
  `state_after` are in scope at this seam; the function derives the filled portion internally from
  the command's fact stream (`MAX(matched_size)`) at terminal time — everything it needs is
  reachable from `conn` + `command_id`. `_TERMINAL_RESERVATION_STATES` (collateral_ledger.py:40-42)
  splits into `_RELEASE_STATES` / `_CONVERT_STATES`; partial-then-cancel terminals convert the
  filled portion and release the remainder in the same idempotent UPDATE.
- `append_order_fact`: **NO change** (critic ruling 1 removed the partial-fill hook; rev-1's
  `apply_partial_fill` bullet withdrawn).
- `executor.py` reserve call sites (:2675-2694, :4475, :5754-5759): signature unchanged; the CAS
  lives inside `reserve_pusd_for_buy`/`reserve_tokens_for_sell`.
- Balance-refresh writer (main.py:1722-1776 path → ledger): gains the clearing step (§ Money
  model) inside its write transaction.

## Implementation notes

- Keep the patch atomic; both collateral DDL copies in lockstep — the NORMALIZED-DDL lockstep guard
  lives in W1.2's test (covers the venue_order_facts pair AND this pair; do not duplicate here).
- K0: include manifest diff + schema diff in the packet evidence.
- The 30/150s snapshot refresh constants (collateral_ledger.py:43-48) are untouched — conversion
  removes the correctness dependence on refresh cadence; cadence becomes freshness hygiene only.

## Schema-specific questions (template)

- Enum/constraint introduced: +1 reservation column (additive, write-once), new direction-aware
  table + composite CHECK, new trigger, findings-kind CHECK widening. No closed string enum in
  src/contracts touched.
- Replay/parity evidence: identity-storm + duplicate-fact-stream replay outputs; no live-replay
  parity needed (additive truth).
- Append-only/idempotency: reservations one-per-command (PK) with IMMUTABLE `amount` and a single
  guarded terminal write; unsettled bucket keyed by command_id PK; derivation is MAX-based and
  replay-idempotent; findings idempotent via ux_findings_unresolved_subject.

## ORIGINAL DECISION REQUESTS (record, rev-1)

1. CAS mechanism choice (guarded INSERT vs BEGIN IMMEDIATE) — resolved: guarded INSERT; critic PASS.
2. Unsettled bucket new-table vs snapshot column — resolved: new table (now direction-aware).
3. CHECK migration recipe precedent search — standing verification obligation.
4. Partial-fill decrement wiring point (append_order_fact vs fill_tracker) — MOOT: critic ruling 1
   removed partial-fill ledger writes entirely; derivation replaces both alternatives.
