# DAY0 / same-day wrong-side — phase-gate IMPLEMENTATION (#98)

- Created: 2026-06-01
- Last reused or audited: 2026-06-01
- Authority basis: DAY0_OBSERVATION_WRONGSIDE_ROOT_2026-06-01.md (root) +
  DESIGN_CRITIC_2026-06-01.md (binding critic, MAJOR-1..4) + src/strategy/market_phase.py (phase authority).
- Status: IMPLEMENTED + TDD-verified. Shadow daemon unaffected until restart.

## Rule (STRONGER than design §4.1, per critic MAJOR-4)
forecast_only admits a family **ONLY** when `MarketPhase == PRE_SETTLEMENT_DAY` — the entire
target *local day* is still in the future. `SETTLEMENT_DAY`, `POST_TRADING`, `RESOLVED`, and
`unknown`/`None` all reject fail-closed.

Why stronger than the design's "admit PRE_SETTLEMENT_DAY + SETTLEMENT_DAY": the critic (MAJOR-4)
showed SETTLEMENT_DAY still carries already-observed-extremum wrong-side exposure — a `low`
realized overnight (local day begun, pre-12:00Z close) but decided at 10:00Z is admitted under
SETTLEMENT_DAY, yet forecast_only is blind to it. The category-killer excludes SETTLEMENT_DAY too.
Same-day edge is the **disjoint** day0 observation-aware scope's job; forecast_only must not trade
a window it cannot observe. This resolves MAJOR-4 by construction (no SETTLEMENT_DAY admission ⇒ no
already-observed exposure at all), not by patch.

## Placement (single chokepoint — resolves critic MAJOR-1)
The gate lives in `build_event_bound_no_submit_receipt` (src/engine/event_reactor_adapter.py)
immediately after `family = decision.candidate_family`, BEFORE `_generate_candidate_proofs` (q/FDR/
Kelly). This single function is called by BOTH the no-submit adapter AND the live adapter
(`event_bound_live_adapter…_submit` → `build_event_bound_no_submit_receipt`), so one gate covers
every EDLI receipt path. At this point `family.{city,target_date,metric}` are engine-validated and
the selected snapshot `row` is bound — critic MAJOR-1's "family not in scope at :195/:507" is moot:
the gate sits where family IS in scope, a cleaner chokepoint than the two raw entry sites.

Scoped to `event.event_type == "FORECAST_SNAPSHOT_READY"`. DAY0_EXTREME_UPDATED (currently gated
off) owns its own observation-aware logic; when that scope later activates it gets its own phase
handling. Scopes stay disjoint.

## Authority reuse (predecessor solution — critic MAJOR-2/3 resolved)
- Uses `src.strategy.market_phase_evidence.from_market_dict(market=…, city_timezone=…,
  target_date_str=…, decision_time_utc=…, uma_resolved_source=None)` — the REAL API (critic
  MAJOR-2: `uma_resolved_source` is `Optional[str]` tx hash, passed `None` until the UMA listener
  lands; not the pseudocode `uma_resolved` bool).
- The selected snapshot `row` carries `market_end_at`; when absent (NULL on ~100% of retained rows
  per critic MAJOR-3) `from_market_dict` falls back to the F1 12:00-UTC anchor (`phase_source =
  fallback_f1`, still `is_live_authoritative`). The uncommitted `market_end_at IS NULL OR … > ?` SQL
  predicate (ae5fe38, fail-OPEN on NULL) is NOT used — the typed phase evidence with F1 fallback is
  the authority. (ae5fe38 dropped.)
- Missing city timezone ⇒ phase=None ⇒ fail-closed reject (`city_timezone_missing:<city>`).

## Reject receipt + observability
Returns `EventSubmissionReceipt(reason="EVENT_BOUND_MARKET_PHASE_CLOSED:<phase>:<phase_source>",
city/target_date/metric/family_id populated, family_complete=True)`. Because the regret ledger pulls
city/target_date/metric and `rejection_reason` from the receipt, a `no_trade_regret_events` row is
produced automatically (no extra wiring).

## RED → GREEN (TDD; relationship-first)
Test: `tests/engine/test_edli_forecast_only_phase_exclusion.py`.
- TIER 1 (pure rule × phase clock, distinct city/date/time per case): future PRE_SETTLEMENT_DAY
  admit; same-day POST_TRADING reject; same-day SETTLEMENT_DAY (pre-close) reject (the MAJOR-4 case);
  unknown-city fail-closed; explicit-future endDate honored (verified_gamma).
- TIER 2 (wiring): a FORECAST_SNAPSHOT_READY family decided 4h past its 12:00Z close yields NO
  candidate (`reason` starts `EVENT_BOUND_MARKET_PHASE_CLOSED`); CONTROL: the same fixture at the
  normal future-date decision still yields an accepted candidate (no over-fire).
- RED: ImportError (helpers absent) on pre-#98 HEAD. GREEN: 7/7 pass.

## Regression (no new failures)
- `tests/engine/test_event_reactor_no_bypass.py`: 74 passed, 1 xfailed (pre-existing).
- `tests/engine/test_substrate_illiquid_bin_capture.py`: pass.
- The 14 pre-existing `#97` canary/online failures are UNCHANGED and causally disjoint: each reaches
  the certificate-build stage (`taker FOK/FAK disabled`, etc.), which is AFTER this pre-scoring gate
  — proving their forecast families are still ADMITTED (PRE_SETTLEMENT_DAY), not newly rejected.
  No failure references `EVENT_BOUND_MARKET_PHASE_CLOSED`.

## Downstream 10-step trace
1. Closed/same-day families rejected at admission → 2. no q/FDR/Kelly computed → 3. no submit-ready
receipt → 4. continuous re-decision re-enqueues route back through the reactor → same gate → no
re-fire → 5. `no_trade_regret_events` gains MARKET_PHASE_CLOSED rows (observable) → 6. future-date
families unaffected (PRE_SETTLEMENT_DAY admit) → 7. shoulder vs exact bins unaffected (family-level,
pre-bin) → 8. #24 unshadow gate cleaner (no same-day wrong-side contamination) → 9. day0 scope, when
activated, owns same-day; forecast_only stays excluded (disjoint) → 10. CI antibody
(test_edli_forecast_only_phase_exclusion) prevents a future refactor from dropping the gate.

## Residual / follow-ups
- The Paris-class 13 same-day buy_no candidates are now rejected pre-scoring. Verify post-restart
  that the live pool's same-day count → 0 (re-run the #98 blast-radius query).
- Far-east cities: forecast_only's tradeable window for a target_date ends at local-midnight-of-
  target_date (UTC-earlier for eastern tz). Intended consequence of the category rule.
