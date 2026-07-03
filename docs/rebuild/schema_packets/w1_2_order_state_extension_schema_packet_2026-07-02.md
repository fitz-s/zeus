# W1.2 — Order-State Extension (K0 schema packet, 2026-07-02, rev 2 post-critic)

## CRITIC RULINGS APPLIED (2026-07-02, binding — verdict PASS-WITH-REQUIRED-FIXES)

1. DDL lockstep test is **NORMALIZED/parsed comparison, NOT byte-equality** (the copies differ by
   indentation — a literal assertion false-positives). The guard covers BOTH pairs:
   venue_order_facts (db.py:1373 vs :5267) AND the collateral DDL pair (db.py:5513-5538 vs
   collateral_ledger.py:53-79) — the entire `_TRADE_CLASS_DDL` is outside the schema fingerprint,
   so W1.1's DDL edits are equally CI-blind; the combined guard lives in THIS packet's test.
2. **NULL-q_version rest-forever leak closed:** the derived-predicate module ALSO defines
   `rest_deadline_exceeded` (deterministic max-rest-age from release_calendar + W0.2-measured
   p99s — the C3/REST_ELIGIBLE deadline) applying to ALL open rests REGARDLESS of q_version.
   NULL-q rests are classified by deadline alone; q staleness stays INDETERMINATE for them.
   Cross-reference (strengthens the plan's same-packet rule): **W4 may delete
   `maker_rest_escalation` ONLY in the packet that lands this deadline predicate wired to the
   cancel path.**
   BOOTSTRAP (orchestrator ruling 2026-07-02): until W0.2 p99 measurements accumulate,
   `rest_deadline_exceeded` bootstraps from the INCUMBENT deadline — the live
   `MAKER_REST_ESCALATION_DEADLINE_MINUTES` operating value (today's operator-accepted behavior).
   No new number is invented and the W4 handover is behavior-continuous; the measured-p99 formula
   replaces the constant once data exists (K1-style fitted boundary, not a habit constant).
3. MEDs: stamping-precedent cites re-pointed (insert_command `snapshot_id` kwarg,
   venue_command_repo.py:812-834, INSERT :888; executor.py:1216-1226 is certificate VALIDATION,
   not stamping); INV-CL-1 dropped from invariants_touched (code-comment contract in
   canonical_lifecycle.py, not a registered invariant — cited as file contract instead);
   family_exclusive_dedup path corrected to src/strategy/.
4. Citation-verify queue folded: q_version is NULL BY RULE at the two reconcile backfill creation
   sites (exchange_reconcile.py:1152, :1660 — "not Zeus's decision basis"); pre-existing reducer/
   VenueOrderStatus gaps recorded below (out of scope); INV-28 cite = invariants.yaml:561;
   OPEN_ORDER_FACT_STATES = canonical_projections.py:40-44.

## ERRATUM (2026-07-02, post-verification — premise correction)

The "negative fingerprint check" acceptance criterion (pin must NOT move) rested on a FALSE
premise: `venue_commands` has a WORLD-schema DDL copy inside `init_schema()` (db.py:~2594 region),
which IS hashed by `scripts/check_schema_fingerprint.py`. Adding `q_version` to both DDL copies in
lockstep (the correct implementation) therefore legitimately moves the pin. The delivered work
(b845bd6c) repinned via `--write-pin`; verifier confirmed the pin move is exactly the q_version
column and nothing else. The negative check is REPLACED by: "pin moves exactly once, attributable
to the q_version column addition, verified by fingerprint-check exit 0 after repin." Same class of
premise correction as W1.1's fourth-DDL-copy finding — the copy-proliferation disease strikes the
packet authors too.

## ORCHESTRATOR DECISIONS — RESOLVED 2026-07-02

1. **OPTION B DECIDED (derived predicate; architecture doc row AMENDED same day).** First
   principles under the continuous-flow axiom: staleness is a continuous RELATION between live q
   and the order's stamped q_version, recomputed on every event — storing it as a state transition
   creates a stale copy of staleness itself (a poll-era artifact). Plane separation:
   `venue_order_facts` is venue truth (A2); stale-pending-cancel is our decision-plane
   classification and does not belong in that table. Derived-from-truth cannot desync; Option B
   eliminates the dual-DDL drift, reducer silent-drop, `_ORDER_STATUS_FOLD` ValueError, and
   fingerprint blind-spot risk classes wholesale, and cancel INTENT already exists as
   `CANCEL_PENDING`. The Option A sections below are RETAINED AS REJECTED-ALTERNATIVE RECORD —
   do not implement them. Implementation scope under B: `venue_commands.q_version` column +
   stamp plumbing + derived staleness/delay/rest-deadline predicate module + normalized lockstep
   DDL guard test.
   Edge case the predicate MUST define: family with NO current servable q (readiness BLOCKED) —
   staleness is then INDETERMINATE, and C3 must treat indeterminate as "do not churn cancels on a
   blind family" (fail-closed = leave resting orders under existing freshness gates, do not
   mass-cancel on missing q). Indeterminate-by-NULL-stamp orders are governed by
   `rest_deadline_exceeded` instead (critic ruling 2).
2. **CONFIRMED: `delayed` is derived-only** (dwell-time of in-flight CommandStates vs W0.2 measured
   p99). No CHECK slot, no enum member. The plan-doc W1.2 line that listed it beside the CHECK
   extension is corrected in the same commit as this resolution.
3. **CONFIRMED, AMENDED by critic ruling 1:** the DDL guard test lands in this packet as
   `test_ddl_copies_normalized_identical` — NORMALIZED table-definition comparison (parse via
   `sqlite3` in-memory create + `PRAGMA table_info`/index list, or equivalent canonicalization),
   covering BOTH the venue_order_facts pair and the collateral pair. Byte-equality rejected.
4. **OUT OF SCOPE, RECORDED:** the pre-existing `_OPEN_ORDER_FACT_STATES` vs
   `canonical_projections.OPEN_ORDER_FACT_STATES` drift goes to the plan doc's deferred list as a
   standalone hygiene fix; this packet documents both sets and widens nothing.

## Front matter

```yaml
work_packet_id: SCH-W1.2-ORDER-STATE
packet_type: schema_packet
objective: >
  Give every order a decision-basis stamp (q_version) joinable to its collateral
  reservation, realize the C3 staleness classification as a DERIVED predicate
  (Option B), define `delayed` as a derived submit-flight dwell-time flag, and
  define rest_deadline_exceeded as the deterministic max-rest-age deadline covering
  ALL open rests including NULL-q_version ones — without touching position
  LifecyclePhase and without expanding CommandState.
why_this_now: >
  W4's C3 staleness path (SOURCE_RUN_ARRIVED -> stale classification -> cancel-set ->
  reconciled re-solve) needs the q_version stamp and the classification predicates to
  exist BEFORE event-driven triggers raise decision frequency; W1 is the truth-object
  wave that makes W4 safe. W4's deletion of maker_rest_escalation (today's only GTC
  TTL owner) is licensed ONLY by the packet wiring rest_deadline_exceeded to the
  cancel path (critic ruling 2).
why_not_other_approach:
  - "Stored STALE_PENDING_CANCEL state (Option A, rejected): stores a derivable relation; crosses the A2 venue-truth plane with decision-plane classification; incurs dual-DDL drift, reducer silent-drop, fold ValueError, and fingerprint blind-spot risk classes — see rejected-alternative record below."
  - "CommandState member: INV-29's 2026-04-27 amendment (invariants.yaml:487-492) explicitly excludes venue-truth-shaped states from CommandState; a new member needs a fresh planning-lock amendment, bumps test_command_bus_types count==17, and adds _TRANSITIONS rows — all avoidable because cancel intent is already CANCEL_PENDING."
  - "Fold into position LifecyclePhase: forbidden orthogonality — PositionPhase (canonical_lifecycle.py:147-163) is a coarse position-level PROJECTION; a position stays ACTIVE while a child order is stale. derive_position_phase() (canonical_projections.py:214) takes boolean facts only, no order-state param; keep it that way."
truth_layer: "venue_commands (+q_version column, write-once, NULL BY RULE for non-decision-basis rows); reservation link already exists via collateral_reservations PK=command_id"
control_layer: "derived-predicate module: is_stale_pending_cancel / is_delayed / rest_deadline_exceeded; NO stored-state changes"
evidence_layer: "byte-identical replay of reducer over existing fact corpus (no reducer change under B — replay is the no-op proof); normalized DDL lockstep guard (both pairs)"
zones_touched: [K2_runtime]  # DECLARED K0 by content (canonical truth vocabulary), same elevation note as W1.1
invariants_touched: [INV-28 (transitions grammar, invariants.yaml:561 — untouched, no CommandState change), INV-29 (invariants.yaml:471-539 — respected by NOT expanding CommandState)]
file_contracts_respected:
  - "canonical_lifecycle.py INV-CL-1 code contract: normalize_venue_order_status remains the sole raw->typed conversion (no new raw strings introduced under Option B, so no fold changes)"
required_reads:
  - src/state/db.py:1369-1401,5263-5294,2599,5513-5538
  - src/state/venue_command_repo.py:56-129,186-203,812-888,1170-1190,2914-2999   # :812-834 insert_command snapshot_id kwarg, INSERT :888 — the STAMPING precedent
  - src/execution/command_bus.py:45-69,121-127
  - src/execution/order_truth_reducer.py:1-169
  - src/contracts/canonical_lifecycle.py:57-65,106-118,147-163,169-224
  - src/state/canonical_projections.py:16,40-44,214
  - src/execution/exchange_reconcile.py:1152,1651-1667   # the two NULL-q backfill creation sites
  - src/execution/executor.py:1216-1226                  # certificate VALIDATION precedent (NOT stamping — critic ruling 3a)
  - architecture/invariants.yaml:471-539,561
files_may_change:
  - src/state/db.py                       # venue_commands q_version column (additive) — NO CHECK edits under Option B
  - src/state/venue_command_repo.py       # insert_command stamp plumbing (q_version kwarg beside snapshot_id :812-834, INSERT :888)
  - src/state/canonical_projections.py    # derived-predicate module home (or new sibling module — implementer choice, registered either way)
  - src/execution/executor.py             # pass q_version at command creation
  - tests/*
files_may_not_change:
  - src/execution/command_bus.py          # NO CommandState expansion — hard boundary of this packet
  - src/execution/order_truth_reducer.py  # Option B: reducer untouched — its unchanged output IS the replay evidence
  - src/contracts/canonical_lifecycle.py  # Option B: no VenueOrderStatus/OrderProofClass/fold changes; PositionPhase orthogonality clause
  - architecture/_schema_fingerprint.txt  # Option B touches no fingerprint-covered DDL; pin must NOT move (negative check)
schema_changes: true
ci_gates_required:
  - "test_ddl_copies_normalized_identical (this packet) — normalized comparison over BOTH pairs: venue_order_facts (db.py:1373 vs :5267) and collateral (db.py:5513-5538 vs collateral_ledger.py:53-79); the _TRADE_CLASS_DDL is fingerprint-blind (scripts/check_schema_fingerprint.py:55-68)"
  - "negative fingerprint check: architecture/_schema_fingerprint.txt pin UNCHANGED by this packet (Option B edits no fingerprint-covered DDL)"
tests_required:
  - "test_ddl_copies_normalized_identical — parsed/normalized table defs equal for both pairs; deliberately NOT byte-equality (critic ruling 1)"
  - "byte-identical replay: reducer output over the existing venue_order_facts corpus unchanged pre/post (trivially true under B — the test IS the proof the packet made no truth-plane change)"
  - "q_version stamp: command created from a decision carries posterior_identity_hash; the two reconcile backfill sites (exchange_reconcile.py:1152, :1660) create with q_version NULL BY RULE (asserted)"
  - "is_stale_pending_cancel truth table: (stamped ≠ current, open) → True; (stamped = current) → False; (q_version NULL) → INDETERMINATE, never True; (family readiness BLOCKED / no servable q) → INDETERMINATE, no cancel churn"
  - "is_delayed: in-flight command past measured SLA flags delayed; terminal command never does"
  - "rest_deadline_exceeded: applies to ALL open rests regardless of q_version; NULL-q rest past deadline → True (the leak-closure case); deadline derives deterministically from release_calendar + measured p99 inputs (same inputs → same deadline)"
  - "CommandState count still 17 (tests/test_command_bus_types.py:344-345 unmodified)"
parity_required: false
replay_required: true
rollback: >
  venue_commands.q_version is additive/nullable — rollback is code revert, column inert.
  Predicate module is pure code. No CHECK edits, no enum edits, no reducer edits under
  Option B — nothing schema-destructive to unwind beyond the one column.
acceptance:
  - "predicate truth-tables green (staleness incl. INDETERMINATE branches, delayed, rest-deadline)"
  - "old rows unaffected — byte-identical replay over existing corpus; fingerprint pin unmoved"
  - "PositionPhase and derive_position_phase() untouched (diff-proof)"
  - "CommandState count still 17"
  - "normalized DDL guard green over both pairs"
evidence_required:
  - "schema diff + manifest diff (K0)"
  - "fingerprint pin before == after (negative proof)"
  - "replay-equality run output"
  - "predicate truth-table test output"
```

## Schema delta (exact)

### 1. `venue_commands.q_version` — additive column (the K0 core; the ONLY DDL change under Option B)

```sql
ALTER TABLE venue_commands ADD COLUMN q_version TEXT;  -- nullable; = forecast_posteriors.posterior_identity_hash at decision time
```

Stamped at command creation as a new `insert_command` kwarg beside the existing `snapshot_id`
kwarg — **stamping precedent: venue_command_repo.py:812-834 (kwarg) + INSERT :888** (critic
ruling 3a; executor.py:1216-1226 is the certificate-VALIDATION precedent — the read-side mismatch
check pattern — not the stamping site; the arch doc's `:1215` cite was additionally one line
stale). Per-ORDER granularity is satisfied through the existing 1:1 command→venue-order relation
joined by `command_id`; venue_order_facts gets NO stamp (facts are venue observations —
denormalizing our decision basis into them crosses truth planes). The **reserved-cash link**
requires NO new schema: `collateral_reservations` PK is already `command_id` (db.py:5526-5538).

**NULL BY RULE:** the two reconcile backfill creation sites (exchange_reconcile.py:1152, :1660 —
synthetic/external orders) create commands with `q_version = NULL`, meaning "not Zeus's decision
basis" — NULL is a defined semantic value, not missing data. Legacy rows are NULL by migration
default. Consequence handled by ruling 2: NULL-q orders are never q-stale (INDETERMINATE), and are
governed by `rest_deadline_exceeded` instead — no rest-forever leak.

Migration pattern: additive-column precedent `_ensure_position_current_authority_columns`
(db.py ~5797 area); register per that pattern (confirm the runner registration point before coding).

### 2. Derived-predicate module (pure code; the C3 vocabulary under Option B)

Home: `src/state/canonical_projections.py` or a registered sibling; three predicates, one module,
no stored state:

- `is_stale_pending_cancel(command_q_version, current_family_q_version, order_open)` — True iff
  stamped ≠ current AND open. NULL stamp → INDETERMINATE (never True). Family with no servable q
  (readiness BLOCKED) → INDETERMINATE for ALL its orders: C3 must not churn cancels on a blind
  family (fail-closed = existing freshness gates own that case).
- `is_delayed(command)` — `state ∈ {SUBMITTING, POSTING, SIGNED_PERSISTED} AND
  now − state_entered_at > submit_flight_sla`, SLA from W0.2's measured submit p99. Monitoring +
  (W4) REST_ELIGIBLE surface; never DDL.
- `rest_deadline_exceeded(order)` — **(critic ruling 2)** deterministic max-rest-age from
  `release_calendar` + W0.2-measured cancel/submit p99s (the C3/REST_ELIGIBLE deadline). Applies to
  ALL open rests REGARDLESS of q_version — this predicate, not q-staleness, retires NULL-q rests.
  **W4 cross-reference (binding):** `maker_rest_escalation` (today's only GTC TTL owner) may be
  deleted ONLY in the packet that wires this predicate to the cancel path.

The C3 "mark orders stale" step is a NO-WRITE: on `SOURCE_RUN_ARRIVED`/q_version advance the
affected family's open orders satisfy the predicate immediately; the cancel-set goes out through
the existing CANCEL intent (CommandState CANCEL_PENDING); reconciliation and re-solve read the
same derived truth. A fill landing against a superseded q_version is absorbed as endowment and
counted by the A2 tripwire (design doc :52-53) — no special case, fills always win.

### 3. Normalized DDL lockstep guard (critic ruling 1)

`test_ddl_copies_normalized_identical`: execute each DDL copy into an in-memory sqlite3 database
and compare canonical structure (`PRAGMA table_info`, index list, CHECK expressions normalized for
whitespace) — NOT string bytes. Pairs covered:
1. `venue_order_facts`: db.py:1373-1377 (world ghost, fingerprint-covered) vs db.py:5267-5271
   (trade, authoritative, fingerprint-BLIND).
2. collateral pair: db.py:5513-5538 (trade literal) vs collateral_ledger.py:53-79 (module schema) —
   guards W1.1's edits too; the whole `_TRADE_CLASS_DDL` has no CI coverage otherwise.

## Pre-existing gaps (recorded, OUT OF SCOPE — critic ruling 4 / dossier risks #5-#7)

- `order_truth_reducer` silently DROPS facts whose state matches no bucket — today that is 4
  CHECK-legal states (CANCEL_REQUESTED, CANCEL_UNKNOWN, CANCEL_FAILED, HEARTBEAT_CANCEL_SUSPECTED):
  they classify into no bucket and degrade silently. Pre-dates this packet; unchanged by Option B.
- `VenueOrderStatus` (canonical_lifecycle.py:57-65) types only 6 of the 11 DB-legal states —
  missing RESTING + the 4 above; `normalize_venue_order_status` raises on them.
- `exchange_reconcile._OPEN_ORDER_FACT_STATES` = {LIVE,RESTING,CANCEL_UNKNOWN} vs
  `canonical_projections.OPEN_ORDER_FACT_STATES` (canonical_projections.py:40-44) =
  {LIVE,PARTIALLY_MATCHED,RESTING} — drifted sets.
All three go to the plan doc's deferred hygiene list; this packet widens nothing.

## Orthogonality clause (explicit)

This packet does not add, rename, or re-map any `PositionPhase`/`LifecyclePhase` member
(canonical_lifecycle.py:147-163; alias canonical_projections.py:16) and does not add an
order-state parameter to `derive_position_phase()` (canonical_projections.py:214). A position
remains ACTIVE while a child order is stale-pending-cancel; order-plane truth reaches position
plane only through the existing boolean-facts interface.

## Implementation notes

- Atomic patch. Option B scope: one column + stamp plumbing + predicate module + guard tests.
- K0: manifest diff + schema diff in evidence; new predicate module (if a new file) registered in
  module_manifest.yaml + source_rationale.yaml same packet.
- Do NOT touch tests/test_command_bus_types.py count assertions — their staying green IS evidence.

## Schema-specific questions (template)

- Enum/constraint changed: venue_commands +1 nullable column. NO CHECK edits, NO enum edits, NO
  reducer edits (Option B). CommandState: unchanged.
- Replay/parity evidence: reducer byte-identical replay (no-op proof); fingerprint pin negative
  check; predicate truth tables.
- Append-only/idempotency: venue_order_facts untouched; q_version write-once at command creation;
  predicates are pure functions of stored truth — idempotent by construction.

---

## REJECTED-ALTERNATIVE RECORD — Option A (stored STALE_PENDING_CANCEL), do not implement

Retained verbatim from rev-1 for the K0 record; path fix applied (family_exclusive_dedup lives in
**src/strategy/**, critic ruling 3c).

### A-1. `venue_order_facts` CHECK widening (BOTH copies, lockstep)

```sql
-- db.py:1373-1377 (world ghost, fingerprint-covered) AND db.py:5267-5271 (trade, authoritative):
state TEXT NOT NULL CHECK (state IN (
  'LIVE','RESTING','MATCHED','PARTIALLY_MATCHED',
  'CANCEL_REQUESTED','CANCEL_CONFIRMED','CANCEL_UNKNOWN','CANCEL_FAILED',
  'EXPIRED','VENUE_WIPED','HEARTBEAT_CANCEL_SUSPECTED',
  'STALE_PENDING_CANCEL'                                   -- NEW
)),
```

### A-2. Typed vocabulary + write-gate + reducer

- `VenueOrderStatus.STALE_PENDING_CANCEL` + `_ORDER_STATUS_FOLD` entry (canonical_lifecycle.py:57-65,
  :169-184); `OrderProofClass` member (:106-118); `venue_command_repo._ORDER_FACT_STATES` (:186-199)
  write-gate add; reducer NEW 6th bucket, priority: terminal_fill > terminal_partial > matched>0 >
  terminal_zero_no_fill > review > unknown > stale_pending_cancel > open.
- `canonical_projections.OPEN_ORDER_FACT_STATES` (:40-44): NOT added; predicate
  `is_stale_pending_cancel()` resolves the not-open-but-dedup-blocking split; consumers:
  **src/strategy/**family_exclusive_dedup.py:101,408,412 (stale must STILL block family dedup —
  can fill until cancel confirms); maker_rest_escalation.py:51,59 (W4 deletes; no investment).

### A-3. Blast radius (Option A only — the table Option B avoids)

| consumer | site | handling |
|---|---|---|
| order_truth_reducer | buckets :100-121, chain :124-168 | new bucket; add unknown-state assertion |
| venue_command_repo._ORDER_FACT_STATES | :186-199 | add value (write-gate) |
| canonical_lifecycle VenueOrderStatus/_FOLD/OrderProofClass | :57-65,:106-118,:169-224 | add member + fold entry |
| canonical_projections.OPEN_ORDER_FACT_STATES | :40-44 | NOT added; new predicate |
| exchange_reconcile._OPEN_ORDER_FACT_STATES | :100-103 | NOT added (drifted local set); document |
| src/strategy/family_exclusive_dedup.py | :101,408,412 | verify stale still blocks dedup (test) |
| evaluator._ENTRY_ORDER_FACT_TERMINAL_NO_FILL_STATES | :3162-3230 | NOT terminal — no change; assert |
| cycle_runtime raw IN-list | :2140-2163 | NOT terminal — no change; assert |
| status_summary / substrate_observer / event_reactor_adapter raw SQL | :710-743 / :556-595 / :15035-15115 | fresh grep pass pre-ship |
| CommandState consumers (executor :3298-3356, command_recovery ~45 sites) | — | UNTOUCHED |

## ORIGINAL DECISION REQUESTS (record, rev-1)

1. Stored state vs derived predicate — resolved: Option B (derived).
2. `delayed` derived-only — confirmed.
3. DDL guard test — confirmed, amended to normalized comparison covering both pairs (critic ruling 1).
4. Pre-existing open-set drift — out of scope, recorded above.
