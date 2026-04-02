import json
from pathlib import Path

import scripts.rebuild_strategy_tracker_current_regime as rebuild


def test_rebuild_strategy_tracker_creates_current_regime_and_archives_history(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    tracker_path = state_dir / "strategy_tracker-paper.json"
    tracker_path.write_text(json.dumps({
        "strategies": {
            "opening_inertia": {
                "trades": [
                    {"trade_id": "old1", "pnl": 164.44, "status": "exited", "entered_at": "2026-03-30T09:00:00+00:00"}
                ]
            }
        }
    }))

    positions_path = state_dir / "positions-paper.json"
    positions_path.write_text(json.dumps({
        "positions": [
            {
                "trade_id": "open1",
                "strategy": "opening_inertia",
                "edge_source": "opening_inertia",
                "city": "Seattle",
                "target_date": "2026-04-05",
                "entered_at": "2026-03-31T16:49:28.503175+00:00",
                "state": "entered",
            }
        ],
        "recent_exits": [
            {
                "trade_id": "exit1",
                "strategy": "shoulder_sell",
                "edge_source": "shoulder_sell",
                "city": "Chicago",
                "target_date": "2026-04-02",
                "entered_at": "2026-03-31T18:00:00+00:00",
                "exited_at": "2026-03-31T19:00:00+00:00",
                "status": "exited",
                "market_id": "real_market",
            }
        ],
        "truth": {"mode": "paper"},
    }))

    monkeypatch.setattr(rebuild, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(rebuild, "mode_state_path", lambda filename, mode: state_dir / f"{filename[:-5]}-{mode}.json")
    monkeypatch.setattr(
        rebuild,
        "read_mode_truth_json",
        lambda filename, mode=None: (json.loads(positions_path.read_text()), {"mode": "paper"}),
    )

    report = rebuild.run(mode="paper")
    rebuilt = json.loads(tracker_path.read_text())
    history = json.loads((state_dir / "strategy_tracker-paper-history.json").read_text())

    assert report["includes_legacy_history"] is False
    assert report["current_regime_started_at"] == "2026-03-31T16:49:28.503175+00:00"
    assert rebuilt["accounting"]["performance_headline_authority"].endswith("status_summary-paper.json")
    assert rebuilt["accounting"]["tracker_role"] == "attribution_surface"
    assert rebuilt["accounting"]["includes_legacy_history"] is False
    assert history["accounting"]["includes_legacy_history"] is True
    assert history["accounting"]["accounting_scope"] == "full_history_archive"
    assert rebuilt["strategies"]["opening_inertia"]["trades"][0]["trade_id"] == "open1"
    assert rebuilt["strategies"]["shoulder_sell"]["trades"][0]["trade_id"] == "exit1"
