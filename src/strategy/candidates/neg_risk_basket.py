# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/A1.yaml
from __future__ import annotations

from . import BaseStrategyCandidate, CandidateMetadata


class NegRiskBasket(BaseStrategyCandidate):
    def __init__(self) -> None:
        super().__init__(CandidateMetadata("neg_risk_basket", "candidate_stub", "Stub for future negative-risk basket benchmarking."))
