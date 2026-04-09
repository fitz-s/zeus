from __future__ import annotations

import os
import sys
import types

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

    assert result == {"mode": "paper", "risk_level": "GREEN", "status": "refreshed"}
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
