# THE continuous-harvest blocker — latched governor kill switch (#123 / M2), 2026-06-15

- Created: 2026-06-15
- Last reused or audited: 2026-06-15
- Authority basis: independent causal trace (oh-my-claudecode:tracer) + direct
  verification against live DBs (state/zeus_trades.db, state/zeus-world.db) and code
  (governor.py, command_recovery.py, command_state.py, edli_absence_resolver.py).
  Deploy target: live/iteration-2026-06-13 (the branch the daemon runs).

## The finding (overturns the earlier "no inventory / external wait" framing)
RUN-verified via direct Polymarket API: open, liquid, edge-band forecast markets exist
RIGHT NOW (London high 06-17 NO book 25 asks @0.650; Tokyo high 06-16 NO 18 asks @0.590).
The harvest is NOT trading them — and NOT because of the edge, the forecast/FSR lane,
topology, capture, the spine, or the decision gate. The tracer proved (H1/H2/H3 falsified,
H5 confirmed) that all five pipeline stages are healthy: 5 buy_no decisions reach
`VenueSubmitAttempted` and ALL die at one global pre-submit gate.

## Root cause (verified to the exact row)
The portfolio governor's automatic kill switch is **latched on `unknown_side_effect_threshold`**,
blocking EVERY buy_no submit since 16:28 UTC:
- `governor.py:575-595 count_unknown_side_effects()` counts `venue_commands` rows (in
  **state/zeus_trades.db**) with `state IN {SUBMIT_UNKNOWN_SIDE_EFFECT, UNKNOWN, REVIEW_REQUIRED}`
  (governor.py:37). `kill_switch_reason()` (234-248) trips when count > `unknown_side_effect_limit`
  = **0** (config/risk_caps.yaml:12). Block site `executor.py:3195,3200` — family-agnostic → global.
- The ONE stuck row: `venue_commands.command_id=01049c6a357d4f97`, token `95270664…177126`,
  side BUY, size 75.149, price 0.76, **venue_order_id=NULL**, state `SUBMIT_UNKNOWN_SIDE_EFFECT`,
  created 16:29:23 UTC — the operator's "11:30 @0.76" chengdu-06-17 buy_no.

## Why it's safe to resolve (authenticated-absence-proven — ZERO exposure)
The EDLI event-sourced ledger (`edli_live_order_events`, state/zeus-world.db), aggregate
`edli_evt_1b1483f82bebcfcfd3eea…`, has a `Reconciled` event carrying `authenticated_absence_proof`
(`AUTHENTICATED_CLOB_ABSENCE_NO_OPEN_ORDER_OR_TRADE`, `venue_order_exists=false`,
`venue_trade_exists=false`) + `CapTransitioned(RELEASED)`; `edli_live_order_projection.pending_reconcile=0`.
The boot absence-resolver authenticated to Polymarket and confirmed NO open order and NO trade
for the token — the submit never landed. position_current has no row for the token. The order
is provably absent; resolving the stuck venue_commands row merely SYNCS it to that truth.

## THE GAP (#123 / M2) — why it stays latched
Two separate tracking systems. The EDLI ledger side was resolved (absence-proven,
pending_reconcile=0), but the **`venue_commands` row was never transitioned out of
`SUBMIT_UNKNOWN_SIDE_EFFECT`** — there is no auto-resolution path for a NULL-venue_order_id
unknown (`command_state.py:113` comment: "from SUBMIT_UNKNOWN_SIDE_EFFECT (M2 will own active
resolution logic)"; `_reconcile_edli_pre_venue_unknown_thresholds` (command_recovery.py:5449)
deliberately skips rows that HAVE a venue_commands row via its `NOT EXISTS venue_commands` clause).
The governor reads `venue_commands` → counts 1 forever → kill switch latched → 7h+ stall.

## The fix (in flight — NOT a limit raise, NOT a one-row hack)
Wire the EDLI-absence → venue_commands-terminalization sync into `_edli_command_recovery_cycle`:
for a SUBMIT_UNKNOWN_SIDE_EFFECT venue_commands row with NULL venue_order_id whose EDLI aggregate
is authenticated-absence-proven (no order, no trade), append the canonical terminal CommandEvent
(landing OUTSIDE `_UNRESOLVED_SIDE_EFFECT_STATES`) citing the proof. Fail-closed: never terminalize
without the proof or with any matching exposure; never raise `unknown_side_effect_limit`. INV-37
cross-DB (venue_commands=zeus_trades.db, proof=zeus-world.db). On the next recovery cycle this
resolves command 01049c6a → governor count→0 → buy_no resumes on the live liquid edge markets.
Implementation: opus executor, worktree-isolated, with fail-closed tests + money-path baseline.
Then review → deploy → verify the governor un-latches and the harvest fires.

## POST-DEPLOY RESULT (verified) + the NEXT lever
Deployed `f4ff1bb035` (cherry-pick onto live), restarted (PID 18557). Verified:
- Recovery cycle resolved cmd `01049c6a` → `SUBMIT_REJECTED`; `count_unknown_side_effects → 0`;
  `risk_allocator_pre_submit_blocked` gone from reactor cycles. Latch CLEARED.
- Harvest AUTONOMOUSLY re-fired: a real venue-acknowledged order at 00:39:59 UTC —
  buy_no Chengdu 06-17 high @0.72, 73.08 sh, `venue_order_id=0x0616e62a…` (a REAL id; the
  stuck one was NULL). NOT a forced order — the redecision cycle produced it once un-gated.

**The order is RESTING, not filled.** Live book (verified via CLOB /book): NO bid 0.72 (ours,
top) / NO ask 0.73 — we sit 1¢ under the ask. The system rests at the conservative `q_no_lcb`
(0.72) instead of crossing to take the 0.73 ask. With NO-on-modal gate-fired win-rate ~0.778,
crossing to 0.73 is EV ≈ 0.778−0.73−0.02 = **+0.028/share** — a +EV fill forgone. This is the
live instance of the spine grade's item 2 (NO maker-bid fill suppressed), now QUANTIFIED on a
live book, not just historical proxy.

### PRIME NEXT LEVER (continuous fills) — maker-bid → taker-cross, settlement-validated
The structural blockers (#122 capture, #123 governor) are removed; the system now places real
harvest orders. The gap to CONTINUOUS FILLS is execution-price: convert a justified resting bid
into a taker fill when `point_q_no − ask − fee > 0` (cross to take), instead of resting at the
conservative `q_lcb` and timing out (900s) unfilled. Caveats (operator law + grade): NOT a cap/
loosen; gate the cross on the POINT q (which the grade flagged as 1.22× over-confident on the
modal — so validate the cross is +EV against settlement, not just point-q). This needs its own
careful money-path change + settlement validation (the grade's mandatory guard #1: forward
paper-trade ≥1 week of real modal-NO asks) — which the un-gated system is NOW generating. Do NOT
rush it on a long session; it is the right next work with fresh judgment + the live fill data.

## Lesson (for the index / how-to-work)
"Silent live system = dead submit plumbing, diagnose ORDER EMISSION before belief" held AGAIN:
the harvest, edge, spine, and capture were all fine; one stuck command latched a global safety
gate. Also: I wrongly inferred "no inventory" from skip-counts and from querying the WRONG DBs
with `immutable=1` (stale/empty pages on the live WAL DB) — the operator's hook correctly forced
a RUN-verified venue probe that overturned it. Use `?mode=ro` (WAL-aware), correct K1-split DBs.
