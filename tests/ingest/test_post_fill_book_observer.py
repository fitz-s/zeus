# Created: 2026-09-08
# Last reused/audited: 2026-09-08
# Authority basis: docs/operations/current/plans/hourly_capital_gains_improvement_loop.md
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3

import pytest
from datetime import datetime, timedelta, timezone


from src.ingest import post_fill_book_observer as observer
from src.state.schema.post_fill_book_observations_schema import ensure_table

from src.ingest.post_fill_book_observer import (
    _validate_book,
    classify_source_fact,
    run_cycle,
)
from src.state import post_fill_book_repo as repo


def test_book_requires_exact_asset_and_condition_but_allows_empty_depth():
    request = {"token_id": "token", "condition_id": "condition"}
    body = json.dumps(
        {
            "asset_id": "token",
            "market": "CONDITION",
            "bids": [],
            "asks": [],
            "timestamp": 1788888161689,
        }
    ).encode()
    parsed, stamp = _validate_book(body, request)
    assert parsed["bids"] == [] and stamp == "1788888161689"


def test_book_rejects_wrong_identity_and_nonfinite_quote():
    request = {"token_id": "token", "condition_id": "condition"}
    try:
        _validate_book(
            json.dumps(
                {"asset_id": "other", "market": "condition", "bids": [], "asks": []}
            ).encode(),
            request,
        )
    except ValueError as exc:
        assert str(exc) == "BOOK_TOKEN_MISMATCH"
    else:
        raise AssertionError("wrong asset must fail")
    try:
        _validate_book(
            json.dumps(
                {
                    "asset_id": "token",
                    "market": "condition",
                    "bids": [{"price": "NaN", "size": "1"}],
                    "asks": [],
                }
            ).encode(),
            request,
        )
    except ValueError as exc:
        assert str(exc) == "BOOK_QUOTE_NONFINITE"
    else:
        raise AssertionError("nonfinite quote must fail")


def test_clock_requires_aware_venue_timestamp_and_never_substitutes_observed_at():
    protocol = {
        "registered_at": "2026-09-08T12:00:00+00:00",
        "horizon_seconds": 60,
        "window_seconds": 30,
    }
    row = {
        "command_id": "cmd",
        "trade_id": "trade",
        "venue_order_id": "order",
        "state": "CONFIRMED",
        "condition_id": "condition",
        "token_id": "token",
        "side": "BUY",
        "envelope_token_id": "token",
        "envelope_side": "BUY",
        "filled_size": "1",
        "fill_price": "0.5",
        "venue_timestamp": None,
        "observed_at": "2026-09-08T12:03:00+00:00",
    }
    assert classify_source_fact(row, protocol)[0] == "VENUE_TIMESTAMP_INVALID"
    row["venue_timestamp"] = "2026-09-08T11:59:00+00:00"
    assert classify_source_fact(row, protocol)[0] == "HISTORICAL_PRE_REGISTRATION"
    row["venue_timestamp"] = "2026-09-08T12:01:00+00:00"
    assert classify_source_fact(row, protocol) == (
        "SCHEDULED",
        "2026-09-08T12:01:00+00:00",
        "2026-09-08T12:02:00+00:00",
        "2026-09-08T12:02:30+00:00",
    )


