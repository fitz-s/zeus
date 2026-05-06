# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: evidence/phase4_h_decision.md L-3; IMPLEMENTATION_PLAN §6 Gate 2;
#                  src/execution/live_executor.py _COMPAT_EXPIRES_AT

"""L-3 carry-forward: @untyped_for_compat 30-day expiry CI enforcement.

Phase 4 critic L-3: @untyped_for_compat decorator (in src/execution/live_executor.py)
carries expires_at: 2026-06-05. No CI check enforces removal before that date.

THIS TEST IS A TIME-BOMB BY DESIGN.
------------------------------------
Today (2026-05-06) this test PASSES — the deadline is 30 days in the future.
After 2026-06-05 this test FAILS — by design, forcing decorator removal.

When this test starts failing:
  1. Remove all @untyped_for_compat decorators from src/execution/
  2. Update call sites to pass LiveAuthToken explicitly (Gate 2 contract)
  3. Delete this file (no longer needed once decorator is gone)

See: evidence/l3_expiry_guard.md for implementation notes.
"""

from __future__ import annotations

import datetime
import importlib
import sys
from types import ModuleType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_live_executor() -> ModuleType:
    """Import (or reload) src.execution.live_executor."""
    mod_name = "src.execution.live_executor"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    return importlib.import_module(mod_name)


def _today() -> datetime.date:
    return datetime.date.today()


def _parse_expires(s: str) -> datetime.date:
    return datetime.date.fromisoformat(s)


# ---------------------------------------------------------------------------
# B-3-1: Module constant _COMPAT_EXPIRES_AT is present and ISO-parseable
# ---------------------------------------------------------------------------

class TestUntypedForCompatExpiryGuard:
    """CI time-bomb: test fails after 2026-06-05 to force decorator removal."""

    def test_compat_expires_at_constant_present(self) -> None:
        """_COMPAT_EXPIRES_AT must be present in live_executor and ISO-date parseable."""
        mod = _get_live_executor()

        assert hasattr(mod, "_COMPAT_EXPIRES_AT"), (
            "src.execution.live_executor must define _COMPAT_EXPIRES_AT "
            "(the 30-day escape hatch expiry date for @untyped_for_compat)"
        )
        raw = mod._COMPAT_EXPIRES_AT
        try:
            _parse_expires(raw)
        except ValueError as exc:
            raise AssertionError(
                f"_COMPAT_EXPIRES_AT={raw!r} is not a valid ISO date: {exc}"
            ) from exc

    def test_compat_expires_at_not_yet_passed(self) -> None:
        """TIME-BOMB: this test FAILS after 2026-06-05 to force @untyped_for_compat removal.

        When this test begins failing in CI:
          - Remove all @untyped_for_compat call sites from src/execution/
          - Update callers to pass LiveAuthToken parameter explicitly
          - Delete this file

        Deadline: 2026-06-05 (30 days after Gate 2 authoring on 2026-05-06).
        """
        mod = _get_live_executor()
        expires_at = _parse_expires(mod._COMPAT_EXPIRES_AT)
        today = _today()

        assert today < expires_at, (
            f"@untyped_for_compat DEADLINE PASSED: expires_at={expires_at}, today={today}. "
            "Remove all @untyped_for_compat decorators from src/execution/ and update "
            "callers to pass LiveAuthToken explicitly. Then delete this test file. "
            "See: evidence/l3_expiry_guard.md"
        )

    def test_untyped_for_compat_decorator_attribute_present(self) -> None:
        """Decorated functions must carry _compat_expires_at matching the module constant."""
        mod = _get_live_executor()

        assert hasattr(mod, "untyped_for_compat"), (
            "src.execution.live_executor must export untyped_for_compat decorator"
        )
        untyped_for_compat = mod.untyped_for_compat
        module_expiry = mod._COMPAT_EXPIRES_AT

        # Verify decorator sets the attribute correctly on a fresh decorated function
        @untyped_for_compat
        def _dummy_fn(x: int) -> int:
            return x

        assert hasattr(_dummy_fn, "_compat_expires_at"), (
            "@untyped_for_compat must set _compat_expires_at on the wrapped function"
        )
        assert _dummy_fn._compat_expires_at == module_expiry, (
            f"_compat_expires_at={_dummy_fn._compat_expires_at!r} must equal "
            f"_COMPAT_EXPIRES_AT={module_expiry!r}"
        )

    def test_decorator_attribute_expiry_not_yet_passed(self) -> None:
        """TIME-BOMB: decorator _compat_expires_at must also be a future date.

        Guards against the edge case where the module constant is updated but the
        decorator itself still embeds the old expiry value.
        """
        mod = _get_live_executor()
        untyped_for_compat = mod.untyped_for_compat

        @untyped_for_compat
        def _dummy_fn2(x: int) -> int:
            return x

        expires_at = _parse_expires(_dummy_fn2._compat_expires_at)
        today = _today()

        assert today < expires_at, (
            f"@untyped_for_compat decorator _compat_expires_at DEADLINE PASSED: "
            f"expires_at={expires_at}, today={today}. "
            "Remove all @untyped_for_compat decorators and update callers. "
            "See: evidence/l3_expiry_guard.md"
        )
