import json

from src.state.db import get_connection, init_schema


def test_backfill_semantic_snapshots_reconstructs_trade_and_portfolio_state(tmp_path):
    from scripts.backfill_semantic_snapshots import run_backfill
    import scripts.backfill_semantic_snapshots as backfill
    import src.state.db as db_module

    db_path = tmp_path / "zeus.db"
    conn = get_connection(db_path)
    init_schema(conn)
    conn.execute(
        """
        INSERT INTO ensemble_snapshots
        (snapshot_id, city, target_date, issue_time, valid_time, available_at, fetch_time,
         lead_hours, members_json, p_raw_json, spread, is_bimodal, model_version, data_version)
        VALUES (123, 'NYC', '2026-04-01', '2026-03-31T00:00:00Z', '2026-04-01T00:00:00Z',
                '2026-03-31T01:00:00Z', '2026-03-31T01:05:00Z', 24.0, '[40.0]', '[0.6]', 2.0, 0, 'ecmwf', 'live_v1')
        """
    )
    conn.execute(
        """
        INSERT INTO trade_decisions
        (market_id, bin_label, direction, size_usd, price, timestamp, forecast_snapshot_id,
         p_raw, p_posterior, edge, ci_lower, ci_upper, kelly_fraction, status, edge_source, env,
         entry_method, selected_method)
        VALUES ('real_mkt', '39-40°F', 'buy_yes', 5.0, 0.4, '2026-04-01T01:00:00+00:00', 123,
                0.6, 0.6, 0.2, 0.55, 0.65, 0.0, 'entered', 'center_buy', 'paper',
                'ens_member_counting', 'ens_member_counting')
        """
    )
    conn.commit()
    conn.close()

    positions_path = tmp_path / "positions-paper.json"
    positions_path.write_text(
        json.dumps(
            {
                "positions": [
                    {
                        "trade_id": "t1",
                        "market_id": "real_mkt",
                        "bin_label": "39-40°F",
                        "direction": "buy_yes",
                        "decision_snapshot_id": "123",
                        "entered_at": "2026-04-01T01:00:00+00:00",
                    }
                ],
                "recent_exits": [],
            }
        ),
        encoding="utf-8",
    )

    original_get_connection = backfill.get_connection
    try:
        backfill.get_connection = lambda: db_module.get_connection(db_path)
        result = run_backfill(positions_path)
    finally:
        backfill.get_connection = original_get_connection

    assert result["updated_trade_rows"] == 1
    assert result["updated_open_positions"] == 1
    assert result["remaining_null_settlement_semantics"] == 0
    assert result["remaining_null_epistemic_context"] == 0
    assert result["remaining_null_edge_context"] == 0

    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT settlement_semantics_json, epistemic_context_json, edge_context_json FROM trade_decisions"
    ).fetchone()
    conn.close()
    assert row["settlement_semantics_json"]
    assert row["epistemic_context_json"]
    assert row["edge_context_json"]

    state = json.loads(positions_path.read_text())
    pos = state["positions"][0]
    assert pos["settlement_semantics_json"]
    assert pos["epistemic_context_json"]
    assert pos["edge_context_json"]
