# Created: 2026-07-02
# Authority basis: docs/rebuild/order_engine_implementation_architecture_2026-07-02.md
#   line 53 (self-trade guard: BUILD, nothing exists) +
#   docs/operations/current/plans/order_engine_rebuild_execution_plan_2026-07-02.md
#   W2 packet (self-trade guard lands INERT — no call site wired yet; W3's
#   solve wires it at envelope build).

"""Self-trade guard result/input types (SCH-W2.2-SELF-TRADE remnant).

The executable predicate (``check_self_trade``) and its DB loader
(``load_own_open_resting_orders``) were deleted as dead code in the
gate-stack simplification (Phase 1, 2026-07-06): both had zero call sites
outside this module and their own unit tests, and the designed consumer
(``batch_order_submission.submit_orders_batch``, also deleted in the same
pass since it too had no live caller) was itself never wired to a live
caller.

``SelfTradeVerdict``, ``RestingOrder``, and ``SelfTradeCheckResult`` remain
below as inert result/input types with no current producer or consumer in
this codebase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class SelfTradeVerdict(str, Enum):
    CLEAR = "CLEAR"
    WOULD_SELF_CROSS = "WOULD_SELF_CROSS"
    INDETERMINATE = "INDETERMINATE"


@dataclass(frozen=True)
class RestingOrder:
    """One of Zeus's own open resting orders, as seen by the guard."""

    command_id: str
    token_id: str
    side: str
    price: Decimal | str | float


@dataclass(frozen=True)
class SelfTradeCheckResult:
    verdict: SelfTradeVerdict
    crossing_command_ids: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""
