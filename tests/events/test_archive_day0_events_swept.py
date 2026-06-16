# Created: 2026-06-15
# Last reused or audited: 2026-06-15
# Authority basis: GOAL #83 / task #118 — DAY0_EXTREME_UPDATED events were in NEITHER
#   drain sweep (archive_expired_candidates filtered FORECAST_SNAPSHOT_READY only;
#   _CHANNEL_EVENT_TYPES is token-keyed and excludes day0). Live 2026-06-15: 1972 pending
#   day0 rows across only 152 families (890 for PAST local days, settled markets) piled up
#   at the Tier-0 claim priority and 100% starved tradeable FORECAST_SNAPSHOT_READY (the
#   rebuilt-spine trigger), which all expired unprocessed → the spine never ran → zero
#   forecast orders. This antibody pins: (1) day0 past-local-day rows expire via the
#   now-day0-aware archive_expired_candidates; (2) FSR expiry is unbroken by the
#   generalization; (3) archive_superseded_day0_events keeps only the latest day0 per
#   (city, target_date, metric) family.
"""RED→GREEN relationship antibody for the day0 queue-drain sweeps.

Companion to tests/events/test_archive_expired_sweep.py (FSR per-city-tz sweep) and
tests/events/test_archive_channel_events_superseded.py (token-keyed channel sweep).
Same append-only provenance contract: only opportunity_event_processing.processing_status
is mutated; the immutable opportunity_events row is never deleted.
"""
from __future__ import annotations

import sqlite3

from src.events.event_store import EventStore
from src.events.opportunity_event import (
    Day0ExtremeUpdatedPayload,
    ForecastSnapshotReadyPayload,
    make_opportunity_event,
)
from src.state.db import init_schema


class CaptureConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed_sql: list[tuple[str, tuple]] = []

    def execute(self, sql, parameters=(), /):  # type: ignore[override]
        params = tuple(parameters) if isinstance(parameters, (list, tuple)) else parameters
        self.executed_sql.append((sql, params))
        return super().execute(sql, parameters)


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


def _day0_event(
    city: str,
    target_date: str,
    metric: str,
    *,
    available_at: str,
    seq: int = 0,
):
    payload = Day0ExtremeUpdatedPayload(
        city=city,
        target_date=target_date,
        metric=metric,  # type: ignore[arg-type]
        settlement_source="metar",
        station_id="STN",
        observation_time=available_at,
        observation_available_at=available_at,
        raw_value=30.0 + seq,
        rounded_value=30 + seq,
        high_so_far=30.0 + seq,
    )
    return make_opportunity_event(
        event_type="DAY0_EXTREME_UPDATED",
        entity_key=f"{city}|{target_date}|{metric}|{seq}",
        source="day0",
        observed_at=available_at,
        available_at=available_at,
        received_at=available_at,
        payload=payload,
        priority=60,
    )


def _fsr_event(city: str, target_date: str, snapshot_id: str, *, available_at: str):
    payload = ForecastSnapshotReadyPayload(
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
    return make_opportunity_event(
        event_type="FORECAST_SNAPSHOT_READY",
        entity_key=f"{city}|{target_date}|high|{snapshot_id}",
        source="forecast",
        observed_at="2026-06-01T04:10:00+00:00",
        available_at=available_at,
        received_at=available_at,
        causal_snapshot_id=snapshot_id,
        payload=payload,
        priority=0,
    )


def _pending_count(conn: sqlite3.Connection, consumer: str = "edli_reactor_v1") -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM opportunity_event_processing "
        "WHERE consumer_name = ? AND processing_status = 'pending'",
        (consumer,),
    ).fetchone()[0]


def _status_of(conn: sqlite3.Connection, event_id: str, consumer: str = "edli_reactor_v1") -> str:
    row = conn.execute(
        "SELECT processing_status FROM opportunity_event_processing "
        "WHERE consumer_name = ? AND event_id = ?",
        (consumer, event_id),
    ).fetchone()
    return row[0] if row else "MISSING"


def _event_row_still_present(conn: sqlite3.Connection, event_id: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM opportunity_events WHERE event_id = ?", (event_id,)).fetchone()
        is not None
    )


# Decision time 2026-06-05T12:00:00Z: Chicago (UTC-5) local 06-05 07:00 → 06-04 is a PAST
# local day; Auckland (UTC+12) local 06-06 00:00 → 06-06 is the CURRENT local day.
_DECISION_TIME = "2026-06-05T12:00:00+00:00"


