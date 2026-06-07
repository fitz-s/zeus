"""Dry-run gate for replacement forecast simple-switch readiness."""

from __future__ import annotations

import importlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from src.data.replacement_forecast_live_switch_surface import (
    REFIT_HANDOFF_FILE,
    REQUIRED_EVIDENCE_GATES,
    REQUIRED_FORECAST_TABLES,
    REQUIRED_LIVE_READ_FILES,
    ReplacementForecastLiveSwitchInput,
    ReplacementForecastLiveSwitchReport,
    build_replacement_forecast_live_switch_report,
)
from src.data.replacement_forecast_refit_handoff import refit_decision_from_handoff_payload
from src.data.replacement_forecast_runtime_policy import REQUIRED_FLAGS, resolve_replacement_forecast_runtime_policy
from src.state.db import _connect, list_sqlite_tables_and_views_read_only


OPTIONAL_DEPENDENCIES = ("requests", "ecmwf.opendata", "eccodes")
PROMOTION_EVIDENCE_FILE = "state/replacement_forecast_shadow/promotion_evidence.json"


@dataclass(frozen=True)
class ReplacementForecastLiveDryRunInput:
    root: Path
    runtime_flags: Mapping[str, object]
    enabled_evidence_gates: tuple[str, ...] = REQUIRED_EVIDENCE_GATES
    optional_dependencies: tuple[str, ...] = OPTIONAL_DEPENDENCIES
    source_fact_status_override: str | None = None
    data_fact_status_override: str | None = None
    assume_replacement_shadow_schema_initialized: bool = False
    assume_refit_handoff_available: bool = False
    assume_raw_artifact_lineage_available: bool = False


@dataclass(frozen=True)
class ReplacementForecastLiveDryRunReport:
    status: str
    reason_codes: tuple[str, ...]
    runtime_policy_status: str
    live_switch_report: ReplacementForecastLiveSwitchReport
    dependency_status: Mapping[str, str]
    source_fact_status: str
    data_fact_status: str
    forecast_db_exists: bool
    world_db_exists: bool
    trade_db_exists: bool
    forecast_tables: tuple[str, ...]
    world_tables: tuple[str, ...]
    trade_tables: tuple[str, ...]
    refit_handoff_status: str
    materialized_posterior_count: int
    shadow_decision_count: int
    latest_materialized_posterior: Mapping[str, object] | None
    latest_shadow_decision: Mapping[str, object] | None
    configured_refit_handoff_path: str
    configured_refit_handoff_status: str
    raw_artifact_lineage_status: str
    raw_artifact_lineage_counts: Mapping[str, int]
    latest_readiness_artifact_status: str
    latest_readiness_artifact_counts: Mapping[str, int]
    current_target_coverage_status: str
    current_target_coverage_counts: Mapping[str, int]
    current_target_coverage_missing_examples: tuple[Mapping[str, object], ...]
    assumptions: Mapping[str, object]

    @property
    def ok(self) -> bool:
        return self.status == "DRY_RUN_READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "runtime_policy_status": self.runtime_policy_status,
            "dependency_status": dict(self.dependency_status),
            "source_fact_status": self.source_fact_status,
            "data_fact_status": self.data_fact_status,
            "forecast_db_exists": self.forecast_db_exists,
            "world_db_exists": self.world_db_exists,
            "trade_db_exists": self.trade_db_exists,
            "forecast_tables": list(self.forecast_tables),
            "world_tables": list(self.world_tables),
            "trade_tables": list(self.trade_tables),
            "refit_handoff_status": self.refit_handoff_status,
            "materialized_posterior_count": self.materialized_posterior_count,
            "shadow_decision_count": self.shadow_decision_count,
            "latest_materialized_posterior": dict(self.latest_materialized_posterior) if self.latest_materialized_posterior is not None else None,
            "latest_shadow_decision": dict(self.latest_shadow_decision) if self.latest_shadow_decision is not None else None,
            "configured_refit_handoff_path": self.configured_refit_handoff_path,
            "configured_refit_handoff_status": self.configured_refit_handoff_status,
            "raw_artifact_lineage_status": self.raw_artifact_lineage_status,
            "raw_artifact_lineage_counts": dict(self.raw_artifact_lineage_counts),
            "latest_readiness_artifact_status": self.latest_readiness_artifact_status,
            "latest_readiness_artifact_counts": dict(self.latest_readiness_artifact_counts),
            "current_target_coverage_status": self.current_target_coverage_status,
            "current_target_coverage_counts": dict(self.current_target_coverage_counts),
            "current_target_coverage_missing_examples": [
                dict(item) for item in self.current_target_coverage_missing_examples
            ],
            "assumptions": dict(self.assumptions),
            "live_switch": self.live_switch_report.as_dict(),
        }


