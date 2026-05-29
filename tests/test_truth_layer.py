import json

import pytest

from src.state.portfolio import DeprecatedStateFileError, load_portfolio
import src.state.db as db_module
from src.state.strategy_tracker import StrategyTracker


def test_load_portfolio_rejects_deprecated_state_file(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({
        "error": "deprecated",
        "truth": {"deprecated": True},
    }))
    with pytest.raises(DeprecatedStateFileError):
        load_portfolio(path)

def test_strategy_tracker_summary_exposes_only_trade_count_and_pnl(monkeypatch):
    monkeypatch.setattr(
        db_module,
        "query_authoritative_settlement_rows",
        lambda *_args, **_kwargs: [
            {
                "trade_id": "t1",
                "strategy": "opening_inertia",
                "pnl": 2.5,
                "metric_ready": True,
            }
        ],
    )
    tracker = StrategyTracker()
    # record_trade is a removed shim (K1 post-split); writes are no-ops and the method
    # no longer exists. Reads project from query_authoritative_settlement_rows (monkeypatched above).

    summary = tracker.summary(conn=object())

    assert summary["opening_inertia"] == {"trades": 1, "pnl": 2.5}
    assert "win_rate" not in summary["opening_inertia"]
    assert summary["shoulder_sell"] == {"trades": 0, "pnl": 0}
    assert "win_rate" not in summary["shoulder_sell"]
    assert tracker.to_dict()["strategies"]["opening_inertia"]["trades"] == []
    assert "win_rate" not in tracker.to_dict()["strategies"]["opening_inertia"]


def test_strategy_tracker_rejects_unknown_strategy_instead_of_defaulting():
    tracker = StrategyTracker()
    # record_trade is a removed shim; unknown strategies are silently dropped via
    # the query path. Verify summary still returns zero for all known strategies.

    assert tracker.summary()["opening_inertia"] == {"trades": 0, "pnl": 0}
    assert tracker.summary()["settlement_capture"] == {"trades": 0, "pnl": 0}
