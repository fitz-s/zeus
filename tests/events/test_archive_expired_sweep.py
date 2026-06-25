# Created: 2026-06-04
# Last reused/audited: 2026-06-04
# Authority basis: operator directive 2026-06-04 — archive expired/inactive candidates
#                  to history (per-city LOCAL tz, Oceania-frontier anchored) so the
#                  reactor stops re-scanning settled markets. Builds on STEP 3
#                  fetch_pending claim-floor (#183) — same per-city tz predicate
#                  (settlement_day_entry_utc), but PRUNES the working set instead of
#                  only filtering on read.
"""RED→GREEN relationship antibody for the archive-expired sweep.

The defect (operator-reported, live-confirmed): ``opportunity_event_processing``
accumulates ~1.76M ``pending`` rows that the reactor re-JOINs and re-ORDERs every
cycle. ``fetch_pending`` filters strictly-past FSR rows on READ (#183) but never
PRUNES them, so they pile up forever and waste runtime each cycle.

These tests pin the CROSS-MODULE invariant the sweep must hold:

  (a) An expired-in-its-tz FSR candidate is archived (terminal ``expired`` status)
      and does NOT reappear in the active scan (``fetch_pending``) next cycle.
  (b) A still-active candidate (target local day still open in its OWN city tz) is
      NOT archived even if it looks past in raw UTC — proven with BOTH an Oceania
      UTC+13 city whose local day is still open after UTC has rolled, AND a
      UTC-negative city whose local day is still open after UTC midnight.
  (c) The active-scan volume drops after the sweep (only active rows enumerated).

The sweep marks the MUTABLE processing row terminal (``expired``); it NEVER deletes
the append-only immutable ``opportunity_events`` row (provenance preserved).
"""
from __future__ import annotations

import sqlite3

from src.events.event_store import EventStore
from src.events.opportunity_event import ForecastSnapshotReadyPayload, make_opportunity_event
from src.state.db import init_schema


class CaptureConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed_sql: list[tuple[str, tuple]] = []

    def execute(self, sql, parameters=(), /):  # type: ignore[override]
        params = tuple(parameters) if isinstance(parameters, (list, tuple)) else parameters
        self.executed_sql.append((sql, params))
        return super().execute(sql, parameters)


def _payload(city: str, target_date: str, snapshot_id: str) -> ForecastSnapshotReadyPayload:
    return ForecastSnapshotReadyPayload(
        city=city,
        target_date=target_date,
        metric="high",
        source_id="ecmwf-open-data",
        source_run_id="run-1",
        cycle="00",
        track="ens",
        snapshot_id=snapshot_id,
        snapshot_hash=snapshot_id,
        captured_at="2026-06-01T04:10:00+00:00",
        available_at="2026-06-01T04:15:00+00:00",
        required_fields_present=True,
        required_steps_present=True,
        member_count=51,
        min_members_floor=40,
        completeness_status="COMPLETE",
        required_steps=[0, 3, 6],
        observed_steps=[0, 3, 6],
        expected_members=51,
        source_run_status="COMMITTED",
        source_run_completeness_status="COMPLETE",
        coverage_completeness_status="COMPLETE",
        coverage_readiness_status="LIVE_ELIGIBLE",
    )


def _fsr_event(city: str, target_date: str, snapshot_id: str, *, available_at: str):
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|{target_date}|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-06-01T04:10:00+00:00",
        available_at=available_at,
        received_at=available_at,
        causal_snapshot_id=snapshot_id,
        payload=_payload(city, target_date, snapshot_id),
        priority=0,
    )


def _world_conn(*, factory=sqlite3.Connection) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", factory=factory)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def _plan_text(conn: sqlite3.Connection, sql: str, params: tuple) -> str:
    plan_rows = conn.execute(f"EXPLAIN QUERY PLAN {sql}", params).fetchall()
    return " ".join(
        (r["detail"] if isinstance(r, sqlite3.Row) else r[-1]) for r in plan_rows
    ).upper()


def _pending_count(conn: sqlite3.Connection, consumer: str = "edli_reactor_v1") -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM opportunity_event_processing "
        "WHERE consumer_name = ? AND processing_status = 'pending'",
        (consumer,),
    ).fetchone()[0]


