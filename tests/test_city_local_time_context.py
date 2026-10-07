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



def test_hours_to_settlement_close_counts_real_elapsed_time_across_dst() -> None:
    """A local day is 23 h or 25 h on a transition day, not always 24 h.

    Subtracting two aware datetimes that share one ZoneInfo compares wall
    clocks and ignores the offset change, so the close must be taken in UTC.
    """
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    from src.engine.time_context import lead_hours_to_settlement_close

    tz = ZoneInfo("Europe/Helsinki")
    for target, local_day_hours in (("2026-03-29", 23.0), ("2026-10-25", 25.0)):
        local_midnight = datetime.fromisoformat(target).replace(tzinfo=tz).astimezone(timezone.utc)
        assert lead_hours_to_settlement_close(target, "Europe/Helsinki", local_midnight) == local_day_hours
        assert lead_hours_to_settlement_close(
            target, "Europe/Helsinki", local_midnight + timedelta(hours=local_day_hours)
        ) == 0.0
        assert lead_hours_to_settlement_close(
            target, "Europe/Helsinki", local_midnight + timedelta(hours=1)
        ) == local_day_hours - 1.0


def test_hours_to_settlement_close_is_unchanged_on_an_ordinary_day() -> None:
    from src.engine.time_context import lead_hours_to_settlement_close

    assert lead_hours_to_settlement_close(
        "2026-06-08", "Asia/Tokyo", datetime(2026, 6, 7, 15, 0, tzinfo=timezone.utc)
    ) == 24.0
