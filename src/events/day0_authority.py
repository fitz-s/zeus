"""Day0 live-authority checks for EDLI redemption."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from src.contracts.settlement_semantics import SettlementSemantics


class Day0AuthorityError(ValueError):
    """Raised when a Day0 observation cannot authorize live hard facts."""


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
        if getattr(evidence, field_name) not in accepted:
            raise Day0AuthorityError(f"{field_name} does not authorize live Day0 fact")
    rounded = int(evidence.settlement_semantics.round_single(evidence.raw_value))
    if rounded != evidence.rounded_value:
        raise Day0AuthorityError("rounded_value does not match SettlementSemantics")


def observability_row_to_authority(row: Mapping[str, object]) -> Day0AuthorityEvidence:
    if row.get("live_authority_status") != "live":
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
        live_authority_status=str(row.get("live_authority_status") or "UNKNOWN"),
        observation_available_at=str(row.get("observation_available_at") or ""),
        observation_time=str(row.get("observation_time") or ""),
        raw_value=float(row.get("raw_value") or 0.0),
        rounded_value=int(row.get("rounded_value") or 0),
        settlement_semantics=semantics,
    )
