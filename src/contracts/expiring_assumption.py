from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, TypeVar

T = TypeVar("T")

@dataclass(frozen=True)
class ExpiringAssumption(Generic[T]):
    """Turns a hardcoded float into a dying assumption.
    
    Enforces that 'buy_no_floor' will expire gracefully, 
    halting systems until verified rather than silently bit-rotting.
    """
    value: T
    fallback: T
    last_verified_at: datetime
    max_lifespan_days: int
    kill_switch_action: str  # "halt_trading", "revert_to_fallback"
    semantic_version: str
    owner: str
    verified_by: str
    verification_source: str
    
    def is_valid(self, current_time: datetime) -> bool:
        """Returns True if the assumption has not yet rotted."""
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
            
        return (current_time - self.last_verified_at).days <= self.max_lifespan_days

    @property
    def active_value(self) -> T:
        """Safe access: fallback returned if system drifts beyond verification bounds."""
        now = datetime.now(timezone.utc)
        if self.is_valid(now):
            return self.value
        if self.kill_switch_action == "halt_trading":
            raise RuntimeError(f"ExpiringAssumption died: max {self.max_lifespan_days} days exceeded.")
        
        return self.fallback
