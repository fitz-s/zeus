# Task #132 — Submit-recapture spurious SUBMIT_ABORTED_PRICE_MOVED

- Created: 2026-06-16
- Last audited: 2026-06-16
- Authority basis: docs/evidence/settlement_guard/dead_order_lane_per_token_book_storm_2026-06-16.md
  (POST-DEPLOY section) + operator guardrails (NEVER bypass, NEVER widen 30s window,
  NEVER force a submit).

## Problem

Post-deploy of the batch book fix (task #131, commit 9276367053), the daemon restarted
clean at 16:03. The forecast spine correctly selected +edge candidates every cycle, but
EVERY selected candidate aborted at submit-time with:

    reason=SUBMIT_ABORTED_PRICE_MOVED:recapture failed: no fresh executable snapshot;
    fail closed (§13 'Snapshot stale and recapture fails')

158 of 174 SUBMIT_ABORTED_PRICE_MOVED on 2026-06-16 are this exact failure. Zero forecast
families decided (processed=0 in reactor logs). The abort text put the decision lane and
its events in an indefinite transient-requeue loop.

## Classification

**Defect class (b) — capture mechanism failure.** The snapshot IS fresh and valid. The
market HAS an executable price. The abort is spurious; the snapshot is not stale; the book
has not moved.

## Root cause

`qkernel_spine_enabled: true` is active on the live main tree. When the spine selects a
candidate, the bridge calls `_overlay_spine_economics_onto_proof` (qkernel_spine_bridge.py:
1130) to write the spine's economics onto the reactor proof before passing it to the submit
pipeline. The overlay sets:

    proof.q_posterior = float(selected.q_dot_payoff)   # payoff-space quantity
    # q_lcb_5pct was NOT overlaid — stayed at original probability-space value

`q_dot_payoff` is `q @ payoff` — a payoff-weighted Arrow-Debreu fair value, NOT a raw
probability. For the dominant trade class (neg-risk buy_no at price ≈ 0.002):

- `q_lcb_5pct ≈ 0.990`  (5th-percentile raw NO probability lower bound, high near 1.0)
- `q_dot_payoff ≈ 0.052`  (payoff-weighted EV ≈ q_no × 1 − cost ≈ 0.995 × 0.998 × payoff)

After the overlay: `proof.q_lcb_5pct = 0.990 > proof.q_posterior = 0.052`.

The Q_LCB_INVALID guard in `_native_side_candidate_from_proof` (event_reactor_adapter.py:7228):

    if not (0.0 <= q_lcb <= 1.0) or not (0.0 <= q_point <= 1.0) or q_lcb > q_point:
        return NativeSideCandidate.no_trade(... reason=CandidateNoTradeReason.Q_LCB_INVALID ...)

fires every time for every spine-overlaid forecast proof (q_lcb_5pct >> q_dot_payoff on
neg-risk NO). This sets `candidate.is_tradeable = False` → Gate 1 in
`_evaluate_submit_recapture_for_selected` returns the SUBMIT_ABORTED_PRICE_MOVED decision
without even reading the snapshot → the snapshot's freshness is never checked → every abort
message says "no fresh executable snapshot" even though the snapshot is perfectly fresh.

The Q_LCB_INVALID guard is not the bug. The semantic mismatch that feeds it is.

## Fix

In `_overlay_spine_economics_onto_proof` (qkernel_spine_bridge.py), add `q_lcb_5pct` to
the overlay dict, clamped to `min(original, new_q)`:

    original_q_lcb = float(getattr(proof, "q_lcb_5pct", 1.0))
    clamped_q_lcb = min(original_q_lcb, new_q)
    overlay = {
        "q_posterior": new_q,
        "trade_score": new_trade_score,
        "q_source": "qkernel_spine",
        "q_lcb_5pct": clamped_q_lcb,      # ← NEW
    }

Properties of the fix:

- **Does NOT bypass the Q_LCB_INVALID guard** — q_lcb_5pct ≤ q_posterior is maintained,
  so the guard passes for proofs the spine said are +edge (edge_lcb > 0 already fired).
- **Does NOT loosen the 30s freshness window** — `_K1_DEFAULT_PRESUBMIT_FRESHNESS_SECONDS`
  is unchanged; the snapshot freshness check now actually runs rather than being bypassed
  by the spurious Q_LCB_INVALID return.
- **Does NOT force a submit** — the downstream recapture gate remains fail-closed for
  genuinely moved/stale books.
- **Does NOT raise q_lcb_5pct** — `min()` is one-sided; when q_lcb_5pct is already below
  q_dot_payoff (normal taker case), it is left at its original value.
- **Conservative Kelly sizing** — binary Kelly uses `q_lcb_5pct` for
  `f* = (q_lcb − cost)/(1 − cost)`. The clamped value equals `q_dot_payoff`, which for
  a genuine +edge candidate produces a positive but conservative Kelly fraction.

## Files changed

- `src/engine/qkernel_spine_bridge.py` — `_overlay_spine_economics_onto_proof`, add
  `q_lcb_5pct` clamp to the overlay dict (17 lines added/modified in the overlay block).

## Tests added (RED-on-revert)

Two tests added to
`tests/integration/test_qkernel_spine_blockers_pr409.py` (BLOCKER 5):

- `test_overlay_clamps_q_lcb_5pct_to_q_posterior` — drives the overlay with
  `q_lcb_5pct=0.990, q_dot_payoff=0.052` (realistic neg-risk case). Asserts
  `q_lcb_5pct ≤ q_posterior` after overlay and `q_lcb_5pct ≤ original`. RED without
  the `q_lcb_5pct` key in the overlay dict.

- `test_overlay_does_not_clamp_when_q_lcb_already_below_q_dot_payoff` — drives with
  `q_lcb_5pct=0.32, q_dot_payoff=0.44` (well-formed taker case). Asserts `q_lcb_5pct`
  is unchanged after overlay. RED if the fix hard-sets `q_lcb_5pct = new_q` instead of
  `min(original, new_q)`.

Both pass; full blockers suite (12 tests) passes; spine routing + μσ-threading + metric
identity suites (21 tests) pass.

## What is NOT the cause (ruled out)

- NOT snapshot freshness: the snapshot IS fresh; the abort bypasses the freshness check
  entirely via the Q_LCB_INVALID early-return.
- NOT `captured_at` semantics: the snapshot row is not consulted at all on this path.
- NOT genuine price move: the book has not moved; the abort fires on every candidate,
  including markets last traded seconds ago.
- NOT the maker-quote lane (class a): maker-quote execution_price is correctly materialized
  in `_native_side_cost_curve_from_execution_price`; it does not fail with ValueError.
  The maker-quote lane does not reach the recapture abort; the Q_LCB_INVALID gate fires
  before the cost-curve path.
- NOT day0 (class d): day0 routes to legacy before the spine; not affected.
