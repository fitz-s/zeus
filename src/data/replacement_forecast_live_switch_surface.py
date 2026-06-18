"""Live switch surface report for replacement forecast integration.

This module is intentionally a read-model. It describes what the live runtime
switch is allowed to read, what it must not write, and which evidence gates must
be satisfied before the Open-Meteo ECMWF IFS 9km + AIFS sampled-2t path can
carry live authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from src.data.replacement_forecast_runtime_policy import ReplacementForecastRuntimePolicy
from src.state.db import list_sqlite_tables_and_views_read_only


CURRENT_SOURCE_FACT_FILE = "docs/operations/current_source_validity.md"
CURRENT_DATA_FACT_FILE = "docs/operations/current_data_state.md"
REFIT_HANDOFF_FILE = "state/replacement_forecast_live/refit_handoff.json"
REQUIRED_LIVE_READ_FILES = (
    "config/settings.json",
    "config/cities.json",
    "config/source_release_calendar.yaml",
    CURRENT_SOURCE_FACT_FILE,
    CURRENT_DATA_FACT_FILE,
    "state/zeus-forecasts.db",
    "state/zeus-world.db",
    "state/zeus_trades.db",
)
REQUIRED_FORECAST_TABLES = (
    "ensemble_snapshots",
    "source_run",
    "source_run_coverage",
    "readiness_state",
    "market_events",
    "settlement_outcomes",
    "executable_market_snapshots",
    "edli_no_submit_receipts",
    "raw_forecast_artifacts",
    "deterministic_forecast_anchors",
    "raw_model_forecasts",
    "forecast_posteriors",
)
REQUIRED_WORLD_TABLES = (
    "market_events",
)
REQUIRED_TRADE_TABLES = (
    "executable_market_snapshots",
)
PROHIBITED_SIMPLE_SWITCH_WRITES = (
    "settlement_outcomes",
    "settlements",
    "observations",
    "calibration_pairs",
    "calibration_pairs_v2",
    "platt_models",
    "emos_models",
    "orders",
    "venue_commands",
)
REQUIRED_EVIDENCE_GATES = (
    "runtime_policy_allows_live_authority",
    "baseline_executable_reader_ready",
    "aifs_sampled_2t_source_run_ready",
    "openmeteo_ecmwf_ifs9_anchor_ready",
    "derived_posterior_available_before_decision",
    "same_clob_market_snapshot_bound",
    "official_truth_filter_enforced",
    "calibration_refit_gate_not_promoted",
)
_FORBIDDEN_TRANSCRIPT_ALIAS = "h" + "3"


def _reject_alias(value: str, *, field_name: str) -> None:
    if _FORBIDDEN_TRANSCRIPT_ALIAS in value.lower():
        raise ValueError(f"{field_name} must use full replacement identity")


@dataclass(frozen=True)
class ReplacementForecastLiveSwitchInput:
    runtime_policy: ReplacementForecastRuntimePolicy
    available_files: tuple[str, ...]
    forecast_tables: tuple[str, ...]
    world_tables: tuple[str, ...]
    trade_tables: tuple[str, ...]
    enabled_evidence_gates: tuple[str, ...]
    proposed_write_tables: tuple[str, ...] = ()
    source_fact_status: str = "STALE_FOR_LIVE"
    data_fact_status: str = "STALE_FOR_LIVE"

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_policy, ReplacementForecastRuntimePolicy):
            raise TypeError("runtime_policy must be ReplacementForecastRuntimePolicy")
        for field_name in (
            "available_files",
            "forecast_tables",
            "world_tables",
            "trade_tables",
            "enabled_evidence_gates",
            "proposed_write_tables",
        ):
            values = getattr(self, field_name)
            if not isinstance(values, tuple):
                raise TypeError(f"{field_name} must be a tuple")
            for value in values:
                text = str(value or "")
                if not text:
                    raise ValueError(f"{field_name} entries must be non-empty")
                _reject_alias(text, field_name=field_name)
        if self.source_fact_status not in {"CURRENT_FOR_LIVE", "STALE_FOR_LIVE"}:
            raise ValueError("source_fact_status must be CURRENT_FOR_LIVE or STALE_FOR_LIVE")
        if self.data_fact_status not in {"CURRENT_FOR_LIVE", "STALE_FOR_LIVE"}:
            raise ValueError("data_fact_status must be CURRENT_FOR_LIVE or STALE_FOR_LIVE")


@dataclass(frozen=True)
class ReplacementForecastLiveSwitchReport:
    status: str
    reason_codes: tuple[str, ...]
    readable_files: tuple[str, ...]
    readable_forecast_tables: tuple[str, ...]
    readable_world_tables: tuple[str, ...]
    readable_trade_tables: tuple[str, ...]
    prohibited_write_tables: tuple[str, ...]
    missing_files: tuple[str, ...]
    missing_tables: tuple[str, ...]
    missing_evidence_gates: tuple[str, ...]
    proposed_forbidden_writes: tuple[str, ...]
    reversible: bool
    live_trade_authority: bool

    @property
    def simple_switch_ready(self) -> bool:
        return self.status == "SIMPLE_SWITCH_READY"

    @property
    def live_authority_ready(self) -> bool:
        return self.status == "LIVE_AUTHORITY_READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "readable_files": list(self.readable_files),
            "readable_forecast_tables": list(self.readable_forecast_tables),
            "readable_world_tables": list(self.readable_world_tables),
            "readable_trade_tables": list(self.readable_trade_tables),
            "prohibited_write_tables": list(self.prohibited_write_tables),
            "missing_files": list(self.missing_files),
            "missing_tables": list(self.missing_tables),
            "missing_evidence_gates": list(self.missing_evidence_gates),
            "proposed_forbidden_writes": list(self.proposed_forbidden_writes),
            "reversible": self.reversible,
            "simple_switch_ready": self.simple_switch_ready,
            "live_authority_ready": self.live_authority_ready,
            "live_trade_authority": self.live_trade_authority,
        }


def _missing(required: Iterable[str], available: Iterable[str]) -> tuple[str, ...]:
    available_set = {str(item) for item in available}
    return tuple(item for item in required if item not in available_set)


def _existing_files(root: Path, required: Iterable[str]) -> tuple[str, ...]:
    return tuple(path for path in required if (root / path).exists())


def _sqlite_tables(path: Path) -> tuple[str, ...]:
    return list_sqlite_tables_and_views_read_only(path)


def _existing_tables(root: Path, db_relative_path: str, required: Iterable[str]) -> tuple[str, ...]:
    available = set(_sqlite_tables(root / db_relative_path))
    return tuple(table for table in required if table in available)


def _current_fact_status(root: Path, relative_path: str) -> str:
    path = root / relative_path
    try:
        first_lines = path.read_text(encoding="utf-8").splitlines()[:20]
    except OSError:
        return "STALE_FOR_LIVE"
    for line in first_lines:
        if line.startswith("Status:"):
            return "CURRENT_FOR_LIVE" if "CURRENT_FOR_LIVE" in line else "STALE_FOR_LIVE"
    return "STALE_FOR_LIVE"


def build_replacement_forecast_live_switch_report(
    request: ReplacementForecastLiveSwitchInput,
) -> ReplacementForecastLiveSwitchReport:
    """Describe whether the replacement path can be switched on safely."""

    if not isinstance(request, ReplacementForecastLiveSwitchInput):
        raise TypeError("request must be ReplacementForecastLiveSwitchInput")
    missing_files = _missing(REQUIRED_LIVE_READ_FILES, request.available_files)
    missing_forecast_tables = _missing(REQUIRED_FORECAST_TABLES, request.forecast_tables)
    missing_world_tables = _missing(REQUIRED_WORLD_TABLES, request.world_tables)
    missing_trade_tables = _missing(REQUIRED_TRADE_TABLES, request.trade_tables)
    missing_tables = (*missing_forecast_tables, *missing_world_tables, *missing_trade_tables)
    missing_evidence = _missing(REQUIRED_EVIDENCE_GATES, request.enabled_evidence_gates)
    forbidden_writes = tuple(
        table for table in request.proposed_write_tables if table in PROHIBITED_SIMPLE_SWITCH_WRITES
    )
    reasons: list[str] = []
    if not request.runtime_policy.can_initiate_trade:
        reasons.append("REPLACEMENT_SWITCH_POLICY_NOT_READABLE")
    if request.source_fact_status != "CURRENT_FOR_LIVE":
        reasons.append("REPLACEMENT_SWITCH_SOURCE_FACTS_STALE")
    if request.data_fact_status != "CURRENT_FOR_LIVE":
        reasons.append("REPLACEMENT_SWITCH_DATA_FACTS_STALE")
    if missing_files:
        reasons.append("REPLACEMENT_SWITCH_MISSING_READ_FILES")
    if missing_tables:
        reasons.append("REPLACEMENT_SWITCH_MISSING_READ_TABLES")
    if missing_evidence:
        reasons.append("REPLACEMENT_SWITCH_MISSING_EVIDENCE_GATES")
    if forbidden_writes:
        reasons.append("REPLACEMENT_SWITCH_FORBIDDEN_WRITES_PROPOSED")
    if reasons:
        status = "BLOCKED"
    elif request.runtime_policy.can_initiate_trade:
        status = "LIVE_AUTHORITY_READY"
    else:
        status = "SIMPLE_SWITCH_READY"
    return ReplacementForecastLiveSwitchReport(
        status=status,
        reason_codes=tuple(
            reasons
            or (
                ("REPLACEMENT_SWITCH_LIVE_AUTHORITY_READY",)
                if request.runtime_policy.can_initiate_trade
                else ("REPLACEMENT_SWITCH_READ_ONLY_REVERSIBLE_READY",)
            )
        ),
        readable_files=tuple(REQUIRED_LIVE_READ_FILES),
        readable_forecast_tables=tuple(REQUIRED_FORECAST_TABLES),
        readable_world_tables=tuple(REQUIRED_WORLD_TABLES),
        readable_trade_tables=tuple(REQUIRED_TRADE_TABLES),
        prohibited_write_tables=tuple(PROHIBITED_SIMPLE_SWITCH_WRITES),
        missing_files=missing_files,
        missing_tables=missing_tables,
        missing_evidence_gates=missing_evidence,
        proposed_forbidden_writes=forbidden_writes,
        reversible=not forbidden_writes,
        live_trade_authority=request.runtime_policy.can_initiate_trade,
    )


def build_replacement_forecast_live_switch_input_from_current_state(
    root: Path,
    *,
    runtime_policy: ReplacementForecastRuntimePolicy,
    enabled_evidence_gates: tuple[str, ...] = (),
    proposed_write_tables: tuple[str, ...] = (),
) -> ReplacementForecastLiveSwitchInput:
    """Build a switch input from actual repo files and SQLite table inventory.

    The function is read-only and deliberately conservative. Missing or
    unreadable files/DBs are omitted from the available inventory, and stale or
    unreadable current-fact docs remain ``STALE_FOR_LIVE``.
    """

    if not isinstance(root, Path):
        raise TypeError("root must be pathlib.Path")
    root = root.resolve()
    available_files = _existing_files(root, REQUIRED_LIVE_READ_FILES)
    forecast_tables = _existing_tables(root, "state/zeus-forecasts.db", REQUIRED_FORECAST_TABLES)
    world_tables = _existing_tables(root, "state/zeus-world.db", REQUIRED_WORLD_TABLES)
    trade_tables = _existing_tables(root, "state/zeus_trades.db", REQUIRED_TRADE_TABLES)
    return ReplacementForecastLiveSwitchInput(
        runtime_policy=runtime_policy,
        available_files=available_files,
        forecast_tables=forecast_tables,
        world_tables=world_tables,
        trade_tables=trade_tables,
        enabled_evidence_gates=enabled_evidence_gates,
        proposed_write_tables=proposed_write_tables,
        source_fact_status=_current_fact_status(root, CURRENT_SOURCE_FACT_FILE),
        data_fact_status=_current_fact_status(root, CURRENT_DATA_FACT_FILE),
    )


def default_replacement_forecast_live_switch_inventory() -> Mapping[str, tuple[str, ...]]:
    """Return the canonical read/write inventory used by reports and tests."""

    return {
        "required_live_read_files": tuple(REQUIRED_LIVE_READ_FILES),
        "current_source_fact_file": (CURRENT_SOURCE_FACT_FILE,),
        "current_data_fact_file": (CURRENT_DATA_FACT_FILE,),
        "refit_handoff_file": (REFIT_HANDOFF_FILE,),
        "required_forecast_tables": tuple(REQUIRED_FORECAST_TABLES),
        "required_world_tables": tuple(REQUIRED_WORLD_TABLES),
        "required_trade_tables": tuple(REQUIRED_TRADE_TABLES),
        "prohibited_simple_switch_writes": tuple(PROHIBITED_SIMPLE_SWITCH_WRITES),
        "required_evidence_gates": tuple(REQUIRED_EVIDENCE_GATES),
    }
