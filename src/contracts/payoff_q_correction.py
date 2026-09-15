# Created: 2026-08-27
# Last reused or audited: 2026-09-15
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
from datetime import datetime, timezone
from decimal import Decimal


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

    def current_buy_price_anchor(
        self, *, best_bid: object, best_ask: object, min_tick: object,
    ) -> float:
        """Recreate this ENTRY policy's price feature from one current book.

        This is a probability input, not permission to BUY or a SELL proceeds
        quote. Order price bands, size, capacity and risk remain execution law.
        """
        def number(value: object, field: str) -> Decimal:
            if type(value) not in (int, float, Decimal):
                raise PayoffQCorrectionUnavailable(f"ENTRY_PRICE_ANCHOR_INVALID:{field}")
            result = Decimal(str(value))
            if not result.is_finite():
                raise PayoffQCorrectionUnavailable(f"ENTRY_PRICE_ANCHOR_INVALID:{field}")
            return result

        ask = number(best_ask, "ask")
        if not Decimal("0") < ask < Decimal("1"):
            raise PayoffQCorrectionUnavailable("ENTRY_PRICE_ANCHOR_INVALID:ask")
        if self.execution_mode == "TAKER_LIMIT":
            return float(ask)
        bid = number(best_bid, "bid")
        tick = number(min_tick, "tick")
        if not Decimal("0") < bid < Decimal("1") or tick <= 0:
            raise PayoffQCorrectionUnavailable("ENTRY_PRICE_ANCHOR_INVALID:bid_or_tick")
        try:
            price = bid + tick
            if price >= ask or bid % tick != 0:
                raise PayoffQCorrectionUnavailable("ENTRY_PRICE_ANCHOR_INVALID:passive_price")
        except ArithmeticError as exc:
            raise PayoffQCorrectionUnavailable("ENTRY_PRICE_ANCHOR_INVALID:passive_price") from exc
        return float(price)

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
class CanonicalTrainingManifest:
    """Constant-size commitment to the canonical rows used by one fit."""

    scope_hash: str
    corpus_revision: str
    training_cutoff: str
    row_count: int
    event_count: int
    weight_sum: float
    max_fill_available_at: str
    max_label_available_at: str
    availability_upper_bound: str
    input_hash: str
    manifest_hash: str

    _TYPE = "CanonicalTrainingManifest"
    _VERSION = 1

    @staticmethod
    def _timestamp(value: object, *, name: str) -> datetime:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"canonical training manifest {name} is invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"canonical training manifest {name} is invalid") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"canonical training manifest {name} is invalid")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _finite(value: object) -> bool:
        return type(value) in (int, float) and math.isfinite(float(value))

    @staticmethod
    def _hash_value(value: object) -> bool:
        if not isinstance(value, str) or len(value) != 64:
            return False
        return all(character in "0123456789abcdef" for character in value)

    def __post_init__(self) -> None:
        if (
            not self._hash_value(self.scope_hash)
            or not isinstance(self.corpus_revision, str)
            or not self.corpus_revision.strip()
            or not self._hash_value(self.input_hash)
            or not self._hash_value(self.manifest_hash)
        ):
            raise ValueError("canonical training manifest identity is invalid")
        cutoff = self._timestamp(self.training_cutoff, name="training_cutoff")
        fill_at = self._timestamp(self.max_fill_available_at, name="max_fill_available_at")
        label_at = self._timestamp(self.max_label_available_at, name="max_label_available_at")
        upper = self._timestamp(self.availability_upper_bound, name="availability_upper_bound")
        if max(fill_at, label_at) != upper or upper >= cutoff:
            raise ValueError("canonical training manifest availability is invalid")
        if type(self.row_count) is not int or self.row_count <= 0:
            raise ValueError("canonical training manifest row_count is invalid")
        if type(self.event_count) is not int or self.event_count <= 0 or self.event_count > self.row_count:
            raise ValueError("canonical training manifest event_count is invalid")
        if not self._finite(self.weight_sum) or self.weight_sum <= 0:
            raise ValueError("canonical training manifest weight_sum is invalid")
        object.__setattr__(self, "training_cutoff", cutoff.isoformat().replace("+00:00", "Z"))
        object.__setattr__(self, "max_fill_available_at", fill_at.isoformat().replace("+00:00", "Z"))
        object.__setattr__(self, "max_label_available_at", label_at.isoformat().replace("+00:00", "Z"))
        object.__setattr__(self, "availability_upper_bound", upper.isoformat().replace("+00:00", "Z"))
        body = {
            "type": self._TYPE,
            "version": self._VERSION,
            "scope_hash": self.scope_hash,
            "corpus_revision": self.corpus_revision,
            "training_cutoff": self.training_cutoff,
            "row_count": self.row_count,
            "event_count": self.event_count,
            "weight_sum": self.weight_sum,
            "max_fill_available_at": self.max_fill_available_at,
            "max_label_available_at": self.max_label_available_at,
            "availability_upper_bound": self.availability_upper_bound,
            "input_hash": self.input_hash,
        }
        if self.manifest_hash != self._hash(body):
            raise ValueError("canonical training manifest hash is invalid")

    def as_payload(self) -> dict[str, object]:
        return {
            "type": self._TYPE,
            "version": self._VERSION,
            "scope_hash": self.scope_hash,
            "corpus_revision": self.corpus_revision,
            "training_cutoff": self.training_cutoff,
            "row_count": self.row_count,
            "event_count": self.event_count,
            "weight_sum": self.weight_sum,
            "max_fill_available_at": self.max_fill_available_at,
            "max_label_available_at": self.max_label_available_at,
            "availability_upper_bound": self.availability_upper_bound,
            "input_hash": self.input_hash,
            "manifest_hash": self.manifest_hash,
        }

    @classmethod
    def build(
        cls, *, scope_hash: str, corpus_revision: str, training_cutoff: str,
        row_count: int, event_count: int, weight_sum: float,
        max_fill_available_at: str, max_label_available_at: str, input_hash: str,
    ) -> "CanonicalTrainingManifest":
        cutoff = cls._timestamp(training_cutoff, name="training_cutoff")
        fill_at = cls._timestamp(max_fill_available_at, name="max_fill_available_at")
        label_at = cls._timestamp(max_label_available_at, name="max_label_available_at")
        upper = max(fill_at, label_at)
        body = {
            "type": cls._TYPE,
            "version": cls._VERSION,
            "scope_hash": scope_hash,
            "corpus_revision": corpus_revision,
            "training_cutoff": cutoff.isoformat().replace("+00:00", "Z"),
            "row_count": row_count,
            "event_count": event_count,
            "weight_sum": weight_sum,
            "max_fill_available_at": fill_at.isoformat().replace("+00:00", "Z"),
            "max_label_available_at": label_at.isoformat().replace("+00:00", "Z"),
            "availability_upper_bound": upper.isoformat().replace("+00:00", "Z"),
            "input_hash": input_hash,
        }
        return cls(
            scope_hash=scope_hash,
            corpus_revision=corpus_revision,
            training_cutoff=body["training_cutoff"],
            row_count=row_count,
            event_count=event_count,
            weight_sum=weight_sum,
            max_fill_available_at=body["max_fill_available_at"],
            max_label_available_at=body["max_label_available_at"],
            availability_upper_bound=body["availability_upper_bound"],
            input_hash=input_hash,
            manifest_hash=cls._hash(body),
        )

    @staticmethod
    def _hash(payload: dict[str, object]) -> str:
        from src.decision_kernel.canonicalization import stable_hash
        return stable_hash(payload)

    @classmethod
    def from_payload(cls, payload: object) -> "CanonicalTrainingManifest":
        if not isinstance(payload, dict):
            raise ValueError("canonical training manifest payload must be an object")
        expected = {
            "type", "version", "scope_hash", "corpus_revision", "training_cutoff",
            "row_count", "event_count", "weight_sum", "max_fill_available_at",
            "max_label_available_at", "availability_upper_bound", "input_hash", "manifest_hash",
        }
        if (
            set(payload) != expected
            or payload.get("type") != cls._TYPE
            or type(payload.get("version")) is not int
            or payload.get("version") != cls._VERSION
        ):
            raise ValueError("canonical training manifest payload fields are invalid")
        manifest_hash = payload.get("manifest_hash")
        body = {key: payload[key] for key in expected if key != "manifest_hash"}
        if not isinstance(manifest_hash, str) or manifest_hash != cls._hash(body):
            raise ValueError("canonical training manifest hash is invalid")
        manifest = cls(
            scope_hash=payload["scope_hash"], corpus_revision=payload["corpus_revision"],
            training_cutoff=payload["training_cutoff"], row_count=payload["row_count"],
            event_count=payload["event_count"], weight_sum=payload["weight_sum"],
            max_fill_available_at=payload["max_fill_available_at"],
            max_label_available_at=payload["max_label_available_at"],
            availability_upper_bound=payload["availability_upper_bound"],
            input_hash=payload["input_hash"], manifest_hash=manifest_hash,
        )
        if manifest.as_payload() != payload:
            raise ValueError("canonical training manifest payload is not canonical")
        return manifest


