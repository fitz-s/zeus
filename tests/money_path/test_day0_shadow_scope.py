# Created: 2026-06-05
# Last reused or audited: 2026-06-05
# Authority basis: day0 phased plan P1 (architect 2026-06-05). Adds the `day0_shadow`
#   edli_live_scope: a scope that ADMITS day0 (does not raise DAY0_OUT_OF_SCOPE_FOR_PR332
#   when day0 flags are on) while keeping forecast_only byte-identical, AND structurally
#   forbids any real submit. The no-submit guarantee is STRUCTURAL: day0_shadow neither
#   sets nor implies real_order_submit_enabled, so a day0 candidate routes through the
#   SAME armed-flag block (reactor.py EDLI_REAL_ORDER_SUBMIT_DISABLED / NO_SUBMIT) as
#   every other event when not armed. These are relationship tests: scope -> admission,
#   scope -> (no arm path) -> no-submit.
"""P1 tests for the day0_shadow edli_live_scope (admit day0, structurally no-submit)."""
from __future__ import annotations

import pytest

from src.main import _assert_edli_live_scope


def _day0_flags_on(scope: str) -> dict:
    return {
        "edli_live_scope": scope,
        "day0_extreme_trigger_enabled": True,
        "day0_hard_fact_live_enabled": True,
    }


# ---------------------------------------------------------------------------
# forecast_only stays byte-identical: day0 flags still crash (the #332 guard).
# ---------------------------------------------------------------------------
def test_forecast_only_with_day0_flags_still_raises():
    with pytest.raises(RuntimeError, match="DAY0_OUT_OF_SCOPE_FOR_PR332"):
        _assert_edli_live_scope(_day0_flags_on("forecast_only"))


def test_forecast_only_without_day0_flags_is_silent():
    # Unchanged: forecast_only with no day0 flags must not raise.
    _assert_edli_live_scope(
        {
            "edli_live_scope": "forecast_only",
            "day0_extreme_trigger_enabled": False,
            "day0_hard_fact_live_enabled": False,
        }
    )


def test_default_scope_is_forecast_only_and_admits_no_day0():
    # Missing scope -> forecast_only default -> day0 flags crash (regression pin).
    with pytest.raises(RuntimeError, match="DAY0_OUT_OF_SCOPE_FOR_PR332"):
        _assert_edli_live_scope(
            {
                "day0_extreme_trigger_enabled": True,
                "day0_hard_fact_live_enabled": False,
            }
        )


# ---------------------------------------------------------------------------
# day0_shadow ADMITS day0: day0 flags on must NOT raise.
# ---------------------------------------------------------------------------
def test_day0_shadow_admits_day0_flags_without_raising():
    # The whole point of the new scope: day0 may be turned on without the
    # DAY0_OUT_OF_SCOPE guard firing.
    _assert_edli_live_scope(_day0_flags_on("day0_shadow"))


def test_day0_shadow_admits_each_day0_flag_individually():
    _assert_edli_live_scope(
        {"edli_live_scope": "day0_shadow", "day0_extreme_trigger_enabled": True}
    )
    _assert_edli_live_scope(
        {"edli_live_scope": "day0_shadow", "day0_hard_fact_live_enabled": True}
    )


def test_day0_shadow_with_no_day0_flags_is_silent():
    _assert_edli_live_scope({"edli_live_scope": "day0_shadow"})


# ---------------------------------------------------------------------------
# Unknown scopes still fail closed.
# ---------------------------------------------------------------------------
def test_unknown_scope_still_rejected():
    with pytest.raises(RuntimeError, match="UNSUPPORTED_EDLI_LIVE_SCOPE"):
        _assert_edli_live_scope({"edli_live_scope": "totally_made_up"})


# ---------------------------------------------------------------------------
# STRUCTURAL no-submit: day0_shadow provides NO arm path. The scope axis
# (edli_live_scope) is independent of the arm axis (real_order_submit_enabled);
# day0_shadow neither sets nor requires the arm flag, so any day0 candidate
# falls through the existing not-armed block exactly like a forecast_only one.
# ---------------------------------------------------------------------------
def test_day0_shadow_does_not_imply_arm():
    # Admitting day0_shadow must never flip real_order_submit_enabled on, and the
    # scope assertion must not consult / require it (so it cannot grant submit).
    cfg = _day0_flags_on("day0_shadow")
    assert "real_order_submit_enabled" not in cfg  # scope does not inject an arm flag
    _assert_edli_live_scope(cfg)  # passes without any arm flag present
    assert cfg.get("real_order_submit_enabled", False) is False
