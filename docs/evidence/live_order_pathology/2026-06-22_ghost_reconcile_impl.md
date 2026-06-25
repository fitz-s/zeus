<!--
Created: 2026-06-22
Last audited: 2026-06-22
Authority basis: Wellington ad064baf never-submitted-ghost reconcile (live_order_pathology 2026-06-22)
-->
# Abandoned never-submitted EDLI ghost reconcile — implementation evidence

**Date:** 2026-06-22
**Authority basis:** Wellington `ad064baf` never-submitted-ghost reconcile (live_order_pathology 2026-06-22)
**Mode:** STRICT TDD (failing test first, then minimal implementation). No deploy, no `state/*.db` writes, no merge by this agent.

## 1. Problem (diagnosis confirmed against source)

A live decision can build an EDLI order aggregate all the way to `ExecutionCommandCreated` (the executor accepted the command internally) and then be interrupted — daemon restart / SQLite lock — **before** the venue submit. The aggregate stalls with no subsequent event:

- latest event = `ExecutionCommandCreated`, no `VenueSubmitAttempted` / `SubmitRejected` / `SubmitUnknown` / ack / user event / `Reconciled`;
- `edli_live_order_projection.venue_order_id IS NULL`;
- zero `venue_commands` rows for its `execution_command_id` → the order **never reached the venue, $0 at risk**.

Such a ghost is **non-terminal** per `event_reactor_adapter._TERMINAL_EVENT_SQL` (`src/engine/event_reactor_adapter.py:6178`), so `_locked_live_opportunity_active_order_reason` (`:6270`) treats it as an ACTIVE order and **permanently suppresses every new submit on the same weather family** (the lock keys on `family_id` OR `(city,target_date,metric)` OR `(condition_id,token_id,direction)`, with **no direction filter** on the family-key path — a stuck buy_YES ghost blocks a live buy_NO on the same family forever).

No existing reconcile terminalizes it:
- the SUBMIT_UNKNOWN reconcile `_reconcile_edli_pending_no_order_if_proven` (`command_recovery.py:7676` pre-change) requires `pending_reconcile=1` (a `SubmitUnknown` the ghost never reached);
- `append_reconciled` (`src/events/live_order_reconcile.py:147`) requires `RECONCILE_SOURCE` venue truth a never-submitted order has none of.

### Every brief claim was verified in source before any code was written
- `_validate_event_append` for `SubmitRejected` (`live_order_aggregate.py:429-436`): requires a preceding `ExecutionCommandCreated`; requires `VenueSubmitAttempted` **only if** `not _is_pre_submit_rejection_payload(payload)`; requires `_require_command_binding`; requires a non-empty `reason_code`/`reject_reason`. → a **pre-submit** `SubmitRejected` is a legal terminal **directly after** `ExecutionCommandCreated`.
- `_is_pre_submit_rejection_payload` (`:621-626`): `pre_submit_rejection is True` AND `submit_status == "PRE_SUBMIT_ERROR"` AND `venue_call_started is False`.
- `_require_command_binding` (`:491-499`): payload `event_id`, `final_intent_id`, and (if present) `execution_command_id` must match the `ExecutionCommandCreated` payload.
- event_id-drift guard (`:132-134`): a later event's `event_id` must equal the aggregate's first event_id.
- `edli_live_order_events` is **append-only** (UPDATE/DELETE triggers, `edli_live_order_events_schema.py:149-163`) → terminalize by **appending**, never UPDATE.
- `_TERMINAL_EVENT_SQL` treats any `SubmitRejected` row as TERMINAL (no payload condition) → appending the pre-submit `SubmitRejected` releases the family lock.
- `LiveCapLedger.release(usage_id)` (`live_cap.py:158-177`) flips `reservation_status` to `RELEASED`; the cap row is keyed by `usage_id` and carries `execution_command_id`.

The legal payload shape was taken **verbatim** from the existing anchor test `tests/events/test_live_order_aggregate.py::test_pre_submit_rejected_terminates_without_venue_submit_attempt` (lines 185-209) — not guessed.

## 2. Files changed (with line ranges)

### `src/execution/command_recovery.py`
- **Line 39** — import: `from datetime import datetime, timedelta, timezone` (added `timedelta`).
- **Lines 7405-7726** — new block inserted before `reconcile_stale_intent_created_no_submit`:
  - `7447` — module constant `_ABANDONED_GHOST_GRACE_SECONDS = _SAFE_REPLAY_MIN_AGE_SECONDS` (reuses the existing 900s safe-replay grace at `:136`).
  - `7452-7462` — module constant `_GHOST_DISQUALIFYING_EVENT_TYPES` (the venue/user/later-terminal/cap events that prove the aggregate progressed past the never-submitted boundary).
  - `7464-7528` — **finder** `_abandoned_unsubmitted_ghost_candidates(conn, *, events_ref, projection_ref, cutoff_iso) -> list[dict]` (read-only).
  - `7530-7642` — **reconcile action** `_terminalize_abandoned_unsubmitted_ghost(...) -> bool` (append pre-submit `SubmitRejected` + release cap + rebuild projection).
  - `7644-7726` — **pass entry point** `reconcile_abandoned_unsubmitted_ghosts(conn, *, updated_before=None) -> dict` (per-candidate SAVEPOINT, returns `{scanned, advanced, stayed, errors}`).
