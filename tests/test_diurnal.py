from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock
from src.signal.diurnal import get_peak_hour_context, build_day0_temporal_context

@patch('src.state.db.get_world_connection')
def test_diurnal_returns_correct_tuple(mock_get_conn):
    # Mock connection and rows returned by DB
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    # Let's say we have 15 hours of data, peak is round hour 16 (16:00) with avg temp 85.0
    mock_rows = [
        {"hour": h, "avg_temp": 70.0 + h if h <= 16 else 85.0 - (h - 16)*2, "std_temp": 2.0, "p_high_set": None}
        for h in range(4, 20)
    ]
    mock_cursor.fetchall.return_value = mock_rows
    mock_cursor.fetchone.return_value = None  # No monthly data → fall through to heuristic
    mock_conn.execute.return_value = mock_cursor
    mock_get_conn.return_value = mock_conn

    # 1. Test before peak
    peak, conf, reason = get_peak_hour_context("Miami", date(2026, 4, 1), 10)
    assert peak == 16
    assert conf == 0.1
    assert reason == "well_before_peak"

    # 2. Test exactly at peak
    peak, conf, reason = get_peak_hour_context("Miami", date(2026, 4, 1), 16)
    assert peak == 16
    assert conf == 0.5
    assert reason == "at_peak_uncertain"

    # 3. Test after peak when temp dropped
    peak, conf, reason = get_peak_hour_context("Miami", date(2026, 4, 1), 18)
    assert peak == 16
    # Conf should be max(time_conf, drop_conf).
    # time_conf = min(0.95, 0.5 + 2 * 0.1) = 0.7
    # drop_conf = min(0.95, 0.5 + 2.5 * 0.15) = 0.875
    assert conf == 0.875
    assert reason == "heuristic_slope"

@patch('src.state.db.get_world_connection')
def test_diurnal_handles_missing_data(mock_get_conn):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    # Less than 12 rows missing data
    mock_cursor.fetchall.return_value = []
    mock_conn.execute.return_value = mock_cursor
    mock_get_conn.return_value = mock_conn

    peak, conf, reason = get_peak_hour_context("Miami", date(2026, 4, 1), 12)
    assert peak is None
    assert conf == 0.0
    assert reason == "insufficient_diurnal_data_rows"


@patch('src.state.db.get_world_connection')
def test_diurnal_uses_solar_heuristic_when_empirical_confidence_missing(mock_get_conn):
    class FakeConn:
        def execute(self, query, params=()):
            query = " ".join(query.split())
            if "FROM diurnal_curves" in query:
                return type("Cursor", (), {
                    "fetchall": lambda self: [
                        {"hour": h, "avg_temp": 70.0 + h if h <= 16 else 85.0 - (h - 16) * 2, "std_temp": 2.0, "p_high_set": None}
                        for h in range(4, 20)
                    ]
                })()
            if "FROM diurnal_peak_prob" in query:
                return type("Cursor", (), {"fetchone": lambda self: None})()
            if "FROM solar_daily" in query:
                return type("Cursor", (), {"fetchone": lambda self: {
                    "timezone": "America/New_York",
                    "sunrise_local": "2026-04-01T06:30-04:00",
                    "sunset_local": "2026-04-01T19:30-04:00",
                    "sunrise_utc": "2026-04-01T10:30+00:00",
                    "sunset_utc": "2026-04-01T23:30+00:00",
                    "utc_offset_minutes": -240,
                    "dst_active": 1,
                }})()
            raise AssertionError(query)

        def close(self):
            return None

    mock_get_conn.return_value = FakeConn()

    peak, conf, reason = get_peak_hour_context("Miami", date(2026, 4, 1), 5)
    assert peak == 16
    assert conf == 0.0
    assert reason == "solar_heuristic"

    peak, conf, reason = get_peak_hour_context("Miami", date(2026, 4, 1), 19)
    assert peak == 16
    assert conf >= 0.95
    assert reason == "solar_heuristic"


