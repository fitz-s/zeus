# EDLI order-lifecycle fixes — #125 (stale active-order lock) + #127 (settlement-honest rest-then-cross)

- Created: 2026-06-15
- Last reused or audited: 2026-06-15
- Authority basis: independent ChatGPT-Pro code review of the live no-fills problem,
  confirmed against live state. Conservative-entry law + K4.0 REST-THEN-CROSS doctrine
  (`docs/operations/consolidated_systemic_overhaul_2026-06-11.md`), operator directive
  2026-06-11 (no forced cross above q_lcb; no new caps/haircuts/throttles).
- Scope: build + test in the isolated worktree only. NOT committed to live, NOT deployed.

Both fixes share `src/engine/event_reactor_adapter.py` and the rest-then-cross policy in
`src/strategy/live_inference/mode_consistent_ev.py`, so they are implemented together.

---

## FIX A (#125) — replace the historical-command lock with a LIVE-ORDER-STATE lock

### The bug

`_locked_live_opportunity_no_price_improvement_reason` (cert-build seam,
`event_reactor_adapter.py`) was a HISTORICAL-COMMAND lock: it suppressed re-bidding the
same `(condition_id, token_id, direction)` whenever ANY past aggregate had reached
`ExecutionCommandCreated` and lacked a `SubmitRejected`, UNLESS the new limit price improved
by `>= improve_delta` (0.02). After a resting maker order's 900s timeout CANCEL (terminal,
unfilled) there is no `SubmitRejected` and usually no 0.02 price move, so the family was
suppressed and NEVER re-bid (live: lone Chengdu 06-17 buy_no @0.72 rested 1c under the 0.73
ask, would time out, never re-decide).

### The fix — live-order-state lock predicate

New function `_locked_live_opportunity_active_order_reason(live_cap_conn, *, condition_id,
token_id, direction, side=None, limit_price=None)`. It derives ACTIVE-vs-TERMINAL from the
`edli_live_order_events` aggregate lifecycle (the live-order-state projection), NOT from
"any historical command that is not a SubmitRejected":

1. Find every order aggregate for the exact `(condition_id, token_id, direction)` keyed off
   its `SubmitPlanBuilt` event (which carries those three identity fields), newest first by
   `occurred_at, event_sequence`.
2. Inspect ONLY the most-recent aggregate. Run a payload-aware terminal-event query:
   - **TERMINAL** (→ **RELEASE**, `return None`; the family re-bids at the fresh price):
     - `SubmitRejected` — venue rejected; order never rested.
     - `UserTradeObserved` — fill confirmed; order is done.
     - `CapTransitioned` with `to_status IN ('CONSUMED', 'RELEASED')` — cap released or
       filled into position; order done.
     - `Reconciled` with `pending_reconcile = False` (or absent) — fully settled; done.
   - **ACTIVE** (→ **SUPPRESS**; return `EDLI_LIVE_ORDER_ACTIVE_DUPLICATE_SUPPRESSED:...`):
     - All other states, including:
       - `CapTransitioned` with `to_status = 'PENDING_RECONCILE'` — cap still RESERVED;
         order MAY still be resting live on the venue (emitted alongside `SubmitUnknown`
         on submit TIMEOUT_UNKNOWN / POST_SUBMIT_UNKNOWN).  NOT terminal.
       - `Reconciled` with `pending_reconcile = True` — matched-pending-finality; NOT
         terminal for re-bid.
       - Any other in-flight / indeterminate state.

The arbitrary 0.02 price-improvement requirement is GONE — duplicate-prevention is now
bound to genuinely ACTIVE orders only, not to historical price levels.

### Lock predicate (exact) — payload-aware terminal-event SQL

```sql
SELECT 1 FROM edli_live_order_events
WHERE aggregate_id = <latest_aggregate_id>
  AND (
    event_type = 'SubmitRejected'
    OR event_type = 'UserTradeObserved'
    OR (event_type = 'CapTransitioned'
        AND json_extract(payload_json, '$.to_status') IN ('CONSUMED', 'RELEASED'))
    OR (event_type = 'Reconciled'
        AND COALESCE(json_extract(payload_json, '$.pending_reconcile'), 0) = 0)
  )
LIMIT 1
```

