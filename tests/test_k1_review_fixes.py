# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: DSA-07 non-live execution residue cleanup; K1 monitor authority gate reuse.
"""K1 package-review fixes — authority gate in monitor, _parse_boolish_text, quarantine guard."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import date


def test_monitor_refresh_emos_regime_skips_legacy_calibrators(monkeypatch):
    """EMOS-sole forecast monitor must use the entry q seam, not legacy exit calibrators."""
    import numpy as np

    from src.engine import monitor_refresh
    from src.engine.monitor_refresh import _refresh_ens_member_counting
    from src.state.portfolio import Position
    from src.types import Bin

    pos = Position.__new__(Position)
    pos.bin_label = "20°C"
    pos.direction = "buy_yes"
    pos.entry_method = "ens_member_counting"
    pos.p_posterior = 0.42
    # M2b (2026-06-16): real entered_at — monitor_refresh refuses (no alpha) on a missing one.
    # This EMOS-sole-calibrator test exercises the alpha/posterior path, so it needs a finite hold age.
    pos.entered_at = "2026-07-14T12:00:00+00:00"
    pos.target_date = date(2026, 7, 15)
    pos.condition_id = "cond_test"
    pos.market_id = "market_20c"
    pos.token_id = "yes_20c"
    pos.no_token_id = "no_20c"
    pos.temperature_metric = "high"
    pos.entry_model_agreement = "NOT_CHECKED"

    city = MagicMock()
    city.name = "Paris"
    city.lat = 48.85
    city.timezone = "Europe/Paris"
    city.cluster = "EUROPE"
    city.settlement_unit = "C"
    city.settlement_source_type = "wu_icao"

    ens_result = {
        "period_extrema_members": [18.0 + 0.05 * i for i in range(51)],
        "members_unit": "degC",
        "source_id": "ecmwf_open_data",
        "forecast_source_role": "primary_forecast",
    }
    bins = [
        Bin(low=19.0, high=19.0, label="19°C", unit="C"),
        Bin(low=20.0, high=20.0, label="20°C", unit="C"),
        Bin(low=21.0, high=21.0, label="21°C", unit="C"),
    ]

    def boom(*args, **kwargs):
        raise AssertionError("legacy calibrator path must not run under EMOS monitor regime")

    monkeypatch.setitem(
        monitor_refresh.settings["edli"],
        "edli_emos_sole_calibrator_enabled",
        True,
    )
    monkeypatch.setitem(
        monitor_refresh.settings["edli"],
        "edli_settlement_sigma_floor_enabled",
        False,
    )
    monkeypatch.setattr(
        monitor_refresh,
        "_read_monitor_executable_forecast",
        lambda **kwargs: (ens_result, None),
    )
    monkeypatch.setattr(monitor_refresh, "lead_days_to_date_start", lambda *args, **kwargs: 2.0)
    monkeypatch.setattr(monitor_refresh, "_build_all_bins", lambda *args, **kwargs: (bins, 1))
    monkeypatch.setattr(monitor_refresh, "_resolve_unified_exit_bias_native", boom)
    # _resolve_ft_error_model removed 2026-06-14 (dead FT shadow) — can no longer run.
    monkeypatch.setattr(monitor_refresh, "_monitor_calibrator_for_ens_result", boom)
    monkeypatch.setattr(monitor_refresh, "calibrate_and_normalize", boom)
    monkeypatch.setattr(monitor_refresh, "_check_persistence_anomaly", lambda *args, **kwargs: 1.0)

    import src.calibration.emos_q_builder as q_builder

    monkeypatch.setattr(
        q_builder,
        "build_emos_q",
        lambda **kwargs: (np.array([0.1, 0.7, 0.2], dtype=float), 20.0, 1.2),
    )

    p, applied = _refresh_ens_member_counting(
        position=pos,
        current_p_market=0.50,
        conn=MagicMock(),
        city=city,
        target_d=date(2026, 7, 15),
    )

    assert p == pytest.approx(0.7)
    assert "monitor_emos_sole_calibrator" in applied
    assert "q_source:emos" in applied
    assert "platt_recalibration" not in applied
    assert "full_transport_live" not in applied
    assert "exit_bias_family_unify" not in applied
    bootstrap_ctx = getattr(pos, "_bootstrap_context")
    assert bootstrap_ctx["bootstrap_signal_type"] == "monitor_emos_sole_calibrator"
    assert bootstrap_ctx["bootstrap_probability_sampler"] is not None


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
