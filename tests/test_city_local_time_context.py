from datetime import datetime, timezone

from src.engine.time_context import (
    city_local_date_at,
    city_local_fetch_window,
    has_city_local_day_started,
)


def test_city_local_date_at_uses_settlement_timezone_not_utc_date() -> None:
    now_utc = datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc)

    assert now_utc.date().isoformat() == "2026-06-07"
    assert city_local_date_at("Asia/Tokyo", now_utc).isoformat() == "2026-06-08"
    assert city_local_date_at("America/Los_Angeles", now_utc).isoformat() == "2026-06-07"


def test_city_local_fetch_window_includes_started_east_of_utc_day0() -> None:
    start_date, end_date = city_local_fetch_window(
        "Asia/Tokyo",
        reference_time=datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc),
        days_back=1,
    )

    assert start_date.isoformat() == "2026-06-07"
    assert end_date.isoformat() == "2026-06-08"


def test_day0_started_uses_city_local_midnight_not_utc_midnight() -> None:
    now_utc = datetime(2026, 6, 7, 16, 0, tzinfo=timezone.utc)

    assert has_city_local_day_started("2026-06-08", "Asia/Tokyo", now_utc)
    assert not has_city_local_day_started("2026-06-08", "America/Los_Angeles", now_utc)

