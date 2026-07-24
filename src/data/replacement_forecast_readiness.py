"""Dependency readiness for the replacement forecast soft-anchor posterior."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Mapping, Sequence

from src.data.replacement_forecast_source_run_identity import (
    expected_replacement_dependency_identity_by_role,
)


STRATEGY_KEY = "openmeteo_ecmwf_ifs9_bayes_fusion"
PRODUCT_ID = "openmeteo_ecmwf_ifs9_bayes_fusion_v1"
SOURCE_ID = "openmeteo_ecmwf_ifs9_bayes_fusion"
HIGH_DATA_VERSION = "openmeteo_ecmwf_ifs9_bayes_fusion_high_v1"
LOW_DATA_VERSION = "openmeteo_ecmwf_ifs9_bayes_fusion_low_v1"
LIVE_RUNTIME_LAYER = "live"
READY_STATUS = "READY"
BLOCKED_STATUS = "BLOCKED"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


def normalize_replacement_readiness_status(status: str) -> str:
    return status


def latest_replacement_readiness(
    conn: sqlite3.Connection,
    *,
    city: str,
    target_date: str,
    temperature_metric: str,
    decision_time: datetime,
) -> "ReplacementForecastReadinessDecision | None":
    """Read the newest causal readiness certificate for one live family."""

    metric = str(temperature_metric).strip().lower()
    if metric not in {"high", "low"}:
        return None
    data_version = HIGH_DATA_VERSION if metric == "high" else LOW_DATA_VERSION
    row = conn.execute(
        """
        SELECT *
        FROM readiness_state
        WHERE scope_type = 'strategy'
          AND strategy_key = ?
          AND source_id = ?
          AND data_version = ?
          AND city = ?
          AND target_local_date = ?
          AND temperature_metric = ?
          AND julianday(computed_at) <= julianday(?)
        ORDER BY julianday(computed_at) DESC, readiness_id DESC
        LIMIT 1
        """,
        (
            STRATEGY_KEY,
            SOURCE_ID,
            data_version,
            city,
            target_date,
            metric,
            _to_utc(decision_time, field_name="decision_time").isoformat(),
        ),
    ).fetchone()
    if row is None:
        return None
    payload = dict(row)
    try:
        status = normalize_replacement_readiness_status(str(payload.get("status") or BLOCKED_STATUS))
        reasons = json.loads(str(payload.get("reason_codes_json") or "[]"))
        dependencies = json.loads(str(payload.get("dependency_json") or "{}"))
        provenance = json.loads(str(payload.get("provenance_json") or "{}"))
        if not isinstance(reasons, list) or not isinstance(dependencies, Mapping) or not isinstance(provenance, Mapping):
            return None
        return ReplacementForecastReadinessDecision(
            readiness_id=str(payload["readiness_id"]),
            status=status if status in {READY_STATUS, BLOCKED_STATUS} else BLOCKED_STATUS,
            reason_codes=tuple(str(reason) for reason in reasons) or ("REPLACEMENT_READINESS_STATE_LOADED",),
            dependency_json=dependencies,
            provenance_json=provenance,
            expires_at=payload.get("expires_at") if status == READY_STATUS else None,
            source_id=SOURCE_ID,
            product_id=PRODUCT_ID,
            strategy_key=STRATEGY_KEY,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _to_utc(value: datetime | str, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field_name} must be a timezone-aware datetime")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _date_text(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        date.fromisoformat(value)
        return value
    raise ValueError("target_date must be a date or ISO date string")


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use the full product identity")


def _posterior_data_version(metric: str) -> str:
    return HIGH_DATA_VERSION if metric == "high" else LOW_DATA_VERSION


@dataclass(frozen=True)
class ReplacementForecastDependency:
    role: str
    source_id: str
    product_id: str
    data_version: str
    source_run_id: str | None
    source_available_at: datetime | str
    status: str = READY_STATUS
    artifact_id: int | None = None
    anchor_id: int | None = None
    posterior_id: int | None = None

    def __post_init__(self) -> None:
        if not self.role:
            raise ValueError("dependency role must be set")
        for field_name, value in (
            ("source_id", self.source_id),
            ("product_id", self.product_id),
            ("data_version", self.data_version),
        ):
            if not value:
                raise ValueError(f"{field_name} must be set")
            _reject_alias(value, field_name=field_name)
        object.__setattr__(self, "source_available_at", _to_utc(self.source_available_at, field_name="source_available_at"))

    def as_payload(self) -> dict[str, object]:
        return {
            "role": self.role,
            "source_id": self.source_id,
            "product_id": self.product_id,
            "data_version": self.data_version,
            "source_run_id": self.source_run_id,
            "source_available_at": self.source_available_at.isoformat(),
            "status": self.status,
            "artifact_id": self.artifact_id,
            "anchor_id": self.anchor_id,
            "posterior_id": self.posterior_id,
        }


@dataclass(frozen=True)
class ReplacementForecastReadinessDecision:
    readiness_id: str
    status: str
    reason_codes: tuple[str, ...]
    dependency_json: Mapping[str, object]
    provenance_json: Mapping[str, object]
    expires_at: datetime | None
    source_id: str = SOURCE_ID
    product_id: str = PRODUCT_ID
    strategy_key: str = STRATEGY_KEY

    def __post_init__(self) -> None:
        if self.status not in {READY_STATUS, BLOCKED_STATUS}:
            raise ValueError("replacement readiness status must be READY or BLOCKED")
        for field_name, value in (("source_id", self.source_id), ("product_id", self.product_id), ("strategy_key", self.strategy_key)):
            _reject_alias(value, field_name=field_name)
        if self.status == READY_STATUS and self.expires_at is None:
            raise ValueError("READY replacement readiness requires expires_at")
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _to_utc(self.expires_at, field_name="expires_at"))


def _stable_id(payload: Mapping[str, object]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "replacement_readiness:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def build_replacement_forecast_readiness(
    *,
    city: str,
    target_date: date | str,
    temperature_metric: str,
    decision_time: datetime | str,
    computed_at: datetime | str,
    expires_at: datetime | str | None,
    dependencies: Sequence[ReplacementForecastDependency],
    required_roles: Sequence[str] = ("baseline_b0", "openmeteo_ifs9_anchor", "soft_anchor_posterior"),
) -> ReplacementForecastReadinessDecision:
    if not city:
        raise ValueError("city must be set")
    if temperature_metric not in {"high", "low"}:
        raise ValueError("temperature_metric must be high or low")
    decision_utc = _to_utc(decision_time, field_name="decision_time")
    computed_utc = _to_utc(computed_at, field_name="computed_at")
    if not dependencies:
        raise ValueError("dependencies must not be empty")
    required = tuple(required_roles)
    if not required:
        raise ValueError("required_roles must not be empty")
    by_role: dict[str, ReplacementForecastDependency] = {}
    for dependency in dependencies:
        if not isinstance(dependency, ReplacementForecastDependency):
            raise TypeError("dependencies must contain ReplacementForecastDependency objects")
        if dependency.role in by_role:
            raise ValueError(f"duplicate replacement dependency role: {dependency.role}")
        by_role[dependency.role] = dependency

    reasons: list[str] = []
    missing_roles = [role for role in required if role not in by_role]
    if missing_roles:
        reasons.append("REPLACEMENT_DEPENDENCY_MISSING")
    for role in missing_roles:
        by_role[role] = ReplacementForecastDependency(
            role=role,
            source_id="missing_dependency",
            product_id="missing_dependency",
            data_version="missing_dependency",
            source_run_id=None,
            source_available_at=decision_utc,  # AVAIL-POSSESSION-EXEMPTED: synthetic stub for a MISSING role (already status=BLOCKED + REPLACEMENT_DEPENDENCY_MISSING). The role has no source/source_run, so no possession time is threadable; decision_utc is the only anchor and is benign at the :185 `> decision_utc` not-future check (decision_utc>decision_utc is False; the role is already BLOCKED regardless). Real roles set source_available_at from honest values upstream.
            status=BLOCKED_STATUS,
        )

    unavailable_roles: list[str] = []
    blocked_roles: list[str] = []
    identity_mismatch_roles: list[str] = []
    expected_identity = expected_replacement_dependency_identity_by_role(temperature_metric)
    for role in required:
        dependency = by_role[role]
        expected = expected_identity.get(role)
        if role == "soft_anchor_posterior":
            if (
                dependency.source_id != SOURCE_ID
                or dependency.product_id != PRODUCT_ID
                or dependency.data_version != _posterior_data_version(temperature_metric)
            ):
                identity_mismatch_roles.append(role)
        elif expected is not None:
            if (
                dependency.source_id != expected.source_id
                or dependency.product_id != expected.product_id
                or dependency.data_version != expected.data_version
            ):
                identity_mismatch_roles.append(role)
        if dependency.source_available_at > decision_utc:
            unavailable_roles.append(role)
        dependency_status = normalize_replacement_readiness_status(dependency.status)
        if dependency_status not in {READY_STATUS, "LIVE_ELIGIBLE"}:
            blocked_roles.append(role)
    if unavailable_roles:
        reasons.append("REPLACEMENT_DEPENDENCY_AFTER_DECISION_TIME")
    if blocked_roles:
        reasons.append("REPLACEMENT_DEPENDENCY_NOT_READY")
    if identity_mismatch_roles:
        reasons.append("REPLACEMENT_DEPENDENCY_IDENTITY_MISMATCH")

    final_status = BLOCKED_STATUS if reasons else READY_STATUS
    if final_status == READY_STATUS and expires_at is None:
        reasons.append("REPLACEMENT_READINESS_EXPIRY_MISSING")
        final_status = BLOCKED_STATUS
    expires_utc = _to_utc(expires_at, field_name="expires_at") if expires_at is not None else None
    if expires_utc is not None and expires_utc <= computed_utc:
        reasons.append("REPLACEMENT_READINESS_ALREADY_EXPIRED")
        final_status = BLOCKED_STATUS

    dependency_payload: dict[str, object] = {
        "required_roles": list(required),
        "dependencies": [by_role[role].as_payload() for role in required],
        "product_id_authority_by_role": {
            role: (
                "derived_from_current_registry_data_version"
                if role == "baseline_b0"
                else "declared_replacement_product_contract"
            )
            for role in required
        },
        "missing_roles": missing_roles,
        "unavailable_roles": unavailable_roles,
        "blocked_roles": blocked_roles,
        "identity_mismatch_roles": identity_mismatch_roles,
    }
    provenance_payload: dict[str, object] = {
        "city": city,
        "target_date": _date_text(target_date),
        "temperature_metric": temperature_metric,
        "decision_time": decision_utc.isoformat(),
        "computed_at": computed_utc.isoformat(),
        "role": "soft_anchor_readiness_dependency_builder",
        "readiness_status": final_status,
        "runtime_layer": LIVE_RUNTIME_LAYER if final_status == READY_STATUS else "blocked",
        "runtime_policy_status": LIVE_RUNTIME_LAYER if final_status == READY_STATUS else "blocked",
        "training_allowed": False,
    }
    identity_payload = {
        "city": city,
        "target_date": _date_text(target_date),
        "temperature_metric": temperature_metric,
        "decision_time": decision_utc.isoformat(),
        "dependencies": dependency_payload,
    }
    return ReplacementForecastReadinessDecision(
        readiness_id=_stable_id(identity_payload),
        status=final_status,
        reason_codes=tuple(reasons or ("REPLACEMENT_DEPENDENCIES_READY",)),
        dependency_json=dependency_payload,
        provenance_json=provenance_payload,
        expires_at=expires_utc if final_status == READY_STATUS else None,
    )