# ---------------------------------------------------------------------------
# (1) Past-local-day day0 is now swept by the generalized expiry sweep.
# ---------------------------------------------------------------------------


def test_day0_past_local_day_archived():
    """The root defect: a DAY0_EXTREME_UPDATED for a target local day that has ENDED in
    its city tz (a settled market) must now be archived 'expired' — previously it was
    excluded from the FSR-only sweep and piled up at Tier-0, starving the spine lane."""
    conn = _world_conn()
    store = EventStore(conn)
    past = _day0_event("Chicago", "2026-06-04", "high", available_at="2026-06-04T20:00:00+00:00")
    store.insert_or_ignore(past)
    assert _status_of(conn, past.event_id) == "pending"

    n = store.archive_expired_candidates(decision_time=_DECISION_TIME)

    assert n >= 1, "the strictly-past Chicago day0 event must be archived"
    assert _status_of(conn, past.event_id) == "expired"
    assert _event_row_still_present(conn, past.event_id), "immutable event row preserved"


def test_day0_active_local_day_not_archived():
    """Fail-closed correctness: a day0 whose target local day is still open (Auckland
    06-06 at decision) must NOT be archived — only settled (past-local-day) day0 go."""
    conn = _world_conn()
    store = EventStore(conn)
    active = _day0_event("Auckland", "2026-06-06", "high", available_at="2026-06-05T20:00:00+00:00")
    store.insert_or_ignore(active)

    store.archive_expired_candidates(decision_time=_DECISION_TIME)

    assert _status_of(conn, active.event_id) == "pending", (
        "a day0 whose local target day is still open must NOT be archived"
    )


def test_fsr_expiry_unbroken_by_day0_generalization():
    """Regression guard: generalizing archive_expired_candidates to also sweep day0 must
    NOT change FSR behavior — a past-local-day FSR is still archived, an active one kept."""
    conn = _world_conn()
    store = EventStore(conn)
    fsr_past = _fsr_event("Chicago", "2026-06-04", "snap-p", available_at="2026-06-03T00:00:00+00:00")
    fsr_active = _fsr_event("Auckland", "2026-06-06", "snap-a", available_at="2026-06-04T00:00:00+00:00")
    store.insert_or_ignore(fsr_past)
    store.insert_or_ignore(fsr_active)

    store.archive_expired_candidates(decision_time=_DECISION_TIME)

    assert _status_of(conn, fsr_past.event_id) == "expired"
    assert _status_of(conn, fsr_active.event_id) == "pending"


def test_day0_frontier_band_settled_is_swept_fsr_live_preserved():
    """The FRONTIER-BAND gap: a day0 whose target local day has ENDED but sits in the
    Oceania -1-day margin (e.g. yesterday) was previously SPARED — it then piled up at
    the Tier-0 claim priority and starved tradeable FSR. day0's today-inclusive frontier
    now sweeps it. A LIVE FSR (venue still open, local day not yet ended) in the same
    band must NOT be swept.

    Decision 2026-06-05T12:30Z → Auckland (UTC+12) local 2026-06-06 00:30.
    - Auckland/2026-06-05: local day ended at 12:00Z; venue closed at 12:00Z → POST_TRADING.
      Both day0 AND FSR for this settled date are swept.
    - Auckland/2026-06-06: local day NOT ended (ends at 2026-06-06T12:00Z); venue NOT closed
      (venue close for 06-06 = 2026-06-06T12:00Z, still future) → SETTLEMENT_DAY → kept.
    """
    conn = _world_conn()
    store = EventStore(conn)
    decision = "2026-06-05T12:30:00+00:00"
    # Settled day0 for 2026-06-05 (both local-day-past AND POST_TRADING) → must be swept.
    settled_day0 = _day0_event("Auckland", "2026-06-05", "high", available_at="2026-06-05T11:00:00+00:00")
    # Live FSR for the NEXT day 2026-06-06 (venue open, local day open) → must be kept.
    live_fsr = _fsr_event("Auckland", "2026-06-06", "snap-live", available_at="2026-06-05T11:00:00+00:00")
    store.insert_or_ignore(settled_day0)
    store.insert_or_ignore(live_fsr)

    store.archive_expired_candidates(decision_time=decision)

    assert _status_of(conn, settled_day0.event_id) == "expired", (
        "a settled past-local-day day0 in the frontier band must now be swept off Tier-0"
    )
    assert _status_of(conn, live_fsr.event_id) == "pending", (
        "a live FSR (venue open, local day not ended) must NOT be archived"
    )


