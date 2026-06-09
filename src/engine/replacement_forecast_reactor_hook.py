"""Pure pre-intent hook for replacement forecast shadow/veto evidence.

This module is side-effect-free: it consumes an already-read B0 candidate and
an already-read replacement posterior bundle, then returns the effective values
the caller may use before final order intent construction. The DB-backed
factory in ``replacement_forecast_hook_factory`` may write an audit row for a
shadow-veto decision; that write is outside this pure hook contract.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import Any, Mapping

from src.data.replacement_forecast_bundle_reader import ReplacementForecastPosteriorBundle
from src.data.replacement_forecast_guardrail_report import ReplacementForecastGuardrailReport
from src.data.replacement_forecast_readiness import ReplacementForecastReadinessDecision
from src.data.replacement_forecast_receipt_provenance import ReplacementForecastReceiptProvenance, build_replacement_forecast_receipt_provenance
from src.data.replacement_forecast_runtime_policy import (
    BLOCKED_STATUS,
    LIVE_AUTHORITY_STATUS,
    ReplacementForecastRuntimePolicy,
    SAFE_DEFAULT_STATUS,
    SHADOW_ONLY_STATUS,
    SHADOW_VETO_ONLY_STATUS,
)
from src.data.replacement_forecast_switch_decision import (
    SWITCH_BLOCKED,
    SWITCH_DISABLED,
    SWITCH_LIVE_AUTHORITY,
    SWITCH_SHADOW_ONLY,
    SWITCH_SHADOW_VETO_ONLY,
    ReplacementForecastSwitchDecision,
)
from src.engine.replacement_forecast_veto import ReplacementForecastVetoDecision, ReplacementForecastVetoInput, apply_replacement_forecast_shadow_veto


_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class ReplacementForecastCandidateView:
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
            value = str(getattr(self, field_name) or "")
            if not value:
                raise ValueError(f"{field_name} is required")
            if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
                raise ValueError(f"{field_name} must use full replacement identity")
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
                raise ValueError("q values must be in [0, 1]")
        if self.baseline_kelly_fraction < 0.0 or self.candidate_kelly_fraction < 0.0:
            raise ValueError("kelly fractions must be non-negative")
        # FIX-3 (§0.5) structural guard: the directional tokens must be well-formed
        # so the posterior-derived DIRECTION LAW recheck at the flip boundary has a
        # parseable side+bin to validate. A malformed direction is unconstructable.
        for field_name in ("baseline_direction", "candidate_direction"):
            side, bin_id = _split_direction(str(getattr(self, field_name)))
            if side not in {"buy_yes", "buy_no"}:
                raise ValueError(f"{field_name} side must be buy_yes or buy_no")
            if not bin_id:
                raise ValueError(f"{field_name} must carry a bin id as side:bin")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ReplacementForecastCandidateView":
        return cls(
            baseline_direction=str(payload.get("baseline_direction") or payload.get("direction") or ""),
            baseline_q_posterior=float(payload.get("baseline_q_posterior", payload.get("q_posterior", 0.0))),
            baseline_q_lcb=float(payload.get("baseline_q_lcb", payload.get("q_lcb_5pct", 0.0))),
            baseline_kelly_fraction=float(payload.get("baseline_kelly_fraction", payload.get("kelly_fraction", 0.0))),
            candidate_direction=str(payload.get("candidate_direction") or payload.get("replacement_direction") or ""),
            candidate_q_posterior=float(payload.get("candidate_q_posterior", payload.get("replacement_q_posterior", 0.0))),
            candidate_q_lcb=float(payload.get("candidate_q_lcb", payload.get("replacement_q_lcb", 0.0))),
            candidate_kelly_fraction=float(payload.get("candidate_kelly_fraction", payload.get("replacement_kelly_fraction", 0.0))),
            market_snapshot_id=str(payload.get("market_snapshot_id") or ""),
            condition_id=str(payload.get("condition_id") or ""),
            token_id=str(payload.get("token_id") or ""),
            decision_time=str(payload.get("decision_time") or ""),
        )

    def baseline_values(self) -> dict[str, object]:
        return {
            "direction": self.baseline_direction,
            "q_posterior": self.baseline_q_posterior,
            "q_lcb": self.baseline_q_lcb,
            "kelly_fraction": self.baseline_kelly_fraction,
        }


@dataclass(frozen=True)
class ReplacementForecastReactorHookResult:
    status: str
    reason_codes: tuple[str, ...]
    effective_direction: str
    effective_q_posterior: float
    effective_q_lcb: float
    effective_kelly_fraction: float
    veto_decision: ReplacementForecastVetoDecision | None = None
    receipt_provenance: ReplacementForecastReceiptProvenance | None = None

    @property
    def changed_baseline(self) -> bool:
        return self.veto_decision is not None and (
            self.effective_direction != self.veto_decision.baseline_direction
            or abs(self.effective_q_posterior - self.veto_decision.baseline_q_posterior) > 1e-15
            or abs(self.effective_q_lcb - self.veto_decision.baseline_q_lcb) > 1e-15
            or abs(self.effective_kelly_fraction - self.veto_decision.baseline_kelly_fraction) > 1e-15
        )

    def effective_values(self) -> dict[str, object]:
        return {
            "direction": self.effective_direction,
            "q_posterior": self.effective_q_posterior,
            "q_lcb": self.effective_q_lcb,
            "kelly_fraction": self.effective_kelly_fraction,
        }

    def as_receipt_tag(self) -> dict[str, object] | None:
        if self.receipt_provenance is None:
            return None
        return self.receipt_provenance.as_dict()


def _selected_bin_id(replacement_bundle: ReplacementForecastPosteriorBundle) -> str | None:
    """Return argmax(q) bin id from the replacement posterior, or None if empty."""

    q = getattr(replacement_bundle, "q", None) or {}
    if not isinstance(q, Mapping) or not q:
        return None
    return max((str(key) for key in q), key=lambda key: (float(q[key]), key))


def _split_direction(direction: str) -> tuple[str, str | None]:
    """Split a directional token 'buy_yes:bin' -> ('buy_yes', 'bin')."""

    text = str(direction or "")
    if ":" in text:
        side, _, bin_id = text.partition(":")
        return side.strip(), bin_id.strip() or None
    return text.strip(), None


def _lawful_direction_for_candidate(
    candidate_direction: str, replacement_bundle: ReplacementForecastPosteriorBundle
) -> str | None:
    """Re-derive the lawful direction from (candidate bin vs argmax(replacement.q)).

    DIRECTION LAW (FIX-3, §0.5): buy_yes <=> the candidate's bin IS the posterior
    argmax bin; otherwise buy_no. Returns the lawful 'side:bin' token, or None when
    the candidate carries no bin id (then no posterior-derived law can be asserted).
    """

    _side, bin_id = _split_direction(candidate_direction)
    if bin_id is None:
        return None
    selected = _selected_bin_id(replacement_bundle)
    if selected is None:
        return None
    lawful_side = "buy_yes" if selected == bin_id else "buy_no"
    return f"{lawful_side}:{bin_id}"


def _no_change(candidate: ReplacementForecastCandidateView, *, status: str, reason_codes: tuple[str, ...]) -> ReplacementForecastReactorHookResult:
    return ReplacementForecastReactorHookResult(
        status=status,
        reason_codes=reason_codes,
        effective_direction=candidate.baseline_direction,
        effective_q_posterior=candidate.baseline_q_posterior,
        effective_q_lcb=candidate.baseline_q_lcb,
        effective_kelly_fraction=candidate.baseline_kelly_fraction,
    )


def _forecast_decision_payload(
    *,
    replacement_bundle: ReplacementForecastPosteriorBundle,
    candidate: ReplacementForecastCandidateView,
    status: str,
    reasons: tuple[str, ...],
) -> dict[str, object]:
    return {
        "posterior_id": replacement_bundle.posterior_id,
        "product_id": replacement_bundle.product_id,
        "baseline_direction": candidate.baseline_direction,
        "candidate_direction": candidate.candidate_direction,
        "allowed_direction": candidate.candidate_direction,
        "baseline_q_posterior": candidate.baseline_q_posterior,
        "candidate_q_posterior": candidate.candidate_q_posterior,
        "allowed_q_posterior": candidate.candidate_q_posterior,
        "baseline_q_lcb": candidate.baseline_q_lcb,
        "candidate_q_lcb": candidate.candidate_q_lcb,
        "allowed_q_lcb": candidate.candidate_q_lcb,
        "baseline_kelly_fraction": candidate.baseline_kelly_fraction,
        "candidate_kelly_fraction": candidate.candidate_kelly_fraction,
        "allowed_kelly_fraction": candidate.candidate_kelly_fraction,
        "veto": False,
        "reasons": reasons,
        "market_snapshot_id": candidate.market_snapshot_id,
        "condition_id": candidate.condition_id,
        "token_id": candidate.token_id,
        "decision_time": candidate.decision_time,
        "trade_authority_status": status,
    }


def apply_replacement_forecast_reactor_hook(
    *,
    policy: ReplacementForecastRuntimePolicy,
    switch_decision: ReplacementForecastSwitchDecision | None = None,
    candidate: ReplacementForecastCandidateView | Mapping[str, Any],
    replacement_bundle: ReplacementForecastPosteriorBundle | None = None,
    readiness: ReplacementForecastReadinessDecision | None = None,
    guardrail_report: ReplacementForecastGuardrailReport | Mapping[str, Any] | None = None,
) -> ReplacementForecastReactorHookResult:
    """Apply replacement forecast shadow/veto logic before final intent.

    Disabled, blocked, and shadow-only states never mutate the baseline
    candidate. Any non-disabled path must pass through the daemon-facing switch
    decision so stale inventory, missing readiness, or missing replacement
    bundles cannot be bypassed by handing the hook a raw policy.
    """

    if not isinstance(policy, ReplacementForecastRuntimePolicy):
        raise TypeError("policy must be ReplacementForecastRuntimePolicy")
    candidate_view = candidate if isinstance(candidate, ReplacementForecastCandidateView) else ReplacementForecastCandidateView.from_mapping(candidate)
    if policy.status == SAFE_DEFAULT_STATUS:
        return _no_change(candidate_view, status="DISABLED", reason_codes=policy.reason_codes)
    if switch_decision is None:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=("REPLACEMENT_REACTOR_SWITCH_DECISION_MISSING",))
    if not isinstance(switch_decision, ReplacementForecastSwitchDecision):
        raise TypeError("switch_decision must be ReplacementForecastSwitchDecision")
    if switch_decision.status == SWITCH_DISABLED:
        return _no_change(candidate_view, status="DISABLED", reason_codes=switch_decision.reason_codes)
    if switch_decision.status == SWITCH_BLOCKED:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=switch_decision.reason_codes)
    if policy.status == BLOCKED_STATUS:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=policy.reason_codes)
    if policy.status != LIVE_AUTHORITY_STATUS and (
        policy.can_initiate_trade or policy.can_increase_kelly or policy.can_flip_direction
    ):
        return _no_change(candidate_view, status="BLOCKED", reason_codes=("REPLACEMENT_REACTOR_POLICY_UNSUPPORTED",))
    if switch_decision.status == SWITCH_SHADOW_ONLY:
        return _no_change(candidate_view, status="SHADOW_ONLY", reason_codes=switch_decision.reason_codes)
    if policy.status == SHADOW_ONLY_STATUS:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=("REPLACEMENT_REACTOR_SWITCH_POLICY_MISMATCH",))
    if (
        policy.status == SHADOW_VETO_ONLY_STATUS
        and switch_decision.status == SWITCH_SHADOW_VETO_ONLY
        and switch_decision.can_apply_veto
        and policy.can_apply_veto
    ):
        mode = "SHADOW_VETO_ONLY"
    elif (
        policy.status == LIVE_AUTHORITY_STATUS
        and switch_decision.status == SWITCH_LIVE_AUTHORITY
        and switch_decision.can_initiate_trade
    ):
        mode = "LIVE_AUTHORITY"
    else:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=("REPLACEMENT_REACTOR_POLICY_UNSUPPORTED",))
    if replacement_bundle is None or readiness is None:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=("REPLACEMENT_REACTOR_HOOK_DEPENDENCY_MISSING",))

    if mode == "LIVE_AUTHORITY":
        # Authorization gates: a flip vs the baseline direction requires explicit
        # flip authority; a Kelly increase requires explicit kelly authority.
        if candidate_view.candidate_direction != candidate_view.baseline_direction and not policy.can_flip_direction:
            return _no_change(
                candidate_view,
                status="BLOCKED",
                reason_codes=("REPLACEMENT_REACTOR_DIRECTION_FLIP_NOT_AUTHORIZED",),
            )
        if candidate_view.candidate_kelly_fraction > candidate_view.baseline_kelly_fraction + 1e-15 and not policy.can_increase_kelly:
            return _no_change(
                candidate_view,
                status="BLOCKED",
                reason_codes=("REPLACEMENT_REACTOR_KELLY_INCREASE_NOT_AUTHORIZED",),
            )
        # FIX-3 (§0.5): re-assert DIRECTION LAW at the flip/consuming boundary.
        # Do NOT trust the upstream candidate_direction string. Re-derive the
        # lawful side from (candidate bin vs argmax(replacement.q)); if the claimed
        # side disagrees, refuse the flip with the typed law-violation receipt.
        lawful_direction = _lawful_direction_for_candidate(
            candidate_view.candidate_direction, replacement_bundle
        )
        if lawful_direction is not None and lawful_direction != candidate_view.candidate_direction:
            return _no_change(
                candidate_view,
                status="BLOCKED",
                reason_codes=("REPLACEMENT_FORECAST_DIRECTION_LAW_VIOLATION",),
            )
        decision_payload = _forecast_decision_payload(
            replacement_bundle=replacement_bundle,
            candidate=candidate_view,
            status="LIVE_AUTHORITY",
            reasons=("REPLACEMENT_LIVE_AUTHORITY_APPLIED",),
        )
        live_receipt_provenance = build_replacement_forecast_receipt_provenance(
            veto_decision=decision_payload,
            readiness=readiness,
            guardrail_report=guardrail_report,
        )
        return ReplacementForecastReactorHookResult(
            status="LIVE_AUTHORITY",
            reason_codes=("REPLACEMENT_LIVE_AUTHORITY_APPLIED",),
            effective_direction=candidate_view.candidate_direction,
            effective_q_posterior=candidate_view.candidate_q_posterior,
            effective_q_lcb=candidate_view.candidate_q_lcb,
            effective_kelly_fraction=candidate_view.candidate_kelly_fraction,
            receipt_provenance=live_receipt_provenance,
        )

    veto_decision = apply_replacement_forecast_shadow_veto(
        replacement_bundle=replacement_bundle,
        veto_input=ReplacementForecastVetoInput(
            baseline_direction=candidate_view.baseline_direction,
            baseline_q_posterior=candidate_view.baseline_q_posterior,
            baseline_q_lcb=candidate_view.baseline_q_lcb,
            baseline_kelly_fraction=candidate_view.baseline_kelly_fraction,
            candidate_direction=candidate_view.candidate_direction,
            candidate_q_posterior=candidate_view.candidate_q_posterior,
            candidate_q_lcb=candidate_view.candidate_q_lcb,
            candidate_kelly_fraction=candidate_view.candidate_kelly_fraction,
            market_snapshot_id=candidate_view.market_snapshot_id,
            condition_id=candidate_view.condition_id,
            token_id=candidate_view.token_id,
            decision_time=candidate_view.decision_time,
        ),
    )
    receipt_provenance = build_replacement_forecast_receipt_provenance(
        veto_decision=veto_decision,
        readiness=readiness,
        guardrail_report=guardrail_report,
    )
    return ReplacementForecastReactorHookResult(
        status="SHADOW_VETO_ONLY",
        reason_codes=veto_decision.reasons or policy.reason_codes,
        effective_direction=veto_decision.allowed_direction,
        effective_q_posterior=candidate_view.baseline_q_posterior,
        effective_q_lcb=veto_decision.allowed_q_lcb,
        effective_kelly_fraction=veto_decision.allowed_kelly_fraction,
        veto_decision=veto_decision,
        receipt_provenance=receipt_provenance,
    )
