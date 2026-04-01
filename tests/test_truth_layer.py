import json
from pathlib import Path

import pytest

import src.state.truth_files as truth_files
from src.state.portfolio import DeprecatedStateFileError, PortfolioState, load_portfolio, save_portfolio
from src.state.strategy_tracker import StrategyTracker, load_tracker, save_tracker


def test_deprecate_legacy_state_files_archives_and_tombstones(tmp_path, monkeypatch):
    archive_dir = tmp_path / "archive"
    legacy_dir = tmp_path / "state"
    legacy_dir.mkdir()

    def _legacy(name: str) -> Path:
        return legacy_dir / name

    def _mode(name: str, mode: str | None = None) -> Path:
        mode = mode or "paper"
        stem, ext = name.rsplit(".", 1)
        return legacy_dir / f"{stem}-{mode}.{ext}"

    monkeypatch.setattr(truth_files, "legacy_state_path", _legacy)
    monkeypatch.setattr(truth_files, "mode_state_path", _mode)
    monkeypatch.setattr(truth_files, "LEGACY_ARCHIVE_DIR", archive_dir)

    old_path = legacy_dir / "positions.json"
    old_path.write_text(json.dumps({"positions": [{"trade_id": "old"}]}))

    result = truth_files.ensure_legacy_state_tombstone("positions.json")
    tombstone = json.loads(old_path.read_text())

    assert result["archived"] is True
    assert tombstone["truth"]["deprecated"] is True
    assert tombstone["truth"]["replacement_paths"]["paper"].endswith("positions-paper.json")
    assert any(p.name.startswith("positions.json.") for p in archive_dir.iterdir())


def test_load_portfolio_rejects_deprecated_state_file(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({
        "error": "deprecated",
        "truth": {"deprecated": True},
    }))
    with pytest.raises(DeprecatedStateFileError):
        load_portfolio(path)


def test_load_tracker_rejects_deprecated_state_file(tmp_path):
    path = tmp_path / "strategy_tracker.json"
    path.write_text(json.dumps({
        "error": "deprecated",
        "truth": {"deprecated": True},
    }))
    with pytest.raises(RuntimeError, match="deprecated legacy truth file"):
        load_tracker(path)


def test_portfolio_and_tracker_save_truth_metadata(tmp_path):
    portfolio_path = tmp_path / "positions-paper.json"
    tracker_path = tmp_path / "strategy_tracker-paper.json"

    save_portfolio(PortfolioState(), portfolio_path)
    save_tracker(StrategyTracker(), tracker_path)

    portfolio_data = json.loads(portfolio_path.read_text())
    tracker_data = json.loads(tracker_path.read_text())

    assert portfolio_data["truth"]["deprecated"] is False
    assert portfolio_data["truth"]["source_path"] == str(portfolio_path)
    assert tracker_data["truth"]["deprecated"] is False
    assert tracker_data["truth"]["source_path"] == str(tracker_path)
