"""Replacement forecast EMOS identity verifier."""

from __future__ import annotations

from dataclasses import dataclass


REPLACEMENT_EMOS_KEY_SCHEMA = "replacement_product_keyed_v1"
LEGACY_EMOS_KEY_SCHEMA = "legacy_city_season_metric"
READY_STATUS = "REPLACEMENT_EMOS_PRODUCT_IDENTITY_READY"
BLOCKED_STATUS = "BLOCKED"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full replacement identity")


def replacement_emos_cell_key(
    *,
    city: str,
    season: str,
    metric: str,
    source_family: str,
    source_id: str,
    product_id: str,
    data_version: str,
) -> str:
    """Return the replacement-only EMOS key including product lineage."""

    parts = (city, season, metric, source_family, source_id, product_id, data_version)
    cleaned: list[str] = []
    for index, part in enumerate(parts):
        text = str(part or "").strip()
        if not text:
            raise ValueError(f"replacement EMOS key part {index} is required")
        _reject_alias(text, field_name="replacement_emos_cell_key")
        cleaned.append(text)
    return "|".join(cleaned)


@dataclass(frozen=True)
class ReplacementForecastEmosIdentityEvidence:
    cell_key: str
    key_schema: str
    city: str
    season: str
    metric: str
    source_family: str
    source_id: str
    product_id: str
    data_version: str
    calibration_method: str

    def __post_init__(self) -> None:
        for field_name in (
            "cell_key",
            "key_schema",
            "city",
            "season",
            "metric",
            "source_family",
            "source_id",
            "product_id",
            "data_version",
            "calibration_method",
        ):
            value = str(getattr(self, field_name) or "")
            if not value:
                raise ValueError(f"{field_name} is required")
            _reject_alias(value, field_name=field_name)
        if self.metric not in {"high", "low"}:
            raise ValueError("metric must be high or low")


@dataclass(frozen=True)
class ReplacementForecastEmosIdentityDecision:
    status: str
    reason_codes: tuple[str, ...]
    key_schema: str
    cell_key: str
    product_keyed: bool

    @property
    def ready(self) -> bool:
        return self.status == READY_STATUS

    def as_refit_fields(self) -> dict[str, object]:
        return {
            "emos_key_includes_product": self.product_keyed,
            "emos_key_schema": self.key_schema,
            "emos_identity_evidence_status": self.status,
        }


def evaluate_replacement_forecast_emos_identity(
    evidence: ReplacementForecastEmosIdentityEvidence,
) -> ReplacementForecastEmosIdentityDecision:
    """Return whether an EMOS cell identity is safe for replacement refit."""

    if not isinstance(evidence, ReplacementForecastEmosIdentityEvidence):
        raise TypeError("evidence must be ReplacementForecastEmosIdentityEvidence")
    reasons: list[str] = []
    expected_key = replacement_emos_cell_key(
        city=evidence.city,
        season=evidence.season,
        metric=evidence.metric,
        source_family=evidence.source_family,
        source_id=evidence.source_id,
        product_id=evidence.product_id,
        data_version=evidence.data_version,
    )
    if evidence.key_schema != REPLACEMENT_EMOS_KEY_SCHEMA:
        reasons.append("REPLACEMENT_EMOS_KEY_SCHEMA_NOT_PRODUCT_KEYED")
    if evidence.cell_key != expected_key:
        reasons.append("REPLACEMENT_EMOS_CELL_KEY_DOES_NOT_MATCH_PRODUCT_DOMAIN")
    if evidence.cell_key.count("|") < 6:
        reasons.append("REPLACEMENT_EMOS_CELL_KEY_MISSING_PRODUCT_PARTS")
    if evidence.calibration_method.lower() in {"emos", "raw_honest", "sigma_floor", "platt", "extended_platt"}:
        reasons.append("REPLACEMENT_EMOS_BASELINE_METHOD_FORBIDDEN")
    product_keyed = not reasons
    return ReplacementForecastEmosIdentityDecision(
        status=READY_STATUS if product_keyed else BLOCKED_STATUS,
        reason_codes=tuple(reasons or ("REPLACEMENT_EMOS_PRODUCT_KEYED_CELL_READY",)),
        key_schema=evidence.key_schema,
        cell_key=evidence.cell_key,
        product_keyed=product_keyed,
    )
