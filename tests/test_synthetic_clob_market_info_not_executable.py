from pathlib import Path


def test_synthetic_clob_market_info_is_marked_substrate_only_not_executable() -> None:
    text = (Path(__file__).resolve().parents[1] / "src/data/market_scanner.py").read_text(encoding="utf-8")

    assert "synthetic_clob_market_info_substrate_only" in text
    assert "executable_allowed=False" in text
