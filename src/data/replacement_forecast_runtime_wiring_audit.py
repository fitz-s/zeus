"""Read-only runtime wiring audit for the replacement forecast switch."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.data.replacement_forecast_live_dry_run import (
    ReplacementForecastLiveDryRunInput,
    build_replacement_forecast_live_dry_run_report,
    _configured_promotion_evidence,
)
from src.data.replacement_forecast_live_switch_surface import REFIT_HANDOFF_FILE
from src.data.replacement_forecast_config_switch import TARGET_SHADOW_MATERIALIZATION_CONFIG
from src.data.replacement_forecast_refit_handoff import refit_decision_from_handoff_payload
from src.data.replacement_forecast_runtime_policy import (
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    LIVE_AUTHORITY_STATUS,
    REQUIRED_FLAGS,
    SHADOW_VETO_ONLY_STATUS,
    TRADE_AUTHORITY_FLAG,
    resolve_replacement_forecast_runtime_policy,
)


REQUIRED_MAIN_ANCHORS = (
    "_replacement_forecast_runtime_flags_from_settings",
    "_replacement_forecast_refit_decision_from_settings",
    "replacement_forecast_baseline_bundle_provider_from_forecast_conn",
    "replacement_forecast_runtime_flags=replacement_forecast_runtime_flags",
    "replacement_forecast_refit_decision=replacement_forecast_refit_decision",
    "replacement_forecast_source_fact_status=replacement_forecast_source_fact_status",
    "replacement_forecast_data_fact_status=replacement_forecast_data_fact_status",
    "_replacement_forecast_shadow_materialize_cycle",
)
REQUIRED_ADAPTER_ANCHORS = (
    "_resolve_replacement_forecast_adapter_hook",
    "build_replacement_forecast_event_hook",
    "replacement_forecast_hook(proof, event, decision_time)",
    "REPLACEMENT_FORECAST_HOOK_BLOCKED",
    "REPLACEMENT_FORECAST_HOOK_DIRECTION_FLIP",
    "effective_q_lcb = min(proof.q_lcb_5pct, replacement_hook_result.effective_q_lcb)",
    'raw_receipt["replacement_forecast"] = replacement_forecast_receipt_tag',
)
REQUIRED_HOOK_FACTORY_ANCHORS = (
    "build_replacement_forecast_event_hook",
    "_latest_replacement_readiness",
    "read_replacement_forecast_bundle",
    "_write_replacement_shadow_decision",
    "replacement_shadow_decisions",
)


@dataclass(frozen=True)
class ReplacementForecastRuntimeWiringAudit:
    status: str
    reason_codes: tuple[str, ...]
    live_root: str
    runtime_policy_status: str
    dry_run_status: str
    refit_handoff_status: str
    raw_artifact_lineage_status: str
    raw_artifact_lineage_counts: Mapping[str, int]
    latest_readiness_artifact_status: str
    latest_readiness_artifact_counts: Mapping[str, int]
    shadow_materialization_config_status: str
    missing_shadow_materialization_config: tuple[str, ...]
    shadow_materialization_paths_status: str
    shadow_materialization_paths: Mapping[str, str]
    main_anchor_status: Mapping[str, bool]
    adapter_anchor_status: Mapping[str, bool]
    hook_factory_anchor_status: Mapping[str, bool]
    live_authority_flags_false: bool
    receipt_status: str | None
    receipt_live_root_written: bool | None
    read_files: tuple[str, ...]
    write_surfaces: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status == "RUNTIME_WIRING_READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "live_root": self.live_root,
            "runtime_policy_status": self.runtime_policy_status,
            "dry_run_status": self.dry_run_status,
            "refit_handoff_status": self.refit_handoff_status,
            "raw_artifact_lineage_status": self.raw_artifact_lineage_status,
            "raw_artifact_lineage_counts": dict(self.raw_artifact_lineage_counts),
            "latest_readiness_artifact_status": self.latest_readiness_artifact_status,
            "latest_readiness_artifact_counts": dict(self.latest_readiness_artifact_counts),
            "shadow_materialization_config_status": self.shadow_materialization_config_status,
            "missing_shadow_materialization_config": list(self.missing_shadow_materialization_config),
            "shadow_materialization_paths_status": self.shadow_materialization_paths_status,
            "shadow_materialization_paths": dict(self.shadow_materialization_paths),
            "main_anchor_status": dict(self.main_anchor_status),
            "adapter_anchor_status": dict(self.adapter_anchor_status),
            "hook_factory_anchor_status": dict(self.hook_factory_anchor_status),
            "live_authority_flags_false": self.live_authority_flags_false,
            "receipt_status": self.receipt_status,
            "receipt_live_root_written": self.receipt_live_root_written,
            "read_files": list(self.read_files),
            "write_surfaces": list(self.write_surfaces),
        }


def _settings_payload(root: Path) -> Mapping[str, object]:
    payload = json.loads((root / "config" / "settings.json").read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("settings JSON must decode to an object")
    return payload


def _feature_flags(settings_payload: Mapping[str, object], *, assume_shadow_veto: bool) -> dict[str, object]:
    raw = settings_payload.get("feature_flags", {})
    flags = dict(raw) if isinstance(raw, Mapping) else {}
    if assume_shadow_veto:
        from src.data.replacement_forecast_config_switch import TARGET_SHADOW_VETO_FLAGS

        flags.update(TARGET_SHADOW_VETO_FLAGS)
    return {key: flags.get(key, False) for key in REQUIRED_FLAGS}


def _refit_handoff_path(root: Path, settings_payload: Mapping[str, object]) -> Path:
    cfg = settings_payload.get("replacement_forecast_shadow", {})
    raw = REFIT_HANDOFF_FILE
    if isinstance(cfg, Mapping):
        raw = str(cfg.get("refit_handoff_path") or raw)
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _missing_shadow_materialization_config(settings_payload: Mapping[str, object], *, assume_shadow_veto: bool) -> tuple[str, ...]:
    raw = settings_payload.get("replacement_forecast_shadow", {})
    shadow_config = dict(raw) if isinstance(raw, Mapping) else {}
    if assume_shadow_veto:
        shadow_config.update(TARGET_SHADOW_MATERIALIZATION_CONFIG)
    return tuple(
        key
        for key, value in TARGET_SHADOW_MATERIALIZATION_CONFIG.items()
        if shadow_config.get(key) != value
    )


def _shadow_materialization_paths(
    root: Path,
    settings_payload: Mapping[str, object],
    *,
    assume_shadow_veto: bool,
) -> dict[str, str]:
    raw = settings_payload.get("replacement_forecast_shadow", {})
    shadow_config = dict(raw) if isinstance(raw, Mapping) else {}
    if assume_shadow_veto:
        shadow_config.update(TARGET_SHADOW_MATERIALIZATION_CONFIG)

    def _resolve(key: str) -> str:
        configured = shadow_config.get(key)
        if configured in (None, ""):
            configured = TARGET_SHADOW_MATERIALIZATION_CONFIG.get(key)
        if configured in (None, ""):
            return ""
        path = Path(str(configured))
        return str(path if path.is_absolute() else root / path)

    return {
        key: _resolve(key)
        for key in (
            "raw_manifest_dir",
            "request_dir",
            "processed_dir",
            "failed_dir",
            "seed_dir",
            "seed_processed_dir",
            "seed_failed_dir",
            "forecast_db",
        )
    }


def _shadow_materialization_paths_status(root: Path, paths: Mapping[str, str]) -> str:
    try:
        root_resolved = root.resolve()
    except OSError:
        root_resolved = root.absolute()
    for raw_path in paths.values():
        if not raw_path:
            return "MISSING"
        path = Path(raw_path)
        if not path.is_absolute():
            return "RELATIVE"
        try:
            path_resolved = path.resolve(strict=False)
        except OSError:
            path_resolved = path.absolute()
        if root_resolved not in (path_resolved, *path_resolved.parents):
            return "OUTSIDE_LIVE_ROOT"
    return "READY"


def _refit_status(root: Path, settings_payload: Mapping[str, object], *, assume_refit_handoff: bool) -> str:
    if assume_refit_handoff:
        return "ASSUMED_READY"
    path = _refit_handoff_path(root, settings_payload)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return "INVALID:NOT_OBJECT"
        refit_decision_from_handoff_payload(payload)
    except FileNotFoundError:
        return "MISSING"
    except Exception as exc:  # noqa: BLE001 - audit payload
        return f"INVALID:{exc.__class__.__name__}"
    return "READY"


def _anchor_status(path: Path, anchors: tuple[str, ...]) -> dict[str, bool]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {anchor: False for anchor in anchors}
    return {anchor: anchor in text for anchor in anchors}


def _receipt(path: Path | str | None) -> tuple[str | None, bool | None]:
    if path is None:
        return None, None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("receipt JSON must decode to an object")
    return str(payload.get("status") or ""), bool(payload.get("live_root_written", False))


def build_replacement_forecast_runtime_wiring_audit(
    *,
    live_root: Path | str,
    repo_root: Path | str,
    apply_receipt_json: Path | str | None = None,
    assume_shadow_veto: bool = False,
    assume_current_facts: bool = False,
    assume_shadow_schema: bool = False,
    assume_refit_handoff: bool = False,
    optional_dependencies: tuple[str, ...] = ("requests",),
) -> ReplacementForecastRuntimeWiringAudit:
    """Audit the switch-to-reactor path without mutating runtime state."""

    root = Path(live_root)
    repo = Path(repo_root)
    settings_payload = _settings_payload(root)
    flags = _feature_flags(settings_payload, assume_shadow_veto=assume_shadow_veto)
    promotion_evidence, capital_objective_evidence, _promotion_evidence_status = _configured_promotion_evidence(root)
    policy = resolve_replacement_forecast_runtime_policy(
        flags,
        promotion_evidence=promotion_evidence,
        capital_objective_evidence=capital_objective_evidence,
    )
    dry_run = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(
            root=root,
            runtime_flags=flags,
            optional_dependencies=optional_dependencies,
            source_fact_status_override="CURRENT_FOR_LIVE" if assume_current_facts else None,
            data_fact_status_override="CURRENT_FOR_LIVE" if assume_current_facts else None,
            assume_replacement_shadow_schema_initialized=assume_shadow_schema,
            assume_refit_handoff_available=assume_refit_handoff,
        )
    )
    main_anchors = _anchor_status(repo / "src" / "main.py", REQUIRED_MAIN_ANCHORS)
    adapter_anchors = _anchor_status(repo / "src" / "engine" / "event_reactor_adapter.py", REQUIRED_ADAPTER_ANCHORS)
    hook_factory_anchors = _anchor_status(repo / "src" / "engine" / "replacement_forecast_hook_factory.py", REQUIRED_HOOK_FACTORY_ANCHORS)
    receipt_status, receipt_live_root_written = _receipt(apply_receipt_json)
    authority_flags_false = all(
        flags.get(key) is False
        for key in (TRADE_AUTHORITY_FLAG, KELLY_INCREASE_FLAG, DIRECTION_FLIP_FLAG)
    )
    dangerous_authority_flags_false = all(
        flags.get(key) is False
        for key in (KELLY_INCREASE_FLAG, DIRECTION_FLIP_FLAG)
    )
    refit_status = _refit_status(root, settings_payload, assume_refit_handoff=assume_refit_handoff)
    missing_shadow_config = _missing_shadow_materialization_config(settings_payload, assume_shadow_veto=assume_shadow_veto)
    shadow_config_status = "READY" if not missing_shadow_config else "MISSING"
    materialization_paths = _shadow_materialization_paths(
        root,
        settings_payload,
        assume_shadow_veto=assume_shadow_veto,
    )
    materialization_paths_status = _shadow_materialization_paths_status(root, materialization_paths)
    reasons: list[str] = []
    if policy.status not in {SHADOW_VETO_ONLY_STATUS, LIVE_AUTHORITY_STATUS}:
        reasons.append("REPLACEMENT_RUNTIME_WIRING_POLICY_NOT_SWITCHABLE")
    if not dangerous_authority_flags_false:
        reasons.append("REPLACEMENT_RUNTIME_WIRING_DANGEROUS_AUTHORITY_FLAGS_NOT_FALSE")
    if not dry_run.ok:
        reasons.append("REPLACEMENT_RUNTIME_WIRING_DRY_RUN_NOT_READY")
    if refit_status not in {"READY", "ASSUMED_READY"}:
        reasons.append("REPLACEMENT_RUNTIME_WIRING_REFIT_HANDOFF_NOT_READY")
    if missing_shadow_config:
        reasons.append("REPLACEMENT_RUNTIME_WIRING_SHADOW_MATERIALIZATION_CONFIG_MISSING")
    if materialization_paths_status != "READY":
        reasons.append("REPLACEMENT_RUNTIME_WIRING_SHADOW_MATERIALIZATION_PATHS_NOT_READY")
    if not all(main_anchors.values()):
        reasons.append("REPLACEMENT_RUNTIME_WIRING_MAIN_ANCHOR_MISSING")
    if not all(adapter_anchors.values()):
        reasons.append("REPLACEMENT_RUNTIME_WIRING_ADAPTER_ANCHOR_MISSING")
    if not all(hook_factory_anchors.values()):
        reasons.append("REPLACEMENT_RUNTIME_WIRING_HOOK_FACTORY_ANCHOR_MISSING")
    if receipt_status is not None and receipt_status not in {
        "SHADOW_VETO_SWITCH_READY",
        "SHADOW_VETO_SWITCH_APPLIED",
        "LIVE_AUTHORITY_SWITCH_READY",
        "LIVE_AUTHORITY_SWITCH_APPLIED",
    }:
        reasons.append("REPLACEMENT_RUNTIME_WIRING_RECEIPT_NOT_READY")
    status = "RUNTIME_WIRING_READY" if not reasons else "RUNTIME_WIRING_BLOCKED"
    return ReplacementForecastRuntimeWiringAudit(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons or ["REPLACEMENT_RUNTIME_WIRING_READY"])),
        live_root=str(root),
        runtime_policy_status=policy.status,
        dry_run_status=dry_run.status,
        refit_handoff_status=refit_status,
        raw_artifact_lineage_status=dry_run.raw_artifact_lineage_status,
        raw_artifact_lineage_counts=dry_run.raw_artifact_lineage_counts,
        latest_readiness_artifact_status=dry_run.latest_readiness_artifact_status,
        latest_readiness_artifact_counts=dry_run.latest_readiness_artifact_counts,
        shadow_materialization_config_status=shadow_config_status,
        missing_shadow_materialization_config=missing_shadow_config,
        shadow_materialization_paths_status=materialization_paths_status,
        shadow_materialization_paths=materialization_paths,
        main_anchor_status=main_anchors,
        adapter_anchor_status=adapter_anchors,
        hook_factory_anchor_status=hook_factory_anchors,
        live_authority_flags_false=authority_flags_false,
        receipt_status=receipt_status,
        receipt_live_root_written=receipt_live_root_written,
        read_files=tuple(dry_run.live_switch_report.readable_files),
        write_surfaces=(
            "state/zeus-forecasts.db:replacement_shadow_decisions",
            "edli_no_submit_receipts.replacement_forecast",
        ),
    )
