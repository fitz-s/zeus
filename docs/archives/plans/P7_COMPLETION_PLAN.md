# P7 Migration Completion Plan

Version: 2026-04-06
Status: **DRAFT — requires Fitz approval**
Authority: AGENTS.md §3 (planning lock required for truth surface changes)

---

## §0 The design failure

P7 declared canonical truth surfaces (`position_events` + `position_current`) as THE authority (INV-03, INV-08). But:

1. **Entry dual-write silently fails** — `except RuntimeError` at `cycle_runtime.py:260` swallows all canonical write failures at DEBUG level
2. **Exit lifecycle was never wired** — `exit_lifecycle.py` has zero canonical writes
3. **`outcome_fact` and `execution_fact` have 0 rows** — P4 learning spine never populated
4. **`settlement_edge_usd` is not P&L** — it hides early-exit losses by writing 0

Result: the system declared canonical authority, created the tables, wrote the functions — then ran on legacy JSON for 100% of its operational life. No monitoring detected this because the failure was silent.

This is 1 structural decision: **make the canonical path the ONLY path, or admit it doesn't exist yet.**

---

## §1 Structural decision

**Option A: Complete the canonical path (make it work)**
- Wire all lifecycle phases into canonical writes
- Backfill missing data
- Remove legacy fallback after verification
- Risk: large scope, touches every lifecycle phase

**Option B: Demote canonical to aspirational, formalize legacy as truth (acknowledge reality)**
- Accept `positions-paper.json` + `trade_decisions` as the current truth
- Remove the `stale_legacy_fallback` detection that causes confusion
- Keep canonical tables for future migration
- Risk: loses the P1 architecture promise

**Recommended: Option A, but in 3 bounded phases, not one big bang.**

---

## §2 Three phases

### Phase 1: Stop the bleeding (immediate, 1 agent)

**Goal:** Canonical writes work again for new entries. No backfill yet.

1. **Fix silent swallowing:** `cycle_runtime.py:260` — change `logger.debug` → `logger.error` for `RuntimeError`
2. **Diagnose the actual RuntimeError:** Run `assert_canonical_transaction_schema()` against live DB, find what's wrong
3. **Fix the schema if needed:** Align `position_events` columns with `CANONICAL_POSITION_EVENT_COLUMNS`
4. **Verify:** Next cycle's entry writes to BOTH `trade_decisions` AND `position_events`/`position_current`
5. **Add staleness monitor:** If `trade_decisions` count > `position_current` count by >2 for >1 hour, log ERROR

**Files touched:** `cycle_runtime.py` (1 line log level), possibly `db.py` schema migration
**Test:** `position_current` gets a new row on next trade entry

### Phase 2: Wire exit lifecycle (1-2 agents)

**Goal:** Exit events flow through canonical path. `position_current.phase` can reach `pending_exit` / `economically_closed` / `settled`.

1. **Add canonical dual-writes to `exit_lifecycle.py`:** EXIT_INTENT, EXIT_ORDER_POSTED, EXIT_ORDER_FILLED, EXIT_VOIDED events
2. **Add canonical dual-write to harvester settlement path** (already exists but guards on entry events existing — will work after Phase 1)
3. **Add canonical dual-write for monitor refresh** (position monitoring updates phase)
4. **Test:** Position enters → monitors → exits → settles, all events in `position_events`, phase progresses in `position_current`

**Files touched:** `exit_lifecycle.py`, possibly `harvester.py`, `cycle_runtime.py` monitor section
**Test:** Full lifecycle in `position_events` for one position

### Phase 3: Backfill + fact tables + cleanup (1-2 agents)

**Goal:** Historical data complete, fact tables populated, legacy fallback removable.

1. **Backfill 37 missing entries** into `position_events`/`position_current` from `trade_decisions`
2. **Backfill exit/settlement events** from `position_events_legacy` (99 events) into canonical format
3. **Populate `outcome_fact`** from settled `trade_decisions` + `settlements`
4. **Populate `execution_fact`** from `trade_decisions` fill data
5. **Verify `query_portfolio_loader_view()` returns `status: "ok"`** — no more fallback
6. **Rename `settlement_edge_usd`** → document or add `realized_pnl_usd` column
7. **Add P&L reconciliation test:** canonical P&L == legacy P&L for all settled positions

**Files touched:** backfill scripts, `db.py` fact population, test files
**Test:** `portfolio_truth_source: "position_current"` (not fallback)

---

## §3 What this does NOT include

- Deleting legacy surfaces (P7 M4 — deferred behind reality-alignment gates, per FINAL spec)
- Changing how riskguard computes P&L (it will naturally use canonical source once fallback is unnecessary)
- Restructuring the portfolio loader (it already has the canonical path — just needs working data)

---

## §4 Exit condition

P7 completion closes when:
1. `query_portfolio_loader_view()` returns `status: "ok"` (not fallback)
2. `position_events` has entries for ALL active positions (not just 12/49)
3. `position_current.updated_at` is within 1 cycle of latest `position_events_legacy` timestamp
4. `outcome_fact` row count matches settled positions count
5. New positions go through full lifecycle in canonical path: entry → monitor → exit → settlement
6. Legacy fallback path is not triggered in 7 consecutive days of operation

---

## §5 Team plan

| Phase | Agents | Model | Scope |
|---|---|---|---|
| 1 | 1 (`p7-restore`) | opus | Fix silent failure + diagnose schema + verify new entries write canonical |
| 2 | 1 (`p7-exit-wire`) | opus | Wire exit lifecycle canonical writes |
| 3 | 2 (`p7-backfill` + `p7-verify`) | sonnet + opus | Backfill historical data + verify + reconcile |

Phase 1 can start immediately. Phase 2 depends on Phase 1. Phase 3 depends on Phase 2.
