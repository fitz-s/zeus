# GAP 4 — escalation-cross re-rest race fix (2026-06-21)

- Created: 2026-06-21
- Last audited: 2026-06-21
- Authority basis: docs/evidence/live_order_pathology/2026-06-21_mission_capability_map.md
  (GAP 4) + 2026-06-21_captured_ev_deciding_analysis.md (n=156 no-fills, ~$87/day
  missed admissible captured-EV) + two independent real-chain investigations
  (a3e9a986, a83223e5). Architect cross-check verdict: SOUND-WITH-CAVEATS.
- Branch: fix/escalation-cross-rerest-race-20260621 (base 39e34093).

## The bug (state-precedence / re-rest race)

The engine rests POST_ONLY maker entries 1 tick inside a tight book; ~92% never
fill. An aged unfilled rest is supposed to ESCALATE and CROSS to the ask (taker)
if admissible (FIX-B: `ask + fee <= q_lcb`).

`_family_rest_state` (`src/engine/event_reactor_adapter.py`) scans 24h of venue
truth and returns `(unexpired_family_rest, escalated)`. `select_rest_then_cross_mode`
(`src/strategy/live_inference/mode_consistent_ev.py`) orders the lanes:

- line ~561: `if unexpired_family_rest: HOLD_REST_IN_PROGRESS` (the single-flight ANTIBODY)
- line ~571: `if escalated_after_rest and taker_admissible: TAKER_ESCALATED_AFTER_REST`

A family SERIALLY RE-RESTS (real venue truth: 2–5 sequential ENTRY rests per
family, all cancelled — e.g. token 111133600: 5 cmds / 4 cancelled). A prior
CANCEL_CONFIRMED-unfilled aged rest arms `escalated=True`; a just-posted re-rest
sets `unexpired_family_rest=True`. The UNFIXED loop conflated BOTH into the return
tuple, so `(True, True)` came back and **line 561 HOLD pre-empted line 571's armed
cross**. The cross fired exactly ONCE since 06-19 (one FOK fill, proving the chain
works when state aligns); every other admissible cross was suppressed.

## The fix

### file:line of the precedence change

`src/engine/event_reactor_adapter.py` — `_family_rest_state` body (the loop after
the venue-truth SELECT, ~lines 9605–9686) was restructured from a single pass into
**two passes**:

- **PASS 1 (escalation arming)**: unchanged arming rule, plus it now records
  `escalation_arm_time` = the LATEST arming observation (the cancel/expire
  `observed_at` of an aged cancelled-unfilled rest).
- **PASS 2 (single-flight antibody, with the GAP-4 exemption)**: an OPEN ENTRY
  rest sets `unexpired_rest=True` UNLESS escalation is armed AND the open rest's
  `created_at` is STRICTLY AFTER `escalation_arm_time` — in which case it is a
  redundant SERIAL re-rest and is skipped (does not shadow the armed cross).

No change was needed in `select_rest_then_cross_mode`: with the corrected flags
`(unexpired_rest=False, escalated=True)`, control falls through line 561 and
reaches line 571 (the armed cross), still gated by FIX-B/`taker_admissible`. The
branch ORDER is untouched — only the input that feeds it is corrected at the
source, where the per-row provenance lives.

### (a) re-rest no longer shadows the armed cross

Implemented in PASS 2: the `is_redundant_rerest` test
(`escalated and escalation_arm_time is not None and created_at is not None and
created_at > escalation_arm_time`) drops `unexpired_rest` for a post-escalation
re-rest only. In the race the function now returns `(False, True)`.

### (b) "stop posting rest#2+ while an armed escalation is pending"

NOT implemented as separate machinery — and that is correct, not an omission. Once
an escalation is armed, the policy never returns `REST_DEFAULT` for that family:
it returns `TAKER_ESCALATED_AFTER_REST` (admissible) or, when the fresh taker is
inadmissible, `MAKER_TAKER_FORBIDDEN` with `chosen_ev=-inf` (lane 6a,
mode_consistent_ev.py ~line 609, the existing "no identical re-rest" lane). Both
are non-resting, so the serial re-rest is STRUCTURALLY self-suppressed by (a) plus
the existing policy ordering. An explicit suppressor would be redundant gate-mass
(operator no-gate-accretion law). The architect cross-check independently reached
the same conclusion ("drop part (b) as separate code").

## How each hard constraint is satisfied (code evidence)

