import json
from pathlib import Path

import scripts.backfill_cluster_taxonomy as backfill
from src.state.db import get_connection, init_schema


def test_backfill_calibration_pairs_updates_cluster_and_clears_models(tmp_path, monkeypatch):
    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO calibration_pairs
        (city, target_date, range_label, p_raw, outcome, lead_days, season, cluster, forecast_available_at, settlement_value)
        VALUES ('Paris', '2026-04-03', '12°C', 1.0, 1, 1.0, 'MAM', 'Europe', '2026-04-02T08:00:00Z', 12.0)
        """
    )
    conn.execute(
        """
        INSERT INTO platt_models
        (bucket_key, param_A, param_B, param_C, bootstrap_params_json, n_samples, brier_insample, fitted_at, is_active, input_space)
        VALUES ('Europe_MAM', 1.0, 0.0, 0.0, '[]', 20, 0.1, '2026-04-01T00:00:00Z', 1, 'raw_probability')
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(backfill, "get_connection", lambda: get_connection(db_path))
    report = backfill.backfill_calibration_pairs()

    conn = get_connection(db_path)
    cluster = conn.execute("SELECT cluster FROM calibration_pairs WHERE city = 'Paris'").fetchone()[0]
    remaining_models = conn.execute("SELECT COUNT(*) FROM platt_models").fetchone()[0]
    conn.close()

    assert report["calibration_pairs_updated"] == 1
    assert report["platt_models_cleared"] == 1
    assert cluster == "Europe-Continental"
    assert remaining_models == 0


def test_backfill_portfolio_state_updates_positions_and_recent_exits(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    portfolio_path = state_dir / "positions-paper.json"
    portfolio_path.write_text(json.dumps({
        "positions": [
            {"city": "Chicago", "cluster": "US-Midwest"},
            {"city": "Denver", "cluster": "US-Mountain"},
        ],
        "recent_exits": [
            {"city": "Los Angeles", "cluster": "US-Pacific"},
            {"city": "Paris", "cluster": ""},
        ],
    }))

    monkeypatch.setattr(backfill, "STATE_DIR", state_dir)
    report = backfill.backfill_portfolio_state()

    updated = json.loads(portfolio_path.read_text())
    assert report["portfolio_cluster_rows_updated"] == 4
    assert report["portfolio_files_touched"] == 1
    assert updated["positions"][0]["cluster"] == "US-GreatLakes"
    assert updated["positions"][1]["cluster"] == "US-Rockies"
    assert updated["recent_exits"][0]["cluster"] == "US-California-Coast"
    assert updated["recent_exits"][1]["cluster"] == "Europe-Continental"
