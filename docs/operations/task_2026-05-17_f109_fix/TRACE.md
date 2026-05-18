# F109 — Non-idempotent position-open: trace + structural fix design

**Date**: 2026-05-17
**Branch**: `fix/f109-position-open-idempotency-2026-05-17`
**Worktree**: `.claude/worktrees/fix-f109-position-open-idempotency-2026-05-17`
**Scope**: read-only trace + structural fix (writer + replay + antibody). NO operator-void, NO src/execution/ touches, NO daemon restart.
**Authority**: this doc serves as ARCH_PLAN_EVIDENCE for the capability gate on `canonical_position_write` because the F109 fix tightens that capability's invariants (adds a pre-INSERT idempotency check).

## 1. Live evidence (verbatim probes against `state/zeus_trades.db`)

Probe 1 — multi-row tokens (read-only):
```
SELECT token_id, COUNT(*) FROM position_current GROUP BY token_id HAVING COUNT(*) > 1;
-- 15 tokens have >1 row, but all but ONE have at most 1 OPEN_EXPOSURE_PHASES row
```

Probe 2 — tokens with multiple ACTIVE-phase rows:
```
SELECT token_id, COUNT(*) FROM position_current
 WHERE phase IN ('day0_window','pending_entry','active','pending_exit')
 GROUP BY token_id HAVING COUNT(*) > 1;
-- 113959433546428599583458171463964346033318046435676830124564125503733330054946 | 2
```

Probe 3 — London position lineage (5 rows on one token):
```
position_id   phase                  shares  strategy_key      updated_at
cee5fc85-3dd  voided                 0.0     opening_inertia   2026-05-17T07:46:52
2d08b0ec-b2e  voided                 0.0     opening_inertia   2026-05-17T11:13:35
3a6f0728-c50  economically_closed    5.0     opening_inertia   2026-05-17T20:04:45   (entry+exit completed)
0a0e3b72-46e  pending_exit           6.0     opening_inertia   2026-05-17T22:22:15   (overlap A)
7557a029-4ad  pending_exit           6.0     opening_inertia   2026-05-18T00:37:05   (overlap B)
```

Probe 4 — event ordering for the two overlap rows (excerpt):
```
0a0e3b72-46e  POSITION_OPEN_INTENT  2026-05-17T21:53:07
0a0e3b72-46e  EXIT_ORDER_REJECTED   2026-05-17T22:13:38  (first exit attempt rejected)
0a0e3b72-46e  EXIT_ORDER_POSTED     2026-05-17T22:22:15  (exit posted, MATCHED only)
7557a029-4ad  POSITION_OPEN_INTENT  2026-05-17T22:24:07  (NEW position opened 31 minutes after 0a0e3b72 first entry, while it was still pending_exit)
```

Probe 5 — on-chain truth (latest CHAIN snapshot id=12669, captured 2026-05-18T00:47:09):
```
"113959...054946": 6000000   (6.0 shares total on chain)
```

Probe 6 — both overlap positions claim CONFIRMED entries:
```
command_id        position_id   side  state      filled_size
361d9bd71bbd459f  0a0e3b72-46e  BUY   FILLED     6  (CONFIRMED in venue_trade_facts)
384d1d118ddd4a21  0a0e3b72-46e  SELL  PARTIAL    6  (MATCHED only in venue_trade_facts)
e714db28dff949b6  7557a029-4ad  BUY   FILLED     6  (CONFIRMED in venue_trade_facts)
```

DB sum across open-phase rows = 12; chain truth = 6; overbook = +6.

## 2. K decision (Fitz Constraint #1)

The position-open pipeline mints a fresh `trade_id = str(uuid.uuid4())[:12]` per `execute_intent` / `execute_final_intent` invocation at `src/execution/executor.py:1720` and `src/execution/executor.py:1757`. This freshly minted id becomes `position_id` via `materialize_position` → `Position(trade_id=...)` at `src/engine/cycle_runtime.py:1288-1378`. The writer `upsert_position_current` at `src/state/projection.py:100-138` then performs an `INSERT ... ON CONFLICT(position_id)` — checking ONLY position_id uniqueness, never token-level uniqueness. There is NO upstream or writer-side check for an existing OPEN_EXPOSURE_PHASES row on the same `token_id`.

This is a single structural decision: **position uniqueness is anchored to a per-call UUID, not to (token_id × open-phase) which is the actual real-world invariant**. The defect is not a race condition (London evidence: 31-minute gap between opens) and not a strategy switch (all three London opens share `strategy_key='opening_inertia'`). It is a missing existence check.

## 3. Hypothesis ledger

- **H1 (concurrent race)**: REJECTED. London overlap opens are 31 minutes apart, sequential, not concurrent.
- **H2 (strategy switch)**: REJECTED. All London opens share `strategy_key='opening_inertia'`, `entry_method='ens_member_counting'`, `discovery_mode='opening_hunt'`.
- **H3 (reconciliation creates a fresh row when discovering chain)**: REJECTED. `src/state/chain_reconciliation.py` only reads `phase` and SELECTs from `position_current`; no INSERT path.
- **H4 (voided-row dedup miss)**: PARTIAL. Voided rows are correctly excluded by the open-phase check below; the actual defect is the absence of ANY token-level check.
- **H5 (best-effort writer swallows exception)**: REJECTED. `upsert_position_current` raises on schema violation. The INSERT succeeds because nothing forbids it.
- **H6 (orphan exit advances new row)**: CONFIRMED as the trigger pattern. EXIT for `0a0e3b72-46e` stuck at MATCHED in DB (real on-chain SELL completed; chain dropped from 6→0), then a fresh entry decision was made and minted a NEW position_id (`7557a029-4ad`) because no pipeline stage checked "does token X already have an open position?". The K decision is the missing check, not the stuck exit.

