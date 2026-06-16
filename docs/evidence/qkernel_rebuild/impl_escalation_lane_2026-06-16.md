# Implementation: escalation re-decision Tier-0 lane (redecide-block fix)

```
# Created: 2026-06-16
# Last audited: 2026-06-16
# Authority basis: docs/evidence/qkernel_rebuild/redecide_block_2026-06-16.md §FIX
#   (authoritative). Live main tree /Users/leofitz/zeus, branch live/iteration-2026-06-13,
#   HEAD 9424744b01. Implement + test only — NOT committed, daemon NOT restarted.
# Scope: ONE blocker — un-starve the armed TAKER_ESCALATED_AFTER_REST cross.
```

## What was implemented (the two-part change, exactly as §FIX prescribes)

1. **Emit a family-targeted re-decision on confirmed cancel.** The escalation cancel
   job now harvests the just-cancelled families; the caller (which owns DB access)
   writes one `EDLI_REDECISION_PENDING` opportunity_event per family, marked
   escalation-origin via a distinct `source` prefix `escalation_cross-`. The existing
   `EDLI_REDECISION_PENDING` type and the existing FSR re-emit machinery
   (`scan_committed_snapshots`) are REUSED — no new event type, no new payload shape.

2. **Tier-0 claim for escalation-origin re-decisions (the load-bearing half).** A new
   clause in `claim_tier_expr_sql` ranks an `EDLI_REDECISION_PENDING` whose `source`
   begins with `escalation_cross-` at Tier 0 — strictly below the entire 49-deep
   per-(tier,city) round-robin. Because `fetch_pending`'s outer sort is `_claim_tier
   ASC` first, the armed cross is claimed before any city's rank-1 FSR → fires on the
   NEXT cycle, not in ~2-3h.

## Tier variant chosen: TIER-0 (the report's PRIMARY recommendation), not `_city_round=0`

Chosen because it is the SMALLER, safer blast radius:
- It touches ONLY the CASE expression in `event_priority.py` (the single tier
  authority). The per-city round-robin window in `event_store.py:231-239`
  (`PARTITION BY c._claim_tier, c._city_key`, the 2026-06-11 incident-law surface) is
  **left byte-for-byte untouched** — so per-city fairness for every non-escalation
  event is provably unchanged.
- The escalation clause matches ONLY `EDLI_REDECISION_PENDING` with the
  `escalation_cross-` source. The continuous-redecision `cycle-*` EDLI_REDECISION_PENDING,
  FSR, day0, and channel events all evaluate EXACTLY as before (verified by the
  rendered-SQL table below).
- The escalation set is bounded and self-extinguishing (one event per confirmed
  cancel), so a Tier-0 lane cannot become a flood/claim-storm — no cap or throttle is
  needed, consistent with operator law.
- The clause is evaluated FIRST and INDEPENDENT of `day0_is_tradeable` (the report:
  "tier 0 regardless of `day0_is_tradeable`, since this is a confirmed-armed cross
  with proven settlement edge, not a shadow"). It coexists with the existing day0
  Tier-0 clause; under the live `day0_shadow` scope, Tier 0 contains ONLY escalation
  events.

The `_city_round=0` variant was rejected because it would modify the ROW_NUMBER
partition logic directly — exactly the fairness surface the incident law guards — for
no additional benefit.

### Rendered tier CASE (proof the lane is exact)

Under `day0_is_tradeable=False` (live scope), tiers resolve as:

| event_type | source | tier |
|---|---|---|
| EDLI_REDECISION_PENDING | `escalation_cross-tok-3` | **0** |
| FORECAST_SNAPSHOT_READY | (COMPLETE+LIVE_ELIGIBLE) | 1 |
| EDLI_REDECISION_PENDING | `cycle-tok-3` (continuous) | 2 |
| DAY0_EXTREME_UPDATED | (shadow) | 2 |

The continuous `cycle-*` re-decision stays Tier 2 (falls to the ELSE) — its fairness
is unchanged. Only the `escalation_cross-` source jumps.

## Exact diff (file:line)

### `src/events/event_priority.py`
- **+L92-98**: new module constant `ESCALATION_CROSS_SOURCE_PREFIX = "escalation_cross-"`
  (the single discriminator, shared by emitter and tier authority).
- **L142-156** (`claim_tier_expr_sql`): prepend `escalation_tier0_clause`
  (`WHEN e.event_type='EDLI_REDECISION_PENDING' AND e.source LIKE 'escalation_cross-%' THEN 0`)
  to the CASE, BEFORE the day0 clause; always present, independent of
  `day0_is_tradeable`. Docstring updated (Tier-0 description + the explicit
  "non-escalation fairness is untouched" invariant).

### `src/execution/maker_rest_escalation.py`
- **L116-160** (`run_cancels_for_expired_rests`): added optional out-parameter
  `collect_cancelled: list[dict] | None = None` (L121). On a CONFIRMED cancel only
  (the `cancel_failed` path `continue`s before it), the entry is appended (L156-160).
  `stats` is kept BYTE-IDENTICAL — the harvest rides the out-parameter, so every
  existing exact-equality caller/test holds. The connection-free network contract is
  preserved (the only added work is an in-memory list append).

### `src/main.py`
- **+L2627-2648**: `_edli_next_escalation_cross_source()` — returns
  `escalation_cross-{boot_token}-{N}` (shared boot token + monotonic N → distinct
  idempotency_key per emit; `split('-')[-1]` stays an int for the fairness-cursor
  parse in `scan_committed_snapshots`).
- **+L6316-6379**: `_escalation_families_from_cancelled(cancelled, trade_conn, forecasts_conn)`
  — recovers `(city,target_date,metric)` from VENUE TRUTH via two canonical,
  already-proven joins: `token_id → condition_id` (freshest
  `executable_market_snapshots.selected_outcome_token_id`, the SAME resolution
  `_edli_open_maker_rests_for_screen` uses) then `condition_id →
  market_events.(city,target_date,temperature_metric)`. Best-effort per entry: an
  unresolvable row is skipped, never crashes.
- **+L6382-6430**: `_emit_escalation_cross_redecisions(families, decision_time, received_at)`
  — routes the families through `ForecastSnapshotReadyTrigger.scan_committed_snapshots`
  with `event_type='EDLI_REDECISION_PENDING'`, `source=escalation_cross-…`,
  `restrict_to_families=families`, and DELIBERATELY **no** `already_pending_keys` (a
  pending FSR is exactly what is stuck behind the round-robin; the Tier-0 lane is what
  un-starves it). World write under the world-write mutex, mirroring the existing
  `_edli_emit_*` / continuous-redecision-screen pattern; COMMIT inside the mutex.
- **L6493-6543** (`_maker_rest_escalation_cycle`): pass `collect_cancelled=cancelled_entries`
  to the cancel call; then, gated on `event_writer_enabled`, recover families on
  short read-only connections and emit. The whole emit block is wrapped in a
  fail-closed `try/except` (L6510, L6535-…): any error logs and continues — it can
  NEVER crash the cancel job (worst case = pre-fix behavior: the family waits for the
  round-robin). The connection-free cancel phase is preserved (all DB work runs in the
  caller, AFTER the venue cancels).

## Hard-constraint compliance
- **No cap/throttle/allowlist/global rotation change.** The change is a priority LANE
  for an event the system already knows is armed + EV. No budget, no cap.
- **Per-city fairness for non-escalation events unchanged.** The round-robin window in
  `event_store.py` is untouched; the tier clause matches ONLY `escalation_cross-`
  sources. Verified by `test_escalation_lane_does_not_disturb_non_escalation_city_fairness`
  and the full green `test_fetch_pending_city_fairness.py` (8 tests) + `test_fair_lane_interleave.py`.
- **#122 `database is locked` warm-cycle spiral NOT touched** (separate ticket).
- **Fail-closed**: the re-decision emit is wrapped so an error never crashes the cancel
  job (`test_cancel_failed_is_not_harvested` + the caller try/except).

## Test output

### RED-on-revert proof (the load-bearing assertion)
`tests/events/test_fetch_pending_escalation_cross_lane.py::test_escalation_redecision_jumps_full_city_backlog`:
a 49-city tradeable-FSR backlog (full live round-robin depth) + ONE escalation
re-decision for a city buried at index 40, with the WEAKEST within-tier signal
(priority 0, oldest available_at). With the Tier-0 clause it is claimed FIRST under a
budget of 1. **Reverting the clause** (`escalation_tier0_clause = ""`) makes it RED:

```
E  AssertionError: the armed escalation re-decision did NOT jump the 49-city FSR
   backlog — it would wait ~2-3h for its city's round-robin turn.