def _seed_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      CREATE TABLE venue_trade_facts (trade_fact_id INTEGER PRIMARY KEY, trade_id TEXT, venue_order_id TEXT, command_id TEXT, state TEXT, filled_size TEXT, fill_price TEXT, fee_paid_micro INTEGER, tx_hash TEXT, source TEXT, observed_at TEXT, venue_timestamp TEXT, raw_payload_hash TEXT);
      CREATE TABLE venue_commands (command_id TEXT PRIMARY KEY, envelope_id TEXT, token_id TEXT, side TEXT);
      CREATE TABLE venue_submission_envelopes (envelope_id TEXT PRIMARY KEY, condition_id TEXT, selected_outcome_token_id TEXT, side TEXT);
    """)
    repo.register_protocol(
        conn,
        protocol_id="p",
        caller="test",
        horizon_seconds=60,
        window_seconds=30,
        clock=lambda: datetime(2026, 9, 8, 12, tzinfo=timezone.utc),
    )
    conn.execute("INSERT INTO venue_commands VALUES ('cmd','env','token','BUY')")
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES ('env','condition','token','BUY')"
    )
    conn.execute(
        "INSERT INTO venue_trade_facts VALUES (1,'trade','order','cmd','CONFIRMED','1','0.5',0,'tx','REST','2026-09-08T12:00:01+00:00','2026-09-08T12:00:00+00:00',?)",
        ("a" * 64,),
    )
    conn.commit()
    conn.close()


def test_cycle_commits_source_before_fetch_and_persists_raw_identity_valid_book(
    tmp_path, monkeypatch
):
    path = tmp_path / "trade.db"
    _seed_db(path)
    monkeypatch.setattr(
        "src.state.db.get_trade_connection", lambda **_: sqlite3.connect(path)
    )
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    body = json.dumps(
        {
            "asset_id": "token",
            "market": "condition",
            "bids": [],
            "asks": [],
            "timestamp": 1788888161689,
        }
    ).encode()
    calls = []

    def fetch(token_id, **kwargs):
        calls.append((token_id, kwargs))
        return 200, body

    result = run_cycle(
        protocol_id="p",
        fetch=fetch,
        utc_clock=lambda: datetime(2026, 9, 8, 12, 1, 1, tzinfo=timezone.utc),
    )
    assert result["captured"] == 1 and calls[0][0] == "token"
    conn = sqlite3.connect(path)
    event = conn.execute(
        "SELECT event_type,raw_body,raw_body_sha256,freshness_verified FROM post_fill_book_observation_events WHERE event_type='CAPTURED'"
    ).fetchone()
    assert event[0] == "CAPTURED" and event[1] == body and event[3] == 0


def test_cycle_records_wrong_identity_as_retryable_error_and_never_captures(
    tmp_path, monkeypatch
):
    path = tmp_path / "trade.db"
    _seed_db(path)
    monkeypatch.setattr(
        "src.state.db.get_trade_connection", lambda **_: sqlite3.connect(path)
    )
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    body = json.dumps(
        {"asset_id": "wrong", "market": "condition", "bids": [], "asks": []}
    ).encode()
    result = run_cycle(
        protocol_id="p",
        fetch=lambda *_args, **_kwargs: (200, body),
        utc_clock=lambda: datetime(2026, 9, 8, 12, 1, 1, tzinfo=timezone.utc),
    )
    assert result["errors"] == 1 and result["captured"] == 0
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT event_type,reason,raw_body FROM post_fill_book_observation_events WHERE event_type='FETCH_ERROR'"
    ).fetchone()
    assert (
        row[0] == "FETCH_ERROR" and row[1] == "BOOK_TOKEN_MISMATCH" and row[2] == body
    )


def test_left_join_missing_command_is_observed_and_cursor_advances(
    tmp_path, monkeypatch
):
    path = tmp_path / "trade.db"
    _seed_db(path)
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO venue_trade_facts VALUES (2,'missing','order-missing','missing-command','MATCHED','1','0.5',0,'tx2','REST','2026-09-08T12:00:01+00:00','2026-09-08T12:00:00+00:00',?)",
        ("b" * 64,),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        "src.state.db.get_trade_connection", lambda **_: sqlite3.connect(path)
    )
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    run_cycle(
        protocol_id="p",
        fetch=lambda *_args, **_kwargs: (200, b"{}"),
        utc_clock=lambda: datetime(2026, 9, 8, 12, 1, 1, tzinfo=timezone.utc),
    )
    conn = sqlite3.connect(path)
    assert (
        conn.execute(
            "SELECT reason FROM post_fill_book_observation_events WHERE source_trade_fact_id=2"
        ).fetchone()[0]
        == "COMMAND_OR_ENVELOPE_IDENTITY_MISSING"
    )
    assert (
        conn.execute(
            "SELECT last_trade_fact_id FROM post_fill_book_cursors WHERE protocol_id='p'"
        ).fetchone()[0]
        == 2
    )


def test_clock_rollback_after_fetch_records_error_and_never_captures(
    tmp_path, monkeypatch
):
    path = tmp_path / "trade.db"
    _seed_db(path)
    monkeypatch.setattr(
        "src.state.db.get_trade_connection", lambda **_: sqlite3.connect(path)
    )
    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    stamps = iter(
        [
            datetime(2026, 9, 8, 12, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 8, 12, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 8, 12, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 8, 12, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 8, 12, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 8, 12, 1, tzinfo=timezone.utc),
        ]
    )
    body = json.dumps(
        {"asset_id": "token", "market": "condition", "bids": [], "asks": []}
    ).encode()
    result = run_cycle(
        protocol_id="p",
        fetch=lambda *_args, **_kwargs: (200, body),
        utc_clock=lambda: next(stamps),
    )
    assert result["captured"] == 0 and result["errors"] == 1
    conn = sqlite3.connect(path)
    assert (
        conn.execute(
            "SELECT reason FROM post_fill_book_observation_events WHERE event_type='FETCH_ERROR'"
        ).fetchone()[0]
        == "CLOCK_ROLLBACK"
    )


def test_public_fetch_enforces_one_deadline_around_the_full_async_operation(
    monkeypatch,
):
    import asyncio

    from src.ingest import post_fill_book_observer as observer

    async def slow_fetch(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return 200, b"{}"

    monkeypatch.setattr(observer, "_public_book_fetch_async", slow_fetch)
    with pytest.raises(ValueError, match="HTTP_OVERALL_TIMEOUT"):
        observer.public_book_fetch("token", timeout_seconds=0.001, max_body_bytes=64)


REG = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
NOW = REG + timedelta(hours=2, minutes=30)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _fact(
    fact_id: int,
    *,
    trade_id: str = "trade-1",
    command_id: str = "cmd-1",
    state: str = "MATCHED",
    fill_time: datetime = REG + timedelta(minutes=1),
    size: str = "1",
    price: str = "0.5",
    fee: int | None = 0,
    token: str = "token-1",
    condition: str = "condition-1",
    side: str = "BUY",
) -> dict[str, object]:
    return {
        "trade_fact_id": fact_id,
        "command_id": command_id,
        "trade_id": trade_id,
        "venue_order_id": f"order-{trade_id}",
        "condition_id": condition,
        "token_id": token,
        "side": side,
        "envelope_token_id": token,
        "envelope_side": side,
        "state": state,
        "venue_timestamp": _iso(fill_time),
        "observed_at": _iso(fill_time + timedelta(seconds=1)),
        "filled_size": size,
        "fill_price": price,
        "fee_paid_micro": fee,
        "tx_hash": f"tx-{trade_id}",
        "source": "REST",
        "raw_payload_hash": hashlib.sha256(f"raw-{fact_id}".encode()).hexdigest(),
    }


def _memory_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE venue_trade_facts (trade_fact_id INTEGER PRIMARY KEY)")
    ensure_table(conn)
    return conn


def _register(conn: sqlite3.Connection, protocol_id: str = "p", *, clock=REG) -> None:
    repo.register_protocol(
        conn,
        protocol_id=protocol_id,
        caller="acceptance",
        horizon_seconds=60,
        window_seconds=30,
        clock=lambda: clock,
    )


def _observe(conn: sqlite3.Connection, protocol_id: str, rows) -> None:
    source_rows = list(rows)
    observed_times = [
        datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
        for row in source_rows
    ]
    repo.observe_source_facts(
        conn,
        protocol_id=protocol_id,
        rows=source_rows,
        observed_at=_iso(max(observed_times) + timedelta(seconds=1)),
        classify=observer.classify_source_fact,
    )


def _request(conn: sqlite3.Connection, protocol_id: str = "p") -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM post_fill_book_requests WHERE protocol_id=? ORDER BY request_id",
        (protocol_id,),
    ).fetchone()
    assert row is not None
    return row


def test_matched_to_late_confirmed_freezes_original_request_and_records_both_facts():
    conn = _memory_conn()
    _register(conn)
    first = _fact(1, size="1", price="0.50", fee=100)
    late = _fact(
        2,
        state="CONFIRMED",
        size="1.25",
        price="0.61",
        fee=900,
        fill_time=REG + timedelta(minutes=2),
    )
    _observe(conn, "p", [first])
    _observe(conn, "p", [late])

    request = _request(conn)
    assert tuple(
        request[key]
        for key in (
            "source_trade_fact_id",
            "filled_size",
            "fill_price",
            "fee_paid_micro",
        )
    ) == (1, "1", "0.50", 100)
    events = conn.execute(
        "SELECT source_trade_fact_id,source_state,event_type FROM post_fill_book_observation_events ORDER BY event_id"
    ).fetchall()
    assert [(row[0], row[1], row[2]) for row in events] == [
        (1, "MATCHED", "SOURCE_OBSERVED"),
        (2, "CONFIRMED", "SOURCE_OBSERVED"),
    ]


def test_partial_fills_with_distinct_trade_ids_are_independent_and_duplicate_is_idempotent():
    conn = _memory_conn()
    _register(conn)
    rows = [
        _fact(1, trade_id="partial-a", size="0.4"),
        _fact(
            2, trade_id="partial-b", size="0.6", fill_time=REG + timedelta(minutes=2)
        ),
    ]
    _observe(conn, "p", rows)
    before = conn.execute(
        "SELECT COUNT(*) FROM post_fill_book_observation_events"
    ).fetchone()[0]
    _observe(conn, "p", rows)

    assert (
        conn.execute("SELECT COUNT(*) FROM post_fill_book_requests").fetchone()[0] == 2
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM post_fill_book_observation_events"
        ).fetchone()[0]
        == before
    )


def test_captured_close_then_reopen_revision_does_not_create_a_second_sample(tmp_path):
    path = tmp_path / "restart.db"
    _file_schema(path, facts=[])
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _observe(conn, "p", [_fact(1)])
    request_id = _request(conn)["request_id"]
    body = _valid_body()
    repo.append_result(
        conn,
        request_id=request_id,
        protocol_id="p",
        event_type="CAPTURED",
        reason="ok",
        observed_at=_iso(REG + timedelta(minutes=2, seconds=2)),
        **_capture_fields(body),
    )
    conn.commit()
    conn.close()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _observe(conn, "p", [_fact(2, state="CONFIRMED", size="9", price="0.9", fee=999)])

    assert (
        conn.execute("SELECT COUNT(*) FROM post_fill_book_requests").fetchone()[0] == 1
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM post_fill_book_observation_events WHERE event_type='CAPTURED'"
        ).fetchone()[0]
        == 1
    )


def _file_schema(
    path: Path, *, facts: list[dict[str, object]], protocols: tuple[str, ...] = ("p",)
) -> None:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE venue_trade_facts (
            trade_fact_id INTEGER PRIMARY KEY, trade_id TEXT, venue_order_id TEXT,
            command_id TEXT, state TEXT, filled_size TEXT, fill_price TEXT,
            fee_paid_micro INTEGER, tx_hash TEXT, source TEXT, observed_at TEXT,
            venue_timestamp TEXT, raw_payload_hash TEXT
        );
        CREATE TABLE venue_commands (
            command_id TEXT PRIMARY KEY, envelope_id TEXT, token_id TEXT, side TEXT
        );
        CREATE TABLE venue_submission_envelopes (
            envelope_id TEXT PRIMARY KEY, condition_id TEXT,
            selected_outcome_token_id TEXT, side TEXT
        );
        """
    )
    ensure_table(conn)
    for protocol_id in protocols:
        _register(conn, protocol_id)
    for row in facts:
        conn.execute(
            "INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(
                row.get(key)
                for key in (
                    "trade_fact_id",
                    "trade_id",
                    "venue_order_id",
                    "command_id",
                    "state",
                    "filled_size",
                    "fill_price",
                    "fee_paid_micro",
                    "tx_hash",
                    "source",
                    "observed_at",
                    "venue_timestamp",
                    "raw_payload_hash",
                )
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO venue_commands VALUES (?,?,?,?)",
            (
                row["command_id"],
                f"env-{row['command_id']}",
                row["token_id"],
                row["side"],
            ),
        )
        conn.execute(
            "INSERT OR IGNORE INTO venue_submission_envelopes VALUES (?,?,?,?)",
            (
                f"env-{row['command_id']}",
                row["condition_id"],
                row["token_id"],
                row["side"],
            ),
        )
    conn.commit()
    conn.close()


