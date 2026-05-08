#!/usr/bin/env python3
# Lifecycle: created=2026-04-30; last_reviewed=2026-04-30; last_reused=never
# Purpose: Cron-safe source-contract transition controller with deterministic date scopes, receipts, and Discord reporting.
# Reuse: Inspect docs/operations/task_2026-04-30_source_auto_conversion/plan.md and architecture/script_manifest.yaml before enabling apply.
"""Plan or execute source-contract transitions from the runtime source monitor.

Default mode writes quarantine/receipt artifacts only. ``--execute-apply
--force`` promotes a same-provider WU station change through deterministic
config, backfill, settlement, calibration, verification, and quarantine-release
steps. Hidden branches remain fail-closed and keep the city source quarantine
active.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import watch_source_contract  # noqa: E402
from src.state.db_writer_lock import WriteClass, subprocess_run_with_write_class  # noqa: E402
from src.config import CONFIG_DIR, load_cities, state_path  # noqa: E402
from src.contracts.season import season_from_date  # noqa: E402
from src.data import market_scanner as ms  # noqa: E402


SCHEMA_VERSION = 1
DEFAULT_HISTORY_DAYS = 1095
DEFAULT_MIN_ALERT_MARKETS = 2
DEFAULT_MIN_TARGET_DATES = 1
DEFAULT_RECEIPT_DIR = state_path("source_contract_auto_convert")
DEFAULT_LOCK_PATH = state_path("source_contract_auto_convert.lock")
DEFAULT_MINI_REPORT_SUFFIX = ".mini_report.md"
DEFAULT_CITY_CONFIG_PATH = CONFIG_DIR / "cities.json"
DEFAULT_SOURCE_VALIDITY_PATH = ROOT / "docs/operations/current_source_validity.md"
DEFAULT_WORLD_DB_PATH = state_path("zeus-world.db")
DEFAULT_SOURCE_QUARANTINE_PATH = ms.source_contract_quarantine_path()
DEFAULT_EVIDENCE_BASE = ROOT / "docs/operations/task_2026-04-30_source_auto_conversion/evidence"
AVIATIONWEATHER_STATIONINFO_URL = "https://aviationweather.gov/api/data/stationinfo"

STATUS_EXIT_CODES = {
    "noop": 0,
    "applied": 0,
    "planned": 1,
    "blocked": 1,
    "failed": 2,
}

STATIC_WU_STATION_METADATA = {
    # AirportGuide publishes LFPB as 48.969398 / 2.44139. Keep this as a
    # deterministic offline seed so the Paris canary does not depend on a live
    # metadata API, while unknown stations still require network proof.
    # Source: https://airportguide.com/airport/info/LBG
    "LFPB": {
        "station_id": "LFPB",
        "airport_name": "Paris-Le Bourget Airport",
        "lat": 48.969398,
        "lon": 2.44139,
        "country_code": "FR",
        "metadata_source": "static_seed:https://airportguide.com/airport/info/LBG",
    },
}

MANUAL_BRANCH_REASONS = {
    "provider_family_change_requires_new_source_role": "provider family changed; a new source-role adapter/config path is required before automation can continue",
    "unsupported_source_requires_manual_provider_adapter_review": "resolution source is unsupported or lacks station proof",
    "ambiguous_source_requires_manual_market_attestation": "multiple source families or stations were observed",
    "source_contract_mismatch": "source mismatch did not satisfy same-provider station-change proof",
    "source_contract_review": "source evidence needs manual review",
    "no_active_quarantine": "no active quarantine entry exists for this city",
}


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True)
class RuntimePolicy:
    history_days: int = DEFAULT_HISTORY_DAYS
    min_alert_markets: int = DEFAULT_MIN_ALERT_MARKETS
    min_target_dates: int = DEFAULT_MIN_TARGET_DATES
    today: date = field(default_factory=_utc_today)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _sorted_unique(values: list[Any] | tuple[Any, ...]) -> list[str]:
    normalized = {
        str(value).strip()
        for value in values
        if value is not None and str(value).strip()
    }
    return sorted(normalized)


def _slug_token(value: Any, *, fallback: str = "unknown") -> str:
    token = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return token or fallback


def _source_change_git_workspace(
    *,
    run_id: str,
    city: str,
    source_contract: dict[str, Any],
    date_scope: dict[str, Any],
) -> dict[str, Any]:
    """Build deterministic git isolation metadata for one source-change batch."""
    run_token = _slug_token(run_id)
    city_token = _slug_token(city, fallback="city")
    from_token = _slug_token(
        "-".join(source_contract.get("from_station_ids") or [])
        or "-".join(source_contract.get("from_source_families") or []),
        fallback="from-unknown",
    )
    to_token = _slug_token(
        "-".join(source_contract.get("to_station_ids") or [])
        or "-".join(source_contract.get("to_source_families") or []),
        fallback="to-unknown",
    )
    first_date = _slug_token(date_scope.get("affected_market_start"), fallback="unknown-date")
    branch_name = f"source-contract/{first_date}-{city_token}-{from_token}-to-{to_token}-{run_token}"
    worktree_name = f"zeus-{branch_name.replace('/', '-')}"
    worktree_path = ROOT.parent / worktree_name
    create_command = [
        "git",
        "-C",
        str(ROOT),
        "worktree",
        "add",
        "-b",
        branch_name,
        str(worktree_path),
        "HEAD",
    ]
    return {
        "required": True,
        "status": "exists" if worktree_path.exists() else "missing",
        "branch_name": branch_name,
        "worktree_path": str(worktree_path),
        "base_ref": "HEAD",
        "create_command": create_command,
        "protocol": [
            "Create or reuse this source-change-specific worktree before any apply step.",
            "Do not run source conversion apply from the stable cron repo once this worktree is assigned.",
            "Commit/review the source-change diff on this branch; future source changes must get a new branch/worktree.",
        ],
    }


def _alert_events_by_city(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in report.get("events", []):
        city = event.get("city")
        contract = event.get("source_contract") or {}
        if not city:
            continue
        if event.get("severity") != "ALERT":
            continue
        if contract.get("status") not in ms.SOURCE_CONTRACT_ALERT_STATUSES:
            continue
        grouped[str(city)].append(event)
    return dict(sorted(grouped.items()))


def _date_scope(events: list[dict[str, Any]], policy: RuntimePolicy) -> dict[str, Any]:
    target_dates = [
        parsed
        for parsed in (_iso_date(event.get("target_date")) for event in events)
        if parsed is not None
    ]
    sorted_dates = sorted(set(target_dates))
    affected_start = sorted_dates[0] if sorted_dates else None
    affected_end = sorted_dates[-1] if sorted_dates else None

    executable_fetch_end = policy.today - timedelta(days=2)
    desired_backfill_end = max(
        [value for value in (affected_end, policy.today) if value is not None],
        default=policy.today,
    )
    baseline_start = executable_fetch_end - timedelta(days=policy.history_days - 1)
    fetch_start = min(
        [value for value in (affected_start, baseline_start) if value is not None],
        default=baseline_start,
    )
    backfill_days = max(1, (executable_fetch_end - fetch_start).days + 1)

    future_or_recent = [
        value.isoformat()
        for value in sorted_dates
        if value > executable_fetch_end
    ]
    missing_target_date_count = sum(1 for event in events if not event.get("target_date"))

    return {
        "affected_target_dates": [value.isoformat() for value in sorted_dates],
        "affected_market_start": affected_start.isoformat() if affected_start else None,
        "affected_market_end": affected_end.isoformat() if affected_end else None,
        "missing_target_date_count": missing_target_date_count,
        "history_window_days": policy.history_days,
        "desired_backfill_end": desired_backfill_end.isoformat(),
        "executable_wu_fetch_end": executable_fetch_end.isoformat(),
        "backfill_start": fetch_start.isoformat(),
        "backfill_days": backfill_days,
        "future_or_recent_dates_not_fetchable_by_wu_history": future_or_recent,
        "date_scope_note": (
            "backfill_wu_daily_all.py ends at runtime today-2; future/current "
            "settlement dates stay pending until WU history is available."
        ),
    }


def _source_contract_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    contracts = [event.get("source_contract") or {} for event in events]
    resolution_sources = _sorted_unique(
        [
            source
            for contract in contracts
            for source in (contract.get("resolution_sources") or [])
        ]
    )
    return {
        "statuses": _sorted_unique([contract.get("status") for contract in contracts]),
        "from_source_families": _sorted_unique(
            [contract.get("configured_source_family") for contract in contracts]
        ),
        "from_station_ids": _sorted_unique(
            [contract.get("configured_station_id") for contract in contracts]
        ),
        "to_source_families": _sorted_unique(
            [contract.get("source_family") for contract in contracts]
        ),
        "to_station_ids": _sorted_unique([contract.get("station_id") for contract in contracts]),
        "resolution_sources": resolution_sources,
    }


def _transition_branch(events: list[dict[str, Any]]) -> str:
    return ms.source_contract_transition_branch({"evidence": {"events": events}})


def _threshold_blockers(
    *,
    branch: str,
    summary: dict[str, Any],
    events: list[dict[str, Any]],
    date_scope: dict[str, Any],
    policy: RuntimePolicy,
) -> list[str]:
    blockers: list[str] = []
    if branch != "same_provider_station_change":
        blockers.append(MANUAL_BRANCH_REASONS.get(branch, f"unsupported transition branch: {branch}"))
        return blockers

    if summary["from_source_families"] != ["wu_icao"]:
        blockers.append(f"configured source family is not exactly wu_icao: {summary['from_source_families']}")
    if summary["to_source_families"] != ["wu_icao"]:
        blockers.append(f"observed source family is not exactly wu_icao: {summary['to_source_families']}")
    if len(summary["from_station_ids"]) != 1:
        blockers.append(f"configured station proof is not singular: {summary['from_station_ids']}")
    if len(summary["to_station_ids"]) != 1:
        blockers.append(f"observed station proof is not singular: {summary['to_station_ids']}")
    if (
        len(summary["from_station_ids"]) == 1
        and len(summary["to_station_ids"]) == 1
        and summary["from_station_ids"][0] == summary["to_station_ids"][0]
    ):
        blockers.append("observed station equals configured station; not a station-change conversion")
    if len(events) < policy.min_alert_markets:
        blockers.append(
            f"alert market count {len(events)} is below threshold {policy.min_alert_markets}"
        )
    if len(date_scope["affected_target_dates"]) < policy.min_target_dates:
        blockers.append(
            "distinct affected target-date count "
            f"{len(date_scope['affected_target_dates'])} is below threshold {policy.min_target_dates}"
        )
    if date_scope["missing_target_date_count"]:
        blockers.append(
            f"{date_scope['missing_target_date_count']} alert event(s) are missing target_date"
        )
    return blockers


def _runtime_gaps(
    metrics: list[str],
    date_scope: dict[str, Any],
    *,
    auto_confirmed: bool,
) -> list[str]:
    if not auto_confirmed:
        return []
    gaps: list[str] = []
    unsupported = sorted(set(metrics) - {"high", "low"})
    if unsupported:
        gaps.append(f"unsupported affected temperature metrics: {unsupported}")
    future_or_recent = list(
        date_scope.get("future_or_recent_dates_not_fetchable_by_wu_history") or []
    )
    if future_or_recent:
        gaps.append(
            "affected market dates are not fetchable by WU history yet: "
            f"{future_or_recent}; executable_wu_fetch_end="
            f"{date_scope.get('executable_wu_fetch_end')}; keep source quarantine active"
        )
    return gaps


def _evidence_root(run_id: str, city: str) -> str:
    slug = city.lower().replace(" ", "_").replace("/", "_")
    return f"docs/operations/task_2026-04-30_source_auto_conversion/evidence/{run_id}/{slug}"


def _evidence_root_path(run_id: str, city: str, *, base: Path | None = None) -> Path:
    if base is None:
        return ROOT / _evidence_root(run_id, city)
    slug = city.lower().replace(" ", "_").replace("/", "_")
    return base / run_id / slug


def _workspace_locator(
    run_id: str,
    city: str,
    *,
    source_change_git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_root = _evidence_root(run_id, city)
    source_change_git = source_change_git or {}
    return {
        "repo_root": str(ROOT),
        "source_change_git": source_change_git,
        "required_worktree": source_change_git.get("worktree_path"),
        "required_branch": source_change_git.get("branch_name"),
        "primary_runtime_artifacts": [
            {
                "id": "latest_receipt",
                "path": "state/source_contract_auto_convert/latest.json",
                "access": "read",
                "purpose": "current cron output and mini execution contract",
            },
            {
                "id": "source_quarantine",
                "path": "state/source_contract_quarantine.json",
                "access": "read_write_by_controller_only",
                "purpose": "city source quarantine; blocks new entries while old positions can monitor and exit",
            },
            {
                "id": "evidence_root",
                "path": evidence_root,
                "access": "write_evidence_only",
                "purpose": "run-scoped evidence artifacts referenced by release evidence",
            },
        ],
        "code_navigation": [
            {
                "id": "controller",
                "path": "scripts/source_contract_auto_convert.py",
                "look_for": ["build_receipt", "_mini_execution_packet", "_workspace_locator"],
                "purpose": "cron controller, receipt, mini work order, Discord and report generation",
                "access": "read_or_change_only_in_controller_packet",
            },
            {
                "id": "source_watch",
                "path": "scripts/watch_source_contract.py",
                "look_for": ["analyze_events", "apply_source_quarantines", "build_conversion_plan"],
                "purpose": "Gamma source-contract analysis and quarantine writer",
                "access": "read_or_run",
            },
            {
                "id": "quarantine_helpers",
                "path": "src/data/market_scanner.py",
                "look_for": [
                    "SOURCE_CONTRACT_ALERT_STATUSES",
                    "REQUIRED_SOURCE_CONVERSION_EVIDENCE",
                    "release_source_contract_quarantine",
                    "source_contract_transition_branch",
                ],
                "purpose": "source-contract status classification, quarantine state, and release evidence contract",
                "access": "read_only_in_this_phase",
            },
            {
                "id": "city_config_truth",
                "path": "config/cities.json",
                "look_for": [f"\"name\": \"{city}\"", "settlement_source", "wu_station"],
                "purpose": "configured settlement source contract",
                "access": "deterministic_controller_write_only_under_execute_apply",
            },
            {
                "id": "source_current_fact",
                "path": "docs/operations/current_source_validity.md",
                "look_for": [city, "source-contract"],
                "purpose": "operator current-fact summary; not durable law",
                "access": "write_only_with_evidence_patch",
            },
            {
                "id": "wu_backfill",
                "path": "scripts/backfill_wu_daily_all.py",
                "look_for": ["--cities", "--start-date", "--end-date", "--replace-station-mismatch"],
                "purpose": "WU daily observation backfill from live config station identity",
                "access": "exact_command_only",
            },
            {
                "id": "settlement_rebuild",
                "path": "scripts/rebuild_settlements.py",
                "look_for": ["rebuild_settlements_scoped", "--city", "--start-date", "--end-date", "--temperature-metric"],
                "purpose": "date- and metric-scoped settlement rebuild helper",
                "access": "exact_command_only",
            },
            {
                "id": "calibration_pairs_rebuild",
                "path": "scripts/rebuild_calibration_pairs_v2.py",
                "look_for": ["rebuild_all_v2", "--city", "--dry-run", "--no-dry-run", "--force"],
                "purpose": "calibration pair rebuild helper",
                "access": "run_dry_only_from_allowed_command",
            },
            {
                "id": "platt_refit",
                "path": "scripts/refit_platt_v2.py",
                "look_for": ["refit_all_v2", "--cluster", "--season", "--data-version", "--temperature-metric"],
                "purpose": "bucket-scoped Platt refit helper",
                "access": "exact_command_only",
            },
        ],
        "future_capability_locations": [
            {
                "capability": "deterministic_config_writer",
                "candidate_path": "scripts/source_contract_config_writer.py",
                "required_test": "tests/test_market_scanner_provenance.py",
                "allowed_write_target_when_unblocked": "config/cities.json",
                "purpose": "apply same-provider WU station/source URL updates without model-authored JSON edits",
            },
            {
                "capability": "scoped_settlement_rebuild",
                "candidate_path": "scripts/rebuild_settlements.py",
                "required_test": "tests/test_rebuild_pipeline.py",
                "required_cli": ["--start-date", "--end-date", "--temperature-metric"],
                "purpose": "avoid broad city-wide or high-only settlement mutation",
            },
            {
                "capability": "low_track_settlement_rebuild",
                "candidate_path": "scripts/rebuild_settlements.py",
                "required_test": "tests/test_settlements_physical_quantity_invariant.py",
                "purpose": "support affected low-temperature markets without cross-metric contamination",
            },
            {
                "capability": "scoped_platt_refit",
                "candidate_path": "scripts/refit_platt_v2.py",
                "required_test": "tests/test_phase4_platt_v2.py",
                "required_cli": ["--city or bucket selector", "--temperature-metric"],
                "purpose": "avoid all-bucket refit as a mechanical source-transition step",
            },
        ],
    }


def _safe_execution_contract(
    run_id: str,
    city: str,
    *,
    source_change_git: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence_root = _evidence_root(run_id, city)
    source_change_git = source_change_git or {}
    return {
        "command_policy": "exact_allowed_command_only",
        "stable_controller_cwd": str(ROOT),
        "apply_cwd_required": source_change_git.get("worktree_path") or str(ROOT),
        "source_change_branch_required": source_change_git.get("branch_name"),
        "cron_lock_path": str(DEFAULT_LOCK_PATH),
        "allowed_write_globs_current_phase": [
            "state/source_contract_quarantine.json",
            "state/source_contract_auto_convert/*.json",
            "state/source_contract_auto_convert/*.mini_report.md",
            "state/backfill_manifest_wu_daily_all_*.json",
            "config/cities.json (only via source_contract_auto_convert.py --execute-apply --force)",
            "state/zeus-world.db (only via exact scoped rebuild commands from the receipt)",
            f"{evidence_root}/*",
        ],
        "forbidden_write_globs_current_phase": [
            "state/zeus.db",
            "state/risk_state*.db",
            "src/state/**",
            "src/execution/**",
            "src/engine/**",
            "docs/authority/**",
        ],
        "forbidden_command_tokens": [
            "--apply/--no-dry-run/--force outside the exact allowed commands in this receipt",
            "rm",
            "mv state/",
            "sqlite3 state/",
            "DELETE FROM",
            "DROP TABLE",
            "git reset",
            "git checkout --",
            "git clean",
        ],
        "preflight_before_running_allowed_command": [
            "If source_change_branch_required is present, create/use that worktree before apply-oriented commands.",
            "Confirm command exactly matches one step_protocol.allowed_command.",
            "Confirm no forbidden_command_tokens appear in the command.",
            "Run apply-oriented commands from apply_cwd_required.",
            "Write stdout/stderr or generated manifest path into evidence_manifest before advancing.",
        ],
        "stop_and_report_if": [
            "requested action is not listed in allowed_actions",
            "write target is outside allowed_write_globs_current_phase",
            "command contains any forbidden_command_tokens",
            "a required artifact is missing",
            "a deterministic script exits non-zero",
            "Gamma/source watch data is unavailable",
            "Discord is required but notification status is not sent",
        ],
    }


def _evidence_manifest(run_id: str, city: str) -> dict[str, dict[str, str]]:
    root = _evidence_root(run_id, city)
    return {
        "config_updated": {
            "status": "missing",
            "expected_artifact": f"{root}/config_update.json",
            "description": ms.SOURCE_CONVERSION_EVIDENCE_DESCRIPTIONS["config_updated"],
        },
        "source_validity_updated": {
            "status": "missing",
            "expected_artifact": f"{root}/current_source_validity_patch.md",
            "description": ms.SOURCE_CONVERSION_EVIDENCE_DESCRIPTIONS["source_validity_updated"],
        },
        "backfill_completed": {
            "status": "missing",
            "expected_artifact": f"{root}/backfill_manifest.json",
            "description": ms.SOURCE_CONVERSION_EVIDENCE_DESCRIPTIONS["backfill_completed"],
        },
        "settlements_rebuilt": {
            "status": "missing",
            "expected_artifact": f"{root}/settlement_rebuild_receipt.json",
            "description": ms.SOURCE_CONVERSION_EVIDENCE_DESCRIPTIONS["settlements_rebuilt"],
        },
        "calibration_rebuilt": {
            "status": "missing",
            "expected_artifact": f"{root}/calibration_rebuild_receipt.json",
            "description": ms.SOURCE_CONVERSION_EVIDENCE_DESCRIPTIONS["calibration_rebuilt"],
        },
        "verification_passed": {
            "status": "missing",
            "expected_artifact": f"{root}/verification_receipt.json",
            "description": ms.SOURCE_CONVERSION_EVIDENCE_DESCRIPTIONS["verification_passed"],
        },
    }


def _mini_step_protocol(
    *,
    run_id: str,
    city: str,
    auto_confirmed: bool,
    threshold_blockers: list[str],
    runtime_gaps: list[str],
    command_plan: list[dict[str, Any]],
    source_change_git: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    evidence = _evidence_manifest(run_id, city)
    dry_run_by_id = {step["id"]: step for step in command_plan}
    blocked_by_gap = bool(runtime_gaps)
    source_change_git = source_change_git or {}
    steps = [
        {
            "id": "prepare_source_change_worktree",
            "status": "pending" if auto_confirmed else "blocked",
            "allowed_actor": "mini_or_cron_can_run_exact_command",
            "allowed_command": source_change_git.get("create_command"),
            "evidence_key": "source_change_git",
            "expected_artifact": source_change_git.get("worktree_path"),
            "stop_if": [
                "branch/worktree path does not match source_change_git",
                "worktree already exists but is not on the expected branch",
                "git command exits non-zero for any reason other than already-existing reviewed worktree",
            ],
        },
        {
            "id": "detect_and_quarantine",
            "status": "done" if auto_confirmed or threshold_blockers else "noop",
            "allowed_actor": "deterministic_controller",
            "evidence_key": "source_watch",
            "allowed_command": dry_run_by_id.get("source_watch_confirm", {}).get("command"),
            "stop_if": ["source watch data unavailable", "quarantine write failed"],
        },
        {
            "id": "threshold_classification",
            "status": "done" if auto_confirmed else "blocked",
            "allowed_actor": "deterministic_controller",
            "blockers": threshold_blockers,
            "stop_if": ["any threshold_blocker is present"],
        },
        {
            "id": "execute_apply_controller",
            "status": "pending" if auto_confirmed and not blocked_by_gap else "blocked",
            "allowed_actor": "mini_or_cron_can_run_exact_command",
            "allowed_command": dry_run_by_id.get("controller_apply", {}).get("command"),
            "evidence_key": "all_release_evidence",
            "expected_artifact": f"{_evidence_root(run_id, city)}/verification_receipt.json",
            "stop_if": [
                "command exits non-zero",
                "receipt status is not applied",
                "source quarantine remains active after release",
            ],
        },
        {
            "id": "backfill_dry_run",
            "status": "pending" if auto_confirmed else "blocked",
            "allowed_actor": "mini_or_cron_can_run_exact_command",
            "allowed_command": dry_run_by_id.get("wu_backfill_dry_run", {}).get("command"),
            "evidence_key": "backfill_completed",
            "expected_artifact": evidence["backfill_completed"]["expected_artifact"],
            "stop_if": ["command exits non-zero", "manifest missing", "unexpected zero coverage"],
        },
        {
            "id": "config_update",
            "status": "blocked" if blocked_by_gap else "pending",
            "allowed_actor": "deterministic_config_writer_required",
            "evidence_key": "config_updated",
            "expected_artifact": evidence["config_updated"]["expected_artifact"],
            "stop_if": ["config writer missing", "new source family is not wu_icao", "station proof is not singular"],
        },
        {
            "id": "settlement_rebuild",
            "status": "blocked" if blocked_by_gap else "pending",
            "allowed_actor": "deterministic_rebuild_script_required",
            "allowed_command": dry_run_by_id.get("settlements_rebuild_dry_run", {}).get("command"),
            "evidence_key": "settlements_rebuilt",
            "expected_artifact": evidence["settlements_rebuilt"]["expected_artifact"],
            "stop_if": ["date/metric scope unavailable", "low metric affected without low rebuild support"],
        },
        {
            "id": "calibration_rebuild",
            "status": "blocked" if blocked_by_gap else "pending",
            "allowed_actor": "deterministic_rebuild_script_required",
            "allowed_command": dry_run_by_id.get("calibration_pairs_rebuild_dry_run", {}).get("command"),
            "evidence_key": "calibration_rebuilt",
            "expected_artifact": evidence["calibration_rebuilt"]["expected_artifact"],
            "stop_if": ["calibration dry-run fails", "Platt scope is broader than policy allows"],
        },
        {
            "id": "verify_and_release",
            "status": "blocked" if blocked_by_gap or not auto_confirmed else "pending_after_evidence",
            "allowed_actor": "deterministic_controller",
            "allowed_command": dry_run_by_id.get("post_conversion_source_watch", {}).get("command"),
            "evidence_key": "verification_passed",
            "expected_artifact": evidence["verification_passed"]["expected_artifact"],
            "stop_if": ["any release evidence ref missing", "post-conversion source watch still alerts"],
        },
    ]
    return steps


def _mini_execution_packet(
    *,
    run_id: str,
    city: str,
    auto_confirmed: bool,
    threshold_blockers: list[str],
    runtime_gaps: list[str],
    command_plan: list[dict[str, Any]],
    source_change_git: dict[str, Any] | None,
) -> dict[str, Any]:
    can_complete = auto_confirmed and not threshold_blockers and not runtime_gaps
    missing = list(threshold_blockers) + list(runtime_gaps)
    return {
        "mini_model_can_directly_complete": can_complete,
        "confidence": "high" if missing else "medium",
        "current_authority": "report_and_dry_run_only" if missing else "ready_for_deterministic_apply_packet",
        "missing_capabilities_or_blockers": missing,
        "allowed_actions": [
            "Read this receipt and linked evidence artifacts.",
            "Create or reuse the exact source_change_git branch/worktree before apply-oriented commands.",
            "Prefer the execute_apply_controller step when present; it runs the full deterministic workflow.",
            "Run only commands listed under step_protocol.allowed_command.",
            "Write a report summarizing status, blockers, next deterministic capability, and receipt path.",
            "Keep source-contract quarantine active until release evidence refs are complete.",
        ],
        "forbidden_actions": [
            "Do not invent source adapters or infer provider semantics from free text.",
            "Do not hand-edit config/cities.json; only the deterministic controller may write it under --execute-apply --force.",
            "Do not mutate production DB truth except through the exact scoped commands in this receipt after DB backup succeeds.",
            "Do not run --apply, --no-dry-run, or --force commands unless they exactly match a step_protocol.allowed_command.",
            "Do not run conversion apply from the stable cron repo when source_change_git requires an isolated worktree.",
            "Do not release source quarantine while any evidence_manifest item is missing.",
        ],
        "evidence_manifest": _evidence_manifest(run_id, city),
        "source_change_git": source_change_git or {},
        "workspace_locator": _workspace_locator(
            run_id,
            city,
            source_change_git=source_change_git,
        ),
        "safe_execution_contract": _safe_execution_contract(
            run_id,
            city,
            source_change_git=source_change_git,
        ),
        "step_protocol": _mini_step_protocol(
            run_id=run_id,
            city=city,
            auto_confirmed=auto_confirmed,
            threshold_blockers=threshold_blockers,
            runtime_gaps=runtime_gaps,
            command_plan=command_plan,
            source_change_git=source_change_git,
        ),
        "report_template": {
            "city": city,
            "can_complete_remaining_conversion": can_complete,
            "source_quarantine_should_remain_active": True,
            "blocking_reasons": missing,
            "next_safe_action": (
                "execute listed deterministic apply steps and collect evidence refs"
                if can_complete
                else "write report, keep quarantine active, and request missing deterministic capability"
            ),
        },
    }


def _metric_scope_arg(metrics: list[str]) -> str:
    normalized = sorted(set(metrics))
    if normalized == ["high"]:
        return "high"
    if normalized == ["low"]:
        return "low"
    return "all"


def _city_cluster_and_lat(city: str) -> tuple[str, float]:
    for city_cfg in load_cities():
        if city_cfg.name.lower() == city.lower():
            return city_cfg.cluster, city_cfg.lat
    return city, 90.0


def _seasons_for_scope(date_scope: dict[str, Any], *, city: str) -> list[str]:
    start = _iso_date(date_scope.get("backfill_start"))
    end = _iso_date(date_scope.get("executable_wu_fetch_end"))
    if start is None or end is None or end < start:
        return ["DJF", "MAM", "JJA", "SON"]
    _, lat = _city_cluster_and_lat(city)
    seasons: set[str] = set()
    cursor = date(start.year, start.month, 1)
    end_month = date(end.year, end.month, 1)
    while cursor <= end_month:
        seasons.add(season_from_date(cursor.isoformat(), lat=lat))
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)
    return sorted(seasons)


def _season_cli_args(seasons: list[str]) -> list[str]:
    args: list[str] = []
    for season in sorted(set(seasons)):
        args.extend(["--season", season])
    return args


def _command_plan(city: str, metrics: list[str], date_scope: dict[str, Any]) -> list[dict[str, Any]]:
    python_bin = sys.executable or "python3"
    metric_scope = _metric_scope_arg(metrics)
    cluster, _ = _city_cluster_and_lat(city)
    seasons = _seasons_for_scope(date_scope, city=city)
    season_args = _season_cli_args(seasons)
    db_path = str(DEFAULT_WORLD_DB_PATH)
    plan = [
        {
            "id": "source_watch_confirm",
            "mode": "read_or_quarantine",
            "command": [
                python_bin,
                "scripts/watch_source_contract.py",
                "--city",
                city,
                "--json",
            ],
            "writes": ["state/source_contract_quarantine.json"],
        },
        {
            "id": "controller_apply",
            "mode": "apply_controller",
            "command": [
                python_bin,
                "scripts/source_contract_auto_convert.py",
                "--city",
                city,
                "--execute-apply",
                "--force",
                "--db",
                db_path,
                "--config-path",
                str(DEFAULT_CITY_CONFIG_PATH),
                "--source-validity-path",
                str(DEFAULT_SOURCE_VALIDITY_PATH),
                "--evidence-root-base",
                str(DEFAULT_EVIDENCE_BASE),
            ],
            "writes": [
                "state/source_contract_quarantine.json",
                "state/source_contract_auto_convert/*.json",
                "config/cities.json",
                db_path,
                "docs/operations/current_source_validity.md",
            ],
            "purpose": "preferred mini/Venus entrypoint; runs all internal dry-run/apply/verify/release steps",
        },
        {
            "id": "wu_backfill_dry_run",
            "mode": "dry_run",
            "command": [
                python_bin,
                "scripts/backfill_wu_daily_all.py",
                "--cities",
                city,
                "--start-date",
                date_scope["backfill_start"],
                "--end-date",
                date_scope["executable_wu_fetch_end"],
                "--missing-only",
                "--replace-station-mismatch",
                "--db",
                db_path,
                "--dry-run",
            ],
            "writes": ["state/backfill_manifest_wu_daily_all_*.json"],
            "date_scope": {
                "backfill_start": date_scope["backfill_start"],
                "executable_wu_fetch_end": date_scope["executable_wu_fetch_end"],
                "backfill_days": int(date_scope["backfill_days"]),
            },
        },
        {
            "id": "wu_backfill_apply",
            "mode": "apply",
            "command": [
                python_bin,
                "scripts/backfill_wu_daily_all.py",
                "--cities",
                city,
                "--start-date",
                date_scope["backfill_start"],
                "--end-date",
                date_scope["executable_wu_fetch_end"],
                "--missing-only",
                "--replace-station-mismatch",
                "--db",
                db_path,
            ],
            "writes": [db_path, "state/backfill_manifest_wu_daily_all_*.json"],
            "requires": ["db_backup", "config_update"],
        },
        {
            "id": "settlements_rebuild_dry_run",
            "mode": "dry_run",
            "command": [
                python_bin,
                "scripts/rebuild_settlements.py",
                "--city",
                city,
                "--start-date",
                date_scope["backfill_start"],
                "--end-date",
                date_scope["executable_wu_fetch_end"],
                "--temperature-metric",
                metric_scope,
                "--db",
                db_path,
            ],
            "writes": [],
        },
        {
            "id": "settlements_rebuild_apply",
            "mode": "apply",
            "command": [
                python_bin,
                "scripts/rebuild_settlements.py",
                "--city",
                city,
                "--start-date",
                date_scope["backfill_start"],
                "--end-date",
                date_scope["executable_wu_fetch_end"],
                "--temperature-metric",
                metric_scope,
                "--db",
                db_path,
                "--apply",
            ],
            "writes": [db_path],
            "requires": ["db_backup", "backfill_completed"],
        },
        {
            "id": "calibration_pairs_rebuild_dry_run",
            "mode": "dry_run",
            "command": [
                python_bin,
                "scripts/rebuild_calibration_pairs_v2.py",
                "--dry-run",
                "--city",
                city,
                "--start-date",
                date_scope["backfill_start"],
                "--end-date",
                date_scope["executable_wu_fetch_end"],
                "--temperature-metric",
                metric_scope,
                "--db",
                db_path,
                "--n-mc",
                "1000",
            ],
            "writes": [],
        },
        {
            "id": "calibration_pairs_rebuild_apply",
            "mode": "apply",
            "command": [
                python_bin,
                "scripts/rebuild_calibration_pairs_v2.py",
                "--no-dry-run",
                "--force",
                "--city",
                city,
                "--start-date",
                date_scope["backfill_start"],
                "--end-date",
                date_scope["executable_wu_fetch_end"],
                "--temperature-metric",
                metric_scope,
                "--db",
                db_path,
                "--n-mc",
                "1000",
            ],
            "writes": [db_path],
            "requires": ["db_backup", "settlements_rebuilt"],
        },
        {
            "id": "platt_refit_dry_run",
            "mode": "dry_run",
            "command": [
                python_bin,
                "scripts/refit_platt_v2.py",
                "--dry-run",
                "--temperature-metric",
                metric_scope,
                "--cluster",
                cluster,
                "--city",
                city,
                "--start-date",
                date_scope["backfill_start"],
                "--end-date",
                date_scope["executable_wu_fetch_end"],
                *season_args,
                "--db",
                db_path,
            ],
            "bucket_scope": {
                "city": city,
                "cluster": cluster,
                "seasons": seasons,
                "date_start": date_scope["backfill_start"],
                "date_end": date_scope["executable_wu_fetch_end"],
                "data_versions": "derived_from_scoped_calibration_pairs",
            },
            "writes": [],
        },
        {
            "id": "platt_refit_apply",
            "mode": "apply",
            "command": [
                python_bin,
                "scripts/refit_platt_v2.py",
                "--no-dry-run",
                "--force",
                "--temperature-metric",
                metric_scope,
                "--cluster",
                cluster,
                "--city",
                city,
                "--start-date",
                date_scope["backfill_start"],
                "--end-date",
                date_scope["executable_wu_fetch_end"],
                *season_args,
                "--db",
                db_path,
            ],
            "bucket_scope": {
                "city": city,
                "cluster": cluster,
                "seasons": seasons,
                "date_start": date_scope["backfill_start"],
                "date_end": date_scope["executable_wu_fetch_end"],
                "data_versions": "derived_from_scoped_calibration_pairs",
            },
            "writes": [db_path],
            "requires": ["db_backup", "calibration_pairs_rebuilt"],
        },
        {
            "id": "post_conversion_source_watch",
            "mode": "verification",
            "command": [
                python_bin,
                "scripts/watch_source_contract.py",
                "--city",
                city,
                "--json",
                "--report-only",
            ],
            "writes": [],
        },
    ]
    return plan


def build_candidates(report: dict[str, Any], policy: RuntimePolicy, *, run_id: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for city, events in _alert_events_by_city(report).items():
        branch = _transition_branch(events)
        summary = _source_contract_summary(events)
        metrics = _sorted_unique([event.get("temperature_metric") for event in events])
        date_scope = _date_scope(events, policy)
        threshold_blockers = _threshold_blockers(
            branch=branch,
            summary=summary,
            events=events,
            date_scope=date_scope,
            policy=policy,
        )
        auto_confirmed = not threshold_blockers
        runtime_gaps = _runtime_gaps(
            metrics,
            date_scope,
            auto_confirmed=auto_confirmed,
        )
        command_plan = _command_plan(city, metrics, date_scope) if auto_confirmed else []
        source_change_git = _source_change_git_workspace(
            run_id=run_id,
            city=city,
            source_contract=summary,
            date_scope=date_scope,
        )
        mini_packet = _mini_execution_packet(
            run_id=run_id,
            city=city,
            auto_confirmed=auto_confirmed,
            threshold_blockers=threshold_blockers,
            runtime_gaps=runtime_gaps,
            command_plan=command_plan,
            source_change_git=source_change_git,
        )
        candidates.append(
            {
                "city": city,
                "transition_branch": branch,
                "source_change_git": source_change_git,
                "confirmation_status": "auto_confirmed" if auto_confirmed else "manual_review_required",
                "release_ready": False,
                "alert_event_count": len(events),
                "event_ids": _sorted_unique([event.get("event_id") for event in events]),
                "affected_metrics": metrics,
                "source_contract": summary,
                "date_scope": date_scope,
                "threshold_blockers": threshold_blockers,
                "runtime_gaps_before_apply": runtime_gaps,
                "command_plan": command_plan,
                "mini_llm_execution": mini_packet,
                "release_evidence_required": list(ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE),
                "release_evidence_refs_required": {
                    key: ms.SOURCE_CONVERSION_EVIDENCE_DESCRIPTIONS[key]
                    for key in ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE
                },
            }
        )
    return candidates


def build_receipt(
    report: dict[str, Any],
    *,
    policy: RuntimePolicy,
    run_id: str | None = None,
    quarantine_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_run_id = run_id or f"source_contract_auto_convert_{_utcnow().strftime('%Y%m%dT%H%M%SZ')}"
    candidates = build_candidates(report, policy, run_id=resolved_run_id)
    if not candidates:
        status = "noop"
    elif any(candidate["threshold_blockers"] for candidate in candidates):
        status = "blocked"
    else:
        status = "planned"

    checked_at = str(report.get("checked_at_utc") or _utcnow().isoformat())
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": resolved_run_id,
        "status": status,
        "mode": "plan_only",
        "checked_at_utc": checked_at,
        "policy": {
            "history_days": policy.history_days,
            "min_alert_markets": policy.min_alert_markets,
            "min_target_dates": policy.min_target_dates,
            "today": policy.today.isoformat(),
        },
        "watch_status": report.get("status"),
        "watch_authority": report.get("authority"),
        "watch_summary": report.get("summary"),
        "watch_checked_event_count": report.get("checked_event_count"),
        "watch_event_count": report.get("event_count"),
        "quarantine_actions": quarantine_actions if quarantine_actions is not None else report.get("quarantine_actions", []),
        "candidates": candidates,
        "next_actions": _next_actions_for_receipt(status, candidates),
        "notification": {"attempted": False, "sent": False, "status": "not_requested"},
    }


def build_failure_receipt(
    *,
    run_id: str | None,
    policy: RuntimePolicy,
    error: str,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or f"source_contract_auto_convert_{_utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        "status": "failed",
        "mode": "plan_only",
        "checked_at_utc": _utcnow().isoformat(),
        "policy": {
            "history_days": policy.history_days,
            "min_alert_markets": policy.min_alert_markets,
            "min_target_dates": policy.min_target_dates,
            "today": policy.today.isoformat(),
        },
        "error": error,
        "watch_status": (report or {}).get("status"),
        "watch_authority": (report or {}).get("authority"),
        "watch_summary": (report or {}).get("summary"),
        "candidates": [],
        "next_actions": ["Operator review required; source auto-conversion controller failed before producing a trusted plan."],
        "notification": {"attempted": False, "sent": False, "status": "not_requested"},
    }


def _candidate_apply_ready(candidate: dict[str, Any]) -> list[str]:
    blockers = []
    if candidate.get("confirmation_status") != "auto_confirmed":
        blockers.append("candidate is not auto_confirmed")
    blockers.extend(candidate.get("threshold_blockers") or [])
    runtime_gaps = list(candidate.get("runtime_gaps_before_apply") or [])
    blockers.extend(runtime_gaps)
    date_scope = candidate.get("date_scope") or {}
    future_or_recent = list(
        date_scope.get("future_or_recent_dates_not_fetchable_by_wu_history") or []
    )
    if future_or_recent and not any("not fetchable by WU history" in str(gap) for gap in runtime_gaps):
        blockers.append(
            "affected market dates are not fetchable by WU history yet: "
            f"{future_or_recent}; executable_wu_fetch_end="
            f"{date_scope.get('executable_wu_fetch_end')}"
        )
    branch = candidate.get("transition_branch")
    if branch != "same_provider_station_change":
        blockers.append(f"unsupported apply branch: {branch}")
    source_contract = candidate.get("source_contract") or {}
    if source_contract.get("from_source_families") != ["wu_icao"]:
        blockers.append("configured source family is not singular wu_icao")
    if source_contract.get("to_source_families") != ["wu_icao"]:
        blockers.append("observed source family is not singular wu_icao")
    if len(source_contract.get("from_station_ids") or []) != 1:
        blockers.append("configured station id is not singular")
    if len(source_contract.get("to_station_ids") or []) != 1:
        blockers.append("observed station id is not singular")
    return blockers


def _load_station_metadata_override(path: Path | None, station_id: str) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("station metadata override must be a JSON object")
    raw = payload.get(station_id) or payload.get(station_id.upper())
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"station metadata override for {station_id} must be an object")
    return _normalize_station_metadata(raw, station_id, source=f"override:{path}")


def _normalize_station_metadata(raw: dict[str, Any], station_id: str, *, source: str) -> dict[str, Any]:
    station = str(
        raw.get("station_id")
        or raw.get("icaoId")
        or raw.get("icao")
        or raw.get("id")
        or station_id
    ).upper()
    if station != station_id.upper():
        raise ValueError(f"station metadata id {station!r} does not match {station_id!r}")
    lat = raw.get("lat", raw.get("latitude"))
    lon = raw.get("lon", raw.get("longitude"))
    if lat is None or lon is None:
        raise ValueError(f"station metadata for {station_id} missing lat/lon")
    airport_name = str(raw.get("airport_name") or raw.get("site") or raw.get("name") or station)
    country = str(raw.get("country_code") or raw.get("country") or "").upper()
    if len(country) > 2:
        country = country[:2]
    return {
        "station_id": station,
        "airport_name": airport_name,
        "lat": float(lat),
        "lon": float(lon),
        "country_code": country,
        "metadata_source": str(raw.get("metadata_source") or source),
    }


def _fetch_station_metadata_from_aviationweather(station_id: str, *, timeout_seconds: float) -> dict[str, Any] | None:
    query = urllib.parse.urlencode({"ids": station_id.upper(), "format": "json"})
    url = f"{AVIATIONWEATHER_STATIONINFO_URL}?{query}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Zeus source_contract_auto_convert/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            if response.status == 204:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None
    rows = payload if isinstance(payload, list) else payload.get("features", []) if isinstance(payload, dict) else []
    for row in rows:
        raw = row
        if isinstance(row, dict) and "properties" in row:
            raw = row.get("properties") or {}
        if not isinstance(raw, dict):
            continue
        try:
            return _normalize_station_metadata(
                raw,
                station_id,
                source=f"aviationweather:{url}",
            )
        except ValueError:
            continue
    return None


def resolve_station_metadata(
    station_id: str,
    *,
    override_path: Path | None = None,
    allow_network: bool = True,
    timeout_seconds: float = 8.0,
) -> dict[str, Any]:
    station = station_id.upper()
    override = _load_station_metadata_override(override_path, station)
    if override is not None:
        return override
    if station in STATIC_WU_STATION_METADATA:
        return _normalize_station_metadata(
            STATIC_WU_STATION_METADATA[station],
            station,
            source=str(STATIC_WU_STATION_METADATA[station]["metadata_source"]),
        )
    if allow_network:
        fetched = _fetch_station_metadata_from_aviationweather(
            station,
            timeout_seconds=timeout_seconds,
        )
        if fetched is not None:
            return fetched
    raise RuntimeError(
        f"exact station metadata unavailable for {station}; refusing config promotion"
    )


def _fetch_noaa_grid(lat: float, lon: float, *, timeout_seconds: float = 8.0) -> dict[str, Any]:
    url = f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Zeus source_contract_auto_convert/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    props = payload.get("properties") if isinstance(payload, dict) else None
    if not isinstance(props, dict):
        raise RuntimeError(f"NOAA grid metadata unavailable for {lat},{lon}")
    return {
        "office": props["gridId"],
        "gridX": int(props["gridX"]),
        "gridY": int(props["gridY"]),
    }


def _select_resolution_source(candidate: dict[str, Any], station_id: str) -> str:
    source_contract = candidate.get("source_contract") or {}
    station = station_id.upper()
    for source in source_contract.get("resolution_sources") or []:
        text = str(source or "")
        if station in text.upper():
            return text
    raise RuntimeError(f"no resolutionSource proving station {station}")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _snapshot_file_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _restore_file_bytes(path: Path, payload: bytes | None) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if payload is None:
        try:
            path.unlink()
            status = "removed"
        except FileNotFoundError:
            status = "already_absent"
    else:
        tmp_path = path.with_name(f".{path.name}.rollback.tmp")
        tmp_path.write_bytes(payload)
        tmp_path.replace(path)
        status = "restored"
    return {
        "path": str(path),
        "status": status,
        "restored_at_utc": _utcnow().isoformat(),
    }


def apply_config_update(
    candidate: dict[str, Any],
    *,
    config_path: Path,
    evidence_root: Path,
    station_metadata_path: Path | None = None,
    allow_station_metadata_network: bool = True,
) -> dict[str, Any]:
    city = str(candidate["city"])
    source_contract = candidate["source_contract"]
    from_station = source_contract["from_station_ids"][0]
    to_station = source_contract["to_station_ids"][0]
    resolution_source = _select_resolution_source(candidate, to_station)
    metadata = resolve_station_metadata(
        to_station,
        override_path=station_metadata_path,
        allow_network=allow_station_metadata_network,
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    rows = payload.get("cities")
    if not isinstance(rows, list):
        raise ValueError(f"{config_path} missing cities array")
    row = None
    for city_row in rows:
        if isinstance(city_row, dict) and str(city_row.get("name") or "").lower() == city.lower():
            row = city_row
            break
    if row is None:
        raise RuntimeError(f"city {city!r} not found in {config_path}")

    before = {
        key: row.get(key)
        for key in (
            "name",
            "lat",
            "lon",
            "noaa",
            "wu_station",
            "wu_pws",
            "meteostat_station",
            "airport_name",
            "country_code",
            "settlement_source",
            "settlement_source_type",
        )
    }
    if str(row.get("wu_station") or "").upper() != str(from_station).upper():
        raise RuntimeError(
            f"{city} config station is {row.get('wu_station')!r}, expected {from_station!r}; refusing stale receipt"
        )

    row["wu_station"] = metadata["station_id"]
    row["settlement_source_type"] = "wu_icao"
    row["settlement_source"] = resolution_source
    row["airport_name"] = metadata["airport_name"]
    row["country_code"] = metadata["country_code"] or row.get("country_code")
    if row.get("wu_pws"):
        row["wu_pws"] = None
    noaa = row.get("noaa")
    if isinstance(noaa, dict):
        noaa["lat"] = metadata["lat"]
        noaa["lon"] = metadata["lon"]
        if str(row.get("country_code") or metadata["country_code"]).upper() == "US":
            noaa.update(_fetch_noaa_grid(metadata["lat"], metadata["lon"]))
    else:
        row["lat"] = metadata["lat"]
        row["lon"] = metadata["lon"]

    after = {
        key: row.get(key)
        for key in (
            "name",
            "lat",
            "lon",
            "noaa",
            "wu_station",
            "wu_pws",
            "meteostat_station",
            "airport_name",
            "country_code",
            "settlement_source",
            "settlement_source_type",
        )
    }
    _write_json_atomic(config_path, payload)
    artifact = {
        "status": "updated",
        "city": city,
        "from_station": from_station,
        "to_station": metadata["station_id"],
        "resolution_source": resolution_source,
        "station_metadata": metadata,
        "config_path": str(config_path),
        "before": before,
        "after": after,
        "updated_at_utc": _utcnow().isoformat(),
    }
    path = evidence_root / "config_update.json"
    _write_json_atomic(path, artifact)
    return {"artifact": artifact, "path": str(path)}


def write_source_validity_patch(
    candidate: dict[str, Any],
    *,
    source_validity_path: Path,
    evidence_root: Path,
    config_artifact: dict[str, Any],
) -> dict[str, Any]:
    now = _utcnow().isoformat()
    source_contract = candidate.get("source_contract") or {}
    dates = candidate.get("date_scope") or {}
    section = "\n".join(
        [
            "",
            f"## {now} Source Auto-Conversion Applied: {candidate.get('city')}",
            "",
            f"- Branch: `{candidate.get('transition_branch')}`.",
            f"- Source contract: `{source_contract.get('from_source_families')}/{source_contract.get('from_station_ids')}` -> `{source_contract.get('to_source_families')}/{source_contract.get('to_station_ids')}`.",
            f"- Affected market dates: `{dates.get('affected_market_start')}` to `{dates.get('affected_market_end')}`; backfill window `{dates.get('backfill_start')}` to `{dates.get('executable_wu_fetch_end')}`.",
            f"- Config artifact: `{evidence_root / 'config_update.json'}`.",
            "- Runtime note: city remained source-quarantined until config, backfill, settlement, calibration, and verification evidence refs were complete.",
            "",
        ]
    )
    source_validity_path.parent.mkdir(parents=True, exist_ok=True)
    previous = source_validity_path.read_text(encoding="utf-8") if source_validity_path.exists() else ""
    tmp_path = source_validity_path.with_name(f".{source_validity_path.name}.tmp")
    tmp_path.write_text(previous.rstrip() + section + "\n", encoding="utf-8")
    tmp_path.replace(source_validity_path)
    patch_artifact = {
        "status": "updated",
        "source_validity_path": str(source_validity_path),
        "updated_at_utc": now,
        "section": section,
        "config_update_ref": str(evidence_root / "config_update.json"),
        "station_metadata_source": (config_artifact.get("station_metadata") or {}).get("metadata_source"),
    }
    artifact_path = evidence_root / "current_source_validity_patch.md"
    artifact_path.write_text(section, encoding="utf-8")
    return {"artifact": patch_artifact, "path": str(artifact_path)}


def backup_world_db(db_path: Path, *, evidence_root: Path) -> dict[str, Any]:
    if not db_path.exists():
        raise RuntimeError(f"world DB does not exist: {db_path}")
    evidence_root.mkdir(parents=True, exist_ok=True)
    backup_path = evidence_root / f"{db_path.name}.backup"
    shutil.copy2(db_path, backup_path)
    artifact = {
        "status": "created",
        "source_db": str(db_path),
        "backup_path": str(backup_path),
        "size_bytes": backup_path.stat().st_size,
        "created_at_utc": _utcnow().isoformat(),
    }
    _write_json_atomic(evidence_root / "db_backup.json", artifact)
    return artifact


def _run_command(command: list[str], *, cwd: Path, artifact_path: Path) -> dict[str, Any]:
    started = _utcnow()
    completed = subprocess_run_with_write_class(
        [str(part) for part in command],
        WriteClass.BULK,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    receipt = {
        "command": [str(part) for part in command],
        "cwd": str(cwd),
        "started_at_utc": started.isoformat(),
        "finished_at_utc": _utcnow().isoformat(),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
    }
    _write_json_atomic(artifact_path, receipt)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed rc={completed.returncode}: {' '.join(str(part) for part in command)}"
        )
    return receipt


def _replace_db_arg(command: list[str], db_path: Path) -> list[str]:
    result = [str(part) for part in command]
    if "--db" in result:
        result[result.index("--db") + 1] = str(db_path)
    return result


def _controller_apply_command_for_args(city: str, args: argparse.Namespace) -> list[str]:
    command = [
        sys.executable or "python3",
        "scripts/source_contract_auto_convert.py",
        "--city",
        city,
        "--execute-apply",
        "--force",
        "--history-days",
        str(args.history_days),
        "--min-alert-markets",
        str(args.min_alert_markets),
        "--min-target-dates",
        str(args.min_target_dates),
        "--db",
        str(args.db),
        "--config-path",
        str(args.config_path),
        "--source-validity-path",
        str(args.source_validity_path),
        "--evidence-root-base",
        str(args.evidence_root_base),
        "--receipt-dir",
        str(args.receipt_dir),
        "--lock-path",
        str(args.lock_path),
        "--json",
    ]
    if args.today is not None:
        command.extend(["--today", args.today.isoformat()])
    if args.fixture is not None:
        command.extend(["--fixture", str(args.fixture)])
    if args.quarantine_path is not None:
        command.extend(["--quarantine-path", str(args.quarantine_path)])
    if args.station_metadata_path is not None:
        command.extend(["--station-metadata-path", str(args.station_metadata_path)])
    if args.no_station_metadata_network:
        command.append("--no-station-metadata-network")
    if args.include_unconfigured:
        command.append("--include-unconfigured")
    if args.no_lock:
        command.append("--no-lock")
    if args.discord:
        command.append("--discord")
    if args.discord_required:
        command.append("--discord-required")
    if args.notify_noop:
        command.append("--notify-noop")
    return command


def stamp_runtime_invocation(receipt: dict[str, Any], args: argparse.Namespace) -> None:
    """Put exact current-run paths into mini/Venus command plans."""
    for candidate in receipt.get("candidates") or []:
        command_plan = candidate.get("command_plan") or []
        for step in command_plan:
            command = [str(part) for part in step.get("command") or []]
            if not command:
                continue
            step_id = step.get("id")
            if step_id == "controller_apply":
                step["command"] = _controller_apply_command_for_args(
                    str(candidate.get("city") or ""),
                    args,
                )
                continue
            if "--db" in command:
                command = _replace_db_arg(command, args.db)
            if "scripts/watch_source_contract.py" in command:
                command = _add_fixture_and_quarantine_args(
                    command,
                    fixture=args.fixture,
                    quarantine_path=args.quarantine_path,
                )
            step["command"] = command
        if command_plan:
            candidate["mini_llm_execution"] = _mini_execution_packet(
                run_id=str(receipt.get("run_id")),
                city=str(candidate.get("city")),
                auto_confirmed=candidate.get("confirmation_status") == "auto_confirmed",
                threshold_blockers=list(candidate.get("threshold_blockers") or []),
                runtime_gaps=list(candidate.get("runtime_gaps_before_apply") or []),
                command_plan=command_plan,
                source_change_git=candidate.get("source_change_git"),
            )


def init_source_change_worktrees(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """Create missing per-source-change git worktrees from receipt metadata."""
    actions: list[dict[str, Any]] = []
    for candidate in receipt.get("candidates") or []:
        source_change_git = candidate.get("source_change_git") or {}
        if not source_change_git.get("required"):
            continue
        branch = str(source_change_git.get("branch_name") or "")
        worktree_value = str(source_change_git.get("worktree_path") or "")
        worktree = Path(worktree_value)
        command = [str(part) for part in source_change_git.get("create_command") or []]
        action = {
            "city": candidate.get("city"),
            "branch_name": branch,
            "worktree_path": str(worktree),
            "command": command,
            "status": "pending",
        }
        if not branch or not command or not worktree_value:
            action["status"] = "failed"
            action["error"] = "missing source_change_git branch/worktree/command"
            actions.append(action)
            continue
        if worktree.exists():
            current_branch = subprocess.run(
                ["git", "-C", str(worktree), "branch", "--show-current"],
                text=True,
                capture_output=True,
                check=False,
            )
            observed_branch = current_branch.stdout.strip()
            action["observed_branch"] = observed_branch
            if current_branch.returncode == 0 and observed_branch == branch:
                action["status"] = "exists"
                source_change_git["status"] = "exists"
            else:
                action["status"] = "failed"
                action["error"] = (
                    "worktree exists but expected branch was not observed: "
                    f"expected={branch!r} observed={observed_branch!r}"
                )
            actions.append(action)
            continue
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        action.update(
            {
                "returncode": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        )
        if completed.returncode == 0:
            action["status"] = "created"
            source_change_git["status"] = "exists"
        else:
            action["status"] = "failed"
            action["error"] = "git worktree creation failed"
        actions.append(action)
    receipt["source_change_worktree_actions"] = actions
    if any(action.get("status") == "failed" for action in actions):
        receipt["status"] = "failed"
        receipt["next_actions"] = [
            "Source-change worktree creation failed; do not run apply steps.",
            "Inspect source_change_worktree_actions and fix branch/worktree state before rerunning.",
        ]
    return actions


def _add_fixture_and_quarantine_args(
    command: list[str],
    *,
    fixture: Path | None,
    quarantine_path: Path | None,
) -> list[str]:
    result = [str(part) for part in command]
    if fixture is not None and "--fixture" not in result:
        result.extend(["--fixture", str(fixture)])
    if quarantine_path is not None and "--quarantine-path" not in result:
        result.extend(["--quarantine-path", str(quarantine_path)])
    return result


def _same_resolved_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)


def _production_apply_surface_reasons(
    *,
    db_path: Path,
    config_path: Path,
    source_validity_path: Path,
    quarantine_path: Path | None,
    evidence_root_base: Path | None,
) -> list[str]:
    reasons: list[str] = []
    if _same_resolved_path(db_path, DEFAULT_WORLD_DB_PATH):
        reasons.append("default world DB")
    if _same_resolved_path(config_path, DEFAULT_CITY_CONFIG_PATH):
        reasons.append("default city config")
    if _same_resolved_path(source_validity_path, DEFAULT_SOURCE_VALIDITY_PATH):
        reasons.append("default source-validity current fact")
    if quarantine_path is None or _same_resolved_path(quarantine_path, DEFAULT_SOURCE_QUARANTINE_PATH):
        reasons.append("default source quarantine")
    if evidence_root_base is None or _same_resolved_path(evidence_root_base, DEFAULT_EVIDENCE_BASE):
        reasons.append("default evidence root")
    return reasons


def _assert_fixture_allowed_for_apply(
    *,
    fixture: Path | None,
    db_path: Path,
    config_path: Path,
    source_validity_path: Path,
    quarantine_path: Path | None,
    evidence_root_base: Path | None,
) -> None:
    if fixture is None:
        return
    production_reasons = _production_apply_surface_reasons(
        db_path=db_path,
        config_path=config_path,
        source_validity_path=source_validity_path,
        quarantine_path=quarantine_path,
        evidence_root_base=evidence_root_base,
    )
    if production_reasons:
        joined = ", ".join(production_reasons)
        raise RuntimeError(
            "--execute-apply with --fixture is test-only; refusing fixture-backed "
            f"release on production apply surfaces: {joined}"
        )


def _release_evidence_from_manifest(manifest: dict[str, dict[str, str]]) -> dict[str, Any]:
    evidence = {key: True for key in ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE}
    evidence["evidence_refs"] = {
        key: manifest[key]["actual_artifact"]
        for key in ms.REQUIRED_SOURCE_CONVERSION_EVIDENCE
    }
    return evidence


def execute_apply(
    receipt: dict[str, Any],
    *,
    force: bool,
    db_path: Path,
    config_path: Path,
    source_validity_path: Path,
    quarantine_path: Path | None,
    fixture: Path | None,
    station_metadata_path: Path | None,
    allow_station_metadata_network: bool,
    evidence_root_base: Path | None = None,
) -> dict[str, Any]:
    if not force:
        raise RuntimeError("--execute-apply requires --force")
    _assert_fixture_allowed_for_apply(
        fixture=fixture,
        db_path=db_path,
        config_path=config_path,
        source_validity_path=source_validity_path,
        quarantine_path=quarantine_path,
        evidence_root_base=evidence_root_base,
    )
    candidates = receipt.get("candidates") or []
    if not candidates:
        return receipt

    receipt["mode"] = "apply"
    apply_results = []
    failures = []
    for candidate in candidates:
        blockers = _candidate_apply_ready(candidate)
        if blockers:
            failures.append({"city": candidate.get("city"), "blockers": blockers})
            continue

        city = str(candidate["city"])
        evidence_root = _evidence_root_path(
            str(receipt["run_id"]),
            city,
            base=evidence_root_base,
        )
        evidence_root.mkdir(parents=True, exist_ok=True)
        manifest = _evidence_manifest(str(receipt["run_id"]), city)
        candidate["apply_status"] = "running"
        original_config_bytes = _snapshot_file_bytes(config_path)
        original_source_validity_bytes = _snapshot_file_bytes(source_validity_path)

        try:
            if not ms.is_city_source_quarantined(city, path=quarantine_path):
                raise RuntimeError(
                    f"{city} has no active source-contract quarantine; refusing apply without entry gate"
                )
            db_backup = backup_world_db(db_path, evidence_root=evidence_root)
            config_result = apply_config_update(
                candidate,
                config_path=config_path,
                evidence_root=evidence_root,
                station_metadata_path=station_metadata_path,
                allow_station_metadata_network=allow_station_metadata_network,
            )
            manifest["config_updated"].update(
                status="complete",
                actual_artifact=config_result["path"],
            )
            source_validity = write_source_validity_patch(
                candidate,
                source_validity_path=source_validity_path,
                evidence_root=evidence_root,
                config_artifact=config_result["artifact"],
            )
            manifest["source_validity_updated"].update(
                status="complete",
                actual_artifact=source_validity["path"],
            )

            plan_by_id = {step["id"]: step for step in candidate.get("command_plan") or []}
            backfill_receipts = []
            for step_id in ("wu_backfill_dry_run", "wu_backfill_apply"):
                command = _replace_db_arg(plan_by_id[step_id]["command"], db_path)
                backfill_receipts.append(
                    _run_command(
                        command,
                        cwd=ROOT,
                        artifact_path=evidence_root / f"{step_id}.json",
                    )
                )
            backfill_artifact = {
                "status": "complete",
                "db_backup": db_backup,
                "commands": backfill_receipts,
            }
            _write_json_atomic(evidence_root / "backfill_manifest.json", backfill_artifact)
            manifest["backfill_completed"].update(
                status="complete",
                actual_artifact=str(evidence_root / "backfill_manifest.json"),
            )

            settlement_receipts = []
            for step_id in ("settlements_rebuild_dry_run", "settlements_rebuild_apply"):
                command = _replace_db_arg(plan_by_id[step_id]["command"], db_path)
                settlement_receipts.append(
                    _run_command(
                        command,
                        cwd=ROOT,
                        artifact_path=evidence_root / f"{step_id}.json",
                    )
                )
            settlement_artifact = {
                "status": "complete",
                "commands": settlement_receipts,
            }
            _write_json_atomic(evidence_root / "settlement_rebuild_receipt.json", settlement_artifact)
            manifest["settlements_rebuilt"].update(
                status="complete",
                actual_artifact=str(evidence_root / "settlement_rebuild_receipt.json"),
            )

            calibration_receipts = []
            for step_id in (
                "calibration_pairs_rebuild_dry_run",
                "calibration_pairs_rebuild_apply",
                "platt_refit_dry_run",
                "platt_refit_apply",
            ):
                command = _replace_db_arg(plan_by_id[step_id]["command"], db_path)
                calibration_receipts.append(
                    _run_command(
                        command,
                        cwd=ROOT,
                        artifact_path=evidence_root / f"{step_id}.json",
                    )
                )
            calibration_artifact = {
                "status": "complete",
                "commands": calibration_receipts,
            }
            _write_json_atomic(evidence_root / "calibration_rebuild_receipt.json", calibration_artifact)
            manifest["calibration_rebuilt"].update(
                status="complete",
                actual_artifact=str(evidence_root / "calibration_rebuild_receipt.json"),
            )

            verify_command = _add_fixture_and_quarantine_args(
                plan_by_id["post_conversion_source_watch"]["command"],
                fixture=fixture,
                quarantine_path=quarantine_path,
            )
            verification_receipt = _run_command(
                verify_command,
                cwd=ROOT,
                artifact_path=evidence_root / "post_conversion_source_watch.json",
            )
            try:
                verification_report = json.loads(verification_receipt.get("stdout") or "{}")
            except json.JSONDecodeError as exc:
                raise RuntimeError("post-conversion source watch did not emit JSON") from exc
            if verification_report.get("status") == "ALERT":
                raise RuntimeError("post-conversion source watch still reports ALERT")
            verification_artifact = {
                "status": "complete",
                "watch_report": verification_report,
                "command_receipt": str(evidence_root / "post_conversion_source_watch.json"),
            }
            _write_json_atomic(evidence_root / "verification_receipt.json", verification_artifact)
            manifest["verification_passed"].update(
                status="complete",
                actual_artifact=str(evidence_root / "verification_receipt.json"),
            )

            release_evidence = _release_evidence_from_manifest(manifest)
            release_result = ms.release_source_contract_quarantine(
                city,
                released_by="source_contract_auto_convert",
                evidence=release_evidence,
                path=quarantine_path,
            )
            if release_result.get("status") != "released":
                raise RuntimeError(f"source quarantine release failed: {release_result}")

            candidate["release_ready"] = True
            candidate["apply_status"] = "applied"
            candidate["evidence_manifest"] = manifest
            candidate["release_evidence"] = release_evidence
            candidate["release_result"] = release_result
            mini = candidate.get("mini_llm_execution") or {}
            report_template = mini.get("report_template") or {}
            report_template.update(
                {
                    "can_complete_remaining_conversion": True,
                    "source_quarantine_should_remain_active": False,
                    "blocking_reasons": [],
                    "next_safe_action": "conversion complete; inspect transition history and normal monitoring",
                }
            )
            mini["mini_model_can_directly_complete"] = True
            mini["current_authority"] = "conversion_applied_and_verified"
            mini["report_template"] = report_template
            candidate["mini_llm_execution"] = mini
            apply_results.append(
                {
                    "city": city,
                    "status": "applied",
                    "evidence_root": str(evidence_root),
                    "release_status": release_result.get("status"),
                }
            )
        except Exception as exc:
            candidate["apply_status"] = "failed"
            candidate["apply_error"] = f"{type(exc).__name__}: {exc}"
            candidate["evidence_manifest"] = manifest
            rollback_artifact: dict[str, Any] | None = None
            try:
                rollback_artifact = {
                    "status": "complete",
                    "reason": candidate["apply_error"],
                    "restored": [
                        _restore_file_bytes(config_path, original_config_bytes),
                        _restore_file_bytes(source_validity_path, original_source_validity_bytes),
                    ],
                    "created_at_utc": _utcnow().isoformat(),
                }
                rollback_path = evidence_root / "rollback_manifest.json"
                _write_json_atomic(rollback_path, rollback_artifact)
                candidate["rollback_manifest"] = str(rollback_path)
            except Exception as rollback_exc:
                rollback_artifact = {
                    "status": "failed",
                    "reason": candidate["apply_error"],
                    "rollback_error": f"{type(rollback_exc).__name__}: {rollback_exc}",
                    "created_at_utc": _utcnow().isoformat(),
                }
                candidate["rollback_error"] = rollback_artifact["rollback_error"]
            failures.append(
                {
                    "city": city,
                    "error": candidate["apply_error"],
                    "evidence_root": str(evidence_root),
                    "rollback": rollback_artifact,
                }
            )

    receipt["apply_results"] = apply_results
    receipt["apply_failures"] = failures
    if failures:
        receipt["status"] = "failed"
        receipt["next_actions"] = [
            "Keep failed cities in source-contract quarantine; old positions may still monitor and exit.",
            "Read apply_failures and evidence artifacts, fix the deterministic blocker, then rerun --execute-apply --force.",
        ]
    elif apply_results:
        receipt["status"] = "applied"
        receipt["next_actions"] = [
            "Source-contract conversion evidence is complete and affected city quarantine was released.",
            "Run watch_source_contract --history CITY to inspect durable transition history.",
        ]
    return receipt


def _next_actions_for_receipt(status: str, candidates: list[dict[str, Any]]) -> list[str]:
    if status == "noop":
        return ["No source-contract conversion candidate detected."]
    if status == "blocked":
        return [
            "Keep affected cities in source-contract quarantine.",
            "Resolve threshold_blockers before conversion; old positions may still monitor and exit.",
            "Do not release quarantine until every required release evidence ref is present.",
        ]
    return [
        "Keep affected cities in source-contract quarantine while executing the reviewed conversion plan.",
        "Run dry-run commands first and preserve their manifests/logs as release evidence refs.",
        "Do not release quarantine until runtime_gaps_before_apply are resolved and verification passes.",
    ]


def write_receipt(receipt: dict[str, Any], receipt_dir: Path) -> Path:
    receipt_dir.mkdir(parents=True, exist_ok=True)
    path = receipt_dir / f"{receipt['run_id']}.json"
    receipt["receipt_path"] = str(path)
    payload = json.dumps(receipt, indent=2, sort_keys=True) + "\n"
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(payload, encoding="utf-8")
    tmp_path.replace(path)
    latest_path = receipt_dir / "latest.json"
    latest_tmp = latest_path.with_name(".latest.json.tmp")
    latest_tmp.write_text(payload, encoding="utf-8")
    latest_tmp.replace(latest_path)
    return path


def render_mini_report(receipt: dict[str, Any]) -> str:
    """Render a deterministic report a small model can submit or summarize."""
    lines = [
        "# Source Contract Mini Execution Report",
        "",
        f"- run_id: `{receipt.get('run_id')}`",
        f"- status: `{receipt.get('status')}`",
        f"- receipt: `{receipt.get('receipt_path', '<not-written>')}`",
        f"- mode: `{receipt.get('mode')}`",
        "",
    ]
    candidates = receipt.get("candidates") or []
    if not candidates:
        lines.extend(
            [
                "## Result",
                "",
                "No source-contract conversion candidate was detected.",
                "",
            ]
        )
        return "\n".join(lines)

    for candidate in candidates:
        mini = candidate.get("mini_llm_execution") or {}
        report = mini.get("report_template") or {}
        dates = candidate.get("date_scope") or {}
        source_change_git = candidate.get("source_change_git") or {}
        lines.extend(
            [
                f"## {candidate.get('city')}",
                "",
                f"- branch: `{candidate.get('transition_branch')}`",
                f"- source_change_branch: `{source_change_git.get('branch_name')}`",
                f"- source_change_worktree: `{source_change_git.get('worktree_path')}`",
                f"- confirmation: `{candidate.get('confirmation_status')}`",
                f"- can_complete_remaining_conversion: `{report.get('can_complete_remaining_conversion')}`",
                f"- keep_quarantine_active: `{report.get('source_quarantine_should_remain_active')}`",
                f"- affected_dates: `{dates.get('affected_market_start')}` to `{dates.get('affected_market_end')}`",
                f"- affected_metrics: `{candidate.get('affected_metrics')}`",
                f"- next_safe_action: `{report.get('next_safe_action')}`",
                "",
                "### Blocking Reasons",
                "",
            ]
        )
        blockers = report.get("blocking_reasons") or []
        if blockers:
            lines.extend(f"- {item}" for item in blockers)
        else:
            lines.append("- none")
        lines.extend(["", "### Allowed Commands", ""])
        if source_change_git.get("create_command"):
            lines.append(
                "- `"
                + " ".join(str(part) for part in source_change_git["create_command"])
                + "`"
            )
        commands = [
            step.get("allowed_command")
            for step in mini.get("step_protocol", [])
            if step.get("allowed_command")
        ]
        if commands:
            for command in commands:
                lines.append("- `" + " ".join(str(part) for part in command) + "`")
        else:
            lines.append("- none")
        lines.extend(["", "### Forbidden Actions", ""])
        for action in mini.get("forbidden_actions", []):
            lines.append(f"- {action}")
        locator = mini.get("workspace_locator") or {}
        lines.extend(["", "### Key File Locations", ""])
        for item in locator.get("code_navigation", []):
            lines.append(
                f"- `{item.get('path')}` ({item.get('access')}): {item.get('purpose')}"
            )
        safety = mini.get("safe_execution_contract") or {}
        lines.extend(["", "### Current Write Scope", ""])
        for pattern in safety.get("allowed_write_globs_current_phase", []):
            lines.append(f"- allowed: `{pattern}`")
        for pattern in safety.get("forbidden_write_globs_current_phase", []):
            lines.append(f"- forbidden: `{pattern}`")
        lines.extend(["", "### Evidence Manifest", ""])
        for key, value in (mini.get("evidence_manifest") or {}).items():
            lines.append(
                f"- `{key}`: `{value.get('status')}` -> `{value.get('expected_artifact')}`"
            )
        lines.append("")
    return "\n".join(lines)


def write_mini_report(receipt: dict[str, Any], path: Path | None, receipt_dir: Path) -> Path:
    report_path = path or (receipt_dir / f"{receipt['run_id']}{DEFAULT_MINI_REPORT_SUFFIX}")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = report_path.with_name(f".{report_path.name}.tmp")
    tmp_path.write_text(render_mini_report(receipt) + "\n", encoding="utf-8")
    tmp_path.replace(report_path)
    receipt["mini_report_path"] = str(report_path)
    return report_path


@contextlib.contextmanager
def _exclusive_cron_lock(path: Path, *, enabled: bool = True):
    if not enabled:
        yield {"enabled": False, "status": "disabled", "path": str(path)}
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl  # type: ignore

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"source auto-conversion cron lock is already held: {path}") from exc
        except ImportError:
            # fcntl is available on the target macOS/Linux runtime. If it is
            # missing, fail closed rather than allowing concurrent writers.
            raise RuntimeError("fcntl unavailable; refusing source auto-conversion without a cron lock")

        lock_state = {
            "enabled": True,
            "status": "held",
            "path": str(path),
            "pid": os.getpid(),
            "acquired_at_utc": _utcnow().isoformat(),
        }
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(json.dumps(lock_state, sort_keys=True))
        lock_file.flush()
        yield lock_state
    finally:
        try:
            import fcntl  # type: ignore

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_file.close()


def _discord_title(status: str) -> str:
    if status == "noop":
        return "Source Contract Watch: no conversion"
    if status == "applied":
        return "Source Contract Conversion Applied"
    if status == "planned":
        return "Source Contract Conversion Plan Ready"
    if status == "blocked":
        return "Source Contract Conversion Blocked"
    return "Source Contract Auto-Conversion Failed"


def _discord_color(status: str) -> int:
    if status == "noop":
        return 0x3498DB
    if status == "applied":
        return 0x2ECC71
    if status == "planned":
        return 0xFFA500
    if status == "blocked":
        return 0xFFAA00
    return 0xFF0000


def _discord_body(receipt: dict[str, Any]) -> str:
    lines = [
        f"status: `{receipt.get('status')}`",
        f"run: `{receipt.get('run_id')}`",
        f"receipt: `{receipt.get('receipt_path', '<pending>')}`",
    ]
    if receipt.get("mini_report_path"):
        lines.append(f"mini report: `{receipt.get('mini_report_path')}`")
    for candidate in receipt.get("candidates", [])[:6]:
        source = candidate.get("source_contract") or {}
        dates = candidate.get("date_scope") or {}
        lines.extend(
            [
                "",
                f"city: **{candidate.get('city')}** branch=`{candidate.get('transition_branch')}` confirmation=`{candidate.get('confirmation_status')}`",
                f"from: `{source.get('from_source_families')}/{source.get('from_station_ids')}`",
                f"to: `{source.get('to_source_families')}/{source.get('to_station_ids')}`",
                f"dates: `{dates.get('affected_market_start')}` -> `{dates.get('affected_market_end')}` metrics=`{candidate.get('affected_metrics')}`",
                f"events: `{candidate.get('event_ids')}`",
            ]
        )
        blockers = candidate.get("threshold_blockers") or []
        gaps = candidate.get("runtime_gaps_before_apply") or []
        if blockers:
            lines.append("blockers: " + "; ".join(f"`{item}`" for item in blockers[:4]))
        if gaps:
            lines.append("runtime gaps: " + "; ".join(f"`{item}`" for item in gaps[:4]))
    if not receipt.get("candidates"):
        lines.append("No ALERT source-contract candidate was found.")
    body = "\n".join(lines)
    if len(body) > 3900:
        return body[:3850] + "\n...truncated; read receipt JSON for full details."
    return body


def send_discord_notification(receipt: dict[str, Any], *, notify_noop: bool = False) -> dict[str, Any]:
    if receipt.get("status") == "noop" and not notify_noop:
        return {"attempted": False, "sent": False, "status": "not_requested_for_noop"}
    try:
        from src.riskguard import discord_alerts

        if discord_alerts._resolve_webhook() is None:  # existing public behavior is silent skip.
            return {"attempted": True, "sent": False, "status": "skipped_no_webhook"}
        sent = discord_alerts._send_embed(
            "warning",
            _discord_title(str(receipt.get("status") or "failed")),
            _discord_body(receipt),
            color=_discord_color(str(receipt.get("status") or "failed")),
        )
        return {"attempted": True, "sent": bool(sent), "status": "sent" if sent else "send_failed"}
    except Exception as exc:  # pragma: no cover - defensive cron boundary
        return {"attempted": True, "sent": False, "status": "error", "error": str(exc)}


def _load_events(args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    if args.fixture:
        return watch_source_contract.load_fixture(args.fixture), "FIXTURE"
    return watch_source_contract.fetch_active_events()


def _data_unavailable_report(authority: str, *, city: str | None = None) -> dict[str, Any]:
    return {
        "status": "DATA_UNAVAILABLE",
        "checked_at_utc": _utcnow().isoformat(),
        "authority": authority,
        "event_count": 0,
        "checked_event_count": 0,
        "skipped_non_temperature": 0,
        "skipped_unconfigured": 0,
        "city_filter": city,
        "summary": {"OK": 0, "WARN": 0, "ALERT": 0, "DATA_UNAVAILABLE": 1},
        "events": [],
        "next_actions": ["Do not rely on source monitor output until Gamma fetch recovers."],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", help="Limit checks to one configured city name")
    parser.add_argument("--fixture", type=Path, help="Read Gamma events from JSON fixture")
    parser.add_argument("--include-unconfigured", action="store_true")
    parser.add_argument("--report-only", action="store_true", help="Do not write source quarantine")
    parser.add_argument("--quarantine-path", type=Path, help="Override source quarantine state path")
    parser.add_argument("--receipt-dir", type=Path, default=DEFAULT_RECEIPT_DIR)
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--no-lock", action="store_true", help="Disable cron lock; intended only for tests")
    parser.add_argument("--run-id", help="Stable run id for tests or external orchestration")
    parser.add_argument("--today", type=date.fromisoformat, help="Override UTC today YYYY-MM-DD")
    parser.add_argument("--history-days", type=int, default=DEFAULT_HISTORY_DAYS)
    parser.add_argument("--min-alert-markets", type=int, default=DEFAULT_MIN_ALERT_MARKETS)
    parser.add_argument("--min-target-dates", type=int, default=DEFAULT_MIN_TARGET_DATES)
    parser.add_argument("--discord", action="store_true", help="Send Discord notification for non-noop runs")
    parser.add_argument("--discord-required", action="store_true", help="Treat failed Discord delivery for non-noop runs as controller failure")
    parser.add_argument("--notify-noop", action="store_true", help="Also send Discord no-candidate summaries")
    parser.add_argument("--write-mini-report", action="store_true", help="Write a deterministic mini-model execution report")
    parser.add_argument("--mini-report-path", type=Path, help="Override mini execution report path")
    parser.add_argument(
        "--init-source-change-worktrees",
        action="store_true",
        help="Create missing per-candidate git worktrees/branches from source_change_git metadata.",
    )
    parser.add_argument(
        "--execute-apply",
        action="store_true",
        help="Execute deterministic same-provider WU conversion steps after receipt planning.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Required with --execute-apply to authorize config and scoped DB writes.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_WORLD_DB_PATH,
        help="World DB path for scoped backfill/rebuild/refit apply.",
    )
    parser.add_argument(
        "--config-path",
        type=Path,
        default=DEFAULT_CITY_CONFIG_PATH,
        help="City config path to update under --execute-apply.",
    )
    parser.add_argument(
        "--source-validity-path",
        type=Path,
        default=DEFAULT_SOURCE_VALIDITY_PATH,
        help="current_source_validity.md path to append under --execute-apply.",
    )
    parser.add_argument(
        "--station-metadata-path",
        type=Path,
        help="Optional JSON station metadata override map keyed by ICAO.",
    )
    parser.add_argument(
        "--evidence-root-base",
        type=Path,
        default=DEFAULT_EVIDENCE_BASE,
        help="Base directory for apply evidence artifacts.",
    )
    parser.add_argument(
        "--no-station-metadata-network",
        action="store_true",
        help="Disable AviationWeather station metadata lookup; override/static metadata only.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON receipt")
    return parser


def render_text(receipt: dict[str, Any]) -> str:
    lines = [
        (
            "source-contract-auto-convert "
            f"status={receipt['status']} mode={receipt['mode']} "
            f"candidates={len(receipt.get('candidates', []))} "
            f"receipt={receipt.get('receipt_path')}"
        )
    ]
    for candidate in receipt.get("candidates", []):
        dates = candidate["date_scope"]
        lines.append(
            (
                f"- {candidate['city']} branch={candidate['transition_branch']} "
                f"confirmation={candidate['confirmation_status']} "
                f"dates={dates['affected_market_start']}..{dates['affected_market_end']} "
                f"metrics={candidate['affected_metrics']}"
            )
        )
        for blocker in candidate.get("threshold_blockers", []):
            lines.append(f"  blocker: {blocker}")
        for gap in candidate.get("runtime_gaps_before_apply", []):
            lines.append(f"  runtime-gap: {gap}")
    notification = receipt.get("notification") or {}
    lines.append(
        "notification: "
        f"attempted={notification.get('attempted')} sent={notification.get('sent')} "
        f"status={notification.get('status')}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    policy = RuntimePolicy(
        history_days=args.history_days,
        min_alert_markets=args.min_alert_markets,
        min_target_dates=args.min_target_dates,
        today=args.today or _utc_today(),
    )

    lock_state: dict[str, Any] = {
        "enabled": not args.no_lock,
        "status": "not_acquired",
        "path": str(args.lock_path),
    }
    try:
        with _exclusive_cron_lock(args.lock_path, enabled=not args.no_lock) as active_lock:
            lock_state = dict(active_lock)
            try:
                events, authority = _load_events(args)
                if authority in {"EMPTY_FALLBACK", "NEVER_FETCHED"}:
                    report = _data_unavailable_report(authority, city=args.city)
                    receipt = build_failure_receipt(
                        run_id=args.run_id,
                        policy=policy,
                        error=f"source watch data unavailable: {authority}",
                        report=report,
                    )
                else:
                    report = watch_source_contract.analyze_events(
                        events,
                        city=args.city,
                        include_unconfigured=args.include_unconfigured,
                        authority=authority,
                    )
                    quarantine_actions = []
                    if not args.report_only:
                        quarantine_actions = watch_source_contract.apply_source_quarantines(
                            report,
                            quarantine_path=args.quarantine_path,
                        )
                    receipt = build_receipt(
                        report,
                        policy=policy,
                        run_id=args.run_id,
                        quarantine_actions=quarantine_actions,
                    )
                    stamp_runtime_invocation(receipt, args)
                    if args.init_source_change_worktrees and receipt.get("candidates"):
                        init_source_change_worktrees(receipt)
                    if args.execute_apply and receipt.get("status") == "planned":
                        receipt = execute_apply(
                            receipt,
                            force=args.force,
                            db_path=args.db,
                            config_path=args.config_path,
                            source_validity_path=args.source_validity_path,
                            quarantine_path=args.quarantine_path,
                            fixture=args.fixture,
                            station_metadata_path=args.station_metadata_path,
                            allow_station_metadata_network=not args.no_station_metadata_network,
                            evidence_root_base=args.evidence_root_base,
                        )
            except Exception as exc:
                receipt = build_failure_receipt(
                    run_id=args.run_id,
                    policy=policy,
                    error=f"{type(exc).__name__}: {exc}",
                )
            receipt["cron_lock"] = dict(lock_state)
    except Exception as exc:
        receipt = build_failure_receipt(
            run_id=args.run_id,
            policy=policy,
            error=f"{type(exc).__name__}: {exc}",
        )
        lock_state["status"] = "failed"
        lock_state["error"] = f"{type(exc).__name__}: {exc}"
        receipt["cron_lock"] = lock_state

    receipt_path = write_receipt(receipt, args.receipt_dir)
    receipt["receipt_path"] = str(receipt_path)
    if args.write_mini_report or args.mini_report_path:
        write_mini_report(receipt, args.mini_report_path, args.receipt_dir)
        write_receipt(receipt, args.receipt_dir)
    if args.discord:
        receipt["notification"] = send_discord_notification(
            receipt,
            notify_noop=args.notify_noop,
        )
        write_receipt(receipt, args.receipt_dir)

    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(render_text(receipt))

    exit_code = STATUS_EXIT_CODES.get(str(receipt.get("status") or "failed"), 2)
    notification = receipt.get("notification") or {}
    if (
        args.discord
        and args.discord_required
        and receipt.get("status") != "noop"
        and not notification.get("sent")
    ):
        return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
