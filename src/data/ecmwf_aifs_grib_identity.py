"""AIFS ENS GRIB identity scanner for sampled-2t shadow artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


SOURCE_ID = "ecmwf_aifs_ens"
PRODUCT_ID = "ecmwf_aifs_ens_sampled_2t_6h_v1"
EXPECTED_MEMBERS = 51
EXPECTED_CONTROL_TYPES = {"cf"}
EXPECTED_PERTURBED_TYPE = "pf"
EXPECTED_CLASS = "ai"
EXPECTED_STREAMS = {"enfo", "waef"}
EXPECTED_MODEL = "aifs-ens"
EXPECTED_PARAM_SHORT_NAMES = {"2t"}
EXPECTED_PARAM_IDS = {167}
EXPECTED_LEVTYPE = "sfc"
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


@dataclass(frozen=True)
class AifsEnsGribMessageIdentity:
    ecmwf_class: str
    stream: str
    model: str
    message_type: str
    param_short_name: str
    param_id: int | None
    levtype: str
    step_hours: int
    member_id: str
    raw_keys: Mapping[str, object]


@dataclass(frozen=True)
class AifsEnsGribIdentityDecision:
    valid: bool
    reason_codes: tuple[str, ...]
    member_ids: tuple[str, ...]
    step_hours: tuple[int, ...]
    message_count: int
    source_id: str = SOURCE_ID
    product_id: str = PRODUCT_ID
    expected_members: int = EXPECTED_MEMBERS
    trade_authority_status: str = "SHADOW_ONLY"
    training_allowed: bool = False


def _text(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _int_or_none(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _step_hours(row: Mapping[str, object]) -> int:
    value = row.get("step_hours", row.get("step"))
    if value is None:
        raise ValueError("AIFS GRIB message missing step_hours/step")
    if isinstance(value, str) and "-" in value:
        value = value.rsplit("-", 1)[-1]
    return int(value)


def _member_id(row: Mapping[str, object], *, message_type: str) -> str:
    for key in ("member_id", "number", "perturbationNumber", "ensembleMember"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    if message_type in EXPECTED_CONTROL_TYPES:
        return "control"
    return "missing"


def _message_identity(row: Mapping[str, object]) -> AifsEnsGribMessageIdentity:
    message_type = _text(row, "type", "marsType", "dataType").lower()
    return AifsEnsGribMessageIdentity(
        ecmwf_class=_text(row, "class", "marsClass").lower(),
        stream=_text(row, "stream").lower(),
        model=_text(row, "model", "marsModel").lower(),
        message_type=message_type,
        param_short_name=_text(row, "shortName", "param", "param_short_name").lower(),
        param_id=_int_or_none(row.get("paramId", row.get("param_id"))),
        levtype=_text(row, "levtype", "typeOfLevel").lower(),
        step_hours=_step_hours(row),
        member_id=_member_id(row, message_type=message_type),
        raw_keys=dict(row),
    )


def _reject_transcript_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full replacement identity")


def scan_aifs_ens_grib_identity(messages: Sequence[Mapping[str, object]]) -> AifsEnsGribIdentityDecision:
    """Validate AIFS ENS sampled-2t GRIB key rows from pygrib/ecCodes output.

    The scanner accepts already-read GRIB key mappings so callers can use pygrib,
    eccodes, or indexed manifest rows without adding a hard dependency here.
    """

    _reject_transcript_alias(SOURCE_ID, field_name="source_id")
    _reject_transcript_alias(PRODUCT_ID, field_name="product_id")
    if not messages:
        raise ValueError("AIFS ENS GRIB identity scan requires at least one message")

    identities = tuple(_message_identity(message) for message in messages)
    reasons: list[str] = []
    member_ids: set[str] = set()
    step_hours: set[int] = set()
    control_count_by_step: dict[int, int] = {}
    perturbed_members_by_step: dict[int, set[str]] = {}

    for identity in identities:
        if identity.ecmwf_class != EXPECTED_CLASS:
            reasons.append("AIFS_GRIB_CLASS_MISMATCH")
        if identity.stream not in EXPECTED_STREAMS:
            reasons.append("AIFS_GRIB_STREAM_MISMATCH")
        if identity.model != EXPECTED_MODEL:
            reasons.append("AIFS_GRIB_MODEL_MISMATCH")
        if identity.message_type not in EXPECTED_CONTROL_TYPES | {EXPECTED_PERTURBED_TYPE}:
            reasons.append("AIFS_GRIB_TYPE_MISMATCH")
        if identity.message_type == EXPECTED_PERTURBED_TYPE and identity.member_id == "missing":
            reasons.append("AIFS_GRIB_MEMBER_ID_MISSING")
        if identity.param_short_name not in EXPECTED_PARAM_SHORT_NAMES and identity.param_id not in EXPECTED_PARAM_IDS:
            reasons.append("AIFS_GRIB_PARAM_MISMATCH")
        if identity.levtype != EXPECTED_LEVTYPE:
            reasons.append("AIFS_GRIB_LEVTYPE_MISMATCH")
        if identity.step_hours < 0 or identity.step_hours % 6 != 0 or identity.step_hours > 360:
            reasons.append("AIFS_GRIB_STEP_GRID_MISMATCH")
        step_hours.add(identity.step_hours)
        member_id = "control" if identity.message_type in EXPECTED_CONTROL_TYPES else f"pf:{identity.member_id}"
        member_ids.add(member_id)
        if identity.message_type in EXPECTED_CONTROL_TYPES:
            control_count_by_step[identity.step_hours] = control_count_by_step.get(identity.step_hours, 0) + 1
        elif identity.message_type == EXPECTED_PERTURBED_TYPE:
            perturbed_members_by_step.setdefault(identity.step_hours, set()).add(identity.member_id)

    for step in step_hours:
        if control_count_by_step.get(step, 0) != 1:
            reasons.append("AIFS_GRIB_CONTROL_MEMBER_MISSING_OR_DUPLICATED")
        if len(perturbed_members_by_step.get(step, set())) != 50:
            reasons.append("AIFS_GRIB_PERTURBED_MEMBER_COUNT_MISMATCH")

    if len(member_ids) != EXPECTED_MEMBERS:
        reasons.append("AIFS_GRIB_TOTAL_MEMBER_COUNT_MISMATCH")

    reason_tuple = tuple(dict.fromkeys(reasons))
    return AifsEnsGribIdentityDecision(
        valid=not reason_tuple,
        reason_codes=reason_tuple or ("AIFS_GRIB_IDENTITY_VALID",),
        member_ids=tuple(sorted(member_ids)),
        step_hours=tuple(sorted(step_hours)),
        message_count=len(identities),
    )