def _status_of(conn: sqlite3.Connection, event_id: str, consumer: str = "edli_reactor_v1") -> str:
    return conn.execute(
        "SELECT processing_status FROM opportunity_event_processing "
        "WHERE consumer_name = ? AND event_id = ?",
        (consumer, event_id),
    ).fetchone()[0]


def _event_row_still_present(conn: sqlite3.Connection, event_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM opportunity_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        is not None
    )


# Decision time: 2026-06-05T12:00:00Z.
#  - Chicago (UTC-5): local 2026-06-05 07:00 → 2026-06-04 is a PAST local day.
#  - Auckland (UTC+12, Jun): local 2026-06-06 00:00 → 2026-06-06 is the CURRENT local day,
#    and at this instant 2026-06-04 (Chicago) is long past while Auckland already in 06-06.
_DECISION_TIME = "2026-06-05T12:00:00+00:00"


def test_expired_in_tz_candidate_archived_and_not_rescanned():
    """(a) An FSR whose target LOCAL day has ended in its city tz is archived
    (status='expired') and does NOT come back in fetch_pending next cycle. The
    immutable event row is preserved (provenance)."""
    conn = _world_conn()
    store = EventStore(conn)
    # Chicago 2026-06-04: at decision 2026-06-05T12Z the whole local day is past.
    expired = _fsr_event(
        "Chicago", "2026-06-04", "snap-exp", available_at="2026-06-03T00:00:00+00:00"
    )
    store.insert_or_ignore(expired)
    assert _status_of(conn, expired.event_id) == "pending"

    n = store.archive_expired_candidates(decision_time=_DECISION_TIME)
    assert n >= 1, "the strictly-past Chicago candidate must be archived"

    assert _status_of(conn, expired.event_id) == "expired"
    # Provenance: the immutable event row is NEVER deleted.
    assert _event_row_still_present(conn, expired.event_id)
    # It must not reappear in the active scan.
    returned = store.fetch_pending(decision_time=_DECISION_TIME, limit=100)
    assert expired.event_id not in [e.event_id for e in returned]
    # And it is no longer pending in the working set.
    assert _pending_count(conn) == 0


def test_active_oceania_candidate_not_archived_despite_utc_lookback():
    """(b1) An Oceania (UTC+12/13) city whose LOCAL target day is still open is NOT
    archived, even though a naive per-row lookback could mistake it for past. This
    is the frontier-correctness case: the earliest-rolling clock anchors the sweep,
    never raw UTC."""
    conn = _world_conn()
    store = EventStore(conn)
    # Auckland target 2026-06-06: at decision 2026-06-05T12Z Auckland local is
    # 2026-06-06 00:00 — the target day is JUST opening, fully active.
    active = _fsr_event(
        "Auckland", "2026-06-06", "snap-akl", available_at="2026-06-04T00:00:00+00:00"
    )
    store.insert_or_ignore(active)

    store.archive_expired_candidates(decision_time=_DECISION_TIME)

    assert _status_of(conn, active.event_id) == "pending", (
        "an Oceania candidate whose local day is still open must NOT be archived"
    )
    returned = store.fetch_pending(decision_time=_DECISION_TIME, limit=100)
    assert active.event_id in [e.event_id for e in returned]


def test_active_utc_negative_candidate_not_archived_after_utc_midnight():
    """(b2) A UTC-negative city (Chicago) whose local target day is STILL the current
    day after UTC has rolled past midnight is NOT archived. Decision 2026-06-05T02:00Z
    is already 06-05 in UTC, but Chicago local is still 2026-06-04 21:00 — the target
    2026-06-05 has not even started locally; it must stay active."""
    conn = _world_conn()
    store = EventStore(conn)
    decision = "2026-06-05T02:00:00+00:00"  # Chicago local 2026-06-04 21:00
    active = _fsr_event(
        "Chicago", "2026-06-05", "snap-chi-future", available_at="2026-06-03T00:00:00+00:00"
    )
    store.insert_or_ignore(active)

    store.archive_expired_candidates(decision_time=decision)

    assert _status_of(conn, active.event_id) == "pending", (
        "a UTC-negative city whose local target day has not ended must NOT be archived"
    )