def _tables(path: Path) -> tuple[str, ...]:
    return tuple(sorted(list_sqlite_tables_and_views_read_only(path)))


def _status_line(root: Path, relative_path: str) -> str:
    path = root / relative_path
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[:20]
    except OSError:
        return "STALE_FOR_LIVE"
    for line in lines:
        if line.startswith("Status:"):
            return "CURRENT_FOR_LIVE" if "CURRENT_FOR_LIVE" in line else "STALE_FOR_LIVE"
    return "STALE_FOR_LIVE"


def _existing_required_files(root: Path, required: Sequence[str]) -> tuple[str, ...]:
    return tuple(item for item in required if (root / item).exists())


def _dependency_status(modules: Sequence[str]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for module in modules:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - diagnostic payload
            statuses[module] = f"MISSING:{exc.__class__.__name__}"
        else:
            statuses[module] = "OK"
    return statuses


def _replacement_materialization_inventory(forecast_db: Path) -> tuple[int, int, Mapping[str, object] | None, Mapping[str, object] | None]:
    if not forecast_db.exists():
        return 0, 0, None, None
    try:
        conn = sqlite3.connect(f"file:{forecast_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            tables = set(_tables(forecast_db))
            posterior_count = 0
            decision_count = 0
            latest_posterior: Mapping[str, object] | None = None
            latest_decision: Mapping[str, object] | None = None
            if "forecast_posteriors" in tables:
                posterior_count = int(
                    conn.execute(
                        """
                        SELECT COUNT(*)
                        FROM forecast_posteriors
                        WHERE source_id = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
                          AND trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY')
                        """
                    ).fetchone()[0]
                )
                row = conn.execute(
                    """
                    SELECT posterior_id, city, target_date, temperature_metric, data_version,
                           trade_authority_status, training_allowed, computed_at
                    FROM forecast_posteriors
                    WHERE source_id = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
                      AND trade_authority_status IN ('SHADOW_ONLY', 'SHADOW_VETO_ONLY')
                    ORDER BY posterior_id DESC
                    LIMIT 1
                    """
                ).fetchone()
                latest_posterior = dict(row) if row is not None else None
            if "replacement_shadow_decisions" in tables:
                decision_count = int(conn.execute("SELECT COUNT(*) FROM replacement_shadow_decisions").fetchone()[0])
                decision_columns = {
                    str(row["name"])
                    for row in conn.execute("PRAGMA table_info(replacement_shadow_decisions)").fetchall()
                }
                select_columns = [
                    column
                    for column in (
                        "decision_id",
                        "posterior_id",
                        "city",
                        "target_date",
                        "temperature_metric",
                        "baseline_direction",
                        "allowed_direction",
                        "trade_authority_status",
                        "recorded_at",
                    )
                    if column in decision_columns
                ]
                row = conn.execute(
                    f"""
                    SELECT {", ".join(select_columns)}
                    FROM replacement_shadow_decisions
                    ORDER BY recorded_at DESC, decision_id DESC
                    LIMIT 1
                    """
                ).fetchone()
                latest_decision = dict(row) if row is not None else None
            return posterior_count, decision_count, latest_posterior, latest_decision
        finally:
            conn.close()
    except Exception:
        return 0, 0, None, None


def _raw_artifact_lineage_inventory(forecast_db: Path, *, assume_available: bool) -> tuple[str, Mapping[str, int]]:
    required_sources = ("openmeteo_ecmwf_ifs_9km", "ecmwf_aifs_ens")
    empty_counts = {source_id: 0 for source_id in required_sources}
    if assume_available:
        return "ASSUMED_READY", empty_counts
    if not forecast_db.exists():
        return "MISSING_FORECAST_DB", empty_counts
    try:
        conn = sqlite3.connect(f"file:{forecast_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            tables = set(_tables(forecast_db))
            if "raw_forecast_artifacts" not in tables:
                return "MISSING_TABLE", empty_counts
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(raw_forecast_artifacts)").fetchall()
            }
            if not {"source_id", "source_available_at", "artifact_path"}.issubset(columns):
                return "UNREADABLE_SCHEMA", empty_counts
            rows = conn.execute(
                """
                SELECT source_id, COUNT(*) AS count
                FROM raw_forecast_artifacts
                WHERE source_id IN ('openmeteo_ecmwf_ifs_9km', 'ecmwf_aifs_ens')
                  AND source_available_at IS NOT NULL
                  AND source_available_at != ''
                  AND artifact_path IS NOT NULL
                  AND artifact_path != ''
                GROUP BY source_id
                """
            ).fetchall()
            counts = dict(empty_counts)
            for row in rows:
                counts[str(row["source_id"])] = int(row["count"])
            if any(counts[source_id] <= 0 for source_id in required_sources):
                return "MISSING_INPUT_FAMILY", counts
            return "READY", counts
        finally:
            conn.close()
    except Exception:
        return "UNREADABLE", empty_counts


def _latest_readiness_artifact_inventory(
    forecast_db: Path,
    *,
    latest_posterior: Mapping[str, object] | None,
    assume_available: bool,
) -> tuple[str, Mapping[str, int]]:
    required_roles = ("aifs_sampled_2t", "openmeteo_ifs9_anchor")
    empty_counts = {role: 0 for role in required_roles}
    if assume_available:
        return "ASSUMED_READY", empty_counts
    if latest_posterior is None:
        return "NOT_APPLICABLE_NO_POSTERIOR", empty_counts
    if not forecast_db.exists():
        return "MISSING_FORECAST_DB", empty_counts
    try:
        conn = sqlite3.connect(f"file:{forecast_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            tables = set(_tables(forecast_db))
            if "readiness_state" not in tables or "raw_forecast_artifacts" not in tables:
                return "MISSING_TABLE", empty_counts
            readiness_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(readiness_state)").fetchall()
            }
            artifact_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(raw_forecast_artifacts)").fetchall()
            }
            if not {"strategy_key", "computed_at", "dependency_json", "provenance_json"}.issubset(readiness_columns):
                return "UNREADABLE_SCHEMA", empty_counts
            if not {"artifact_id", "source_id", "data_version", "source_available_at"}.issubset(artifact_columns):
                return "UNREADABLE_SCHEMA", empty_counts
            city = str(latest_posterior.get("city") or "")
            target_date = str(latest_posterior.get("target_date") or "")
            metric = str(latest_posterior.get("temperature_metric") or "")
            selected = None
            if {"city", "target_local_date", "temperature_metric"}.issubset(readiness_columns):
                selected = conn.execute(
                    """
                    SELECT dependency_json, provenance_json
                    FROM readiness_state
                    WHERE strategy_key = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
                      AND city = ?
                      AND target_local_date = ?
                      AND temperature_metric = ?
                    ORDER BY computed_at DESC, recorded_at DESC
                    LIMIT 1
                    """,
                    (city, target_date, metric),
                ).fetchone()
            rows = [] if selected is not None else conn.execute(
                """
                SELECT dependency_json, provenance_json
                FROM readiness_state
                WHERE strategy_key = 'openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor'
                ORDER BY computed_at DESC, recorded_at DESC
                """
            ).fetchall()
            for candidate in rows:
                provenance = json.loads(str(candidate["provenance_json"] or "{}"))
                if not isinstance(provenance, Mapping):
                    continue
                if (
                    str(provenance.get("city") or "") == city
                    and str(provenance.get("target_date") or "") == target_date
                    and str(provenance.get("temperature_metric") or "") == metric
                ):
                    selected = candidate
                    break
            if selected is None:
                return "MISSING_READINESS_FOR_POSTERIOR", empty_counts
            dependency_payload = json.loads(str(selected["dependency_json"] or "{}"))
            if not isinstance(dependency_payload, Mapping):
                return "INVALID_DEPENDENCY_JSON", empty_counts
            dependencies = dependency_payload.get("dependencies", ())
            if not isinstance(dependencies, Sequence) or isinstance(dependencies, (str, bytes, bytearray)):
                return "INVALID_DEPENDENCY_JSON", empty_counts
            counts = dict(empty_counts)
            for role in required_roles:
                role_dependencies = [
                    item for item in dependencies
                    if isinstance(item, Mapping) and str(item.get("role") or "") == role
                ]
                if not role_dependencies:
                    return "MISSING_DEPENDENCY_ROLE", counts
                dependency = role_dependencies[0]
                artifact_id = dependency.get("artifact_id")
                if artifact_id in (None, ""):
                    return "MISSING_DEPENDENCY_ARTIFACT_ID", counts
                artifact = conn.execute(
                    """
                    SELECT source_id, data_version, source_available_at
                    FROM raw_forecast_artifacts
                    WHERE artifact_id = ?
                    """,
                    (int(artifact_id),),
                ).fetchone()
                if artifact is None:
                    return "MISSING_DEPENDENCY_ARTIFACT_ROW", counts
                if str(artifact["source_id"] or "") != str(dependency.get("source_id") or ""):
                    return "DEPENDENCY_ARTIFACT_SOURCE_MISMATCH", counts
                if str(artifact["data_version"] or "") != str(dependency.get("data_version") or ""):
                    return "DEPENDENCY_ARTIFACT_DATA_VERSION_MISMATCH", counts
                if str(artifact["source_available_at"] or "") != str(dependency.get("source_available_at") or ""):
                    return "DEPENDENCY_ARTIFACT_AVAILABLE_AT_MISMATCH", counts
                counts[role] += 1
            return "READY", counts
        finally:
            conn.close()
    except Exception:
        return "UNREADABLE", empty_counts


def _current_target_coverage_inventory(
    forecast_db: Path,
) -> tuple[str, Mapping[str, int], tuple[Mapping[str, object], ...]]:
    counts = {
        "target_count": 0,
        "posterior_covered_count": 0,
        "readiness_covered_count": 0,
        "missing_posterior_count": 0,
        "missing_readiness_count": 0,
    }
    if not forecast_db.exists():
        return "MISSING_FORECAST_DB", counts, ()
    try:
        from src.data.replacement_forecast_current_target_plan import build_replacement_forecast_current_target_plan

        plan = build_replacement_forecast_current_target_plan(
            forecast_db,
            require_raw_artifacts=False,
        )
    except Exception:
        return "UNREADABLE", counts, ()
    if plan.status == "BLOCKED":
        return "BLOCKED", counts, ()
    missing: list[Mapping[str, object]] = []
    for row in plan.rows:
        counts["target_count"] += 1
        posterior_count = int(row.posterior_count)
        readiness_count = int(row.readiness_count)
        if posterior_count > 0:
            counts["posterior_covered_count"] += 1
        else:
            counts["missing_posterior_count"] += 1
        if readiness_count > 0:
            counts["readiness_covered_count"] += 1
        else:
            counts["missing_readiness_count"] += 1
        if posterior_count <= 0 or readiness_count <= 0:
            missing.append(
                {
                    "city": row.city,
                    "target_date": row.target_date,
                    "temperature_metric": row.temperature_metric,
                    "baseline_source_run_id": row.baseline_source_run_id,
                    "market_bin_count": int(row.market_bin_count),
                    "posterior_count": posterior_count,
                    "readiness_count": readiness_count,
                }
            )
    if counts["target_count"] <= 0:
        return "NO_CURRENT_TARGETS", counts, ()
    if counts["missing_posterior_count"] or counts["missing_readiness_count"]:
        return "MISSING_REPLACEMENT_TARGET_COVERAGE", counts, tuple(missing[:20])
    return "READY", counts, ()


def _refit_handoff_status(root: Path, *, assume_available: bool) -> str:
    if assume_available:
        return "ASSUMED_READY"
    path = root / REFIT_HANDOFF_FILE
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return "INVALID:NOT_OBJECT"
        refit_decision_from_handoff_payload(payload)
    except FileNotFoundError:
        return "MISSING"
    except Exception as exc:  # noqa: BLE001 - diagnostic fail-closed status
        return f"INVALID:{exc.__class__.__name__}"
    return "READY"


def _configured_refit_handoff(root: Path, *, assume_available: bool) -> tuple[Path, str]:
    settings_path = root / "config" / "settings.json"
    raw_path: object = REFIT_HANDOFF_FILE
    try:
        settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(settings_payload, Mapping):
            shadow_cfg = settings_payload.get("replacement_forecast_shadow")
            if isinstance(shadow_cfg, Mapping):
                raw_path = shadow_cfg.get("refit_handoff_path") or REFIT_HANDOFF_FILE
    except FileNotFoundError:
        return root / REFIT_HANDOFF_FILE, "MISSING_SETTINGS"
    except Exception as exc:  # noqa: BLE001 - diagnostic fail-closed status
        return root / REFIT_HANDOFF_FILE, f"INVALID_SETTINGS:{exc.__class__.__name__}"
    configured_path = Path(str(raw_path))
    if not configured_path.is_absolute():
        configured_path = root / configured_path
    if assume_available:
        return configured_path, "ASSUMED_READY"
    try:
        payload = json.loads(configured_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return configured_path, "INVALID:NOT_OBJECT"
        refit_decision_from_handoff_payload(payload)
    except FileNotFoundError:
        return configured_path, "MISSING"
    except Exception as exc:  # noqa: BLE001 - diagnostic fail-closed status
        return configured_path, f"INVALID:{exc.__class__.__name__}"
    return configured_path, "READY"


def _configured_promotion_evidence(root: Path):
    from src.data.replacement_forecast_go_live_report import (
        replacement_forecast_capital_objective_evidence_from_payload,
        replacement_forecast_promotion_evidence_from_payload,
    )

    settings_path = root / "config" / "settings.json"
    raw_path: object = PROMOTION_EVIDENCE_FILE
    try:
        settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(settings_payload, Mapping):
            shadow_cfg = settings_payload.get("replacement_forecast_shadow")
            if isinstance(shadow_cfg, Mapping):
                raw_path = shadow_cfg.get("promotion_evidence_path") or PROMOTION_EVIDENCE_FILE
    except FileNotFoundError:
        return None, None, "MISSING_SETTINGS"
    except Exception as exc:  # noqa: BLE001 - diagnostic fail-closed status
        return None, None, f"INVALID_SETTINGS:{exc.__class__.__name__}"
    configured_path = Path(str(raw_path))
    if not configured_path.is_absolute():
        configured_path = root / configured_path
    try:
        payload = json.loads(configured_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return None, None, "INVALID:NOT_OBJECT"
        promotion = replacement_forecast_promotion_evidence_from_payload(payload)
        capital_objective = replacement_forecast_capital_objective_evidence_from_payload(payload)
    except FileNotFoundError:
        return None, None, "MISSING"
    except Exception as exc:  # noqa: BLE001 - diagnostic fail-closed status
        return None, None, f"INVALID:{exc.__class__.__name__}"
    return promotion, capital_objective, "READY"


def build_replacement_forecast_live_dry_run_report(
    request: ReplacementForecastLiveDryRunInput,
) -> ReplacementForecastLiveDryRunReport:
    if not isinstance(request, ReplacementForecastLiveDryRunInput):
        raise TypeError("request must be ReplacementForecastLiveDryRunInput")
    root = Path(request.root)
    flags = {key: bool(request.runtime_flags.get(key, False)) for key in REQUIRED_FLAGS}
    policy = resolve_replacement_forecast_runtime_policy(flags)
    forecast_db = root / "state" / "zeus-forecasts.db"
    world_db = root / "state" / "zeus-world.db"
    trade_db = root / "state" / "zeus_trades.db"
    forecast_tables = _tables(forecast_db)
    actual_forecast_tables = forecast_tables
    if request.assume_replacement_shadow_schema_initialized:
        forecast_tables = tuple(sorted(set(forecast_tables).union(REQUIRED_FORECAST_TABLES)))
    world_tables = _tables(world_db)
    trade_tables = _tables(trade_db)
    source_fact_status = request.source_fact_status_override or _status_line(root, "docs/operations/current_source_validity.md")
    data_fact_status = request.data_fact_status_override or _status_line(root, "docs/operations/current_data_state.md")
    if source_fact_status not in {"CURRENT_FOR_LIVE", "STALE_FOR_LIVE"}:
        raise ValueError("source_fact_status_override must be CURRENT_FOR_LIVE or STALE_FOR_LIVE")
    if data_fact_status not in {"CURRENT_FOR_LIVE", "STALE_FOR_LIVE"}:
        raise ValueError("data_fact_status_override must be CURRENT_FOR_LIVE or STALE_FOR_LIVE")
    available_files = _existing_required_files(
        root,
        REQUIRED_LIVE_READ_FILES,
    )
    if request.assume_refit_handoff_available and REFIT_HANDOFF_FILE not in available_files:
        available_files = tuple((*available_files, REFIT_HANDOFF_FILE))
    live_switch = build_replacement_forecast_live_switch_report(
        ReplacementForecastLiveSwitchInput(
            runtime_policy=policy,
            available_files=available_files,
            forecast_tables=forecast_tables,
            world_tables=world_tables,
            trade_tables=trade_tables,
            enabled_evidence_gates=request.enabled_evidence_gates,
            source_fact_status=source_fact_status,
            data_fact_status=data_fact_status,
        )
    )
    dependencies = _dependency_status(request.optional_dependencies)
    refit_handoff_status = _refit_handoff_status(root, assume_available=request.assume_refit_handoff_available)
    configured_refit_handoff_path, configured_refit_handoff_status = _configured_refit_handoff(
        root,
        assume_available=request.assume_refit_handoff_available,
    )
    posterior_count, decision_count, latest_posterior, latest_decision = _replacement_materialization_inventory(forecast_db)
    raw_artifact_lineage_status, raw_artifact_lineage_counts = _raw_artifact_lineage_inventory(
        forecast_db,
        assume_available=request.assume_raw_artifact_lineage_available,
    )
    latest_readiness_artifact_status, latest_readiness_artifact_counts = _latest_readiness_artifact_inventory(
        forecast_db,
        latest_posterior=latest_posterior,
        assume_available=request.assume_raw_artifact_lineage_available,
    )
    current_target_coverage_status, current_target_coverage_counts, current_target_coverage_missing_examples = (
        _current_target_coverage_inventory(forecast_db)
    )
    switch_surface_ready = live_switch.simple_switch_ready or live_switch.live_authority_ready
    reasons = list(live_switch.reason_codes if not switch_surface_ready else ())
    if refit_handoff_status.startswith("INVALID:"):
        reasons.append("REPLACEMENT_DRY_RUN_REFIT_HANDOFF_INVALID")
    if configured_refit_handoff_status == "MISSING":
        reasons.append("REPLACEMENT_DRY_RUN_CONFIGURED_REFIT_HANDOFF_MISSING")
    elif configured_refit_handoff_status.startswith("INVALID:"):
        reasons.append("REPLACEMENT_DRY_RUN_CONFIGURED_REFIT_HANDOFF_INVALID")
    elif configured_refit_handoff_status.startswith("INVALID_SETTINGS:"):
        reasons.append("REPLACEMENT_DRY_RUN_SETTINGS_INVALID")
    elif configured_refit_handoff_status == "MISSING_SETTINGS":
        reasons.append("REPLACEMENT_DRY_RUN_SETTINGS_MISSING")
    if raw_artifact_lineage_status not in {"READY", "ASSUMED_READY"}:
        reasons.append("REPLACEMENT_DRY_RUN_RAW_ARTIFACT_LINEAGE_NOT_READY")
    if latest_readiness_artifact_status not in {"READY", "ASSUMED_READY", "NOT_APPLICABLE_NO_POSTERIOR"}:
        reasons.append("REPLACEMENT_DRY_RUN_LATEST_READINESS_ARTIFACTS_NOT_READY")
    if (
        policy.can_read_shadow_posterior
        and not request.assume_replacement_shadow_schema_initialized
        and current_target_coverage_status not in {"READY", "NO_CURRENT_TARGETS"}
    ):
        reasons.append("REPLACEMENT_DRY_RUN_CURRENT_TARGET_COVERAGE_NOT_READY")
    if "requests" in dependencies and dependencies.get("requests") != "OK":
        reasons.append("REPLACEMENT_DRY_RUN_OPENMETEO_REQUESTS_MISSING")
    if "eccodes" in dependencies and dependencies.get("eccodes") != "OK":
        reasons.append("REPLACEMENT_DRY_RUN_AIFS_GRIB_DECODER_MISSING")
    if "ecmwf.opendata" in dependencies and dependencies.get("ecmwf.opendata") != "OK":
        reasons.append("REPLACEMENT_DRY_RUN_AIFS_DOWNLOAD_CLIENT_MISSING")
    status = "DRY_RUN_READY" if not reasons else "BLOCKED"
    return ReplacementForecastLiveDryRunReport(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons or ["REPLACEMENT_DRY_RUN_READY"])),
        runtime_policy_status=policy.status,
        live_switch_report=live_switch,
        dependency_status=dependencies,
        source_fact_status=source_fact_status,
        data_fact_status=data_fact_status,
        forecast_db_exists=forecast_db.exists(),
        world_db_exists=world_db.exists(),
        trade_db_exists=trade_db.exists(),
        forecast_tables=forecast_tables,
        world_tables=world_tables,
        trade_tables=trade_tables,
        refit_handoff_status=refit_handoff_status,
        materialized_posterior_count=posterior_count,
        shadow_decision_count=decision_count,
        latest_materialized_posterior=latest_posterior,
        latest_shadow_decision=latest_decision,
        configured_refit_handoff_path=str(configured_refit_handoff_path),
        configured_refit_handoff_status=configured_refit_handoff_status,
        raw_artifact_lineage_status=raw_artifact_lineage_status,
        raw_artifact_lineage_counts=raw_artifact_lineage_counts,
        latest_readiness_artifact_status=latest_readiness_artifact_status,
        latest_readiness_artifact_counts=latest_readiness_artifact_counts,
        current_target_coverage_status=current_target_coverage_status,
        current_target_coverage_counts=current_target_coverage_counts,
        current_target_coverage_missing_examples=current_target_coverage_missing_examples,
        assumptions={
            "source_fact_status_override": request.source_fact_status_override,
            "data_fact_status_override": request.data_fact_status_override,
            "assume_replacement_shadow_schema_initialized": request.assume_replacement_shadow_schema_initialized,
            "assume_refit_handoff_available": request.assume_refit_handoff_available,
            "assume_raw_artifact_lineage_available": request.assume_raw_artifact_lineage_available,
            "actual_missing_forecast_tables": [
                table for table in REQUIRED_FORECAST_TABLES if table not in set(actual_forecast_tables)
            ],
        },
    )