def _valid_body(token: str = "token-1", condition: str = "condition-1") -> bytes:
    return json.dumps(
        {"asset_id": token, "market": condition, "bids": [], "asks": []}
    ).encode()


def _capture_fields(
    body: bytes, *, token: str = "token-1", condition: str = "condition-1"
) -> dict[str, object]:
    start = REG + timedelta(minutes=2)
    return {
        "fetch_started_at": _iso(start),
        "fetch_finished_at": _iso(start + timedelta(seconds=1)),
        "endpoint": observer.BOOK_ENDPOINT,
        "http_status": 200,
        "raw_body": body,
        "raw_body_sha256": hashlib.sha256(body).hexdigest(),
        "provider_asset_id": token,
        "provider_market": condition,
    }


def _patch_cycle_db(monkeypatch, path: Path):
    monkeypatch.setattr(
        "src.state.db.get_trade_connection", lambda **_: sqlite3.connect(path)
    )
    monkeypatch.setattr("src.config.state_path", lambda name: path.parent / name)


def test_http_fetch_has_no_sqlite_lease_and_reads_committed_cursor(
    tmp_path, monkeypatch
):
    path = tmp_path / "trade.db"
    row = _fact(1, fill_time=REG + timedelta(minutes=1))
    _file_schema(path, facts=[row])
    _patch_cycle_db(monkeypatch, path)
    seen: dict[str, object] = {}

    def fetch(token_id, **_kwargs):
        other = sqlite3.connect(path, timeout=0.2)
        try:
            other.execute("BEGIN IMMEDIATE")
            seen["cursor"] = other.execute(
                "SELECT last_trade_fact_id FROM post_fill_book_cursors WHERE protocol_id='p'"
            ).fetchone()[0]
            seen["requests"] = other.execute(
                "SELECT COUNT(*) FROM post_fill_book_requests WHERE protocol_id='p'"
            ).fetchone()[0]
            other.commit()
        finally:
            other.close()
        return 200, _valid_body(token_id)

    result = observer.run_cycle(
        protocol_id="p", fetch=fetch, utc_clock=lambda: REG + timedelta(minutes=2)
    )
    assert result["captured"] == 1
    assert seen == {"cursor": 1, "requests": 1}


