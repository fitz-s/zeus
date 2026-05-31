# Created: 2026-05-31
# Last reused or audited: 2026-05-31
# Authority basis: operator directive 2026-05-31 — pre-submit bias-decay Kelly haircut
#   (INTERIM, data-insufficient phase). Relationship test for
#   event_reactor_adapter._maybe_bias_decay_kelly_haircut: |per-city forecast bias| over
#   the unit-aware threshold (3F / 2C) halves the Kelly multiplier; unit-correct for
#   F-settled cities (SF/Seattle); fail-safe on missing bias row; fail-open on error.
import types
import pytest

import src.engine.event_reactor_adapter as era
import src.calibration.ens_bias_repo as ens_bias_repo
import src.state.db as state_db
import src.calibration.manager as cal_manager


class _FakeCity:
    def __init__(self, name, unit, lat=35.0):
        self.name = name
        self.settlement_unit = unit
        self.lat = lat


class _FakeConn:
    row_factory = None
    def close(self):
        pass


class _Family:
    def __init__(self, city, metric="high", target_date="2026-06-01"):
        self.city = city
        self.metric = metric
        self.target_date = target_date


@pytest.fixture
def patched(monkeypatch):
    """Patch the helper's dependencies; default flags ON. Returns a setter for the bias row."""
    cities = {
        "Tokyo": _FakeCity("Tokyo", "C", 35.0),
        "San Francisco": _FakeCity("San Francisco", "F", 37.6),
        "Seattle": _FakeCity("Seattle", "F", 47.6),
        "LowBiasC": _FakeCity("LowBiasC", "C", 40.0),
    }
    monkeypatch.setattr(era, "runtime_cities_by_name", lambda: cities)
    monkeypatch.setattr(state_db, "get_world_connection", lambda: _FakeConn())
    monkeypatch.setattr(cal_manager, "season_from_date", lambda d, lat=None: "JJA")
    # ensure flags ON with canonical thresholds
    era.settings["edli_v1"]["bias_decay_kelly_haircut_enabled"] = True
    era.settings["edli_v1"]["bias_decay_threshold_c"] = 2.0
    era.settings["edli_v1"]["bias_decay_threshold_f"] = 3.0
    era.settings["edli_v1"]["bias_decay_kelly_factor"] = 0.5

    def set_eff(eff_c):
        if eff_c is None:
            monkeypatch.setattr(ens_bias_repo, "read_bias_model", lambda *a, **k: None)
        else:
            monkeypatch.setattr(ens_bias_repo, "read_bias_model", lambda *a, **k: {"effective_bias_c": eff_c})
    return set_eff


def test_celsius_city_bias_over_threshold_halves(patched):
    patched(-3.45)  # Tokyo |3.45| > 2.0
    mult, applied, native, reason = era._maybe_bias_decay_kelly_haircut(0.40, family=_Family("Tokyo"))
    assert applied is True and reason == "bias_exceeds"
    assert mult == pytest.approx(0.20)  # halved


def test_celsius_city_bias_within_threshold_unchanged(patched):
    patched(-1.0)  # |1.0| <= 2.0
    mult, applied, native, reason = era._maybe_bias_decay_kelly_haircut(0.40, family=_Family("LowBiasC"))
    assert applied is False and reason == "within_threshold"
    assert mult == pytest.approx(0.40)


def test_fahrenheit_city_uses_f_threshold_and_converts(patched):
    # SF eff_c -4.68 -> -8.42F, |8.42| > 3.0F -> halve
    patched(-4.68)
    mult, applied, native, reason = era._maybe_bias_decay_kelly_haircut(0.40, family=_Family("San Francisco"))
    assert applied is True and reason == "bias_exceeds"
    assert native == pytest.approx(-4.68 * 1.8)
    assert mult == pytest.approx(0.20)


def test_fahrenheit_city_small_bias_unchanged(patched):
    # Seattle eff_c -0.77 -> -1.39F, |1.39| < 3.0F -> no haircut
    patched(-0.77)
    mult, applied, native, reason = era._maybe_bias_decay_kelly_haircut(0.40, family=_Family("Seattle"))
    assert applied is False and reason == "within_threshold"
    assert mult == pytest.approx(0.40)


def test_no_bias_row_fail_safe_halves(patched):
    patched(None)  # data absent -> conservative haircut
    mult, applied, native, reason = era._maybe_bias_decay_kelly_haircut(0.40, family=_Family("Tokyo"))
    assert applied is True and reason == "no_bias_row_conservative"
    assert mult == pytest.approx(0.20)


def test_toggle_off_never_halves(patched):
    patched(-9.0)  # huge bias, but toggle OFF
    era.settings["edli_v1"]["bias_decay_kelly_haircut_enabled"] = False
    try:
        mult, applied, native, reason = era._maybe_bias_decay_kelly_haircut(0.40, family=_Family("Tokyo"))
    finally:
        era.settings["edli_v1"]["bias_decay_kelly_haircut_enabled"] = True
    assert applied is False and reason == "disabled"
    assert mult == pytest.approx(0.40)


def test_unexpected_error_fail_open_no_haircut(patched, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db blew up")
    monkeypatch.setattr(ens_bias_repo, "read_bias_model", _boom)
    mult, applied, native, reason = era._maybe_bias_decay_kelly_haircut(0.40, family=_Family("Tokyo"))
    assert applied is False and reason == "error_fail_open"
    assert mult == pytest.approx(0.40)  # never zero/crash a live size
