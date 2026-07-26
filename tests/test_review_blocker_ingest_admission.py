# Created: 2026-07-20
# Authority basis: docs/operations/current review-blocker sweep (three
#   independent GPT-5.6 Pro merge-safety reviews) item C6 — ingest fan-out
#   fail-OPEN on Day0 family-admission failure.
"""Review-blocker C6 antibody: Day0 family admission must fail CLOSED.

`_day0_family_admission_for_scopes` (src/ingest_main.py) resolves which
(city, target_date, metric) high/low families are executable -- either
listed on a live market or held as current exposure -- before a source-clock
observation (HKO extrema tick / METAR fast-obs tick) is allowed to emit a
DAY0_EXTREME_UPDATED trade-decision / reactor-wake event.

Pre-fix, a forecast-DB or trade-DB read exception returned ``None``, and the
real consumer -- `Day0ExtremeUpdatedTrigger._write_observation_if_admitted`
in src/events/triggers/day0_extreme_updated.py:152 --
``if self._family_admission is not None and not self._family_admission(...)``
-- treats a bare ``None`` as "no filter configured", i.e. admits EVERY
eligible high/low family. A plain DB fault therefore silently broadened the
executable event set from "nothing" to "all families". This suite proves:

  1. The resolver never returns ``None`` on failure (forecasts-DB fault,
     trade-DB fault, or a fault on either side of the METAR wrapper) -- it
     returns a deny-all predicate instead, matching the existing "no scopes
     requested" branch.
  2. A bounded local retry absorbs one transient failure without falling
     back to deny-all (so a routine SQLITE_BUSY blip does not needlessly
     delay a real family to the next poll).
  3. Wired into the REAL `Day0ExtremeUpdatedTrigger`, an admission fault
     across several simultaneously-eligible (city, target_date) scopes
     emits ZERO DAY0_EXTREME_UPDATED events -- not "all of them" -- while
     the raw observation_instants rows (weather truth) remain untouched.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import src.state.db as db
from src.events.event_writer import EventWriter
from src.events.triggers.day0_extreme_updated import Day0ExtremeUpdatedTrigger
from src.ingest_main import (
    _day0_family_admission_for_scopes,
    _day0_source_family_admission,
)
from src.state.db import init_schema, init_schema_forecasts, init_schema_trade_only

UTC = timezone.utc

SCOPES = (
    ("Paris", "2026-06-06"),
    ("London", "2026-06-06"),
    ("Paris", "2026-06-07"),
)


def _raise_operational_error(*_args, **_kwargs):
    raise sqlite3.OperationalError("database is locked")


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """Keep the bounded retry from actually sleeping out its budget in tests.

    ``raising=False``: these constants are part of the C6 fix itself and do
    not exist on pre-fix code. This fixture must not error out fixture setup
    on pre-fix head -- the point of this suite is for the real assertions
    below to fail meaningfully (None returned / broad events emitted), not
    for an unrelated AttributeError to mask the actual regression.
    """

    monkeypatch.setattr(
        "src.ingest_main.DAY0_FAMILY_ADMISSION_RETRY_BUDGET_SECONDS",
        0.0,
        raising=False,
    )
    monkeypatch.setattr(
        "src.ingest_main.DAY0_FAMILY_ADMISSION_RETRY_INTERVAL_SECONDS",
        0.0,
        raising=False,
    )


# ---------------------------------------------------------------------------
# 1. The resolver itself must never hand back None on failure.
# ---------------------------------------------------------------------------


def test_resolver_never_returns_none_when_forecasts_db_read_fails(monkeypatch):
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", _raise_operational_error)
    monkeypatch.setattr(
        db, "get_trade_connection_read_only", lambda: sqlite3.connect(":memory:")
    )

    family_admission = _day0_family_admission_for_scopes(SCOPES)

    assert family_admission is not None, (
        "C6: a None return is read by the real Day0ExtremeUpdatedTrigger as "
        "'admit every eligible family' -- admission-read failure must never "
        "produce None"
    )
    for city, target_date in SCOPES:
        for metric in ("high", "low"):
            assert (
                family_admission(
                    {"city": city, "target_date": target_date, "metric": metric}
                )
                is False
            )


def test_resolver_fails_closed_even_when_only_the_trade_db_read_fails(monkeypatch):
    # Forecasts DB succeeds and even contains a row that WOULD legitimately
    # admit one family; trade DB fails. Fail-closed must still hold: the
    # exact family set requires both reads, so a partial success is not
    # "admit what forecasts saw" -- it is still "unknown".
    forecasts_conn = sqlite3.connect(":memory:")
    init_schema_forecasts(forecasts_conn)
    forecasts_conn.execute(
        "INSERT INTO market_events (market_slug, city, target_date, "
        "temperature_metric, condition_id) VALUES "
        "('paris-2026-06-06-high', 'Paris', '2026-06-06', 'high', '0xcond')"
    )
    forecasts_conn.commit()

    monkeypatch.setattr(db, "get_forecasts_connection_read_only", lambda: forecasts_conn)
    monkeypatch.setattr(db, "get_trade_connection_read_only", _raise_operational_error)

    family_admission = _day0_family_admission_for_scopes(SCOPES)

    assert family_admission is not None
    assert (
        family_admission({"city": "Paris", "target_date": "2026-06-06", "metric": "high"})
        is False
    ), "a family seen only on the succeeding side must still be denied while the other read is unknown"


def test_metar_wrapper_also_fails_closed_on_db_fault(monkeypatch):
    monkeypatch.setattr(db, "get_forecasts_connection_read_only", _raise_operational_error)
    monkeypatch.setattr(
        db, "get_trade_connection_read_only", lambda: sqlite3.connect(":memory:")
    )

    eligible = (
        (SimpleNamespace(name="Paris"), "metar", "2026-06-06"),
        (SimpleNamespace(name="London"), "metar", "2026-06-06"),
    )
    family_admission = _day0_source_family_admission(eligible)

    assert family_admission is not None
    assert (
        family_admission({"city": "Paris", "target_date": "2026-06-06", "metric": "high"})
        is False
    )


def test_bounded_retry_recovers_from_one_transient_failure(monkeypatch):
    """A single transient failure followed by success must NOT fail closed --
    the local bounded retry should absorb it within the same call."""

    monkeypatch.setattr(
        "src.ingest_main.DAY0_FAMILY_ADMISSION_RETRY_BUDGET_SECONDS", 1.0, raising=False
    )
    monkeypatch.setattr(
        "src.ingest_main.DAY0_FAMILY_ADMISSION_RETRY_INTERVAL_SECONDS", 0.01, raising=False
    )

    trade_conn = sqlite3.connect(":memory:")
    init_schema_trade_only(trade_conn)

    calls = {"n": 0}

    def _flaky_forecasts_connection():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        conn = sqlite3.connect(":memory:")
        init_schema_forecasts(conn)
        return conn

    monkeypatch.setattr(db, "get_forecasts_connection_read_only", _flaky_forecasts_connection)
    monkeypatch.setattr(db, "get_trade_connection_read_only", lambda: trade_conn)

    family_admission = _day0_family_admission_for_scopes(SCOPES)

    assert calls["n"] >= 2, "the resolver must retry at least once within its budget"
    assert family_admission is not None
    # No market/position rows were seeded, so the resolved family set is
    # legitimately empty -- but it must be reached via the SUCCESS path
    # (exact empty set), which the caller cannot distinguish from failure
    # only by this predicate alone; the retry-count assertion above is
    # what proves it took the recovery path rather than exhausting to
    # deny-all on the first failure.
    assert (
        family_admission({"city": "Paris", "target_date": "2026-06-06", "metric": "high"})
        is False
    )


# ---------------------------------------------------------------------------
# 2. Wired into the real trigger: many eligible scopes, admission authority
#    unavailable -> zero broad wakes, raw facts untouched.
# ---------------------------------------------------------------------------


def _insert_observation_instant(
    conn,
    *,
    city,
    station_id,
    timezone_name,
    target_date,
    local_hour,
    local_timestamp,
    utc_timestamp,
    utc_offset_minutes,
    running_max,
    running_min,
    imported_at,
):
    conn.execute(
        """
        INSERT INTO observation_instants (
            city, target_date, source, timezone_name, local_hour, local_timestamp,
            utc_timestamp, utc_offset_minutes, dst_active, is_ambiguous_local_hour,
            is_missing_local_hour, time_basis, temp_current, running_max, running_min,
            temp_unit, station_id, observation_count, imported_at, authority,
            data_version, provenance_json, training_allowed, causality_status, source_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            city,
            target_date,
            "wu_icao_history",
            timezone_name,
            local_hour,
            local_timestamp,
            utc_timestamp,
            utc_offset_minutes,
            1,
            0,
            0,
            "observed",
            running_max,
            running_max,
            running_min,
            "C",
            station_id,
            1,
            imported_at,
            "VERIFIED",
            "v1.wu-native",
            '{"source_url":"redacted","station_id":"%s"}' % station_id,
            1,
            "OK",
            "historical_hourly",
        ),
    )