def test_fetch_error_retries_once_within_window_then_expired_backlog_is_missed(
    tmp_path, monkeypatch
):
    path = tmp_path / "trade.db"
    row = _fact(1, fill_time=REG + timedelta(minutes=1))
    _file_schema(path, facts=[row])
    _patch_cycle_db(monkeypatch, path)
    calls: list[str] = []

    def flaky(token_id, **_kwargs):
        calls.append(token_id)
        if len(calls) == 1:
            raise OSError("simulated fetch crash")
        return 200, _valid_body(token_id)

    first = observer.run_cycle(
        protocol_id="p", fetch=flaky, utc_clock=lambda: REG + timedelta(minutes=2)
    )
    second = observer.run_cycle(
        protocol_id="p", fetch=flaky, utc_clock=lambda: REG + timedelta(minutes=2)
    )
    assert first["errors"] == 1 and first["captured"] == 0
    assert second["captured"] == 1
    assert calls == ["token-1", "token-1"]

    late = observer.run_cycle(
        protocol_id="p",
        fetch=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("expired request fetched")
        ),
        utc_clock=lambda: REG + timedelta(minutes=3),
    )
    assert late["missed"] == 0  # the successful retry already terminalized it

    # A second unhandled request is expired without invoking fetch.
    conn = sqlite3.connect(path)
    row2 = _fact(
        2, trade_id="trade-2", command_id="cmd-2", fill_time=REG + timedelta(minutes=10)
    )
    conn.execute(
        "INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        tuple(
            row2.get(key)
            for key in (
                "trade_fact_id",
                "trade_id",
                "venue_order_id",
                "command_id",
                "state",
                "filled_size",
                "fill_price",
                "fee_paid_micro",
                "tx_hash",
                "source",
                "observed_at",
                "venue_timestamp",
                "raw_payload_hash",
            )
        ),
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?)",
        ("cmd-2", "env-cmd-2", "token-1", "BUY"),
    )
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES (?,?,?,?)",
        ("env-cmd-2", "condition-1", "token-1", "BUY"),
    )
    conn.commit()
    conn.close()
    expired = observer.run_cycle(
        protocol_id="p",
        fetch=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("expired request fetched")
        ),
        utc_clock=lambda: REG + timedelta(minutes=12),
    )
    assert expired["missed"] == 1


