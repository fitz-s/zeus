# Created: 2026-08-27
# Last reused or audited: 2026-08-27
# Authority basis: docs/operations/current/plans/reversal_plan_tier0_2026-08-24.md
#   item 9 ("Market-anchored walk-forward calibrator") — live wiring. The
#   calibrator math lives in src/calibration/market_anchored_residual.py; this
#   module carries only the sealed per-candidate RESULT so the solver
#   (src/solve/solver.py) and the actuation certificate
#   (src/engine/event_reactor_adapter.py) act on ONE value.
"""Sealed per-candidate acting-probability correction.

The market-anchored calibrator is fitted and applied ONCE per candidate, at
solve time, where the market price p0 and the raw payoff probability q_raw are
both in scope. The result travels with the decision as this frozen record.

Why a carried record rather than re-deriving at certificate time: the
certificate seam re-projects the family witness to re-prove the candidate's
probability has not been superseded. If it re-fitted the calibrator instead, a
TTL boundary crossed between solve and certificate would silently change the
acting probability and fire a spurious supersession. Carrying ``raw_q`` keeps
that supersession check exact — the certificate still compares the re-projected
RAW witness value against ``raw_q`` — while ``corrected_q`` is the single value
every downstream economics field, cut probability, and receipt agrees on.

This module is a pure contract: no math, no database, no calibrator import. It
sits in ``src/contracts`` precisely so ``src/solve`` and ``src/engine`` can both
name the shape without either depending on ``src/calibration``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


class PayoffQCorrectionUnavailable(ValueError):
    """A required acting-q fit is unavailable; this BUY cannot use raw q."""


@dataclass(frozen=True)
class CalibrationFitScope:
    """Exact ENTRY population that produced one calibrated-q artifact."""

    metric: str
    execution_mode: str
    execution_contract: str
    raw_probability_revision: str

    _TYPE = "CalibrationFitScope"
    _VERSION = 1
    _CONTRACTS_BY_MODE = {
        "TAKER_LIMIT": frozenset({"FOK_FULL_OR_ZERO", "FAK_PARTIAL"}),
        "MAKER_REST": frozenset({"MAKER_REST"}),
    }

    def __post_init__(self) -> None:
        if type(self.metric) is not str:
            raise ValueError("calibration fit scope metric must be a string")
        if self.metric not in {"high", "low"}:
            raise ValueError("calibration fit scope metric is invalid")
        if type(self.execution_mode) is not str:
            raise ValueError("calibration fit scope execution_mode must be a string")
        if self.execution_mode not in self._CONTRACTS_BY_MODE:
            raise ValueError("calibration fit scope execution_mode is invalid")
        if type(self.execution_contract) is not str:
            raise ValueError("calibration fit scope execution_contract must be a string")
        if self.execution_contract not in self._CONTRACTS_BY_MODE[self.execution_mode]:
            raise ValueError("calibration fit scope execution_contract is invalid")
        if type(self.raw_probability_revision) is not str or not self.raw_probability_revision.strip():
            raise ValueError("calibration fit scope raw_probability_revision is required")

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self._TYPE,
            "version": self._VERSION,
            "metric": self.metric,
            "execution_mode": self.execution_mode,
            "execution_contract": self.execution_contract,
            "raw_probability_revision": self.raw_probability_revision,
        }
        payload["scope_hash"] = self._hash_payload(payload)
        return payload

    @staticmethod
    def _hash_payload(payload: dict[str, object]) -> str:
        from src.decision_kernel.canonicalization import stable_hash

        return stable_hash(payload)

    @classmethod
    def from_payload(cls, payload: object) -> "CalibrationFitScope":
        if not isinstance(payload, dict):
            raise ValueError("calibration fit scope payload must be an object")
        expected = {
            "type", "version", "metric", "execution_mode",
            "execution_contract", "raw_probability_revision", "scope_hash",
        }
        if set(payload) != expected:
            raise ValueError("calibration fit scope payload fields are not exact")
        if (
            payload.get("type") != cls._TYPE
            or type(payload.get("version")) is not int
            or payload.get("version") != cls._VERSION
        ):
            raise ValueError("calibration fit scope payload type or version is invalid")
        scope_hash = payload.get("scope_hash")
        if not isinstance(scope_hash, str) or scope_hash != cls._hash_payload(
            {key: payload[key] for key in expected if key != "scope_hash"}
        ):
            raise ValueError("calibration fit scope hash is invalid")
        candidate = cls(
            metric=payload["metric"],
            execution_mode=payload["execution_mode"],
            execution_contract=payload["execution_contract"],
            raw_probability_revision=payload["raw_probability_revision"],
        )
        if candidate.as_payload() != payload:
            raise ValueError("calibration fit scope payload is not canonically encoded")
        return candidate


@dataclass(frozen=True)
class CalibrationPolicySpec:
    """Immutable identity of the calibration component used by a correction.

    This describes the fitted component's algorithm and inputs only.  Fitted
    values (alpha/beta), cutoff, sample count and parameter hash remain on the
    per-candidate correction and are deliberately excluded from this identity.
    """

    algorithm_revision: str
    input_revision: str
    metric_pooling: str
    lead_calendar_revision: str
    lambda_: float
    min_train_weight: int
    beta_bounds: tuple[float, float]
    logit_clip: float
    probability_clip: tuple[float, float]
    refit_seconds: float

    _TYPE = "CalibrationPolicySpec"
    _VERSION = 1

    def __post_init__(self) -> None:
        for name in (
            "algorithm_revision",
            "input_revision",
            "metric_pooling",
            "lead_calendar_revision",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"calibration policy {name} must be a non-empty string")
        if not _finite_number(self.lambda_) or float(self.lambda_) < 0:
            raise ValueError("calibration policy lambda_ must be finite and non-negative")
        if type(self.min_train_weight) is not int or self.min_train_weight <= 0:
            raise ValueError("calibration policy min_train_weight must be a positive integer")
        if not isinstance(self.beta_bounds, (tuple, list)) or len(self.beta_bounds) != 2:
            raise ValueError("calibration policy beta_bounds must be a 2-tuple")
        if not all(_finite_number(value) for value in self.beta_bounds):
            raise ValueError("calibration policy beta_bounds must be finite numbers")
        beta_lo, beta_hi = map(float, self.beta_bounds)
        if not 0 <= beta_lo <= beta_hi <= 1:
            raise ValueError("calibration policy beta_bounds are invalid")
        if not _finite_number(self.logit_clip) or float(self.logit_clip) <= 0:
            raise ValueError("calibration policy logit_clip must be finite and positive")
        if not isinstance(self.probability_clip, (tuple, list)) or len(self.probability_clip) != 2:
            raise ValueError("calibration policy probability_clip must be a 2-tuple")
        if not all(_finite_number(value) for value in self.probability_clip):
            raise ValueError("calibration policy probability_clip must be finite numbers")
        probability_lo, probability_hi = map(float, self.probability_clip)
        if not 0 < probability_lo < probability_hi < 1:
            raise ValueError("calibration policy probability_clip is invalid")
        if not _finite_number(self.refit_seconds) or float(self.refit_seconds) <= 0:
            raise ValueError("calibration policy refit_seconds must be finite and positive")
        object.__setattr__(self, "lambda_", float(self.lambda_))
        object.__setattr__(self, "beta_bounds", (beta_lo, beta_hi))
        object.__setattr__(self, "logit_clip", float(self.logit_clip))
        object.__setattr__(self, "probability_clip", (probability_lo, probability_hi))
        object.__setattr__(self, "refit_seconds", float(self.refit_seconds))

    def as_payload(self) -> dict[str, object]:
        """Return a detached, JSON-compatible, content-addressed descriptor."""

        descriptor: dict[str, object] = {
            "type": self._TYPE,
            "version": self._VERSION,
            "algorithm_revision": self.algorithm_revision,
            "input_revision": self.input_revision,
            "metric_pooling": self.metric_pooling,
            "lead_calendar_revision": self.lead_calendar_revision,
            "lambda_": self.lambda_,
            "min_train_weight": self.min_train_weight,
            "beta_bounds": list(self.beta_bounds),
            "logit_clip": self.logit_clip,
            "probability_clip": list(self.probability_clip),
            "refit_seconds": self.refit_seconds,
        }
        descriptor["policy_hash"] = self._hash_descriptor(descriptor)
        return descriptor

    @staticmethod
    def _hash_descriptor(descriptor: dict[str, object]) -> str:
        from src.decision_kernel.canonicalization import stable_hash

        return stable_hash(descriptor)

    @classmethod
    def from_payload(cls, payload: object) -> "CalibrationPolicySpec":
        if not isinstance(payload, dict):
            raise ValueError("calibration policy payload must be an object")
        expected = {
            "type", "version", "algorithm_revision", "input_revision",
            "metric_pooling", "lead_calendar_revision", "lambda_",
            "min_train_weight", "beta_bounds", "logit_clip", "probability_clip",
            "refit_seconds", "policy_hash",
        }
        if set(payload) != expected:
            raise ValueError("calibration policy payload fields are not exact")
        if payload.get("type") != cls._TYPE or type(payload.get("version")) is not int or payload["version"] != cls._VERSION:
            raise ValueError("calibration policy payload type or version is invalid")
        policy_hash = payload.get("policy_hash")
        if not isinstance(policy_hash, str) or policy_hash != cls._hash_descriptor(
            {key: payload[key] for key in expected if key != "policy_hash"}
        ):
            raise ValueError("calibration policy hash is invalid")
        if type(payload.get("min_train_weight")) is not int:
            raise ValueError("calibration policy min_train_weight type is invalid")
        for name in ("lambda_", "logit_clip", "refit_seconds"):
            if not _finite_number(payload.get(name)):
                raise ValueError(f"calibration policy {name} type is invalid")
        for name in ("beta_bounds", "probability_clip"):
            value = payload.get(name)
            if (
                not isinstance(value, list)
                or len(value) != 2
                or any(type(item) not in (int, float) for item in value)
            ):
                raise ValueError(f"calibration policy {name} type is invalid")
        candidate = cls(
            algorithm_revision=payload["algorithm_revision"],
            input_revision=payload["input_revision"],
            metric_pooling=payload["metric_pooling"],
            lead_calendar_revision=payload["lead_calendar_revision"],
            lambda_=payload["lambda_"],
            min_train_weight=payload["min_train_weight"],
            beta_bounds=tuple(payload["beta_bounds"]),
            logit_clip=payload["logit_clip"],
            probability_clip=tuple(payload["probability_clip"]),
            refit_seconds=payload["refit_seconds"],
        )
        if candidate.as_payload()["policy_hash"] != policy_hash:
            raise ValueError("calibration policy payload is not canonically encoded")
        return candidate


@dataclass(frozen=True)
class PayoffQCorrection:
    """One candidate's market-anchored correction, sealed at solve time.

    ``raw_q`` and ``corrected_q`` are both in the HELD-TOKEN space — the
    probability that the candidate's own token pays — which is the space the
    solver sizes in and the certificate asserts on. ``p0`` is the decision-time
    gross native fill price of that same token, i.e. the market's implied
    probability it pays, and is the anchor the correction shrinks toward. Fees
    belong to the economic cost curve.

    The remaining fields are provenance for settlement attribution to later
    grade corrected-versus-raw decisions; nothing downstream computes from them.
    """

    family_key: str
    bin_id: str
    side: str
    token_id: str
    raw_q: float
    corrected_q: float
    p0: float
    lead_bucket: str
    alpha_lead: float
    beta: float
    lambda_: float
    training_cutoff: str
    n_train: int
    param_hash: str
    calibration_policy: CalibrationPolicySpec | None = None
    fit_scope: CalibrationFitScope | None = None

    def __post_init__(self) -> None:
        if not all(
            str(value).strip()
            for value in (
                self.family_key,
                self.bin_id,
                self.side,
                self.token_id,
                self.lead_bucket,
                self.training_cutoff,
                self.param_hash,
            )
        ):
            raise ValueError("payoff q correction requires complete identity")
        if self.side not in {"YES", "NO"}:
            raise ValueError("payoff q correction side must be YES or NO")
        if not all(
            math.isfinite(value) and 0.0 <= value <= 1.0
            for value in (self.raw_q, self.corrected_q, self.p0)
        ):
            raise ValueError("payoff q correction probabilities must lie in [0, 1]")
        if not all(
            math.isfinite(value)
            for value in (self.alpha_lead, self.beta, self.lambda_)
        ):
            raise ValueError("payoff q correction parameters must be finite")
        if self.n_train < 0:
            raise ValueError("payoff q correction training count is invalid")
        if self.calibration_policy is not None and not isinstance(
            self.calibration_policy, CalibrationPolicySpec
        ):
            raise TypeError("payoff q correction calibration policy is invalid")
        if self.fit_scope is not None and not isinstance(
            self.fit_scope, CalibrationFitScope
        ):
            raise TypeError("payoff q correction fit scope is invalid")

    def matches(
        self, *, family_key: str, bin_id: str, side: str, token_id: str
    ) -> bool:
        """True when this record was sealed for exactly this candidate leg."""

        return (
            self.family_key == family_key
            and self.bin_id == bin_id
            and self.side == side
            and self.token_id == token_id
        )

    def as_cert_fields(self) -> dict[str, object]:
        """Provenance block stamped onto the qkernel economics certificate."""

        fields = {
            "applied": True,
            "q_raw": float(self.raw_q),
            "q_corrected": float(self.corrected_q),
            "p0": float(self.p0),
            "p0_basis": "GROSS_NATIVE_TOKEN_PRICE",
            "lead_bucket": self.lead_bucket,
            "alpha_lead": float(self.alpha_lead),
            "beta": float(self.beta),
            "lambda": float(self.lambda_),
            "training_cutoff": self.training_cutoff,
            "n_train": int(self.n_train),
            "param_hash": self.param_hash,
        }
        if self.calibration_policy is not None:
            fields["calibration_policy"] = self.calibration_policy.as_payload()
        if self.fit_scope is not None:
            fields["fit_scope"] = self.fit_scope.as_payload()
        return fields
