from datetime import datetime, timedelta, timezone

from src.state.db import get_connection, init_schema


def _seed_rainstorm_observation_instants(db_path):
    conn = get_connection(db_path)
    conn.execute(
        """
        CREATE TABLE observation_instants (
            city TEXT,
            target_date TEXT,
            source TEXT,
            timezone_name TEXT,
            local_hour REAL,
            local_timestamp TEXT,
            utc_timestamp TEXT,
            utc_offset_minutes INTEGER,
            dst_active INTEGER,
            is_ambiguous_local_hour INTEGER,
            is_missing_local_hour INTEGER,
            time_basis TEXT,
            temp_current_f REAL,
            running_max_f REAL,
            delta_rate_f_per_h REAL,
            station_id TEXT,
            observation_count INTEGER,
            raw_response TEXT,
            source_file TEXT,
            imported_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def test_etl_observation_instants_loads_dst_safe_rows(tmp_path):
    from scripts import etl_observation_instants as etl
    import src.state.db as db_module

    rainstorm_db = tmp_path / "rainstorm.db"
    _seed_rainstorm_observation_instants(rainstorm_db)
    rs = get_connection(rainstorm_db)
    rs.executemany(
        """
        INSERT INTO observation_instants
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "NYC", "2025-03-09", "meteostat_hourly", "America/New_York", 1.0,
                "2025-03-09T01:00:00-05:00", "2025-03-09T06:00:00+00:00", -300,
                0, 0, 0, "derived_local_hour", 41.0, 41.0, None, "KNYC", 1, "{}", "x.json", "2026-04-01T00:00:00+00:00",
            ),
            (
                "NYC", "2025-03-09", "meteostat_hourly", "America/New_York", 3.0,
                "2025-03-09T03:00:00-04:00", "2025-03-09T07:00:00+00:00", -240,
                1, 0, 0, "derived_local_hour", 39.0, 41.0, -2.0, "KNYC", 1, "{}", "x.json", "2026-04-01T00:00:00+00:00",
            ),
        ],
    )
    rs.commit()
    rs.close()

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.close()

    original_get_connection = etl.get_connection
    etl.get_connection = lambda: db_module.get_connection(db_path)
    try:
        result = etl.run_etl(rainstorm_db)
    finally:
        etl.get_connection = original_get_connection

    assert result["imported"] == 2
    conn = get_connection(db_path)
    row = conn.execute(
        """
        SELECT city, timezone_name, utc_offset_minutes, dst_active, temp_unit
        FROM observation_instants
        ORDER BY utc_timestamp DESC
        LIMIT 1
        """
    ).fetchone()
    conn.close()
    assert row["city"] == "NYC"
    assert row["timezone_name"] == "America/New_York"
    assert row["utc_offset_minutes"] == -240
    assert row["dst_active"] == 1
    assert row["temp_unit"] == "F"