E  assert 'edli_evt_e7d8…86795' == 'edli_evt_9c9d…c90d3'   # claimed an FSR, not the armed event
   1 failed
```

Restoring the clause → GREEN. This proves `priority` alone is provably too weak (it
sub-sorts below `_city_round`); the decisive edit is the `claim_tier_expr_sql`
ordering, exactly as the report diagnosed.

### Cancel-path emit contract
`tests/execution/test_maker_rest_escalation.py::TestEscalationRedecisionHarvest`:
- `test_one_harvest_per_confirmed_cancel` — exactly one harvested entry per confirmed cancel.
- `test_cancel_failed_is_not_harvested` — a `cancel_failed` family is NOT harvested (zero re-decision).
- `test_no_collect_list_preserves_byte_identical_stats` — default path unchanged.

`tests/execution/test_escalation_redecision_emit.py` (5 tests): family recovery from
venue truth (resolve / skip-unresolvable / empty), and the emit routes through the FSR
machinery with the escalation source, `restrict_to_families`, and NO
`already_pending_keys`.

### Suites GREEN
```
tests/execution/test_maker_rest_escalation.py ............ 11 passed
tests/execution/test_escalation_redecision_emit.py .......  5 passed
tests/events/test_fetch_pending_escalation_cross_lane.py .  2 passed
tests/events/test_fetch_pending_city_fairness.py ......... 11 passed (incl. 2026-06-11 fairness law)
tests/events/test_fetch_pending_day0_shadow_priority.py ..  5 passed
tests/events/test_fetch_pending_timeliness.py ...........
tests/events/test_fair_lane_interleave.py / test_event_store_idempotency.py ...
  -> consolidated spec suite: 54 passed
