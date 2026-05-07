"""Forecast calibration domain identity.

This is the Phase 2.5 base contract from may4math: source/cycle/metric/domain
identity is data, not prose. Runtime wiring can adopt it later without changing
the object shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo


Metric = Literal["high", "low"]
ObservationField = Literal["high_temp", "low_temp"]
SettlementUnit = Literal["F", "C"]
CalibrationRoute = Literal[
    "PRIMARY_EXACT",
    "LEGACY_HIGH_ONLY",
    "ON_THE_FLY_HIGH_ONLY",
    "COMPATIBLE_FALLBACK",
    "RAW_UNCALIBRATED",
    "BLOCKED",
]
AttributionStatus = Literal[
    "FULLY_INSIDE_TARGET_LOCAL_DAY",
    "DETERMINISTICALLY_PREVIOUS_LOCAL_DAY",
    "DETERMINISTICALLY_NEXT_LOCAL_DAY",
    "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY",
    "ISSUED_AFTER_RELEVANT_WINDOW",
    "UNKNOWN",
]


class ForecastCalibrationDomainMismatch(ValueError):
    """Raised when a forecast and calibrator describe different domains."""


class ContractOutcomeDomainMismatch(ValueError):
    """Raised when calibration evidence points at a different contract object."""


def _require_nonempty(value: str | None, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def _expected_observation_field(metric: Metric) -> ObservationField:
    return "high_temp" if metric == "high" else "low_temp"


def _parse_datetime(value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        result = datetime.fromisoformat(text)
    else:
        raise ValueError(f"{field} is required")
    if result.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return result


def _optional_datetime(payload: dict, *fields: str) -> datetime | None:
    for field in fields:
        value = payload.get(field)
        if value not in (None, ""):
            return _parse_datetime(value, field)
    return None


def _boundary_policy(payload: dict) -> dict:
    raw = payload.get("boundary_policy")
    return raw if isinstance(raw, dict) else {}


def _classify_window_attribution(
    *,
    contract_domain: "ContractOutcomeDomain",
    window_start_local: datetime,
    window_end_local: datetime,
) -> tuple[AttributionStatus, float]:
    tz = ZoneInfo(contract_domain.city_timezone)
    day_start = datetime.combine(contract_domain.target_local_date, time.min, tzinfo=tz)
    day_end = datetime.combine(
        contract_domain.target_local_date.fromordinal(
            contract_domain.target_local_date.toordinal() + 1
        ),
        time.min,
        tzinfo=tz,
    )
    start = window_start_local.astimezone(tz)
    end = window_end_local.astimezone(tz)
    overlap_start = max(start, day_start)
    overlap_end = min(end, day_end)
    overlap_seconds = (
        overlap_end.astimezone(timezone.utc) - overlap_start.astimezone(timezone.utc)
    ).total_seconds()
    overlap_hours = max(0.0, overlap_seconds / 3600.0)
    if start >= day_start and end <= day_end:
        return "FULLY_INSIDE_TARGET_LOCAL_DAY", overlap_hours
    if end <= day_start:
        return "DETERMINISTICALLY_PREVIOUS_LOCAL_DAY", overlap_hours
    if start >= day_end:
        return "DETERMINISTICALLY_NEXT_LOCAL_DAY", overlap_hours
    return "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY", overlap_hours


def _single_local_date_for_half_open_interval(
    *,
    timezone_name: str,
    window_start_local: datetime,
    window_end_local: datetime,
) -> date | None:
    """Return the one local date containing ``[start, end)`` if it exists."""
    tz = ZoneInfo(timezone_name)
    start = window_start_local.astimezone(tz)
    end = window_end_local.astimezone(tz)
    if start >= end:
        return None
    end_exclusive = (
        end.astimezone(timezone.utc) - timedelta(microseconds=1)
    ).astimezone(tz)
    if start.date() != end_exclusive.date():
        return None
    return start.date()


@dataclass(frozen=True, slots=True)
class ContractOutcomeDomain:
    """Trading-object identity for calibration evidence.

    This is the contract/bin outcome family, not a weather-window object.  It
    binds the metric, local settlement date, observation field, settlement
    source semantics, unit, and canonical bin grid that a p_raw/p_cal vector
    must serve before it can claim live calibration authority.
    """

    city: str
    target_local_date: date
    city_timezone: str
    temperature_metric: Metric
    observation_field: ObservationField
    settlement_source_type: str
    settlement_station_id: str | None
    settlement_unit: SettlementUnit
    settlement_rounding_policy: str
    bin_grid_id: str
    bin_schema_version: str
    market_id: str | None = None
    condition_id: str | None = None
    token_id_yes: str | None = None
    token_id_no: str | None = None
    candidate_bin_label: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.city, "city")
        _require_nonempty(self.city_timezone, "city_timezone")
        if self.temperature_metric not in ("high", "low"):
            raise ValueError(f"temperature_metric must be high or low, got {self.temperature_metric!r}")
        expected_field = _expected_observation_field(self.temperature_metric)
        if self.observation_field != expected_field:
            raise ContractOutcomeDomainMismatch(
                f"{self.temperature_metric} must bind {expected_field}, got {self.observation_field}"
            )
        _require_nonempty(self.settlement_source_type, "settlement_source_type")
        if self.settlement_unit not in ("F", "C"):
            raise ValueError(f"settlement_unit must be F or C, got {self.settlement_unit!r}")
        _require_nonempty(self.settlement_rounding_policy, "settlement_rounding_policy")
        _require_nonempty(self.bin_grid_id, "bin_grid_id")
        _require_nonempty(self.bin_schema_version, "bin_schema_version")

    @property
    def family_key(self) -> str:
        station = self.settlement_station_id or "station_na"
        return ":".join((
            self.city,
            self.target_local_date.isoformat(),
            self.city_timezone,
            self.temperature_metric,
            self.observation_field,
            self.settlement_source_type,
            station,
            self.settlement_unit,
            self.settlement_rounding_policy,
            self.bin_grid_id,
            self.bin_schema_version,
        ))

    def mismatch_fields(self, other: "ContractOutcomeDomain") -> tuple[str, ...]:
        fields = (
            "city",
            "target_local_date",
            "city_timezone",
            "temperature_metric",
            "observation_field",
            "settlement_source_type",
            "settlement_station_id",
            "settlement_unit",
            "settlement_rounding_policy",
            "bin_grid_id",
            "bin_schema_version",
        )
        return tuple(field for field in fields if getattr(self, field) != getattr(other, field))


@dataclass(frozen=True, slots=True)
class ForecastToBinEvidence:
    """Forecast-window evidence bound to a contract outcome domain.

    Ambiguous or adjacent-day evidence may be useful for shadow research, but it
    cannot be marked training/live allowed here.  Later weighting work can add
    precision weights only after this object proves the contract outcome family.
    """

    contract_domain: ContractOutcomeDomain
    forecast_source_id: str
    data_version: str
    issue_time_utc: datetime
    cycle_hour_utc: int
    horizon_profile: str
    physical_quantity: str
    aggregation_window_hours: int
    window_start_utc: datetime
    window_end_utc: datetime
    window_start_local: datetime
    window_end_local: datetime
    local_day_overlap_hours: float
    attribution_status: AttributionStatus
    contributes_to_target_extrema: bool
    training_allowed: bool
    live_allowed: bool
    block_reasons: tuple[str, ...] = ()

    @property
    def deterministic_local_date(self) -> date | None:
        """The single city-local date containing this forecast window, if any."""
        return _single_local_date_for_half_open_interval(
            timezone_name=self.contract_domain.city_timezone,
            window_start_local=self.window_start_local,
            window_end_local=self.window_end_local,
        )

    @property
    def reassignment_candidate_local_date(self) -> date | None:
        """Adjacent local date if this is deterministic but not the requested date.

        This does not authorize training.  It is the shadow recovery marker that
        says a later revision path may relabel the target date with explicit
        provenance instead of silently accepting the row.
        """
        if self.attribution_status not in {
            "DETERMINISTICALLY_PREVIOUS_LOCAL_DAY",
            "DETERMINISTICALLY_NEXT_LOCAL_DAY",
        }:
            return None
        local_date = self.deterministic_local_date
        if local_date is None:
            return None
        delta_days = local_date.toordinal() - self.contract_domain.target_local_date.toordinal()
        if delta_days not in (-1, 1):
            return None
        return local_date

    @property
    def is_deterministic_reassignment_candidate(self) -> bool:
        return self.reassignment_candidate_local_date is not None

    @classmethod
    def from_snapshot_payload(
        cls,
        contract_domain: ContractOutcomeDomain,
        payload: dict,
        *,
        live_allowed: bool = False,
    ) -> "ForecastToBinEvidence":
        """Build shadow evidence from an ingest/extractor payload.

        The factory intentionally requires explicit forecast-window fields.  It
        does not treat ``local_day_start_utc`` / ``local_day_end_utc`` as the
        6-hour mn2t6/mx2t6 product window, because those fields describe the
        target local-day envelope rather than the member extrema interval.
        """

        issue_time = _parse_datetime(payload.get("issue_time_utc"), "issue_time_utc")
        start_utc = _optional_datetime(
            payload,
            "forecast_window_start_utc",
            "window_start_utc",
        )
        end_utc = _optional_datetime(
            payload,
            "forecast_window_end_utc",
            "window_end_utc",
        )
        start_local = _optional_datetime(
            payload,
            "forecast_window_start_local",
            "window_start_local",
        )
        end_local = _optional_datetime(
            payload,
            "forecast_window_end_local",
            "window_end_local",
        )
        block_reasons: list[str] = []
        if start_utc is None or end_utc is None or start_local is None or end_local is None:
            attribution_status: AttributionStatus = "UNKNOWN"
            overlap_hours = 0.0
            contributes = False
            training_allowed = False
            block_reasons.append("missing_explicit_forecast_window_evidence")
        else:
            attribution_status, overlap_hours = _classify_window_attribution(
                contract_domain=contract_domain,
                window_start_local=start_local,
                window_end_local=end_local,
            )
            contributes = attribution_status == "FULLY_INSIDE_TARGET_LOCAL_DAY"
            training_allowed = contributes
            if attribution_status == "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY":
                block_reasons.append("ambiguous_crosses_local_day_boundary")
            elif attribution_status in {
                "DETERMINISTICALLY_PREVIOUS_LOCAL_DAY",
                "DETERMINISTICALLY_NEXT_LOCAL_DAY",
            }:
                block_reasons.append("deterministic_reassignment_requires_revision")
            if issue_time > start_utc:
                attribution_status = "ISSUED_AFTER_RELEVANT_WINDOW"
                contributes = False
                training_allowed = False
                block_reasons.append("issued_after_relevant_window")
        boundary_ambiguous = bool(_boundary_policy(payload).get("boundary_ambiguous", False))
        if boundary_ambiguous:
            attribution_status = "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY"
            contributes = False
            training_allowed = False
            block_reasons.append("boundary_ambiguous")
        causality = payload.get("causality") if isinstance(payload.get("causality"), dict) else {}
        if causality.get("status") and causality.get("status") != "OK":
            training_allowed = False
            contributes = False
            block_reasons.append(f"causality_status_{causality.get('status')}")
        if not training_allowed and live_allowed:
            block_reasons.append("live_requested_without_training_authority")

        try:
            cycle_hour = int(payload.get("cycle_hour_utc", issue_time.hour))
        except (TypeError, ValueError):
            cycle_hour = issue_time.hour

        aggregation_hours = int(payload.get("aggregation_window_hours") or 6)
        fallback_end = issue_time + timedelta(hours=aggregation_hours)
        return cls(
            contract_domain=contract_domain,
            forecast_source_id=_require_nonempty(
                payload.get("forecast_source_id") or payload.get("source_id"),
                "forecast_source_id",
            ),
            data_version=_require_nonempty(payload.get("data_version"), "data_version"),
            issue_time_utc=issue_time,
            cycle_hour_utc=cycle_hour,
            horizon_profile=_require_nonempty(
                payload.get("horizon_profile") or "unknown",
                "horizon_profile",
            ),
            physical_quantity=_require_nonempty(payload.get("physical_quantity"), "physical_quantity"),
            aggregation_window_hours=aggregation_hours,
            window_start_utc=start_utc or issue_time,
            window_end_utc=end_utc or fallback_end,
            window_start_local=start_local or issue_time,
            window_end_local=end_local or fallback_end,
            local_day_overlap_hours=overlap_hours,
            attribution_status=attribution_status,
            contributes_to_target_extrema=contributes,
            training_allowed=training_allowed,
            live_allowed=live_allowed and training_allowed and not block_reasons,
            block_reasons=tuple(dict.fromkeys(block_reasons)),
        )

    def __post_init__(self) -> None:
        _require_nonempty(self.forecast_source_id, "forecast_source_id")
        _require_nonempty(self.data_version, "data_version")
        if not 0 <= self.cycle_hour_utc <= 23:
            raise ValueError(f"cycle_hour_utc must be in [0, 23], got {self.cycle_hour_utc}")
        _require_nonempty(self.horizon_profile, "horizon_profile")
        _require_nonempty(self.physical_quantity, "physical_quantity")
        if self.aggregation_window_hours <= 0:
            raise ValueError("aggregation_window_hours must be positive")
        if self.window_start_utc >= self.window_end_utc:
            raise ValueError("window_start_utc must be before window_end_utc")
        if self.window_start_local >= self.window_end_local:
            raise ValueError("window_start_local must be before window_end_local")
        if self.local_day_overlap_hours < 0:
            raise ValueError("local_day_overlap_hours must be non-negative")
        if self.training_allowed:
            if self.attribution_status != "FULLY_INSIDE_TARGET_LOCAL_DAY":
                raise ContractOutcomeDomainMismatch(
                    f"{self.attribution_status} cannot be training_allowed; "
                    "requires FULLY_INSIDE_TARGET_LOCAL_DAY"
                )
            if not self.contributes_to_target_extrema:
                raise ContractOutcomeDomainMismatch(
                    "training_allowed requires contributes_to_target_extrema=True"
                )
        blocking_statuses = {
            "DETERMINISTICALLY_PREVIOUS_LOCAL_DAY",
            "DETERMINISTICALLY_NEXT_LOCAL_DAY",
            "AMBIGUOUS_CROSSES_LOCAL_DAY_BOUNDARY",
            "ISSUED_AFTER_RELEVANT_WINDOW",
            "UNKNOWN",
        }
        if self.attribution_status in blocking_statuses:
            if self.training_allowed or self.live_allowed:
                raise ContractOutcomeDomainMismatch(
                    f"{self.attribution_status} cannot be training/live allowed"
                )
        if self.live_allowed and (not self.training_allowed or self.block_reasons):
            raise ContractOutcomeDomainMismatch(
                "live_allowed requires training_allowed=True and no block_reasons"
            )


@dataclass(frozen=True, slots=True)
class CalibrationAuthorityResult:
    """Read-path authority envelope for p_cal use.

    This is a shadow contract shape for the LOW/HIGH alignment work.  It makes
    fallback authority explicit before evaluator wiring can consume it.
    """

    contract_domain: ContractOutcomeDomain
    requested_calibration_domain: "ForecastCalibrationDomain"
    served_calibration_domain: "ForecastCalibrationDomain | None"
    route: CalibrationRoute
    calibrator_model_key: str | None
    n_eff: int
    n_samples: int
    bin_schema_compatible: bool
    settlement_semantics_compatible: bool
    source_cycle_horizon_compatible: bool
    local_day_construction_compatible: bool
    climate_compatible: bool
    live_eligible: bool
    block_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.n_eff < 0 or self.n_samples < 0:
            raise ValueError("n_eff and n_samples must be non-negative")
        if self.route in {"PRIMARY_EXACT", "LEGACY_HIGH_ONLY", "ON_THE_FLY_HIGH_ONLY", "COMPATIBLE_FALLBACK"}:
            if self.served_calibration_domain is None:
                raise ForecastCalibrationDomainMismatch(f"{self.route} requires served_calibration_domain")
            if self.calibrator_model_key is None:
                raise ForecastCalibrationDomainMismatch(f"{self.route} requires calibrator_model_key")
        compatibility = (
            self.bin_schema_compatible,
            self.settlement_semantics_compatible,
            self.source_cycle_horizon_compatible,
            self.local_day_construction_compatible,
            self.climate_compatible,
        )
        if self.live_eligible:
            if self.route not in {"PRIMARY_EXACT", "COMPATIBLE_FALLBACK"}:
                raise ForecastCalibrationDomainMismatch(
                    f"{self.route} cannot be live eligible under the authority envelope"
                )
            if not all(compatibility):
                raise ContractOutcomeDomainMismatch("live eligibility requires all compatibility gates")
            if self.block_reasons:
                raise ContractOutcomeDomainMismatch("live eligibility cannot carry block_reasons")


@dataclass(frozen=True, slots=True)
class ForecastCalibrationDomain:
    source_id: str
    data_version: str
    source_cycle_hour_utc: int
    horizon_profile: str
    metric: Metric
    cluster: str
    season: str
    input_space: str
    city_local_cycle_hour: int | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id is required")
        if not self.data_version.strip():
            raise ValueError("data_version is required")
        if not 0 <= self.source_cycle_hour_utc <= 23:
            raise ValueError(
                f"source_cycle_hour_utc must be in [0, 23], got {self.source_cycle_hour_utc}"
            )
        if self.metric not in ("high", "low"):
            raise ValueError(f"metric must be 'high' or 'low', got {self.metric!r}")
        if not self.cluster.strip():
            raise ValueError("cluster is required")
        if not self.season.strip():
            raise ValueError("season is required")
        if not self.input_space.strip():
            raise ValueError("input_space is required")
        if self.city_local_cycle_hour is not None and not 0 <= self.city_local_cycle_hour <= 23:
            raise ValueError(
                f"city_local_cycle_hour must be in [0, 23], got {self.city_local_cycle_hour}"
            )

    @property
    def key(self) -> str:
        local_hour = "na" if self.city_local_cycle_hour is None else f"{self.city_local_cycle_hour:02d}"
        return ":".join((
            self.source_id,
            self.data_version,
            f"cycle{self.source_cycle_hour_utc:02d}z",
            self.horizon_profile,
            self.metric,
            self.cluster,
            self.season,
            self.input_space,
            f"local{local_hour}",
        ))

    def mismatch_fields(self, other: "ForecastCalibrationDomain") -> tuple[str, ...]:
        fields = (
            "source_id",
            "data_version",
            "source_cycle_hour_utc",
            "horizon_profile",
            "metric",
            "cluster",
            "season",
            "input_space",
            "city_local_cycle_hour",
        )
        return tuple(field for field in fields if getattr(self, field) != getattr(other, field))

    def matches(self, other: "ForecastCalibrationDomain") -> bool:
        return not self.mismatch_fields(other)

    def assert_matches(self, other: "ForecastCalibrationDomain") -> None:
        mismatches = self.mismatch_fields(other)
        if mismatches:
            raise ForecastCalibrationDomainMismatch(
                "CALIBRATION_DOMAIN_MISMATCH: " + ",".join(mismatches)
            )


# ----------------------------------------------------------------------
# PR #55 helpers (preserved through PR #56 merge): standalone utilities
# that don't depend on the domain class above. Used by evaluator and
# monitor_refresh to thread Phase-2 stratification keys from ens_result
# into get_calibrator. Kept here so callers have one canonical import
# site for forecast-domain-related helpers.
# ----------------------------------------------------------------------

import re as _re
from typing import Optional

_ISO_HHMM_RE = _re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})")


def parse_cycle_from_issue_time(issue_time_iso: Optional[str]) -> Optional[str]:
    """Extract cycle_hour_utc (2-char HH) from an ISO-8601 issue_time string.

    Returns None for unparseable input.  Tolerates trailing timezone
    designators including 'Z' and '+HH:MM'.
    """
    if not isinstance(issue_time_iso, str):
        return None
    match = _ISO_HHMM_RE.match(issue_time_iso)
    if match is None:
        return None
    return match.group(4)


def derive_phase2_keys_from_ens_result(
    ens_result: Optional[dict],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Derive (cycle_hour_utc, source_id, horizon_profile) from an ens_result.

    Phase 2 stratification needs these three keys to look up the right Platt
    bucket. ens_result producers populate ``issue_time`` (str OR datetime,
    depending on whether the registered-ingest path was used) and
    ``source_id``; ``horizon_profile`` is *not* populated upstream as of
    2026-05-04, so we derive it from cycle (00/12 → 'full', else 'short').

    Copilot review #4 + #5 (2026-05-04): horizon_profile derivation when
    ``ens_result['horizon_profile']`` is absent.
    Codex P1 review #7 (2026-05-04): handle ``datetime`` issue_time in
    addition to str — the registered-ingest path puts a datetime here.

    Returns (None, None, None) on malformed input.
    """
    if not isinstance(ens_result, dict):
        return None, None, None
    cycle: Optional[str] = None
    source_id: Optional[str] = None
    horizon_profile: Optional[str] = None
    try:
        it = ens_result.get("issue_time")
        if isinstance(it, str):
            cycle = parse_cycle_from_issue_time(it)
        elif hasattr(it, "hour"):
            cycle = f"{int(it.hour):02d}"
        sid = ens_result.get("source_id")
        if isinstance(sid, str) and sid:
            source_id = sid
        hp = ens_result.get("horizon_profile")
        if isinstance(hp, str) and hp:
            horizon_profile = hp
        if horizon_profile is None and cycle is not None:
            # 00/12 → full-horizon TIGGE/OpenData runs; other cycles → short.
            horizon_profile = "full" if cycle in ("00", "12") else "short"
    except (TypeError, AttributeError, KeyError):
        return None, None, None
    return cycle, source_id, horizon_profile


def derive_source_id_from_data_version(data_version: Optional[str]) -> Optional[str]:
    """Map a data_version string to its canonical source_id.

    'tigge_*'           → 'tigge_mars'
    'ecmwf_opendata_*'  → 'ecmwf_open_data'
    anything else       → None  (caller should reject as
                                 UNKNOWN_FORECAST_SOURCE_FAMILY)
    """
    if not isinstance(data_version, str) or not data_version:
        return None
    if data_version.startswith("tigge_"):
        return "tigge_mars"
    if data_version.startswith("ecmwf_opendata_"):
        return "ecmwf_open_data"
    return None