### NO FALSE ENTRY — FIX-B preserved at proof AND submit
- The fix does not touch `taker_admissible` (mode_consistent_ev.py:525–532:
  `ask + fee <= q_lcb`). The cross at line 571 is still `escalated_after_rest AND
  taker_admissible`. An inadmissible fresh book → lane 6a no-trade (line 609).
- The submit-time fresh-ask validator `_fresh_rest_then_cross_mode`
  (event_reactor_adapter.py:4291) re-runs `select_rest_then_cross_mode` on the
  FRESH ask with `escalated_after_rest=(proof policy == TAKER_ESCALATED_AFTER_REST)`
  and `unexpired_family_rest=False`. If the fresh book is inadmissible the policy
  rests and `_validate_final_order_mode_or_abort` aborts MODE_FLIPPED. So the
  worked inadmissible example (token 143754634: ask+fee 0.0042 > q_lcb 0.00197)
  cleanly no-trades. Pinned by `test_chengdu_ask_above_qlcb_stays_maker_on_escalation`
  and the new TDD test #2.

### NO DOUBLE-SUBMIT — live-vs-just-cancelled distinction
- The proof-layer HOLD is the FIRST guard; it remains intact for a genuine live
  rest (no armed escalation, or a live rest predating the arm time → `unexpired_rest=True`).
- The AUTHORITATIVE backstop is the executor's own
  `_entry_duplicate_same_token_component` (src/execution/executor.py:698), run
  UNCONDITIONALLY before every live entry submit (executor.py:4413). It BLOCKS when
  a same-token ENTRY command is in `_ENTRY_DUPLICATE_OPEN_COMMAND_STATES`
  (POSTING/POST_ACKED/ACKED/SUBMITTING/PARTIAL/CANCEL_PENDING/REVIEW_REQUIRED/…,
  executor.py:141–156). It admits a new entry only when the competing command is
  in `_ENTRY_DUPLICATE_TERMINAL_NO_EXPOSURE_COMMAND_STATES`
  (REJECTED/SUBMIT_REJECTED/CANCELLED/EXPIRED, executor.py:157–159) with no
  positive trade fact.
- **The one true double-submit hole is RULED OUT.** A venue-live order can NEVER
  have its `venue_commands.state` in the terminal-no-exposure set: the only path
  into `CANCELLED` is `CANCEL_PENDING --CANCEL_ACKED--> CANCELLED`, and
  `CANCEL_ACKED` is written only after a venue-confirmed cancel
  (command_recovery.py:9394–9422: while the venue reports the order "still active"
  the command STAYS `CANCEL_PENDING`, which is in the BLOCKING open set). EXPIRED
  is likewise venue-truth: the EXPIRED writer fires only off a terminal venue
  ORDER FACT with matched_size=0 and no fill trade facts
  (command_recovery.py:4340–4399, `_latest_terminal_order_fact_candidates`). So a
  cross is admitted only once the competing re-rest is genuinely off-book.
- Consequence (honest disclosure): in the race the cross will FREQUENTLY be
  rejected at submit with `duplicate_entry_same_token` while the re-rest is still
  live, and proceed only once it is cancelled-unfilled. That is the intended safe
  ordering, NOT the fix failing — anyone reading logs must not misread those
  rejections.
- Pinned by the new TDD test #3 (`test_genuine_first_live_rest_no_prior_escalation_still_holds`),
  test `test_live_rest_predating_the_arming_cancel_still_holds`, and the existing
  `TestDoubleSubmitSafetyPreserved` policy suite.

### INV-37 — single connection, ATTACH read-only for cross-store reads
- `_family_rest_state` reads ONLY `trade_conn` (zeus_trades); no new connection,
  no cross-store write. The read-only audit script opens zeus_trades via a single
  `file:…?mode=ro` URI (no ATTACH-write, no second connection).

### No over-engineering
- A precedence/ordering correction in the existing two-flag derivation. Reuses the
  existing cross emit (line 571), FIX-B (525), fresh-ask validator (4291), and the
  executor dedup (698). No new shadow/throttle/cap/table/constant.

## RED → GREEN + regression

New TDD tests (`tests/engine/test_rest_then_cross_adapter_seam.py::TestEscalationCrossRerestRace`):
1. `test_armed_escalation_plus_post_cancel_rerest_does_not_hold` — the bug.
2. `test_post_cancel_rerest_with_no_fact_yet_does_not_hold` — ACKED-no-fact re-rest.
3. `test_genuine_first_live_rest_no_prior_escalation_still_holds` — SAFETY (single-flight).
4. `test_live_rest_predating_the_arming_cancel_still_holds` — SAFETY (live order predates arm).
5. `test_no_armed_escalation_open_rerest_still_holds` — SAFETY (no armed escalation → HOLD).