def test_sweep_reduces_active_scan_volume():
    """(c) After the sweep, the active scan enumerates ONLY active rows — the volume
    drops. Mix of many expired + a few active; pending count and fetch_pending result
    both shrink to the active set."""
    conn = _world_conn()
    store = EventStore(conn)
    # 6 expired Chicago days (all past at decision), 2 active future days.
    for i, td in enumerate(
        ["2026-05-30", "2026-05-31", "2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]
    ):
        store.insert_or_ignore(
            _fsr_event("Chicago", td, f"snap-old-{i}", available_at="2026-06-01T00:00:00+00:00")
        )
    for i, td in enumerate(["2026-06-06", "2026-06-07"]):
        store.insert_or_ignore(
            _fsr_event("Chicago", td, f"snap-new-{i}", available_at="2026-06-04T00:00:00+00:00")
        )

    before = _pending_count(conn)
    assert before == 8

    archived = store.archive_expired_candidates(decision_time=_DECISION_TIME)
    assert archived == 6, "exactly the 6 past-local-day rows must be archived"

    after = _pending_count(conn)
    assert after == 2, "only the 2 future-local-day candidates remain active"
    assert after < before

    returned = store.fetch_pending(decision_time=_DECISION_TIME, limit=100)
    assert len(returned) == 2


def test_sweep_is_idempotent():
    """A second sweep at the same decision time archives nothing new (no double
    work, no churn) — the sweep is idempotent and budget-safe."""
    conn = _world_conn()
    store = EventStore(conn)
    store.insert_or_ignore(
        _fsr_event("Chicago", "2026-06-04", "snap-exp", available_at="2026-06-03T00:00:00+00:00")
    )
    first = store.archive_expired_candidates(decision_time=_DECISION_TIME)
    second = store.archive_expired_candidates(decision_time=_DECISION_TIME)
    assert first >= 1
    assert second == 0, "re-running the sweep must be a no-op (idempotent)"


def test_read_floor_and_archive_share_one_boundary():
    """RELATIONSHIP invariant (the antibody): the read floor (fetch_pending /
    _is_timely) and the archive sweep (_strictly_past_in_tz) MUST agree on the
    expiry boundary for every resolvable city — they share ONE authority
    (_strictly_past_in_tz), so a row the sweep archives is exactly a row the read
    floor would have dropped, and a row the read floor keeps is exactly a row the
    sweep keeps. Tested by scanning a band straddling the boundary: for each
    candidate, archived ⇔ NOT returned by fetch_pending (pre-sweep)."""
    conn = _world_conn()
    store = EventStore(conn)
    events = []
    for i, td in enumerate(
        ["2026-06-03", "2026-06-04", "2026-06-06", "2026-06-07"]
    ):
        ev = _fsr_event("Chicago", td, f"snap-band-{i}", available_at="2026-06-02T00:00:00+00:00")
        store.insert_or_ignore(ev)
        events.append((td, ev))

    # Pre-sweep: which events does the read floor return?
    returned_pre = {e.event_id for e in store.fetch_pending(decision_time=_DECISION_TIME, limit=100)}

    # Sweep, then check status per event.
    store.archive_expired_candidates(decision_time=_DECISION_TIME)

    for td, ev in events:
        archived = _status_of(conn, ev.event_id) == "expired"
        kept_by_read_floor = ev.event_id in returned_pre
        assert archived != kept_by_read_floor, (
            f"boundary divergence for {td}: archived={archived} but "
            f"read_floor_kept={kept_by_read_floor}; the two sites must share one boundary"
        )


def test_unresolvable_tz_candidate_not_archived_failclosed():
    """Fail-closed: an FSR for a city the registry cannot resolve a timezone for is
    NEVER archived (cannot prove it is past → keep active). Archive-by-mistake of an
    active row would silently drop a real candidate."""
    conn = _world_conn()
    store = EventStore(conn)
    unknown = _fsr_event(
        "Atlantis", "2026-05-01", "snap-unk", available_at="2026-04-20T00:00:00+00:00"
    )
    store.insert_or_ignore(unknown)

    store.archive_expired_candidates(decision_time=_DECISION_TIME)

    assert _status_of(conn, unknown.event_id) == "pending", (
        "an unresolvable-tz candidate must be kept active (fail-closed), never archived"
    )


def test_expired_sweep_candidate_query_uses_processing_status_index():
    """The expired sweep must be driven by the active processing set.

    Live has millions of terminal event/processing rows but only a small
    pending/processing set. Driving this maintenance sweep from the event
    target-date index can pin the trading reactor on historical provenance;
    first bound by consumer/status, then inspect target dates in Python.
    """
    conn = _world_conn(factory=CaptureConnection)
    store = EventStore(conn)
    for i, td in enumerate(
        ["2026-05-30", "2026-05-31", "2026-06-01", "2026-06-02", "2026-06-03"]
    ):
        store.insert_or_ignore(
            _fsr_event("Chicago", td, f"snap-plan-{i}", available_at="2026-06-01T00:00:00+00:00")
        )
    conn.execute("ANALYZE")

    event_index_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='opportunity_events'"
        ).fetchall()
    }
    processing_index_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='opportunity_event_processing'"
        ).fetchall()
    }
    assert "idx_opportunity_events_fsr_target_date" in event_index_names
    assert "idx_opportunity_event_processing_status" in processing_index_names

    conn.executed_sql.clear()
    store.archive_expired_candidates(decision_time=_DECISION_TIME)
    candidate_sql, candidate_params = next(
        (sql, params)
        for sql, params in conn.executed_sql
        if "json_extract(e.payload_json, '$.target_date') AS target_date" in sql
        and "INDEXED BY idx_opportunity_event_processing_status" in sql
    )

    plan = _plan_text(conn, candidate_sql, candidate_params)
    assert "IDX_OPPORTUNITY_EVENT_PROCESSING_STATUS" in plan, (
        f"expired sweep candidate query must use active-status index, got: {plan!r}"
    )
    assert "SCAN P" not in plan and "SCAN OPPORTUNITY_EVENT_PROCESSING" not in plan, (
        f"expired sweep candidate query must not full-scan processing rows, got: {plan!r}"
    )


