"""Read-only bundle planner for replacement forecast simple-switch activation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.data.replacement_forecast_config_switch import (
    ReplacementForecastConfigSwitchPlan,
    build_replacement_forecast_config_switch_plan,
)
from src.data.replacement_forecast_current_fact_patch import (
    ReplacementForecastCurrentFactPatchPlan,
    build_replacement_forecast_current_fact_patch_plan,
)
from src.data.replacement_forecast_live_dry_run import (
    OPTIONAL_DEPENDENCIES,
    ReplacementForecastLiveDryRunInput,
    ReplacementForecastLiveDryRunReport,
    build_replacement_forecast_live_dry_run_report,
)
from src.data.replacement_forecast_live_switch_surface import (
    CURRENT_DATA_FACT_FILE,
    CURRENT_SOURCE_FACT_FILE,
    REFIT_HANDOFF_FILE,
    REQUIRED_FORECAST_TABLES,
)
from src.data.replacement_forecast_runtime_policy import REQUIRED_FLAGS
from src.state.db import list_sqlite_tables_and_views_read_only


REPLACEMENT_SHADOW_TABLES = (
    "raw_forecast_artifacts",
    "deterministic_forecast_anchors",
    "forecast_posteriors",
    "replacement_shadow_decisions",
)


@dataclass(frozen=True)
class ReplacementForecastSimpleSwitchBundle:
    status: str
    reason_codes: tuple[str, ...]
    root: str
    config_switch: ReplacementForecastConfigSwitchPlan
    missing_replacement_shadow_tables: tuple[str, ...]
    missing_live_switch_forecast_tables: tuple[str, ...]
    source_fact_status: str
    data_fact_status: str
    current_fact_patch: ReplacementForecastCurrentFactPatchPlan
    dry_run: ReplacementForecastLiveDryRunReport
    next_commands: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status == "SIMPLE_SWITCH_BUNDLE_READY"

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "root": self.root,
            "config_switch": self.config_switch.as_dict(),
            "missing_replacement_shadow_tables": list(self.missing_replacement_shadow_tables),
            "missing_live_switch_forecast_tables": list(self.missing_live_switch_forecast_tables),
            "source_fact_status": self.source_fact_status,
            "data_fact_status": self.data_fact_status,
            "current_fact_patch": self.current_fact_patch.as_dict(),
            "dry_run": self.dry_run.as_dict(),
            "next_commands": list(self.next_commands),
        }


def _settings_payload(root: Path) -> Mapping[str, object]:
    path = root / "config" / "settings.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path} must decode to a JSON object")
    return payload


def normalize_replacement_forecast_evidence_payload(payload: Mapping[str, object] | None) -> Mapping[str, object] | None:
    """Accept either a flat evidence object or a full simple-switch evidence report."""

    if payload is None:
        return None
    nested = payload.get("evidence")
    if isinstance(nested, Mapping):
        return nested
    return payload


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


def _feature_flags_for_dry_run(settings_payload: Mapping[str, object]) -> dict[str, object]:
    flags_raw = settings_payload.get("feature_flags", {})
    if not isinstance(flags_raw, Mapping):
        flags_raw = {}
    return {key: flags_raw.get(key, False) for key in REQUIRED_FLAGS}


def _forecast_tables(root: Path) -> tuple[str, ...]:
    return tuple(sorted(list_sqlite_tables_and_views_read_only(root / "state" / "zeus-forecasts.db")))


def build_replacement_forecast_simple_switch_bundle(
    root: Path | str,
    *,
    optional_dependencies: tuple[str, ...] = OPTIONAL_DEPENDENCIES,
    current_fact_evidence: Mapping[str, object] | None = None,
    current_fact_evidence_path: Path | str | None = None,
    refit_handoff_json_path: Path | str | None = None,
) -> ReplacementForecastSimpleSwitchBundle:
    """Plan all reversible simple-switch prerequisites from current live-root state."""

    root_path = Path(root)
    settings_payload = _settings_payload(root_path)
    config_switch = build_replacement_forecast_config_switch_plan(settings_payload)
    forecast_tables = set(_forecast_tables(root_path))
    missing_shadow_tables = tuple(table for table in REPLACEMENT_SHADOW_TABLES if table not in forecast_tables)
    missing_live_switch_tables = tuple(table for table in REQUIRED_FORECAST_TABLES if table not in forecast_tables)
    source_status = _status_line(root_path, CURRENT_SOURCE_FACT_FILE)
    data_status = _status_line(root_path, CURRENT_DATA_FACT_FILE)
    fact_patch = build_replacement_forecast_current_fact_patch_plan(
        root_path,
        evidence=normalize_replacement_forecast_evidence_payload(current_fact_evidence),
    )
    evidence_arg = str(current_fact_evidence_path or "EVIDENCE_JSON")
    refit_handoff_arg = str(refit_handoff_json_path or "REFIT_HANDOFF_JSON")
    dry_run = build_replacement_forecast_live_dry_run_report(
        ReplacementForecastLiveDryRunInput(
            root=root_path,
            runtime_flags=_feature_flags_for_dry_run(settings_payload),
            optional_dependencies=optional_dependencies,
            assume_raw_artifact_lineage_available=True,
        )
    )

    reasons: list[str] = []
    commands: list[str] = []
    if not config_switch.ok:
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_CONFIG_PLAN_BLOCKED")
    elif config_switch.json_patch:
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_CONFIG_PATCH_REQUIRED")
        commands.append(
            f"python3 scripts/plan_replacement_forecast_shadow_veto_config.py --settings-json {root_path / 'config' / 'settings.json'} --apply --stdout"
        )
    if missing_shadow_tables:
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_SCHEMA_INIT_REQUIRED")
        commands.append(
            f"python3 scripts/init_replacement_forecast_shadow_schema.py --forecast-db {root_path / 'state' / 'zeus-forecasts.db'} --commit --stdout"
        )
    if source_status != "CURRENT_FOR_LIVE":
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_SOURCE_FACT_UPDATE_REQUIRED")
    if data_status != "CURRENT_FOR_LIVE":
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_DATA_FACT_UPDATE_REQUIRED")
    if not (root_path / REFIT_HANDOFF_FILE).exists():
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_REFIT_HANDOFF_REQUIRED")
        commands.append(
            "python3 scripts/plan_replacement_forecast_refit_handoff_install.py "
            f"--live-root {root_path} --refit-handoff-json {refit_handoff_arg} --stdout"
        )
        commands.append(
            "python3 scripts/plan_replacement_forecast_refit_handoff_install.py "
            f"--live-root {root_path} --refit-handoff-json {refit_handoff_arg} --write --stdout"
        )
    if not fact_patch.ready:
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_CURRENT_FACT_EVIDENCE_REQUIRED")
    elif source_status != "CURRENT_FOR_LIVE" or data_status != "CURRENT_FOR_LIVE":
        commands.append(
            "python3 scripts/plan_replacement_forecast_current_fact_patch.py "
            f"--root {root_path} --evidence-json {evidence_arg} --write --stdout"
        )
    if not dry_run.ok:
        reasons.append("REPLACEMENT_SIMPLE_SWITCH_DRY_RUN_NOT_READY")
    commands.append(f"python3 scripts/check_replacement_forecast_live_dry_run.py --root {root_path} --stdout")

    status = "SIMPLE_SWITCH_BUNDLE_READY" if not reasons else "SIMPLE_SWITCH_BUNDLE_BLOCKED"
    return ReplacementForecastSimpleSwitchBundle(
        status=status,
        reason_codes=tuple(dict.fromkeys(reasons or ["REPLACEMENT_SIMPLE_SWITCH_BUNDLE_READY"])),
        root=str(root_path),
        config_switch=config_switch,
        missing_replacement_shadow_tables=missing_shadow_tables,
        missing_live_switch_forecast_tables=missing_live_switch_tables,
        source_fact_status=source_status,
        data_fact_status=data_status,
        current_fact_patch=fact_patch,
        dry_run=dry_run,
        next_commands=tuple(commands),
    )
