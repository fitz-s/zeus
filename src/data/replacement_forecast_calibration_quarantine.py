"""Calibration quarantine checks for replacement forecast products.

Replacement forecast evidence starts shadow-only. This checker prevents B0
OpenData/TIGGE/period-extrema calibration, raw_honest fallback, and sigma floors
from being served as authority for Open-Meteo ECMWF IFS 9km plus AIFS sampled-2t
derived posteriors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


REPLACEMENT_SOURCE_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
REPLACEMENT_PRODUCT_ID = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1"
AIFS_SOURCE_ID = "ecmwf_aifs_ens"
AIFS_PRODUCT_ID = "ecmwf_aifs_ens_sampled_2t_6h_v1"
OPENMETEO_ANCHOR_SOURCE_ID = "openmeteo_ecmwf_ifs_9km"
OPENMETEO_ANCHOR_PRODUCT_ID = "openmeteo_ecmwf_ifs9_deterministic_anchor_v1"
SHADOW_ALLOWED_STATUS = "SHADOW_PRODUCT_SPECIFIC_ALLOWED"
BLOCKED_STATUS = "BLOCKED"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"
_REPLACEMENT_PRODUCT_IDS = {REPLACEMENT_PRODUCT_ID, AIFS_PRODUCT_ID, OPENMETEO_ANCHOR_PRODUCT_ID}
_REPLACEMENT_SOURCE_IDS = {REPLACEMENT_SOURCE_ID, AIFS_SOURCE_ID, OPENMETEO_ANCHOR_SOURCE_ID}
_B0_LINEAGE_TOKENS = ("ecmwf_open_data", "opendata", "tigge", "ifs_ens_0p25", "mx2t3", "mn2t3", "mx2t6", "mn2t6")
_B0_METHODS = {"platt", "extended_platt", "emos", "raw_honest", "sigma_floor", "b0_sigma_floor"}
_ALLOWED_SHADOW_AUTHORITY = "SHADOW_PRODUCT_SPECIFIC"


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full replacement identity")


def _text(value: object, *, field_name: str) -> str:
    text = str(value or "")
    if not text:
        raise ValueError(f"{field_name} is required")
    _reject_alias(text, field_name=field_name)
    return text


@dataclass(frozen=True)
class ReplacementForecastCalibrationRequest:
    target_source_id: str
    target_product_id: str
    target_data_version: str
    calibration_source_id: str
    calibration_product_id: str
    calibration_data_version: str
    calibration_method: str
    calibration_authority: str
    training_allowed: bool = False
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "target_source_id",
            "target_product_id",
            "target_data_version",
            "calibration_source_id",
            "calibration_product_id",
            "calibration_data_version",
            "calibration_method",
            "calibration_authority",
        ):
            _text(getattr(self, field_name), field_name=field_name)
        if not isinstance(self.training_allowed, bool):
            raise TypeError("training_allowed must be bool")


@dataclass(frozen=True)
class ReplacementForecastCalibrationDecision:
    status: str
    reason_codes: tuple[str, ...]
    target_source_id: str
    target_product_id: str
    calibration_source_id: str
    calibration_product_id: str
    calibration_method: str
    training_allowed: bool

    @property
    def allowed(self) -> bool:
        return self.status == SHADOW_ALLOWED_STATUS

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "target_source_id": self.target_source_id,
            "target_product_id": self.target_product_id,
            "calibration_source_id": self.calibration_source_id,
            "calibration_product_id": self.calibration_product_id,
            "calibration_method": self.calibration_method,
            "training_allowed": self.training_allowed,
            "allowed": self.allowed,
        }


def _contains_b0_lineage(*values: str) -> bool:
    joined = "|".join(value.lower() for value in values)
    return any(token in joined for token in _B0_LINEAGE_TOKENS)


def evaluate_replacement_forecast_calibration_quarantine(
    request: ReplacementForecastCalibrationRequest,
) -> ReplacementForecastCalibrationDecision:
    """Return whether a calibration artifact may serve replacement evidence."""

    if not isinstance(request, ReplacementForecastCalibrationRequest):
        raise TypeError("request must be ReplacementForecastCalibrationRequest")
    reasons: list[str] = []
    target_is_replacement = request.target_source_id in _REPLACEMENT_SOURCE_IDS or request.target_product_id in _REPLACEMENT_PRODUCT_IDS
    if not target_is_replacement:
        reasons.append("REPLACEMENT_CALIBRATION_TARGET_NOT_REPLACEMENT_PRODUCT")
    if request.training_allowed:
        reasons.append("REPLACEMENT_CALIBRATION_TRAINING_AUTHORITY_FORBIDDEN")
    method = request.calibration_method.lower()
    if method in _B0_METHODS:
        reasons.append("REPLACEMENT_CALIBRATION_METHOD_REUSES_BASELINE_AUTHORITY")
    if _contains_b0_lineage(
        request.calibration_source_id,
        request.calibration_product_id,
        request.calibration_data_version,
        request.calibration_method,
    ):
        reasons.append("REPLACEMENT_CALIBRATION_BASELINE_LINEAGE_FORBIDDEN")
    if request.calibration_source_id != request.target_source_id or request.calibration_product_id != request.target_product_id:
        reasons.append("REPLACEMENT_CALIBRATION_PRODUCT_IDENTITY_MISMATCH")
    if request.calibration_authority != _ALLOWED_SHADOW_AUTHORITY:
        reasons.append("REPLACEMENT_CALIBRATION_AUTHORITY_NOT_SHADOW_PRODUCT_SPECIFIC")
    if "sampled_2t" in request.target_data_version and any(token in request.calibration_data_version.lower() for token in ("mx2t", "mn2t", "period_extrema")):
        reasons.append("REPLACEMENT_CALIBRATION_SAMPLED_2T_CANNOT_USE_PERIOD_EXTREMA")
    status = BLOCKED_STATUS if reasons else SHADOW_ALLOWED_STATUS
    return ReplacementForecastCalibrationDecision(
        status=status,
        reason_codes=tuple(reasons or ("REPLACEMENT_CALIBRATION_SHADOW_PRODUCT_SPECIFIC",)),
        target_source_id=request.target_source_id,
        target_product_id=request.target_product_id,
        calibration_source_id=request.calibration_source_id,
        calibration_product_id=request.calibration_product_id,
        calibration_method=request.calibration_method,
        training_allowed=request.training_allowed,
    )