@dataclass(frozen=True)
class PayoffQCorrection:
    """One candidate's market-anchored correction, sealed at solve time.

    ``raw_q`` and ``corrected_q`` are both in the HELD-TOKEN space — the
    probability that the candidate's own token pays — which is the space the
    solver sizes in and the certificate asserts on. ``p0`` is the decision-time
    gross BUY price feature of that same token under the ENTRY policy. Held
    redecision recreates that feature; SELL proceeds remain separate. Fees
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
    training_manifest: CanonicalTrainingManifest | None = None

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
        if self.training_manifest is not None and not isinstance(
            self.training_manifest, CanonicalTrainingManifest
        ):
            raise TypeError("payoff q correction training manifest is invalid")
        if self.training_manifest is not None:
            if self.fit_scope is None:
                raise ValueError("payoff q correction training manifest requires fit scope")
            if self.training_manifest.scope_hash != self.fit_scope.as_payload()["scope_hash"]:
                raise ValueError("payoff q correction training manifest scope is unbound")
            try:
                correction_cutoff = CanonicalTrainingManifest._timestamp(
                    self.training_cutoff, name="training_cutoff"
                )
                manifest_cutoff = CanonicalTrainingManifest._timestamp(
                    self.training_manifest.training_cutoff, name="training_cutoff"
                )
            except ValueError as exc:
                raise ValueError("payoff q correction training manifest cutoff is invalid") from exc
            if correction_cutoff != manifest_cutoff or self.n_train != self.training_manifest.row_count:
                raise ValueError("payoff q correction training manifest does not match correction")

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
        if self.training_manifest is not None:
            fields["training_manifest"] = self.training_manifest.as_payload()
        return fields
