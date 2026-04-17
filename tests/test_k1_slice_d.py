"""K1 Slice D tests — token label validation, quarantine sentinel, policy precedence."""
import pytest
import sqlite3
from unittest.mock import patch


# ==================== D1 (#43): Token Label Validation ====================

def test_extract_outcomes_standard_yes_no_order():
    """Standard order: outcomes=["Yes","No"] → tokens stay in place."""
    from src.data.market_scanner import _extract_outcomes
    event = {
        "markets": [{
            "question": "Will NYC high temp be 85-86°F?",
            "clobTokenIds": '["tok_yes", "tok_no"]',
            "outcomePrices": '[0.3, 0.7]',
            "outcomes": '["Yes", "No"]',
            "conditionId": "cond1",
        }],
    }
    results = _extract_outcomes(event)
    assert len(results) == 1
    assert results[0]["token_id"] == "tok_yes"
    assert results[0]["no_token_id"] == "tok_no"
    assert results[0]["price"] == pytest.approx(0.3)
    assert results[0]["no_price"] == pytest.approx(0.7)


def test_extract_outcomes_reversed_no_yes_order():
    """Reversed order: outcomes=["No","Yes"] → tokens and prices must swap."""
    from src.data.market_scanner import _extract_outcomes
    event = {
        "markets": [{
            "question": "Will NYC high temp be 85-86°F?",
            "clobTokenIds": '["tok_first", "tok_second"]',
            "outcomePrices": '[0.7, 0.3]',
            "outcomes": '["No", "Yes"]',
            "conditionId": "cond1",
        }],
    }
    results = _extract_outcomes(event)
    assert len(results) == 1
    # tok_first was position-0 but labeled "No", so it becomes no_token
    assert results[0]["token_id"] == "tok_second"
    assert results[0]["no_token_id"] == "tok_first"
    # Prices should also swap
    assert results[0]["price"] == pytest.approx(0.3)
    assert results[0]["no_price"] == pytest.approx(0.7)


def test_extract_outcomes_unknown_labels_skipped():
    """Unknown outcome labels → market skipped entirely."""
    from src.data.market_scanner import _extract_outcomes
    event = {
        "markets": [{
            "question": "Will it rain?",
            "clobTokenIds": '["a", "b"]',
            "outcomePrices": '[0.5, 0.5]',
            "outcomes": '["Maybe", "Probably"]',
            "conditionId": "cond1",
        }],
    }
    results = _extract_outcomes(event)
    assert len(results) == 0


# ==================== D2 (#49): Quarantine Sentinel ====================

def test_quarantine_position_uses_sentinel():
    """Quarantine positions must use QUARANTINE_SENTINEL, not 'UNKNOWN'."""
    from src.state.portfolio import (
        _chain_only_quarantine_position_from_row,
        QUARANTINE_SENTINEL,
    )
    row = {
        "token_id": "abc123def456",
        "evidence_json": '{"size": 10, "avg_price": 0.5, "cost": 5}',
        "condition_id": "cond_xyz",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    pos = _chain_only_quarantine_position_from_row(row)
    assert pos.city == QUARANTINE_SENTINEL
    assert pos.target_date == QUARANTINE_SENTINEL
    assert pos.bin_label == QUARANTINE_SENTINEL
    assert pos.is_quarantine_placeholder is True


def test_normal_position_is_not_quarantine():
    """Normal positions must NOT be flagged as quarantine placeholders."""
    from src.state.portfolio import Position
    pos = Position(
        trade_id="t1", market_id="m1", city="NYC",
        cluster="NYC", target_date="2026-07-01",
        bin_label="85-86F", direction="buy_yes",
    )
    assert pos.is_quarantine_placeholder is False


# ==================== D4/D5 (#69/#71): Policy Precedence ====================

def test_parse_boolish_rejects_gate_ungate():
    """K1/#71: 'gate' and 'ungate' should raise ValueError, not parse as bool."""
    from src.riskguard.policy import _parse_boolish
    with pytest.raises(ValueError):
        _parse_boolish("gate")
    with pytest.raises(ValueError):
        _parse_boolish("ungate")


def test_parse_boolish_accepts_standard_values():
    """Standard boolean string values still parse correctly."""
    from src.riskguard.policy import _parse_boolish
    assert _parse_boolish("true") is True
    assert _parse_boolish("false") is False
    assert _parse_boolish("1") is True
    assert _parse_boolish("0") is False
    assert _parse_boolish(True) is True
    assert _parse_boolish(False) is False


def test_select_rows_logs_duplicate(caplog):
    """K1/#71: duplicate action_type rows should be logged."""
    from src.riskguard.policy import _select_rows

    # Create mock rows as dicts with sqlite3.Row-like access
    class MockRow(dict):
        def __getitem__(self, key):
            return dict.__getitem__(self, key)
        def get(self, key, default=None):
            return dict.get(self, key, default)

    rows = [
        MockRow(action_type="gate", value="true", override_id="r1"),
        MockRow(action_type="gate", value="false", override_id="r2"),
    ]
    import logging
    with caplog.at_level(logging.WARNING, logger="src.riskguard.policy"):
        result = _select_rows(rows)
    assert len(result) == 1  # first-in wins
    assert "duplicate" in caplog.text.lower()
    assert "r2" in caplog.text


def test_override_precedence_constant_exists():
    """K1/#69: OVERRIDE_PRECEDENCE table must exist and be correct."""
    from src.riskguard.policy import OVERRIDE_PRECEDENCE
    assert OVERRIDE_PRECEDENCE["hard_safety"] > OVERRIDE_PRECEDENCE["manual_override"]
    assert OVERRIDE_PRECEDENCE["manual_override"] > OVERRIDE_PRECEDENCE["risk_action"]



class TestB051MalformedRowIsolation:
    """B051: _select_rows must isolate per-row parse failures.

    Symptom before fix: a single malformed policy row (e.g. missing
    ``action_type`` column) raised inside ``_select_rows`` before any
    parsing began, killing strategy resolution for every other row in
    the same batch.
    """

    def _make_row(self, **kwargs):
        class MockRow(dict):
            def __getitem__(self, key):
                return dict.__getitem__(self, key)  # raises KeyError on miss
            def keys(self):
                return list(dict.keys(self))
        return MockRow(**kwargs)

    def test_malformed_row_isolated_good_rows_survive(self, caplog):
        from src.riskguard.policy import _select_rows

        good = self._make_row(action_type="gate", value="true", override_id="ok")
        # Missing 'action_type' column -> KeyError on row["action_type"]
        bad = self._make_row(value="true", override_id="bad")

        import logging
        with caplog.at_level(logging.WARNING, logger="src.riskguard.policy"):
            result = _select_rows([bad, good])
        assert len(result) == 1, "good row must survive when sibling row is malformed"
        assert result[0]["override_id"] == "ok"
        assert "malformed" in caplog.text.lower() or "skipped" in caplog.text.lower()
        assert "bad" in caplog.text

    def test_all_malformed_rows_returns_empty_not_crash(self, caplog):
        from src.riskguard.policy import _select_rows
        import logging
        bad1 = self._make_row(value="x", override_id="b1")
        bad2 = self._make_row(value="y", action_id="b2")
        with caplog.at_level(logging.WARNING, logger="src.riskguard.policy"):
            result = _select_rows([bad1, bad2])
        assert result == []  # must NOT raise
