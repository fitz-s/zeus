"""Day0 live-authority checks for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

from src.contracts.settlement_semantics import SettlementSemantics


class Day0AuthorityError(ValueError):
    """Raised when a Day0 observation cannot authorize live hard facts."""


DAY0_LIVE_AUTHORITY_MATCHES = {
    "source_match_status": "MATCH",
    "local_date_status": "MATCH",
    "station_match_status": "MATCH",
    "dst_status": "UNAMBIGUOUS",
    "metric_match_status": "MATCH",
    "rounding_status": "MATCH",
    "source_authorized_status": "AUTHORIZED",
    "live_authority_status": "live",
}

DAY0_REMAINING_DAY_Q_SOURCE = "day0_remaining_day"
DAY0_REMAINING_DAY_Q_MODE = "remaining_day"
DAY0_OBSERVATION_HARD_FACT_AUTHORITY = "DAY0_LIVE_OBSERVATION_HARD_FACT"
DAY0_REMAINING_DAY_Q_LCB_GUARD_BASIS = "DAY0_REMAINING_DAY_Q_LCB"
DAY0_OBSERVED_BOUNDARY_GUARD_BASIS = "DAY0_OBSERVED_BOUNDARY"
DAY0_REMAINING_DAY_LCB_Z = 1.6448536269514722
DAY0_REMAINING_DAY_LCB_TOLERANCE = 1e-6


def normalize_day0_live_authority_status(value: object, *, default: str = "UNKNOWN") -> str:
    """Normalize durable Day0 authority status at read boundaries.

    New writers emit only ``live`` or ``blocked``. The upper-case aliases are
    accepted only to drain pre-cutover durable rows/events safely after restart.
    """

    raw = str(value or default)
    if raw == "LIVE_AUTHORITY":
        return "live"
    if raw == "NON_LIVE_AUTHORITY":
        return "blocked"
    return raw


def day0_live_payload_authority_errors(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Return mismatched live Day0 authority fields for an already-built payload."""

    errors: list[str] = []
    for field_name, expected in DAY0_LIVE_AUTHORITY_MATCHES.items():
        observed = payload.get(field_name)
        if observed in (None, ""):
            observed_value = ""
        elif field_name == "live_authority_status":
            observed_value = normalize_day0_live_authority_status(observed)
        else:
            observed_value = str(observed or "").strip()
        if observed_value != expected:
            errors.append(f"{field_name}={observed_value or 'missing'}")
    return tuple(errors)


def assert_live_day0_payload_authority(payload: Mapping[str, object]) -> None:
    """Fail closed unless a payload carries the live Day0 observation authority contract."""

    errors = day0_live_payload_authority_errors(payload)
    if errors:
        raise Day0AuthorityError(",".join(errors))


def _day0_probability_block(payload: Mapping[str, object]) -> Mapping[str, object]:
    block = payload.get("day0_probability_authority")
    return block if isinstance(block, Mapping) else {}


def _first_text(payload: Mapping[str, object], block: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            value = block.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _first_float(payload: Mapping[str, object], block: Mapping[str, object], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            value = block.get(key)
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise Day0AuthorityError(f"{key} is not numeric") from None
        if not math.isfinite(number):
            raise Day0AuthorityError(f"{key} is not finite")
        return number
    return None


def _first_int(payload: Mapping[str, object], block: Mapping[str, object], *keys: str) -> int | None:
    number = _first_float(payload, block, *keys)
    if number is None:
        return None
    return int(number)


def _day0_lcb_transform(payload: Mapping[str, object], block: Mapping[str, object]) -> Mapping[str, object]:
    transform = payload.get("_edli_day0_lcb_transform")
    if not isinstance(transform, Mapping):
        transform = payload.get("day0_lcb_transform")
    if not isinstance(transform, Mapping):
        transform = block.get("lcb_transform")
    if not isinstance(transform, Mapping):
        raise Day0AuthorityError("remaining_day_lcb_transform missing")
    return transform


def _one_sided_wilson_lcb(p_hat: float, n: int, *, z: float = DAY0_REMAINING_DAY_LCB_Z) -> float:
    if n <= 0:
        raise Day0AuthorityError("remaining_day_models missing")
    p = min(max(float(p_hat), 0.0), 1.0)
    denominator = 1.0 + (z * z) / float(n)
    center = p + (z * z) / (2.0 * float(n))
    radius = z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * float(n))) / float(n))
    return min(max((center - radius) / denominator, 0.0), p)


