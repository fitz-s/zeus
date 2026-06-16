# Verifier proof-of-done: rest-then-cross escalation 120->20min + cross partial-fill remainder

- Created: 2026-06-16
- Verifier: verifier (read-only adversarial; no edits to source under verification)
- Commit under test: `9424744b01287e705cc64c943a38e4b40b87be0f` on `live/iteration-2026-06-13` (main tree HEAD)
- Parent (regression baseline): `34e0d516af`
- Root-cause doc read: `docs/evidence/qkernel_rebuild/fill_wall_trace_2026-06-16.md`

## Claim under verification
The day-ahead buy_no harvest forfeited +$88 of settled edge because REST_DEFAULT rests post_only GTC and the cross lanes are structurally unreachable at the 34-50h horizon; the 120min escalation deadline + a `matched<=0` disqualifier meant the rest->cross loop fired once in 436 orders. The fix lowers the escalation deadline 120->20min, drops the `matched<=0` disqualifier so a partial-fill rest crosses its residual, lowers the deadline-horizon fill prior 0.39->0.19, and flips the registry basis MEASURED->DERIVED.

## VERDICT: PASS (all 5 checks)

The three edits match the claim exactly (diff verified). The escalation re-cross loop genuinely closes — re-decision is driven by the UNCONDITIONAL continuous re-emission organ, not by the cancel — so the fix is NOT inert. Disqualifier removal is safe on both sub-claims. Named tests green; money-path regression introduces zero new failures (the 3 reds proven pre-existing on the parent, cold).

---

## CHECK 1 [STATUS: VERIFIED] — Antibody preserved (still rests first; not an immediate cross)

The Denver/Karachi maker-first antibody is intact. `select_rest_then_cross_mode` (`src/strategy/live_inference/mode_consistent_ev.py:438-603`) lane order is unchanged:
- Lane 1 (`mode_consistent_ev.py:560`): `if unexpired_family_rest:` -> HOLD; no new order of EITHER mode while a same-family rest is open. This is the structural block against an immediate re-cross.
- Lane 3 (`mode_consistent_ev.py:570`): `if escalated_after_rest and taker_admissible:` -> cross. Crossing requires a PRIOR rest cancelled UNFILLED >= the escalation deadline (`_family_rest_state` -> `escalated`). There is NO lane that crosses on a favorable all-in alone.
- Lane 6 (`mode_consistent_ev.py:597-603`): `REST_DEFAULT` is unchanged and remains the catch-all default; the in-code doctrine comment "a favorable all-in alone does NOT license an immediate cross — the Karachi antibody" (`:520-521`) is preserved verbatim.

A fresh family with a favorable all-in still RESTS (lane 6), then the rest must sit >= 20min and be cancelled by the escalation job before lane 3 can ever fire. 20min is a shorter wait, not an immediate cross — the rest-first leg is structurally mandatory. The diff touched only the deadline CONSTANT (120->20) and the fill prior (0.39->0.19); it did not add or reorder any cross lane. NOT an immediate cross.

## CHECK 2 [STATUS: VERIFIED] — LIFECYCLE COMPLETENESS (load-bearing): the loop closes

The fix is NOT inert. After the escalation job cancels a rest, the family IS re-decided and lane 3 fires. The re-decision is NOT triggered by the cancel itself (the job is "deliberately DUMB — cancel only", `src/execution/maker_rest_escalation.py:17-26`, `src/main.py:6296-6309`). It is triggered by an independent, UNCONDITIONAL re-emission organ:

