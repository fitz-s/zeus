from dataclasses import dataclass
from src.contracts.semantic_types import Direction

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
    max_slippage: float
    is_sandbox: bool
    market_id: str
    token_id: str
    timeout_seconds: int
    slice_policy: str = "single_shot"
    reprice_policy: str = "static"
    liquidity_guard: bool = True
