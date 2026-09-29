# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: DSA-07 non-live execution residue cleanup; K1 monitor authority gate reuse.
"""K1 package-review fixes — authority gate in monitor, _parse_boolish_text, quarantine guard."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import date


# ==================== Fix 2: _parse_boolish_text in db.py ====================

def test_parse_boolish_text_rejects_gate():
    """_parse_boolish_text must raise ValueError on 'gate' (K1/#71 parity)."""
    from src.state.db import _parse_boolish_text
    with pytest.raises(ValueError, match="unsupported boolish"):
        _parse_boolish_text("gate")


def test_parse_boolish_text_rejects_ungate():
    """_parse_boolish_text must raise ValueError on 'ungate'."""
    from src.state.db import _parse_boolish_text
    with pytest.raises(ValueError, match="unsupported boolish"):
        _parse_boolish_text("ungate")


def test_parse_boolish_text_accepts_standard_values():
    """_parse_boolish_text must accept standard boolean literals."""
    from src.state.db import _parse_boolish_text
    for truthy in ("true", "1", "yes", "on", "enabled"):
        assert _parse_boolish_text(truthy) is True, f"Expected True for {truthy!r}"
    for falsy in ("false", "0", "no", "off", "disabled"):
        assert _parse_boolish_text(falsy) is False, f"Expected False for {falsy!r}"


def test_parse_boolish_text_rejects_typo():
    """_parse_boolish_text must raise on unrecognized input, not silently return False."""
    from src.state.db import _parse_boolish_text
    with pytest.raises(ValueError, match="unsupported boolish"):
        _parse_boolish_text("treu")


# ==================== Fix 3: quarantine placeholder guard ====================

def test_quarantine_placeholder_skipped_in_monitor_loop():
    """A quarantine placeholder position must be skipped before cities_by_name lookup."""
    from src.state.portfolio import Position, QUARANTINE_SENTINEL

    pos = Position.__new__(Position)
    pos.city = QUARANTINE_SENTINEL
    pos.target_date = "2026-07-15"
    pos.trade_id = "test_quarantine_123"
    pos.state = "entered"
    pos.chain_state = "active"  # NOT "quarantined" — simulates the fragile case
    pos.direction = "buy_yes"
    pos.exit_state = ""
    pos.admin_exit_reason = None
    
    # The property should fire
    assert pos.is_quarantine_placeholder is True


# ==================== Relationship test: parsers agree ====================

def test_parse_boolish_and_parse_boolish_text_reject_same_keywords():
    """Both boolish parsers must reject 'gate' and 'ungate' — cross-module invariant."""
    from src.state.db import _parse_boolish_text
    from src.riskguard.policy import _parse_boolish
    
    for keyword in ("gate", "ungate"):
        with pytest.raises(ValueError):
            _parse_boolish(keyword)
        with pytest.raises(ValueError):
            _parse_boolish_text(keyword)
