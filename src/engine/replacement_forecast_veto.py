"""Shadow-veto artifact for replacement forecast posterior integration."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from src.data.replacement_forecast_bundle_reader import ReplacementForecastPosteriorBundle
from src.strategy.openmeteo_ecmwf_ifs9_aifs_soft_anchor import PRODUCT_ID, apply_shadow_veto_guardrail


SOURCE_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
STRATEGY_KEY = SOURCE_ID
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastVetoInput:
    baseline_direction: str
    baseline_q_posterior: float
    baseline_q_lcb: float
    baseline_kelly_fraction: float
    candidate_direction: str
    candidate_q_posterior: float
    candidate_q_lcb: float
    candidate_kelly_fraction: float
    market_snapshot_id: str
    condition_id: str
    token_id: str
    decision_time: str

    def __post_init__(self) -> None:
        for field_name in ("baseline_direction", "candidate_direction", "market_snapshot_id", "condition_id", "token_id", "decision_time"):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be set")
        for field_name in (
            "baseline_q_posterior",
            "baseline_q_lcb",
            "baseline_kelly_fraction",
            "candidate_q_posterior",
            "candidate_q_lcb",
            "candidate_kelly_fraction",
        ):
            value = float(getattr(self, field_name))
            if not math.isfinite(value):
                raise ValueError(f"{field_name} must be finite")
        for field_name in ("baseline_q_posterior", "baseline_q_lcb", "candidate_q_posterior", "candidate_q_lcb"):
            if not 0.0 <= float(getattr(self, field_name)) <= 1.0:
                raise ValueError("q_posterior and q_lcb values must be in [0, 1]")
        if self.baseline_kelly_fraction < 0.0 or self.candidate_kelly_fraction < 0.0:
            raise ValueError("kelly fractions must be non-negative")


@dataclass(frozen=True)
class ReplacementForecastVetoDecision:
    posterior_id: int
    product_id: str
    baseline_direction: str
    candidate_direction: str
    allowed_direction: str
    baseline_q_posterior: float
    candidate_q_posterior: float
    allowed_q_posterior: float
    baseline_q_lcb: float
    candidate_q_lcb: float
    allowed_q_lcb: float
    baseline_kelly_fraction: float
    candidate_kelly_fraction: float
    allowed_kelly_fraction: float
    veto: bool
    reasons: tuple[str, ...]
    market_snapshot_id: str
    condition_id: str
    token_id: str
    decision_time: str
    dependency_source_run_ids: Mapping[str, object]
    provenance: Mapping[str, object]
    trade_authority_status: str = "SHADOW_VETO_ONLY"

    def __post_init__(self) -> None:
        if _FORBIDDEN_TRANSCRIPT_ALIAS in self.product_id.lower():
            raise ValueError("product_id must use full product identity")
        if self.allowed_direction != self.baseline_direction:
            raise ValueError("replacement veto cannot flip trade direction")
        if self.allowed_q_lcb > self.baseline_q_lcb + 1e-15:
            raise ValueError("replacement veto cannot raise q_lcb")
        if self.allowed_kelly_fraction > self.baseline_kelly_fraction + 1e-15:
            raise ValueError("replacement veto cannot raise Kelly")
        if self.trade_authority_status != "SHADOW_VETO_ONLY":
            raise ValueError("replacement veto artifact must be shadow-veto-only")

    def as_shadow_decision_row(self) -> dict[str, object]:
        return {
            "posterior_id": self.posterior_id,
            "market_snapshot_id": self.market_snapshot_id,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "decision_time": self.decision_time,
            "baseline_direction": self.baseline_direction,
            "candidate_direction": self.candidate_direction,
            "allowed_direction": self.allowed_direction,
            "baseline_q_posterior": self.baseline_q_posterior,
            "candidate_q_posterior": self.candidate_q_posterior,
            "allowed_q_posterior": self.allowed_q_posterior,
            "baseline_q_lcb": self.baseline_q_lcb,
            "candidate_q_lcb": self.candidate_q_lcb,
            "allowed_q_lcb": self.allowed_q_lcb,
            "baseline_kelly_fraction": self.baseline_kelly_fraction,
            "candidate_kelly_fraction": self.candidate_kelly_fraction,
            "allowed_kelly_fraction": self.allowed_kelly_fraction,
            "veto": 1 if self.veto else 0,
            "veto_reason": ",".join(self.reasons),
            "dependency_source_run_ids_json": self.dependency_source_run_ids,
            "provenance_json": self.provenance,
            "trade_authority_status": self.trade_authority_status,
        }


def apply_replacement_forecast_shadow_veto(
    *,
    replacement_bundle: ReplacementForecastPosteriorBundle,
    veto_input: ReplacementForecastVetoInput,
) -> ReplacementForecastVetoDecision:
    """Apply replacement forecast shadow/veto constraints before order intent."""

    if not isinstance(replacement_bundle, ReplacementForecastPosteriorBundle):
        raise TypeError("replacement_bundle must be ReplacementForecastPosteriorBundle")
    if not isinstance(veto_input, ReplacementForecastVetoInput):
        raise TypeError("veto_input must be ReplacementForecastVetoInput")
    guardrail = apply_shadow_veto_guardrail(
        baseline_direction=veto_input.baseline_direction,
        candidate_direction=veto_input.candidate_direction,
        baseline_q_lcb=veto_input.baseline_q_lcb,
        candidate_q_lcb=veto_input.candidate_q_lcb,
        baseline_kelly_fraction=veto_input.baseline_kelly_fraction,
        candidate_kelly_fraction=veto_input.candidate_kelly_fraction,
    )
    provenance = {
        "source_id": SOURCE_ID,
        "product_id": PRODUCT_ID,
        "posterior_method": replacement_bundle.posterior_method,
        "posterior_source_available_at": replacement_bundle.source_available_at,
        "posterior_computed_at": replacement_bundle.computed_at,
        "baseline_source_run_id": replacement_bundle.baseline_source_run_id,
        "role": "pre_intent_shadow_veto",
        "training_allowed": False,
    }
    return ReplacementForecastVetoDecision(
        posterior_id=replacement_bundle.posterior_id,
        product_id=replacement_bundle.product_id,
        baseline_direction=guardrail.baseline_direction,
        candidate_direction=guardrail.candidate_direction,
        allowed_direction=guardrail.allowed_direction,
        baseline_q_posterior=veto_input.baseline_q_posterior,
        candidate_q_posterior=veto_input.candidate_q_posterior,
        allowed_q_posterior=veto_input.baseline_q_posterior,
        baseline_q_lcb=guardrail.baseline_q_lcb,
        candidate_q_lcb=guardrail.candidate_q_lcb,
        allowed_q_lcb=guardrail.allowed_q_lcb,
        baseline_kelly_fraction=guardrail.baseline_kelly_fraction,
        candidate_kelly_fraction=guardrail.candidate_kelly_fraction,
        allowed_kelly_fraction=guardrail.allowed_kelly_fraction,
        veto=guardrail.veto,
        reasons=guardrail.reasons,
        market_snapshot_id=veto_input.market_snapshot_id,
        condition_id=veto_input.condition_id,
        token_id=veto_input.token_id,
        decision_time=veto_input.decision_time,
        dependency_source_run_ids=replacement_bundle.dependency_json,
        provenance=provenance,
    )
