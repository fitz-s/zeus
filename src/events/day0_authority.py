"""Day0 live-authority checks for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
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
DAY0_DETERMINISTIC_BIN_PAYOFF_Q_SOURCE = "day0_deterministic_bin_payoff"
DAY0_DETERMINISTIC_BIN_PAYOFF_Q_MODE = "deterministic_bin_payoff"
DAY0_DETERMINISTIC_BIN_PAYOFF_GLOBAL_AUTHORITY = (
    "day0_deterministic_bin_payoff_v1"
)
DAY0_REPLACEMENT_Q_SOURCE = "replacement_0_1"
DAY0_REPLACEMENT_GLOBAL_AUTHORITY = "replacement_current_global_probability_v1"
DAY0_REPLACEMENT_GLOBAL_GUARD_BASIS = "CURRENT_POSTERIOR_BAND"
DAY0_OBSERVATION_HARD_FACT_AUTHORITY = "DAY0_LIVE_OBSERVATION_HARD_FACT"
DAY0_REMAINING_DAY_Q_LCB_GUARD_BASIS = "DAY0_REMAINING_DAY_Q_LCB"
DAY0_OBSERVED_BOUNDARY_GUARD_BASIS = "DAY0_OBSERVED_BOUNDARY"
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


def _truthy_false(value: object) -> bool:
    if isinstance(value, bool):
        return value is False
    if value in (None, ""):
        return False
    return str(value).strip().lower() in {"0", "false", "no"}


def _remaining_day_lcb_has_guarded_qkernel_lcb(
    payload: Mapping[str, object],
    *,
    q_live: float,
    q_lcb: float,
) -> bool:
    """Return whether qkernel guard evidence licenses q_lcb == q_live.

    A remaining-day transform can be numerically degenerate when the selected
    route's explicit 95% guard supplies the conservative bound. Only the Day0
    remaining-window guard and explicit OOF 95% bases qualify; observed-boundary
    and inert pass-through evidence must keep the ordinary non-degenerate
    lower-bound requirement.
    """

    economics = payload.get("qkernel_execution_economics")
    if not isinstance(economics, Mapping):
        return False
    guarded_bases = {
        DAY0_REMAINING_DAY_Q_LCB_GUARD_BASIS,
        "OOF_WILSON_95",
        "OOF_WILSON_95_POOLED_TAIL",
    }
    q_lcb_basis = str(economics.get("q_lcb_guard_basis") or "").strip()
    selection_basis = str(economics.get("selection_guard_basis") or "").strip()
    if q_lcb_basis not in guarded_bases or selection_basis not in guarded_bases:
        return False
    if not (
        _truthy_false(economics.get("q_lcb_guard_abstained"))
        and _truthy_false(economics.get("selection_guard_abstained"))
    ):
        return False
    try:
        payoff_q_point = float(economics.get("payoff_q_point"))
        payoff_q_lcb = float(economics.get("payoff_q_lcb"))
        selection_guard_q_safe = float(economics.get("selection_guard_q_safe"))
    except (TypeError, ValueError):
        return False
    if not all(
        math.isfinite(v)
        for v in (payoff_q_point, payoff_q_lcb, selection_guard_q_safe)
    ):
        return False
    return (
        selection_guard_q_safe > 0.0
        and math.isclose(payoff_q_point, q_live, rel_tol=1e-9, abs_tol=1e-6)
        and math.isclose(payoff_q_lcb, q_lcb, rel_tol=1e-9, abs_tol=1e-6)
    )


def _remaining_day_lcb_has_current_band_tightening(
    payload: Mapping[str, object],
    *,
    q_live: float,
    q_lcb: float,
) -> bool:
    """License only a non-degenerate, identity-bound tightening of the local transform."""

    economics = payload.get("qkernel_execution_economics")
    if not isinstance(economics, Mapping):
        return False
    if (
        str(economics.get("q_lcb_guard_basis") or "") != "CURRENT_POSTERIOR_BAND"
        or str(economics.get("selection_guard_basis") or "")
        != "CURRENT_POSTERIOR_BAND"
        or not str(economics.get("current_state_identity_hash") or "").strip()
        or not _truthy_false(economics.get("q_lcb_guard_abstained"))
        or not _truthy_false(economics.get("selection_guard_abstained"))
    ):
        return False
    try:
        payoff_q_point = float(economics.get("payoff_q_point"))
        payoff_q_lcb = float(economics.get("payoff_q_lcb"))
        selection_guard_q_safe = float(economics.get("selection_guard_q_safe"))
        selection_guard_n = int(economics.get("selection_guard_n"))
    except (TypeError, ValueError):
        return False
    return (
        selection_guard_n >= 2
        and selection_guard_q_safe > 0.0
        and q_lcb < q_live - DAY0_REMAINING_DAY_LCB_TOLERANCE
        and math.isclose(payoff_q_point, q_live, rel_tol=1e-9, abs_tol=1e-6)
        and math.isclose(payoff_q_lcb, q_lcb, rel_tol=1e-9, abs_tol=1e-6)
    )


def _remaining_day_lcb_has_current_absorbing_certainty(
    payload: Mapping[str, object],
    *,
    direction: object | None,
    condition_id: object | None,
    q_live: float,
    q_lcb: float,
) -> bool:
    """License exact certainty only when current physical truth absorbs the leg."""

    if q_live != 1.0 or q_lcb != 1.0:
        return False
    selected_condition = str(
        condition_id or payload.get("condition_id") or ""
    ).strip()
    direction_text = str(direction or payload.get("direction") or "").strip().lower()
    transform = payload.get("_edli_day0_lcb_transform")
    if not isinstance(transform, Mapping) or not selected_condition:
        return False
    if direction_text.endswith("_no"):
        absorbing_key = "absorbing_no_conditions"
        finite_absorbing = payload.get(
            "_edli_day0_finite_evidence_absorbing_no_conditions"
        )
        if not isinstance(finite_absorbing, (list, tuple, set)) or (
            selected_condition not in {str(value) for value in finite_absorbing}
        ):
            return False
        expected_side = "NO"
    elif direction_text.endswith("_yes"):
        absorbing_key = "absorbing_yes_conditions"
        expected_side = "YES"
    else:
        return False
    declared = transform.get(absorbing_key)
    if not isinstance(declared, (list, tuple, set)) or (
        selected_condition not in {str(value) for value in declared}
    ):
        return False

    economics = payload.get("qkernel_execution_economics")
    if not isinstance(economics, Mapping):
        return False
    from src.decision_kernel.canonicalization import (
        qkernel_current_state_rejection_reason,
    )

    if qkernel_current_state_rejection_reason(economics) is not None:
        return False
    try:
        payoff_q_point = float(economics.get("payoff_q_point"))
        payoff_q_lcb = float(economics.get("payoff_q_lcb"))
        selection_guard_q_safe = float(economics.get("selection_guard_q_safe"))
    except (TypeError, ValueError):
        return False
    return (
        str(economics.get("side") or "").strip().upper() == expected_side
        and payoff_q_point == q_live
        and payoff_q_lcb == q_lcb
        and selection_guard_q_safe == q_lcb
    )


def _assert_remaining_day_lcb_is_supported_by_transform(
    *,
    payload: Mapping[str, object],
    direction: object | None,
    condition_id: object | None,
    q_live: float,
    q_lcb: float,
) -> None:
    """Validate the selected-side Day0 qLCB shape.

    ``remaining_models`` is evidence that the remaining-window probability was
    built from live model vectors. It is not a binomial success count, so it must
    not be reused as a Wilson denominator. The load-bearing proof is the
    transform identity checked below: selected qLCB must equal the persisted
    remaining-window transform for the selected condition/direction and must not
    degenerate to the point q.
    """

    if (
        q_lcb >= q_live - DAY0_REMAINING_DAY_LCB_TOLERANCE
        and not _remaining_day_lcb_has_guarded_qkernel_lcb(
            payload,
            q_live=q_live,
            q_lcb=q_lcb,
        )
        and not _remaining_day_lcb_has_current_absorbing_certainty(
            payload,
            direction=direction,
            condition_id=condition_id,
            q_live=q_live,
            q_lcb=q_lcb,
        )
    ):
        raise Day0AuthorityError("remaining_day q_lcb is degenerate with q_live")


def _assert_replacement_global_day0_probability_authority(
    payload: Mapping[str, object],
    block: Mapping[str, object],
    *,
    direction: object | None,
    condition_id: object | None,
    q_live: float | None,
    q_lcb: float | None,
) -> None:
    """Validate one current replacement posterior conditioned on current Day0 truth."""

    authority = str(block.get("probability_authority") or "").strip()
    if authority != DAY0_REPLACEMENT_GLOBAL_AUTHORITY:
        raise Day0AuthorityError(
            "replacement_day0_probability_authority required:"
            f"{authority or 'missing'}"
        )
    q_sources = {
        str(value).strip()
        for value in (
            payload.get("_edli_q_source"),
            payload.get("q_source"),
            block.get("q_source"),
        )
        if value not in (None, "")
    }
    if q_sources != {DAY0_REPLACEMENT_Q_SOURCE}:
        raise Day0AuthorityError(
            "replacement_day0_q_source mismatch:"
            f"{sorted(q_sources) if q_sources else 'missing'}"
        )
    observation = block.get("global_current_observation_payload")
    if not isinstance(observation, Mapping):
        raise Day0AuthorityError("replacement_day0_current_observation missing")
    assert_live_day0_payload_authority(observation)
    binding = observation.get("_edli_global_day0_binding")
    if not isinstance(binding, Mapping):
        raise Day0AuthorityError("replacement_day0_binding missing")

    posterior_id = str(payload.get("posterior_id") or "").strip()
    block_posterior_id = str(block.get("posterior_id") or "").strip()
    bound_posterior_id = str(binding.get("posterior_id") or "").strip()
    if (
        not posterior_id
        or posterior_id != block_posterior_id
        or posterior_id != bound_posterior_id
    ):
        raise Day0AuthorityError(
            "replacement_day0_posterior_id mismatch:"
            f"selected={posterior_id or 'missing'}:"
            f"authority={block_posterior_id or 'missing'}:"
            f"bound={bound_posterior_id or 'missing'}"
        )

    for field_name in ("city", "target_date"):
        selected = _first_text(payload, block, field_name)
        bound = str(binding.get(field_name) or "").strip()
        if not selected or selected != bound:
            raise Day0AuthorityError(
                f"replacement_day0_{field_name} mismatch:"
                f"selected={selected or 'missing'}:bound={bound or 'missing'}"
            )

    metric = _first_text(payload, block, "metric", "temperature_metric").lower()
    bound_metric = str(binding.get("metric") or "").strip().lower()
    if metric not in {"high", "low"} or metric != bound_metric:
        raise Day0AuthorityError(
            "replacement_day0_metric mismatch:"
            f"selected={metric or 'missing'}:bound={bound_metric or 'missing'}"
        )

    for field_name in (
        "observation_time",
        "observation_available_at",
        "station_id",
        "settlement_source",
        "settlement_unit",
    ):
        observed = str(observation.get(field_name) or "").strip()
        bound = str(binding.get(field_name) or "").strip()
        if not observed or observed != bound:
            raise Day0AuthorityError(
                f"replacement_day0_{field_name} mismatch:"
                f"observed={observed or 'missing'}:bound={bound or 'missing'}"
            )

    observed_extreme = _first_float(
        observation,
        {},
        "raw_value",
        "observed_extreme_native",
    )
    bound_extreme = _first_float(binding, {}, "observed_extreme_native")
    if (
        observed_extreme is None
        or bound_extreme is None
        or not math.isclose(observed_extreme, bound_extreme, rel_tol=0.0, abs_tol=1e-9)
    ):
        raise Day0AuthorityError("replacement_day0_observed_extreme mismatch")
    observed_samples = _first_int(observation, {}, "sample_count", "samples_count")
    bound_samples = _first_int(binding, {}, "sample_count")
    if (
        observed_samples is None
        or observed_samples <= 0
        or observed_samples != bound_samples
    ):
        raise Day0AuthorityError("replacement_day0_sample_count mismatch")
    observed_rounded = _first_float(observation, {}, "rounded_value")
    bound_rounded = _first_float(binding, {}, "rounded_value")
    if (
        observed_rounded is None
        or bound_rounded is None
        or not math.isclose(observed_rounded, bound_rounded, rel_tol=0.0, abs_tol=1e-9)
    ):
        raise Day0AuthorityError("replacement_day0_rounded_value mismatch")

    if q_lcb is None:
        return
    try:
        q_lcb_value = float(q_lcb)
        q_live_value = float(q_live)
    except (TypeError, ValueError):
        raise Day0AuthorityError("replacement_day0 q_live/q_lcb nonnumeric") from None
    if not (math.isfinite(q_live_value) and math.isfinite(q_lcb_value)):
        raise Day0AuthorityError("replacement_day0 q_live/q_lcb nonfinite")
    if not (0.0 <= q_lcb_value <= q_live_value <= 1.0):
        raise Day0AuthorityError("replacement_day0 q_live/q_lcb out of order")
    selected_condition = str(condition_id or payload.get("condition_id") or "").strip()
    if not selected_condition:
        raise Day0AuthorityError("selected_condition_id missing")
    direction_text = str(direction or payload.get("direction") or "").strip().lower()
    if not direction_text.endswith(("_yes", "_no")):
        raise Day0AuthorityError(
            f"selected_direction unsupported:{direction_text or 'missing'}"
        )


def _matching_text(label: str, *values: object) -> str:
    present = {str(value).strip() for value in values if value not in (None, "")}
    if not present:
        raise Day0AuthorityError(f"{label} missing")
    if len(present) != 1:
        raise Day0AuthorityError(f"{label} mismatch:{sorted(present)}")
    return next(iter(present))


def _matching_float(label: str, *values: object) -> float:
    present: list[float] = []
    for value in values:
        if value in (None, ""):
            continue
        if isinstance(value, bool):
            raise Day0AuthorityError(f"{label} is not numeric")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise Day0AuthorityError(f"{label} is not numeric") from None
        if not math.isfinite(number):
            raise Day0AuthorityError(f"{label} is not finite")
        present.append(number)
    if not present:
        raise Day0AuthorityError(f"{label} missing")
    if any(
        not math.isclose(value, present[0], rel_tol=0.0, abs_tol=1e-9)
        for value in present[1:]
    ):
        raise Day0AuthorityError(f"{label} mismatch")
    return present[0]


def _exact_binary_payoffs(
    payload: Mapping[str, object],
    block: Mapping[str, object],
    observation: Mapping[str, object],
) -> dict[str, float]:
    maps: list[dict[str, float]] = []
    for owner, key in (
        (payload, "_edli_day0_exact_yes_payoffs"),
        (payload, "exact_yes_payoffs"),
        (block, "exact_yes_payoffs"),
        (observation, "_edli_day0_exact_yes_payoffs"),
    ):
        raw = owner.get(key)
        if raw in (None, ""):
            continue
        if not isinstance(raw, Mapping) or not raw:
            raise Day0AuthorityError("deterministic_exact_yes_payoffs invalid")
        normalized: dict[str, float] = {}
        for raw_bin_id, raw_payoff in raw.items():
            bin_id = str(raw_bin_id or "").strip()
            if not bin_id or isinstance(raw_payoff, bool):
                raise Day0AuthorityError("deterministic_exact_yes_payoffs invalid")
            try:
                payoff = float(raw_payoff)
            except (TypeError, ValueError):
                raise Day0AuthorityError(
                    f"deterministic_yes_payoff nonnumeric:{bin_id}"
                ) from None
            if not math.isfinite(payoff) or payoff not in {0.0, 1.0}:
                raise Day0AuthorityError(
                    f"deterministic_yes_payoff nonbinary:{bin_id}"
                )
            normalized[bin_id] = payoff
        maps.append(normalized)
    if not maps:
        raise Day0AuthorityError("deterministic_exact_yes_payoffs missing")
    if any(candidate != maps[0] for candidate in maps[1:]):
        raise Day0AuthorityError("deterministic_exact_yes_payoffs mismatch")
    return maps[0]


def _deterministic_condition_by_bin(
    payload: Mapping[str, object],
    block: Mapping[str, object],
    observation: Mapping[str, object],
) -> dict[str, str]:
    maps: list[dict[str, str]] = []
    for owner, key in (
        (payload, "_edli_day0_condition_by_bin"),
        (payload, "condition_by_bin"),
        (block, "condition_by_bin"),
        (observation, "_edli_day0_condition_by_bin"),
    ):
        raw = owner.get(key)
        if raw in (None, ""):
            continue
        if not isinstance(raw, Mapping) or not raw:
            raise Day0AuthorityError("deterministic_condition_by_bin invalid")
        normalized = {
            str(raw_bin_id or "").strip(): str(raw_condition_id or "").strip()
            for raw_bin_id, raw_condition_id in raw.items()
        }
        if any(not key or not value for key, value in normalized.items()):
            raise Day0AuthorityError("deterministic_condition_by_bin invalid")
        maps.append(normalized)
    if not maps:
        raise Day0AuthorityError("deterministic_condition_by_bin missing")
    if any(candidate != maps[0] for candidate in maps[1:]):
        raise Day0AuthorityError("deterministic_condition_by_bin mismatch")
    return maps[0]


def _deterministic_bindings(
    payload: Mapping[str, object],
    block: Mapping[str, object],
    observation: Mapping[str, object],
) -> tuple[tuple[str, str, str | None, str | None], ...]:
    candidates: list[tuple[tuple[str, str, str | None, str | None], ...]] = []
    for owner, key in (
        (payload, "_edli_day0_deterministic_bindings"),
        (block, "bindings"),
        (observation, "_edli_day0_deterministic_bindings"),
    ):
        raw = owner.get(key)
        if raw in (None, ""):
            continue
        if not isinstance(raw, (list, tuple)) or not raw:
            raise Day0AuthorityError("deterministic_bindings invalid")
        normalized: list[tuple[str, str, str | None, str | None]] = []
        for item in raw:
            if not isinstance(item, Mapping):
                raise Day0AuthorityError("deterministic_bindings invalid")
            bin_id = str(item.get("bin_id") or "").strip()
            condition = str(item.get("condition_id") or "").strip()
            yes_token = str(item.get("yes_token_id") or "").strip() or None
            no_token = str(item.get("no_token_id") or "").strip() or None
            if not bin_id or not condition:
                raise Day0AuthorityError("deterministic_bindings invalid")
            normalized.append((bin_id, condition, yes_token, no_token))
        value = tuple(normalized)
        if len({row[0] for row in value}) != len(value):
            raise Day0AuthorityError("deterministic_bindings duplicate bin")
        candidates.append(value)
    if not candidates:
        raise Day0AuthorityError("deterministic_bindings missing")
    if any(candidate != candidates[0] for candidate in candidates[1:]):
        raise Day0AuthorityError("deterministic_bindings mismatch")
    return candidates[0]


def _assert_deterministic_bin_payoff_authority(
    payload: Mapping[str, object],
    block: Mapping[str, object],
    *,
    direction: object | None,
    condition_id: object | None,
    q_live: float | None,
    q_lcb: float | None,
) -> None:
    observation = block.get("global_current_observation_payload")
    if not isinstance(observation, Mapping) and isinstance(
        payload.get("_edli_global_day0_binding"), Mapping
    ):
        observation = payload
    if not isinstance(observation, Mapping):
        raise Day0AuthorityError("deterministic_current_observation missing")
    assert_live_day0_payload_authority(observation)
    binding = observation.get("_edli_global_day0_binding")
    if not isinstance(binding, Mapping):
        raise Day0AuthorityError("deterministic_day0_binding missing")

    authority = _matching_text(
        "deterministic_probability_authority",
        payload.get("probability_authority"),
        block.get("probability_authority"),
        observation.get("probability_authority"),
    )
    if authority != DAY0_DETERMINISTIC_BIN_PAYOFF_GLOBAL_AUTHORITY:
        raise Day0AuthorityError(
            f"deterministic_probability_authority invalid:{authority}"
        )
    q_source = _matching_text(
        "deterministic_q_source",
        payload.get("_edli_q_source"),
        payload.get("q_source"),
        block.get("q_source"),
        observation.get("q_source"),
        observation.get("_edli_q_source"),
    )
    if q_source != DAY0_DETERMINISTIC_BIN_PAYOFF_Q_SOURCE:
        raise Day0AuthorityError(f"deterministic_q_source invalid:{q_source}")
    q_mode = _matching_text(
        "deterministic_q_mode",
        payload.get("_edli_day0_q_mode"),
        payload.get("q_mode"),
        block.get("q_mode"),
        observation.get("_edli_day0_q_mode"),
        observation.get("q_mode"),
    )
    if q_mode != DAY0_DETERMINISTIC_BIN_PAYOFF_Q_MODE:
        raise Day0AuthorityError(f"deterministic_q_mode invalid:{q_mode}")

    if block:
        required_block_fields = (
            "probability_authority",
            "q_source",
            "q_mode",
            "exact_yes_payoffs",
            "condition_by_bin",
            "witness_identity",
            "q_version",
            "sample_identity",
            "source_truth_identity",
            "authority_certificate_hash",
            "family_key",
            "bindings",
            "resolution_identity",
            "topology_identity",
            "posterior_identity_hash",
            "band_alpha",
            "band_basis",
            "captured_at_utc",
            "selected_condition_id",
            "selected_bin_id",
            "selected_token_id",
            "selected_direction",
            "selected_q_live",
            "selected_q_lcb",
        )
        missing = tuple(
            field_name
            for field_name in required_block_fields
            if block.get(field_name) in (None, "")
        )
        if missing:
            raise Day0AuthorityError(
                f"deterministic_probability_block incomplete:{','.join(missing)}"
            )

    identities: dict[str, str] = {}
    for field_name, top_key in (
        ("q_version", "_edli_day0_deterministic_q_version"),
        ("source_truth_identity", "_edli_day0_deterministic_source_truth_identity"),
        (
            "authority_certificate_hash",
            "_edli_day0_deterministic_authority_certificate_hash",
        ),
    ):
        identities[field_name] = _matching_text(
            f"deterministic_{field_name}",
            payload.get(top_key),
            block.get(field_name),
            observation.get(top_key),
        )

    witness_identity = _matching_text(
        "deterministic_witness_identity",
        payload.get("_edli_day0_deterministic_witness_identity"),
        block.get("witness_identity"),
        observation.get("_edli_day0_deterministic_witness_identity"),
    )
    sample_identity = _matching_text(
        "deterministic_sample_identity",
        payload.get("_edli_day0_deterministic_sample_identity"),
        block.get("sample_identity"),
        observation.get("_edli_day0_deterministic_sample_identity"),
    )

    for field_name in ("city", "target_date"):
        selected = _matching_text(
            f"deterministic_{field_name}",
            payload.get(field_name),
            observation.get(field_name),
        )
        bound = str(binding.get(field_name) or "").strip()
        if selected != bound:
            raise Day0AuthorityError(f"deterministic_{field_name} binding mismatch")
    metric = _matching_text(
        "deterministic_metric",
        payload.get("metric"),
        payload.get("temperature_metric"),
        observation.get("metric"),
        observation.get("temperature_metric"),
    ).lower()
    if metric != str(binding.get("metric") or "").strip().lower():
        raise Day0AuthorityError("deterministic_metric binding mismatch")
    for field_name in (
        "observation_time",
        "observation_available_at",
        "station_id",
        "settlement_source",
        "settlement_unit",
    ):
        _matching_text(
            f"deterministic_{field_name}",
            payload.get(field_name),
            observation.get(field_name),
            binding.get(field_name),
        )
    _matching_float(
        "deterministic_observed_extreme",
        payload.get("raw_value"),
        payload.get("observed_extreme_native"),
        observation.get("raw_value"),
        observation.get("observed_extreme_native"),
        binding.get("observed_extreme_native"),
    )
    _matching_float(
        "deterministic_rounded_value",
        payload.get("rounded_value"),
        observation.get("rounded_value"),
        binding.get("rounded_value"),
    )
    observed_samples = _matching_float(
        "deterministic_sample_count",
        payload.get("sample_count"),
        payload.get("samples_count"),
        observation.get("sample_count"),
        observation.get("samples_count"),
        binding.get("sample_count"),
    )
    if observed_samples <= 0 or not observed_samples.is_integer():
        raise Day0AuthorityError("deterministic_observation binding mismatch")

    selected_condition = _matching_text(
        "deterministic_selected_condition",
        condition_id,
        payload.get("condition_id"),
        block.get("selected_condition_id"),
    )
    selected_bin = _matching_text(
        "deterministic_selected_bin",
        payload.get("candidate_bin_id"),
        block.get("selected_bin_id"),
    )
    selected_direction = _matching_text(
        "deterministic_selected_direction",
        direction,
        payload.get("direction"),
        block.get("selected_direction"),
    ).lower()
    if selected_direction not in {"buy_yes", "buy_no"}:
        raise Day0AuthorityError(
            f"deterministic_selected_direction unsupported:{selected_direction}"
        )
    condition_by_bin = _deterministic_condition_by_bin(payload, block, observation)
    binding_rows = _deterministic_bindings(payload, block, observation)
    bound_conditions = {row[0]: row[1] for row in binding_rows}
    if condition_by_bin != bound_conditions:
        raise Day0AuthorityError("deterministic_condition_by_bin/bindings mismatch")
    if condition_by_bin.get(selected_bin) != selected_condition:
        raise Day0AuthorityError("deterministic_selected_condition/bin mismatch")
    exact_yes_payoffs = _exact_binary_payoffs(payload, block, observation)
    from src.solve.solver import deterministic_bin_payoff_sample_identity

    expected_sample_identity = deterministic_bin_payoff_sample_identity(
        tuple((bin_id, int(payoff)) for bin_id, payoff in exact_yes_payoffs.items())
    )
    if sample_identity != expected_sample_identity:
        raise Day0AuthorityError("deterministic_sample_identity/payoff mismatch")
    if selected_bin not in exact_yes_payoffs:
        raise Day0AuthorityError(
            f"deterministic_selected_payoff missing:{selected_bin}"
        )
    yes_payoff = exact_yes_payoffs[selected_bin]
    expected_q = yes_payoff if selected_direction == "buy_yes" else 1.0 - yes_payoff
    selected_token = _matching_text(
        "deterministic_selected_token",
        payload.get("token_id"),
        block.get("selected_token_id"),
    )
    selected_binding = next(row for row in binding_rows if row[0] == selected_bin)
    expected_token = (
        selected_binding[2]
        if selected_direction == "buy_yes"
        else selected_binding[3]
    )
    if not expected_token or selected_token != expected_token:
        raise Day0AuthorityError("deterministic_selected_token/bin/side mismatch")
    live_q = _first_float(
        {"q_live": q_live},
        payload,
        "q_live",
        "selected_q_live",
    )
    lcb_q = _first_float(
        {"q_lcb": q_lcb},
        payload,
        "q_lcb",
        "q_lcb_5pct",
        "selected_q_lcb",
    )
    block_live_q = _first_float(block, {}, "selected_q_live")
    block_lcb_q = _first_float(block, {}, "selected_q_lcb")
    if live_q is None or lcb_q is None:
        raise Day0AuthorityError("deterministic_selected_q missing")
    bound_q_values = [live_q, lcb_q]
    if block:
        if block_live_q is None or block_lcb_q is None:
            raise Day0AuthorityError("deterministic_selected_q missing")
        bound_q_values.extend((block_live_q, block_lcb_q))
    if not all(
        math.isclose(value, expected_q, rel_tol=0.0, abs_tol=1e-12)
        for value in bound_q_values
    ):
        raise Day0AuthorityError(
            "deterministic_selected_q/payoff mismatch:"
            f"expected={expected_q}:q_live={live_q}:q_lcb={lcb_q}"
        )

    family_key = _matching_text(
        "deterministic_family_key",
        payload.get("_edli_day0_deterministic_family_key"),
        payload.get("family_id"),
        block.get("family_key"),
        observation.get("_edli_day0_deterministic_family_key"),
    )
    resolution_identity = _matching_text(
        "deterministic_resolution_identity",
        payload.get("_edli_day0_deterministic_resolution_identity"),
        block.get("resolution_identity"),
        observation.get("_edli_day0_deterministic_resolution_identity"),
    )
    topology_identity = _matching_text(
        "deterministic_topology_identity",
        payload.get("_edli_day0_deterministic_topology_identity"),
        block.get("topology_identity"),
        observation.get("_edli_day0_deterministic_topology_identity"),
    )
    posterior_identity_hash = _matching_text(
        "deterministic_posterior_identity_hash",
        payload.get("_edli_day0_deterministic_posterior_identity_hash"),
        block.get("posterior_identity_hash"),
        observation.get("_edli_day0_deterministic_posterior_identity_hash"),
    )
    band_basis = _matching_text(
        "deterministic_band_basis",
        payload.get("_edli_day0_deterministic_band_basis"),
        block.get("band_basis"),
        observation.get("_edli_day0_deterministic_band_basis"),
    )
    band_alpha_text = _matching_text(
        "deterministic_band_alpha",
        payload.get("_edli_day0_deterministic_band_alpha"),
        block.get("band_alpha"),
        observation.get("_edli_day0_deterministic_band_alpha"),
    )
    captured_text = _matching_text(
        "deterministic_captured_at_utc",
        payload.get("_edli_day0_deterministic_captured_at_utc"),
        block.get("captured_at_utc"),
        observation.get("_edli_day0_deterministic_captured_at_utc"),
    )
    try:
        band_alpha = float(band_alpha_text)
        captured_at = datetime.fromisoformat(captured_text.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise Day0AuthorityError("deterministic_witness_content invalid") from exc
    if captured_at.tzinfo is None:
        raise Day0AuthorityError("deterministic_captured_at_utc invalid")

    from src.solve.solver import DeterministicBinPayoffWitness, OutcomeTokenBinding

    try:
        reconstructed = DeterministicBinPayoffWitness(
            family_key=family_key,
            bindings=tuple(
                OutcomeTokenBinding(
                    bin_id=bin_id,
                    condition_id=bound_condition,
                    yes_token_id=yes_token,
                    no_token_id=no_token,
                )
                for bin_id, bound_condition, yes_token, no_token in binding_rows
            ),
            exact_yes_payoffs=tuple(
                (bin_id, int(payoff))
                for bin_id, payoff in exact_yes_payoffs.items()
            ),
            q_version=identities["q_version"],
            resolution_identity=resolution_identity,
            topology_identity=topology_identity,
            posterior_identity_hash=posterior_identity_hash,
            source_truth_identity=identities["source_truth_identity"],
            authority_certificate_hash=identities["authority_certificate_hash"],
            band_alpha=band_alpha,
            band_basis=band_basis,
            captured_at_utc=captured_at,
            max_age=timedelta(seconds=1),
            witness_identity=witness_identity,
        )
    except (TypeError, ValueError) as exc:
        raise Day0AuthorityError(
            f"deterministic_witness_content mismatch:{exc}"
        ) from None
    if reconstructed.sample_matrix_identity != sample_identity:
        raise Day0AuthorityError("deterministic_sample_identity/content mismatch")


def assert_live_day0_probability_authority(
    payload: Mapping[str, object],
    *,
    direction: object | None = None,
    condition_id: object | None = None,
    q_live: float | None = None,
    q_lcb: float | None = None,
) -> None:
    """Fail closed unless Day0 entry probability has one current typed authority.

    A live Day0 observation can authoritatively mask already-impossible bins, but it is
    not itself an exact-bin probability model. Live entry submit therefore needs a
    current remaining-window surface, a current replacement posterior conditioned on
    the observation, or an exact pathwise payoff witness for the selected bin. Every
    route uses the same selected-side q/q_lcb contract.
    """

    block = _day0_probability_block(payload)
    authority = _first_text(payload, block, "authority", "calibration_authority")
    calibration_method = _first_text(payload, block, "calibration_method")
    input_space = _first_text(payload, block, "input_space")
    hard_fact_markers = {authority.upper(), calibration_method.lower(), input_space.lower()}
    if DAY0_OBSERVATION_HARD_FACT_AUTHORITY in hard_fact_markers or "day0_live_observation_hard_fact" in hard_fact_markers:
        raise Day0AuthorityError("day0 hard-fact calibration cannot authorize entry probability")

    q_source = _first_text(payload, block, "_edli_q_source", "day0_q_source", "q_source")
    if q_source == DAY0_REPLACEMENT_Q_SOURCE:
        _assert_replacement_global_day0_probability_authority(
            payload,
            block,
            direction=direction,
            condition_id=condition_id,
            q_live=q_live,
            q_lcb=q_lcb,
        )
        return
    if q_source == DAY0_DETERMINISTIC_BIN_PAYOFF_Q_SOURCE:
        _assert_deterministic_bin_payoff_authority(
            payload,
            block,
            direction=direction,
            condition_id=condition_id,
            q_live=q_live,
            q_lcb=q_lcb,
        )
        return
    if q_source != DAY0_REMAINING_DAY_Q_SOURCE:
        raise Day0AuthorityError(
            "day0_probability_q_source required:"
            f"{q_source or 'missing'}"
        )
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
        _assert_remaining_day_lcb_is_supported_by_transform(
            payload=payload,
            direction=direction,
            condition_id=condition_id,
            q_live=q_live_value,
            q_lcb=q_lcb_value,
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
    if not math.isclose(float(q_lcb), transform_lcb, rel_tol=1e-9, abs_tol=1e-6) and not (
        float(q_lcb) < transform_lcb
        and _remaining_day_lcb_has_current_band_tightening(
            payload,
            q_live=float(q_live),
            q_lcb=float(q_lcb),
        )
    ):
        raise Day0AuthorityError(
            "selected q_lcb does not match remaining-day transform:"
            f"condition_id={selected_condition}:q_lcb={float(q_lcb):.12g}:"
            f"transform_lcb={transform_lcb:.12g}"
        )


# Legitimate bases for a Day0 candidate's q_lcb/selection guard fields.
# ``DAY0_REMAINING_DAY_Q_LCB_GUARD_BASIS`` is the genuine hard-fact recompute grant
# (an already-impossible/already-certain monotone fact -- family_decision_engine's
# ``_apply_day0_observed_boundary_guard``, is_hard_fact=True branch). The rest are the
# empirical OOF q_lcb reliability guard's OWN verdict vocabulary
# (src/decision/qlcb_reliability_guard.py ``apply_guard``): a Day0 candidate the
# hard-fact function refused to certify (is_hard_fact=False) is routed through that
# SAME guard non-Day0 candidates use, and carries its basis instead. "INERT" (no OOF
# artifact -- pass-through) and "OOF_WILSON_95" / "OOF_WILSON_95_POOLED_TAIL" (a real
# cell licensed the candidate, deflated or not) are non-abstaining verdicts a
# positive-edge trade may legitimately carry. Aligned with the sibling allowlist
# ``_GUARDED_FALSE_EDGE_RATE_95_BASES`` (src/engine/qkernel_spine_bridge.py:2110) minus
# its SELECTION_* entries (that guard never runs on a Day0 candidate) plus "INERT" (the
# OOF artifact is not always present; an inert pass-through is the same no-artifact
# posture a non-Day0 candidate already gets). "OOF_WILSON_95_MISSING_CELL" and any
# guard-internal error basis are deliberately excluded -- both always pair with
# ``abstained=True`` in the guard's own verdict construction, so they must fail here
# too, not be waved through.
_DAY0_GUARDED_QLCB_BASES = frozenset(
    {
        DAY0_REMAINING_DAY_Q_LCB_GUARD_BASIS,
        "INERT",
        "OOF_WILSON_95",
        "OOF_WILSON_95_POOLED_TAIL",
    }
)


def assert_live_day0_qkernel_guard_authority(
    economics: Mapping[str, object],
    *,
    probability_payload: Mapping[str, object] | None = None,
) -> None:
    """Fail closed unless Day0 entry qkernel evidence uses a legitimate guard basis.

    A hard-fact-certified candidate carries ``DAY0_REMAINING_DAY_Q_LCB_GUARD_BASIS``
    unchanged. A candidate the hard-fact function could NOT certify
    (is_hard_fact=False) is routed through the empirical OOF q_lcb reliability guard
    (the SAME guard non-Day0 candidates use) and carries THAT guard's own basis
    instead -- see ``_DAY0_GUARDED_QLCB_BASES``. Both are legitimate live authority;
    only an empty basis, the retired ``DAY0_OBSERVED_BOUNDARY`` hard-fact-without-guard
    stamp, or an abstained/unrecognized-basis verdict are rejected.

    ``DAY0_OBSERVED_BOUNDARY`` is a hard-fact observation boundary. It can rule out
    already-impossible outcomes, but it cannot by itself license an entry on a
    finite bin that merely contains the current running extreme.
    """

    block = (
        _day0_probability_block(probability_payload)
        if probability_payload is not None
        else {}
    )
    q_source = (
        _first_text(
            probability_payload,
            block,
            "_edli_q_source",
            "day0_q_source",
            "q_source",
        )
        if probability_payload is not None
        else ""
    )
    replacement_global = q_source == DAY0_REPLACEMENT_Q_SOURCE
    deterministic_payoff = q_source == DAY0_DETERMINISTIC_BIN_PAYOFF_Q_SOURCE
    current_band = any(
        str(economics.get(field_name) or "").strip()
        == DAY0_REPLACEMENT_GLOBAL_GUARD_BASIS
        for field_name in ("q_lcb_guard_basis", "selection_guard_basis")
    ) or bool(str(economics.get("current_state_identity_hash") or "").strip())
    if replacement_global or current_band:
        from src.decision_kernel.canonicalization import (
            qkernel_current_state_rejection_reason,
        )

        current_state_reason = qkernel_current_state_rejection_reason(economics)
        if current_state_reason is not None:
            raise Day0AuthorityError(
                f"replacement_day0_current_state invalid:{current_state_reason}"
            )
    accepted_bases = (
        frozenset({DAY0_REPLACEMENT_GLOBAL_GUARD_BASIS})
        if replacement_global or current_band
        else _DAY0_GUARDED_QLCB_BASES
    )
    for field_name in ("q_lcb_guard_basis", "selection_guard_basis"):
        basis = str(economics.get(field_name) or "").strip()
        if not basis:
            raise Day0AuthorityError(f"{field_name} missing")
        if basis == DAY0_OBSERVED_BOUNDARY_GUARD_BASIS:
            raise Day0AuthorityError(f"{field_name} cannot be DAY0_OBSERVED_BOUNDARY")
        if basis not in accepted_bases:
            raise Day0AuthorityError(
                f"{field_name} must be one of {sorted(accepted_bases)}"
            )
    for field_name in ("q_lcb_guard_abstained", "selection_guard_abstained"):
        if economics.get(field_name) is not False:
            raise Day0AuthorityError(f"{field_name} must be false")
    try:
        selection_guard_q_safe = float(economics.get("selection_guard_q_safe"))
    except (TypeError, ValueError):
        raise Day0AuthorityError("selection_guard_q_safe must be positive") from None
    if not math.isfinite(selection_guard_q_safe) or selection_guard_q_safe <= 0.0:
        raise Day0AuthorityError("selection_guard_q_safe must be positive")
    if probability_payload is not None:
        direction = _first_text(
            probability_payload,
            block,
            "direction",
            "actual_direction",
        ).lower()
        expected_side = {"buy_yes": "YES", "buy_no": "NO"}.get(direction)
        observed_side = str(economics.get("side") or "").strip().upper()
        if expected_side is None or observed_side != expected_side:
            raise Day0AuthorityError(
                "day0_qkernel_side mismatch:"
                f"direction={direction or 'missing'}:side={observed_side or 'missing'}"
            )
        owner_q_live = _first_float(probability_payload, block, "q_live")
        owner_q_lcb = _first_float(probability_payload, block, "q_lcb_5pct")
        payoff_q_point = _first_float(economics, {}, "payoff_q_point")
        payoff_q_lcb = _first_float(economics, {}, "payoff_q_lcb")
        if None in (owner_q_live, owner_q_lcb, payoff_q_point, payoff_q_lcb):
            raise Day0AuthorityError("day0_qkernel_probability_binding missing")
        assert owner_q_live is not None
        assert owner_q_lcb is not None
        assert payoff_q_point is not None
        assert payoff_q_lcb is not None
        if not math.isclose(
            owner_q_live,
            payoff_q_point,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise Day0AuthorityError("day0_qkernel payoff_q_point mismatches q_live")
        if not math.isclose(
            owner_q_lcb,
            payoff_q_lcb,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise Day0AuthorityError("day0_qkernel payoff_q_lcb mismatches q_lcb_5pct")
        if not math.isclose(
            selection_guard_q_safe,
            payoff_q_lcb,
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            raise Day0AuthorityError(
                "day0_qkernel selection_guard_q_safe mismatches payoff_q_lcb"
            )
        if deterministic_payoff:
            observation = block.get("global_current_observation_payload")
            if not isinstance(observation, Mapping):
                raise Day0AuthorityError("deterministic_current_observation missing")
            authority_q_version = _matching_text(
                "deterministic_q_version",
                probability_payload.get("_edli_day0_deterministic_q_version"),
                block.get("q_version"),
                observation.get("_edli_day0_deterministic_q_version"),
            )
            authority_sample_identity = _matching_text(
                "deterministic_sample_identity",
                probability_payload.get(
                    "_edli_day0_deterministic_sample_identity"
                ),
                block.get("sample_identity"),
                observation.get("_edli_day0_deterministic_sample_identity"),
            )
            economics_q_version = str(economics.get("q_version") or "").strip()
            economics_sample_identity = str(
                economics.get("sample_hash") or ""
            ).strip()
            if economics_q_version != authority_q_version:
                raise Day0AuthorityError(
                    "deterministic_qkernel_q_version mismatch"
                )
            if economics_sample_identity != authority_sample_identity:
                raise Day0AuthorityError(
                    "deterministic_qkernel_sample_identity mismatch"
                )
    if (
        probability_payload is not None
        and not replacement_global
        and not deterministic_payoff
    ):
        remaining_models = _first_int(
            probability_payload,
            block,
            "_edli_day0_remaining_models",
            "day0_remaining_models",
            "remaining_models",
        )
        if remaining_models is None or remaining_models <= 0:
            raise Day0AuthorityError("remaining_day_models missing")


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
