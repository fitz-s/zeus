# Z1 retrospective — codex — 2026-04-27

## Objective function applied

Maximize R3 live-money upgrade progress while keeping every new venue-side
effect fail-closed, operator-authorized, test-locked, and reversible. For Z1,
that meant adding a narrow CutoverGuard control surface without choosing a live
cutover date or inventing later exchange-reconciliation semantics.

## What changed

- Added `src/control/cutover_guard.py` with explicit cutover states, legal
  transitions, atomic state persistence, and fail-closed read behavior for
  missing/corrupt/unreadable state.
- Required HMAC-signed operator tokens via
  `ZEUS_CUTOVER_OPERATOR_TOKEN_SECRET`; `LIVE_ENABLED` also requires a concrete
  operator evidence artifact.
- Gated `execute_intent`, `execute_exit_order`, and `_live_order` before command
  persistence or Polymarket SDK contact.
- Added cycle-summary observability and entry blocking when CutoverGuard is not
  `LIVE_ENABLED`.
- Added focused tests for state transitions, unsigned-token rejection,
  fail-closed blocking, atomic write preservation, executor pre-side-effect
  blocking, and cycle discovery blocking.

## Rules that mattered

- Topology doctor plus planning-lock shaped the scope: Z1 could add a K1
  control module and targeted K2 call-site gates, but not DB schema, lifecycle
  grammar, M5 exchange findings, or M4/R1 direct cancel/redeem rewiring.
- Pre-close critic+verifier are hard gates. The first critic rejection was
  correct: a non-empty token was not operator authority, and cancel/redemption
  flags were overclaiming without full side-effect wiring.
- Dirty derived artifacts remain out of scope. `.code-review-graph/graph.db`
  stayed excluded from all Z1 closeout gates and must not be staged.

## Rules to carry forward

- When a phase card says "operator token", implement a verifiable signature or
  deliberately narrow the claim before closeout; non-empty strings are not
  authority.
- Separate "decision surface exists" from "all side effects are wired through
  it". For Z2/Z3/Z4 onward, reviewers should challenge any claim that is not
  proven at the actual side-effect call site.
- If a phase has deferred tests, make the skip reason name the owning future
  phase and avoid counting the deferred behavior as implemented.
- Keep `ready_to_start` frozen until post-close third-party critic+verifier
  completes, even after local phase status is marked `COMPLETE`.

## Verification evidence

- `pytest -q -p no:cacheprovider tests/test_cutover_guard.py`: `12 passed,
  2 skipped`.
- Combined focused executor/control suite:
  `tests/test_cutover_guard.py tests/test_executor.py tests/test_live_execution.py
  tests/test_executor_command_split.py tests/test_executor_db_target.py
  tests/test_executor_typed_boundary.py`: `57 passed, 4 skipped`.
- `python3 -m py_compile src/control/cutover_guard.py src/execution/executor.py
  src/engine/cycle_runner.py`: OK.
- YAML parse for topology/source/module/test maps, phase status, and Z1 card:
  OK.
- `r3_drift_check.py --phase Z1`: `GREEN=15 YELLOW=0 RED=0`.
- Topology navigation: `navigation ok: True`, profile `modify cutover guard`.
- Map-maintenance closeout and planning-lock: OK with
  `.code-review-graph/graph.db` explicitly excluded.
- `git diff --check`: clean.
- Pre-close critic: PASS after one required revision.
- Pre-close verifier: PASS / closure-ready.

## Open risk

Z1 does not wire every direct cancel or redemption path through CutoverGuard.
That is intentional and documented; M4/R1 must own those side-effect seams.
M5/T1 must still prove cutover-wipe reconciliation and full fake-venue
simulation before any operational LIVE_ENABLED cutover.