Row found → TERMINAL → RELEASE.  No row → ACTIVE (or indeterminate) → SUPPRESS (fail-closed).

```
latest_aggregate := most-recent SubmitPlanBuilt for (condition_id, token_id, direction)
if no such aggregate                     -> RELEASE (never planned -> nothing to duplicate)
if terminal SQL query returns a row      -> RELEASE (order confirmed closed)
else                                     -> SUPPRESS (active / pending-reconcile / unknown)
```

The "latest aggregate" rule is what makes Fix A work: after a resting order's terminal
cancel (its aggregate's latest event is `Reconciled` / `CapTransitioned`), the family's
most-recent aggregate is TERMINAL → the lock releases → the family re-bids. A NEWER active
aggregate (a fresh live order) correctly re-suppresses.

### FAIL-CLOSED guarantees

- A SQL/read error at either query → returns
  `EDLI_LIVE_ORDER_STATE_UNREADABLE_FAIL_CLOSED:...` (SUPPRESS). Never risk a double-submit
  when the live state cannot be read.
- An aggregate that exists but carries NO terminal marker (UNKNOWN / indeterminate /
  in-flight) is treated as ACTIVE → SUPPRESS. Never risk a second live order on an order
  that might still be resting on the venue.

### Re-certification is preserved

Releasing the lock only lets the family re-ENTER the pipeline. It does NOT bypass any gate:
the re-decision still runs the full no-submit compile, q_lcb / ΔU / coherence / direction
law, FDR, and the submit-time live-pass (pre-submit revalidation). The lock is a pure
duplicate-prevention guard, nothing more.

### Call sites updated

- `_build_live_execution_command_certificates` (cert-build seam) → calls the new
  live-order-state predicate.
- `_locked_candidate_no_price_improvement_reason` (selector-stage wrapper used by
  `_selected_candidate_proof`) → routes through the same live-order-state predicate.
- The old name `_locked_live_opportunity_no_price_improvement_reason` is retained as a thin
  back-compat alias (accepts and ignores the retired `improve_delta` kwarg) that delegates
  to the new predicate, so any out-of-tree caller resolves to the corrected semantics.

---

## FIX B (#127) — settlement-honest rest-then-cross with a HARD q_lcb cap on every cross

### The bug

The spine posts a maker bid at the conservative q_lcb. When the ask sits above it (Chengdu:
our 0.72 bid vs 0.73 ask) the order rests and times out unfilled (0 fills). Crossing to
0.73 would be WRONG (0.73 > q_lcb 0.72 violates the conservative-entry law). Separately, the
escalation lane (`TAKER_ESCALATED_AFTER_REST`) only checked the spread guard, NOT that the
fresh taker all-in cost clears q_lcb — so an escalated order could SELECT a TAKER that the
downstream cert builder then has to reject with `TAKER_BUY_TOUCH_EXCEEDS_RESERVATION`,
producing a churn loop instead of a clean rest.

### The fix — q_lcb-cap admissibility in `select_rest_then_cross_mode`

A single central gate folded into `taker_admissible` for ALL cross lanes (escalation,
event-end, fleeting, maker-inadmissible). A taker lane is admissible ONLY when:

```
taker_admissible :=
      ev_taker is not None
  AND taker_forbidden_reason is None          (wide-spread guard, unchanged)
  AND taker_all_in_cost is not None
  AND taker_all_in_cost <= q_lcb + 1e-9        (the FIX B HARD CAP)
```

### Cross condition (exact)

```
A taker cross is SELECTED only when a cross lane fires AND
    fresh_taker_all_in_cost <= q_lcb        (conservative bound)
Lanes (each already required taker admissibility; the cap is now part of it):
  - TAKER_ESCALATED_AFTER_REST : rest timed out unfilled + edge re-certified
  - TAKER_EVENT_END_NEAR       : event too near for rest-then-cross to complete
  - TAKER_FLEETING_EDGE        : huge edge near the event end
  - TAKER_MAKER_INADMISSIBLE   : one-sided book, maker structurally impossible
Otherwise -> REST_DEFAULT (post-only maker) / MAKER_TAKER_FORBIDDEN (no trade).
```

### q_lcb-cap proof — no cross can exceed the conservative bound

Two independent walls, both bound to q_lcb / the conservative reservation:

1. **Policy wall (this fix).** A cross lane can only be SELECTED when
   `taker_all_in_cost <= q_lcb`. When the fresh ask all-in exceeds q_lcb (Chengdu
   0.73 > 0.72), every cross lane is inadmissible → the policy returns MAKER
   (`REST_DEFAULT`, or `MAKER_TAKER_FORBIDDEN` on the escalated/one-sided cases). No taker
   is ever selected whose all-in cost is above q_lcb.

2. **Cert-builder wall (pre-existing, kept).** `_build_live_execution_command_certificates`
   prices a marketable taker at the fresh touch, tick-aligns it (BUY rounds up), and raises
   `TAKER_BUY_TOUCH_EXCEEDS_RESERVATION` if the resulting limit `> reservation`
   (= `c_fee_adjusted`, the fee-adjusted conservative bound). A SELL symmetrically raises
   `TAKER_SELL_TOUCH_BELOW_RESERVATION`. So even if a stale path reached here, the submitted
   marketable limit can NEVER execute above the conservative bound.

Composition: the marketable taker fills immediately from certified depth when (and only
when) `fresh ask all-in <= q_lcb`; otherwise it stays maker / no-trade. The Chengdu
0.73 > 0.72 case stays maker / no-trade — a correct outcome, not a forced fill.

### What is NOT loosened

- The wide-spread guard (`TAKER_MAX_RELATIVE_SPREAD = 0.25`) is untouched.
- The REST_DEFAULT doctrine (a favorable all-in alone does NOT license an immediate cross —
  the Karachi antibody: `HEALTHY` book with taker all-in 0.665 <= q_lcb 0.71 STILL rests)
  is preserved.
- No new caps / haircuts / throttles. `edge_lcb > 0`, FDR, coherence, and the direction law
  are untouched. Real taker fee (`0.05*p*(1-p)`) and min-tick are respected (unchanged).
- The gate only makes every taker lane STRICTER (a taker must additionally clear the
  conservative bound), consistent with the conservative-entry law.

### Fail-closed direction

The rest-state flags `(unexpired_family_rest, escalated_after_rest)` are derived from venue
truth (`_family_rest_state`: `venue_commands` + latest `venue_order_facts`), which already
returns `(False, False)` on any query error → the policy RESTS by default (never crosses on
broken provenance). Missing fresh inputs degrade to MAKER. The new q_lcb cap also fails
toward MAKER: a missing/non-finite `taker_all_in_cost` makes the taker inadmissible.

---

## Test results

New / rewritten tests (all pass):

- `tests/strategy/live_inference/test_rest_then_cross_policy.py::TestFixBConservativeQlcbCapOnCross`
  (5 tests): Chengdu ask-above-q_lcb stays maker on escalation and near event-end (never a
  0.73 cross); a fresh all-in clearing q_lcb crosses on escalation and near event-end; a
  one-sided book whose only cross exceeds q_lcb → no trade.
- `tests/money_path/test_edli_live_canary.py` Fix A suite:
  `test_fixA_active_live_order_suppresses_new_submit` (OPEN → suppressed, no price-improve
  escape), `test_fixA_terminal_cancel_releases_lock_for_rebid` (TERMINAL unfilled cancel →
  re-bid allowed at the same price), `test_fixA_unknown_indeterminate_state_fails_closed_suppresses`
  (UNKNOWN → suppress), `test_fixA_no_prior_order_does_not_suppress`,
  `test_fixA_terminal_prior_order_does_not_block_redecision_same_price` (rewritten from
  retired price-improvement test — terminal prior order no longer blocks), plus five new
  payload-variant tests added in the loop-back defect fix:
  - `test_fixA_cap_transitioned_pending_reconcile_suppresses_not_terminal` (RED against
    old bare-type-set code; GREEN after payload-aware SQL fix — the critical regression test)
  - `test_fixA_cap_transitioned_consumed_releases_lock` (CONSUMED → terminal → release)
  - `test_fixA_cap_transitioned_released_releases_lock` (RELEASED → terminal → release)
  - `test_fixA_reconciled_pending_reconcile_true_suppresses` (pending=True → non-terminal → suppress)
  - `test_fixA_reconciled_pending_reconcile_false_releases` (pending=False → terminal → release)

Targeted suites (all pass, 0 new failures):

- `tests/strategy/live_inference/test_rest_then_cross_policy.py` — 23 passed (18 prior + 5 new).
- `tests/money_path/test_edli_live_canary.py` — 55 passed (50 prior + 5 new payload-variant tests).
- `tests/strategy/live_inference/` (full) — all passed.
- `tests/engine/test_rest_then_cross_adapter_seam.py`, `test_final_submit_mode_authority.py`,
  `test_mode_flip_and_recapture_semantics.py` — all passed.

Pre-existing failures (confirmed via clean-tree `git stash` — IDENTICAL set with and without
my changes, so NONE are regressions):

- `tests/engine/` — 166 failed + 8 collection errors. Root cause: `sklearn` is not installed
  in this environment; `src/calibration/platt.py` imports `sklearn.linear_model`, cascading
  through the calibration import chain. Clean-tree diff of `FAILED`/`ERROR` lines: empty in
  both directions (no new regressions, none fixed).
- `tests/money_path/test_edli_online_invariants.py` (19) and
  `tests/money_path/test_finding_b_free_cash_bound.py` (4) — pre-existing (free-cash
  failures called out in the brief; online-invariants are the same sklearn/boot cascade).
  Confirmed identical on clean tree.
- `tests/decision_kernel/test_taker_execution_law.py` (6, `strategy_key missing`) — confirmed
  pre-existing on clean tree.

Syntax verified via `ast.parse` on both modified source files. No Python LSP / `ty` server
available in this environment.

---

## Files changed

- `src/engine/event_reactor_adapter.py` — Fix A: new `_locked_live_opportunity_active_order_reason`
  live-order-state lock + back-compat alias; cert-build and selector call sites repointed.
- `src/strategy/live_inference/mode_consistent_ev.py` — Fix B: q_lcb-cap admissibility gate
  on every cross lane in `select_rest_then_cross_mode`.
- `tests/money_path/test_edli_live_canary.py` — Fix A tests (active/terminal/unknown/no-prior;
  rewrote the retired price-improvement test).
- `tests/strategy/live_inference/test_rest_then_cross_policy.py` — Fix B q_lcb-cap tests.

## Loop-back defect fix (2026-06-15, post-independent-review)

An independent review found a HIGH-severity fail-OPEN in the first version of Fix A:
`_LIVE_ORDER_TERMINAL_EVENT_TYPES` checked bare event-type SET MEMBERSHIP, treating the mere
presence of `CapTransitioned` as terminal → RELEASE, ignoring `to_status`.  But
`CapTransitioned(to_status=PENDING_RECONCILE)` — emitted alongside `SubmitUnknown` on a
submit TIMEOUT_UNKNOWN/POST_SUBMIT_UNKNOWN — means the cap is still RESERVED and the order
MAY STILL BE RESTING LIVE on the venue.  Releasing the lock here enables a double-submit.
Likewise, `Reconciled(pending_reconcile=True)` (matched-pending-finality) is not terminal.

The fix replaces the bare `DISTINCT event_type` set check with a payload-aware SQL query
(`_TERMINAL_EVENT_SQL`) that reads `to_status` from `CapTransitioned` and `pending_reconcile`
from `Reconciled` events.  The `_LIVE_ORDER_TERMINAL_EVENT_TYPES` tuple is removed entirely.
Five regression tests (including the critical RED→GREEN
`test_fixA_cap_transitioned_pending_reconcile_suppresses_not_terminal`) cover all payload
variants.  Full canary suite: 78 passed (23 Fix B + 55 Fix A, including the 5 new).

## Reviewer risk

The two `test_edli_live_canary.py` tests that previously pinned the retired
historical-command + 0.02-price-improvement semantics were REWRITTEN to assert the corrected
live-order-state behavior (terminal prior order no longer blocks a same-price re-decision).
This is an intentional behavior change at the heart of Fix A, not a test-hack: the old
assertions encoded the exact bug (#125).

A reviewer should confirm the payload-aware terminal-event SQL correctly handles every
production CapTransitioned and Reconciled variant — specifically that the `to_status` field
is always present in `CapTransitioned` payloads in production code paths (the test
infrastructure seeds it explicitly; the production event-writer must also set it).  The
fail-closed direction (PENDING_RECONCILE → suppress) means a missing `to_status` would
suppress rather than release — the safe side.
