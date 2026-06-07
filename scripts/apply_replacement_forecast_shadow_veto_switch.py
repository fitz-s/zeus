#!/usr/bin/env python3
"""Apply the replacement forecast shadow/veto switch with a rollback receipt."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.init_replacement_forecast_shadow_schema import initialize_replacement_forecast_shadow_schema  # noqa: E402
from src.data.replacement_forecast_config_switch import TARGET_SHADOW_MATERIALIZATION_CONFIG, apply_replacement_forecast_config_switch, read_replacement_forecast_config_switch_plan  # noqa: E402
from src.data.replacement_forecast_current_fact_patch import read_replacement_forecast_current_fact_patch_plan  # noqa: E402
from src.data.replacement_forecast_live_dry_run import ReplacementForecastLiveDryRunInput, build_replacement_forecast_live_dry_run_report  # noqa: E402
from src.data.replacement_forecast_live_switch_surface import CURRENT_DATA_FACT_FILE, CURRENT_SOURCE_FACT_FILE, REFIT_HANDOFF_FILE  # noqa: E402
from src.data.replacement_forecast_refit_handoff_install import plan_replacement_forecast_refit_handoff_install  # noqa: E402
from src.data.replacement_forecast_runtime_policy import DIRECTION_FLIP_FLAG, KELLY_INCREASE_FLAG, REQUIRED_FLAGS, TRADE_AUTHORITY_FLAG  # noqa: E402


BACKUP_RELATIVE_FILES = (
    "config/settings.json",
    CURRENT_SOURCE_FACT_FILE,
    CURRENT_DATA_FACT_FILE,
    "state/zeus-forecasts.db",
    REFIT_HANDOFF_FILE,
)


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"object of type {value.__class__.__name__} is not JSON serializable")


def _load_feature_flags(root: Path) -> Mapping[str, Any]:
    settings_path = root / "config" / "settings.json"
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("settings JSON must decode to an object")
    flags = payload.get("feature_flags")
    if not isinstance(flags, Mapping):
        raise ValueError("settings JSON must contain feature_flags object")
    return flags


def _write_current_fact_patch_files(plan: Any) -> None:
    if not plan.ready or plan.source_patch is None or plan.data_patch is None:
        raise RuntimeError("current-fact patch is not ready")
    source_path = Path(plan.source_fact_path)
    data_path = Path(plan.data_fact_path)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(plan.source_patch, encoding="utf-8")
    data_path.write_text(plan.data_patch, encoding="utf-8")


def _backup_live_files(root: Path, backup_dir: Path) -> dict[str, str]:
    copied: dict[str, str] = {}
    backup_dir.mkdir(parents=True, exist_ok=False)
    for relative in BACKUP_RELATIVE_FILES:
        source = root / relative
        if not source.exists():
            continue
        target = backup_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied[relative] = str(target)
    return copied


def _rollback_commands(root: Path, backup_dir: Path, copied: Mapping[str, str]) -> list[str]:
    commands: list[str] = []
    for relative in BACKUP_RELATIVE_FILES:
        backup_file = backup_dir / relative
        target = root / relative
        if relative in copied:
            commands.append(f"mkdir -p {target.parent} && cp {backup_file} {target}")
        elif relative == REFIT_HANDOFF_FILE:
            commands.append(f"rm -f {target}")
    return commands


def _ensure_safe_flags(flags: Mapping[str, object]) -> None:
    missing = [key for key in REQUIRED_FLAGS if key not in flags]
    if missing:
        raise RuntimeError(f"replacement runtime flags missing after apply: {','.join(missing)}")
    dangerous = {
        TRADE_AUTHORITY_FLAG: flags.get(TRADE_AUTHORITY_FLAG),
        KELLY_INCREASE_FLAG: flags.get(KELLY_INCREASE_FLAG),
        DIRECTION_FLIP_FLAG: flags.get(DIRECTION_FLIP_FLAG),
    }
    if any(value is not False for value in dangerous.values()):
        raise RuntimeError(f"replacement live authority flags are not all false: {dangerous}")


def _ensure_shadow_materialization_dirs(root: Path) -> dict[str, str]:
    created_or_existing: dict[str, str] = {}
    for key, value in TARGET_SHADOW_MATERIALIZATION_CONFIG.items():
        if not key.endswith("_dir"):
            continue
        path = root / str(value)
        path.mkdir(parents=True, exist_ok=True)
        created_or_existing[key] = str(path)
    return created_or_existing


def apply_replacement_forecast_shadow_veto_switch(
    *,
    live_root: Path,
    evidence_json: Path,
    refit_handoff_json: Path,
    backup_dir: Path | None = None,
    apply: bool = False,
    optional_dependencies: tuple[str, ...] = ("requests",),
) -> dict[str, object]:
    """Plan or apply the chosen replacement strategy as shadow/veto only."""

    root = Path(live_root)
    generated_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    effective_backup_dir = Path(backup_dir) if backup_dir is not None else root / "state" / "replacement_forecast_shadow" / "switch_backups" / generated_at
    settings_path = root / "config" / "settings.json"
    forecast_db = root / "state" / "zeus-forecasts.db"

    config_plan = read_replacement_forecast_config_switch_plan(settings_path)
    current_fact_plan = read_replacement_forecast_current_fact_patch_plan(root, evidence_json=evidence_json)
    refit_plan = plan_replacement_forecast_refit_handoff_install(
        live_root=root,
        refit_handoff_json=refit_handoff_json,
        write=False,
    )
    reasons: list[str] = []
    if not config_plan.ok:
        reasons.append("REPLACEMENT_SWITCH_CONFIG_PLAN_BLOCKED")
    if not current_fact_plan.ready:
        reasons.append("REPLACEMENT_SWITCH_CURRENT_FACT_PLAN_BLOCKED")
    if not refit_plan.ready:
        reasons.append("REPLACEMENT_SWITCH_REFIT_HANDOFF_PLAN_BLOCKED")
    if config_plan.policy_status_after != "SHADOW_VETO_ONLY":
        reasons.append("REPLACEMENT_SWITCH_TARGET_POLICY_NOT_SHADOW_VETO_ONLY")
    if any(config_plan.target_flags.get(key) is not False for key in (TRADE_AUTHORITY_FLAG, KELLY_INCREASE_FLAG, DIRECTION_FLIP_FLAG)):
        reasons.append("REPLACEMENT_SWITCH_DANGEROUS_TARGET_FLAG")

    flags_for_dry_run: Mapping[str, object] = config_plan.target_flags if config_plan.ok else {}
    dry_run = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(
            root=root,
            runtime_flags=flags_for_dry_run,
            optional_dependencies=optional_dependencies,
            source_fact_status_override="CURRENT_FOR_LIVE",
            data_fact_status_override="CURRENT_FOR_LIVE",
            assume_replacement_shadow_schema_initialized=not apply,
            assume_refit_handoff_available=not apply,
            assume_raw_artifact_lineage_available=True,
        )
    )
    if apply and not dry_run.ok:
        reasons.append("REPLACEMENT_SWITCH_PRE_APPLY_DRY_RUN_BLOCKED")

    backups: dict[str, str] = {}
    applied_steps: list[str] = []
    schema_report: dict[str, object] | None = None
    materialization_dirs: dict[str, str] = {}
    current_fact_written = False
    config_apply_report: Mapping[str, object] | None = None
    refit_apply_report: Mapping[str, object] | None = None
    if apply and not reasons:
        backups = _backup_live_files(root, effective_backup_dir)
        config_apply_plan = apply_replacement_forecast_config_switch(settings_path)
        config_apply_report = config_apply_plan.as_dict()
        applied_steps.append("config_shadow_veto_flags")
        materialization_dirs = _ensure_shadow_materialization_dirs(root)
        applied_steps.append("shadow_materialization_dirs")
        schema_report = initialize_replacement_forecast_shadow_schema(forecast_db, commit=True)
        applied_steps.append("replacement_shadow_schema")
        refit_apply = plan_replacement_forecast_refit_handoff_install(
            live_root=root,
            refit_handoff_json=refit_handoff_json,
            write=True,
        )
        refit_apply_report = refit_apply.as_dict()
        applied_steps.append("refit_handoff")
        _write_current_fact_patch_files(current_fact_plan)
        current_fact_written = True
        applied_steps.append("current_fact_patch")
        _ensure_safe_flags(_load_feature_flags(root))
        dry_run = build_replacement_forecast_live_dry_run_report(
            ReplacementForecastLiveDryRunInput(
                root=root,
                runtime_flags=_load_feature_flags(root),
                optional_dependencies=optional_dependencies,
                assume_raw_artifact_lineage_available=True,
            )
        )
        if not dry_run.ok:
            reasons.append("REPLACEMENT_SWITCH_POST_APPLY_DRY_RUN_BLOCKED")
    status = "SHADOW_VETO_SWITCH_APPLIED" if apply and not reasons else "SHADOW_VETO_SWITCH_READY" if not apply and not reasons else "SHADOW_VETO_SWITCH_BLOCKED"
    live_root_written = bool(apply and applied_steps)
    return {
        "schema_version": "replacement_forecast_shadow_veto_switch_receipt_v1",
        "status": status,
        "reason_codes": list(dict.fromkeys(reasons or ["REPLACEMENT_SHADOW_VETO_SWITCH_READY"])),
        "live_root": str(root),
        "live_root_written": live_root_written,
        "apply_requested": bool(apply),
        "backup_dir": str(effective_backup_dir) if backups or apply else None,
        "backups": backups,
        "rollback_commands": _rollback_commands(root, effective_backup_dir, backups) if backups or applied_steps else [],
        "applied_steps": applied_steps,
        "strategy": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor",
        "fixed_config": "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00",
        "config_switch": config_apply_report or config_plan.as_dict(),
        "shadow_materialization_dirs": materialization_dirs,
        "schema_commit": schema_report,
        "refit_handoff_install": refit_apply_report or refit_plan.as_dict(),
        "current_fact_patch": {**current_fact_plan.as_dict(), "written": current_fact_written},
        "runtime_policy_status": dry_run.runtime_policy_status,
        "live_trade_authority": dry_run.live_switch_report.live_trade_authority,
        "live_switch_ready": dry_run.live_switch_report.simple_switch_ready,
        "dry_run": dry_run.as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply replacement forecast shadow/veto simple switch")
    parser.add_argument("--live-root", type=Path, default=ROOT)
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--refit-handoff-json", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Write the target live root; default only produces a readiness receipt")
    parser.add_argument("--receipt-json", type=Path, default=None)
    parser.add_argument("--optional-dependency", action="append", default=["requests"])
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = apply_replacement_forecast_shadow_veto_switch(
            live_root=args.live_root,
            evidence_json=args.evidence_json,
            refit_handoff_json=args.refit_handoff_json,
            backup_dir=args.backup_dir,
            apply=bool(args.apply),
            optional_dependencies=tuple(args.optional_dependency or ()),
        )
        if args.receipt_json is not None:
            args.receipt_json.parent.mkdir(parents=True, exist_ok=True)
            args.receipt_json.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.stdout:
        print(json.dumps(receipt, sort_keys=True, default=_json_default))
    else:
        print(f"{receipt['status']}: {','.join(receipt['reason_codes'])}")
    return 0 if receipt["status"] in {"SHADOW_VETO_SWITCH_READY", "SHADOW_VETO_SWITCH_APPLIED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
