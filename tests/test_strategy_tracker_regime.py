import json
from pathlib import Path

import scripts.rebuild_strategy_tracker_current_regime as rebuild
import src.state.strategy_tracker as strategy_tracker_module


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
    assert rebuilt["accounting"]["tracker_role"] == "compatibility_surface"
    assert rebuilt["accounting"]["authority_mode"] == "non_authority_compatibility"
    assert rebuilt["accounting"]["includes_legacy_history"] is False
    assert history["accounting"]["includes_legacy_history"] is True
    assert history["accounting"]["accounting_scope"] == "full_history_archive"
    assert rebuilt["strategies"]["opening_inertia"]["trades"][0]["trade_id"] == "open1"
    assert rebuilt["strategies"]["shoulder_sell"]["trades"][0]["trade_id"] == "exit1"


def test_edge_compression_requires_enough_time_span(monkeypatch):
    class FrozenDatetime(strategy_tracker_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 2, 12, 0, 0, tzinfo=strategy_tracker_module.timezone.utc)

    monkeypatch.setattr(strategy_tracker_module, "datetime", FrozenDatetime)
    tracker = strategy_tracker_module.StrategyTracker()
    metrics = tracker.strategies["opening_inertia"]

    for i in range(25):
        metrics.record(
            {
                "trade_id": f"oi-{i}",
                "strategy": "opening_inertia",
                "edge_source": "opening_inertia",
                "entered_at": f"2026-04-01T{(i // 6):02d}:{(i % 6) * 10:02d}:00+00:00",
                "edge": 0.20 - (i * 0.003),
                "pnl": 0.0,
                "status": "exited",
            }
        )

    assert tracker.edge_compression_check(window_days=30) == []


def test_edge_compression_still_triggers_with_enough_samples_and_span(monkeypatch):
    class FrozenDatetime(strategy_tracker_module.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 4, 10, 12, 0, 0, tzinfo=strategy_tracker_module.timezone.utc)

    monkeypatch.setattr(strategy_tracker_module, "datetime", FrozenDatetime)
    tracker = strategy_tracker_module.StrategyTracker()
    metrics = tracker.strategies["opening_inertia"]

    for i in range(24):
        day = 1 + (i // 4)
        hour = 8 + (i % 4)
        metrics.record(
            {
                "trade_id": f"oi-span-{i}",
                "strategy": "opening_inertia",
                "edge_source": "opening_inertia",
                "entered_at": f"2026-04-{day:02d}T{hour:02d}:00:00+00:00",
                "edge": 0.25 - (i * 0.005),
                "pnl": 0.0,
                "status": "exited",
            }
        )

    alerts = tracker.edge_compression_check(window_days=30)

    assert len(alerts) == 1
    assert alerts[0].startswith("EDGE_COMPRESSION: opening_inertia edge shrinking at -")


def test_tracker_backfills_current_regime_started_at_from_loaded_trades():
    tracker = strategy_tracker_module.StrategyTracker.from_dict(
        {
            "strategies": {
                "opening_inertia": {
                    "trades": [
                        {"trade_id": "t2", "entered_at": "2026-04-02T00:00:00+00:00", "edge": 0.1},
                        {"trade_id": "t1", "entered_at": "2026-04-01T00:00:00+00:00", "edge": 0.2},
                    ]
                }
            },
            "accounting": {
                "accounting_scope": "current_regime",
                "includes_legacy_history": False,
                "current_regime_started_at": "",
            },
        }
    )

    assert tracker.accounting["current_regime_started_at"] == "2026-04-01T00:00:00+00:00"


def test_tracker_record_trade_updates_current_regime_started_at():
    tracker = strategy_tracker_module.StrategyTracker()

    tracker.record_trade(
        {
            "trade_id": "late",
            "strategy": "opening_inertia",
            "edge_source": "opening_inertia",
            "entered_at": "2026-04-03T00:00:00+00:00",
            "edge": 0.1,
        }
    )
    tracker.record_trade(
        {
            "trade_id": "early",
            "strategy": "opening_inertia",
            "edge_source": "opening_inertia",
            "entered_at": "2026-04-01T00:00:00+00:00",
            "edge": 0.2,
        }
    )

    assert tracker.accounting["current_regime_started_at"] == "2026-04-01T00:00:00+00:00"


def test_tracker_from_dict_normalizes_legacy_compatibility_metadata():
    tracker = strategy_tracker_module.StrategyTracker.from_dict(
        {
            "strategies": {},
            "accounting": {
                "accounting_scope": "current_regime",
                "tracker_role": "attribution_surface",
                "performance_headline_authority": "/tmp/legacy-status.json",
                "includes_legacy_history": False,
            },
        }
    )

    assert tracker.accounting["tracker_role"] == "compatibility_surface"
    assert tracker.accounting["authority_mode"] == "non_authority_compatibility"
    assert tracker.accounting["performance_headline_authority"].endswith("status_summary-paper.json")