- **Lines 9876-9883** — wired into the legacy caller-owned-conn lane `_reconcile_passes_inline` (right after `reconcile_stale_intent_created_no_submit`).
- **Lines 10247-10250** — wired into the scheduled short-connection lane `_reconcile_passes_short_conn` as `_db_pass("abandoned_unsubmitted_ghosts", reconcile_abandoned_unsubmitted_ghosts, "abandoned_unsubmitted_ghosts", updated_before=started_at)`.

### `tests/conftest.py`
- One-line addition to `_WLA_RESIDUAL_ALLOWLIST` for `scripts/reconcile_wellington_zombie_2026_06_22.py` (operator one-shot, dry-run default, RW only on `--commit`, safety-checked; daemon never imports). **Why:** that committed one-shot has a direct `sqlite3.connect()` at line 85 that the collection-time writer-lock antibody flags repo-wide, which **blocked ALL test collection** in the worktree. Cited reason matches the existing `scripts/build_ft_staging_db.py` operator-one-shot pattern. (Flagged to orchestrator; revert if preferred.)

### `tests/execution/test_abandoned_unsubmitted_ghost_reconcile.py` (new)
File-header provenance present.

## 3. How it works

**Finder** (`_abandoned_unsubmitted_ghost_candidates`): joins `edli_live_order_projection` to its `ExecutionCommandCreated` event and selects rows where `current_state = 'EXECUTION_COMMAND_CREATED'` AND `last_event_type = 'ExecutionCommandCreated'` AND `venue_order_id IS NULL` AND the command event `occurred_at < cutoff` (grace) AND `NOT EXISTS` any `_GHOST_DISQUALIFYING_EVENT_TYPES` event on the aggregate. Then, per candidate, it applies the **venue-truth guard**: skip if any `venue_commands` row links to the `execution_command_id` (`decision_id` key — the canonical EDLI↔venue_commands link), and skip if the command event has no `execution_command_id`.

**Reconcile** (`_terminalize_abandoned_unsubmitted_ghost`, inside a SAVEPOINT): re-reads the `ExecutionCommandCreated` payload for binding fields, re-checks the projection under the write lock (final venue-truth guard: still `EXECUTION_COMMAND_CREATED`, still `venue_order_id NULL`), appends the pre-submit `SubmitRejected` via `_append_edli_event_qualified` (the established recovery write path used by `_reconcile_edli_pending_no_order_if_proven`, writing to the ATTACHed `world.*` refs), releases the still-`RESERVED` cap row via direct `UPDATE … SET reservation_status='RELEASED'` + deletes `day_slots`/`rate_window`, then rebuilds the projection via `_rebuild_edli_projection_qualified`.

### Exact pre-submit `SubmitRejected` payload appended
```python
{
    "schema_version": 1,
    "event_id": <ExecutionCommandCreated.event_id>,             # _require_command_binding
    "final_intent_id": <ExecutionCommandCreated.final_intent_id>,# _require_command_binding
    "execution_command_id": <ExecutionCommandCreated.execution_command_id>,  # _require_command_binding
    "execution_receipt_hash": <carried-through or "">,
    "reason_code": "ABANDONED_UNSUBMITTED_GHOST_RECONCILE",       # required non-empty
    "submit_status": "PRE_SUBMIT_ERROR",                          # _is_pre_submit_rejection_payload
    "venue_call_started": False,                                  # _is_pre_submit_rejection_payload
    "venue_ack_received": False,
    "pre_submit_rejection": True,                                 # _is_pre_submit_rejection_payload
    "proof_class": "command_created_never_submitted_no_venue_presence",
    "required_predicates": {...},
    "reviewed_by": "command_recovery",
    "cleared_at": <occurred_at>,
}
```
`source_authority="existing_executor"` (a value the `edli_live_order_events` `source_authority` CHECK constraint accepts — note the one-shot script `scripts/reconcile_wellington_zombie_2026_06_22.py:151` uses `"manual_operator_reconcile"`, which the CHECK constraint **rejects**; flagged to orchestrator).

This payload's legality is **proven independently** of the qualified-write bypass by `test_reconcile_payload_is_legal_per_production_validate_event_append`, which replays the exact appended payload through the production `LiveOrderAggregateLedger.append_event` (running `_validate_event_append`) and confirms acceptance.