tests/events/  (full sweep) ............................... 516 passed, 8 skipped, 2 xfailed
reactor + execution sweep ................................ 81 passed
tests/money_path/ ........................................ 192 passed, 3 failed
```

The **3 `tests/money_path/test_finding_b_free_cash_bound.py` failures are PRE-EXISTING**
(bankroll-provider harness issue: `bankroll cached() -> None: NEVER-FETCHED`). Confirmed
by stashing ONLY my three source files and re-running: the same 3 fail identically on
the clean baseline. My change introduces ZERO new failures.

## Deviations from the spec
None of substance. Two clarifications:

1. **Harvest via an out-parameter, not a changed return type.** The report says
   "COLLECT the cancelled families" in `run_cancels_for_expired_rests`. I added an
   optional `collect_cancelled` list rather than changing the `stats` return shape,
   because existing callers/tests assert `stats == {…}` by exact equality
   (`test_cancel_error_continues_to_next_order`, the continuous-redecision screen at
   `main.py:6603`). The out-parameter keeps `stats` byte-identical while still handing
   the families to the caller — satisfies the spec intent with zero blast radius.

2. **Family recovery joins via `condition_id` (not a literal `venue_commands` column).**
   The report says city/date/metric "are recoverable from the cancelled
   `venue_commands` row / `executable_market_snapshots`." `executable_market_snapshots`
   carries `condition_id`/token columns but NOT city/date/metric directly, so I use the
   canonical two-leg map the codebase already trusts: `token_id → condition_id` (via
   `selected_outcome_token_id`, the same join `_edli_open_maker_rests_for_screen` uses)
   then `condition_id → market_events.(city,target_date,temperature_metric)`. This is
   the same `market_events` family map the FSR re-emit machinery itself relies on. Note:
   live DB probing of the two exact families was blocked by the live daemon's write-lock
   contention (the #122 spiral) — the recovery is therefore validated by an in-memory
   integration test (`test_escalation_redecision_emit.py`) against the same schema, not
   by a live-data query.

## Provenance verdicts (helpers audited before reuse)
- `src/events/event_priority.py` — CURRENT (per-city-fairness 2026-06-11 incident law;
  preserved verbatim for non-escalation events).
- `src/events/event_store.py` `fetch_pending` ordering — CURRENT, UNTOUCHED (the
  Tier-0 lane works entirely through the `_claim_tier ASC` outer sort it already has).
- `src/execution/maker_rest_escalation.py` — CURRENT (split network/DB phases
  2026-06-11; the new harvest preserves the connection-free network contract).
- `src/events/triggers/forecast_snapshot_ready.py` `scan_committed_snapshots` — CURRENT
  (the `restrict_to_families` + `event_type` + `source` parameters are the exact ones
  the 2026-06-12 continuous-redecision resurrection added and the live screen uses).

## Deploy note
Editing only; daemon NOT restarted. The change takes effect on the operator's next
restart. No DB migration, no schema change, no config change.
