from src.engine.replay import run_replay
from src.state.db import get_connection, init_schema


def test_run_replay_allows_snapshot_only_reference_opt_in(tmp_path, monkeypatch):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO settlements (city, target_date, winning_bin, settlement_value)
        VALUES ('Paris', '2026-04-03', '12°C', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (31, 'Paris', '2026-04-03', '2026-04-02T00:00:00Z', '2026-04-03T00:00:00Z',
                '2026-04-02T08:00:00Z', '2026-04-02T08:05:00Z', 24.0, '[12.0]', '[1.0]', 2.0, 0, 'ecmwf', 'v1')
        """
    )
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES ('Paris', '2026-04-03', '12°C', 1.0, 1, 1.0, 'MAM', 'Europe', '2026-04-02T08:00:00Z', 12.0)
        """
    )
    conn.commit()
    conn.close()

    import src.engine.replay as replay_module
    import src.state.db as db_module

    original_get_connection = replay_module.get_connection
    try:
        replay_module.get_connection = lambda: db_module.get_connection(db_path)
        strict = run_replay("2026-04-03", "2026-04-03", mode="audit")
        relaxed = run_replay(
            "2026-04-03",
            "2026-04-03",
            mode="audit",
            allow_snapshot_only_reference=True,
        )
    finally:
        replay_module.get_connection = original_get_connection

    assert strict.n_replayed == 0
    assert relaxed.n_replayed >= 1
