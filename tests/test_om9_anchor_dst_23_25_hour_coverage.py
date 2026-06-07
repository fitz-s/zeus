from datetime import date

from src.data.replacement_forecast_materializer import _expected_om9_hourly_count


def test_om9_expected_hourly_coverage_tracks_dst_23_and_25_hour_days() -> None:
    assert _expected_om9_hourly_count(city_timezone="Europe/London", target_date=date(2026, 3, 29)) == 23
    assert _expected_om9_hourly_count(city_timezone="Europe/London", target_date=date(2026, 10, 25)) == 25
