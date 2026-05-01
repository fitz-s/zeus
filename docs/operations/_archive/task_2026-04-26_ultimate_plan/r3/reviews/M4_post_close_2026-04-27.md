# R3 M4 Post-close Third-party Review — 2026-04-27

Phase: M4 — Cancel/replace + exit safety
Status: POST-CLOSE PASS

## Third-party critic: Chandrasekhar the 2nd

Verdict: APPROVE

Summary:
- M4 can remain `COMPLETE`.
- No blocking evidence requires reopening M4.
- M5/R1 may be unfrozen only after the paired post-close verifier rerun succeeds and repo-facing status docs are updated from verifier-rerun-pending to passed.

Evidence from Chandrasekhar the 2nd:
- M4 is `COMPLETE`, while `_phase_status.yaml` explicitly says post-close third-party review is pending before M5/R1 unfreeze; M5 and R1 remain `PENDING`.
- CAS mutex reacquire is fixed: `ExitMutex.acquire()` uses conditional `UPDATE ... WHERE mutex_key AND command_id AND released_at` and fails closed on stale compare; regression exists in `test_mutex_reacquire_released_row_fails_closed_on_stale_compare`.
- Single active holder is enforced by `exit_mutex_holdings.mutex_key` primary key and active-row check returning false for different command holders.
- `CANCEL UNKNOWN` fails closed: cancel exceptions map to `CancelOutcome("UNKNOWN")`, append existing `CANCEL_REPLACE_BLOCKED`, payload includes `requires_m5_reconcile`, and replacement gate returns `cancel_unknown_requires_m5`.
- Replacement sells are gated before command persistence/SDK construction in `execute_exit_order()`; tests assert no extra command row and SDK is not constructed.
- No new command grammar: command grammar remains `CANCEL_ACKED`, `CANCEL_FAILED`, `CANCEL_REPLACE_BLOCKED`; UNKNOWN is semantic payload, not new `CommandState`.
- `exit_lifecycle` blocks unavailable/missing/failed/unknown cancel paths before replacement; no fail-open replacement found.
- CTF preflight is before command persistence/SDK construction; high pUSD + zero CTF test raises `ctf_tokens_insufficient` and leaves zero venue commands.
- Mutex release is centrally wired only after terminal command states via `venue_command_repo.append_event()` checking `TERMINAL_STATES` before releasing reservation/mutex.
- Docs/status were consistent at critic time: `current_state.md` said `M4 COMPLETE / POST-CLOSE REVIEW PENDING`, M5/R1 frozen pending critic+verifier, and no live venue/cutover/prod DB authorization.
- Fresh verification run by critic: py_compile passed; targeted suite passed `140 passed, 2 skipped, 4 warnings`; R3 drift check for M4 returned `GREEN=10 YELLOW=0 RED=0`; topology navigation matched M4 profile.
- No tracked `state/*.db` changes observed.

Nonblocking risks from critic:
- `exit_lifecycle` may raise rather than gracefully return if a retry tries to re-cancel a command already in `REVIEW_REQUIRED`; this remains fail-closed, not fail-open.
- Live venue cancel/submit remains unexercised by design; tests use fakes/monkeypatches and live activation stays operator-gated.
- M5/R1 should only be unfrozen after the paired verifier pass updates status surfaces from pending to passed.

## Third-party verifier: Halley the 2nd

Initial verdict: BLOCK

Procedural blocker:
- No M4 post-close third-party verifier artifact was present yet.
- Status surfaces consistently said M4 was `COMPLETE` but post-close third-party review was pending; M5/R1 remained frozen.

Evidence from Halley the 2nd:
- `current_state.md` said `M4 COMPLETE / POST-CLOSE REVIEW PENDING`; M5/R1 remain frozen until the required post-close third-party critic+verifier pass completes.
- `_phase_status.yaml` had `M4.status: COMPLETE`, but `critic_review` ended with post-close third-party review pending before M5/R1 unfrozen, and `ready_to_start: []` held M5/R1.
- `M4_work_record_2026-04-27.md` had status `COMPLETE, post-close third-party review pending` and pointed only to the pre-close review artifact.
- `receipt.json` recorded that M4 was marked complete only after pre-close critic+verifier pass and that M5/R1 remained frozen pending post-close third-party review.
- Verifier reran receipt-scoped closeout; result was `ok: true`, `blocking_issues: []`, with only nonblocking warnings.

Remediation now applied:
- Created this post-close review artifact.
- Updated M4 status surfaces to show post-close critic APPROVE and verifier rerun pending.
- M5/R1 remain frozen until Halley rerun returns PASS.

## Verifier rerun conclusion: Halley the 2nd

Final verdict: PASS

Evidence from Halley the 2nd rerun:
- This run was the verifier rerun; pending labels were correct before the verdict and are now authorized to move to PASS.
- Post-close artifact exists and records Chandrasekhar the 2nd APPROVE.
- `current_state.md`, `_phase_status.yaml`, `M4_work_record_2026-04-27.md`, `frozen_interfaces/M4.md`, and `receipt.json` consistently held M4 COMPLETE, critic approved, verifier rerun pending, and M5/R1 frozen until this verdict.
- Receipt-scoped closeout evidence remained sufficient: closeout returned `ok: true`, `blocking_issues: []`.
- R3 drift check evidence remains `GREEN=10 YELLOW=0 RED=0`.
- Targeted pytest/py_compile evidence remains recorded in the work record and receipt.

## Post-close conclusion

- M4 remains COMPLETE.
- M5/R1 may now be unfrozen for phase-entry planning/topology boot.
- R1 still carries inherited Q-FX-1 operator gate.
- Live venue submission, CLOB cutover, M5 reconciliation unblock in production, R1 redeem settlement, credentialed WS activation, and production DB mutation remain unauthorized.
