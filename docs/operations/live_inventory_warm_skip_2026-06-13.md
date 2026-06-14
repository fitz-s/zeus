# Live-inventory focus: venue-close warm-lane skip (2026-06-13)

Created: 2026-06-13
Last reused/audited: 2026-06-13
Authority basis: docs/evidence/no_order_root_2026-06-13/diagnosis.md (reactor venue-close
horizon, commit 274039f956) + operator NO-CAPS law (memory no-caps-no-overengineering-2026-06-12)

## Plan / change scope (planning-lock evidence)

Change-set (cross-zone K2_runtime + K3_extension + architecture):
- `src/strategy/market_phase.py` — ADD pure predicate `family_venue_closed(city, target_date, now_utc)`
  (complement of the existing `market_open_at_decision` POST_TRADING boundary; same F1 12:00-UTC anchor).
- `src/main.py` — `_refresh_pending_family_snapshots`: skip families whose venue market is
  POST_TRADING (the warm-lane analogue of the reactor `_venue_market_closed_horizon`); add injected
  `now_utc` param (defaults to wall-clock) + `venue_closed_skipped` observability counter.
- `tests/money_path/test_edli_market_substrate_warm_cycle.py` — 2 new relationship tests
  (RED-on-revert) + re-date 5 fixed-date fixtures to injected venue-OPEN `now`.
- `architecture/test_topology.yaml` — register the warm-cycle relationship test file.

No new gate, cap, allowlist, throttle, or flag. No decision-gate or 30s-freshness relaxation.
Read-only live-DB diagnosis only. No daemon restart performed by this change (operator-gated).

## Root cause (three-way, read-only evidence — NOW≈2026-06-13T17:51–18:05Z)

THREE candidate roots were distinguished; the data selects WARM-LANE CLOG (root #2), refutes
the ENUMERATION/ENQUEUE gap (root #1), and refutes a decision-branch silent-drop (root "b").

1. ENUMERATION/ENQUEUE gap — REFUTED. `opportunity_events` are produced for 2026-06-14
   (36 683 rows, latest_created 17:45Z) and 2026-06-15 (9 662). `opportunity_event_processing`
   pending (consumer edli_reactor_v1) includes 199 06-14 + 78 06-15 events. Live inventory IS
   enqueued; the per-city day0-scope advance is rolling forward. No fix needed here.

2. WARM-LANE CLOG — CONFIRMED. Classifying the 319 distinct pending warm-lane families by venue
   phase at NOW: 202 are `post_trading` (108×06-13 + 94×06-12) vs only 117 LIVE
   (75 pre_settlement_day [06-14/06-15] + 42 settlement_day [06-14]). 66 of the closed families
   are NOT strictly-past (06-13 cities whose local midnight has not passed), so
   `EventStore._strictly_past_in_tz` alone does NOT skip them — the venue closed at the F1
   12:00-UTC anchor (5.7h before NOW) but the local day has not ended. The warm lane burns its
   ~17s time-box re-probing these closed families (live log: 4 821 lines
   `… <City>/2026-06-13/… family will stay at FDR gate`; result key
   `gamma_slug_timebox_unattempted` is the tell), starving the 117 live families of fresh books.

3. PRIORITY/ORDERING — subsumed by #2. The pending-family query orders target_date DESC (live
   06-14/06-15 first), but the rotating cursor sweeps the whole list, so closed families still
   consume budget every period. The skip removes them entirely.

### The "silent candidate drop" (live_health DEGRADED) is the SAME root, not a new one

`src/control/live_health.py:191` fires `CANDIDATES_WITHOUT_FINAL_INTENTS_OR_NO_TRADE_REASONS`
when `candidates>0 ∧ final_intents==0 ∧ no_trade_reasons empty`. Live cycle pulse
(state/status_summary.json, 18:04Z): `candidates=123, retried=123, rejected=0, dead_lettered=0,
proof_accepted=0, rejection_reason_counts={}`. By `src/main.py:6011`,
`candidates = proof_accepted + rejected + retried + dead_lettered`, and a transient REQUEUE
(`reactor.py:1224-1230`) increments `retried` WITHOUT a terminal `rejection_reason` (documented
at `reactor.py:452-455`: "reasons=[]"). So the 123 "candidates" are honest transient requeues,
not silently-dropped decisions. Every event terminates in exactly one counted outcome
(processed / rejected / retried / dead_lettered / proof_accepted) — `reactor.py:1190-1239` — so
root "b" (a decision-branch that neither submits nor records a receipt) does NOT exist. The
requeues are LIVE families (150 pre_settlement + 21 settlement FORECAST_SNAPSHOT_READY pending)
whose executable snapshot is stale because the warm lane never refreshed their book (only ~509
fresh snapshots universe-wide at 18:05Z). Freeing the warm time-box (this fix) lets live families
get fresh books → reach proof-evaluation → emit a final_intent OR an honest no-trade receipt →
live_health clears.

### Secondary observation for the operator (NOT in this change's scope)

ZERO `MARKET_VENUE_CLOSED`/`MONEY_PATH_HORIZON_EXPIRED` dead-letters exist EVER
(event_dead_letters), despite the reactor venue-close horizon (274039f956) being on the deployed
HEAD. The 55 closed 06-13 FORECAST_SNAPSHOT_READY events are still `pending`/retried, not
terminalized. This means the reactor venue-close horizon is not yet terminalizing closed families
in production — likely the live daemon (restarted 17:30Z) needs a restart to pick up the new
code, or there is a follow-up defect. Flagged for operator; out of this warm-lane fix's scope.

## Fix authority (single clock, no new authority)

`family_venue_closed` reuses `market_open_at_decision` (the one POST_TRADING-boundary predicate)
with the `_f1_fallback_end_utc` 12:00-UTC anchor — the SAME boundary `market_phase_for_decision`
and the reactor `_venue_market_closed_horizon` use. Fail-SOFT: unresolvable city/tz/date ⇒ False
(NOT closed) ⇒ family KEPT (uncertain ⇒ keep; a wrong True would drop a tradeable family).

## Verification

- Relationship test `test_warm_lane_skips_venue_closed_family_keeps_venue_open_family` is
  RED-on-revert (reverting the warm-skip: venue-closed family refreshes, `venue_closed_skipped==0`,
  assertion fails).
- Fail-soft test `test_warm_lane_venue_close_skip_is_failsoft_on_unresolvable_family` pins that an
  unresolvable family is KEPT past the close instant.
- warm-cycle suite 23 passed; market_phase 19; events/reactor 490; live_health 31. The 8
  pre-existing `test_market_phase_evidence/persistence` failures (`skipped_missing_table`) are
  present on the clean base and are unrelated to this change.

## Deploy note

Daemon restart REQUIRED to take effect (the warm cycle is loaded at daemon start). No config/flag
edit. After restart, expect `venue_closed_skipped>0` in the warm-cycle result and the live
06-14/06-15 families reaching candidate-evaluation (edli_no_submit_receipts resuming).
