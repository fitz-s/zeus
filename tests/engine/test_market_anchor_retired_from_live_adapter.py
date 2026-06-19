# Created: 2026-06-18
# Last reused or audited: 2026-06-18
# Authority basis: live/experiment separation cleanup; market-anchor cap retired from live q_lcb.
"""Retired market-anchor cap must not re-enter the live event adapter."""

from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_ADAPTER = _REPO / "src" / "engine" / "event_reactor_adapter.py"


def test_market_anchor_cap_is_not_live_adapter_wired() -> None:
    source = _ADAPTER.read_text(encoding="utf-8")

    assert "replacement_q_market_anchor_enabled" not in source
    assert "_market_anchor_no_lcb_for_candidate" not in source
    assert "market_anchored_no_lcb" not in source
    assert "zeus.replacement_qlcb_shadow" not in source
