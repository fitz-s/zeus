# W1.2 — Order-State Extension (K0 schema packet, 2026-07-02)

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
   stamp plumbing + derived staleness/delay predicate module + lockstep DDL guard test.
   Edge case the predicate MUST define: family with NO current servable q (readiness BLOCKED) —
   staleness is then INDETERMINATE, and C3 must treat indeterminate as "do not churn cancels on a
   blind family" (fail-closed = leave resting orders under existing freshness gates, do not
   mass-cancel on missing q).
2. **CONFIRMED: `delayed` is derived-only** (dwell-time of in-flight CommandStates vs W0.2 measured
   p99). No CHECK slot, no enum member. The plan-doc W1.2 line that listed it beside the CHECK
   extension is corrected in the same commit as this resolution.
3. **CONFIRMED: `test_venue_order_facts_ddl_copies_identical` lands in this packet** even under
   Option B — the trade-authoritative DDL copy being CI-blind is a live hole on a money table
   regardless of whether this packet edits the CHECK.
4. **OUT OF SCOPE, RECORDED:** the pre-existing `_OPEN_ORDER_FACT_STATES` vs
   `canonical_projections.OPEN_ORDER_FACT_STATES` drift goes to the plan doc's deferred list as a
   standalone hygiene fix; this packet documents both sets and widens nothing.

## ORIGINAL DECISION REQUESTS (record)

