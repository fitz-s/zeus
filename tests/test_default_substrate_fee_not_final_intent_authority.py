from pathlib import Path


def test_default_substrate_fee_details_are_not_final_intent_authority() -> None:
    text = (Path(__file__).resolve().parents[1] / "src/data/market_scanner.py").read_text(encoding="utf-8")

    assert "default_substrate_fee_details_not_final_intent_authority" in text
    assert "submit_boundary_revalidates_fee" in text