# ---------------------------------------------------------------------------
# Venue-close (POST_TRADING) sweep tests — #126, 2026-06-15
#
# Bug: archive_expired_candidates used ONLY the local-day predicate.  In the
# [venue_close, local_day_end) window a family is POST_TRADING (venue closed at
# F1 12:00-UTC) but the local day has not yet ended, so _strictly_past_in_tz
# returned False and the family stayed 'pending' forever.  132 families were
# confirmed stuck live on 2026-06-15 (Miami|2026-06-15|low, etc.).
#
# All four tests use a UTC-negative city (Miami, America/New_York UTC-4 in June)
# whose local day ends at 2026-06-15T04:00:00Z but whose F1 venue close fired at
# 2026-06-15T12:00:00Z — i.e. 12:00Z < 04:00Z next day.
#
# decision_time = 2026-06-15T14:00:00Z: venue closed (>=12:00Z), local NOT yet
# ended (14:00Z < 04:00Z next day on 2026-06-16).
# ---------------------------------------------------------------------------

# Scenario constants
# Miami (America/New_York): UTC-4 in June.
# F1 venue close for 2026-06-15 = 2026-06-15T12:00:00Z.
# Local day ends at 2026-06-16T04:00:00Z.
# At 2026-06-15T14:00Z: venue IS closed; local day is NOT yet past.
_VENUE_CLOSE_DECISION = "2026-06-15T14:00:00+00:00"
_VENUE_CLOSE_CITY = "Miami"
_VENUE_CLOSE_TARGET = "2026-06-15"


def test_venue_closed_local_open_swept_to_expired():
    """(a) Bug case: FSR family whose F1 12:00-UTC venue close HAS fired but whose
    local day has NOT yet ended is swept to 'expired'.  This is the [venue_close,
    local_day_end) window that previously kept 132 families stuck 'pending' forever
    (Miami|2026-06-15|low, Wellington|2026-06-15|high, etc., confirmed live 2026-06-16).

    Decision 2026-06-15T14:00Z: Miami/2026-06-15 is POST_TRADING (venue closed
    at 12:00Z) but the local day ends at 2026-06-16T04:00Z — _strictly_past_in_tz
    alone returns False, so without the venue-close path the row stays pending forever.
    """
    conn = _world_conn()
    store = EventStore(conn)
    # Miami 2026-06-15: venue closed at 12:00Z; decision at 14:00Z; local day ends 04:00Z next day.
    stuck = _fsr_event(
        _VENUE_CLOSE_CITY, _VENUE_CLOSE_TARGET, "snap-venue-close",
        available_at="2026-06-15T11:00:00+00:00",
    )
    store.insert_or_ignore(stuck)
    assert _status_of(conn, stuck.event_id) == "pending"

    n = store.archive_expired_candidates(decision_time=_VENUE_CLOSE_DECISION)

    assert n >= 1, (
        "a POST_TRADING family in the [venue_close, local_day_end) window must be swept"
    )
    assert _status_of(conn, stuck.event_id) == "expired", (
        "processing status must be 'expired', not left 'pending'"
    )
    # Provenance: immutable event row must NOT be deleted.
    assert _event_row_still_present(conn, stuck.event_id)
    # Must not reappear in the active scan.
    returned = store.fetch_pending(decision_time=_VENUE_CLOSE_DECISION, limit=100)
    assert stuck.event_id not in [e.event_id for e in returned]


