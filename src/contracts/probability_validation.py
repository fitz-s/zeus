# Created: 2026-09-25
# Last reused or audited: 2026-09-25
# Authority basis: operator directive 2026-09-24 (current-recipe replay, external
#   review) — deployment contract for a probability recipe validated on replay
#   evidence. Not consumed by live entry; the entry seam switch follows a
#   validation table.
"""Deployment contract for a probability recipe validated on replay evidence.

``ProbabilityValidationCertificate`` binds one evaluation recipe to the exact
settlement contracts, execution population, feature ranges and training policy
it was validated on, plus the validation results and a verdict.

``ValidatedIdentity`` is the correction policy such a certificate authorizes
when validation shows raw q needs no transform. It differs from
``SourceIdentityBaseline`` in kind: the baseline acts on raw q because the fit
corpus was too small, and claims nothing; this policy claims validation
evidence and cannot exist without a covering certificate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping

from src.contracts.payoff_q_correction import (
    CalibrationFitScope,
    CalibrationPolicySpec,
    CanonicalTrainingManifest,
)

EXECUTED_ORDER = "EXECUTED_ORDER"
SETTLEMENT_STATE = "SETTLEMENT_STATE"
POPULATIONS = frozenset({EXECUTED_ORDER, SETTLEMENT_STATE})
IDENTITY_VALIDATED = "IDENTITY_VALIDATED"
VERDICTS = frozenset({IDENTITY_VALIDATED, "CORRECTION_REQUIRED", "INSUFFICIENT_EVIDENCE"})


def _hash(payload: dict[str, object]) -> str:
    from src.decision_kernel.canonicalization import stable_hash

    return stable_hash(payload)


def _finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


@dataclass(frozen=True)
class ProbabilityValidationCertificate:
    """One recipe's validated scope; every field is part of its identity."""

    recipe_id: str
    fit_scope: CalibrationFitScope
    settlement_contracts: tuple[str, ...]
    execution_population: str
    feature_ranges: tuple[tuple[str, float, float], ...]
    training_policy: CalibrationPolicySpec
    training_manifest: CanonicalTrainingManifest
    validation_results: tuple[tuple[str, float], ...]
    verdict: str

    _TYPE = "ProbabilityValidationCertificate"
    _VERSION = 1

    def __post_init__(self) -> None:
        if not isinstance(self.fit_scope, CalibrationFitScope):
            raise TypeError("validation certificate fit_scope is invalid")
        if not isinstance(self.training_policy, CalibrationPolicySpec):
            raise TypeError("validation certificate training_policy is invalid")
        if not isinstance(self.training_manifest, CanonicalTrainingManifest):
            raise TypeError("validation certificate training_manifest is invalid")
        if (
            not isinstance(self.recipe_id, str)
            or not self.recipe_id.strip()
            or self.fit_scope.raw_probability_revision != self.recipe_id
        ):
            raise ValueError("validation certificate recipe is not the fit scope recipe")
        if self.training_manifest.scope_hash != self.fit_scope.as_payload()["scope_hash"]:
            raise ValueError("validation certificate manifest is not bound to its scope")
        contracts = tuple(self.settlement_contracts)
        if (
            not contracts
            or any(not isinstance(item, str) or not item.strip() for item in contracts)
            or contracts != tuple(sorted(set(contracts)))
        ):
            raise ValueError("validation certificate settlement contracts are invalid")
        if self.execution_population not in POPULATIONS:
            raise ValueError("validation certificate execution population is invalid")
        names = [name for name, *_ in self.feature_ranges]
        if (
            not names
            or len(set(names)) != len(names)
            or any(
                not isinstance(name, str) or not name
                or not _finite(low) or not _finite(high) or float(low) > float(high)
                for name, low, high in self.feature_ranges
            )
        ):
            raise ValueError("validation certificate feature ranges are invalid")
        if not self.validation_results or any(
            not isinstance(name, str) or not name or not _finite(value)
            for name, value in self.validation_results
        ):
            raise ValueError("validation certificate results are invalid")
        if self.verdict not in VERDICTS:
            raise ValueError("validation certificate verdict is invalid")
        object.__setattr__(self, "settlement_contracts", contracts)
        object.__setattr__(self, "feature_ranges", tuple(
            (name, float(low), float(high)) for name, low, high in self.feature_ranges
        ))
        object.__setattr__(self, "validation_results", tuple(
            (name, float(value)) for name, value in self.validation_results
        ))

    def covers(
        self, *, scope: CalibrationFitScope, settlement_contract_id: str,
        features: Mapping[str, float],
    ) -> bool:
        """True only inside the exact scope, contracts and validated ranges."""

        ranges = {name: (low, high) for name, low, high in self.feature_ranges}
        return (
            scope == self.fit_scope
            and settlement_contract_id in self.settlement_contracts
            and set(features) == set(ranges)
            and all(
                _finite(value) and ranges[name][0] <= float(value) <= ranges[name][1]
                for name, value in features.items()
            )
        )

    def as_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self._TYPE,
            "version": self._VERSION,
            "recipe_id": self.recipe_id,
            "fit_scope": self.fit_scope.as_payload(),
            "settlement_contracts": list(self.settlement_contracts),
            "execution_population": self.execution_population,
            "feature_ranges": [list(item) for item in self.feature_ranges],
            "training_policy": self.training_policy.as_payload(),
            "training_manifest": self.training_manifest.as_payload(),
            "validation_results": [list(item) for item in self.validation_results],
            "verdict": self.verdict,
        }
        payload["certificate_hash"] = _hash(payload)
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "ProbabilityValidationCertificate":
        if not isinstance(payload, dict):
            raise ValueError("validation certificate payload must be an object")
        unsigned = {key: value for key, value in payload.items() if key != "certificate_hash"}
        if (
            payload.get("type") != cls._TYPE
            or payload.get("version") != cls._VERSION
            or payload.get("certificate_hash") != _hash(unsigned)
        ):
            raise ValueError("validation certificate payload identity is invalid")
        candidate = cls(
            recipe_id=payload["recipe_id"],
            fit_scope=CalibrationFitScope.from_payload(payload["fit_scope"]),
            settlement_contracts=tuple(payload["settlement_contracts"]),
            execution_population=payload["execution_population"],
            feature_ranges=tuple(tuple(item) for item in payload["feature_ranges"]),
            training_policy=CalibrationPolicySpec.from_payload(payload["training_policy"]),
            training_manifest=CanonicalTrainingManifest.from_payload(payload["training_manifest"]),
            validation_results=tuple(tuple(item) for item in payload["validation_results"]),
            verdict=payload["verdict"],
        )
        if candidate.as_payload() != payload:
            raise ValueError("validation certificate payload is not canonical")
        return candidate