def test_both_types_swept_in_one_pass():
    """A mixed batch of past-local-day FSR + day0 is fully drained in one sweep."""
    conn = _world_conn()
    store = EventStore(conn)
    store.insert_or_ignore(_fsr_event("Chicago", "2026-06-03", "s1", available_at="2026-06-02T00:00:00+00:00"))
    store.insert_or_ignore(_day0_event("Chicago", "2026-06-03", "high", available_at="2026-06-03T20:00:00+00:00"))
    store.insert_or_ignore(_day0_event("Chicago", "2026-06-04", "low", available_at="2026-06-04T20:00:00+00:00"))

    n = store.archive_expired_candidates(decision_time=_DECISION_TIME)
    assert n == 3
    assert _pending_count(conn) == 0


# ---------------------------------------------------------------------------
# (2) Day0 supersession: keep only the latest per (city, target_date, metric).
# ---------------------------------------------------------------------------


def test_day0_superseded_keep_latest_per_family():
    """N day0 readings for one family → only the latest available_at survives 'pending';
    the N-1 older readings are 'expired' (the running extreme only advances, so only the
    latest observation is actionable)."""
    conn = _world_conn()
    store = EventStore(conn)
    events = [
        _day0_event("Singapore", "2026-06-15", "low", available_at=f"2026-06-15T{10+i:02d}:00:00+00:00", seq=i)
        for i in range(6)
    ]
    for ev in events:
        store.insert_or_ignore(ev)

    archived = store.archive_superseded_day0_events()

    assert archived == 5, f"5 superseded day0 readings should be archived; got {archived}"
    assert _status_of(conn, events[5].event_id) == "pending", "latest reading must survive"
    for i in range(5):
        assert _status_of(conn, events[i].event_id) == "expired", f"reading {i} must be superseded"


def test_day0_families_independent():
    """Each (city, target_date, metric) family preserves its own latest independently."""
    conn = _world_conn()
    store = EventStore(conn)
    families = [
        ("Beijing", "2026-06-15", "high"),
        ("London", "2026-06-15", "low"),
        ("Tokyo", "2026-06-16", "high"),
    ]
    latest_ids = []
    for city, td, metric in families:
        evs = [
            _day0_event(city, td, metric, available_at=f"2026-06-15T{10+j:02d}:00:00+00:00", seq=j)
            for j in range(3)
        ]
        for ev in evs:
            store.insert_or_ignore(ev)
        latest_ids.append(evs[-1].event_id)

    archived = store.archive_superseded_day0_events()
    assert archived == 6, "2 older × 3 families = 6 archived"
    for eid in latest_ids:
        assert _status_of(conn, eid) == "pending", "each family's latest must survive"


def test_day0_same_city_date_distinct_metric_independent():
    """high and low for the same city/date are DISTINCT families — the latest of each is
    kept (the metric is part of the supersession key)."""
    conn = _world_conn()
    store = EventStore(conn)
    high = [
        _day0_event("Paris", "2026-06-15", "high", available_at=f"2026-06-15T{10+j:02d}:00:00+00:00", seq=j)
        for j in range(2)
    ]
    low = [
        _day0_event("Paris", "2026-06-15", "low", available_at=f"2026-06-15T{10+j:02d}:00:00+00:00", seq=10 + j)
        for j in range(2)
    ]
    for ev in (*high, *low):
        store.insert_or_ignore(ev)

    archived = store.archive_superseded_day0_events()
    assert archived == 2, "one older per metric family"
    assert _status_of(conn, high[-1].event_id) == "pending"
    assert _status_of(conn, low[-1].event_id) == "pending"


def test_day0_supersession_idempotent():
    """A second supersession pass at the same state archives nothing new."""
    conn = _world_conn()
    store = EventStore(conn)
    for j in range(5):
        store.insert_or_ignore(
            _day0_event("Wuhan", "2026-06-15", "low", available_at=f"2026-06-15T{10+j:02d}:00:00+00:00", seq=j)
        )
    first = store.archive_superseded_day0_events()
    second = store.archive_superseded_day0_events()
    assert first == 4
    assert second == 0, "second pass must be a no-op (idempotent)"


def test_day0_supersession_batch_preserves_keeper_outside_batch():
    """With a small candidate batch the keeper lookup must still consider the full active
    family stream, so the newest reading is never archived even when outside the batch."""
    conn = _world_conn()
    store = EventStore(conn)
    events = [
        _day0_event("Qingdao", "2026-06-15", "high", available_at=f"2026-06-15T{j:02d}:00:00+00:00", seq=j)
        for j in range(8)
    ]
    for ev in events:
        store.insert_or_ignore(ev)

    first = store.archive_superseded_day0_events(batch_limit=3)
    assert first == 3
    assert _status_of(conn, events[-1].event_id) == "pending", "latest keeper preserved across batches"
    for ev in events[:3]:
        assert _status_of(conn, ev.event_id) == "expired"


