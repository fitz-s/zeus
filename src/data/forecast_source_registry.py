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
from src.data.ecmwf_open_data_ingest import ECMWFOpenDataIngest


ForecastSourceTier = Literal["primary", "secondary", "experimental", "disabled"]
ForecastSourceKind = Literal[
    "forecast_table",
    "live_ensemble",
    "experimental_ingest",
    "scheduled_collector",
    "deterministic_anchor",
    "derived_posterior",
]
ForecastSourceRole = Literal[
    "entry_primary",
    "entry_fallback",
    "monitor_fallback",
    "diagnostic",
    "learning",
    "training_archive_alignment",
]
ForecastDegradationLevel = Literal[
    "OK",
    "DEGRADED_FORECAST_FALLBACK",
    "EXPERIMENTAL_DISABLED",
    "DIAGNOSTIC_NON_EXECUTABLE",
]
ForecastTradeAuthorityStatus = Literal[
    "LIVE_AUTHORITY",
    "SHADOW_ONLY",
    "COMPARATOR_ONLY",
    "DISABLED",
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


@dataclass(frozen=True)
class ForecastProductSpec:
    """Static forecast-product identity for shadow replacement candidates.

    This is product policy only. Membership here does not make a source
    fetchable, trainable, or live-tradeable; runtime activation still flows
    through ``ForecastSourceSpec`` gates and calibration/promotion evidence.
    """

    label: str
    source_id: str
    product_id: str
    source_family: str
    model_name: str
    product_class: str
    stream: str
    product_type: str
    param: str
    aggregation_window_policy: str
    high_data_version: str | None
    low_data_version: str | None
    expected_members: int | None
    trade_authority_status: ForecastTradeAuthorityStatus
    training_allowed: bool

    @property
    def data_versions(self) -> tuple[str, ...]:
        return tuple(
            version
            for version in (self.high_data_version, self.low_data_version)
            if version is not None
        )


@dataclass(frozen=True)
class ForecastReplacementEvidence:
    """Settled empirical evidence for choosing among replacement products."""

    label: str
    settled_decisions: int
    anti_lookahead_violations: int
    availability_violations: int
    q_lcb_coverage: float
    after_cost_pnl: float
    max_drawdown: float
    brier: float
    log_loss: float


@dataclass(frozen=True)
class ForecastReplacementSelection:
    """Result of evidence-gated replacement tournament selection."""

    status: str
    selected_label: str | None
    reason_codes: tuple[str, ...]


OPENMETEO_PREVIOUS_RUNS_MODEL_SOURCE_MAP: dict[str, str] = {
    "best_match": "openmeteo_previous_runs",
    "gfs_global": "gfs_previous_runs",
    "ecmwf_ifs025": "ecmwf_previous_runs",
    "icon_global": "icon_previous_runs",
    "ukmo_global_deterministic_10km": "ukmo_previous_runs",
    # U0R-Bayes F1 decorrelated globals + in-domain regionals (2026-06-08, SPEC §3/§6).
    # OM previous-runs API supports these model ids; the U0R fixed-lead walk-forward train
    # reads them via the temperature_2m_previous_dayN hourly var. icon_eu has NO previous-runs
    # entry: it is dedup-folded to icon_d2 in-EU / icon_global out (SPEC §3 alias dedup).
    "gem_global": "gem_previous_runs",
    "jma_seamless": "jma_previous_runs",
    "icon_d2": "icon_d2_previous_runs",
    "meteofrance_arome_france_hd": "arome_previous_runs",
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
    "docs/historical_evidence/tigge_ingest_decision_*.md"
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
    # U0R-Bayes F1 decorrelated globals + in-domain regionals (2026-06-08, SPEC §3/§6 F0/F1).
    # diagnostic-only (SHADOW capture train): these feed the fixed-lead walk-forward history
    # for u0r_bayes fusion, never a live serve path on their own.
    "gem_previous_runs": ForecastSourceSpec(
        source_id="gem_previous_runs",
        tier="secondary",
        kind="forecast_table",
        model_name="gem_global",
        allowed_roles=("diagnostic",),
    ),
    "jma_previous_runs": ForecastSourceSpec(
        source_id="jma_previous_runs",
        tier="secondary",
        kind="forecast_table",
        model_name="jma_seamless",
        allowed_roles=("diagnostic",),
    ),
    "icon_d2_previous_runs": ForecastSourceSpec(
        source_id="icon_d2_previous_runs",
        tier="secondary",
        kind="forecast_table",
        model_name="icon_d2",
        allowed_roles=("diagnostic",),
    ),
    "arome_previous_runs": ForecastSourceSpec(
        source_id="arome_previous_runs",
        tier="secondary",
        kind="forecast_table",
        model_name="meteofrance_arome_france_hd",
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
        ingest_class=ECMWFOpenDataIngest,
        allowed_roles=(
            "entry_primary",
            "training_archive_alignment",
            "monitor_fallback",
            "diagnostic",
        ),
        degradation_level="OK",
    ),
    "ecmwf_ifs_ens_0p1": ForecastSourceSpec(
        source_id="ecmwf_ifs_ens_0p1",
        tier="disabled",
        kind="experimental_ingest",
        enabled_by_default=False,
        model_name="ecmwf_ifs_ens_0p1",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    "ecmwf_aifs_ens": ForecastSourceSpec(
        source_id="ecmwf_aifs_ens",
        tier="disabled",
        kind="experimental_ingest",
        enabled_by_default=False,
        model_name="ecmwf_aifs_ens",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    "openmeteo_ecmwf_ifs_9km": ForecastSourceSpec(
        source_id="openmeteo_ecmwf_ifs_9km",
        tier="disabled",
        kind="deterministic_anchor",
        enabled_by_default=False,
        model_name="openmeteo_ecmwf_ifs_9km",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor": ForecastSourceSpec(
        source_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        tier="disabled",
        kind="derived_posterior",
        enabled_by_default=False,
        model_name="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    "ecmwf_ifs_control_0p1": ForecastSourceSpec(
        source_id="ecmwf_ifs_control_0p1",
        tier="disabled",
        kind="experimental_ingest",
        enabled_by_default=False,
        model_name="ecmwf_ifs_control_0p1",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    # ---- The Path U0R-Bayes fusion sources (F0) -------------------------------------
    # Authority: U0R_BAYES_SPEC.md §6 F0 (source registry); U0R_PROOF_RESULT.md (core
    # PROMOTE, regionals SHADOW-ONLY/DEFER). These are Open-Meteo previous-runs /
    # single-runs decorrelated globals + in-domain regional experts that feed the U0R
    # multi-model posterior. They are DISABLED plumbing rows until the U0R fusion flag
    # (replacement_0_1_u0r_fusion_enabled, default-OFF) AND an ingest path activate them;
    # the per-model live capture is fail-soft (a missing source is simply dropped).
    "openmeteo_gfs_global": ForecastSourceSpec(
        source_id="openmeteo_gfs_global",
        tier="disabled",
        kind="forecast_table",
        enabled_by_default=False,
        model_name="gfs_global",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    "openmeteo_icon_global": ForecastSourceSpec(
        source_id="openmeteo_icon_global",
        tier="disabled",
        kind="forecast_table",
        enabled_by_default=False,
        model_name="icon_global",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    "openmeteo_gem_global": ForecastSourceSpec(
        source_id="openmeteo_gem_global",
        tier="disabled",
        kind="forecast_table",
        enabled_by_default=False,
        model_name="gem_global",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    "openmeteo_jma_seamless": ForecastSourceSpec(
        source_id="openmeteo_jma_seamless",
        tier="disabled",
        kind="forecast_table",
        enabled_by_default=False,
        model_name="jma_seamless",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    # Regional experts (conditional, in-domain only) — SHADOW-ONLY/DEFER per proof verdict.
    "openmeteo_icon_d2_eu": ForecastSourceSpec(
        source_id="openmeteo_icon_d2_eu",
        tier="disabled",
        kind="forecast_table",
        enabled_by_default=False,
        model_name="icon_d2",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    "openmeteo_arome_fr_hd": ForecastSourceSpec(
        source_id="openmeteo_arome_fr_hd",
        tier="disabled",
        kind="forecast_table",
        enabled_by_default=False,
        model_name="meteofrance_arome_france_hd",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
    # The fused derived posterior product (replaces the single-anchor center/spread when
    # the U0R flag is ON; shadow-only until settlement evidence promotes it).
    "the_path_u0r_fusion": ForecastSourceSpec(
        source_id="the_path_u0r_fusion",
        tier="disabled",
        kind="derived_posterior",
        enabled_by_default=False,
        model_name="the_path_u0r_fusion",
        allowed_roles=("diagnostic",),
        degradation_level="DIAGNOSTIC_NON_EXECUTABLE",
    ),
}


REPLACEMENT_FORECAST_PRODUCTS: dict[str, ForecastProductSpec] = {
    "B0": ForecastProductSpec(
        label="B0",
        source_id="ecmwf_open_data",
        product_id="ecmwf_opendata_ifs_ens_0p25",
        source_family="ecmwf_ifs",
        model_name="ecmwf_open_data",
        product_class="ifs_ens_public_subset",
        stream="enfo",
        product_type="pf+cf",
        param="mx2t3/mn2t3",
        aggregation_window_policy="period_3h_local_calendar_day",
        high_data_version="ecmwf_opendata_mx2t3_local_calendar_day_max",
        low_data_version="ecmwf_opendata_mn2t3_local_calendar_day_min",
        expected_members=51,
        trade_authority_status="LIVE_AUTHORITY",
        training_allowed=True,
    ),
    "R1": ForecastProductSpec(
        label="R1",
        source_id="ecmwf_ifs_ens_0p1",
        product_id="ecmwf_ifs_ens_0p1_mx2t3",
        source_family="ecmwf_ifs",
        model_name="ecmwf_ifs_ens",
        product_class="ifs_ens_direct_model_output",
        stream="enfo",
        product_type="pf+cf",
        param="mx2t3/mn2t3",
        aggregation_window_policy="period_3h_local_calendar_day",
        high_data_version="ecmwf_ifs_ens_0p1_mx2t3_local_calendar_day_max",
        low_data_version="ecmwf_ifs_ens_0p1_mn2t3_local_calendar_day_min",
        expected_members=51,
        trade_authority_status="SHADOW_ONLY",
        training_allowed=False,
    ),
    "R2": ForecastProductSpec(
        label="R2",
        source_id="ecmwf_ifs_ens_0p1",
        product_id="ecmwf_ifs_ens_0p1_since_prev_postproc",
        source_family="ecmwf_ifs",
        model_name="ecmwf_ifs_ens",
        product_class="ifs_ens_direct_model_output",
        stream="enfo",
        product_type="pf+cf",
        param="mx2t/mn2t",
        aggregation_window_policy="since_prev_postproc_local_calendar_day",
        high_data_version=(
            "ecmwf_ifs_ens_0p1_mx2t_since_prev_postproc_local_calendar_day_max"
        ),
        low_data_version=(
            "ecmwf_ifs_ens_0p1_mn2t_since_prev_postproc_local_calendar_day_min"
        ),
        expected_members=51,
        trade_authority_status="SHADOW_ONLY",
        training_allowed=False,
    ),
    "C1": ForecastProductSpec(
        label="C1",
        source_id="ecmwf_ifs_control_0p1",
        product_id="ecmwf_ifs_control_0p1",
        source_family="ecmwf_ifs",
        model_name="ecmwf_ifs_control",
        product_class="ifs_control_comparator",
        stream="oper",
        product_type="fc",
        param="mx2t3/mn2t3",
        aggregation_window_policy="period_3h_local_calendar_day",
        high_data_version=None,
        low_data_version=None,
        expected_members=1,
        trade_authority_status="COMPARATOR_ONLY",
        training_allowed=False,
    ),
    "A1": ForecastProductSpec(
        label="A1",
        source_id="ecmwf_aifs_ens",
        product_id="ecmwf_aifs_ens_sampled_2t_6h_v1",
        source_family="ecmwf_aifs",
        model_name="aifs_ens",
        product_class="ai_ensemble",
        stream="enfo",
        product_type="pf+cf",
        param="2t",
        aggregation_window_policy="sampled_2t_6h_local_calendar_day",
        high_data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_max",
        low_data_version="ecmwf_aifs_ens_sampled_2t_6h_local_calendar_day_min",
        expected_members=51,
        trade_authority_status="SHADOW_ONLY",
        training_allowed=False,
    ),
    "Open-Meteo ECMWF ecmwf_ifs 9km/0.1 deterministic forecast soft spatial anchor": ForecastProductSpec(
        label=(
            "Open-Meteo ECMWF ecmwf_ifs 9km/0.1 deterministic forecast "
            "soft spatial anchor"
        ),
        source_id="openmeteo_ecmwf_ifs_9km",
        product_id="openmeteo_ecmwf_ifs9_deterministic_anchor_v1",
        source_family="openmeteo_ecmwf",
        model_name="ecmwf_ifs",
        product_class="deterministic_spatial_anchor",
        stream="openmeteo_run_pinned",
        product_type="deterministic_anchor",
        param="2t",
        aggregation_window_policy="deterministic_local_calendar_day_anchor",
        high_data_version="openmeteo_ecmwf_ifs9_anchor_localday_high",
        low_data_version="openmeteo_ecmwf_ifs9_anchor_localday_low",
        expected_members=None,
        trade_authority_status="SHADOW_ONLY",
        training_allowed=False,
    ),
    "Open-Meteo ECMWF ecmwf_ifs 9km/0.1 deterministic forecast soft spatial anchor plus AIFS ENS sampled-2t posterior": ForecastProductSpec(
        label=(
            "Open-Meteo ECMWF ecmwf_ifs 9km/0.1 deterministic forecast "
            "soft spatial anchor plus AIFS ENS sampled-2t posterior"
        ),
        source_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
        source_family="derived_posterior",
        model_name="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        product_class="derived_shadow_posterior",
        stream="derived",
        product_type="soft_anchor_posterior",
        param="aifs_sampled_2t_posterior+openmeteo_ecmwf_ifs9_anchor",
        aggregation_window_policy="aifs_sampled_2t_6h_plus_deterministic_anchor_local_calendar_day",
        high_data_version="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1",
        low_data_version="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_low_v1",
        expected_members=None,
        trade_authority_status="SHADOW_ONLY",
        training_allowed=False,
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
    # ECMWF Opendata IS the TIGGE archive's live channel — same physical IFS
    # ensemble per architecture/ecmwf_opendata_tigge_equivalence_2026_05_06.yaml.
    # Calibration bucket axis routes through TIGGE Platt models (599+195 active,
    # source_id='tigge_mars' in platt_models). Identity-mapping this to
    # 'ecmwf_open_data' produces 0 hits at platt_models lookup → silent
    # SHADOW_ONLY. Fixed 2026-05-10 (operator: 接线错误).
    "ecmwf_open_data": "tigge_mars",
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


def replacement_forecast_product(label: str) -> ForecastProductSpec:
    """Return a registered replacement-tournament product by label."""

    try:
        return REPLACEMENT_FORECAST_PRODUCTS[label]
    except KeyError as exc:
        raise SourceNotEnabled(f"replacement forecast product {label!r} is not registered") from exc


def replacement_forecast_data_versions() -> frozenset[str]:
    """Data versions reserved for non-B0 replacement candidates."""

    return frozenset(
        data_version
        for label, product in REPLACEMENT_FORECAST_PRODUCTS.items()
        if label != "B0"
        for data_version in product.data_versions
    )


def replacement_forecast_raw_ensemble_data_versions() -> frozenset[str]:
    """Replacement data versions eligible for raw ensemble snapshot rows.

    Deterministic spatial anchors and derived posterior products are not raw
    ensemble measurements. Keeping this whitelist separate prevents the
    Open-Meteo 9km anchor or the soft-anchor posterior from masquerading as
    member-level forecast evidence.
    """

    return frozenset(
        data_version
        for label, product in REPLACEMENT_FORECAST_PRODUCTS.items()
        if label != "B0"
        and product.expected_members is not None
        and product.expected_members > 1
        and product.product_class in {"ifs_ens_direct_model_output", "ai_ensemble"}
        and product.trade_authority_status == "SHADOW_ONLY"
        for data_version in product.data_versions
    )


def is_replacement_forecast_raw_ensemble_data_version(data_version: str | None) -> bool:
    """Return whether a replacement data version is raw ensemble eligible."""

    key = str(data_version or "").strip()
    return key in replacement_forecast_raw_ensemble_data_versions()


def select_empirical_replacement_strategy(
    evidence: list[ForecastReplacementEvidence],
    *,
    min_settled_decisions: int = 200,
    min_q_lcb_coverage: float = 0.95,
) -> ForecastReplacementSelection:
    """Choose the best replacement candidate from settled evidence.

    Selection is deliberately empirical and fail-closed. A candidate is
    promotable only if it has enough settled decisions, no time-filtration
    violations, q_lcb coverage at or above threshold, and positive
    after-cost PnL. Eligible candidates are ranked by economic result first,
    then forecast skill metrics.
    """

    if not evidence:
        return ForecastReplacementSelection(
            status="NO_EMPIRICAL_EVIDENCE",
            selected_label=None,
            reason_codes=("EMPIRICAL_EVIDENCE_MISSING",),
        )

    eligible: list[ForecastReplacementEvidence] = []
    blocked_reasons: set[str] = set()
    for row in evidence:
        product = replacement_forecast_product(row.label)
        if product.trade_authority_status == "LIVE_AUTHORITY":
            blocked_reasons.add("BASELINE_NOT_REPLACEMENT_CANDIDATE")
            continue
        if product.trade_authority_status not in {"SHADOW_ONLY", "COMPARATOR_ONLY"}:
            blocked_reasons.add("PRODUCT_NOT_SHADOW_EVALUABLE")
            continue
        if row.settled_decisions < min_settled_decisions:
            blocked_reasons.add("INSUFFICIENT_SETTLED_DECISIONS")
            continue
        if row.anti_lookahead_violations:
            blocked_reasons.add("ANTI_LOOKAHEAD_VIOLATION")
            continue
        if row.availability_violations:
            blocked_reasons.add("DECISION_TIME_AVAILABILITY_VIOLATION")
            continue
        if row.q_lcb_coverage < min_q_lcb_coverage:
            blocked_reasons.add("QLCB_COVERAGE_INSUFFICIENT")
            continue
        if row.after_cost_pnl <= 0:
            blocked_reasons.add("AFTER_COST_PNL_NOT_POSITIVE")
            continue
        eligible.append(row)

    if not eligible:
        return ForecastReplacementSelection(
            status="NO_PROMOTION_CANDIDATE",
            selected_label=None,
            reason_codes=tuple(sorted(blocked_reasons)) or ("NO_ELIGIBLE_CANDIDATES",),
        )

    winner = max(
        eligible,
        key=lambda row: (
            row.after_cost_pnl,
            -row.max_drawdown,
            -row.log_loss,
            -row.brier,
            row.q_lcb_coverage,
        ),
    )
    return ForecastReplacementSelection(
        status="PROMOTION_CANDIDATE",
        selected_label=winner.label,
        reason_codes=("EMPIRICAL_WINNER_AFTER_COST",),
    )


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
