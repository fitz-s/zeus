# LIFECYCLE STRUCTURAL FIX — STOP AND REPORT
# Created: 2026-05-17
# Authority: MASS_TRIAGE_2026-05-17.md + task brief (LIFECYCLE STRUCTURAL sweep)
# Status: STOPPED — evidence contradicts framing; escalating to orchestrator

---

## 1. Evidence Reconciliation (F106, F108)

### F108 City Correction

Task brief stated "London positions STUCK in pending_exit."

**Live DB evidence contradicts this.** The two positions silent since ~22:13 are:
- `6d8abfb4-b87` — Miami
- `4cd2f9ee-1d1` — Buenos Aires

London positions (`0a0e3b72-46e`, `7557a029-4ad`) are actively retrying every ~7 min.

### Transitions ARE being logged

Both Miami and BA have `position_events` rows with `phase_before=active`,
`phase_after=pending_exit` and event type `EXIT_ORDER_REJECTED`. The transition
pathway is not broken. The events contain payload `exit_state=backoff_exhausted`.

`_query_transitional_position_hints()` reconstructs `exit_state` from these event
payloads at load time — no separate `exit_state` column in `position_current`.

### Dust-hold is by design

The silent positions hit `executable_snapshot_gate: size X is below snapshot
min_order_size 5`. This routes them through `_mark_exit_dust_hold()` (line 412)
which sets `exit_state = "backoff_exhausted"`. By design:

- `check_pending_exits()` (line 1261) filters `backoff_exhausted` — no retry queued
- `check_pending_retries()` (line 1461) returns False for `backoff_exhausted`
- Positions held to settlement; economics closed at resolution

This is the documented dust-hold doctrine. These positions are not stuck due to a
structural defect — they are held intentionally.

---

## 2. F102 Clarification (temp_persistence)

`temp_persistence` table EXISTS in `sqlite_master` (1 entry). The audit claim
"empty/missing" maps to **empty** (0 data rows), not **missing**. No migration
or schema fix required.

---

## 3. Real Structural Smell (for future scope)

Investigation surfaced a genuine structural concern that was NOT the cause of the
sampled stuck positions, but is a latent reliability risk:

`_mark_pending_exit(position)` (in-memory phase mutation) is decoupled from
`_dual_write_canonical_pending_exit_if_available(conn, position, ...)` (DB event
write) by **caller convention, not construction**. Any caller that invokes
`_mark_pending_exit` without the paired dual-write silently leaves `exit_state`
unrecorded in `position_events`.

### Orphan `_mark_pending_exit` call sites (no paired dual-write in same scope)

| Line | Enclosing function | DB write? |
|------|-------------------|-----------|
| 396  | `handle_exit_pending_missing()` | No |
| 414  | `_mark_exit_dust_hold()` | No |
| 750  | `_execute_live_exit()` | No (dual-write follows at 774/796 in later branches) |
| 1286 | `check_pending_exits()` | No |
| 1593 | `_mark_exit_fill_economics_missing()` | No |
| 1611 | `_mark_exit_retry()` | No |

### Paired call sites (dual-write present in same scope)

Lines 585, 616, 647, 722, 774, 796, 925 — all inside `_execute_live_exit()` and
its branch paths.

### Why this hasn't fired (sampled positions)

The dust-hold path (line 414 in `_mark_exit_dust_hold`) has a corresponding
`EXIT_ORDER_REJECTED` event written by the caller, which carries `exit_state` in
its payload. `_query_transitional_position_hints()` reconstructs `exit_state` from
that payload. So the reconstruction works even without a dedicated event at the
`_mark_pending_exit` call site itself.

The paths at lines 396, 1286, 1593, 1611 are higher-risk: if those functions are
reached without a prior event that carries `exit_state` in its payload, the
reconstructed `exit_state` at next load will be stale or empty.

---

## 4. STOP Justification

A unified transition writer that collapses `_mark_pending_exit` +
`_dual_write_canonical_pending_exit_if_available` into a single atomic call
requires modifying `src/execution/exit_lifecycle.py`.

**This file is hard-excluded without orchestrator approval** per task brief:
> "Don't touch src/execution/ unless ABSOLUTELY required by transition writer
> (consult orchestrator via SendMessage before touching)"

Additionally, closing all 6 orphan call sites would require touching
`src/execution/exit_lifecycle.py` at 6 locations — likely exceeding the
"≤2 file edits + 1 test" budget before scope can be confirmed safe.

No code changes were made. No antibody test was written.

---

## 5. Karachi Safety Confirmation

Position `c30f28a5-d4e` confirmed safe:
- Phase: `day0_window`
- Chain state: `synced`
- Last updated: `2026-05-18T00:13:40.068483+00:00`
- No phase transitions proposed or executed in this task

---

## 6. Recommended Next Scope (for orchestrator)

Option A — Accept dust-hold as by-design, close F108 as false alarm, document the
`_mark_pending_exit` orphan pattern as a future hardening item with no immediate
fix needed.

Option B — Authorize touching `src/execution/exit_lifecycle.py` to add a guard
at the 4 high-risk orphan sites (lines 396, 1286, 1593, 1611) that asserts a DB
write follows within the same transaction scope. Estimated: 1 file, ~20 LOC, 1
antibody test.

Option C — Widen the transition writer to also log `exit_state` explicitly in
dust-hold and retry events (not just in the caller's broader event). Lowest risk,
no behavior change, makes reconstruction explicit rather than implicit.

---

*Investigator: Executor agent, 2026-05-17. Evidence base: live zeus-world.db
position_events rows, exit_lifecycle.py line-level grep, semantic_types.py
ExitState enum, lifecycle_manager.py LEGAL_LIFECYCLE_FOLDS.*
