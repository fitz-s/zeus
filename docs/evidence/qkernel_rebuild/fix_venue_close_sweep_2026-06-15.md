# Venue-Close Sweep Fix — #126 / 2026-06-15

**Root cause fixed**: `archive_expired_candidates` only swept families whose
target LOCAL day had ended (`_strictly_past_in_tz`). Families in the
`[venue_close, local_day_end)` window — venue POST_TRADING but local day still
open — were never fetched as candidates and stayed `'pending'` forever.

## Live Evidence

At ~2026-06-16T02:00Z, 132 families were confirmed stuck `'pending'`:
- target_date: `2026-06-15` (venue closed at `2026-06-15T12:00Z`)
- Example families: `Miami|2026-06-15|low`, `Wellington|2026-06-15|high`,
  `Busan|2026-06-15|high`, `Seoul|2026-06-15|high`, `Tokyo|2026-06-15|high`

Effect: every EDLI redecision cycle logged
`edli_redecision: enqueued=0 batch=60 skipped_pending=132` — the fair cursor
was dominated by these dead families, 2026-06-16/06-17 harvest families were
never re-emitted, the NO-on-modal harvest lane was dark, zero orders.

## Gap Analysis

The cheap pre-filter for FSR is `target_date < frontier_floor`. At
`2026-06-16T02:00Z` the Oceania frontier_floor = `2026-06-15`. The 132 stuck
families have `target_date = '2026-06-15'`. Since `'2026-06-15' < '2026-06-15'`
is False, they were **excluded from the candidate band entirely** — the sweep
never even considered them. The venue-close check was never reached.

For Miami specifically:
- Venue close (F1 12:00-UTC anchor): `2026-06-15T12:00Z`
- Local day end (America/New_York UTC-4 in June): `2026-06-16T04:00Z`
- At `2026-06-16T02:00Z`: venue IS closed, local day is NOT past
- `_strictly_past_in_tz` returns `False` → family kept pending forever

## Fix

### 1. Candidate band widened (`archive_expired_candidates`)

Added `_venue_close_target_ceiling(decision_time_utc)`: the date portion of
`(decision_time - 12h)` in UTC. Any `target_date <= ceiling` could be
POST_TRADING now (its F1-12:00-UTC close has fired). The SQL `OR` clause
widens both FSR and DAY0 bands:

```sql
(e.event_type = 'FORECAST_SNAPSHOT_READY'
   AND (   json_extract(e.payload_json, '$.target_date') < ?           -- frontier_floor (local-day path)
        OR json_extract(e.payload_json, '$.target_date') <= ?))        -- venue_close_ceiling (new)
```

### 2. Venue-close predicate added (`EventStore._venue_closed_in_phase`)

Static method mirrors `reactor._venue_market_closed_horizon` (horizon b)
exactly. Uses `market_phase_for_decision` with `_f1_fallback_end_utc` — the
SAME authority, no new clock, no venue HTTP probe:

```python
phase = market_phase_for_decision(
    target_local_date=target_local_date,
    city_timezone=tz,
    decision_time_utc=decision_time_utc,
    polymarket_start_utc=None,
    polymarket_end_utc=_f1_fallback_end_utc(target_local_date),
)
return phase in (MarketPhase.POST_TRADING, MarketPhase.RESOLVED)
```

Returns `False` on ANY exception (fail-closed).

### 3. Archive predicate extended

```python
if self._strictly_past_in_tz(city, target_date, decision_time_utc) or \
   self._venue_closed_in_phase(city, target_date, decision_time_utc):
    expired_ids.append(event_id)
```

### 4. Broken test updated (`test_archive_day0_events_swept.py`)

`test_day0_frontier_band_settled_is_swept_fsr_margin_preserved` asserted that
`Auckland/2026-06-05` FSR at decision `2026-06-05T12:30Z` stayed `'pending'` —
but `Auckland/2026-06-05` is BOTH POST_TRADING and local-day-past at that
decision time. The test was pinning the old cheap-string exclusion (an
implementation defect), not a real invariant. Renamed to
`test_day0_frontier_band_settled_is_swept_fsr_live_preserved` and replaced
`band_fsr` with `Auckland/2026-06-06` (venue open at decision, local day not
ended) which correctly represents a live FSR that must be kept.

## market_phase Authority Reused

```
src/strategy/market_phase.py
  MarketPhase (enum: PRE_TRADING / PRE_SETTLEMENT_DAY / SETTLEMENT_DAY / POST_TRADING / RESOLVED)
  _f1_fallback_end_utc(target_local_date) -> datetime  # 12:00 UTC of target_date
  market_phase_for_decision(target_local_date, city_timezone, decision_time_utc,
                             polymarket_start_utc, polymarket_end_utc) -> MarketPhase
```

This is the identical import the reactor uses in `_venue_market_closed_horizon`.
The two sites now share a single authority and cannot diverge on the venue-close
instant.

## Fail-Closed Guarantees

| Input state | `_venue_closed_in_phase` result | Row fate |
|---|---|---|
| city missing or empty | `False` | KEPT active |
| target_date missing or empty | `False` | KEPT active |
| city not in `runtime_cities_by_name` | `False` (tz=None early return) | KEPT active |
| tz unresolvable (any exception) | `False` (except clause) | KEPT active |
| Phase = PRE_SETTLEMENT_DAY | `False` | KEPT active |
| Phase = SETTLEMENT_DAY | `False` | KEPT active |
| Phase = POST_TRADING | `True` | SWEPT to `'expired'` |
| Phase = RESOLVED | `True` | SWEPT to `'expired'` |

Never deletes from immutable `opportunity_events` log (append-only provenance
preserved). Only marks `opportunity_event_processing.processing_status =
'expired'`. Idempotent: re-run at same decision_time is a no-op.

## Test Results

### New tests in `tests/events/test_archive_expired_sweep.py`

| Test | Result |
|---|---|
| `test_venue_closed_local_open_swept_to_expired` (a) — bug case: POST_TRADING in `[venue_close, local_day_end)` | PASS |
| `test_genuinely_live_venue_open_not_swept` (b) — live family, venue open, target tomorrow | PASS |
| `test_local_day_past_sweep_unbroken_by_venue_close_path` (c) — existing local-day path regression | PASS |
| `test_failclosed_missing_city_and_target_kept_active` (d) — unresolvable city kept active | PASS |

### Existing sweep tests (no regression)

```
tests/events/test_archive_expired_sweep.py     12 passed (8 pre-existing + 4 new)
tests/events/test_archive_day0_events_swept.py 13 passed
tests/events/                                  513 passed, 8 skipped, 2 xfailed
```

### Pre-existing failures (unrelated to this fix, confirmed on clean tree)

```
tests/money_path/test_edli_online_invariants.py  19 failed (pre-existing)
tests/money_path/test_finding_b_free_cash_bound.py  4 failed (pre-existing)
```

Both failure sets appear on the unmodified clean tree (verified via `git stash`).

## Files Changed

- `src/events/event_store.py`
  - `archive_expired_candidates`: widened SQL candidate band; added
    `_venue_close_target_ceiling` call; extended Python predicate with
    `_venue_closed_in_phase`
  - Added `EventStore._venue_closed_in_phase` static method (lines ~964-1016)
  - Added `_venue_close_target_ceiling` module-level helper (lines ~1312-1335)
- `tests/events/test_archive_expired_sweep.py`: 4 new venue-close tests appended
- `tests/events/test_archive_day0_events_swept.py`: 1 broken test corrected
  (test was pinning old cheap-string-exclusion defect, not a real invariant)
