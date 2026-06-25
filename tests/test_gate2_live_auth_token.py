# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §6 days 56-60 (Gate 2);
#                  RISK_REGISTER R3 mitigation (LiveAuthToken phantom);
#                  ULTIMATE_DESIGN §5 Gate 2 phantom-type subsection

"""Gate 2 type-enforcement tests: LiveAuthToken phantom.

Tests:
  1. test_live_executor_mints_token_only_when_kill_switch_off — runtime gate check
  2. test_untyped_for_compat_escape_hatch_records_sunset — @untyped_for_compat attribute
"""

from __future__ import annotations

import os
import warnings

import pytest


def test_live_executor_mints_token_only_when_kill_switch_off() -> None:
    """Kill switch on → RuntimeError; kill switch off → submit succeeds."""
    from src.execution.live_executor import LiveExecutor, LiveAuthToken
    from typing import Any

    class _TestExecutor(LiveExecutor):
        """Minimal concrete LiveExecutor for testing gate checks."""
        def _do_submit(self, order: Any, token: LiveAuthToken) -> dict:
            return {"status": "ok", "token_issued_at": token._issued_at}

    executor = _TestExecutor()

    # --- kill switch ON → RuntimeError ---
    env_backup = os.environ.copy()
    try:
        os.environ["ZEUS_KILL_SWITCH"] = "1"
        # Clear other blocking vars so only kill switch fires
        os.environ.pop("ZEUS_RISK_HALT", None)
        os.environ.pop("ZEUS_SETTLEMENT_FREEZE", None)

        with pytest.raises(RuntimeError, match="kill switch"):
            executor.submit(order={"market_id": "test"})

    finally:
        os.environ.clear()
        os.environ.update(env_backup)

    # --- kill switch OFF → submit succeeds (returns dict with status) ---
    env_backup2 = os.environ.copy()
    try:
        os.environ.pop("ZEUS_KILL_SWITCH", None)
        os.environ.pop("ZEUS_RISK_HALT", None)
        os.environ.pop("ZEUS_SETTLEMENT_FREEZE", None)

        result = executor.submit(order={"market_id": "test"})
        assert result["status"] == "ok"
        assert "token_issued_at" in result

    finally:
        os.environ.clear()
        os.environ.update(env_backup2)


# ---------------------------------------------------------------------------
# C5-4: @untyped_for_compat escape hatch records sunset attribute
# ---------------------------------------------------------------------------

def test_untyped_for_compat_escape_hatch_records_sunset() -> None:
    """@untyped_for_compat decorated function must carry _compat_expires_at attribute.

    The attribute is the machine-readable sunset for CI detection.
    A DeprecationWarning must be emitted at call time.
    """
    from src.execution.live_executor import untyped_for_compat, _COMPAT_EXPIRES_AT

    @untyped_for_compat
    def legacy_submit(order):
        return {"submitted": order}

    # 1. _compat_expires_at attribute must be present
    assert hasattr(legacy_submit, "_compat_expires_at"), (
        "@untyped_for_compat must set _compat_expires_at on the wrapped function"
    )

    # 2. Value must match module constant
    assert legacy_submit._compat_expires_at == _COMPAT_EXPIRES_AT, (
        f"_compat_expires_at={legacy_submit._compat_expires_at!r} "
        f"must equal _COMPAT_EXPIRES_AT={_COMPAT_EXPIRES_AT!r}"
    )

    # 3. DeprecationWarning emitted at call time
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = legacy_submit(order="test_order")
        assert result == {"submitted": "test_order"}

    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert dep_warnings, (
        "@untyped_for_compat must emit a DeprecationWarning on each call"
    )
    warning_text = str(dep_warnings[0].message)
    assert _COMPAT_EXPIRES_AT in warning_text, (
        f"DeprecationWarning must mention expiry date {_COMPAT_EXPIRES_AT!r}. "
        f"Got: {warning_text!r}"
    )


# ---------------------------------------------------------------------------
# R-3 (M-2): K0-1 forgery resistance regression test
# ---------------------------------------------------------------------------

def test_live_auth_token_unforgeable_via_dict_write():
    """K0-1 regression test — phantom token must not be constructible via object.__new__ + __dict__.

    Before R-1 (slots=True), object.__new__(LiveAuthToken) produced an instance
    with a __dict__, allowing arbitrary attribute injection that bypassed the
    __new__ caller-file guard. With slots=True, __dict__ does not exist on
    instances, so the write raises AttributeError.
    """
    import pytest
    from src.execution.live_executor import LiveAuthToken

    obj = object.__new__(LiveAuthToken)
    with pytest.raises(AttributeError):
        obj.__dict__["_issued_at"] = "2026-01-01T00:00:00+00:00"
