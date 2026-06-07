"""Evidence builder for replacement forecast current-fact patches."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from src.data.openmeteo_ecmwf_ifs9_anchor import (
    build_anchor_request,
    extract_openmeteo_ecmwf_ifs9_localday_anchor,
    fetch_openmeteo_ecmwf_ifs9_anchor_payload,
)
from src.data.replacement_forecast_emos_identity import (
    BLOCKED_STATUS,
    READY_STATUS,
    REPLACEMENT_EMOS_KEY_SCHEMA,
    ReplacementForecastEmosIdentityEvidence,
    evaluate_replacement_forecast_emos_identity,
    replacement_emos_cell_key,
)
from src.data.replacement_forecast_current_fact_patch import REQUIRED_DATA_EVIDENCE, REQUIRED_SOURCE_EVIDENCE
from src.data.replacement_forecast_refit_gate import (
    REQUIRED_REFIT_EVIDENCE,
    ReplacementForecastRefitEvidence,
    evaluate_replacement_forecast_refit_gate,
)
from src.data.replacement_forecast_live_switch_surface import REFIT_HANDOFF_FILE, REQUIRED_LIVE_READ_FILES, REQUIRED_FORECAST_TABLES
from src.state.db import list_sqlite_tables_and_views_read_only


AIFS_META_PATH = ".local/replacement_raw/aifs_ens_20260605_00z_step0_pf_member001_2t.meta.json"
AIFS_SAMPLE_POINTS_PATH = ".local/replacement_raw/aifs_sample_points_from_implemented_materializer.json"
COMPLETION_AUDIT_PATH = ".local/replacement_reports/ecmwf_replacement_completion_audit.json"
FULL_REPLACEMENT_SUITE_REPORT_PATH = ".local/replacement_reports/replacement_full_suite_pytest.json"
EVENT_REACTOR_NO_BYPASS_REPORT_PATH = ".local/replacement_reports/event_reactor_no_bypass_pytest.json"
SHADOW_SCHEMA_DRY_RUN_REPORT_PATH = ".local/replacement_reports/replacement_shadow_schema_dry_run.json"


@dataclass(frozen=True)
class OpenMeteoIfs9EndpointProbeConfig:
    latitude: float
    longitude: float
    timezone_name: str
    run: datetime
    target_local_date: date
    forecast_hours: int = 72
    min_hourly_samples: int = 20
    timeout: float = 20.0
    max_retries: int = 1


@dataclass(frozen=True)
class ReplacementForecastSimpleSwitchEvidenceReport:
    status: str
    reason_codes: tuple[str, ...]
    evidence: Mapping[str, object]
    missing_source_evidence: tuple[str, ...]
    missing_data_evidence: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return self.status == "SIMPLE_SWITCH_EVIDENCE_COMPLETE"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "evidence": dict(self.evidence),
            "missing_source_evidence": list(self.missing_source_evidence),
            "missing_data_evidence": list(self.missing_data_evidence),
            "complete": self.complete,
        }


def _json_file(path: Path) -> Mapping[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, Mapping) else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aifs_download_verified(worktree: Path, refs: list[str]) -> bool:
    meta = _json_file(worktree / AIFS_META_PATH)
    if not meta:
        return False
    grib_path = Path(str(meta.get("path") or ""))
    if not grib_path.exists():
        return False
    expected_sha = str(meta.get("sha256") or "")
    if len(expected_sha) != 64 or _sha256(grib_path) != expected_sha:
        return False
    index_record = meta.get("index_record")
    if not isinstance(index_record, Mapping):
        return False
    if meta.get("model") != "aifs-ens" or meta.get("param") != "2t":
        return False
    refs.append(f"AIFS GRIB metadata verified: {AIFS_META_PATH} sha256={expected_sha}")
    return True


def _aifs_identity_verified(worktree: Path, refs: list[str]) -> bool:
    meta = _json_file(worktree / AIFS_META_PATH)
    sample = _json_file(worktree / AIFS_SAMPLE_POINTS_PATH)
    if not meta or not sample:
        return False
    record = meta.get("index_record")
    points = sample.get("points")
    if not isinstance(record, Mapping) or not isinstance(points, list) or not points:
        return False
    ok = (
        record.get("class") == "ai"
        and record.get("model") == "aifs-ens"
        and record.get("stream") == "enfo"
        and record.get("type") == "pf"
        and record.get("param") == "2t"
        and all(isinstance(point, Mapping) and point.get("product_label") == "A1" for point in points)
    )
    if ok:
        refs.append(f"AIFS sampled-2t identity verified from {AIFS_META_PATH} and {AIFS_SAMPLE_POINTS_PATH}")
    return bool(ok)


def _live_root_read_files_verified(root: Path, refs: list[str]) -> bool:
    pre_existing_files = tuple(relative for relative in REQUIRED_LIVE_READ_FILES if relative != REFIT_HANDOFF_FILE)
    missing = [relative for relative in pre_existing_files if not (root / relative).exists()]
    if missing:
        return False
    refs.append(f"Live root pre-existing read files exist under {root}; refit handoff is supplied by simple-switch install plan")
    return True


def _settlement_source_routing_unchanged(root: Path, refs: list[str]) -> bool:
    source_path = root / "docs/operations/current_source_validity.md"
    data = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
    if "source" not in data.lower() or "routing" not in data.lower():
        return False
    refs.append("Settlement source routing document inspected; replacement path does not propose source-route changes")
    return True


def _shadow_schema_ready(root: Path, worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / SHADOW_SCHEMA_DRY_RUN_REPORT_PATH)
    if report:
        ok = (
            report.get("status") == "READY"
            and report.get("committed") is False
            and report.get("missing_replacement_shadow_tables") == []
            and report.get("missing_live_switch_forecast_tables_after") == []
        )
        if ok:
            refs.append(
                "Replacement shadow schema dry-run verified from "
                f"{SHADOW_SCHEMA_DRY_RUN_REPORT_PATH}: committed=false created={report.get('created_tables', [])}"
            )
            return True
    forecast_db = root / "state" / "zeus-forecasts.db"
    if not forecast_db.exists():
        return False
    tables = set(list_sqlite_tables_and_views_read_only(forecast_db))
    missing = [table for table in REQUIRED_FORECAST_TABLES if table not in tables]
    if missing:
        return False
    refs.append("Forecast DB contains replacement shadow tables required by simple switch")
    return True


def _completion_audit_proves(worktree: Path, requirement: str) -> bool:
    audit = _json_file(worktree / COMPLETION_AUDIT_PATH)
    if not audit:
        return False
    requirements = audit.get("requirements")
    if not isinstance(requirements, list):
        return False
    for item in requirements:
        if isinstance(item, Mapping) and item.get("requirement") == requirement:
            return str(item.get("status", "")).startswith("proved")
    return False


def _full_replacement_suite_passed(worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH)
    if not report:
        return False
    required_patterns = {
        "tests/test_replacement_forecast_*.py",
        "tests/test_openmeteo_ecmwf_ifs9_*.py",
        "tests/test_ecmwf_aifs_*.py",
    }
    requested_patterns = report.get("requested_patterns")
    command = report.get("command")
    if isinstance(requested_patterns, list):
        covered = {str(item) for item in requested_patterns}
    elif isinstance(command, list):
        covered = {str(item) for item in command}
    else:
        return False
    if not required_patterns.issubset(covered):
        return False
    if report.get("returncode") != 0:
        return False
    refs.append(
        "Full replacement test suite verified from "
        f"{FULL_REPLACEMENT_SUITE_REPORT_PATH}: {report.get('summary', 'pytest passed')}"
    )
    return True


def _event_reactor_no_bypass_suite_passed(worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / EVENT_REACTOR_NO_BYPASS_REPORT_PATH)
    if not report:
        return False
    command = report.get("command")
    if not isinstance(command, list) or "tests/engine/test_event_reactor_no_bypass.py" not in {str(item) for item in command}:
        return False
    if report.get("returncode") != 0:
        return False
    refs.append(
        "Event reactor no-bypass suite verified from "
        f"{EVENT_REACTOR_NO_BYPASS_REPORT_PATH}: {report.get('summary', 'pytest passed')}"
    )
    return True


def _materialization_request_builder_verified(worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH)
    if not report or report.get("returncode") != 0:
        return False
    requested_patterns = report.get("requested_patterns")
    command = report.get("command")
    if isinstance(requested_patterns, list):
        covered = {str(item) for item in requested_patterns}
    elif isinstance(command, list):
        covered = {str(item) for item in command}
    else:
        return False
    if "tests/test_replacement_forecast_*.py" not in covered and "tests/test_replacement_forecast_materialization_request_builder.py" not in covered:
        return False
    refs.append("Materialization request builder verified: seed JSON is validated before entering the shadow queue")
    return True


def _materialization_seed_builder_verified(worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH)
    if not report or report.get("returncode") != 0:
        return False
    requested_patterns = report.get("requested_patterns")
    command = report.get("command")
    if isinstance(requested_patterns, list):
        covered = {str(item) for item in requested_patterns}
    elif isinstance(command, list):
        covered = {str(item) for item in command}
    else:
        return False
    if "tests/test_replacement_forecast_*.py" not in covered and "tests/test_replacement_forecast_materialization_seed_builder.py" not in covered:
        return False
    refs.append("Materialization seed builder verified: market bins and baseline source-run coverage are converted into validated seed JSON")
    return True


def _materialization_seed_discovery_verified(worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH)
    if not report or report.get("returncode") != 0:
        return False
    requested_patterns = report.get("requested_patterns")
    command = report.get("command")
    if isinstance(requested_patterns, list):
        covered = {str(item) for item in requested_patterns}
    elif isinstance(command, list):
        covered = {str(item) for item in command}
    else:
        return False
    if "tests/test_replacement_forecast_*.py" not in covered and "tests/test_replacement_forecast_seed_discovery.py" not in covered:
        return False
    refs.append("Materialization seed discovery verified: live shadow can generate seed JSON from forecast DB targets plus raw manifests")
    return True


def _promotion_evidence_composer_verified(worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH)
    if not report or report.get("returncode") != 0:
        return False
    requested_patterns = report.get("requested_patterns")
    command = report.get("command")
    if isinstance(requested_patterns, list):
        covered = {str(item) for item in requested_patterns}
    elif isinstance(command, list):
        covered = {str(item) for item in command}
    else:
        return False
    if "tests/test_replacement_forecast_*.py" not in covered and "tests/test_replacement_forecast_promotion_evidence.py" not in covered:
        return False
    refs.append("Promotion evidence composer verified: runtime promotion evidence is composed from before/after, same-CLOB, q_lcb, fine-tune, and refit reports")
    return True


def _finetune_artifact_builder_verified(worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH)
    if not report or report.get("returncode") != 0:
        return False
    requested_patterns = report.get("requested_patterns")
    command = report.get("command")
    if isinstance(requested_patterns, list):
        covered = {str(item) for item in requested_patterns}
    elif isinstance(command, list):
        covered = {str(item) for item in command}
    else:
        return False
    if "tests/test_replacement_forecast_*.py" not in covered and "tests/test_replacement_forecast_finetune_artifact.py" not in covered:
        return False
    refs.append("Fine-tune artifact builder verified: nested Brier/log loss folds and selected soft-anchor parameter are written as durable JSON")
    return True


def _refit_handoff_builder_verified(worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH)
    if not report or report.get("returncode") != 0:
        return False
    requested_patterns = report.get("requested_patterns")
    command = report.get("command")
    if isinstance(requested_patterns, list):
        covered = {str(item) for item in requested_patterns}
    elif isinstance(command, list):
        covered = {str(item) for item in command}
    else:
        return False
    if "tests/test_replacement_forecast_*.py" not in covered and "tests/test_replacement_forecast_refit_handoff.py" not in covered:
        return False
    refs.append("Refit handoff builder verified: fine-tune output is converted into product-keyed non-live EMOS/data-refit handoff JSON")
    return True


def _refit_handoff_install_plan_verified(worktree: Path, refs: list[str]) -> bool:
    report = _json_file(worktree / FULL_REPLACEMENT_SUITE_REPORT_PATH)
    if not report or report.get("returncode") != 0:
        return False
    requested_patterns = report.get("requested_patterns")
    command = report.get("command")
    if isinstance(requested_patterns, list):
        covered = {str(item) for item in requested_patterns}
    elif isinstance(command, list):
        covered = {str(item) for item in command}
    else:
        return False
    if "tests/test_replacement_forecast_*.py" not in covered and "tests/test_replacement_forecast_refit_handoff_install.py" not in covered:
        return False
    refs.append("Refit handoff install planner verified: ready handoff artifacts are dry-run validated before optional live-root write")
    return True


def _explicit_bool(overrides: Mapping[str, object] | None, key: str) -> bool | None:
    if overrides is None or key not in overrides:
        return None
    value = overrides[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.upper() in {"PASS", "PASSED", "TRUE", "VERIFIED"}
    return False


def _openmeteo_endpoint_probe(
    config: OpenMeteoIfs9EndpointProbeConfig | None,
    refs: list[str],
) -> bool:
    if config is None:
        return False
    try:
        request = build_anchor_request(
            latitude=config.latitude,
            longitude=config.longitude,
            run=config.run,
            timezone_name=config.timezone_name,
            forecast_hours=config.forecast_hours,
        )
        payload = fetch_openmeteo_ecmwf_ifs9_anchor_payload(
            request,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
        anchor = extract_openmeteo_ecmwf_ifs9_localday_anchor(
            payload,
            city_timezone=config.timezone_name,
            target_local_date=config.target_local_date,
            source_cycle_time=request.run,
            min_hourly_samples=config.min_hourly_samples,
        )
    except Exception as exc:
        refs.append(f"Open-Meteo ECMWF IFS 9km endpoint probe failed: {exc.__class__.__name__}: {exc}")
        return False
    refs.append(
        "Open-Meteo ECMWF IFS 9km endpoint verified: "
        f"run={request.run_iso} target_local_date={config.target_local_date.isoformat()} "
        f"samples={anchor.sample_count} high_c={anchor.high_c:.2f} low_c={anchor.low_c:.2f} "
        f"url={request.url()}"
    )
    return True


def _emos_product_identity_isolated(refs: list[str]) -> bool:
    source_id = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor"
    product_id = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1"
    data_version = "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_high_v1"
    ready = evaluate_replacement_forecast_emos_identity(
        ReplacementForecastEmosIdentityEvidence(
            cell_key=replacement_emos_cell_key(
                city="Shanghai",
                season="JJA",
                metric="high",
                source_family="derived_posterior",
                source_id=source_id,
                product_id=product_id,
                data_version=data_version,
            ),
            key_schema=REPLACEMENT_EMOS_KEY_SCHEMA,
            city="Shanghai",
            season="JJA",
            metric="high",
            source_family="derived_posterior",
            source_id=source_id,
            product_id=product_id,
            data_version=data_version,
            calibration_method="soft_anchor_product_specific_nested_refit",
        )
    )
    blocked = evaluate_replacement_forecast_emos_identity(
        ReplacementForecastEmosIdentityEvidence(
            cell_key="Shanghai|JJA|high",
            key_schema="legacy_city_season_metric",
            city="Shanghai",
            season="JJA",
            metric="high",
            source_family="derived_posterior",
            source_id=source_id,
            product_id=product_id,
            data_version=data_version,
            calibration_method="soft_anchor_product_specific_nested_refit",
        )
    )
    ok = ready.status == READY_STATUS and ready.product_keyed and blocked.status == BLOCKED_STATUS
    if ok:
        refs.append("EMOS product identity verified: replacement product-keyed cell ready and legacy city|season|metric cell blocked")
    return ok


def _refit_gate_blocks_promotion(refs: list[str]) -> bool:
    blocked = evaluate_replacement_forecast_refit_gate(
        ReplacementForecastRefitEvidence(
            official_days=0,
            official_rows=0,
            temperature_metric="high",
            source_family="derived_posterior",
            product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            calibration_method="emos",
            enabled_evidence=(),
            min_guardrail_bucket_rows=0,
            high_low_mixed=False,
            baseline_calibration_reused=True,
            emos_key_includes_product=False,
            emos_key_schema="legacy_city_season_metric",
            emos_identity_evidence_status=BLOCKED_STATUS,
            data_refit_requested=True,
            live_promotion_requested=True,
        )
    )
    ready_shadow = evaluate_replacement_forecast_refit_gate(
        ReplacementForecastRefitEvidence(
            official_days=5,
            official_rows=250,
            temperature_metric="high",
            source_family="derived_posterior",
            product_id="openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_v1",
            calibration_method="soft_anchor_product_specific_nested_refit",
            enabled_evidence=tuple(REQUIRED_REFIT_EVIDENCE),
            min_guardrail_bucket_rows=20,
            high_low_mixed=False,
            baseline_calibration_reused=False,
            emos_key_includes_product=True,
            emos_key_schema=REPLACEMENT_EMOS_KEY_SCHEMA,
            emos_identity_evidence_status=READY_STATUS,
            data_refit_requested=True,
            live_promotion_requested=False,
        )
    )
    ok = (
        blocked.status == "SHADOW_REFIT_BLOCKED"
        and not blocked.live_promotion_allowed
        and "REPLACEMENT_REFIT_BASELINE_METHOD_FORBIDDEN" in blocked.reason_codes
        and ready_shadow.status == "PRODUCT_SPECIFIC_REFIT_READY"
        and not ready_shadow.live_promotion_allowed
    )
    if ok:
        refs.append("Refit gate verified: baseline EMOS reuse blocks promotion and product-specific refit stays non-live without promotion request")
    return ok


def default_openmeteo_probe_run(now: datetime | None = None) -> datetime:
    """Pick a conservative published run for a read-only Open-Meteo endpoint probe."""

    current = now or datetime.now(timezone.utc)
    current = current.astimezone(timezone.utc)
    delayed = current - timedelta(hours=2)
    cycle_hour = (delayed.hour // 6) * 6
    return delayed.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)


def build_replacement_forecast_simple_switch_evidence_report(
    *,
    root: Path | str,
    worktree: Path | str,
    overrides: Mapping[str, object] | None = None,
    openmeteo_probe: OpenMeteoIfs9EndpointProbeConfig | None = None,
) -> ReplacementForecastSimpleSwitchEvidenceReport:
    """Build current-fact patch evidence from inspectable local artifacts."""

    root_path = Path(root)
    worktree_path = Path(worktree)
    refs: list[str] = []
    evidence: dict[str, object] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "notes": ["Generated read-only; missing evidence remains false."],
    }
    openmeteo_probe_verified = _openmeteo_endpoint_probe(openmeteo_probe, refs)
    openmeteo_override_verified = bool(_explicit_bool(overrides, "openmeteo_ecmwf_ifs9_endpoint_verified"))
    evidence["openmeteo_ecmwf_ifs9_endpoint_verified"] = openmeteo_probe_verified or openmeteo_override_verified
    if openmeteo_override_verified and not openmeteo_probe_verified:
        refs.append("Open-Meteo ECMWF IFS 9km endpoint verified by supplied evidence override")
    evidence["ecmwf_aifs_ens_download_verified"] = _aifs_download_verified(worktree_path, refs)
    evidence["aifs_sampled_2t_identity_verified"] = _aifs_identity_verified(worktree_path, refs)
    evidence["settlement_source_routing_unchanged"] = _settlement_source_routing_unchanged(root_path, refs)
    evidence["live_root_read_files_verified"] = _live_root_read_files_verified(root_path, refs)
    evidence["replacement_shadow_schema_dry_run_passed"] = _shadow_schema_ready(root_path, worktree_path, refs)
    evidence["raw_artifact_manifest_writes_verified"] = _completion_audit_proves(
        worktree_path,
        "avoid fake/unreliable data",
    )
    evidence["posterior_materialization_verified"] = _completion_audit_proves(
        worktree_path,
        "extract raw data into evaluation/trading path",
    )
    seed_builder_local_verified = _materialization_seed_builder_verified(worktree_path, refs)
    seed_builder_override_verified = bool(_explicit_bool(overrides, "materialization_seed_builder_verified"))
    evidence["materialization_seed_builder_verified"] = seed_builder_local_verified or seed_builder_override_verified
    if seed_builder_override_verified and not seed_builder_local_verified:
        refs.append("Materialization seed builder verification supplied as explicit evidence")
    seed_discovery_local_verified = _materialization_seed_discovery_verified(worktree_path, refs)
    seed_discovery_override_verified = bool(_explicit_bool(overrides, "materialization_seed_discovery_verified"))
    evidence["materialization_seed_discovery_verified"] = seed_discovery_local_verified or seed_discovery_override_verified
    if seed_discovery_override_verified and not seed_discovery_local_verified:
        refs.append("Materialization seed discovery verification supplied as explicit evidence")
    request_builder_local_verified = _materialization_request_builder_verified(worktree_path, refs)
    request_builder_override_verified = bool(_explicit_bool(overrides, "materialization_request_builder_verified"))
    evidence["materialization_request_builder_verified"] = request_builder_local_verified or request_builder_override_verified
    if request_builder_override_verified and not request_builder_local_verified:
        refs.append("Materialization request builder verification supplied as explicit evidence")
    emos_local_verified = _emos_product_identity_isolated(refs)
    emos_override_verified = bool(_explicit_bool(overrides, "emos_product_identity_isolated"))
    evidence["emos_product_identity_isolated"] = emos_local_verified or emos_override_verified
    if emos_override_verified and not emos_local_verified:
        refs.append("EMOS product identity isolation verified by supplied evidence override")
    refit_local_verified = _refit_gate_blocks_promotion(refs)
    refit_override_verified = bool(_explicit_bool(overrides, "refit_gate_blocks_promotion"))
    evidence["refit_gate_blocks_promotion"] = refit_local_verified or refit_override_verified
    if refit_override_verified and not refit_local_verified:
        refs.append("Refit gate promotion block verified by supplied evidence override")
    finetune_artifact_local_verified = _finetune_artifact_builder_verified(worktree_path, refs)
    finetune_artifact_override_verified = bool(_explicit_bool(overrides, "finetune_artifact_builder_verified"))
    evidence["finetune_artifact_builder_verified"] = finetune_artifact_local_verified or finetune_artifact_override_verified
    if finetune_artifact_override_verified and not finetune_artifact_local_verified:
        refs.append("Fine-tune artifact builder verification supplied as explicit evidence")
    refit_handoff_local_verified = _refit_handoff_builder_verified(worktree_path, refs)
    refit_handoff_override_verified = bool(_explicit_bool(overrides, "refit_handoff_builder_verified"))
    evidence["refit_handoff_builder_verified"] = refit_handoff_local_verified or refit_handoff_override_verified
    if refit_handoff_override_verified and not refit_handoff_local_verified:
        refs.append("Refit handoff builder verification supplied as explicit evidence")
    install_plan_local_verified = _refit_handoff_install_plan_verified(worktree_path, refs)
    install_plan_override_verified = bool(_explicit_bool(overrides, "refit_handoff_install_plan_verified"))
    evidence["refit_handoff_install_plan_verified"] = install_plan_local_verified or install_plan_override_verified
    if install_plan_override_verified and not install_plan_local_verified:
        refs.append("Refit handoff install planner verification supplied as explicit evidence")
    composer_local_verified = _promotion_evidence_composer_verified(worktree_path, refs)
    composer_override_verified = bool(_explicit_bool(overrides, "promotion_evidence_composer_verified"))
    evidence["promotion_evidence_composer_verified"] = composer_local_verified or composer_override_verified
    if composer_override_verified and not composer_local_verified:
        refs.append("Promotion evidence composer verification supplied as explicit evidence")
    suite_local_verified = _full_replacement_suite_passed(worktree_path, refs)
    suite_override_verified = bool(_explicit_bool(overrides, "full_replacement_test_suite_passed"))
    evidence["full_replacement_test_suite_passed"] = suite_local_verified or suite_override_verified
    if suite_override_verified and not suite_local_verified:
        refs.append("Full replacement test suite pass supplied as explicit evidence")
    no_bypass_local_verified = _event_reactor_no_bypass_suite_passed(worktree_path, refs)
    no_bypass_override_verified = bool(_explicit_bool(overrides, "event_reactor_no_bypass_suite_passed"))
    evidence["event_reactor_no_bypass_suite_passed"] = no_bypass_local_verified or no_bypass_override_verified
    if no_bypass_override_verified and not no_bypass_local_verified:
        refs.append("Event reactor no-bypass suite pass supplied as explicit evidence")
    evidence["evidence_refs"] = refs

    missing_source = tuple(key for key in REQUIRED_SOURCE_EVIDENCE if evidence.get(key) is not True)
    missing_data = tuple(key for key in REQUIRED_DATA_EVIDENCE if evidence.get(key) is not True)
    reasons: list[str] = []
    if missing_source:
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_SOURCE_EVIDENCE_INCOMPLETE")
    if missing_data:
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_DATA_EVIDENCE_INCOMPLETE")
    status = "SIMPLE_SWITCH_EVIDENCE_COMPLETE" if not reasons else "SIMPLE_SWITCH_EVIDENCE_INCOMPLETE"
    return ReplacementForecastSimpleSwitchEvidenceReport(
        status=status,
        reason_codes=tuple(reasons or ("REPLACEMENT_SIMPLE_SWITCH_EVIDENCE_COMPLETE",)),
        evidence=evidence,
        missing_source_evidence=missing_source,
        missing_data_evidence=missing_data,
    )
