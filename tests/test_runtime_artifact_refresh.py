from __future__ import annotations

import os
import sys
import types
from pathlib import Path

from scripts.refresh_paper_runtime_artifacts import refresh_paper_runtime_artifacts


def test_refresh_paper_runtime_artifacts_calls_tick_then_write_status_in_paper_mode(monkeypatch):
    calls: list[tuple[str, str, object | None]] = []

    fake_riskguard = types.ModuleType("src.riskguard.riskguard")

    def _tick():
        calls.append(("tick", os.environ.get("ZEUS_MODE", ""), None))
        return types.SimpleNamespace(value="GREEN")

    fake_riskguard.tick = _tick  # type: ignore[attr-defined]

    fake_status = types.ModuleType("src.observability.status_summary")

    def _write_status(payload=None):
        calls.append(("write_status", os.environ.get("ZEUS_MODE", ""), payload))

    fake_status.write_status = _write_status  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "src.riskguard.riskguard", fake_riskguard)
    monkeypatch.setitem(sys.modules, "src.observability.status_summary", fake_status)

    result = refresh_paper_runtime_artifacts()

    assert result == {"mode": "paper", "risk_level": "GREEN", "status": "refreshed", "state_dir": None}
    assert calls == [
        ("tick", "paper", None),
        ("write_status", "paper", {"mode": "paper", "artifact_refresh": True, "risk_level": "GREEN"}),
    ]


def test_refresh_paper_runtime_artifacts_restores_prior_mode(monkeypatch):
    fake_riskguard = types.ModuleType("src.riskguard.riskguard")
    fake_riskguard.tick = lambda: types.SimpleNamespace(value="YELLOW")  # type: ignore[attr-defined]

    fake_status = types.ModuleType("src.observability.status_summary")
    fake_status.write_status = lambda payload=None: None  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "src.riskguard.riskguard", fake_riskguard)
    monkeypatch.setitem(sys.modules, "src.observability.status_summary", fake_status)
    monkeypatch.setenv("ZEUS_MODE", "live")

    refresh_paper_runtime_artifacts()

    assert os.environ.get("ZEUS_MODE") == "live"


def test_refresh_paper_runtime_artifacts_rebinds_paths_for_explicit_state_dir(monkeypatch, tmp_path):
    import src.state.db as db_module
    import src.state.portfolio as portfolio_module
    import src.state.strategy_tracker as tracker_module
    import src.riskguard.riskguard as riskguard_module
    import src.observability.status_summary as status_module

    # Capture originals before the call to verify restore.
    orig_state_dir = db_module.STATE_DIR
    orig_db_path = db_module.ZEUS_DB_PATH
    orig_risk_path = db_module.RISK_DB_PATH
    orig_positions = portfolio_module.POSITIONS_PATH
    orig_tracker = tracker_module.TRACKER_PATH

    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        riskguard_module,
        "tick",
        lambda: calls.append(("tick", str(db_module.RISK_DB_PATH))) or types.SimpleNamespace(value="GREEN"),
    )
    monkeypatch.setattr(
        status_module,
        "write_status",
        lambda payload=None: calls.append(("write_status", str(status_module.STATUS_PATH))),
    )

    result = refresh_paper_runtime_artifacts(state_dir=tmp_path)

    assert result == {
        "mode": "paper",
        "risk_level": "GREEN",
        "status": "refreshed",
        "state_dir": str(tmp_path.resolve()),
    }
    # Paths were overridden during execution (verified via captured calls).
    assert calls == [
        ("tick", str(Path(tmp_path) / "risk_state-paper.db")),
        ("write_status", str(Path(tmp_path) / "status_summary-paper.json")),
    ]
    # Paths are restored after the call returns (context-manager guard).
    assert db_module.STATE_DIR == orig_state_dir
    assert db_module.ZEUS_DB_PATH == orig_db_path
    assert db_module.RISK_DB_PATH == orig_risk_path
    assert portfolio_module.POSITIONS_PATH == orig_positions
    assert tracker_module.TRACKER_PATH == orig_tracker