@patch('src.state.db.get_world_connection')
def test_build_day0_temporal_context_returns_typed_context(mock_get_conn):
    class FakeConn:
        def execute(self, query, params=()):
            query = " ".join(query.split())
            if "FROM diurnal_curves" in query:
                return type("Cursor", (), {
                    "fetchall": lambda self: [
                        {"hour": h, "avg_temp": 70.0 + h if h <= 16 else 85.0 - (h - 16) * 2, "std_temp": 2.0, "p_high_set": None}
                        for h in range(4, 20)
                    ]
                })()
            if "FROM diurnal_peak_prob" in query:
                return type("Cursor", (), {"fetchone": lambda self: None})()
            if "FROM solar_daily" in query:
                return type("Cursor", (), {"fetchone": lambda self: {
                    "timezone": "America/New_York",
                    "sunrise_local": "2026-04-01T06:30-04:00",
                    "sunset_local": "2026-04-01T19:30-04:00",
                    "sunrise_utc": "2026-04-01T10:30+00:00",
                    "sunset_utc": "2026-04-01T23:30+00:00",
                    "utc_offset_minutes": -240,
                    "dst_active": 1,
                }})()
            raise AssertionError(query)

        def close(self):
            return None

    mock_get_conn.return_value = FakeConn()

    ctx = build_day0_temporal_context("Miami", date(2026, 4, 1), "America/New_York", current_local_hour=12.0)
    assert ctx is not None
    assert ctx.current_local_hour == 12.0
    assert 0.0 <= ctx.daylight_progress <= 1.0
    assert ctx.current_local_timestamp.tzinfo is not None
    assert ctx.current_utc_timestamp.tzinfo is not None
    assert ctx.time_basis == "synthetic_local_hour"
    assert ctx.confidence_source in {"solar_heuristic", "seasonal_empirical", "monthly_empirical", "heuristic_slope"}


@patch('src.state.db.get_world_connection')
def test_build_day0_temporal_context_prefers_observation_timestamp(mock_get_conn):
    class FakeConn:
        def execute(self, query, params=()):
            query = " ".join(query.split())
            if "FROM diurnal_curves" in query:
                return type("Cursor", (), {
                    "fetchall": lambda self: [
                        {"hour": h, "avg_temp": 70.0 + h if h <= 16 else 85.0 - (h - 16) * 2, "std_temp": 2.0, "p_high_set": None}
                        for h in range(4, 20)
                    ]
                })()
            if "FROM diurnal_peak_prob" in query:
                return type("Cursor", (), {"fetchone": lambda self: None})()
            if "FROM solar_daily" in query:
                return type("Cursor", (), {"fetchone": lambda self: {
                    "timezone": "America/New_York",
                    "sunrise_local": "2026-04-01T06:30-04:00",
                    "sunset_local": "2026-04-01T19:30-04:00",
                    "sunrise_utc": "2026-04-01T10:30+00:00",
                    "sunset_utc": "2026-04-01T23:30+00:00",
                    "utc_offset_minutes": -240,
                    "dst_active": 1,
                }})()
            raise AssertionError(query)

        def close(self):
            return None

    mock_get_conn.return_value = FakeConn()

    ctx = build_day0_temporal_context(
        "Miami",
        date(2026, 4, 1),
        "America/New_York",
        observation_time="2026-04-01T13:45:00+00:00",
        observation_source="wu_api",
    )
    assert ctx is not None
    assert ctx.observation_instant is not None
    assert ctx.observation_instant.source == "wu_api"
    assert ctx.time_basis == "runtime_timestamp"
    assert ctx.current_utc_timestamp == datetime(2026, 4, 1, 13, 45, tzinfo=timezone.utc)
    assert ctx.current_local_timestamp.isoformat() == "2026-04-01T09:45:00-04:00"
    assert ctx.current_local_hour == 9.75


@patch('src.state.db.get_world_connection')
def test_build_day0_temporal_context_handles_cross_timezone_target_date(mock_get_conn):
    class FakeConn:
        def execute(self, query, params=()):
            query = " ".join(query.split())
            if "FROM diurnal_curves" in query:
                return type("Cursor", (), {
                    "fetchall": lambda self: [
                        {"hour": h, "avg_temp": 14.0 + h if h <= 14 else 28.0 - (h - 14) * 1.5, "std_temp": 1.5, "p_high_set": None}
                        for h in range(0, 24)
                    ]
                })()
            if "FROM diurnal_peak_prob" in query:
                return type("Cursor", (), {"fetchone": lambda self: None})()
            if "FROM solar_daily" in query:
                return type("Cursor", (), {"fetchone": lambda self: {
                    "timezone": "Asia/Tokyo",
                    "sunrise_local": "2026-04-02T05:25+09:00",
                    "sunset_local": "2026-04-02T18:02+09:00",
                    "sunrise_utc": "2026-04-01T20:25+00:00",
                    "sunset_utc": "2026-04-02T09:02+00:00",
                    "utc_offset_minutes": 540,
                    "dst_active": 0,
                }})()
            raise AssertionError(query)

        def close(self):
            return None

    mock_get_conn.return_value = FakeConn()

    ctx = build_day0_temporal_context(
        "Tokyo",
        date(2026, 4, 2),
        "Asia/Tokyo",
        observation_time="2026-04-01T18:30:00+00:00",
        observation_source="wu_api",
    )
    assert ctx is not None
    assert ctx.current_local_timestamp.isoformat() == "2026-04-02T03:30:00+09:00"
    assert ctx.current_utc_timestamp == datetime(2026, 4, 1, 18, 30, tzinfo=timezone.utc)
    assert ctx.dst_active is False
    assert ctx.utc_offset_minutes == 540
    assert ctx.current_local_timestamp.date() == date(2026, 4, 2)
