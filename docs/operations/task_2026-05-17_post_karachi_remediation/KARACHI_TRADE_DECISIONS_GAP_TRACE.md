# Karachi `trade_decisions` Gap — Structural Root Cause Trace

**Date**: 2026-05-17  **Investigator**: tracer agent  **DB**: `state/zeus_trades.db` (read-only)
**Scope**: Identify the K structural decision (Fitz Constraint #1) that allowed `position_current` to be written without a matching `trade_decisions` row, design structural fix + antibody, recommend replay path.

## 1. Live Evidence (verbatim probes against `state/zeus_trades.db`)

```
SELECT COUNT(*) FROM position_current WHERE position_id='c30f28a5-d4e';
-- 1
SELECT position_id, phase, shares, strategy_key FROM position_current WHERE position_id='c30f28a5-d4e';
-- c30f28a5-d4e|day0_window|1.5873|opening_inertia
SELECT COUNT(*) FROM trade_decisions WHERE runtime_trade_id='c30f28a5-d4e';
-- 0
SELECT COUNT(*) FROM trade_decisions WHERE trade_id LIKE '%c30f28a5%';
-- 0
SELECT sequence_no, event_type, source_module FROM position_events WHERE position_id='c30f28a5-d4e' ORDER BY sequence_no;
-- 1|POSITION_OPEN_INTENT|src.engine.cycle_runtime
-- 2|ENTRY_ORDER_POSTED|src.engine.cycle_runtime
-- 3|CHAIN_SYNCED|src.state.chain_reconciliation
-- 4|ENTRY_ORDER_FILLED|src.execution.exchange_reconcile
-- 5|DAY0_WINDOW_ENTERED|src.engine.cycle_runtime
SELECT command_id, intent_kind, state FROM venue_commands WHERE position_id='c30f28a5-d4e';
-- 2f3807c5dc744a32|ENTRY|EXPIRED
```

**Population scope**: 17/76 `position_current` rows lack a matching `trade_decisions` row. ALL 17 share `strategy_key='opening_inertia'` and `entry_method='ens_member_counting'` / `discovery_mode='opening_hunt'`. 15 are `order_status='canceled'`; the two non-canceled cases are Karachi (`c30f28a5-d4e`, partial) and Singapore (`8f02dc01-b6b`, sell_filled — also identified in F20 §2a as pre-rollout). The gap is NOT Karachi-specific.

## 2. Hypothesis Trail

### H1 — `log_trade_entry` silently swallows its own INSERT failure (writer is "best-effort"). CONFIRMED

**Evidence FOR**:
- `src/state/db.py:5916-5974` — `log_trade_entry` wraps the entire INSERT in `try: ... except Exception as e: logging.warning('Failed to log trade entry: %s', e)`. Any sqlite error, FK miss, NOT NULL violation, or attribute-error on `pos` is consumed locally and never propagates to the caller.
- `src/engine/cycle_runtime.py:3505-3527` — the call site holds an outer `sp_candidate_*` SAVEPOINT, calls `log_trade_entry`, then calls `_dual_write_canonical_entry_if_available` which writes `position_current` via `append_many_and_project`. Because `log_trade_entry` consumes its own exception, the SAVEPOINT continues, `position_current` is written, and `RELEASE SAVEPOINT` commits without the trade_decisions row.
- F20 evidence confirms Karachi's canonical entry events (`POSITION_OPEN_INTENT`, `ENTRY_ORDER_POSTED`, `ENTRY_ORDER_FILLED`) all exist — proving the entry path ran end-to-end through `materialize_position` + `_dual_write_canonical_entry_if_available`.
- `src/state/db.py:6171-6189` — `update_trade_lifecycle` silently returns when no prior trade_decisions row exists (`if row is None: return`). All downstream lifecycle updates (fill, exit, settlement) are silently no-op'd after the initial gap, masking the defect indefinitely.

**Evidence AGAINST**:
- No live log line proving the exception fired (logs rotated; `zeus-live.log` last touched 2026-05-15, Karachi entry was 2026-05-16T00:32). The exception class is unknown — but the mechanism is structural and triggers regardless of which exception fires.

**Probes run**:
- `grep -n "INSERT.*position_current\|REPLACE.*position_current" src/ scripts/ -r --include='*.py'` → single writer (`src/state/projection.py:103`).
- `grep -rn "log_trade_entry" src/ --include='*.py'` → single production caller (`src/engine/cycle_runtime.py:3508`).
- `git log -S "Failed to log trade entry"` → original commit `62a51e453f` ("Live safety layer + provenance-aware state architecture"); silent-except is original design, not a regression.

**Verdict**: **CONFIRMED.** The structural decision is "treat `trade_decisions` as best-effort telemetry inside an entry that simultaneously commits authoritative truth to `position_current` + `position_events` + `venue_commands`". Authority direction inversion: one writer is best-effort, all readers (position_lots FK, lifecycle update, F20-class reconciliation) treat trade_decisions as a required bridge.

### H2 — K1 DB split (2026-05-11) lost the linkage. REJECTED

**Evidence FOR**: K1 commits (`eba80d2b9d` 2026-05-11, `2e00271cee` 2026-05-15) created `zeus-forecasts.db` split.

**Evidence AGAINST**:
- `git show 2e00271cee` (commit body) shows K1 reroutes daily-observations and market events, NOT trade_decisions writes. trade_decisions stayed on `zeus_trades.db`.
- `git log -L 5907,5975:src/state/db.py` shows the silent-except `log_trade_entry` body is unchanged since `62a51e453f` (pre-K1).
- The 17-row gap spans across canceled+active positions on `opening_inertia` from 2026-05-15 onwards — co-located with K1 dates but the silent-except mechanism predates K1.

**Verdict**: REJECTED. K1 introduced new DB boundaries but did not move or alter the trade_decisions writer.

### H3 — Cleanup race deleted the trade_decisions row. REJECTED

**Evidence FOR**: F20 mentions cleanup operations.

**Evidence AGAINST**:
- `find scripts/ -name "cleanup*.py"` and `grep -rn "DELETE.*trade_decisions" src/ scripts/ --include='*.py'` produced no production deleter of trade_decisions rows by `runtime_trade_id`. No autoinc gap visible in trade_decisions (highest trade_id contiguous).
- F20 reports 17 lots present and 0 orphan lots when joined via the correct INTEGER bridge. If rows had been deleted, position_lots referencing those trade_id INTs would orphan; they don't.

**Verdict**: REJECTED. No deletion path observed.

### H4 — Schema mismatch: trade_decisions written to a different DB. REJECTED

**Evidence FOR**: K1 introduced DB splits.

**Evidence AGAINST**:
- `for db in state/*.db; do sqlite3 "$db" "SELECT COUNT(*) FROM trade_decisions WHERE runtime_trade_id='c30f28a5-d4e'"; done` → 0 in every DB.
- `src/state/db.py:5961` writer uses `conn` passed in from `cycle_runtime.py:3505` which is the canonical `zeus_trades.db` connection. Other DBs (`zeus-forecasts.db`, `zeus-world.db`) do not define a `trade_decisions` table.

**Verdict**: REJECTED. Single-DB owner, correct DB.

### H5 — opening_inertia uses a different code path that bypasses log_trade_entry. REJECTED

**Evidence FOR**: All 17 gap positions share `strategy_key='opening_inertia'` + `entry_method='ens_member_counting'`.

**Evidence AGAINST**:
- `grep -rn "strategy_key.*opening_inertia\|opening_inertia.*strategy" src/ --include='*.py'` → no opening_inertia-specific entry path. opening_inertia is a label on the same generic cycle_runtime flow.
- `src/main.py:1433` runs `_run_mode(DiscoveryMode.OPENING_HUNT)` which routes through the same `run_cycle` → materialize_position → log_trade_entry sequence.
- Karachi has full canonical events (POSITION_OPEN_INTENT etc.) emitted from `src.engine.cycle_runtime` — proving it followed the standard path.

**Verdict**: REJECTED — opening_inertia clustering is a SYMPTOM, not a separate path. The clustering is likely because the opening_inertia path posts more pending orders that race against the silent-except surface (e.g., snapshot_fk lookup against a forecasts.db boundary, calibration_version coercion, or a transient FK race on ensemble_snapshots during high-volume opening_hunt windows). The cluster anchors the hypothesis but the root mechanism is universal: silent except.

## 3. Convergence / Separation Notes

H1 is the K structural decision. H2/H3/H4/H5 are alternative framings that pre-K1 evidence eliminates. H5's opening_inertia clustering converges to H1 once we view it as "opening_inertia exposes the silent-except more often", not "opening_inertia has its own writer". The opening_inertia clustering is a **load amplifier** of the same H1 defect.

## 4. Rebuttal Round

**Strongest challenge to H1**: "If log_trade_entry silently fails universally, why do only 17/76 positions show the gap? Why does this specific failure mode trigger only on opening_inertia / opening_hunt?"

**Why H1 still stands**: Silent-except does not mean "fails 100% of the time". It means "if it fails for any reason, the failure is invisible and the system continues". Opening_hunt fires at higher cadence with shorter time-to-snapshot lookup, increases the likelihood of a transient `ensemble_snapshots` FK miss (snapshot row not yet committed when log_trade_entry reads), or saves an uncoerced field that fails an INSERT NOT NULL constraint. Whatever the proximate cause, the structural defect is **the exception does not propagate** — that is the antibody-eligible category, not the proximate cause.

## 5. Current Best Explanation (Root Cause Verdict)

**K structural decision (Fitz Constraint #1)**: `trade_decisions` was authored as best-effort entry-side telemetry (`try/except Exception → warning`), but downstream consumers treat it as the authoritative bridge linking the UUID `position_current` row to the INTEGER `position_lots` ledger. This authority-direction mismatch was permissible when `position_lots` did not yet enforce a FK, but became fatal once `position_lots.position_id` was bound to `trade_decisions.trade_id`. The silent-except in `src/state/db.py:5972-5974` and the equally silent `update_trade_lifecycle` early-return in `src/state/db.py:6188-6189` allow `position_current` to ship without a bridge AND mask the defect on every subsequent lifecycle update.

## 6. Structural Fix Shape (NOT a backfill)

Three coordinated edits, all in `src/state/db.py` + `src/engine/cycle_runtime.py`:

1. **Remove the silent-except from `log_trade_entry`** (`src/state/db.py:5972-5974`). Let exceptions propagate to the outer `sp_candidate_*` SAVEPOINT so an INSERT failure rolls BOTH the trade_decisions write AND the position_current/position_events write back together. Atomic-or-nothing: either the bridge is intact or the position is never visible.
2. **Reorder the SAVEPOINT body** at `src/engine/cycle_runtime.py:3506-3522` so `log_trade_entry` runs LAST inside the SAVEPOINT, after `_dual_write_canonical_entry_if_available`. (Optional but defense-in-depth: any future writer added before `log_trade_entry` still atomically rolls back when the bridge insert fails.) Add a single post-write assertion that confirms `trade_decisions.runtime_trade_id` exists for the new `position_id` before `RELEASE SAVEPOINT`.
3. **Promote `update_trade_lifecycle` silent-no-op to a hard failure**: replace `if row is None: return` (`src/state/db.py:6188-6189`) with a raise that fires the moment any fill / exit / lifecycle update encounters a position lacking a bridge. This converts the *masking* surface into a *detection* surface and prevents the gap from accumulating silently.

**No new tables. No new columns.** Pure invariant tightening on the existing money-path writer. Money-path tier files only — no docs/test edits buried under the same packet.

## 7. Antibody Class

**Schema-level antibody** (deployable in the same packet):

```sql
-- INV-NEW: every position_current row must have a trade_decisions row
-- before the row commits. Enforced via a row-level CHECK is not possible
-- across tables in SQLite, so use a BEFORE INSERT/UPDATE TRIGGER:
CREATE TRIGGER position_current_requires_trade_decision
BEFORE INSERT ON position_current
WHEN NEW.phase NOT IN ('voided', 'admin_closed')
BEGIN
  SELECT RAISE(ABORT, 'position_current insert requires matching trade_decisions.runtime_trade_id')
  WHERE NOT EXISTS (
    SELECT 1 FROM trade_decisions WHERE runtime_trade_id = NEW.position_id
  );
END;
```

Pair it with **a relationship test** (`tests/test_position_current_trade_decisions_bridge.py`) that asserts: for any seed `position_current` row with `phase != 'voided'`, removing the matching `trade_decisions` row raises the TRIGGER error on the next upsert. This is the cross-module invariant test mandated by Fitz's "test relationships, not just functions" rule.

**Antibody category promoted to the immune system**: "Best-effort telemetry inside an authoritative-write SAVEPOINT" — every existing `except Exception: logging.warning(...)` inside a `sp_*` SAVEPOINT block is a recurrence candidate. Schedule a one-shot audit (`grep -B5 -A2 "except Exception" src/state/db.py src/execution/*.py src/engine/cycle_runtime.py | grep -B3 -A2 "logging.warning"`) and re-classify each silent-except as either (a) genuinely best-effort + outside any SAVEPOINT, or (b) mandatory + propagate.

## 8. Replay Strategy (no manual operator CLI)

Karachi (`c30f28a5-d4e`) currently has phase=`day0_window`, shares=1.5873, WIN confirmed on-chain. After the structural fix lands and the daemon restarts:

1. **Restart-safety contract**: the new TRIGGER is `BEFORE INSERT` — pre-existing rows are unaffected at boot. No migration is required for the 17 pre-existing gap rows. (The TRIGGER fires only on NEW inserts; the row-level audit can later flag historical gaps without blocking startup.)
2. **Replay mechanism**: Karachi's bridge row gets re-created the next time `update_trade_lifecycle` runs against `c30f28a5-d4e` IF and ONLY IF we add a `_repair_missing_trade_decision_bridge` helper next to step 3 of the structural fix above. Concretely: when the new hard-fail in `update_trade_lifecycle` fires for a pre-existing gap row, invoke a one-shot synthesizer that builds the `trade_decisions` row from `(position_current ⋈ venue_commands ⋈ position_events ⋈ execution_fact)` for that `position_id`. The synthesizer runs through the same `log_trade_entry` writer (now non-silent), so the bridge row is restored atomically inside the lifecycle SAVEPOINT — no operator CLI, no backfill script.
3. **If no replay path exists** (synthesizer not part of the fix packet): that is itself a structural defect to document. The minimum viable structural fix is steps 1+2 only (close the new-position gap); the historical 17 rows can be repaired by a SUBSEQUENT packet that adds the synthesizer as a side effect of the new hard-fail. Karachi specifically remains observable in `position_current` and `position_events` for redeem cascade purposes either way — the redeem path keys off `position_id` UUID and on-chain order_id, not `trade_decisions.trade_id`. So the structural fix is deployable mid-cascade without disturbing the active Karachi settlement.

## 9. Uncertainty Notes

- The exact proximate exception class that fired in `log_trade_entry` for the 17 rows is not recoverable from rotated logs. The structural antibody is correct regardless of which exception class fires.
- Singapore (`8f02dc01-b6b`) was flagged by F20 as "pre-rollout" — its gap may predate the current writer entirely; that is a separate (historical) defect class not material to the live structural decision.
- Karachi safety: the proposed BEFORE INSERT TRIGGER does not touch existing rows. The proposed `update_trade_lifecycle` hard-fail change DOES affect Karachi: it would fire on the next lifecycle update for Karachi. The replay synthesizer in §8.2 is therefore mandatory in the same packet, not a follow-up — without it, the hard-fail bricks Karachi's redeem cascade. **Recommended packet order: TRIGGER + synthesizer + hard-fail, all together. Do not ship hard-fail in isolation.**

## 10. Critical Unknown / Discriminating Probe (post-fix)

**Critical unknown**: which proximate exception fires inside `log_trade_entry` for the opening_inertia cluster. Knowing this would help size the synthesizer's input recovery.

**Discriminating probe** (deployable BEFORE the structural fix, zero risk): replace the `logging.warning(...)` at `src/state/db.py:5973` with a `logger.error("LOG_TRADE_ENTRY_FAILED position_id=%s err=%r", getattr(pos, 'trade_id', '?'), e); raise` for ONE deployment cycle. Watch zeus-live.log for the new ERROR on the next opening_hunt tick that produces a fresh entry. The first observed exception class points directly at the proximate trigger and validates that the structural fix correctly captures it.

