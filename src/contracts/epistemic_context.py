import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class EpistemicContext:
    """Physical mapping of 'what the system knew and when it knew it'.
    
    Strictly forbids 'date.today()' scattered locally across the system. This context 
    must be issued by the Orchestrator and passed deeply into the Fair Value Plane.
    """
    decision_time_utc: datetime
    data_cutoff_time: datetime
    data_version: str
    is_fallback: bool
    
    def __post_init__(self):
        if self.decision_time_utc.tzinfo is None:
            raise ValueError("EpistemicContext requires timezone-aware decision time.")
            
    @classmethod
    def enter_cycle(cls, fallback_override: Optional[datetime] = None) -> "EpistemicContext":
        """Created strictly at the entry of the CycleRunner."""
        now = fallback_override or datetime.now(timezone.utc)
        return cls(
            decision_time_utc=now,
            data_cutoff_time=now,
            data_version="live_v1",
            is_fallback=fallback_override is not None
        )
