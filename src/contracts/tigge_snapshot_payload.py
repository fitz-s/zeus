# Created: 2026-04-29
# Last reused/audited: 2026-04-29
# Authority basis: Antibody #16 — TIGGE extractor↔ingester schema drift fix.
#   Derived from full field audit of:
#     scripts/extract_tigge_mx2t6_localday_max.py (_finalize_record)
#     scripts/extract_tigge_mn2t6_localday_min.py (build_low_snapshot_json)
#     scripts/ingest_grib_to_snapshots.py (ingest_json_file)
#   Operator directive 2026-04-29: "drift needs a one-and-done solution, no asymmetry"
#   Structural fix per Fitz Constraint #1 (structural decisions > patches) and
#   Fitz Constraint #4 (data provenance > code correctness).
"""Canonical schema dataclass for TIGGE ensemble snapshot payloads.

Both extractors (mx2t6 high-track, mn2t6 low-track) MUST construct a
TiggeSnapshotPayload instance and call to_json_dict() to produce their output.
The ingester MUST call from_json_dict() as the ONLY way to read extracted JSONs.

This makes schema drift between extractors and ingester structurally impossible:
the same class defines what both sides emit and accept.

HIGH track (mx2t6):
  - causality_status defaults to "OK" (pure forecast; boundary=False always)
  - boundary_ambiguous defaults to False (no boundary quarantine law for MAX)
  - boundary_policy NOT emitted (high track has no boundary policy dict)

LOW track (mn2t6):
  - causality_status is mandatory (boundary-leakage law, R-AH/R-AJ)
  - boundary_policy is mandatory (training_rule + boundary_ambiguous + ambiguous_member_count)
  - members_unit = "K" (raw Kelvin; ingester normalises via city unit)
"""
from __future__ import annotations

from typing import Any, Optional


class ProvenanceViolation(Exception):
    """Raised when from_json_dict detects a missing required field or malformed causality."""


