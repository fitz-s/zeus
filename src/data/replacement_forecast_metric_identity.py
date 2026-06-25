"""Metric identity for replacement forecast blocked products.

This module keeps Open-Meteo ECMWF IFS 9km plus AIFS sampled-2t research
identity out of the live ``MetricIdentity`` factories. Replacement products are
not B0/TIGGE/OpenData calibration authorities, so they need an explicit product
lineage map before they can be used by blocked reports, replay, or veto receipts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.data.forecast_source_registry import (
    ForecastProductSpec,
    replacement_forecast_product,
    replacement_forecast_raw_ensemble_data_versions,
)


TemperatureMetric = Literal["high", "low"]


@dataclass(frozen=True)
class ReplacementForecastMetricIdentity:
    """Product-scoped high/low identity for replacement blocked evidence."""

    temperature_metric: TemperatureMetric
    observation_field: Literal["high_temp", "low_temp"]
    source_id: str
    source_family: str
    product_id: str
    product_class: str
    data_version: str
    physical_quantity: str
    measurement_object: str
    aggregation_window_policy: str
    raw_ensemble_eligible: bool
    training_allowed: bool
    trade_authority_status: str

    def __post_init__(self) -> None:
        if self.temperature_metric == "high" and self.observation_field != "high_temp":
            raise ValueError("high replacement identity requires high_temp observation field")
        if self.temperature_metric == "low" and self.observation_field != "low_temp":
            raise ValueError("low replacement identity requires low_temp observation field")
        if self.data_version.startswith("ecmwf_opendata_") or self.data_version.startswith("tigge_"):
            raise ValueError("replacement metric identity cannot use baseline calibration data_version")
        if self.training_allowed:
            raise ValueError("replacement metric identity cannot carry training authority")
        if self.trade_authority_status == "LIVE_AUTHORITY":
            raise ValueError("replacement metric identity cannot carry live trade authority")


def _data_version_for_metric(product: ForecastProductSpec, metric: TemperatureMetric) -> str:
    data_version = product.high_data_version if metric == "high" else product.low_data_version
    if not data_version:
        raise ValueError(f"replacement product {product.label!r} has no {metric} data_version")
    return data_version


def _physical_quantity_for_product(product: ForecastProductSpec, metric: TemperatureMetric) -> str:
    suffix = "max" if metric == "high" else "min"
    if product.product_class == "ai_ensemble":
        return f"sampled_2t_6h_local_calendar_day_{suffix}"
    if product.product_class == "ifs_ens_direct_model_output":
        if "since_prev_postproc" in product.aggregation_window_policy:
            base = "mx2t_since_prev_postproc" if metric == "high" else "mn2t_since_prev_postproc"
        else:
            base = "mx2t3" if metric == "high" else "mn2t3"
        return f"{base}_local_calendar_day_{suffix}"
    if product.product_class == "deterministic_spatial_anchor":
        return f"deterministic_2t_anchor_local_calendar_day_{suffix}"
    if product.product_class == "derived_blocked_posterior":
        return f"aifs_sampled_2t_plus_openmeteo_ecmwf_ifs9_anchor_local_calendar_day_{suffix}"
    raise ValueError(f"unsupported replacement product_class {product.product_class!r}")


def _measurement_object_for_product(product: ForecastProductSpec) -> str:
    if product.product_class == "ai_ensemble":
        return "aifs_ens_member_sampled_2t_6h"
    if product.product_class == "ifs_ens_direct_model_output":
        return "ifs_ens_member_period_extrema"
    if product.product_class == "deterministic_spatial_anchor":
        return "openmeteo_ecmwf_ifs9_deterministic_anchor"
    if product.product_class == "derived_blocked_posterior":
        return "derived_aifs_sampled_2t_openmeteo_ecmwf_ifs9_soft_anchor_posterior"
    raise ValueError(f"unsupported replacement product_class {product.product_class!r}")


def replacement_forecast_metric_identity(
    product_label: str,
    temperature_metric: str,
) -> ReplacementForecastMetricIdentity:
    """Return product-scoped high/low identity for replacement evidence."""

    if temperature_metric not in {"high", "low"}:
        raise ValueError("temperature_metric must be 'high' or 'low'")
    product = replacement_forecast_product(product_label)
    if product.label == "B0" or product.trade_authority_status == "LIVE_AUTHORITY":
        raise ValueError("baseline products are not replacement metric identities")
    metric = temperature_metric  # type: ignore[assignment]
    data_version = _data_version_for_metric(product, metric)
    raw_ensemble_versions = replacement_forecast_raw_ensemble_data_versions()
    return ReplacementForecastMetricIdentity(
        temperature_metric=metric,
        observation_field="high_temp" if metric == "high" else "low_temp",
        source_id=product.source_id,
        source_family=product.source_family,
        product_id=product.product_id,
        product_class=product.product_class,
        data_version=data_version,
        physical_quantity=_physical_quantity_for_product(product, metric),
        measurement_object=_measurement_object_for_product(product),
        aggregation_window_policy=product.aggregation_window_policy,
        raw_ensemble_eligible=data_version in raw_ensemble_versions,
        training_allowed=product.training_allowed,
        trade_authority_status=product.trade_authority_status,
    )


def replacement_source_family_from_data_version(data_version: str | None) -> str | None:
    """Reverse map replacement data_version to its source_family."""

    key = str(data_version or "").strip()
    if not key or key.startswith("ecmwf_opendata_") or key.startswith("tigge_"):
        return None
    for product in (
        spec for label, spec in replacement_forecast_product_registry_items() if label != "B0"
    ):
        if key in product.data_versions:
            return product.source_family
    return None


def replacement_physical_quantity_for_data_version(
    temperature_metric: str,
    data_version: str | None,
) -> str | None:
    """Return replacement physical quantity for data_version, or None."""

    key = str(data_version or "").strip()
    if temperature_metric not in {"high", "low"} or not key:
        return None
    for label, product in replacement_forecast_product_registry_items():
        if label == "B0" or key not in product.data_versions:
            continue
        identity = replacement_forecast_metric_identity(label, temperature_metric)
        if identity.data_version == key:
            return identity.physical_quantity
    return None


def replacement_forecast_product_registry_items() -> tuple[tuple[str, ForecastProductSpec], ...]:
    """Expose registry items for replacement identity reverse lookups."""

    from src.data.forecast_source_registry import REPLACEMENT_FORECAST_PRODUCTS

    return tuple(REPLACEMENT_FORECAST_PRODUCTS.items())
