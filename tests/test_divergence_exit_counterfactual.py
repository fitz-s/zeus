import json
from pathlib import Path

from src.state.db import get_connection, init_schema
import scripts.audit_divergence_exit_counterfactual as counterfactual


def test_counterfactual_reports_delayed_and_settlement_pnl(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    positions_path = state_dir / "positions-paper.json"
    positions_path.write_text(json.dumps({
        "recent_exits": [
            {
                "trade_id": "t1",
                "city": "Paris",
                "target_date": "2026-04-03",
                "bin_label": "12°C",
                "direction": "buy_yes",
                "token_id": "yes1",
                "size_usd": 10.0,
                "entry_price": 0.5,
                "exited_at": "2026-04-02T10:00:00Z",
                "pnl": 1.0,
                "exit_trigger": "MODEL_DIVERGENCE_PANIC",
                "strategy": "opening_inertia",
            }
        ]
    }))

    db_path = state_dir / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        "INSERT INTO token_price_log (token_id, price, timestamp) VALUES ('yes1', 0.70, '2026-04-02T11:00:00Z')"
    )
    conn.execute(
        "INSERT INTO token_price_log (token_id, price, timestamp) VALUES ('yes1', 0.80, '2026-04-02T13:00:00Z')"
    )
    conn.execute(
        "INSERT INTO token_price_log (token_id, price, timestamp) VALUES ('yes1', 0.90, '2026-04-02T16:00:00Z')"
    )
    conn.execute(
        """
        INSERT INTO settlements
        (city, target_date, winning_bin, settlement_value, temperature_metric,
         physical_quantity, observation_field, data_version)
        VALUES ('Paris', '2026-04-03', '12°C', 12.0, 'high',
                'mx2t6_local_calendar_day_max', 'high_temp',
                'tigge_mx2t6_local_calendar_day_max_v1')
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(counterfactual, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(counterfactual, "DB_PATH", db_path)

    report = counterfactual.run_audit(mode="paper")
    sample = report["sample"][0]

    assert report["divergence_exits_analyzed"] == 1
    assert sample["plus_1h"]["pnl_delta"] == 3.0
    assert sample["settlement"]["pnl_delta"] == 9.0
    assert report["by_strategy_pnl_delta"]["opening_inertia"]["settlement"]["avg"] == 9.0
