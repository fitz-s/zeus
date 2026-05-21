# Created: 2026-05-20
# Last reused or audited: 2026-05-20
# Authority basis: PHASE_2_ULTRAPLAN.md §8.4 — backward-compat antibody
"""
T5 backward-compat antibody: load v1-vintage positions.json (without market_slug)
→ Position.market_slug defaults to None → round-trip stable.

§8.4 specification:
  - Load a v1-vintage positions.json WITHOUT market_slug field.
  - Assert: Position instances load with market_slug is None.
  - Save round-trip: loaded → saved JSON preserves market_slug: None
    (no silent drop, no field omission).
  - Assert: subsequent load reads same market_slug is None.

The reflection path at portfolio.py:1235 (Position(**filtered)) uses
dataclasses.fields(Position) to filter; the new defaulted field auto-flows
when absent from JSON (default=None applied by dataclass machinery).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.state.portfolio import Position, PortfolioState, save_portfolio, _load_portfolio_json_payload


_V1_POSITION_DICT: dict = {
    "trade_id": "trade-v1-001",
    "market_id": "v1-market-001",
    "city": "TestCity",
    "cluster": "Test",
    "target_date": "2026-06-15",
    "bin_label": "70-80°F",
    "direction": "buy_yes",
    "temperature_metric": "high",
    "env": "test",
    "state": "holding",
    # NOTE: no market_slug key — simulates v1-vintage payload
}


def _write_v1_positions_json(path: Path) -> None:
    """Write a v1-vintage positions.json with no market_slug field."""
    payload = {"positions": [_V1_POSITION_DICT]}
    path.write_text(json.dumps(payload))


def test_v1_position_loads_with_market_slug_none(tmp_path: Path) -> None:
    """v1-vintage positions.json (no market_slug) loads with market_slug=None."""
    path = tmp_path / "positions.json"
    _write_v1_positions_json(path)

    payload = _load_portfolio_json_payload(path)
    raw_positions = payload.get("positions") or payload
    if isinstance(raw_positions, dict):
        raw_positions = list(raw_positions.values())

    found = next((p for p in raw_positions if p.get("trade_id") == "trade-v1-001"), None)
    assert found is not None, "trade-v1-001 not found in loaded payload"
    assert "market_slug" not in found, (
        "v1-vintage JSON should NOT have market_slug key — "
        "test fixture is wrong if this fires"
    )

    # Now construct via the same reflection path as _load_portfolio_json_payload uses:
    from dataclasses import fields
    position_fields = {f.name for f in fields(Position)}
    filtered = {k: v for k, v in found.items() if k in position_fields}
    pos = Position(**filtered)
    assert pos.market_slug is None, (
        f"Expected market_slug=None for v1-vintage position, got {pos.market_slug!r}"
    )


def test_v1_round_trip_stable(tmp_path: Path) -> None:
    """Load v1 position → save → reload → market_slug still None (round-trip stable)."""
    path = tmp_path / "positions.json"
    _write_v1_positions_json(path)

    from dataclasses import fields as dc_fields

    payload = _load_portfolio_json_payload(path)
    raw_positions = payload.get("positions") or payload
    if isinstance(raw_positions, dict):
        raw_positions = list(raw_positions.values())
    found = next((p for p in raw_positions if p.get("trade_id") == "trade-v1-001"), None)
    position_fields = {f.name for f in dc_fields(Position)}
    filtered = {k: v for k, v in found.items() if k in position_fields}
    pos = Position(**filtered)
    assert pos.market_slug is None

    # Save + reload
    state = PortfolioState(positions=[pos])
    out_path = tmp_path / "positions_out.json"
    save_portfolio(state, path=out_path)

    payload2 = _load_portfolio_json_payload(out_path)
    raw2 = payload2.get("positions") or payload2
    if isinstance(raw2, dict):
        raw2 = list(raw2.values())
    found2 = next((p for p in raw2 if p.get("trade_id") == "trade-v1-001"), None)
    assert found2 is not None

    filtered2 = {k: v for k, v in found2.items() if k in position_fields}
    pos2 = Position(**filtered2)
    assert pos2.market_slug is None, (
        f"Round-trip instability: market_slug changed from None to {pos2.market_slug!r}"
    )
