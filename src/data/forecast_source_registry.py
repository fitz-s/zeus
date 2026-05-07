# Created: 2026-04-27
# Last reused/audited: 2026-04-27
# Authority basis: docs/operations/task_2026-04-26_ultimate_plan/r3/slice_cards/F1.yaml
"""Forecast source registry and operator gate checks for R3 F1.

The registry is forecast-source plumbing. It is not settlement-source
authority, does not activate new upstream ingest, and does not retrain
calibration. Experimental sources stay dormant until both operator evidence
and a runtime env flag are present.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Literal

from src.data.forecast_ingest_protocol import ForecastAuthorityTier, ForecastIngestProtocol
from src.data.tigge_client import TIGGEIngest


ForecastSourceTier = Literal["primary", "secondary", "experimental", "disabled"]
ForecastSourceKind = Literal["forecast_table", "live_ensemble", "experimental_ingest", "scheduled_collector"]
ForecastSourceRole = Literal[
    "entry_primary",
    "entry_fallback",
    "monitor_fallback",
    "diagnostic",
    "learning",
]
ForecastDegradationLevel = Literal[
    "OK",
    "DEGRADED_FORECAST_FALLBACK",
    "EXPERIMENTAL_DISABLED",
    "DIAGNOSTIC_NON_EXECUTABLE",
]

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SourceNotEnabled(RuntimeError):
    """Raised when a forecast source is disabled or operator-gated closed."""


@dataclass(frozen=True)
class ForecastSourceSpec:
    """Typed registry row for a forecast source."""

    source_id: str
    tier: ForecastSourceTier
    kind: ForecastSourceKind
    authority_tier: ForecastAuthorityTier = "FORECAST"
    ingest_class: type[ForecastIngestProtocol] | None = None
    requires_api_key: bool = False
    requires_operator_decision: bool = False
    operator_decision_artifact: str | None = None
    env_flag_name: str | None = None
    enabled_by_default: bool = True
    model_name: str | None = None
    allowed_roles: tuple[ForecastSourceRole, ...] = ("diagnostic",)
    degradation_level: ForecastDegradationLevel = "OK"

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("ForecastSourceSpec.source_id is required")
        if self.tier == "disabled" and self.enabled_by_default:
            raise ValueError("disabled forecast sources cannot be enabled_by_default")
        if self.degradation_level == "OK" and "entry_fallback" in self.allowed_roles:
            raise ValueError("entry fallback sources must carry a degraded forecast level")
        if self.requires_operator_decision and not (
            self.operator_decision_artifact and self.env_flag_name
        ):
            raise ValueError(
                "operator-gated forecast sources require artifact pattern and env flag"
            )


OPENMETEO_PREVIOUS_RUNS_MODEL_SOURCE_MAP: dict[str, str] = {
    "best_match": "openmeteo_previous_runs",
    "gfs_global": "gfs_previous_runs",
    "ecmwf_ifs025": "ecmwf_previous_runs",
    "icon_global": "icon_previous_runs",
    "ukmo_global_deterministic_10km": "ukmo_previous_runs",
}

# Phase 3 routing fix (2026-05-04): training/serving alignment.
# `ecmwf_ifs025` model now routes to `ecmwf_open_data` (raw ECMWF public
# feed) instead of `openmeteo_ensemble_ecmwf_ifs025` (third-party broker).
# Open-Meteo applies 1-hour temporal interpolation and re-packages the
# 51-member ENS, breaking member-identity alignment with the TIGGE
# archive that Platt models were trained on. ECMWF Open Data is the
# same model, same ensemble, raw GRIB2 → no training/serving skew.
# Authority: docs/operations/task_2026-05-04_tigge_ingest_resilience/
#            DESIGN_PHASE3_LIVE_ROUTING_FIX.md
ENSEMBLE_MODEL_SOURCE_MAP: dict[str, str] = {
    "ecmwf_ifs025": "ecmwf_open_data",
    "gfs025": "openmeteo_ensemble_gfs025",
    "gfs": "openmeteo_ensemble_gfs025",
    "tigge": "tigge",
}

_TIGGE_OPERATOR_ARTIFACT = (
    "docs/operations/task_2026-04-26_ultimate_plan/**/"
    "evidence/tigge_ingest_decision_*.md"
)

SOURCES: dict[str, ForecastSourceSpec] = {
    "openmeteo_previous_runs": ForecastSourceSpec(
        source_id="openmeteo_previous_runs",
        tier="primary",
        kind="forecast_table",
        model_name="best_match",
        allowed_roles=("diagnostic",),
    ),
    "gfs_previous_runs": ForecastSourceSpec(
        source_id="gfs_previous_runs",
        tier="secondary",
        kind="forecast_table",
        model_name="gfs_global",
        allowed_roles=("diagnostic",),
    ),
    "ecmwf_previous_runs": ForecastSourceSpec(
        source_id="ecmwf_previous_runs",
        tier="secondary",
        kind="forecast_table",
        model_name="ecmwf_ifs025",
        allowed_roles=("diagnostic",),
    ),
    "icon_previous_runs": ForecastSourceSpec(
        source_id="icon_previous_runs",
        tier="secondary",
        kind="forecast_table",
        model_name="icon_global",
        allowed_roles=("diagnostic",),
    ),
    "ukmo_previous_runs": ForecastSourceSpec(
        source_id="ukmo_previous_runs",
        tier="secondary",
        kind="forecast_table",
        model_name="ukmo_global_deterministic_10km",
        allowed_roles=("diagnostic",),
    ),
    "openmeteo_ensemble_ecmwf_ifs025": ForecastSourceSpec(
        source_id="openmeteo_ensemble_ecmwf_ifs025",
        tier="secondary",
        kind="live_ensemble",
        model_name="ecmwf_ifs025",
        allowed_roles=("monitor_fallback", "diagnostic"),
        degradation_level="DEGRADED_FORECAST_FALLBACK",
    ),
    "openmeteo_ensemble_gfs025": ForecastSourceSpec(
        source_id="openmeteo_ensemble_gfs025",
        tier="secondary",
        kind="live_ensemble",
        model_name="gfs025",
        allowed_roles=("monitor_fallback", "diagnostic"),
        degradation_level="DEGRADED_FORECAST_FALLBACK",
    ),
    "tigge": ForecastSourceSpec(
        source_id="tigge",
        tier="experimental",
        kind="experimental_ingest",
        ingest_class=TIGGEIngest,
        requires_api_key=True,
        requires_operator_decision=True,
        operator_decision_artifact=_TIGGE_OPERATOR_ARTIFACT,
        env_flag_name="ZEUS_TIGGE_INGEST_ENABLED",
        enabled_by_default=False,
        allowed_roles=("entry_primary", "monitor_fallback", "diagnostic"),
        degradation_level="OK",
    ),
    # Phase 3 (2026-05-04): ECMWF Open Data promoted from diagnostic to
    # entry_primary candidate. Same model & 51-member ENS as the TIGGE
    # archive used for Platt training; raw GRIB2 with no third-party
    # interpolation; 4 cycles/day. Live eligibility is gated separately
    # by evaluate_calibration_transfer (Phase 2.5) — a forecast routed
    # here can still be SHADOW_ONLY if no validated_transfers row exists
    # for its (cycle, source, horizon, season) domain. Routing presence
    # ≠ live trading; the calibration transfer evaluator is the unlock
    # gate.
    # Authority: docs/operations/task_2026-05-04_tigge_ingest_resilience/
    #            DESIGN_PHASE3_LIVE_ROUTING_FIX.md
    "ecmwf_open_data": ForecastSourceSpec(
        source_id="ecmwf_open_data",
        tier="secondary",
        kind="scheduled_collector",
        model_name="ecmwf_open_data",
        allowed_roles=(
            "entry_primary",
            "training_archive_alignment",
            "monitor_fallback",
            "diagnostic",
        ),
        degradation_level="OK",
    ),
}


def stable_payload_hash(payload: object) -> str:
    """Return a deterministic sha256 digest for raw forecast payloads."""

    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def get_source(
    source_id: str,
    *,
    sources: Mapping[str, ForecastSourceSpec] | None = None,
) -> ForecastSourceSpec:
    registry = sources or SOURCES
    try:
        return registry[source_id]
    except KeyError as exc:
        raise SourceNotEnabled(f"forecast source {source_id!r} is not registered") from exc


def forecast_table_source_ids(
    *,
    sources: Mapping[str, ForecastSourceSpec] | None = None,
) -> tuple[str, ...]:
    registry = sources or SOURCES
    return tuple(
        source.source_id
        for source in registry.values()
        if source.kind == "forecast_table" and source.tier != "disabled"
    )


def source_id_for_previous_runs_model(model: str) -> str:
    try:
        return OPENMETEO_PREVIOUS_RUNS_MODEL_SOURCE_MAP[model]
    except KeyError as exc:
        raise SourceNotEnabled(f"Open-Meteo previous-runs model {model!r} is not registered") from exc


def source_id_for_ensemble_model(model: str | None) -> str:
    key = str(model or "ecmwf_ifs025").strip().lower()
    return ENSEMBLE_MODEL_SOURCE_MAP.get(key, key)


_CALIBRATION_LOOKUP_SOURCE_ID_BY_FORECAST_SOURCE_ID: dict[str, str] = {
    "tigge": "tigge_mars",
    "tigge_mars": "tigge_mars",
    "ecmwf_open_data": "ecmwf_open_data",
}


def calibration_source_id_for_lookup(source_id: str | None) -> str | None:
    """Map forecast-source identity to the Platt bucket source axis.

    Forecast evidence keeps its provider/source id (`tigge`,
    `ecmwf_open_data`, fallback provider ids, etc.). Platt v2 lookup uses a
    narrower bucket identity. Returning None means the forecast source has no
    live calibration bucket authority and callers must not rely on schema
    defaults or legacy Platt fallback as if it did.
    """

    key = str(source_id or "").strip().lower()
    if not key:
        return None
    return _CALIBRATION_LOOKUP_SOURCE_ID_BY_FORECAST_SOURCE_ID.get(key)


def _env_enabled(flag_name: str, environ: Mapping[str, str]) -> bool:
    return str(environ.get(flag_name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _operator_artifact_present(pattern: str, *, root: Path) -> bool:
    return any(root.glob(pattern))


def is_source_enabled(
    source_id: str,
    *,
    environ: Mapping[str, str] | None = None,
    root: Path | None = None,
    sources: Mapping[str, ForecastSourceSpec] | None = None,
) -> bool:
    spec = get_source(source_id, sources=sources)
    if spec.tier == "disabled" or not spec.enabled_by_default:
        if not spec.requires_operator_decision:
            return False
    if not spec.requires_operator_decision:
        return spec.enabled_by_default and spec.tier != "disabled"

    env = environ or os.environ
    base = root or PROJECT_ROOT
    assert spec.env_flag_name is not None
    assert spec.operator_decision_artifact is not None
    return _env_enabled(spec.env_flag_name, env) and _operator_artifact_present(
        spec.operator_decision_artifact,
        root=base,
    )


def gate_source(
    source_id: str,
    *,
    environ: Mapping[str, str] | None = None,
    root: Path | None = None,
    sources: Mapping[str, ForecastSourceSpec] | None = None,
) -> ForecastSourceSpec:
    """Return enabled source spec or raise SourceNotEnabled."""

    spec = get_source(source_id, sources=sources)
    if not is_source_enabled(source_id, environ=environ, root=root, sources=sources):
        raise SourceNotEnabled(
            f"forecast source {source_id!r} is disabled or operator-gated closed"
        )
    return spec


def source_allows_role(spec: ForecastSourceSpec, role: ForecastSourceRole) -> bool:
    return role in spec.allowed_roles


def gate_source_role(spec: ForecastSourceSpec, role: ForecastSourceRole) -> None:
    """Fail closed when a source is not authorized for the requested money lane."""

    if not source_allows_role(spec, role):
        raise SourceNotEnabled(
            f"forecast source {spec.source_id!r} is not authorized for role {role!r} "
            f"(degradation_level={spec.degradation_level})"
        )


def active_sources(
    *,
    environ: Mapping[str, str] | None = None,
    root: Path | None = None,
    sources: Mapping[str, ForecastSourceSpec] | None = None,
) -> list[ForecastSourceSpec]:
    """Return forecast sources whose static/runtime gates are open."""

    registry = sources or SOURCES
    return [
        source
        for source in registry.values()
        if is_source_enabled(
            source.source_id,
            environ=environ,
            root=root,
            sources=registry,
        )
    ]