def test_crash_after_fetch_before_result_leaves_committed_request_for_restart_retry(
    tmp_path, monkeypatch
):
    path = tmp_path / "crash.db"
    _file_schema(path, facts=[_fact(1, fill_time=REG + timedelta(minutes=1))])
    _patch_cycle_db(monkeypatch, path)

    class SyntheticCrash(BaseException):
        pass

    real_append = repo.append_result
    crashed = False

    def crash_after_capture(*args, **kwargs):
        nonlocal crashed
        if kwargs.get("event_type") == "CAPTURED" and not crashed:
            crashed = True
            raise SyntheticCrash("process died after HTTP fetch")
        return real_append(*args, **kwargs)

    monkeypatch.setattr(repo, "append_result", crash_after_capture)
    with pytest.raises(SyntheticCrash):
        observer.run_cycle(
            protocol_id="p",
            fetch=lambda token_id, **_k: (200, _valid_body(token_id)),
            utc_clock=lambda: REG + timedelta(minutes=2),
        )

    after_crash = sqlite3.connect(path)
    assert (
        after_crash.execute(
            "SELECT last_trade_fact_id FROM post_fill_book_cursors WHERE protocol_id='p'"
        ).fetchone()[0]
        == 1
    )
    assert (
        after_crash.execute(
            "SELECT COUNT(*) FROM post_fill_book_requests WHERE protocol_id='p'"
        ).fetchone()[0]
        == 1
    )
    assert (
        after_crash.execute(
            "SELECT COUNT(*) FROM post_fill_book_observation_events WHERE event_type IN ('CAPTURED','MISSED_WINDOW')"
        ).fetchone()[0]
        == 0
    )
    after_crash.close()

    monkeypatch.setattr(repo, "append_result", real_append)
    retried = observer.run_cycle(
        protocol_id="p",
        fetch=lambda token_id, **_k: (200, _valid_body(token_id)),
        utc_clock=lambda: REG + timedelta(minutes=2),
    )
    assert retried["captured"] == 1


def test_outer_transaction_rolls_back_repo_registration_and_sentinel():
    conn = _memory_conn()
    conn.execute("CREATE TABLE sentinel (value TEXT)")
    conn.execute("BEGIN")
    conn.execute("INSERT INTO sentinel VALUES ('uncommitted')")
    _register(conn, "rollback-p")
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM sentinel").fetchone()[0] == 0
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM post_fill_book_protocols WHERE protocol_id='rollback-p'"
        ).fetchone()[0]
        == 0
    )


