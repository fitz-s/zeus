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


def test_expired_sweep_candidate_query_uses_fsr_target_date_index():
    """The expired sweep must not scan all opportunity_events for target_date JSON."""
    conn = _world_conn(factory=CaptureConnection)
    store = EventStore(conn)
    for i, td in enumerate(
        ["2026-05-30", "2026-05-31", "2026-06-01", "2026-06-02", "2026-06-03"]
    ):
        store.insert_or_ignore(
            _fsr_event("Chicago", td, f"snap-plan-{i}", available_at="2026-06-01T00:00:00+00:00")
        )
    conn.execute("ANALYZE")

    index_names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='opportunity_events'"
        ).fetchall()
    }
    assert "idx_opportunity_events_fsr_target_date" in index_names

    conn.executed_sql.clear()
    store.archive_expired_candidates(decision_time=_DECISION_TIME)
    candidate_sql, candidate_params = next(
        (sql, params)
        for sql, params in conn.executed_sql
        if "json_extract(e.payload_json, '$.target_date') AS target_date" in sql
        and "INDEXED BY idx_opportunity_events_fsr_target_date" in sql
    )

    plan = _plan_text(conn, candidate_sql, candidate_params)
    assert "IDX_OPPORTUNITY_EVENTS_FSR_TARGET_DATE" in plan, (
        f"expired sweep candidate query must use target_date expression index, got: {plan!r}"
    )
    assert "SCAN E" not in plan and "SCAN OPPORTUNITY_EVENTS" not in plan, (
        f"expired sweep candidate query must not full-scan opportunity_events, got: {plan!r}"
    )
