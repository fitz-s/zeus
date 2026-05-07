# Created: 2026-05-06
# Last reused or audited: 2026-05-06
# Authority basis: IMPLEMENTATION_PLAN §6 days 56-60 (Gate 2);
#                  architecture/capabilities.yaml live_venue_submit hard_kernel_paths

"""Concrete LiveExecutor adapter for the Polymarket venue.

sunset_date: 2026-08-04  (90 days from authoring per ANTI_DRIFT_CHARTER §5)

Downstream callers import from this module, not from live_executor directly,
per capabilities.yaml hard_kernel_paths.  LiveAuthToken is re-exported here
for caller convenience.
"""

from __future__ import annotations

from typing import Any

from src.execution.live_executor import LiveAuthToken, LiveExecutor

# Re-export for caller convenience (capabilities.yaml routes callers here).
__all__ = ["LiveAuthToken", "VenueAdapterExecutor"]


class VenueAdapterExecutor(LiveExecutor):
    """Concrete LiveExecutor that adapts to the existing Polymarket CLOB venue.

    For live submissions, callers should use:
        executor = VenueAdapterExecutor()
        result = executor.submit(order)

    The base-class submit() runs kill-switch + risk-level + freeze checks,
    mints a LiveAuthToken, then calls _do_submit with the validated token.
    """

    def _do_submit(self, order: Any, token: LiveAuthToken) -> Any:
        """Delegate to the existing executor path after gate checks pass.

        The token proves all Gate 2 checks completed.  This method does NOT
        reconstruct or bypass the token -- it is proof the live path was taken.
        """
        # Lazy import to avoid circular deps at module load time.
        from src.execution.executor import execute_intent, execute_final_intent
        from src.contracts import FinalExecutionIntent, ExecutionIntent

        if isinstance(order, FinalExecutionIntent):
            return execute_final_intent(order)
        if isinstance(order, ExecutionIntent):
            # Legacy path: edge_vwmp not available here; caller should use
            # execute_final_intent for new code.  Provide a sentinel float.
            return execute_intent(order, edge_vwmp=float(order.limit_price), label="venue_adapter")
        raise TypeError(
            f"VenueAdapterExecutor._do_submit: unsupported order type {type(order).__name__}. "
            "Use FinalExecutionIntent or ExecutionIntent."
        )