RED (pre-fix): tests #1, #2 fail `assert (True, True) == (False, True)` — the
re-rest shadows the armed escalation. Tests #3–#5 already pass (preserved behaviour).

GREEN (post-fix):
```
tests/engine/test_rest_then_cross_adapter_seam.py
tests/strategy/live_inference/test_rest_then_cross_policy.py  -> 48 passed
```
Regression (excluding pre-existing base failures, see below):
```
test_executor_command_split + test_live_safety_invariants + test_edli_live_canary
+ test_command_recovery + adapter_seam + rest_then_cross_policy + test_dedup_gate_token
-> 463 passed, 1 xpassed
test_dedup_gate_token + mode_flip + fetch_pending_escalation_cross_lane
+ escalation_redecision_emit + maker_rest_escalation + continuous_redecision_emit
-> 149 passed, 1 xpassed
```
PRE-EXISTING failures on the clean base 39e34093 (verified by `git stash -u`):
`tests/decision_kernel/test_taker_execution_law.py` (9 fail) and
`tests/test_runtime_guards.py` (8 fail). They fail identically WITHOUT this change
and are unrelated to `_family_rest_state` (they exercise the cert-builder
`TAKER_QUALITY_PROOF_REQUIRED` path and run_cycle gates). NOT a regression.

## Read-only auditability (which live families would now cross)

`scripts/audit_escalation_cross_rerest.py` (read-only, `file:…?mode=ro`, allowlisted
in `src/state/db_writer_lock.py`). It IMPORTS the real `_family_rest_state` (never
re-implements it, so the audit cannot drift from the live decision logic) and
classifies every recent family by the returned `(unexpired_rest, escalated)`:
`WOULD_CROSS` (reaches the armed-cross lane, still FIX-B gated), `WOULD_HOLD`
(single-flight HOLD), `NEUTRAL`, and `flipped_by_fix` (pre-fix HOLD → post-fix
armed cross via the re-rest exemption).

Run against the PRIMARY live DB:
```
ZEUS_PRIMARY_ROOT=/Users/leofitz/zeus \
  /Users/leofitz/zeus/.venv/bin/python3 scripts/audit_escalation_cross_rerest.py --hours 48
```
Live result 2026-06-21: 54 families scanned, 30 WOULD_CROSS (reach the lane, still
FIX-B gated), 1 FLIPPED by the fix (HOLD → armed cross). The single FLIP is exactly
the bug shape: an armed escalation shadowed by a post-cancel re-rest. The
coordinator must audit the WOULD_CROSS population against the real admissible
no-fill set (cert q_lcb vs fresh ask+fee) before arming — the audit reports the
LANE; FIX-B still gates the actual cross at proof and submit.

## Open risks (brutally honest)

1. **The live-rest vs just-cancelled-rest distinction** is the load-bearing risk.
   The fix moves the proof-time HOLD's authority for the post-escalation re-rest
   onto the timestamp comparison `created_at > escalation_arm_time` PLUS the
   executor dedup. The comparison is fail-CLOSED (null/pre-dating → HOLD), and the
   dedup is the authoritative backstop with NO state gap (proven above). But the
   guarantee is now DISTRIBUTED across three files instead of a single proof-time
   chokepoint. **If anyone ever adds an optimistic-local write into
   `venue_commands.state ∈ {CANCELLED, EXPIRED}` before venue confirmation, the
   hole opens silently.** Mitigation already true today (no such write exists);
   recommended antibody: a CI test asserting no code path writes a terminal-no-
   exposure command state without venue-confirmed evidence (the transition table
   has no optimistic edge into CANCELLED/EXPIRED).
2. **Submit-time rejection noise**: the cross is frequently rejected
   `duplicate_entry_same_token` while the re-rest is still live (expected, safe).
   Operators reading logs must not mistake it for the fix failing.
3. **PARTIALLY_MATCHED post-escalation re-rest**: treated like any open row — if it
   post-dates the arm it is exempted at the proof layer, but the executor dedup
   (PARTIAL ∈ open set) still BLOCKS the cross at submit, and the arming-rest
   re-cert sizes against existing exposure. No double-count, but the cross simply
   waits until that partial terminates — acceptable.
4. **Clock skew between command `created_at` and fact `observed_at`** could in
   theory mis-order a re-rest vs the arm time. Both are venue/DB timestamps written
   by the same process lineage; the fail-closed direction (ambiguity → HOLD) bounds
   the downside to "keeps suppressing the cross", never "reopens double-submit".