class TiggeSnapshotPayload:
    """Canonical payload for one (city, issue_date, target_local_date, lead_day) slot.

    Fields are a strict superset of all fields emitted by both extractors and
    consumed by the ingester. Optional fields are None when absent.

    REQUIRED_FIELDS lists fields that must be non-None in any valid payload.
    Track-specific requirements are enforced in validate().
    """

    REQUIRED_FIELDS: frozenset = frozenset({
        # These are the fields the ingester reads directly and whose absence
        # causes silent data corruption or incorrect DB rows.
        "data_version",       # assert_data_version_allowed + INSERT
        "unit",               # _normalize_unit -> members_unit column
        "causality",          # Law 5 / R-AJ: absent causality must never silently default to OK
        "members",            # _members_list -> members_json column
        "issue_time_utc",     # local_day_start_utc + lead_hours
        "city",               # INSERT city column
        "target_date_local",  # INSERT target_date column
        "lead_day",           # _lead_hours -> lead_hours column
    })

    def __init__(
        self,
        *,
        # Provenance / identity
        generated_at: str,
        data_version: str,
        physical_quantity: str,
        param: str,
        paramId: int,
        short_name: str,
        step_type: str,
        aggregation_window_hours: int,
        # City / location
        city: str,
        lat: Optional[float],
        lon: Optional[float],
        unit: str,
        timezone: str,
        manifest_sha256: str,
        manifest_hash: str,
        # Temporal
        issue_time_utc: str,
        target_date_local: str,
        lead_day: int,
        lead_day_anchor: str,
        local_day_start_utc: Optional[str],
        local_day_end_utc: Optional[str],
        local_day_window: dict,
        step_horizon_hours: Optional[float],
        step_horizon_deficit_hours: Optional[float],
        # Causality (REQUIRED for both tracks per Law 5 / R-AJ)
        causality: dict,
        # Boundary
        boundary_ambiguous: bool,
        boundary_policy: Optional[dict],
        # Grid proximity
        nearest_grid_lat: Optional[float],
        nearest_grid_lon: Optional[float],
        nearest_grid_distance_km: Optional[float],
        # Member data
        member_count: int,
        missing_members: list,
        training_allowed: bool,
        members: list,
        # LOW-track-specific fields (None for HIGH)
        temperature_metric: Optional[str] = None,
        members_unit: Optional[str] = None,
        # LOW-track step range diagnostics
        selected_step_ranges_inner: Optional[list] = None,
        selected_step_ranges_boundary: Optional[list] = None,
        # HIGH-track step range
        selected_step_ranges: Optional[list] = None,
        # Optional explicit 6h forecast-window evidence. Current extractors can
        # still emit selected_step_ranges only; the ingester derives an envelope
        # from those ranges when these fields are absent.
        forecast_window_start_utc: Optional[str] = None,
        forecast_window_end_utc: Optional[str] = None,
        forecast_window_start_local: Optional[str] = None,
        forecast_window_end_local: Optional[str] = None,
    ) -> None:
        self.generated_at = generated_at
        self.data_version = data_version
        self.physical_quantity = physical_quantity
        self.param = param
        self.paramId = paramId
        self.short_name = short_name
        self.step_type = step_type
        self.aggregation_window_hours = aggregation_window_hours
        self.city = city
        self.lat = lat
        self.lon = lon
        self.unit = unit
        self.timezone = timezone
        self.manifest_sha256 = manifest_sha256
        self.manifest_hash = manifest_hash
        self.issue_time_utc = issue_time_utc
        self.target_date_local = target_date_local
        self.lead_day = lead_day
        self.lead_day_anchor = lead_day_anchor
        self.local_day_start_utc = local_day_start_utc
        self.local_day_end_utc = local_day_end_utc
        self.local_day_window = local_day_window
        self.step_horizon_hours = step_horizon_hours
        self.step_horizon_deficit_hours = step_horizon_deficit_hours
        self.causality = causality
        self.boundary_ambiguous = boundary_ambiguous
        self.boundary_policy = boundary_policy
        self.nearest_grid_lat = nearest_grid_lat
        self.nearest_grid_lon = nearest_grid_lon
        self.nearest_grid_distance_km = nearest_grid_distance_km
        self.member_count = member_count
        self.missing_members = missing_members
        self.training_allowed = training_allowed
        self.members = members
        self.temperature_metric = temperature_metric
        self.members_unit = members_unit
        self.selected_step_ranges_inner = selected_step_ranges_inner
        self.selected_step_ranges_boundary = selected_step_ranges_boundary
        self.selected_step_ranges = selected_step_ranges
        self.forecast_window_start_utc = forecast_window_start_utc
        self.forecast_window_end_utc = forecast_window_end_utc
        self.forecast_window_start_local = forecast_window_start_local
        self.forecast_window_end_local = forecast_window_end_local

    def to_json_dict(self) -> dict:
        """Produce a JSON-serializable dict. None fields are omitted."""
        d: dict[str, Any] = {
            "generated_at": self.generated_at,
            "data_version": self.data_version,
            "physical_quantity": self.physical_quantity,
            "param": self.param,
            "paramId": self.paramId,
            "short_name": self.short_name,
            "step_type": self.step_type,
            "aggregation_window_hours": self.aggregation_window_hours,
            "city": self.city,
            "unit": self.unit,
            "timezone": self.timezone,
            "manifest_sha256": self.manifest_sha256,
            "manifest_hash": self.manifest_hash,
            "issue_time_utc": self.issue_time_utc,
            "target_date_local": self.target_date_local,
            "lead_day": self.lead_day,
            "lead_day_anchor": self.lead_day_anchor,
            "local_day_window": self.local_day_window,
            "causality": self.causality,
            "boundary_ambiguous": self.boundary_ambiguous,
            "member_count": self.member_count,
            "missing_members": self.missing_members,
            "training_allowed": self.training_allowed,
            "members": self.members,
        }
        # Optional fields included only when not None
        for attr, key in [
            ("lat", "lat"),
            ("lon", "lon"),
            ("local_day_start_utc", "local_day_start_utc"),
            ("local_day_end_utc", "local_day_end_utc"),
            ("step_horizon_hours", "step_horizon_hours"),
            ("step_horizon_deficit_hours", "step_horizon_deficit_hours"),
            ("boundary_policy", "boundary_policy"),
            ("nearest_grid_lat", "nearest_grid_lat"),
            ("nearest_grid_lon", "nearest_grid_lon"),
            ("nearest_grid_distance_km", "nearest_grid_distance_km"),
            ("temperature_metric", "temperature_metric"),
            ("members_unit", "members_unit"),
            ("selected_step_ranges_inner", "selected_step_ranges_inner"),
            ("selected_step_ranges_boundary", "selected_step_ranges_boundary"),
            ("selected_step_ranges", "selected_step_ranges"),
            ("forecast_window_start_utc", "forecast_window_start_utc"),
            ("forecast_window_end_utc", "forecast_window_end_utc"),
            ("forecast_window_start_local", "forecast_window_start_local"),
            ("forecast_window_end_local", "forecast_window_end_local"),
        ]:
            val = getattr(self, attr)
            if val is not None:
                d[key] = val
        return d

    @classmethod
    def from_json_dict(cls, d: dict) -> "TiggeSnapshotPayload":
        """Construct from a raw dict. Raises ProvenanceViolation on missing required fields.

        This is the ONLY approved way for the ingester to read extracted JSONs.
        Fail-closed: any missing required field or malformed causality raises immediately.
        """
        missing = [f for f in cls.REQUIRED_FIELDS if f not in d or d[f] is None]
        if missing:
            raise ProvenanceViolation(
                f"TiggeSnapshotPayload.from_json_dict: missing required fields: "
                f"{sorted(missing)}. "
                f"city={d.get('city')!r} "
                f"target={d.get('target_date_local')!r} "
                f"issue={d.get('issue_time_utc')!r} "
                f"data_version={d.get('data_version')!r}"
            )
        causality = d["causality"]
        if not isinstance(causality, dict):
            raise ProvenanceViolation(
                f"causality must be a dict, got {type(causality).__name__!r}: {causality!r}"
            )
        if "status" not in causality:
            raise ProvenanceViolation(
                f"causality dict missing 'status' key: {causality!r}"
            )
        return cls(
            # Required fields (already verified above)
            data_version=str(d["data_version"]),
            unit=str(d["unit"]),
            causality=dict(causality),
            members=list(d["members"]),
            issue_time_utc=str(d["issue_time_utc"]),
            city=str(d["city"]),
            target_date_local=str(d["target_date_local"]),
            lead_day=int(d["lead_day"]),
            # Optional fields — use .get() with sensible defaults
            generated_at=str(d.get("generated_at", "")),
            physical_quantity=str(d.get("physical_quantity", "")),
            param=str(d.get("param", "")),
            paramId=int(d["paramId"]) if d.get("paramId") is not None else 0,
            short_name=str(d.get("short_name", "")),
            step_type=str(d.get("step_type", "")),
            aggregation_window_hours=int(d["aggregation_window_hours"]) if d.get("aggregation_window_hours") is not None else 0,
            lat=float(d["lat"]) if d.get("lat") is not None else None,
            lon=float(d["lon"]) if d.get("lon") is not None else None,
            timezone=str(d.get("timezone", "")),
            manifest_sha256=str(d.get("manifest_sha256", "")),
            manifest_hash=str(d.get("manifest_hash", "")),
            lead_day_anchor=str(d.get("lead_day_anchor", "issue_utc.date()")),
            local_day_start_utc=d.get("local_day_start_utc"),
            local_day_end_utc=d.get("local_day_end_utc"),
            local_day_window=dict(d["local_day_window"]) if d.get("local_day_window") else {},
            step_horizon_hours=(
                float(d["step_horizon_hours"])
                if d.get("step_horizon_hours") is not None else None
            ),
            step_horizon_deficit_hours=(
                float(d["step_horizon_deficit_hours"])
                if d.get("step_horizon_deficit_hours") is not None else None
            ),
            boundary_ambiguous=bool(d["boundary_ambiguous"]) if d.get("boundary_ambiguous") is not None else False,
            boundary_policy=d.get("boundary_policy"),
            nearest_grid_lat=(
                float(d["nearest_grid_lat"])
                if d.get("nearest_grid_lat") is not None else None
            ),
            nearest_grid_lon=(
                float(d["nearest_grid_lon"])
                if d.get("nearest_grid_lon") is not None else None
            ),
            nearest_grid_distance_km=(
                float(d["nearest_grid_distance_km"])
                if d.get("nearest_grid_distance_km") is not None else None
            ),
            member_count=int(d["member_count"]) if d.get("member_count") is not None else len(d.get("members", [])),
            missing_members=list(d.get("missing_members", [])),
            training_allowed=bool(d.get("training_allowed", False)),
            temperature_metric=d.get("temperature_metric"),
            members_unit=d.get("members_unit"),
            selected_step_ranges_inner=d.get("selected_step_ranges_inner"),
            selected_step_ranges_boundary=d.get("selected_step_ranges_boundary"),
            selected_step_ranges=d.get("selected_step_ranges"),
            forecast_window_start_utc=d.get("forecast_window_start_utc"),
            forecast_window_end_utc=d.get("forecast_window_end_utc"),
            forecast_window_start_local=d.get("forecast_window_start_local"),
            forecast_window_end_local=d.get("forecast_window_end_local"),
        )

    def validate(self) -> None:
        """Raise ProvenanceViolation on any contract violation."""
        if not isinstance(self.causality, dict):
            raise ProvenanceViolation(f"causality must be dict, got {type(self.causality)}")
        if "status" not in self.causality:
            raise ProvenanceViolation(f"causality missing 'status': {self.causality!r}")
        if len(self.members) != self.member_count:
            raise ProvenanceViolation(
                f"members length {len(self.members)} != member_count {self.member_count}"
            )
        # LOW track requirements
        if self.temperature_metric == "low":
            if self.boundary_policy is None:
                raise ProvenanceViolation(
                    "LOW track requires boundary_policy dict (boundary-leakage law)"
                )
            if "boundary_ambiguous" not in self.boundary_policy:
                raise ProvenanceViolation(
                    f"boundary_policy missing 'boundary_ambiguous': {self.boundary_policy!r}"
                )
            if self.members_unit is None:
                raise ProvenanceViolation("LOW track requires members_unit (R-AH)")
        # HIGH track: boundary_ambiguous must be False
        if self.temperature_metric is None or self.temperature_metric == "high":
            if self.boundary_ambiguous is not False:
                raise ProvenanceViolation(
                    f"HIGH track boundary_ambiguous must be False, got {self.boundary_ambiguous!r}"
                )

    @classmethod
    def make_high_track(
        cls,
        *,
        generated_at: str,
        data_version: str,
        physical_quantity: str,
        param: str,
        paramId: int,
        short_name: str,
        step_type: str,
        aggregation_window_hours: int,
        city: str,
        lat: Optional[float],
        lon: Optional[float],
        unit: str,
        timezone: str,
        manifest_sha256: str,
        manifest_hash: str,
        issue_time_utc: str,
        target_date_local: str,
        lead_day: int,
        lead_day_anchor: str,
        local_day_start_utc: Optional[str],
        local_day_end_utc: Optional[str],
        local_day_window: dict,
        step_horizon_hours: Optional[float],
        step_horizon_deficit_hours: Optional[float],
        causality: dict,
        nearest_grid_lat: Optional[float],
        nearest_grid_lon: Optional[float],
        nearest_grid_distance_km: Optional[float],
        member_count: int,
        missing_members: list,
        training_allowed: bool,
        members: list,
        selected_step_ranges: Optional[list] = None,
    ) -> "TiggeSnapshotPayload":
        """Convenience constructor for HIGH track (mx2t6). Always sets boundary_ambiguous=False."""
        return cls(
            generated_at=generated_at,
            data_version=data_version,
            physical_quantity=physical_quantity,
            param=param,
            paramId=paramId,
            short_name=short_name,
            step_type=step_type,
            aggregation_window_hours=aggregation_window_hours,
            city=city,
            lat=lat,
            lon=lon,
            unit=unit,
            timezone=timezone,
            manifest_sha256=manifest_sha256,
            manifest_hash=manifest_hash,
            issue_time_utc=issue_time_utc,
            target_date_local=target_date_local,
            lead_day=lead_day,
            lead_day_anchor=lead_day_anchor,
            local_day_start_utc=local_day_start_utc,
            local_day_end_utc=local_day_end_utc,
            local_day_window=local_day_window,
            step_horizon_hours=step_horizon_hours,
            step_horizon_deficit_hours=step_horizon_deficit_hours,
            causality=causality,
            boundary_ambiguous=False,
            boundary_policy=None,
            nearest_grid_lat=nearest_grid_lat,
            nearest_grid_lon=nearest_grid_lon,
            nearest_grid_distance_km=nearest_grid_distance_km,
            member_count=member_count,
            missing_members=missing_members,
            training_allowed=training_allowed,
            members=members,
            temperature_metric=None,
            members_unit=None,
            selected_step_ranges=selected_step_ranges,
            selected_step_ranges_inner=None,
            selected_step_ranges_boundary=None,
        )

    @classmethod
    def make_low_track(
        cls,
        *,
        generated_at: str,
        data_version: str,
        physical_quantity: str,
        temperature_metric: str,
        param: str,
        paramId: int,
        short_name: str,
        step_type: str,
        aggregation_window_hours: int,
        city: str,
        lat: Optional[float],
        lon: Optional[float],
        unit: str,
        members_unit: str,
        timezone: str,
        manifest_sha256: str,
        manifest_hash: str,
        issue_time_utc: str,
        target_date_local: str,
        lead_day: int,
        lead_day_anchor: str,
        local_day_start_utc: Optional[str],
        local_day_end_utc: Optional[str],
        local_day_window: dict,
        step_horizon_hours: Optional[float],
        step_horizon_deficit_hours: Optional[float],
        causality: dict,
        boundary_ambiguous: bool,
        boundary_policy: dict,
        nearest_grid_lat: Optional[float],
        nearest_grid_lon: Optional[float],
        nearest_grid_distance_km: Optional[float],
        member_count: int,
        missing_members: list,
        training_allowed: bool,
        members: list,
        selected_step_ranges_inner: Optional[list] = None,
        selected_step_ranges_boundary: Optional[list] = None,
    ) -> "TiggeSnapshotPayload":
        """Convenience constructor for LOW track (mn2t6). boundary_policy is mandatory."""
        return cls(
            generated_at=generated_at,
            data_version=data_version,
            physical_quantity=physical_quantity,
            param=param,
            paramId=paramId,
            short_name=short_name,
            step_type=step_type,
            aggregation_window_hours=aggregation_window_hours,
            city=city,
            lat=lat,
            lon=lon,
            unit=unit,
            timezone=timezone,
            manifest_sha256=manifest_sha256,
            manifest_hash=manifest_hash,
            issue_time_utc=issue_time_utc,
            target_date_local=target_date_local,
            lead_day=lead_day,
            lead_day_anchor=lead_day_anchor,
            local_day_start_utc=local_day_start_utc,
            local_day_end_utc=local_day_end_utc,
            local_day_window=local_day_window,
            step_horizon_hours=step_horizon_hours,
            step_horizon_deficit_hours=step_horizon_deficit_hours,
            causality=causality,
            boundary_ambiguous=boundary_ambiguous,
            boundary_policy=boundary_policy,
            nearest_grid_lat=nearest_grid_lat,
            nearest_grid_lon=nearest_grid_lon,
            nearest_grid_distance_km=nearest_grid_distance_km,
            member_count=member_count,
            missing_members=missing_members,
            training_allowed=training_allowed,
            members=members,
            temperature_metric=temperature_metric,
            members_unit=members_unit,
            selected_step_ranges=None,
            selected_step_ranges_inner=selected_step_ranges_inner,
            selected_step_ranges_boundary=selected_step_ranges_boundary,
        )