- `src/main.py:5839-5871` — continuous re-decision. Comment: "now UNCONDITIONAL when event writing is enabled — this is the fill-rate ORGAN, not an optional feature." Every reactor cycle (~60s) it calls `_edli_emit_forecast_snapshot_events(..., source=<per-cycle distinct>)` over ALL committed market-backed families via a WRAPPING fair cursor (`_EDLI_REDECISION_FAIR_BATCH`), so "a fixed per-cycle batch reaches EVERY family within ceil(N/batch) cycles and NONE is ever dropped."
- The ONLY skip is `already_pending_keys` (`src/main.py:5857`, `_edli_pending_entity_keys` `:6887-6928`) — families with an UNPROCESSED FSR event already queued. It does NOT gate on rest state. A just-cancelled family has no open rest and (once the prior FSR is drained) no pending event, so it re-emits on the next cycle.

End-to-end loop, confirmed statically:
1. Rest posts (lane 6 REST_DEFAULT).
2. While open: `_family_rest_state` returns `unexpired_family_rest=True` -> lane 1 HOLD (no churn). Re-emission still happens but the reactor HOLDs — correct.
3. At 20min the escalation job cancels the remainder (`run_cancels_for_expired_rests`, CANCEL_CONFIRMED).
4. Next reactor cycle re-emits the family (unconditional fair cursor).
5. `_family_rest_state` (`event_reactor_adapter.py:8221-8254`) now reads venue truth: open_fact_states no longer match (rest gone) so `unexpired_rest=False`; the cancelled fact is `CANCEL_CONFIRMED in terminal_unfilled_states` with `(observed_at-created_at) >= deadline_seconds` so `escalated=True`. Returns `(False, True)`.
6. Lane 1 no longer blocks; lane 3 (`escalated_after_rest and taker_admissible`) fires -> `TAKER_ESCALATED_AFTER_REST` cross.

Why the tracer saw "fired once in 436": the OLD path gated on the 120min deadline AND the `matched<=0` disqualifier — both now removed/lowered. The re-emission machinery was never the bottleneck; it has been unconditional since Wave-1 2026-06-12 (`src/main.py:5839`). The fragility was in `_family_rest_state`'s licensing predicate, which is exactly what this commit fixes. The loop closes.

Residual uncertainty (stated honestly): this is a STATIC trace of the re-emission + licensing path; I did not run a live rest->20min->cancel->re-cross end-to-end in production. The static chain is complete and the continuity organ is unconditional, so confidence is high. Recommended live confirmation probe: after deploy, watch `zeus-world.db` decision_certificates for a `rest_then_cross_policy=TAKER_ESCALATED_AFTER_REST` row appearing within ~2-3 cycles of a maker rest CANCEL_CONFIRMED on the same family token (the previously-once-in-436 event should now recur per expired rest).

## CHECK 3 [STATUS: VERIFIED] — Disqualifier-removal safety (both sub-claims hold)

(a) Completed orders never re-cross. `terminal_unfilled_states = ("CANCEL_CONFIRMED", "EXPIRED")` (`event_reactor_adapter.py:8196`) is the ONLY licensing set for `escalated`. A fully-filled order terminates as MATCHED/FILLED (FILLED is a distinct terminal state, `event_reactor_adapter.py:616`), which is NOT in `terminal_unfilled_states`. So a completed order can never license a re-cross. Removing `matched<=0` only admits the CANCEL_CONFIRMED/EXPIRED-after-partial case, never a fully-matched one. VERIFIED.

(b) Residual cross sizes against EXISTING exposure; the partial is not double-counted. The live spine selection path (`q_source=qkernel_spine`, the authority the tracer identified) computes `_spine_selection_exposure = _family_existing_exposure_for_selection_by_bin_id(...)` on EVERY decision (`event_reactor_adapter.py:2518-2534`) and passes it as `extra_exposure_by_bin_id` into BOTH the ΔU selection and sizing. That function (`:9549-9603`) reads `get_open_positions(state)` — COMMITTED open positions with `_runtime_open_exposure_usd` (cost basis), attributed per family bin by `condition_id`. A partial fill is a committed open position with a condition_id, so on the escalated re-decision it is already in `portfolio_state_provider`, enters `extra_exposure_by_bin_id`, and the concave ΔU objective (`_robust_marginal_utility_exposure`, `:8649-8685`) shrinks the residual leg (or forces no-trade). The partial position is netted, not double-counted. VERIFIED.

