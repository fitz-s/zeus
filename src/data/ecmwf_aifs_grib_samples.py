"""Optional ecCodes extraction of AIFS ENS 2t point samples from GRIB."""

from __future__ import annotations

import importlib
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from src.data.ecmwf_aifs_grib_identity import scan_aifs_ens_grib_identity
from src.data.ecmwf_aifs_sampled_2t_localday import AifsInstantSample


UTC = timezone.utc


@dataclass(frozen=True)
class AifsGribPointSampleExtraction:
    samples: tuple[AifsInstantSample, ...]
    message_count: int
    member_ids: tuple[str, ...]
    step_hours: tuple[int, ...]
    nearest_points: tuple[Mapping[str, object], ...]
    identity_reason_codes: tuple[str, ...]
    identity_decision_hash: str
    raw_sha256: str


def _canonical_hash(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _import_eccodes():
    try:
        return importlib.import_module("eccodes")
    except ImportError as exc:
        raise RuntimeError(
            "ecCodes Python bindings are required to extract AIFS GRIB samples; "
            "install eccodes/python bindings or provide pre-extracted samples JSON"
        ) from exc


def _codes_get(eccodes: Any, gid: Any, key: str, default: object = None) -> object:
    try:
        return eccodes.codes_get(gid, key)
    except Exception:
        return default


def _message_keys(eccodes: Any, gid: Any) -> dict[str, object]:
    return {
        "class": _codes_get(eccodes, gid, "marsClass", _codes_get(eccodes, gid, "class")),
        "stream": _codes_get(eccodes, gid, "stream"),
        "model": _codes_get(eccodes, gid, "model"),
        "type": _codes_get(eccodes, gid, "marsType", _codes_get(eccodes, gid, "dataType")),
        "shortName": _codes_get(eccodes, gid, "shortName"),
        "paramId": _codes_get(eccodes, gid, "paramId"),
        "levtype": _codes_get(eccodes, gid, "levtype", _codes_get(eccodes, gid, "typeOfLevel")),
        "step": _codes_get(eccodes, gid, "step"),
        "number": _codes_get(eccodes, gid, "number", _codes_get(eccodes, gid, "perturbationNumber")),
    }


def _member_id(keys: Mapping[str, object]) -> str:
    message_type = str(keys.get("type") or "").lower()
    if message_type == "cf":
        return "control"
    number = keys.get("number")
    if number is None or str(number) == "":
        return "missing"
    return f"pf:{int(number):03d}"


def _valid_time(source_cycle_time: datetime, step: object) -> datetime:
    if source_cycle_time.tzinfo is None or source_cycle_time.utcoffset() is None:
        raise ValueError("source_cycle_time must be timezone-aware")
    step_text = str(step)
    if "-" in step_text:
        step_text = step_text.rsplit("-", 1)[-1]
    return source_cycle_time.astimezone(UTC) + timedelta(hours=int(step_text))


def extract_aifs_2t_point_samples_from_grib(
    grib_path: Path | str,
    *,
    latitude: float,
    longitude: float,
    source_cycle_time: datetime,
    eccodes_module: Any | None = None,
) -> AifsGribPointSampleExtraction:
    """Extract nearest-grid 2t samples for one point from an AIFS ENS GRIB file."""

    path = Path(grib_path)
    if not path.exists():
        raise FileNotFoundError(path)
    injected_eccodes = eccodes_module is not None
    eccodes = eccodes_module or _import_eccodes()
    messages: list[dict[str, object]] = []
    sample_rows: list[tuple[dict[str, object], Mapping[str, object]]] = []
    with path.open("rb") as fh:
        while True:
            gid = eccodes.codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                keys = _message_keys(eccodes, gid)
                messages.append(keys)
                nearest = eccodes.codes_grib_find_nearest(gid, float(latitude), float(longitude))[0]
                sample_rows.append((keys, dict(nearest)))
            finally:
                eccodes.codes_release(gid)
    if not messages and not injected_eccodes:
        raise RuntimeError(
            "ecCodes Python bindings are required to extract AIFS GRIB samples from a valid GRIB file; "
            "install eccodes/python bindings or provide pre-extracted samples JSON"
        )
    decision = scan_aifs_ens_grib_identity(messages)
    if not decision.valid:
        raise ValueError("AIFS_GRIB_IDENTITY_INVALID:" + ",".join(decision.reason_codes))
    samples: list[AifsInstantSample] = []
    nearest_payloads: list[Mapping[str, object]] = []
    for keys, nearest in sample_rows:
        samples.append(
            AifsInstantSample(
                member_id=_member_id(keys),
                valid_time_utc=_valid_time(source_cycle_time, keys["step"]),
                temperature=float(nearest["value"]),
                temperature_unit="K",
            )
        )
        nearest_payloads.append(
            {
                "member_id": _member_id(keys),
                "step": int(str(keys["step"]).rsplit("-", 1)[-1]),
                "grid_latitude": nearest.get("lat"),
                "grid_longitude": nearest.get("lon"),
                "distance": nearest.get("distance"),
            }
        )
    raw_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    identity_decision_hash = _canonical_hash(
        {
            "valid": decision.valid,
            "reason_codes": decision.reason_codes,
            "member_ids": decision.member_ids,
            "step_hours": decision.step_hours,
            "message_count": decision.message_count,
            "source_id": decision.source_id,
            "product_id": decision.product_id,
            "expected_members": decision.expected_members,
            "raw_sha256": raw_sha256,
        }
    )
    return AifsGribPointSampleExtraction(
        samples=tuple(samples),
        message_count=decision.message_count,
        member_ids=decision.member_ids,
        step_hours=decision.step_hours,
        nearest_points=tuple(nearest_payloads),
        identity_reason_codes=decision.reason_codes,
        identity_decision_hash=identity_decision_hash,
        raw_sha256=raw_sha256,
    )
