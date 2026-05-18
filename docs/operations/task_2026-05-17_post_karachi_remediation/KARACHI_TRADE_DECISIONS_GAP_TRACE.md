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