## CHECK 4 [STATUS: VERIFIED] — Relation + tests green

- `pytest tests/strategy/live_inference/test_rest_then_cross_policy.py tests/execution/test_maker_rest_escalation.py -q` -> **31 passed in 0.94s**. GREEN.
- `TestConstantsProvenance::test_event_end_floor_exceeds_deadline` is green and the constants confirm: `TAKER_IMMEDIATE_EVENT_END_FLOOR_MINUTES = 180.0` (`mode_consistent_ev.py:145`) > deadline `20.0` (`mode_consistent_ev.py:131`). Relation holds with wide margin.
- Registry relation: `test_registry_relation` asserts `maker_rest_escalation_deadline` basis_kind == DERIVED and value == 20/60 h; the registry MUST_EXCEED relation (`time_semantics.py:912-924`, floor held at 3.0h) holds since 3.0h >> 0.33h. The diff updated both the source constant and the registry entry consistently.

## CHECK 5 [STATUS: VERIFIED] — No NEW money-path regression (3 reds proven pre-existing, cold)

- `pytest tests/money_path/ -q` on commit `9424744b01` -> **3 failed, 192 passed**. The 3 failures are exactly `tests/money_path/test_finding_b_free_cash_bound.py::{test_free_cash_provider_binds_stake_to_free_cash, test_free_cash_above_stake_does_not_inflate, test_no_free_cash_provider_is_legacy_no_clamp}` — all the `bankroll cached() -> None: NEVER-FETCHED in this process` harness issue (`bankroll_provider.py:617`), unrelated to this change.
- Pre-existing proof (two independent methods):
  1. Blob identity: `tests/money_path/test_finding_b_free_cash_bound.py` and `src/runtime/bankroll_provider.py` are BYTE-IDENTICAL between parent `34e0d516af` and commit `9424744b01` (same blob hashes `e9ea00bd...` and `d4eed22a...`). The commit touched neither; its file set (`time_semantics.py`, `event_reactor_adapter.py`, `mode_consistent_ev.py` + 2 test files) is disjoint from the failing test's dependencies (no import of the 3 changed files in the failing test).
  2. Cold reproduction: ran `pytest tests/money_path/test_finding_b_free_cash_bound.py` in an isolated `git worktree` checked out at parent `34e0d516af` -> **3 failed, 1 passed** — the SAME 3 tests fail on the parent. (Temp worktree removed after.)
- Conclusion: this commit introduces ZERO additional money-path failures. VERIFIED.

---

## Provenance of code read (audit verdicts)
- `src/strategy/live_inference/mode_consistent_ev.py` — modified by this commit; the 6-lane policy + FIX B q_lcb cap is current law. CURRENT.
- `src/engine/event_reactor_adapter.py` `_family_rest_state` / spine selection / exposure-by-bin — modified by this commit at `:8237`; surrounding selection+exposure machinery last under the qkernel_spine regime. CURRENT_REUSABLE.
- `src/execution/maker_rest_escalation.py` — header `Created 2026-06-10 / audited 2026-06-11`, cancel-only, read-only DB. CURRENT_REUSABLE (own header audit).
- `src/main.py:5839-5871` continuous re-decision organ — Wave-1 2026-06-12, unconditional. CURRENT.
- `src/contracts/time_semantics.py` registry — modified by this commit; DERIVED basis + relation consistent. CURRENT.

## What would still strengthen the proof (not blocking PASS)
- A LIVE end-to-end observation of one rest->20min->CANCEL_CONFIRMED->TAKER_ESCALATED_AFTER_REST cross row in `decision_certificates` (Check 2 was confirmed statically; the re-emission organ is unconditional so the static chain is complete, but production behavior under the real fair-cursor cadence + `already_pending` skip is the one datum not re-run here).
