from __future__ import annotations

from dataclasses import dataclass
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
    slice_policy: str = "single_shot"
    reprice_policy: str = "static"
    liquidity_guard: bool = True
    decision_edge: float = 0.0  # T5.a 2026-04-23: field was read at src/execution/executor.py:136,428 but missing from dataclass, latent TypeError on live entry; paired default maintains backward compatibility.
