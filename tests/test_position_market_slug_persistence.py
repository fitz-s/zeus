# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md §8.2 — Position.market_slug JSON round-trip
"""
T5 antibody: Position.market_slug JSON round-trip preservation.

Verifies:
  1. Position accepts market_slug on construction.
  2. save_portfolio writes market_slug into positions.json.
  3. load_portfolio (JSON fallback path) reads it back correctly.
  4. Round-trip is stable (write → read → write yields same JSON).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.state.portfolio import Position, PortfolioState, save_portfolio, _load_portfolio_json_payload


def _minimal_position(trade_id: str = "trade-t5-001", market_slug: str | None = None) -> Position:
    return Position(
        trade_id=trade_id,
        market_id="test-market-001",
        city="TestCity",
        cluster="Test",
        target_date="2026-06-15",
        bin_label="70-80°F",
        direction="buy_yes",
        temperature_metric="high",
        env="test",
        state="holding",
        market_slug=market_slug,
    )


def test_position_accepts_market_slug() -> None:
    """Position dataclass accepts market_slug kwarg without error."""
    pos = _minimal_position(market_slug="boston-2026-06-15-high")
    assert pos.market_slug == "boston-2026-06-15-high"


def test_position_market_slug_defaults_to_none() -> None:
    """market_slug defaults to None when omitted (backward-compat)."""
    pos = _minimal_position()
    assert pos.market_slug is None


def test_save_portfolio_writes_market_slug(tmp_path: Path) -> None:
    """save_portfolio writes market_slug into the JSON payload."""
    pos = _minimal_position(market_slug="dallas-2026-06-15-high")
    state = PortfolioState(positions=[pos])
    out_path = tmp_path / "positions.json"
    save_portfolio(state, path=out_path)

    raw = json.loads(out_path.read_text())
    positions = raw.get("positions") or raw  # handle both list and dict payloads
    if isinstance(positions, dict):
        positions = list(positions.values())
    found = next((p for p in positions if p.get("trade_id") == "trade-t5-001"), None)
    assert found is not None, "trade-t5-001 not found in serialized positions.json"
    assert found.get("market_slug") == "dallas-2026-06-15-high", (
        f"Expected market_slug='dallas-2026-06-15-high' in JSON, got {found.get('market_slug')!r}"
    )


def test_json_round_trip_preserves_market_slug(tmp_path: Path) -> None:
    """Write position with market_slug → read back → market_slug preserved."""
    pos = _minimal_position(market_slug="chicago-2026-06-15-high")
    state = PortfolioState(positions=[pos])
    out_path = tmp_path / "positions.json"
    save_portfolio(state, path=out_path)

    payload = _load_portfolio_json_payload(out_path)
    raw_positions = payload.get("positions") or payload
    if isinstance(raw_positions, dict):
        raw_positions = list(raw_positions.values())
    found = next((p for p in raw_positions if p.get("trade_id") == "trade-t5-001"), None)
    assert found is not None
    assert found.get("market_slug") == "chicago-2026-06-15-high"
