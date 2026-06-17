# Created: 2026-06-06
# Last reused/audited: 2026-06-06
# Lifecycle: created=2026-06-06; last_reviewed=2026-06-06; last_reused=2026-06-06
# Purpose: Protect replacement forecast dependency readiness and no-lookahead gating.
# Reuse: Run before wiring Open-Meteo ECMWF IFS 9km + AIFS sampled-2t posterior into readers or event reactor.
# Authority basis: Operator-directed Open-Meteo ECMWF IFS 9km + AIFS ENS sampled-2t shadow/veto integration.
"""Replacement forecast readiness dependency tests."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.data.replacement_forecast_readiness import (
    BLOCKED_STATUS,
    PRODUCT_ID,
    READY_STATUS,
    SOURCE_ID,
    STRATEGY_KEY,
    ReplacementForecastDependency,
    build_replacement_forecast_readiness,
)


UTC = timezone.utc


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 6, hour, minute, tzinfo=UTC)


def _deps(
    *,
    metric: str = "high",
    late_role: str | None = None,
    blocked_role: str | None = None,
    product_mismatch_role: str | None = None,
) -> tuple[ReplacementForecastDependency, ...]:
    suffix = "max" if metric == "high" else "min"
    baseline_param = "mx2t3" if metric == "high" else "mn2t3"
    deps = []
    for role, source_id, product_id, data_version in (
        ("baseline_b0", "ecmwf_open_data", "ecmwf_opendata_ifs_ens_0p25", f"ecmwf_opendata_{baseline_param}_local_calendar_day_{suffix}"),
        ("aifs_sampled_2t", "ecmwf_aifs_ens", "ecmwf_aifs_ens_sampled_2t_6h_v1", f"ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_{suffix}"),
        ("openmeteo_ifs9_anchor", "openmeteo_ecmwf_ifs_9km", "openmeteo_ecmwf_ifs9_deterministic_anchor_v1", f"openmeteo_ecmwf_ifs9_anchor_localday_{metric}"),
        ("soft_anchor_posterior", SOURCE_ID, PRODUCT_ID, f"openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_{metric}_v1"),
    ):
        if role == product_mismatch_role:
            product_id = "wrong_replacement_product"
        deps.append(
            ReplacementForecastDependency(
                role=role,
                source_id=source_id,
                product_id=product_id,
                data_version=data_version,
                source_run_id=f"run-{role}",
                source_available_at=_dt(3) if role != late_role else _dt(5),
                status=BLOCKED_STATUS if role == blocked_role else READY_STATUS,
                artifact_id=10 if role == "aifs_sampled_2t" else None,
                anchor_id=20 if role == "openmeteo_ifs9_anchor" else None,
                posterior_id=30 if role == "soft_anchor_posterior" else None,
            )
        )
    return tuple(deps)


def _decision(**kwargs):
    metric = kwargs.get("temperature_metric", "high")
    params = {
        "city": "Shanghai",
        "target_date": date(2026, 6, 7),
        "temperature_metric": metric,
        "decision_time": _dt(4),
        "computed_at": _dt(4, 1),
        "expires_at": _dt(6),
        "dependencies": _deps(metric=metric),
    }
    params.update(kwargs)
    return build_replacement_forecast_readiness(**params)


def test_replacement_readiness_is_shadow_only_when_all_dependencies_are_available_before_decision() -> None:
    decision = _decision()

    assert decision.source_id == SOURCE_ID
    assert decision.product_id == PRODUCT_ID
    assert decision.strategy_key == STRATEGY_KEY
    assert decision.status == READY_STATUS
    assert decision.reason_codes == ("REPLACEMENT_DEPENDENCIES_READY",)
    assert decision.expires_at == _dt(6)
    # AIFS DROPPED (operator directive 2026-06-17 "drop aifs"): aifs_sampled_2t is no longer a
    # required readiness role. The live q is the multi-model fused Normal (zero AIFS dependency).
    assert decision.dependency_json["required_roles"] == [
        "baseline_b0",
        "openmeteo_ifs9_anchor",
        "soft_anchor_posterior",
    ]
    assert "aifs_sampled_2t" not in decision.dependency_json["required_roles"]
    assert decision.dependency_json["missing_roles"] == []
    assert decision.dependency_json["unavailable_roles"] == []
    assert decision.dependency_json["identity_mismatch_roles"] == []
    assert decision.provenance_json["trade_authority_status"] == "SHADOW_VETO_ONLY"
    assert decision.provenance_json["training_allowed"] is False


def test_replacement_readiness_validates_dependency_identity_by_metric() -> None:
    low = _decision(temperature_metric="low")

    assert low.status == READY_STATUS
    dependencies = {row["role"]: row for row in low.dependency_json["dependencies"]}
    assert dependencies["baseline_b0"]["data_version"] == "ecmwf_opendata_mn2t3_local_calendar_day_min"
    # AIFS DROPPED: aifs_sampled_2t is no longer a required role, so it is not part of the validated
    # dependency payload (the role loop iterates required roles only).
    assert "aifs_sampled_2t" not in dependencies
    assert dependencies["openmeteo_ifs9_anchor"]["data_version"] == "openmeteo_ecmwf_ifs9_anchor_localday_low"
    assert dependencies["soft_anchor_posterior"]["data_version"] == "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_low_v1"


def test_replacement_readiness_blocks_role_identity_mismatch() -> None:
    # AIFS DROPPED: re-pointed from aifs_sampled_2t (no longer a required role, so its identity is
    # never checked) to openmeteo_ifs9_anchor — a still-required role whose identity is validated.
    decision = _decision(dependencies=_deps(product_mismatch_role="openmeteo_ifs9_anchor"))

    assert decision.status == BLOCKED_STATUS
    assert "REPLACEMENT_DEPENDENCY_IDENTITY_MISMATCH" in decision.reason_codes
    assert decision.dependency_json["identity_mismatch_roles"] == ["openmeteo_ifs9_anchor"]


def test_replacement_readiness_blocks_missing_dependency() -> None:
    decision = _decision(dependencies=_deps()[:-1])

    assert decision.status == BLOCKED_STATUS
    assert "REPLACEMENT_DEPENDENCY_MISSING" in decision.reason_codes
    assert "REPLACEMENT_DEPENDENCY_NOT_READY" in decision.reason_codes
    assert decision.dependency_json["missing_roles"] == ["soft_anchor_posterior"]
    assert decision.expires_at is None


def test_replacement_readiness_blocks_dependency_after_decision_time() -> None:
    decision = _decision(dependencies=_deps(late_role="openmeteo_ifs9_anchor"))

    assert decision.status == BLOCKED_STATUS
    assert decision.reason_codes == ("REPLACEMENT_DEPENDENCY_AFTER_DECISION_TIME",)
    assert decision.dependency_json["unavailable_roles"] == ["openmeteo_ifs9_anchor"]


def test_replacement_readiness_blocks_dependency_not_ready() -> None:
    # AIFS DROPPED: re-pointed from aifs_sampled_2t to openmeteo_ifs9_anchor (a still-required role);
    # a blocked AIFS leg can no longer block readiness because AIFS is no longer required.
    decision = _decision(dependencies=_deps(blocked_role="openmeteo_ifs9_anchor"))

    assert decision.status == BLOCKED_STATUS
    assert decision.reason_codes == ("REPLACEMENT_DEPENDENCY_NOT_READY",)
    assert decision.dependency_json["blocked_roles"] == ["openmeteo_ifs9_anchor"]


def test_replacement_readiness_ready_with_no_aifs_dependency() -> None:
    # AIFS-DROP RED-on-revert (operator directive 2026-06-17): readiness is READY with ONLY the
    # baseline + OM9 anchor + posterior legs and NO aifs_sampled_2t dependency at all. If aifs is
    # re-added to required_roles, this BLOCKS on REPLACEMENT_DEPENDENCY_MISSING.
    deps_no_aifs = tuple(d for d in _deps() if d.role != "aifs_sampled_2t")
    decision = _decision(dependencies=deps_no_aifs)

    assert decision.status == READY_STATUS
    assert decision.reason_codes == ("REPLACEMENT_DEPENDENCIES_READY",)
    assert "aifs_sampled_2t" not in decision.dependency_json["required_roles"]
    assert decision.dependency_json["missing_roles"] == []


def test_replacement_readiness_blocks_missing_or_expired_expiry() -> None:
    missing = _decision(expires_at=None)
    expired = _decision(expires_at=_dt(4))

    assert missing.status == BLOCKED_STATUS
    assert missing.reason_codes == ("REPLACEMENT_READINESS_EXPIRY_MISSING",)
    assert expired.status == BLOCKED_STATUS
    assert expired.reason_codes == ("REPLACEMENT_READINESS_ALREADY_EXPIRED",)


def test_replacement_readiness_rejects_bad_identity_or_metric() -> None:
    with pytest.raises(ValueError, match="temperature_metric"):
        _decision(temperature_metric="mean")

    with pytest.raises(ValueError, match="full product identity"):
        ReplacementForecastDependency(
            role="bad",
            source_id="bad_" + "h" + "3",
            product_id="product",
            data_version="version",
            source_run_id=None,
            source_available_at=_dt(1),
        )
