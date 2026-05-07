# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §6 days 56-60 (Gate 2);
#                  ULTIMATE_DESIGN §5 Gate 2 phantom-type subsection;
#                  RISK_REGISTER R3 mitigation (ShadowExecutor cannot construct LiveAuthToken)

"""Gate 2: ShadowExecutor ABC + concrete impl for non-live paths.

sunset_date: 2026-08-04  (90 days from authoring per ANTI_DRIFT_CHARTER §5)

ShadowExecutor is the NON-live path.  Its submit() signature has no `token`
parameter — making cross-path confusion a type-time error.

Used by replay, paper, and backtest paths.  The structural impossibility of
constructing the live auth token from this module is the R3 mitigation
guarantee: ShadowExecutor tests are unaffected by Gate 2 phantom-type
enforcement by definition, because this module never imports from the live
execution module.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------
SUNSET_DATE: str = "2026-08-04"
_GATE_NAME: str = "gate2_shadow_executor"

# STRUCTURAL INVARIANT: this module MUST NOT import from the live execution module.
# The absence of the live auth token type here is the Gate 2 enforcement mechanism.
# Any import of the phantom token type in this file is an R3 mitigation violation.


# ---------------------------------------------------------------------------
# ShadowExecutor ABC
# ---------------------------------------------------------------------------

class ShadowExecutor(ABC):
    """Abstract base for all shadow/paper/replay order executors.

    The submit() signature deliberately has NO token parameter.  Shadow
    execution does not go to the live venue; therefore a LiveAuthToken is
    neither required nor permitted at this seam.

    Gate 2 enforcement: mypy/pyright will reject any caller that tries to pass
    a live auth token to ShadowExecutor.submit(), because the parameter does not
    exist in this signature.  The converse (calling the live executor without
    a token) is also a type error because the live ABC's _do_submit requires one.

    Concrete subclasses MUST implement _do_shadow_submit.
    """

    def submit(self, order: Any) -> Any:
        """Run shadow submission — no live venue contact, no token required.

        Delegates to _do_shadow_submit after logging the shadow routing.
        """
        logger.debug(
            "%s.submit: shadow path — no live venue contact",
            type(self).__name__,
        )
        return self._do_shadow_submit(order)

    @abstractmethod
    def _do_shadow_submit(self, order: Any) -> Any:
        """Concrete shadow implementation receives order only (no token)."""
        ...


# ---------------------------------------------------------------------------
# ShadowExecutorImpl — concrete no-op / paper fill impl
# ---------------------------------------------------------------------------

class ShadowExecutorImpl(ShadowExecutor):
    """Concrete ShadowExecutor for replay, paper, and backtest paths.

    Returns a minimal result dict that mimics the shape callers expect without
    touching any live venue or Gate 2 token machinery.
    """

    def _do_shadow_submit(self, order: Any) -> dict[str, Any]:
        """Return a synthetic shadow-fill result without any live venue contact."""
        order_id = getattr(order, "token_id", None) or getattr(order, "trade_id", None) or "shadow"
        logger.info(
            "ShadowExecutorImpl: paper fill for order=%r",
            order_id,
        )
        return {
            "status": "shadow_filled",
            "order_id": f"shadow:{order_id}",
            "live_venue_contacted": False,
        }
