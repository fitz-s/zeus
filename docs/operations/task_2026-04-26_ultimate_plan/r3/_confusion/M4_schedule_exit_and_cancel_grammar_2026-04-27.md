# M4 confusion — schedule_exit seam and cancel grammar planes

Status: RESOLVED
Phase: M4 — Cancel/replace + exit safety
Date: 2026-04-27

## Trigger

- CC-5: M4 slice card references `src/execution/exit_triggers.py::schedule_exit`, but current code has no `schedule_exit` symbol. Exit actuation flows through `src/execution/exit_lifecycle.py::execute_exit` and `src/execution/executor.py::execute_exit_order`.
- CC-8/CC-11: M4 acceptance wording uses `CANCEL_CONFIRMED` and `CANCEL_UNKNOWN`, while M1 closed command grammar already contains `CANCEL_ACKED`, `CANCEL_FAILED`, `CANCEL_REPLACE_BLOCKED`, and command state `CANCELLED`; adding new command grammar would violate M4's own stop condition unless planned separately.

## Evidence

- `src/execution/exit_triggers.py` is signal-only; it evaluates exit triggers and does not schedule live submits.
- `src/execution/exit_lifecycle.py` owns live exit actuation and previously had an untyped stale-cancel retry path.
- `src/execution/command_bus.py` and `src/state/venue_command_repo.py` already define closed cancel event/state grammar.
- M4 card says no new command states/events should be added unless explicitly planned.

## Resolution applied

- Retargeted M4 integration to the actual live-money seams: `exit_lifecycle`, `executor`, and `venue_command_repo`.
- Implemented `src/execution/exit_safety.py` as a typed semantic surface without adding command grammar:
  - `CancelOutcome(status="CANCELED")` maps to existing `CANCEL_ACKED` event and command state `CANCELLED`.
  - `CancelOutcome(status="NOT_CANCELED")` maps to existing `CANCEL_FAILED` event and command state `REVIEW_REQUIRED`.
  - `CancelOutcome(status="UNKNOWN")` maps to existing `CANCEL_REPLACE_BLOCKED` event with payload `semantic_cancel_status="CANCEL_UNKNOWN"` and `requires_m5_reconcile=true`.
- M5 remains the only owner of proving absence/unblocking unknown cancel outcomes.

## Scope guard

This resolution does not implement exchange reconciliation, live cutover, production DB mutation, or new command grammar.
