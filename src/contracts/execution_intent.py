from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from src.contracts.semantic_types import Direction

if TYPE_CHECKING:
    from src.contracts.slippage_bps import SlippageBps

@dataclass(frozen=True)
class ExecutionIntent:
    """Replaces loose size_usd passing and limits. 
    
    Contains toxicity budgets, sandbox flags, and 
    everything risk-related from the Adverse Execution Plane.
    """
    direction: Direction
    target_size_usd: float
    limit_price: float
    toxicity_budget: float
    # Slice P3.3 (PR #19 phase 3, 2026-04-26): typed slippage budget.
    # Pre-fix `max_slippage: float` was unit-ambiguous (caller read 0.02 as
    # either 0.02 bps or 2% — the type system couldn't distinguish) AND
    # had zero readers in src/, making it a dead budget that nobody
    # enforced. Promoting to SlippageBps gives the magnitude an explicit
    # unit (bps) and an explicit direction semantic (adverse limit).
    # Enforcement (actually rejecting fills above this budget) remains a
    # separate follow-on packet — P3.3 closes the typing seam first.
    max_slippage: "SlippageBps"
    is_sandbox: bool
    market_id: str
    token_id: str
    timeout_seconds: int
    decision_edge: float = 0.0  # T5.a 2026-04-23: field was read at src/execution/executor.py:136,428 but missing from dataclass, latent TypeError on live entry; paired default maintains backward compatibility.
    # U1: executable CLOB snapshot citation required by venue_command_repo
    # before persistence/submission. Do not populate this from forecast
    # decision_snapshot_id; it is market/execution truth, not model truth.
    executable_snapshot_id: str = ""
    executable_snapshot_min_tick_size: Decimal | str | None = None
    executable_snapshot_min_order_size: Decimal | str | None = None
    executable_snapshot_neg_risk: bool | None = None
    # R3 A2 allocation metadata. These fields are intentionally typed on the
    # production intent boundary so per-event / per-resolution-window /
    # correlated-exposure caps do not depend on test-only dynamic attributes.
    event_id: str = ""
    resolution_window: str = "default"
    correlation_key: str = ""

    def __post_init__(self) -> None:
        # Slice P3-fix1 (post-review BLOCKER from critic M1 + code-reviewer
        # M3, 2026-04-26): runtime isinstance check on max_slippage.
        # Pre-fix1, the type annotation was a string (forward-ref via
        # TYPE_CHECKING), so passing a raw float silently stored as float
        # — the typing seam was illusory. Now the dataclass refuses
        # construction at runtime; the antibody is universal across
        # production callers + every test fixture.
        from src.contracts.slippage_bps import SlippageBps
        if not isinstance(self.max_slippage, SlippageBps):
            raise TypeError(
                f"ExecutionIntent.max_slippage must be SlippageBps, "
                f"got {type(self.max_slippage).__name__} "
                f"(value={self.max_slippage!r}). Per P3.3 + P3-fix1, "
                f"raw floats are no longer accepted at the boundary."
            )
        normalized_event = str(self.event_id or self.market_id or "").strip()
        normalized_window = str(self.resolution_window or "default").strip() or "default"
        normalized_correlation = str(self.correlation_key or normalized_event or self.market_id or "").strip()
        object.__setattr__(self, "event_id", normalized_event)
        object.__setattr__(self, "resolution_window", normalized_window)
        object.__setattr__(self, "correlation_key", normalized_correlation)