def test_active_window_is_served_despite_expired_backlog_and_second_protocol(
    tmp_path, monkeypatch
):
    path = tmp_path / "fair.db"
    expired = _fact(
        1, trade_id="expired", command_id="old", fill_time=REG + timedelta(minutes=1)
    )
    active = _fact(
        2,
        trade_id="active",
        command_id="new",
        fill_time=REG + timedelta(hours=1, minutes=1),
        token="token-2",
        condition="condition-2",
    )
    _file_schema(path, facts=[expired], protocols=("backlog",))
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _register(conn, "active-protocol")
    conn.execute(
        "INSERT INTO venue_trade_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        tuple(
            active.get(key)
            for key in (
                "trade_fact_id",
                "trade_id",
                "venue_order_id",
                "command_id",
                "state",
                "filled_size",
                "fill_price",
                "fee_paid_micro",
                "tx_hash",
                "source",
                "observed_at",
                "venue_timestamp",
                "raw_payload_hash",
            )
        ),
    )
    conn.execute(
        "INSERT INTO venue_commands VALUES (?,?,?,?)",
        ("new", "env-new", "token-2", "BUY"),
    )
    conn.execute(
        "INSERT INTO venue_submission_envelopes VALUES (?,?,?,?)",
        ("env-new", "condition-2", "token-2", "BUY"),
    )
    conn.commit()
    conn.close()
    _patch_cycle_db(monkeypatch, path)
    tokens: list[str] = []

    def fetch(token_id, **_kwargs):
        tokens.append(token_id)
        condition = "condition-2" if token_id == "token-2" else "condition-1"
        return 200, _valid_body(token_id, condition)

    result = observer.run_cycle(
        protocol_id=None,
        fetch=fetch,
        utc_clock=lambda: REG + timedelta(hours=1, minutes=2),
        request_limit=1,
    )
    assert result["captured"] >= 1
    assert "token-2" in tokens
    assert result["missed"] >= 1
    conn = sqlite3.connect(path)
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM post_fill_book_observation_events WHERE protocol_id='active-protocol' AND event_type='CAPTURED'"
        ).fetchone()[0]
        == 1
    )
    conn.close()


def test_trade_only_schema_empty_registered_protocol_runs_without_fetch(
    tmp_path, monkeypatch
):
    from src.state.db import init_schema_trade_only

    path = tmp_path / "empty.db"
    conn = sqlite3.connect(path)
    init_schema_trade_only(conn)
    ensure_table(conn)
    _register(conn)
    conn.commit()
    conn.close()
    _patch_cycle_db(monkeypatch, path)

    def no_fetch(*_args, **_kwargs):
        raise AssertionError("empty source must not fetch")

    result = observer.run_cycle(protocol_id="p", fetch=no_fetch, utc_clock=lambda: REG)
    assert result["protocols"] == 1
    assert (
        result["source_observed"]
        == result["captured"]
        == result["errors"]
        == result["missed"]
        == 0
    )


@pytest.mark.parametrize(
    ("label", "mutator"),
    [
        ("missing-field", lambda fields: fields.pop("provider_market")),
        ("wrong-hash", lambda fields: fields.update(raw_body_sha256="0" * 64)),
        ("wrong-token", lambda fields: fields.update(provider_asset_id="other-token")),
        (
            "wrong-condition",
            lambda fields: fields.update(provider_market="other-condition"),
        ),
        (
            "before-window",
            lambda fields: fields.update(
                fetch_started_at=_iso(REG + timedelta(minutes=1, seconds=59))
            ),
        ),
        (
            "after-window",
            lambda fields: fields.update(
                fetch_finished_at=_iso(REG + timedelta(minutes=2, seconds=31))
            ),
        ),
        (
            "clock-rollback",
            lambda fields: fields.update(
                fetch_finished_at=_iso(REG + timedelta(minutes=1, seconds=1)),
                fetch_started_at=_iso(REG + timedelta(minutes=1, seconds=2)),
            ),
        ),
    ],
)
def test_capture_writer_rejects_invalid_identity_hash_or_window(label, mutator):
    del label
    conn = _memory_conn()
    _register(conn)
    _observe(conn, "p", [_fact(1), _fact(2, trade_id="negative")])
    request_ids = [
        row[0]
        for row in conn.execute(
            "SELECT request_id FROM post_fill_book_requests ORDER BY request_id"
        )
    ]
    repo.append_result(
        conn,
        request_id=request_ids[0],
        protocol_id="p",
        event_type="CAPTURED",
        reason="control",
        observed_at=_iso(REG + timedelta(minutes=2, seconds=2)),
        **_capture_fields(_valid_body()),
    )
    fields = _capture_fields(_valid_body())
    mutator(fields)

    with pytest.raises((ValueError, sqlite3.DatabaseError)):
        repo.append_result(
            conn,
            request_id=request_ids[1],
            protocol_id="p",
            event_type="CAPTURED",
            reason="invalid",
            observed_at=_iso(REG + timedelta(minutes=2, seconds=2)),
            **fields,
        )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM post_fill_book_observation_events WHERE event_type='CAPTURED'"
        ).fetchone()[0]
        == 1
    )