def test_day0_supersession_failclosed_missing_metric():
    """Fail-closed: a day0 row with no parseable metric is KEPT active (never archived)."""
    conn = _world_conn()
    store = EventStore(conn)
    import json
    from src.events.idempotency import (
        canonical_json,
        payload_hash,
        stable_event_id,
        stable_idempotency_key,
    )

    broken = {"event_type": "DAY0_EXTREME_UPDATED", "city": "Nowhere", "target_date": "2026-06-15"}
    payload_json = canonical_json(broken)
    digest = payload_hash(broken)
    idem = stable_idempotency_key("DAY0_EXTREME_UPDATED", "no-metric", "day0", "2026-06-15T00:00:00+00:00", digest)
    event_id = stable_event_id(idem)
    conn.execute(
        """
        INSERT OR IGNORE INTO opportunity_events
          (event_id, event_type, entity_key, source, observed_at, available_at, received_at,
           causal_snapshot_id, payload_hash, idempotency_key, priority, expires_at, payload_json,
           schema_version, created_at)
        VALUES (?,?,?,?,?,?,?,NULL,?,?,60,NULL,?,1,?)
        """,
        (event_id, "DAY0_EXTREME_UPDATED", "no-metric", "day0",
         "2026-06-15T00:00:00+00:00", "2026-06-15T00:00:00+00:00", "2026-06-15T00:00:00+00:00",
         digest, idem, payload_json, "2026-06-15T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO opportunity_event_processing "
        "(consumer_name, event_id, processing_status, attempt_count, updated_at) "
        "VALUES (?, ?, 'pending', 0, ?)",
        ("edli_reactor_v1", event_id, "2026-06-15T00:00:00+00:00"),
    )

    store = EventStore(conn)
    store.archive_superseded_day0_events()
    assert _status_of(conn, event_id) == "pending", "missing-metric day0 must be kept (fail-closed)"


def test_day0_supersession_tied_latest_all_kept():
    """Distinct readings sharing the max available_at are all kept (no arbitrary archive)."""
    conn = _world_conn()
    store = EventStore(conn)
    older = _day0_event("Seoul", "2026-06-15", "low", available_at="2026-06-15T10:00:00+00:00", seq=0)
    tie_a = _day0_event("Seoul", "2026-06-15", "low", available_at="2026-06-15T11:00:00+00:00", seq=1)
    tie_b = _day0_event("Seoul", "2026-06-15", "low", available_at="2026-06-15T11:00:00+00:00", seq=2)
    for ev in (older, tie_a, tie_b):
        store.insert_or_ignore(ev)

    archived = store.archive_superseded_day0_events()
    assert archived == 1
    assert _status_of(conn, older.event_id) == "expired"
    assert _status_of(conn, tie_a.event_id) == "pending"
    assert _status_of(conn, tie_b.event_id) == "pending"


def test_day0_supersession_candidate_query_uses_processing_status_index():
    """Live-perf antibody: the candidate-batch query must not full-scan
    opportunity_event_processing (the pin that starved the reactor on the channel sweep)."""
    conn = _world_conn(factory=CaptureConnection)
    store = EventStore(conn)
    for fam in range(15):
        for j in range(20):
            store.insert_or_ignore(
                _day0_event(f"City{fam}", "2026-06-15", "high", available_at=f"2026-06-15T{j % 24:02d}:00:00+00:00", seq=fam * 20 + j)
            )
    conn.execute("ANALYZE")

    conn.executed_sql.clear()
    store.archive_superseded_day0_events()
    candidate_sql, candidate_params = next(
        (sql, params)
        for sql, params in conn.executed_sql
        if "json_extract(e.payload_json, '$.metric')      AS metric" in sql
        and "INDEXED BY idx_opportunity_event_processing_status" in sql
    )
    plan = _plan_text(conn, candidate_sql, candidate_params)
    assert "IDX_OPPORTUNITY_EVENT_PROCESSING_STATUS" in plan, (
        f"day0 candidate query must use active-status index, got: {plan!r}"
    )
    assert "SCAN P" not in plan, (
        f"day0 candidate query must not full-scan opportunity_event_processing, got: {plan!r}"
    )