1. **Stored state vs derived predicate (LOAD-BEARING — changes the whole packet).** The architecture
   doc row (order_engine_implementation_architecture_2026-07-02.md:45) prescribes Option A: add
   `STALE_PENDING_CANCEL` to the `venue_order_facts` CHECK + reducer vocabulary. The dossier
   evidence points at a leaner Option B: **staleness is DERIVABLE** — once `venue_commands` carries
   a `q_version` stamp, "stale" ≡ `command.q_version ≠ current family q_version AND order open`,
   and "pending cancel" already exists as CommandState CANCEL_PENDING. Option B stores NO new state
   value: no dual-DDL CHECK edit (risk #1), no reducer bucket (risk #5 silent-drop class), no
   VenueOrderStatus/_ORDER_STATUS_FOLD edits (risk #7 ValueError class), no fingerprint blind-spot
   exposure (risk #2), and it cannot desync from truth because it is computed from truth.
   Cost of B: consumers needing the classification must call a predicate with current-q context
   instead of reading a state string; the C3 "mark orders stale" step becomes a no-write. Evidence
   also shows `venue_order_facts` is VENUE truth (A2) — `STALE_PENDING_CANCEL` is OUR decision-plane
   classification, which argues it does not belong in that table at all.
   **This packet fully specifies Option A below (as directed) and recommends Option B.** Decide
   before implementation; if B, sections "CHECK delta", "reducer delta", "typed-vocabulary delta"
   collapse to the derived-predicate module only.
2. **`delayed` is NOT a stored enum value in either option.** Design doc line 191 names it a
   "submit-flight delayed state": dwell-time of in-flight CommandStates
   (SUBMITTING/POSTING/SIGNED_PERSISTED) beyond a measured SLA — a derived/monitoring flag
   (dossier risk #4). Packet defines it as a predicate + W0.2 metric join, NO CHECK slot, NO enum
   member. Confirm this reading (the plan-doc W1.2 line lists `delayed` next to the CHECK
   extension, which would be a governance mistake per evidence).
3. **Dual-DDL byte-equality test.** No test today asserts the world-ghost copy (db.py:1373) and the
   trade-authoritative copy (db.py:5267) stay identical, and the schema fingerprint does NOT cover
   the trade copy (scripts/check_schema_fingerprint.py:55-68 hashes init_schema +
   init_schema_forecasts only — dossier risk #2). Packet adds
   `test_venue_order_facts_ddl_copies_identical`. Confirm (should be uncontroversial).
4. **Pre-existing consumer drift is IN SCOPE or OUT?** `exchange_reconcile._OPEN_ORDER_FACT_STATES`
   = {LIVE,RESTING,CANCEL_UNKNOWN} already disagrees with `canonical_projections.OPEN_ORDER_FACT_STATES`
   = {LIVE,PARTIALLY_MATCHED,RESTING} (dossier risk #6, pre-dates this packet). Packet default:
   OUT of scope (do not widen a K0 schema packet into a refactor), but the new-state handling table
   below documents both sets so the drift is at least not widened. Flag if you want it fixed here.

## Front matter

```yaml
work_packet_id: SCH-W1.2-ORDER-STATE
packet_type: schema_packet
objective: >
  Give every order a decision-basis stamp (q_version) joinable to its collateral
  reservation, realize the C3 staleness classification STALE_PENDING_CANCEL
  (stored enum per Option A / derived predicate per Option B), and define
  `delayed` as a derived submit-flight dwell-time flag — without touching
  position LifecyclePhase and without expanding CommandState.
why_this_now: >
  W4's C3 staleness path (SOURCE_RUN_ARRIVED -> stale classification -> cancel-set ->
  reconciled re-solve) needs the q_version stamp and the classification vocabulary to
  exist BEFORE event-driven triggers raise decision frequency; W1 is the truth-object
  wave that makes W4 safe.
why_not_other_approach:
  - "CommandState member: INV-29's 2026-04-27 amendment (invariants.yaml:487-492) explicitly excludes venue-truth-shaped states from CommandState; a new member needs a fresh planning-lock amendment, bumps test_command_bus_types count==17, and adds _TRANSITIONS rows — all avoidable because cancel intent is already CANCEL_PENDING."
  - "Fold into position LifecyclePhase: forbidden orthogonality — PositionPhase (canonical_lifecycle.py:147-163) is a coarse position-level PROJECTION; a position stays ACTIVE while a child order is stale. derive_position_phase() (canonical_projections.py:214) takes boolean facts only, no order-state param; keep it that way."
truth_layer: "venue_commands (+q_version column); venue_order_facts CHECK (Option A only); reservation link already exists via collateral_reservations PK=command_id"
control_layer: "order_truth_reducer classification; venue_command_repo._ORDER_FACT_STATES write-gate (Option A); staleness/delay predicates (both options)"
evidence_layer: "byte-identical replay of reducer over existing fact corpus; dual-DDL lockstep test"
zones_touched: [K2_runtime]  # DECLARED K0 by content (canonical truth vocabulary), same elevation note as W1.1
invariants_touched: [INV-28 (transitions grammar — untouched if no CommandState change), INV-29 (respected by NOT expanding CommandState), INV-CL-1 (normalize_venue_order_status remains sole raw->typed conversion)]
required_reads:
  - src/state/db.py:1369-1401,5263-5294,2599
  - src/execution/command_bus.py:45-69,121-127
  - src/execution/order_truth_reducer.py:1-169
  - src/contracts/canonical_lifecycle.py:57-65,106-118,147-163,169-224
  - src/state/canonical_projections.py:16,40-49,214
  - src/state/venue_command_repo.py:56-129,186-203,1170-1190,2914-2999
  - architecture/invariants.yaml:471-539
files_may_change:
  - src/state/db.py                       # both venue_order_facts DDL copies + venue_commands column (Option A: CHECK too)
  - src/state/venue_command_repo.py       # write-gate + append_order_fact stamp plumbing
  - src/execution/order_truth_reducer.py  # Option A: new bucket + branch
  - src/contracts/canonical_lifecycle.py  # Option A: VenueOrderStatus + OrderProofClass + _ORDER_STATUS_FOLD
  - src/state/canonical_projections.py    # predicate (both options); OPEN_ORDER_FACT_STATES decision
  - src/execution/executor.py             # stamp q_version at command creation (snapshot precedent :1216-1226)
  - architecture/_schema_fingerprint.txt  # --write-pin after world-copy edit (Option A)
  - tests/*
files_may_not_change:
  - src/execution/command_bus.py          # NO CommandState expansion — hard boundary of this packet
  - src/contracts/canonical_lifecycle.py::PositionPhase   # orthogonality clause
  - src/state/canonical_projections.py::derive_position_phase  # no order-state param, ever (this packet)
schema_changes: true
ci_gates_required:
  - "scripts/check_schema_fingerprint.py --write-pin (Option A; verify hash MOVES from 4f8392d6...eb11 — proves the fingerprint-covered world copy was edited, dossier (g))"
tests_required:
  - "test_venue_order_facts_ddl_copies_identical (decision 3 — both options)"
  - "byte-identical replay: reducer output over the existing venue_order_facts corpus unchanged pre/post (acceptance; both options)"
  - "Option A: reducer round-trips STALE_PENDING_CANCEL — classified into its own bucket, never silently dropped (dossier risk #5: today's reducer drops unknown states without raising — add an explicit unknown-state assertion too)"
  - "Option A: normalize_venue_order_status('STALE_PENDING_CANCEL') returns the typed member, does not raise (dossier risk #7)"
  - "q_version stamp: command created from a decision carries posterior_identity_hash; NULL allowed for legacy rows"
  - "delayed predicate: in-flight command past SLA flags delayed; terminal command never does"
  - "Option B: is_stale_pending_cancel(command_q_version, current_q_version, order_open) truth table"
parity_required: false
replay_required: true
rollback: >
  venue_commands.q_version is additive/nullable — rollback is code revert, column inert.
  Option A CHECK widening: old rows never carry the new value; rollback = code revert +
  (optionally) narrowing CHECK back on next rebuild — safe iff zero rows written with the
  new state (query before narrowing). Reducer/vocabulary reverts are pure code.
acceptance:
  - "reducer round-trips every new state (Option A) / predicate truth-table green (Option B)"
  - "old rows unaffected — byte-identical replay over existing corpus"
  - "PositionPhase and derive_position_phase() untouched (diff-proof)"
  - "CommandState count still 17 (tests/test_command_bus_types.py:344-345 unmodified)"
evidence_required:
  - "schema diff + manifest diff (K0)"
  - "fingerprint before/after hashes (Option A)"
  - "replay-equality run output"
```

## Schema delta (exact)

### 1. `venue_commands.q_version` — additive column (BOTH options; the actual K0 core)

```sql
ALTER TABLE venue_commands ADD COLUMN q_version TEXT;  -- nullable; = forecast_posteriors.posterior_identity_hash at decision time
```

Stamped at command creation next to the existing `snapshot_id` (db.py:2599; stamping precedent
executor.py:1216-1226 — note the arch doc's `:1215` cite is one line stale, dossier risk #9).
Per-ORDER granularity is satisfied through the existing 1:1 command→venue-order relation joined by
`command_id`; venue_order_facts gets NO duplicate stamp (facts are venue observations — denormalizing
our decision basis into them crosses truth planes). The **reserved-cash link** requires NO new
schema: `collateral_reservations` PK is already `command_id` (db.py:5526-5538) — q_version ↔
reservation joins through the command row. Migration pattern: additive-column precedent
`_ensure_position_current_authority_columns` (db.py ~5797 area); register per that pattern
(dossier registry note #4 — confirm the runner registration point before coding).

### 2. Option A ONLY — `venue_order_facts` CHECK widening (BOTH copies, lockstep)

```sql
-- db.py:1373-1377 (world ghost, fingerprint-covered) AND db.py:5267-5271 (trade, authoritative):
state TEXT NOT NULL CHECK (state IN (
  'LIVE','RESTING','MATCHED','PARTIALLY_MATCHED',
  'CANCEL_REQUESTED','CANCEL_CONFIRMED','CANCEL_UNKNOWN','CANCEL_FAILED',
  'EXPIRED','VENUE_WIPED','HEARTBEAT_CANCEL_SUSPECTED',
  'STALE_PENDING_CANCEL'                                   -- NEW
)),
```

`delayed` gets NO CHECK slot (decision 2). Live-DB migration: CHECK widening on an append-only
table via the standard rebuild recipe; fresh DBs pick it up from DDL. Fingerprint: world-copy edit
moves the pin (run `--write-pin`, verify hash changes); trade-copy has no fingerprint coverage —
the lockstep test (decision 3) is the guard.

### 3. Option A ONLY — typed vocabulary + write-gate + reducer

- `VenueOrderStatus.STALE_PENDING_CANCEL = "STALE_PENDING_CANCEL"` (canonical_lifecycle.py:57-65) +
  `_ORDER_STATUS_FOLD["STALE_PENDING_CANCEL"] = VenueOrderStatus.STALE_PENDING_CANCEL` (:169-184) —
  without the fold entry `normalize_venue_order_status` raises on the new string (:202-224).
- `OrderProofClass` gains `STALE_PENDING_CANCEL` (canonical_lifecycle.py:106-118).
- `venue_command_repo._ORDER_FACT_STATES` (:186-199) adds the value — this is the Python write-gate
  `append_order_fact` enforces at :2932; per the HEARTBEAT_CANCEL_SUSPECTED precedent note (:199),
  Python-gate and DB CHECK may sequence independently, gate first.
- `order_truth_reducer.py`: NEW 6th bucket + branch. Priority (justified): terminal_fill >
  terminal_partial > matched>0 > terminal_zero_no_fill > review > unknown > **stale_pending_cancel**
  > open. Stale beats plain open (it is the more specific current truth about a resting order) but
  loses to unknown/review (ambiguous side-effects are scarier than staleness) and to every
  fill/terminal (a fill against a superseded q_version is absorbed as endowment per design doc
  :52-53 — the matched>0 branch already wins, no special case needed).
- `canonical_projections.OPEN_ORDER_FACT_STATES` (:40-49): STALE_PENDING_CANCEL is **NOT added**
  (submits blocked ⇒ not "open for matching bookkeeping" in the dedup sense) BUT it IS
  resting-exposed — add predicate `is_stale_pending_cancel()` and audit the two consumers of the
  open-set (family_exclusive_dedup.py:101,408,412 — stale order must STILL block family dedup,
  since it can fill until cancel confirms; maker_rest_escalation.py:51,59 — W4 deletes it, no
  investment). This split (not-open BUT dedup-blocking) is exactly the "single boolean is
  insufficient" point from the dossier seam #6; the predicate is the resolution.

### 4. BOTH options — `delayed` (derived, no schema)

`is_delayed(command) := state ∈ {SUBMITTING, POSTING, SIGNED_PERSISTED} AND now − state_entered_at
> submit_flight_sla` where the SLA comes from W0.2's measured submit p99 (plan doc W4 row). Lives
beside the staleness predicate; surfaces in monitoring and (W4) REST_ELIGIBLE, never in DDL.

## Blast radius — every state-switching consumer (Option A handling column)

| consumer | site | handling |
|---|---|---|
| order_truth_reducer | buckets :100-121, chain :124-168 | new bucket (above); add unknown-state assertion |
| venue_command_repo._ORDER_FACT_STATES | :186-199 | add value (write-gate) |
| canonical_lifecycle VenueOrderStatus/_FOLD/OrderProofClass | :57-65,:106-118,:169-224 | add member + fold entry |
| canonical_projections.OPEN_ORDER_FACT_STATES | :40-49 | NOT added; new predicate |
| exchange_reconcile._OPEN_ORDER_FACT_STATES | :100-103 | NOT added (already-drifted local set — decision 4); document |
| family_exclusive_dedup | :101,408,412 | verify stale still blocks dedup (test) |
| evaluator._ENTRY_ORDER_FACT_TERMINAL_NO_FILL_STATES | :3162-3230 | NOT terminal — no change; assert by test |
| cycle_runtime raw IN-list | :2140-2163 | NOT terminal — no change; assert by test |
| maker_rest_escalation OPEN_REST_FACT_STATES | :51,59 | no change (W4 deletes) |
| status_summary / substrate_observer / event_reactor_adapter raw SQL | :710-743 / :556-595 / :15035-15115 | fresh grep pass pre-ship (dossier flagged unreviewed slices) |
| CommandState consumers (executor :3298-3356, command_recovery ~45 sites) | — | UNTOUCHED — no CommandState change in either option |

## Orthogonality clause (explicit)

This packet does not add, rename, or re-map any `PositionPhase`/`LifecyclePhase` member
(canonical_lifecycle.py:147-163; alias canonical_projections.py:16) and does not add an order-state
parameter to `derive_position_phase()` (canonical_projections.py:214). A position remains ACTIVE
while a child order is STALE_PENDING_CANCEL; order-plane truth reaches position plane only through
the existing boolean-facts interface.

## Implementation notes

- Atomic patch; if Option B is chosen, delete sections 2-3 and the blast-radius rows they own —
  the packet shrinks to: q_version column + stamping + two predicates + tests.
- K0: manifest diff + schema diff in evidence.
- Do NOT touch tests/test_command_bus_types.py count assertions — their staying green IS evidence.

## Schema-specific questions (template)

- Enum/constraint changed: venue_commands +1 nullable column (both options); venue_order_facts CHECK
  +1 value, VenueOrderStatus/OrderProofClass +1 member (Option A only). CommandState: unchanged.
- Replay/parity evidence: reducer byte-identical replay over existing corpus; fingerprint move proof.
- Append-only/idempotency: venue_order_facts stays append-only (new state is a new fact row, never
  an UPDATE); q_version column is write-once at command creation.