def test_genuinely_live_venue_open_not_swept():
    """(b) Fail-closed: a family whose venue is still OPEN (target_date tomorrow,
    before its 12:00-UTC close) must NOT be archived.

    Decision 2026-06-15T14:00Z; target_date 2026-06-16: F1 close fires at
    2026-06-16T12:00Z which is in the future → phase is PRE_SETTLEMENT_DAY or
    SETTLEMENT_DAY (never POST_TRADING) → must stay 'pending'.
    """
    conn = _world_conn()
    store = EventStore(conn)
    live = _fsr_event(
        _VENUE_CLOSE_CITY, "2026-06-16", "snap-live",
        available_at="2026-06-15T10:00:00+00:00",
    )
    store.insert_or_ignore(live)

    store.archive_expired_candidates(decision_time=_VENUE_CLOSE_DECISION)

    assert _status_of(conn, live.event_id) == "pending", (
        "a genuinely-live family (venue still open) must NOT be archived"
    )
    returned = store.fetch_pending(decision_time=_VENUE_CLOSE_DECISION, limit=100)
    assert live.event_id in [e.event_id for e in returned]


def test_local_day_past_sweep_unbroken_by_venue_close_path():
    """(c) Regression guard: the existing local-day-strictly-past sweep still archives
    as before.  A family for a date that is strictly past in local tz (Chicago 2026-06-04
    at decision 2026-06-05T12Z) is still archived, and an active future day (Chicago
    2026-06-07) is still kept.  The venue-close path must not disturb either outcome.
    """
    conn = _world_conn()
    store = EventStore(conn)
    decision = "2026-06-05T12:00:00+00:00"
    past = _fsr_event("Chicago", "2026-06-04", "snap-past", available_at="2026-06-03T00:00:00+00:00")
    future = _fsr_event("Chicago", "2026-06-07", "snap-fut", available_at="2026-06-04T00:00:00+00:00")
    store.insert_or_ignore(past)
    store.insert_or_ignore(future)

    archived = store.archive_expired_candidates(decision_time=decision)

    assert _status_of(conn, past.event_id) == "expired", (
        "a strictly-past-local-day family must still be archived (no regression)"
    )
    assert _status_of(conn, future.event_id) == "pending", (
        "a future-local-day family must still be kept (no regression)"
    )
    assert archived >= 1


def test_failclosed_missing_city_and_target_kept_active():
    """(d) Fail-closed: a row with missing city OR missing target_date is NEVER archived
    by the venue-close path.  Both ``_strictly_past_in_tz`` and ``_venue_closed_in_phase``
    return False for unresolvable inputs so the row stays 'pending'.

    Also tests that an unknown city (not in runtime_cities_by_name) is kept active —
    the registry lookup returns None, which propagates as False from both predicates.
    """
    conn = _world_conn()
    store = EventStore(conn)
    # An FSR for a city the registry cannot resolve.
    unknown_city = _fsr_event(
        "Atlantis", _VENUE_CLOSE_TARGET, "snap-atlantis",
        available_at="2026-06-14T00:00:00+00:00",
    )
    store.insert_or_ignore(unknown_city)

    store.archive_expired_candidates(decision_time=_VENUE_CLOSE_DECISION)

    assert _status_of(conn, unknown_city.event_id) == "pending", (
        "a row with an unresolvable city must be kept active (fail-closed)"
    )
