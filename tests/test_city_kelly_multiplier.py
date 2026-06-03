# Created: 2026-05-03
# Last reused/audited: 2026-05-03
# Authority basis: RERUN_PLAN_v2.md §5 D-A (Denver/Paris asymmetric loss
#                  migration to Kelly layer) + zeus_kelly_asymmetric_loss_handoff.md
"""Tests for ``city_kelly_multiplier`` and ``dynamic_kelly_mult(city=...)``.

The per-city Kelly multiplier replaces the old "Ruling A" pattern of overriding
the DDD floor for asymmetric-loss preferences (Denver, Paris). Final live
sizing composition::

    final_kelly =
        base_kelly
      × strategy_kelly_multiplier(strategy_key)
      × city_kelly_multiplier(city)
      × (1 - DDD_discount)
"""

from __future__ import annotations

import json

import pytest

from src.strategy.kelly import (
    DEFAULT_CITY_KELLY_MULTIPLIERS,
    city_kelly_multiplier,
    dynamic_kelly_mult,
)
import src.strategy.kelly as kelly_mod


@pytest.fixture(autouse=True)
def _reset_kelly_cache():
    """Clear the per-city override cache so each test sees a fresh load."""
    kelly_mod._CITY_KELLY_CACHE = None
    yield
    kelly_mod._CITY_KELLY_CACHE = None


# ── pure city_kelly_multiplier ───────────────────────────────────────────────


def test_denver_default_is_0_7():
    assert city_kelly_multiplier("Denver") == pytest.approx(0.7)


def test_paris_default_is_0_7():
    """Paris is registered now so the wiring is ready when workstream A
    re-includes Paris in the DDD universe."""
    assert city_kelly_multiplier("Paris") == pytest.approx(0.7)


def test_unknown_city_defaults_to_1_0():
    """Fail-OPEN to 1.0× for cities without a documented asymmetric override."""
    assert city_kelly_multiplier("NYC") == 1.0
    assert city_kelly_multiplier("Tokyo") == 1.0
    assert city_kelly_multiplier("CityThatDoesntExist") == 1.0


def test_none_or_empty_city_returns_1_0():
    assert city_kelly_multiplier(None) == 1.0
    assert city_kelly_multiplier("") == 1.0
    assert city_kelly_multiplier("   ") == 1.0


def test_default_table_includes_denver_and_paris_only():
    """Sanity: only the two documented Ruling-A cities should be in defaults.
    Adding a city here is a policy change requiring operator authorization.
    """
    assert set(DEFAULT_CITY_KELLY_MULTIPLIERS.keys()) == {"Denver", "Paris"}


# ── settings.json overrides ──────────────────────────────────────────────────


def test_settings_override_replaces_default(monkeypatch, tmp_path):
    """Operator can lower Denver to 0.5 via settings.json without code change."""
    fake_root = tmp_path / "fake_root"
    (fake_root / "src" / "strategy").mkdir(parents=True)
    (fake_root / "config").mkdir()
    (fake_root / "config" / "settings.json").write_text(
        json.dumps({
            "sizing": {
                "city_kelly_multipliers": {
                    "Denver": 0.5,
                    "NYC": 0.9,  # add a new override
                }
            }
        })
    )
    monkeypatch.setattr(
        kelly_mod, "__file__", str(fake_root / "src" / "strategy" / "kelly.py")
    )
    kelly_mod._CITY_KELLY_CACHE = None  # force re-read

    assert city_kelly_multiplier("Denver") == pytest.approx(0.5)
    assert city_kelly_multiplier("NYC") == pytest.approx(0.9)
    # Paris default still applies (not in override)
    assert city_kelly_multiplier("Paris") == pytest.approx(0.7)
    # Unknown city still 1.0×
    assert city_kelly_multiplier("Tokyo") == 1.0