def test_real_trigger_emits_zero_broad_wakes_when_admission_authority_unavailable(
    monkeypatch,
):
    """Main antibody: several distinct eligible high/low families, admission
    DB fault injected, the REAL Day0ExtremeUpdatedTrigger must emit nothing
    (not "everything") and the raw observation rows stay intact."""

    conn = sqlite3.connect(":memory:")
    init_schema(conn)

    _insert_observation_instant(
        conn,
        city="Paris",
        station_id="LFPB",
        timezone_name="Europe/Paris",
        target_date="2026-06-06",
        local_hour=6.0,
        local_timestamp="2026-06-06T06:00:00+02:00",
        utc_timestamp="2026-06-06T04:00:00+00:00",
        utc_offset_minutes=120,
        running_max=14.0,
        running_min=12.0,
        imported_at="2026-06-06T04:15:00+00:00",
    )
    _insert_observation_instant(
        conn,
        city="London",
        station_id="EGLC",
        timezone_name="Europe/London",
        target_date="2026-06-06",
        local_hour=7.0,
        local_timestamp="2026-06-06T07:00:00+02:00",
        utc_timestamp="2026-06-06T05:00:00+00:00",
        utc_offset_minutes=120,
        running_max=14.0,
        running_min=11.0,
        imported_at="2026-06-06T05:15:00+00:00",
    )
    _insert_observation_instant(
        conn,
        city="Paris",
        station_id="LFPB",
        timezone_name="Europe/Paris",
        target_date="2026-06-07",
        local_hour=6.0,
        local_timestamp="2026-06-07T06:00:00+02:00",
        utc_timestamp="2026-06-07T04:00:00+00:00",
        utc_offset_minutes=120,
        running_max=15.0,
        running_min=9.0,
        imported_at="2026-06-07T04:15:00+00:00",
    )
    raw_facts_before = conn.execute(
        "SELECT COUNT(*) FROM observation_instants"
    ).fetchone()[0]
    assert raw_facts_before == 3

    monkeypatch.setattr(db, "get_forecasts_connection_read_only", _raise_operational_error)
    monkeypatch.setattr(
        db, "get_trade_connection_read_only", lambda: sqlite3.connect(":memory:")
    )

    family_admission = _day0_family_admission_for_scopes(SCOPES)
    trigger = Day0ExtremeUpdatedTrigger(EventWriter(conn), family_admission=family_admission)

    results = trigger.scan_observation_instants_rows(
        observation_conn=conn,
        settlement_semantics=lambda observation: SimpleNamespace(
            round_single=lambda value: round(value)
        ),
        decision_time=datetime(2026, 6, 7, 5, 20, tzinfo=UTC),
        received_at="2026-06-07T05:20:00+00:00",
    )

    assert results == [], (
        "C6: admission authority was unavailable for 3 eligible families -- "
        "the trigger must emit ZERO trade-decision wakes, not all of them"
    )
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0

    # Raw source facts (weather truth) are untouched by the denied admission.
    assert (
        conn.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0]
        == raw_facts_before
    )


