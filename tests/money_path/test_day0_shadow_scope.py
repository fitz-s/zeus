# Created: 2026-06-05
# Last reused/audited: 2026-06-18
# Authority basis: live-only EDLI scope law. Production has exactly one
# execution scope, forecast_plus_day0. Former forecast_only/day0_shadow scopes
# are rejected before they can become execution semantics.
"""Live-only EDLI scope tests.

These are regression tests against resurrecting a shadow/no-submit bridge in
the production execution surface. Day0 and forecast events share the single
``forecast_plus_day0`` live scope; every other scope fails closed.
"""
from __future__ import annotations

import pytest

from src.main import EDLI_LIVE_SCOPES, _assert_edli_live_scope


def _cfg(scope: str | None, **extra: object) -> dict:
    cfg = {
        "day0_extreme_trigger_enabled": True,
        "day0_hard_fact_live_enabled": True,
    }
    if scope is not None:
        cfg["edli_live_scope"] = scope
    cfg.update(extra)
    return cfg


def test_only_forecast_plus_day0_is_a_live_scope() -> None:
    assert EDLI_LIVE_SCOPES == frozenset({"forecast_plus_day0"})


def test_forecast_plus_day0_accepts_forecast_and_day0_flags() -> None:
    _assert_edli_live_scope(_cfg("forecast_plus_day0"))


def test_missing_scope_defaults_to_forecast_plus_day0() -> None:
    _assert_edli_live_scope(_cfg(None))


@pytest.mark.parametrize("scope", ["forecast_only", "day0_shadow", "totally_made_up"])
def test_non_live_scopes_are_rejected(scope: str) -> None:
    with pytest.raises(RuntimeError, match=f"UNSUPPORTED_EDLI_LIVE_SCOPE:{scope}"):
        _assert_edli_live_scope(_cfg(scope))


def test_scope_assertion_does_not_arm_or_mutate_config() -> None:
    cfg = _cfg("forecast_plus_day0")

    _assert_edli_live_scope(cfg)

    assert cfg.get("real_order_submit_enabled", False) is False
    assert cfg["edli_live_scope"] == "forecast_plus_day0"