@dataclass(frozen=True)
class ValidatedIdentity:
    """Raw q acts unchanged because a covering certificate validated that."""

    family_key: str
    bin_id: str
    side: str
    token_id: str
    raw_q: float
    p0: float
    settlement_contract_id: str
    certificate: ProbabilityValidationCertificate

    _TYPE = "ValidatedIdentity"
    _VERSION = 1
    _POLICY = "VALIDATED_IDENTITY_V1"

    def __post_init__(self) -> None:
        if self.side not in {"YES", "NO"}:
            raise ValueError("validated identity side must be YES or NO")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.family_key, self.bin_id, self.token_id, self.settlement_contract_id)
        ):
            raise ValueError("validated identity requires complete identity")
        if not all(_finite(value) and 0.0 <= float(value) <= 1.0 for value in (self.raw_q, self.p0)):
            raise ValueError("validated identity probabilities must lie in [0, 1]")
        if not isinstance(self.certificate, ProbabilityValidationCertificate):
            raise TypeError("validated identity requires a validation certificate")
        if self.certificate.verdict != IDENTITY_VALIDATED:
            raise ValueError("validation certificate did not validate identity")
        if not self.certificate.covers(
            scope=self.certificate.fit_scope,
            settlement_contract_id=self.settlement_contract_id,
            features={"q_raw": float(self.raw_q), "p0": float(self.p0)},
        ):
            raise ValueError("validation certificate does not cover this candidate")
        object.__setattr__(self, "raw_q", float(self.raw_q))
        object.__setattr__(self, "p0", float(self.p0))

    @property
    def corrected_q(self) -> float:
        return self.raw_q

    @property
    def fit_scope(self) -> CalibrationFitScope:
        return self.certificate.fit_scope

    def matches(self, *, family_key: str, bin_id: str, side: str, token_id: str) -> bool:
        return (
            self.family_key == family_key and self.bin_id == bin_id
            and self.side == side and self.token_id == token_id
        )

    def as_cert_fields(self) -> dict[str, object]:
        return {
            "type": self._TYPE,
            "version": self._VERSION,
            "policy": self._POLICY,
            "applied": False,
            "family_key": self.family_key,
            "bin_id": self.bin_id,
            "side": self.side,
            "token_id": self.token_id,
            "q_raw": self.raw_q,
            "q_corrected": self.corrected_q,
            "p0": self.p0,
            "settlement_contract_id": self.settlement_contract_id,
            "fit_scope": self.fit_scope.as_payload(),
            "validation_certificate_hash": self.certificate.as_payload()["certificate_hash"],
        }