## 4. Structural fix shape (no operator-void)

Three layers of defense:

**Layer 1 — Writer-side existence check (`src/state/projection.py`)**
Before INSERT, if `projection.phase in OPEN_EXPOSURE_PHASES` and a different `position_id` already holds an open-phase row on the same `token_id`, raise `DuplicatePositionOpenError`. The exception propagates to the caller's `sp_candidate_*` SAVEPOINT (`src/engine/cycle_runtime.py:3506-3527`); the ROLLBACK undoes the `log_trade_entry`, `log_execution_report`, and dual-write writes that ran earlier in the SAVEPOINT. Atomic-or-nothing — no orphaned bridge rows. (Advisor 2026-05-17: returning the existing position_id quietly would leave dangling trade_decisions / execution_report rows from this savepoint; raise cleanly instead.)

**Layer 2 — Schema-level hard floor (`scripts/migrations/202605_position_current_idempotent_open_per_token.py`)**
Partial UNIQUE INDEX on `position_current(token_id) WHERE phase IN OPEN_EXPOSURE_PHASES AND token_id IS NOT NULL`. Any race or programmatic bypass that slips past Layer 1 hits an `sqlite3.IntegrityError` on INSERT; the SAVEPOINT rolls back identically. Migration refuses to apply over duplicate data — deploy order requires the consolidator to run first.

**Layer 3 — Programmatic replay consolidator (`src/state/position_duplicate_consolidator.py`)**
For each token with >1 open-phase rows, classify via on-chain truth:
- `db_sum <= chain_shares` → DIVERGENT (legitimate split exposure or chain-snapshot stale) → SKIP, log `[CONSOLIDATOR_DIVERGENT]`.
- `db_sum >  chain_shares` → OVERBOOK → void oldest rows (by first-event occurred_at ASC) until DB collapses to chain truth.

The consolidator is the autonomous replay path mandated by `feedback_no_manual_precedent_for_any_structural_defect`. No operator CLI; no manual void. Per-row void writes a `position_events.ADMIN_VOIDED` audit row alongside the `phase='voided'` update, both atomically inside one SAVEPOINT.

**Antibody (`tests/state/test_position_open_idempotency.py`)**
Relationship tests covering: writer-side raise on TRUE_DUP; UNIQUE INDEX catches sneaked-past-writer race; consolidator OVERBOOK voids oldest; consolidator DIVERGENT skips; consolidator idempotency (second pass is no-op); London-fixture replay.

## 5. Deploy order (mandatory)

1. Consolidator runs (at boot, or invoked just-in-time per token via `consolidate_token`); duplicate rows resolved to chain truth.
2. Migration `202605_position_current_idempotent_open_per_token` applies the partial UNIQUE INDEX (now safe — pre-flight passes).
3. Writer-side check (Layer 1) is the cheap first line of defense; UNIQUE INDEX (Layer 2) is the hard floor.

If Layer 2 fires (i.e., a race slips past Layer 1), the entry transaction rolls back. No partial state. Re-attempt on next cycle will see the existing row and idempotent-return is not offered — the caller must explicitly NO-OP that entry decision (engine layer; out of scope for this packet).

## 6. Karachi safety

Karachi position `c30f28a5-d4e` (token `5391...57884`) has exactly ONE open-phase row (`day0_window`, 1.5873 shares). Token-level duplicate check is a NO-OP for single-row tokens. UNIQUE INDEX is a NO-OP for unique tokens. Consolidator returns immediately (token not in duplicates list). Karachi cascade unaffected.

Verification: see §VERIFICATION_LOG.md.

## 7. London 5/19 replay verification

Expected behavior: consolidator reads chain snapshot (6 shares), sees DB sum = 12 (0a0e3b72 + 7557a029 both 6), classifies OVERBOOK, voids `0a0e3b72-46e` (older by first-event occurred_at), leaves `7557a029-4ad` as the single live row owning the 6 on-chain shares. London 5/19 settles cleanly against the surviving row.

Note: the consolidator does NOT run against the live production DB as part of this PR. The PR ships:
- The migration (deployable)
- The consolidator code (importable)
- The antibody tests (covering replay correctness on synthetic fixtures)

Production replay is operator-initiated via daemon-boot or one-shot script invocation (no manual SQL touches); the consolidator's design guarantees deterministic, audit-logged behavior.

## 8. Hard exclusions verified

- `src/execution/**`: untouched.
- `src/venue/**`: untouched.
- Daemons: not restarted (PR ships code; deploy is operator-mediated).
- Operator-void script: NOT created. The consolidator IS the structural replay path.

## 9. Critical unknown

The `384d1d118ddd4a21` exit command for `0a0e3b72-46e` is in DB state PARTIAL with venue_trade_facts state MATCHED only — never advanced to CONFIRMED in DB, despite chain truth (6 shares were sold). The DB lifecycle update from MATCHED→CONFIRMED for this command is a SEPARATE defect class (exit-confirmation gap); it is NOT in scope for F109 but is the proximate trigger of the duplicate-open. Logging this as F112 (out-of-scope for this PR). The F109 fix neutralizes the duplicate-open category regardless of whether F112 ever fires.