def test_capture_writer_accepts_complete_valid_positive_control():
    conn = _memory_conn()
    _register(conn)
    _observe(conn, "p", [_fact(1)])
    request_id = _request(conn)["request_id"]
    repo.append_result(
        conn,
        request_id=request_id,
        protocol_id="p",
        event_type="CAPTURED",
        reason="valid",
        observed_at=_iso(REG + timedelta(minutes=2, seconds=2)),
        **_capture_fields(_valid_body()),
    )
    row = conn.execute(
        "SELECT event_type,raw_body_sha256,provider_asset_id,provider_market FROM post_fill_book_observation_events WHERE event_type='CAPTURED'"
    ).fetchone()
    assert tuple(row) == (
        "CAPTURED",
        hashlib.sha256(_valid_body()).hexdigest(),
        "token-1",
        "condition-1",
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"not-json",
        b"[]",
        b'{"asset_id":"other","market":"condition-1","bids":[],"asks":[]}',
        b'{"asset_id":"token-1","market":"other","bids":[],"asks":[]}',
        b'{"asset_id":"token-1","market":"condition-1"}',
        b'{"asset_id":"token-1","market":"condition-1","bids":[{"price":"NaN","size":"1"}],"asks":[]}',
        b'{"asset_id":"token-1","market":"condition-1","bids":[{"price":"-1e-1000","size":"1"}],"asks":[]}',
        b'{"asset_id":"token-1","market":"condition-1","bids":[{"price":-1e-1000,"size":1}],"asks":[]}',
        b'{"asset_id":"token-1","market":"condition-1","bids":[{"price":1.000000000000000000001,"size":1}],"asks":[]}',
        b'{"asset_id":"token-1","market":"condition-1","bids":[{"price":"0.5","size":true}],"asks":[]}',
    ],
)
def test_writer_validates_raw_content_even_when_hash_and_metadata_match(raw):
    conn = _memory_conn()
    try:
        _register(conn)
        _observe(conn, "p", [_fact(1)])
        request_id = _request(conn)["request_id"]
        # Metadata names the right request even when the actual body does not.
        fields = _capture_fields(raw)
        with pytest.raises(ValueError):
            repo.append_result(
                conn,
                request_id=request_id,
                protocol_id="p",
                event_type="CAPTURED",
                reason="raw-validation",
                observed_at=_iso(REG + timedelta(minutes=2, seconds=2)),
                **fields,
            )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM post_fill_book_observation_events WHERE event_type='CAPTURED'"
            ).fetchone()[0]
            == 0
        )
        repo.append_result(
            conn,
            request_id=request_id,
            protocol_id="p",
            event_type="CAPTURED",
            reason="valid-control",
            observed_at=_iso(REG + timedelta(minutes=2, seconds=2)),
            **_capture_fields(_valid_body()),
        )
    finally:
        conn.close()


def test_writer_rejects_observation_before_fetch_and_capture_before_request_creation():
    conn = _memory_conn()
    try:
        _register(conn)
        repo.observe_source_facts(
            conn,
            protocol_id="p",
            rows=[_fact(1)],
            observed_at=_iso(REG + timedelta(minutes=2, seconds=5)),
            classify=observer.classify_source_fact,
        )
        request_id = _request(conn)["request_id"]
        fields = _capture_fields(_valid_body())
        with pytest.raises(ValueError, match="window invalid"):
            repo.append_result(
                conn,
                request_id=request_id,
                protocol_id="p",
                event_type="CAPTURED",
                reason="before-request",
                observed_at=_iso(REG + timedelta(minutes=2, seconds=10)),
                **fields,
            )
        fields["fetch_started_at"] = _iso(REG + timedelta(minutes=2, seconds=6))
        fields["fetch_finished_at"] = _iso(REG + timedelta(minutes=2, seconds=7))
        with pytest.raises(ValueError, match="window invalid"):
            repo.append_result(
                conn,
                request_id=request_id,
                protocol_id="p",
                event_type="CAPTURED",
                reason="before-fetch",
                observed_at=fields["fetch_started_at"],
                **fields,
            )
        repo.append_result(
            conn,
            request_id=request_id,
            protocol_id="p",
            event_type="CAPTURED",
            reason="valid-control",
            observed_at=fields["fetch_finished_at"],
            **fields,
        )
    finally:
        conn.close()


def test_connection_acquisition_failure_isolated_and_same_deadline_reaches_every_db_open(
    tmp_path, monkeypatch
):
    path = tmp_path / "isolated.db"
    _file_schema(path, facts=[_fact(1)], protocols=("broken", "healthy"))
    _patch_cycle_db(monkeypatch, path)
    mono = [100.0]
    monkeypatch.setattr(observer.time, "monotonic", lambda: mono[0])
    opens = []

    def connect(**kwargs):
        opens.append(kwargs)
        if len(opens) == 2:
            raise sqlite3.OperationalError("simulated connection failure")
        return sqlite3.connect(path)

    monkeypatch.setattr("src.state.db.get_trade_connection", connect)
    result = run_cycle(
        fetch=lambda token, **_: (200, _valid_body(token)),
        utc_clock=lambda: REG + timedelta(minutes=2),
        cycle_deadline_seconds=20,
    )
    assert result["protocol_failures"] == 1
    assert result["captured"] == 1
    assert all(
        call["deadline_monotonic"] == 120.0 and call["busy_timeout_ms"] == 0
        for call in opens
    )