def test_malformed_override_falls_back_to_defaults(monkeypatch, tmp_path):
    """Garbage in settings.json doesn't crash; defaults still apply."""
    fake_root = tmp_path / "fake_root"
    (fake_root / "src" / "strategy").mkdir(parents=True)
    (fake_root / "config").mkdir()
    (fake_root / "config" / "settings.json").write_text(
        json.dumps({
            "sizing": {
                "city_kelly_multipliers": {
                    "Denver": "not-a-number",  # invalid
                    "Paris": -0.5,             # negative — refused
                    "NYC": 5.0,                # too large — refused (sanity > 2.0)
                }
            }
        })
    )
    monkeypatch.setattr(
        kelly_mod, "__file__", str(fake_root / "src" / "strategy" / "kelly.py")
    )
    kelly_mod._CITY_KELLY_CACHE = None

    # All three invalid → defaults restored
    assert city_kelly_multiplier("Denver") == pytest.approx(0.7)
    assert city_kelly_multiplier("Paris") == pytest.approx(0.7)
    assert city_kelly_multiplier("NYC") == 1.0


def test_settings_missing_section_uses_defaults(monkeypatch, tmp_path):
    """settings.json with no sizing.city_kelly_multipliers → defaults applied."""
    fake_root = tmp_path / "fake_root"
    (fake_root / "src" / "strategy").mkdir(parents=True)
    (fake_root / "config").mkdir()
    (fake_root / "config" / "settings.json").write_text(
        json.dumps({"sizing": {"kelly_multiplier": 0.25}})
    )
    monkeypatch.setattr(
        kelly_mod, "__file__", str(fake_root / "src" / "strategy" / "kelly.py")
    )
    kelly_mod._CITY_KELLY_CACHE = None

    assert city_kelly_multiplier("Denver") == pytest.approx(0.7)


# ── dynamic_kelly_mult composition ───────────────────────────────────────────


def test_dynamic_kelly_mult_no_city_legacy_behavior():
    """city=None: no per-city adjustment — legacy compat."""
    base = 0.25
    out = dynamic_kelly_mult(base=base, ci_width=0.0, lead_days=0)
    assert out == pytest.approx(base)


def test_dynamic_kelly_mult_with_denver_applies_0_7():
    base = 0.25
    out_no_city = dynamic_kelly_mult(base=base)
    out_denver = dynamic_kelly_mult(base=base, city="Denver")
    assert out_denver == pytest.approx(out_no_city * 0.7)


def test_dynamic_kelly_mult_with_unknown_city_is_no_op():
    base = 0.25
    out_no_city = dynamic_kelly_mult(base=base)
    out_unknown = dynamic_kelly_mult(base=base, city="Tokyo")
    assert out_unknown == pytest.approx(out_no_city)


def test_dynamic_kelly_mult_strategy_and_city_compose_multiplicatively():
    """strategy_key=opening_inertia (0.5×) + Denver (0.7×) → 0.5 × 0.7 = 0.35
    on top of the base."""
    base = 0.20
    out = dynamic_kelly_mult(
        base=base,
        strategy_key="opening_inertia",
        city="Denver",
    )
    assert out == pytest.approx(base * 0.5 * 0.7)


def test_dynamic_kelly_mult_strategy_zero_zeroes_out_with_city():
    """strategy_key with mult 0.0 zeroes out, even with a city override.

    But because the base ValueError check happens BEFORE the strategy/city
    multipliers, we shouldn't hit that path. Verify the result is 0 exactly.
    """
    base = 0.25
    out = dynamic_kelly_mult(
        base=base,
        strategy_key="shoulder_sell",  # 0.0×
        city="Denver",                 # 0.7×
    )
    assert out == 0.0


def test_dynamic_kelly_mult_drawdown_ci_lead_compose_correctly():
    """CI gate × drawdown × city all multiplicative — sanity over composition."""
    base = 0.25
    out = dynamic_kelly_mult(
        base=base,
        ci_width=0.12,           # ×0.7
        lead_days=4,             # ×0.8
        drawdown_pct=0.10,       # ×(1 - 0.10/0.20) = ×0.5
        city="Denver",           # ×0.7
    )
    expected = base * 0.7 * 0.8 * 0.5 * 0.7
    assert out == pytest.approx(expected)
