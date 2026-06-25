import src.state.db as db_module
import src.state.strategy_tracker as strategy_tracker_module


def test_strategy_tracker_summary_excludes_metric_unready_settlement_rows(monkeypatch):
    monkeypatch.setattr(
        db_module,
        "query_authoritative_settlement_rows",
        lambda *_args, **_kwargs: [
            {
                "trade_id": "legacy-settle",
                "strategy": "shoulder_sell",
                "pnl": 99.0,
                "metric_ready": False,
                "settlement_authority": "LEGACY_UNKNOWN",
            },
            {
                "trade_id": "verified-settle",
                "strategy": "center_buy",
                "pnl": 4.2,
                "metric_ready": True,
                "settlement_authority": "VERIFIED",
            },
        ],
    )

    summary = strategy_tracker_module.StrategyTracker().summary(conn=object())

    assert summary["shoulder_sell"] == {"trades": 0, "pnl": 0.0}
    assert summary["center_buy"] == {"trades": 1, "pnl": 4.2}
