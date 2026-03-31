from datetime import date
from unittest.mock import patch, MagicMock
from src.signal.diurnal import get_peak_hour_context

@patch('src.state.db.get_connection')
def test_diurnal_returns_correct_tuple(mock_get_conn):
    # Mock connection and rows returned by DB
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    # Let's say we have 15 hours of data, peak is round hour 16 (16:00) with avg temp 85.0
    mock_rows = [
        {"hour": h, "avg_temp": 70.0 + h if h <= 16 else 85.0 - (h - 16)*2, "std_temp": 2.0}
        for h in range(4, 20)
    ]
    mock_cursor.fetchall.return_value = mock_rows
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
    assert reason == "data_derived"

@patch('src.state.db.get_connection')
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