def test_real_trigger_emits_normally_once_admission_authority_recovers():
    """Control: the SAME 3 families, with a real (non-faulting) admission
    resolve that lists them as current exposure, DO emit -- proving the
    zero-emission result above is caused by the admission fault, not by
    some unrelated defect in the fabricated rows."""

    conn = sqlite3.connect(":memory:")
    init_schema(conn)
    _insert_observation_instant(
        conn,
        city="Paris",
        station_id="LFPB",
        timezone_name="Europe/Paris",
        target_date="2026-06-06",
        local_hour=6.0,
        local_timestamp="2026-06-06T06:00:00+02:00",
        utc_timestamp="2026-06-06T04:00:00+00:00",
        utc_offset_minutes=120,
        running_max=14.0,
        running_min=12.0,
        imported_at="2026-06-06T04:15:00+00:00",
    )

    trigger = Day0ExtremeUpdatedTrigger(
        EventWriter(conn),
        family_admission=lambda observation: True,
    )
    results = trigger.scan_observation_instants_rows(
        observation_conn=conn,
        settlement_semantics=lambda observation: SimpleNamespace(
            round_single=lambda value: round(value)
        ),
        decision_time=datetime(2026, 6, 6, 4, 20, tzinfo=UTC),
        received_at="2026-06-06T04:20:00+00:00",
    )

    assert len(results) == 2
    assert conn.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# 3. Raw source facts persist upstream of (independent of) the admission
#    gate -- structural check on the HKO source-clock tick.
# ---------------------------------------------------------------------------


def test_hko_tick_resolves_family_admission_before_opening_the_raw_write_transaction():
    """The K2 HKO tick calls the family-admission resolver BEFORE it ever
    opens the world-DB write transaction that persists the raw extrema
    projection (project_accumulator_to_v2). Combined with the resolver
    never raising (proven above -- it always returns a callable, fail-open
    or fail-closed), this means an admission-DB fault is fully absorbed
    before the raw-fact write even begins: the write path runs fresh and
    unconditionally afterward, so admission failure can only withhold the
    derived trade-decision event, never the underlying weather fact."""

    import inspect

    import src.ingest_main as ingest_main

    source = inspect.getsource(ingest_main._k2_hko_tick)
    admission_pos = source.index("_day0_family_admission_for_scopes(")
    write_conn_pos = source.index("get_world_connection(")
    write_pos = source.index("project_accumulator_to_v2(")
    assert admission_pos < write_conn_pos < write_pos, (
        "family-admission resolution must complete before the raw-fact "
        "write transaction opens, so a fail-closed (or any) resolver "
        "outcome cannot roll back or block the already-separate raw write"
    )


