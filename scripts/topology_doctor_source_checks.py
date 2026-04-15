"""Source rationale and scoped-AGENTS checker family for topology_doctor."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def run_source(api: Any) -> Any:
    rationale = api.load_source_rationale()
    tracked_src = sorted(path for path in api._git_ls_files() if path.startswith("src/"))
    declared = set((rationale.get("files") or {}).keys())
    issues: list[Any] = []

    for path in tracked_src:
        if path not in declared:
            issues.append(api._issue("source_rationale_missing", path, "tracked src file has no rationale entry"))

    for path in sorted(declared):
        if path.startswith("src/") and path not in tracked_src:
            issues.append(api._issue("source_rationale_stale", path, "rationale entry has no tracked src file"))

    state_files = [
        path
        for path in tracked_src
        if path.startswith("src/state/")
        and path not in {"src/state/AGENTS.md", "src/state/__init__.py"}
    ]
    files = rationale.get("files") or {}
    hazards = set((rationale.get("hazard_badges") or {}).keys())
    write_routes = set((rationale.get("write_routes") or {}).keys())
    for path in state_files:
        zone = (files.get(path) or {}).get("zone")
        if zone not in {"K0_frozen_kernel", "K2_runtime"}:
            issues.append(
                api._issue(
                    "source_state_role_unsplit",
                    path,
                    f"src/state file must be split K0/K2, got {zone!r}",
                )
            )

    for route in (
        "canonical_position_write",
        "control_write",
        "settlement_write",
        "backtest_diagnostic_write",
        "calibration_persistence_write",
        "calibration_decision_group_write",
        "decision_artifact_write",
        "script_repair_write",
    ):
        if route not in (rationale.get("write_routes") or {}):
            issues.append(api._issue("source_write_route_missing", route, "required write route card missing"))

    for path, entry in files.items():
        for field in ("zone", "authority_role", "why"):
            if not entry.get(field):
                issues.append(api._issue("source_required_field_missing", path, f"missing {field}"))
        for hazard in entry.get("hazards", []):
            if hazard not in hazards:
                issues.append(api._issue("source_unknown_hazard", path, f"unknown hazard badge {hazard}"))
        for route in entry.get("write_routes", []):
            if route not in write_routes:
                issues.append(api._issue("source_unknown_write_route", path, f"unknown write route {route}"))

    required_file_routes = {
        "src/calibration/store.py": "calibration_persistence_write",
        "src/calibration/effective_sample_size.py": "calibration_decision_group_write",
        "src/state/decision_chain.py": "decision_artifact_write",
        "src/state/ledger.py": "canonical_position_write",
        "src/state/projection.py": "canonical_position_write",
        "src/execution/harvester.py": "settlement_write",
        "src/engine/replay.py": "backtest_diagnostic_write",
        "src/control/control_plane.py": "control_write",
    }
    for path, route in required_file_routes.items():
        if route not in (files.get(path) or {}).get("write_routes", []):
            issues.append(api._issue("source_file_write_route_missing", path, f"missing required write route {route}"))

    required_file_roles = {
        "src/state/strategy_tracker.py": ("K2_runtime", "derived_strategy_tracker"),
        "src/observability/status_summary.py": ("K2_runtime", "derived_status_read_model"),
    }
    for path, (zone, role) in required_file_roles.items():
        entry = files.get(path) or {}
        if entry.get("zone") != zone or entry.get("authority_role") != role:
            issues.append(
                api._issue(
                    "source_file_role_mismatch",
                    path,
                    f"expected zone={zone} authority_role={role}",
                )
            )

    return api.StrictResult(ok=not issues, issues=issues)


def expected_zone_for_agents_path(rationale: dict[str, Any], agents_rel: str) -> str | None:
    directory = agents_rel.removesuffix("/AGENTS.md")
    defaults = rationale.get("package_defaults") or {}
    if directory in defaults:
        return (defaults.get(directory) or {}).get("zone")
    files = rationale.get("files") or {}
    if agents_rel in files:
        return (files.get(agents_rel) or {}).get("zone")
    return None


def declared_zone_in_agents(api: Any, path: Path) -> str | None:
    text = path.read_text(encoding="utf-8", errors="ignore")
    match = api.ZONE_DECLARATION_PATTERN.search(text)
    if not match:
        return None
    raw = match.group(1)
    aliases = {
        "K0": "K0_frozen_kernel",
        "K1": "K1_governance",
        "K2": "K2_runtime",
        "K3": "K3_extension",
        "K4": "K4_experimental",
        "Cross": "K0_frozen_kernel",
        "Cross-cutting": "K0_frozen_kernel",
    }
    return aliases.get(raw, raw)


def run_agents_coherence(api: Any) -> Any:
    rationale = api.load_source_rationale()
    issues: list[Any] = []
    for path in sorted((api.ROOT / "src").glob("*/AGENTS.md")):
        rel = path.relative_to(api.ROOT).as_posix()
        declared = declared_zone_in_agents(api, path)
        expected = expected_zone_for_agents_path(rationale, rel)
        if declared and expected and declared != expected:
            issues.append(
                api._issue(
                    "agents_zone_mismatch",
                    rel,
                    f"declares {declared}, but source_rationale/package default declares {expected}",
                )
            )
        if expected in {"K0_frozen_kernel", "K1_governance", "K2_runtime"}:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
            if "no planning lock required" in text:
                issues.append(
                    api._issue(
                        "agents_planning_lock_downgrade",
                        rel,
                        f"{expected} scoped AGENTS lowers planning-lock expectations",
                    )
                )
    return api.StrictResult(ok=not issues, issues=issues)