def test_etl_hourly_observations_collapses_ambiguous_hour_to_latest_utc(tmp_path):
    from scripts import etl_hourly_observations as etl
    import src.state.db as db_module

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.executemany(
        """
        INSERT INTO observation_instants
        (city, target_date, source, timezone_name, local_hour, local_timestamp, utc_timestamp,
         utc_offset_minutes, dst_active, is_ambiguous_local_hour, is_missing_local_hour,
         time_basis, temp_current, running_max, delta_rate_per_h, temp_unit, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "NYC", "2025-11-02", "meteostat_hourly", "America/New_York", 1.0,
                "2025-11-02T01:00:00-04:00", "2025-11-02T05:00:00+00:00", -240,
                1, 1, 0, "derived_local_hour", 50.0, 50.0, None, "F", "2026-04-01T00:00:00+00:00",
            ),
            (
                "NYC", "2025-11-02", "meteostat_hourly", "America/New_York", 1.0,
                "2025-11-02T01:00:00-05:00", "2025-11-02T06:00:00+00:00", -300,
                0, 1, 0, "derived_local_hour", 48.0, 50.0, -2.0, "F", "2026-04-01T00:00:00+00:00",
            ),
        ],
    )
    conn.commit()
    conn.close()

    original_get_connection = etl.get_connection
    etl.get_connection = lambda: db_module.get_connection(db_path)
    try:
        result = etl.run_etl()
    finally:
        etl.get_connection = original_get_connection

    assert result["excluded_ambiguous"] == 2
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT obs_hour, temp FROM hourly_observations WHERE city='NYC' AND obs_date='2025-11-02'"
    ).fetchone()
    conn.close()
    assert row is None


def test_etl_diurnal_curves_builds_peak_prob_from_observation_instants(tmp_path):
    from scripts import etl_diurnal_curves as etl
    import src.state.db as db_module

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    base_local = datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=-5)))
    rows = []
    for day in range(5):
        target_date = f"2026-01-0{day + 1}"
        hour_data = [
            (12, 40.0 + day, 40.0 + day),
            (13, 42.0 + day, 42.0 + day),
            (14, 45.0 + day, 45.0 + day),
        ]
        for hour, temp, running_max in hour_data:
            local_ts = base_local.replace(day=day + 1, hour=hour)
            utc_ts = local_ts.astimezone(timezone.utc)
            rows.append(
                (
                    "NYC", target_date, "meteostat_hourly", "America/New_York", float(hour),
                    local_ts.isoformat(), utc_ts.isoformat(), -300, 0, 0, 0,
                    "derived_local_hour", temp, running_max, None, "F", "2026-04-01T00:00:00+00:00",
                )
            )

    conn.executemany(
        """
        INSERT INTO observation_instants
        (city, target_date, source, timezone_name, local_hour, local_timestamp, utc_timestamp,
         utc_offset_minutes, dst_active, is_ambiguous_local_hour, is_missing_local_hour,
         time_basis, temp_current, running_max, delta_rate_per_h, temp_unit, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()

    original_get_connection = etl.get_connection
    etl.get_connection = lambda: db_module.get_connection(db_path)
    try:
        result = etl.run_etl()
    finally:
        etl.get_connection = original_get_connection

    assert result["stored"] >= 3
    assert result["monthly_rows"] >= 3
    conn = get_connection(db_path)
    noon = conn.execute(
        "SELECT p_high_set FROM diurnal_curves WHERE city='NYC' AND season='DJF' AND hour=12"
    ).fetchone()
    afternoon = conn.execute(
        "SELECT p_high_set FROM diurnal_peak_prob WHERE city='NYC' AND month=1 AND hour=14"
    ).fetchone()
    conn.close()
    assert noon["p_high_set"] == 0.0
    assert afternoon["p_high_set"] == 1.0


def test_etl_diurnal_curves_excludes_ambiguous_fallback_hours(tmp_path):
    from scripts import etl_diurnal_curves as etl
    import src.state.db as db_module

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    rows = []
    for day in range(5):
        target_date = f"2025-11-0{day + 1}"
        rows.append(
            (
                "NYC", target_date, "meteostat_hourly", "America/New_York", 1.0,
                f"{target_date}T01:00:00-04:00", f"{target_date}T05:00:00+00:00", -240, 1, 1, 0,
                "derived_local_hour", 50.0, 55.0, None, "F", "2026-04-01T00:00:00+00:00",
            )
        )
        rows.append(
            (
                "NYC", target_date, "meteostat_hourly", "America/New_York", 2.0,
                f"{target_date}T02:00:00-05:00", f"{target_date}T07:00:00+00:00", -300, 0, 0, 0,
                "derived_local_hour", 48.0, 55.0, None, "F", "2026-04-01T00:00:00+00:00",
            )
        )

    conn.executemany(
        """
        INSERT INTO observation_instants
        (city, target_date, source, timezone_name, local_hour, local_timestamp, utc_timestamp,
         utc_offset_minutes, dst_active, is_ambiguous_local_hour, is_missing_local_hour,
         time_basis, temp_current, running_max, delta_rate_per_h, temp_unit, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()

    original_get_connection = etl.get_connection
    etl.get_connection = lambda: db_module.get_connection(db_path)
    try:
        result = etl.run_etl()
    finally:
        etl.get_connection = original_get_connection

    assert result["stored"] >= 1
    conn = get_connection(db_path)
    ambiguous_hour = conn.execute(
        "SELECT * FROM diurnal_curves WHERE city='NYC' AND hour=1"
    ).fetchone()
    non_ambiguous_hour = conn.execute(
        "SELECT * FROM diurnal_curves WHERE city='NYC' AND hour=2"
    ).fetchone()
    conn.close()
    assert ambiguous_hour is None
    assert non_ambiguous_hour is not None


def test_etl_diurnal_curves_collapses_multi_source_hour_to_single_sample(tmp_path):
    from scripts import etl_diurnal_curves as etl
    import src.state.db as db_module

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)

    rows = []
    for day in range(5):
        target_date = f"2026-01-0{day + 1}"
        for source, temp in [("meteostat_hourly", 40.0 + day), ("openmeteo_archive_hourly", 42.0 + day)]:
            rows.append(
                (
                    "NYC", target_date, source, "America/New_York", 12.0,
                    f"{target_date}T12:00:00-05:00", f"{target_date}T17:00:00+00:00", -300, 0, 0, 0,
                    "derived_local_hour", temp, 45.0 + day, None, "F", "2026-04-01T00:00:00+00:00",
                )
            )

    conn.executemany(
        """
        INSERT INTO observation_instants
        (city, target_date, source, timezone_name, local_hour, local_timestamp, utc_timestamp,
         utc_offset_minutes, dst_active, is_ambiguous_local_hour, is_missing_local_hour,
         time_basis, temp_current, running_max, delta_rate_per_h, temp_unit, imported_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()

    original_get_connection = etl.get_connection
    etl.get_connection = lambda: db_module.get_connection(db_path)
    try:
        result = etl.run_etl()
    finally:
        etl.get_connection = original_get_connection

    assert result["stored"] >= 1
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT avg_temp, n_samples FROM diurnal_curves WHERE city='NYC' AND season='DJF' AND hour=12"
    ).fetchone()
    conn.close()
    assert row["avg_temp"] == 43.0
    assert row["n_samples"] == 5