def _assert_remaining_day_lcb_is_statistically_supported(
    *,
    q_live: float,
    q_lcb: float,
    remaining_models: int,
) -> None:
    if q_lcb >= q_live - DAY0_REMAINING_DAY_LCB_TOLERANCE:
        raise Day0AuthorityError("remaining_day q_lcb is degenerate with q_live")
    supported_lcb = _one_sided_wilson_lcb(q_live, remaining_models)
    if q_lcb > supported_lcb + DAY0_REMAINING_DAY_LCB_TOLERANCE:
        raise Day0AuthorityError(
            "remaining_day q_lcb exceeds model-count Wilson lower bound:"
            f"q_live={q_live:.12g}:q_lcb={q_lcb:.12g}:"
            f"remaining_models={remaining_models}:wilson_lcb={supported_lcb:.12g}"
        )


def assert_live_day0_probability_authority(
    payload: Mapping[str, object],
    *,
    direction: object | None = None,
    condition_id: object | None = None,
    q_live: float | None = None,
    q_lcb: float | None = None,
) -> None:
    """Fail closed unless Day0 entry probability is remaining-window qkernel evidence.

    A live Day0 observation can authoritatively mask already-impossible bins, but it is
    not itself an exact-bin probability model. Live entry submit therefore needs proof
    that q/q_lcb came from the remaining-day probability surface and that the selected
    side's q_lcb equals the Day0 LCB transform for the selected condition.
    """

    block = _day0_probability_block(payload)
    authority = _first_text(payload, block, "authority", "calibration_authority")
    calibration_method = _first_text(payload, block, "calibration_method")
    input_space = _first_text(payload, block, "input_space")
    hard_fact_markers = {authority.upper(), calibration_method.lower(), input_space.lower()}
    if DAY0_OBSERVATION_HARD_FACT_AUTHORITY in hard_fact_markers or "day0_live_observation_hard_fact" in hard_fact_markers:
        raise Day0AuthorityError("day0 hard-fact calibration cannot authorize entry probability")

    q_source = _first_text(payload, block, "_edli_q_source", "day0_q_source", "q_source")
    if q_source != DAY0_REMAINING_DAY_Q_SOURCE:
        raise Day0AuthorityError(f"remaining_day_q_source required:{q_source or 'missing'}")
    q_mode = _first_text(payload, block, "_edli_day0_q_mode", "day0_q_mode", "q_mode")
    if q_mode != DAY0_REMAINING_DAY_Q_MODE:
        raise Day0AuthorityError(f"remaining_day_q_mode required:{q_mode or 'missing'}")
    remaining_models = _first_int(
        payload,
        block,
        "_edli_day0_remaining_models",
        "day0_remaining_models",
        "remaining_models",
    )
    if remaining_models is None or remaining_models <= 0:
        raise Day0AuthorityError("remaining_day_models missing")
    rounded_value = _first_float(payload, block, "rounded_value", "rounded_extreme")
    if rounded_value is None:
        raise Day0AuthorityError("remaining_day_observed_extreme missing")
    observation_time = _first_text(payload, block, "observation_time", "observation_available_at")
    if not observation_time:
        raise Day0AuthorityError("remaining_day_observation_time missing")

    transform = _day0_lcb_transform(payload, block)
    if q_lcb is None:
        return
    if q_live is not None:
        try:
            q_live_value = float(q_live)
            q_lcb_value = float(q_lcb)
        except (TypeError, ValueError):
            raise Day0AuthorityError("remaining_day q_live/q_lcb nonnumeric") from None
        if not (math.isfinite(q_live_value) and math.isfinite(q_lcb_value)):
            raise Day0AuthorityError("remaining_day q_live/q_lcb nonfinite")
        if q_live_value < 0.0 or q_live_value > 1.0 or q_lcb_value < 0.0 or q_lcb_value > 1.0:
            raise Day0AuthorityError("remaining_day q_live/q_lcb out of range")
        _assert_remaining_day_lcb_is_statistically_supported(
            q_live=q_live_value,
            q_lcb=q_lcb_value,
            remaining_models=remaining_models,
        )
    selected_condition = str(condition_id or payload.get("condition_id") or "").strip()
    if not selected_condition:
        raise Day0AuthorityError("selected_condition_id missing")
    direction_text = str(direction or payload.get("direction") or "").strip().lower()
    if direction_text.endswith("_yes"):
        transform_key = "yes_lcb_by_condition"
    elif direction_text.endswith("_no"):
        transform_key = "no_lcb_by_condition"
    else:
        raise Day0AuthorityError(f"selected_direction unsupported:{direction_text or 'missing'}")
    by_condition = transform.get(transform_key)
    if not isinstance(by_condition, Mapping):
        raise Day0AuthorityError(f"{transform_key} missing")
    if selected_condition not in by_condition:
        raise Day0AuthorityError(f"selected_condition_lcb missing:{selected_condition}")
    try:
        transform_lcb = float(by_condition[selected_condition])
    except (TypeError, ValueError):
        raise Day0AuthorityError(f"selected_condition_lcb nonnumeric:{selected_condition}") from None
    if not math.isfinite(transform_lcb):
        raise Day0AuthorityError(f"selected_condition_lcb nonfinite:{selected_condition}")
    if not math.isclose(float(q_lcb), transform_lcb, rel_tol=1e-9, abs_tol=1e-6):
        raise Day0AuthorityError(
            "selected q_lcb does not match remaining-day transform:"
            f"condition_id={selected_condition}:q_lcb={float(q_lcb):.12g}:"
            f"transform_lcb={transform_lcb:.12g}"
        )


