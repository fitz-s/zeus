from pathlib import Path

from scripts.etl_solar_times import run_etl
from src.state.db import get_connection, init_schema


def test_etl_solar_times_loads_jsonl_into_solar_daily(tmp_path):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    source = tmp_path / "solar.jsonl"
    source.write_text(
        "\n".join([
            '{"city":"London","target_date":"2026-03-31","timezone":"Europe/London","lat":51.47,"lon":-0.46,"sunrise_local":"2026-03-31T06:40+01:00","sunset_local":"2026-03-31T19:29+01:00","sunrise_utc":"2026-03-31T05:40+00:00","sunset_utc":"2026-03-31T18:29+00:00","utc_offset_minutes":60,"dst_active":true}',
            '{"city":"NYC","target_date":"2026-03-31","timezone":"America/New_York","lat":40.77,"lon":-73.87,"sunrise_local":"2026-03-31T06:41-04:00","sunset_local":"2026-03-31T19:20-04:00","sunrise_utc":"2026-03-31T10:41+00:00","sunset_utc":"2026-03-31T23:20+00:00","utc_offset_minutes":-240,"dst_active":true}'
        ]) + "\n",
        encoding="utf-8",
    )

    # Monkeypatch by calling helper against tmp DB after redirecting get_connection.
    import scripts.etl_solar_times as etl
    import src.state.db as db_module

    original_get_connection = etl.get_connection if hasattr(etl, "get_connection") else None
    etl.get_connection = lambda: db_module.get_connection(db_path)
    try:
        result = run_etl(source)
    finally:
        if original_get_connection is not None:
            etl.get_connection = original_get_connection

    assert result["imported"] == 2

    conn = get_connection(db_path)
    rows = conn.execute("SELECT city, target_date, dst_active FROM solar_daily ORDER BY city").fetchall()
    conn.close()
    assert len(rows) == 2
    assert rows[0]["city"] == "London"