def test_slow_source_connection_exhausts_cycle_without_further_db_or_http_work(
    tmp_path, monkeypatch
):
    path = tmp_path / "deadline.db"
    _file_schema(path, facts=[_fact(1)], protocols=("first", "second"))
    _patch_cycle_db(monkeypatch, path)
    mono = [100.0]
    monkeypatch.setattr(observer.time, "monotonic", lambda: mono[0])
    opens = []

    def connect(**kwargs):
        opens.append(kwargs)
        if len(opens) == 2:
            mono[0] = 121.0
        return sqlite3.connect(path)

    monkeypatch.setattr("src.state.db.get_trade_connection", connect)
    result = run_cycle(
        fetch=lambda *_a, **_k: pytest.fail("HTTP after deadline"),
        utc_clock=lambda: REG + timedelta(minutes=2),
        cycle_deadline_seconds=20,
    )
    assert result["protocol_failures"] == 1
    assert result["captured"] == 0
    assert len(opens) == 2


def test_expired_drain_stops_at_same_cycle_deadline(tmp_path, monkeypatch):
    path = tmp_path / "expired-budget.db"
    _file_schema(path, facts=[_fact(1), _fact(2, trade_id="second")])
    _patch_cycle_db(monkeypatch, path)
    mono = [100.0]
    monkeypatch.setattr(observer.time, "monotonic", lambda: mono[0])
    append = repo.append_result

    def expire_once(*args, **kwargs):
        append(*args, **kwargs)
        mono[0] = 121.0

    monkeypatch.setattr(repo, "append_result", expire_once)
    result = run_cycle(
        fetch=lambda *_a, **_k: pytest.fail("expired request fetched"),
        utc_clock=lambda: REG + timedelta(minutes=3),
        cycle_deadline_seconds=20,
    )
    assert result["missed"] == 1
    assert result["source_observed"] == 2


def test_process_lock_refuses_duplicate_worker_before_opening_database(
    tmp_path, monkeypatch
):
    import fcntl

    monkeypatch.setattr("src.config.state_path", lambda name: tmp_path / name)
    monkeypatch.setattr(
        "src.state.db.get_trade_connection",
        lambda **_: pytest.fail("locked worker opened DB"),
    )
    with (tmp_path / "post-fill-book-observer.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_cycle() == {"skipped_locked": 1}
        assert observer._LOCK.acquire(blocking=False)
        observer._LOCK.release()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def test_daemon_registers_and_invokes_passive_observer_without_other_jobs(monkeypatch):
    import logging
    from types import SimpleNamespace

    from src.ingest import post_trade_capital_daemon as daemon

    jobs = {}

    class Scheduler:
        def add_job(self, func, trigger, **kwargs):
            jobs[kwargs["id"]] = (func, trigger, kwargs)

        def get_jobs(self):
            return [SimpleNamespace(id=job_id) for job_id in jobs]

        def start(self):
            pass

    monkeypatch.setattr("apscheduler.schedulers.blocking.BlockingScheduler", Scheduler)
    monkeypatch.setattr(
        "src.data.proxy_health.bypass_dead_proxy_env_vars", lambda: None
    )
    monkeypatch.setattr(
        "src.state.db.get_trade_connection", lambda **kw: sqlite3.connect(":memory:")
    )
    monkeypatch.setattr(
        "src.state.db.get_world_connection", lambda **kw: sqlite3.connect(":memory:")
    )
    monkeypatch.setattr(daemon.signal, "signal", lambda *args: None)
    monkeypatch.setattr(daemon, "_write_post_trade_capital_heartbeat", lambda: None)
    monkeypatch.setattr(daemon, "_scheduler", None)
    monkeypatch.setattr(daemon, "_scheduler_job", lambda name: lambda func: func)
    monkeypatch.delenv("ZEUS_POST_FILL_BOOK_OBSERVER_INTERVAL_SECONDS", raising=False)
    observed = []
    monkeypatch.setattr(
        observer,
        "run_cycle",
        lambda: observed.append("all_registered") or {"captured": 1},
    )
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        daemon.main()
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)
    func, trigger, options = jobs["post_fill_book_observer"]
    assert trigger == "interval"
    assert options["seconds"] == 1.0
    assert options["max_instances"] == 1 and options["coalesce"] is True
    assert func() == {"captured": 1}
    assert observed == ["all_registered"]
    monkeypatch.setenv("ZEUS_POST_FILL_BOOK_OBSERVER_INTERVAL_SECONDS", "2.5")
    assert daemon._post_fill_book_observer_interval_seconds() == 2.5
    for raw in ("0", "-1", "nan", "inf", "invalid"):
        monkeypatch.setenv("ZEUS_POST_FILL_BOOK_OBSERVER_INTERVAL_SECONDS", raw)
        with pytest.raises(ValueError, match="finite positive"):
            daemon._post_fill_book_observer_interval_seconds()