def assert_live_day0_qkernel_guard_authority(economics: Mapping[str, object]) -> None:
    """Fail closed unless Day0 entry qkernel evidence uses remaining-day guard law.

    ``DAY0_OBSERVED_BOUNDARY`` is a hard-fact observation boundary. It can rule out
    already-impossible outcomes, but it cannot by itself license an entry on a
    finite bin that merely contains the current running extreme.
    """

    for field_name in ("q_lcb_guard_basis", "selection_guard_basis"):
        basis = str(economics.get(field_name) or "").strip()
        if not basis:
            raise Day0AuthorityError(f"{field_name} missing")
        if basis == DAY0_OBSERVED_BOUNDARY_GUARD_BASIS:
            raise Day0AuthorityError(f"{field_name} cannot be DAY0_OBSERVED_BOUNDARY")
        if basis != DAY0_REMAINING_DAY_Q_LCB_GUARD_BASIS:
            raise Day0AuthorityError(
                f"{field_name} must be {DAY0_REMAINING_DAY_Q_LCB_GUARD_BASIS}"
            )
    for field_name in ("q_lcb_guard_abstained", "selection_guard_abstained"):
        if economics.get(field_name) is not False:
            raise Day0AuthorityError(f"{field_name} must be false")
    try:
        selection_guard_n = int(float(economics.get("selection_guard_n")))
    except (TypeError, ValueError):
        raise Day0AuthorityError("selection_guard_n must be positive") from None
    if selection_guard_n <= 0:
        raise Day0AuthorityError("selection_guard_n must be positive")


@dataclass(frozen=True)
class Day0AuthorityEvidence:
    city: str
    target_date: str
    metric: str
    source_match_status: str
    station_match_status: str
    local_date_status: str
    dst_status: str
    metric_match_status: str
    rounding_status: str
    source_authorized_status: str
    live_authority_status: str
    observation_available_at: str
    observation_time: str
    raw_value: float
    rounded_value: int
    settlement_semantics: SettlementSemantics


def assert_live_day0_authority(evidence: Day0AuthorityEvidence) -> None:
    expected = {
        "live_authority_status": {"live"},
        "source_match_status": {"MATCH"},
        "station_match_status": {"MATCH"},
        "local_date_status": {"MATCH"},
        "dst_status": {"UNAMBIGUOUS", "MATCH"},
        "metric_match_status": {"MATCH"},
        "rounding_status": {"MATCH"},
        "source_authorized_status": {"AUTHORIZED"},
    }
    for field_name, accepted in expected.items():
        value = getattr(evidence, field_name)
        if field_name == "live_authority_status":
            value = normalize_day0_live_authority_status(value)
        if value not in accepted:
            raise Day0AuthorityError(f"{field_name} does not authorize live Day0 fact")
    rounded = int(evidence.settlement_semantics.round_single(evidence.raw_value))
    if rounded != evidence.rounded_value:
        raise Day0AuthorityError("rounded_value does not match SettlementSemantics")


def observability_row_to_authority(row: Mapping[str, object]) -> Day0AuthorityEvidence:
    live_authority_status = normalize_day0_live_authority_status(row.get("live_authority_status"))
    if live_authority_status != "live":
        raise Day0AuthorityError("observability row is not live authority")
    semantics = row.get("settlement_semantics")
    if not isinstance(semantics, SettlementSemantics):
        raise Day0AuthorityError("live authority row must carry SettlementSemantics")
    return Day0AuthorityEvidence(
        city=str(row.get("city") or ""),
        target_date=str(row.get("target_date") or ""),
        metric=str(row.get("metric") or ""),
        source_match_status=str(row.get("source_match_status") or "UNKNOWN"),
        station_match_status=str(row.get("station_match_status") or "UNKNOWN"),
        local_date_status=str(row.get("local_date_status") or "UNKNOWN"),
        dst_status=str(row.get("dst_status") or "UNKNOWN"),
        metric_match_status=str(row.get("metric_match_status") or "UNKNOWN"),
        rounding_status=str(row.get("rounding_status") or "UNKNOWN"),
        source_authorized_status=str(row.get("source_authorized_status") or "UNKNOWN"),
        live_authority_status=live_authority_status,
        observation_available_at=str(row.get("observation_available_at") or ""),
        observation_time=str(row.get("observation_time") or ""),
        raw_value=float(row.get("raw_value") or 0.0),
        rounded_value=int(row.get("rounded_value") or 0),
        settlement_semantics=semantics,
    )
