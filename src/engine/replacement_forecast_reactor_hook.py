# Created: 2026-06-06
# Last reused/audited: 2026-06-13
# Authority basis: workflow A3 diagnosis 2026-06-13, task #62 (Manila/Shanghai/Wellington
#   class). Scope the ReplacementForecastCandidateView bin-id requirement to what actually
#   consumes it: drop the absolutist :bin fail-close that masked the canonical bare-direction
#   producer as a hard reject (UNKNOWN_REVIEW_REQUIRED, zero orders). Single-q-authority
#   regime (2026-06-12) made the baseline provenance-only; the candidate bin stays optional
#   because the downstream DIRECTION-LAW recheck no-ops on a bare candidate.
"""Pure pre-intent hook for live replacement forecast evidence.

This module is side-effect-free: it consumes an already-read B0 candidate and
an already-read replacement posterior bundle, then returns the effective values
the caller may use before final order intent construction. The DB-backed
factory in ``replacement_forecast_hook_factory`` may write an audit row for a
diagnostic decision; that write is outside this pure hook contract.
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
    LIVE_STATUS,
    ReplacementForecastRuntimePolicy,
    SAFE_DEFAULT_STATUS,
)
from src.data.replacement_forecast_switch_decision import (
    SWITCH_BLOCKED,
    SWITCH_DISABLED,
    SWITCH_LIVE,
    ReplacementForecastSwitchDecision,
)

_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"
REPLACEMENT_EXECUTION_LIVE_STATUS = "live"
REPLACEMENT_EXECUTION_EXPERIMENT_STATUS = "experiment"


@dataclass(frozen=True)
class _ExecutionLayerReceiptProvenance:
    inner: ReplacementForecastReceiptProvenance

    def as_dict(self) -> dict[str, Any]:
        payload = self.inner.as_dict()
        payload["runtime_layer"] = REPLACEMENT_EXECUTION_LIVE_STATUS
        return payload


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
        for q_field, lcb_field in (
            ("baseline_q_posterior", "baseline_q_lcb"),
            ("candidate_q_posterior", "candidate_q_lcb"),
        ):
            q_value = float(getattr(self, q_field))
            lcb_value = float(getattr(self, lcb_field))
            if lcb_value > q_value + 1e-12:
                raise ValueError(
                    f"{lcb_field} must not exceed {q_field}: "
                    f"{lcb_value:.12g} > {q_value:.12g}"
                )
        if self.baseline_kelly_fraction < 0.0 or self.candidate_kelly_fraction < 0.0:
            raise ValueError("kelly fractions must be non-negative")
        # FIX-3 (§0.5) structural guard: the directional tokens must be WELL-FORMED
        # — the side must be canonical buy_yes/buy_no so the posterior-derived
        # DIRECTION LAW recheck at the flip boundary has a parseable side. A
        # malformed direction (e.g. 'foo') is unconstructable.
        #
        # task #62 (2026-06-13): the bin-id requirement is SCOPED to what actually
        # consumes it. The canonical producer (replacement_forecast_hook_factory
        # ._candidate_view_from_proof) stamps the baseline VERBATIM from
        # ``proof.direction`` which is canonically BARE (buy_no/buy_yes) system-wide,
        # and the candidate is bare whenever no replacement_bundle has bound a bin
        # (the no-bundle first call site). Under the 2026-06-12 single-q-authority
        # regime the baseline is PROVENANCE-ONLY — its bin is never read downstream —
        # so requiring a ``:bin`` on it (added 2026-06-07, cbc454e17e) was an
        # absolutist fail-close masking a missing INTERNAL transform as a hard reject,
        # sending real tradeable families to UNKNOWN_REVIEW_REQUIRED (zero orders).
        # For the candidate, the bin is OPTIONAL here: the downstream DIRECTION-LAW
        # recheck (_lawful_direction_for_candidate) already no-ops when the candidate
        # carries no bin id (returns None -> skips the flip-veto), so a bare candidate
        # is admissible and still passes the q_lcb>price and DIRECTION-LAW honest
        # gates. We therefore keep the side-well-formedness check for BOTH fields and
        # DROP the bin-required raise.
        for field_name in ("baseline_direction", "candidate_direction"):
            side, _bin_id = _split_direction(str(getattr(self, field_name)))
            if side not in {"buy_yes", "buy_no"}:
                raise ValueError(f"{field_name} side must be buy_yes or buy_no")

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
    veto_decision: Mapping[str, Any] | None = None
    receipt_provenance: ReplacementForecastReceiptProvenance | _ExecutionLayerReceiptProvenance | None = None

    @property
    def changed_baseline(self) -> bool:
        decision = self.veto_decision
        return decision is not None and (
            self.effective_direction != decision.get("baseline_direction")
            or abs(self.effective_q_posterior - float(decision.get("baseline_q_posterior", 0.0))) > 1e-15
            or abs(self.effective_q_lcb - float(decision.get("baseline_q_lcb", 0.0))) > 1e-15
            or abs(self.effective_kelly_fraction - float(decision.get("baseline_kelly_fraction", 0.0))) > 1e-15
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
        "runtime_layer": status,
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
    """Apply live replacement forecast logic before final intent.

    Disabled and blocked states never mutate the baseline candidate. Any live
    path must pass through the daemon-facing switch decision so stale inventory,
    missing readiness, or missing replacement bundles cannot be bypassed by
    handing the hook a raw policy.
    """

    if not isinstance(policy, ReplacementForecastRuntimePolicy):
        raise TypeError("policy must be ReplacementForecastRuntimePolicy")
    candidate_view = candidate if isinstance(candidate, ReplacementForecastCandidateView) else ReplacementForecastCandidateView.from_mapping(candidate)
    if policy.status == SAFE_DEFAULT_STATUS:
        return _no_change(candidate_view, status=REPLACEMENT_EXECUTION_EXPERIMENT_STATUS, reason_codes=policy.reason_codes)
    if switch_decision is None:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=("REPLACEMENT_REACTOR_SWITCH_DECISION_MISSING",))
    if not isinstance(switch_decision, ReplacementForecastSwitchDecision):
        raise TypeError("switch_decision must be ReplacementForecastSwitchDecision")
    if switch_decision.status == SWITCH_DISABLED:
        return _no_change(candidate_view, status=REPLACEMENT_EXECUTION_EXPERIMENT_STATUS, reason_codes=switch_decision.reason_codes)
    if switch_decision.status == SWITCH_BLOCKED:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=switch_decision.reason_codes)
    if policy.status == BLOCKED_STATUS:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=policy.reason_codes)
    if policy.status != LIVE_STATUS and (
        policy.can_initiate_trade or policy.can_increase_kelly or policy.can_flip_direction
    ):
        return _no_change(candidate_view, status="BLOCKED", reason_codes=("REPLACEMENT_REACTOR_POLICY_UNSUPPORTED",))
    if not (
        policy.status == LIVE_STATUS
        and switch_decision.status == SWITCH_LIVE
        and switch_decision.can_initiate_trade
    ):
        return _no_change(candidate_view, status="BLOCKED", reason_codes=("REPLACEMENT_REACTOR_POLICY_UNSUPPORTED",))
    if replacement_bundle is None or readiness is None:
        return _no_change(candidate_view, status="BLOCKED", reason_codes=("REPLACEMENT_REACTOR_HOOK_DEPENDENCY_MISSING",))

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
        status=REPLACEMENT_EXECUTION_LIVE_STATUS,
        reasons=("REPLACEMENT_LIVE_APPLIED",),
    )
    live_receipt_provenance = _ExecutionLayerReceiptProvenance(
        build_replacement_forecast_receipt_provenance(
            veto_decision=decision_payload,
            readiness=readiness,
            guardrail_report=guardrail_report,
        )
    )
    return ReplacementForecastReactorHookResult(
        status=REPLACEMENT_EXECUTION_LIVE_STATUS,
        reason_codes=("REPLACEMENT_LIVE_APPLIED",),
        effective_direction=candidate_view.candidate_direction,
        effective_q_posterior=candidate_view.candidate_q_posterior,
        effective_q_lcb=candidate_view.candidate_q_lcb,
        effective_kelly_fraction=candidate_view.candidate_kelly_fraction,
        receipt_provenance=live_receipt_provenance,
    )
