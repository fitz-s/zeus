from dataclasses import dataclass
from typing import Optional, List
import numpy as np

from src.contracts.semantic_types import EntryMethod

@dataclass(frozen=True)
class EdgeContext:
    """The unified Semantic Context carrying both the probabilities and their provenance.
    
    This replaces `p_posterior: float` as the parameter signature for Exit logic, ensuring 
    that functions cannot consume edge estimations without automatically absorbing the 
    EntryMethod/Decision metrics.
    """
    p_raw: np.ndarray
    p_cal: np.ndarray
    p_market: np.ndarray
    p_posterior: float
    forward_edge: float
    alpha: float
    confidence_band_upper: float
    confidence_band_lower: float
    entry_provenance: EntryMethod
    decision_snapshot_id: str
    n_edges_found: int
    n_edges_after_fdr: int
    
    # Phase 3 Hard-Trigger Metrics
    market_velocity_1h: float = 0.0
    divergence_score: float = 0.0
    
    @property
    def ci_width(self) -> float:
        return self.confidence_band_upper - self.confidence_band_lower
        
    def __post_init__(self):
        if not isinstance(self.entry_provenance, EntryMethod):
            # Attempt coercion or log warning
            pass