### `_require_command_binding` / `_is_pre_submit_rejection_payload` requirements satisfied
- `_is_pre_submit_rejection_payload`: `pre_submit_rejection=True`, `submit_status="PRE_SUBMIT_ERROR"`, `venue_call_started=False` → no `VenueSubmitAttempted` required.
- `_require_command_binding`: `event_id` / `final_intent_id` / `execution_command_id` copied from the aggregate's `ExecutionCommandCreated` payload (re-read at reconcile time).
- non-empty `reason_code` set.

## 4. Wiring into boot recovery

`reconcile_unresolved_commands` (`command_recovery.py:9460`) is the single recovery entry, dispatching to:
- **scheduled lane** `_reconcile_passes_short_conn` (when `conn is None`) — the `_edli_command_recovery_cycle` scheduler job (`src/main.py:5848`), which runs periodically **and at boot**. My pass added at `:10247`.
- **legacy/per-cycle lane** `_reconcile_passes_inline` (when caller owns `conn`) — `src/engine/cycle_runner.py:805`. My pass added at `:9876`.

Both lanes now run `reconcile_abandoned_unsubmitted_ghosts`, so a daemon restart self-heals any ghost it created on its way down. DB-only pass (no venue client).

## 5. Test results

`tests/execution/test_abandoned_unsubmitted_ghost_reconcile.py` — 8 tests, all GREEN (failed first with `ImportError` for the missing function, per TDD red):

1. `test_reconcile_terminalizes_aged_never_submitted_ghost` — finder picks up an aged ghost; projection → `SUBMIT_REJECTED`, cap → `RELEASED`, `_TERMINAL_EVENT_SQL` now returns a row (duplicate lock releases).
2. `test_appended_terminal_is_legal_pre_submit_submit_rejected` — the appended terminal is a `SubmitRejected` with the pre-submit markers + command binding + non-empty reason_code.
3. `test_fresh_ghost_within_grace_is_not_terminalized` — a ghost younger than the grace is left at `EXECUTION_COMMAND_CREATED`, cap stays `RESERVED`.
4. `test_ghost_with_venue_submit_attempt_is_never_terminalized` — venue-truth guard (a `VenueSubmitAttempted` exists) → never terminalized.
5. `test_ghost_with_venue_order_id_in_projection_is_never_terminalized` — venue-truth guard (projection `venue_order_id` set) → never terminalized.
6. `test_ghost_with_venue_commands_row_is_never_terminalized` — venue-truth guard (a `venue_commands` row for the `execution_command_id`) → never terminalized.
7. `test_no_ghosts_is_a_clean_noop` — empty DB → `{scanned:0, advanced:0, stayed:0, errors:0}`.
8. `test_reconcile_payload_is_legal_per_production_validate_event_append` — replays the appended payload through the real ledger's `_validate_event_append`; accepted.

```
8 passed in 1.65s
```

### Regression check (existing suites kept green)
```
tests/execution/test_abandoned_unsubmitted_ghost_reconcile.py  8 new
tests/test_command_recovery.py + tests/events/test_live_order_aggregate.py  → 166 passed in 10.12s
tests/execution/test_venue_sync_contract.py + test_maker_rest_escalation.py + test_unknown_side_effect.py + test_edli_absence_resolver_boot.py + tests/money_path/test_edli_online_invariants.py  → 122 passed in 6.91s
```
The only failure observed in `tests/execution/` (`test_redeem_pivot_antibody.py::test_scheduler_job_calm_skips`) is **pre-existing and unrelated** — it references `src.main._redeem_submitter_cycle`, a symbol removed by commit `b55a215d` ("redeem: make submission UNCONSTRUCTABLE"); my diff does not touch `src/main.py`.

## 6. Money-path safety summary

- **Never terminalizes venue presence**: triple guard (projection `venue_order_id IS NULL`, no `venue_commands` row for the `execution_command_id`, none of the venue/user/terminal/cap events) — checked in the finder AND re-checked under the write lock in the reconcile action (fail closed).
- **Append-only respected**: terminalize by appending a `SubmitRejected` event; never UPDATE/DELETE the immutable event log.
- **Cross-DB discipline (INV-37)**: writes go through the recovery conn's ATTACHed qualified refs (`world.*`) under a per-candidate `SAVEPOINT`/`RELEASE`/`ROLLBACK TO`, identical to the established `_reconcile_edli_pre_venue_unknown_thresholds` pattern.
- **Grace window**: only ghosts older than the existing 900s safe-replay grace are eligible — never races a still-completing submit.
- **No deploy / no `state/*.db` writes by this agent.** Orchestrator owns verification, the real-capital deploy, and the daemon restart that terminalizes the live `ad064baf` ghost.