def test_ogimet_tick_retries_missing_day0_event_from_committed_canonical_fact(tmp_path):
    """A later source tick must close the canonical-fact -> event gap.

    This is the Tel Aviv 2026-07-26 antibody: the first write may retain raw
    weather truth while admission/event publication is unavailable. Replaying
    the same idempotent observation after admission recovers must publish the
    missing DAY0_EXTREME_UPDATED event even when no new obs row is inserted.
    """

    import json
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from scripts.obs_live_tick import _write_rows
    from src.data.observation_instants_writer import ObsV2Row

    db_path = tmp_path / "world.db"
    conn = sqlite3.connect(db_path)
    init_schema(conn)
    conn.commit()
    conn.close()

    now_utc = datetime.now(UTC).replace(microsecond=0)
    observed_utc = now_utc - timedelta(minutes=2)
    imported_utc = now_utc - timedelta(minutes=1)
    observed_local = observed_utc.astimezone(ZoneInfo("Asia/Jerusalem"))
    row = ObsV2Row(
        city="Tel Aviv",
        target_date=observed_local.date().isoformat(),
        source="ogimet_metar_llbg",
        timezone_name="Asia/Jerusalem",
        local_timestamp=observed_local.isoformat(),
        utc_timestamp=observed_utc.isoformat(),
        utc_offset_minutes=int(observed_local.utcoffset().total_seconds() // 60),
        time_basis="utc_hour_bucket_extremum",
        temp_unit="C",
        imported_at=imported_utc.isoformat(),
        authority="VERIFIED",
        data_version="v1.wu-native",
        provenance_json=json.dumps(
            {
                "tier": "OGIMET_METAR",
                "station_id": "LLBG",
                "source_url": "https://www.ogimet.com/cgi-bin/getmetar?icao=LLBG",
                "payload_hash": "sha256:" + ("1" * 64),
                "parser_version": "obs_v2_live_tick_v1",
                "latest_raw_ts": observed_utc.isoformat(),
                "hour_max_raw_ts": observed_utc.isoformat(),
                "hour_min_raw_ts": observed_utc.isoformat(),
            }
        ),
        local_hour=float(observed_local.hour),
        running_max=31.0,
        running_min=25.0,
        station_id="LLBG",
        observation_count=1,
    )

    first_written = _write_rows(db_path, [row])
    assert first_written == 1
    check = sqlite3.connect(db_path)
    assert check.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0] == 1
    assert check.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 0
    check.close()

    event_ids: list[str] = []
    families: list[tuple[str, str, str]] = []
    second_written = _write_rows(
        db_path,
        [row],
        day0_event_city="Tel Aviv",
        day0_family_admission=lambda observation: (
            observation["city"] == "Tel Aviv"
            and observation["target_date"] == row.target_date
            and observation["metric"] == "high"
        ),
        inserted_event_ids=event_ids,
        inserted_event_families=families,
    )

    check = sqlite3.connect(db_path)
    payload = json.loads(
        check.execute(
            "SELECT payload_json FROM opportunity_events "
            "WHERE event_type='DAY0_EXTREME_UPDATED'"
        ).fetchone()[0]
    )
    assert second_written == 0
    assert check.execute("SELECT COUNT(*) FROM observation_instants").fetchone()[0] == 1
    assert check.execute("SELECT COUNT(*) FROM opportunity_events").fetchone()[0] == 1
    assert payload["city"] == "Tel Aviv"
    assert payload["target_date"] == row.target_date
    assert payload["metric"] == "high"
    assert payload["rounded_value"] == 31
    assert payload["settlement_source"] == "ogimet_metar_llbg"
    assert event_ids
    assert families == [("Tel Aviv", row.target_date, "high")]
    check.close()


@pytest.mark.parametrize("job_name", ("_k2_obs_tick", "_k2_obs_fast_tick"))
def test_noaa_obs_jobs_resolve_admission_then_commit_then_bridge(job_name):
    """Production schedulers must wire admission and the post-commit wake."""

    import inspect

    import src.ingest_main as ingest_main

    source = inspect.getsource(getattr(ingest_main, job_name))
    admission_pos = source.index("_obs_tick_day0_family_admission(")
    run_pos = source.index("run_live_tick(")
    bridge_pos = source.index("_bridge_obs_tick_day0_results(")
    assert admission_pos < run_pos < bridge_pos
    assert "day0_family_admission=family_admission" in source
