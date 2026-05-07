"""Script manifest checker family for topology_doctor."""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-24; last_reused=2026-04-24
# Purpose: Validate top-level script lifecycle, naming, and write-target metadata.
# Reuse: Inspect architecture/script_manifest.yaml + architecture/naming_conventions.yaml before changing script gates.

from __future__ import annotations

import re
from datetime import date
from fnmatch import fnmatch
from typing import Any


SQL_MUTATION_PATTERN = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE)\b", re.IGNORECASE)
FILE_WRITE_PATTERN = re.compile(r"(\.write_text\(|open\([^)]*['\"]w|json\.dump\()", re.IGNORECASE)
CANONICAL_WRITE_HELPERS = (
    "append_many_and_project",
    "log_trade_entry",
    "log_settlement_event",
    "log_shadow_signal",
    "store_artifact",
)


def top_level_scripts(api: Any) -> set[str]:
    return {
        path.name
        for path in (api.ROOT / "scripts").iterdir()
        if path.is_file() and path.suffix in {".py", ".sh"}
    }


def effective_script_entry(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    scripts = manifest.get("scripts") or {}
    entry = dict(scripts.get(name) or {})
    defaults = dict((manifest.get("class_defaults") or {}).get(entry.get("class"), {}))
    return {**defaults, **entry}


def metadata_missing(api: Any, value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in api.EMPTY_METADATA_VALUES
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


def long_lived_script_name_allowed(api: Any, manifest: dict[str, Any], name: str) -> bool:
    conventions = api.load_naming_conventions() if api.NAMING_CONVENTIONS_PATH.exists() else {}
    naming = (
        (((conventions.get("file_naming") or {}).get("scripts") or {}).get("long_lived") or {})
        or manifest.get("long_lived_naming")
        or {}
    )
    prefixes = tuple(naming.get("allowed_prefixes") or ())
    exceptions = set((naming.get("exceptions") or {}).keys())
    return name.startswith(prefixes) or name in exceptions


def write_target_allowed(target: str, allowed: set[str]) -> bool:
    return any(target == pattern or fnmatch(target, pattern) for pattern in allowed)


def parse_delete_by(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_script_lifecycle(
    api: Any,
    manifest: dict[str, Any],
    name: str,
    effective: dict[str, Any],
) -> list[Any]:
    rel = f"scripts/{name}"
    issues: list[Any] = []
    lifecycle = effective.get("lifecycle")
    allowed_lifecycles = set(
        manifest.get("allowed_lifecycles")
        or {"long_lived", "packet_ephemeral", "promotion_candidate", "deprecated_fail_closed"}
    )

    if lifecycle not in allowed_lifecycles:
        return [api._issue("script_lifecycle_invalid", rel, f"invalid lifecycle {lifecycle!r}")]

    if effective.get("status") == "deprecated" and lifecycle != "deprecated_fail_closed":
        issues.append(
            api._issue(
                "script_deprecated_not_fail_closed",
                rel,
                "deprecated scripts must use lifecycle=deprecated_fail_closed",
            )
        )

    if lifecycle == "long_lived":
        for field in ("reuse_when", "do_not_use_when", "canonical_command", "delete_policy"):
            if metadata_missing(api, effective.get(field)):
                issues.append(api._issue("script_long_lived_metadata_missing", rel, f"missing {field}"))
        if effective.get("delete_policy") == "delete_on_packet_close":
            issues.append(
                api._issue(
                    "script_long_lived_delete_policy_invalid",
                    rel,
                    "long-lived scripts must not use delete_on_packet_close",
                )
            )
        if api.ONE_OFF_SCRIPT_NAME_PATTERN.search(name):
            issues.append(
                api._issue(
                    "script_long_lived_one_off_name",
                    rel,
                    "task/probe/scratch names must be packet_ephemeral or renamed before promotion",
                )
            )
        if not long_lived_script_name_allowed(api, manifest, name):
            issues.append(
                api._issue_with_admission_severity(
                    "script_long_lived_bad_name",
                    rel,
                    "long-lived script name must use an allowed prefix or a documented naming exception",
                )
            )

    if lifecycle == "packet_ephemeral":
        if not api.EPHEMERAL_SCRIPT_NAME_PATTERN.fullmatch(name):
            issues.append(
                api._issue(
                    "script_ephemeral_bad_name",
                    rel,
                    "packet-ephemeral scripts must use task_YYYY-MM-DD_<purpose> naming",
                )
            )
        for field in ("owner_packet", "created_for"):
            if metadata_missing(api, effective.get(field)):
                issues.append(api._issue("script_ephemeral_metadata_missing", rel, f"missing {field}"))
        delete_by_raw = effective.get("delete_by")
        delete_by = parse_delete_by(delete_by_raw)
        if not delete_by:
            issues.append(
                api._issue(
                    "script_ephemeral_delete_policy_missing",
                    rel,
                    "packet-ephemeral scripts need delete_by=YYYY-MM-DD",
                )
            )
        if delete_by_raw and not delete_by:
            issues.append(
                api._issue(
                    "script_ephemeral_delete_by_invalid",
                    rel,
                    "delete_by must be YYYY-MM-DD",
                )
            )
        if delete_by and delete_by < date.today():
            issues.append(
                api._issue(
                    "script_ephemeral_expired",
                    rel,
                    "packet-ephemeral script is past delete_by and must be deleted or promoted",
                )
            )
        if effective.get("delete_policy") == "retain_until_superseded":
            issues.append(
                api._issue(
                    "script_ephemeral_delete_policy_invalid",
                    rel,
                    "packet-ephemeral scripts must not retain_until_superseded",
                )
            )

    if lifecycle == "promotion_candidate":
        for field in ("owner_packet", "created_for", "promotion_deadline", "promotion_decision"):
            if metadata_missing(api, effective.get(field)):
                issues.append(api._issue("script_promotion_candidate_metadata_missing", rel, f"missing {field}"))
        promotion_deadline = parse_delete_by(effective.get("promotion_deadline"))
        if effective.get("promotion_deadline") and not promotion_deadline:
            issues.append(
                api._issue(
                    "script_promotion_candidate_deadline_invalid",
                    rel,
                    "promotion_deadline must be YYYY-MM-DD",
                )
            )
        if promotion_deadline and promotion_deadline < date.today():
            issues.append(
                api._issue(
                    "script_promotion_candidate_expired",
                    rel,
                    "promotion candidate is past promotion_deadline and must be promoted or deleted",
                )
            )

    if lifecycle == "deprecated_fail_closed":
        if effective.get("status") != "deprecated":
            issues.append(
                api._issue(
                    "script_deprecated_lifecycle_status_mismatch",
                    rel,
                    "deprecated_fail_closed scripts must carry status=deprecated",
                )
            )
        if effective.get("fail_closed") is not True:
            issues.append(
                api._issue(
                    "script_deprecated_not_fail_closed",
                    rel,
                    "deprecated_fail_closed scripts must set fail_closed=true",
                )
            )
        if not any(effective.get(field) for field in ("reason", "replacement", "archive_reason")):
            issues.append(
                api._issue(
                    "script_deprecated_missing_disposition",
                    rel,
                    "deprecated scripts need reason, replacement, or archive_reason",
                )
            )
        if effective.get("canonical_command") != "DO_NOT_RUN":
            issues.append(
                api._issue(
                    "script_deprecated_runnable_command",
                    rel,
                    "deprecated scripts must use canonical_command=DO_NOT_RUN",
                )
            )

    return issues


def run_scripts(api: Any) -> Any:
    manifest = api.load_script_manifest()
    actual = api._top_level_scripts()
    declared = set((manifest.get("scripts") or {}).keys())
    required = manifest.get("required_effective_fields") or []
    diagnostic_allowed = set(manifest.get("diagnostic_allowed_write_targets") or [])
    canonical_targets = set(manifest.get("canonical_db_targets") or [])
    issues: list[Any] = []

    for name in sorted(actual - declared):
        issues.append(api._issue("script_manifest_missing", f"scripts/{name}", "top-level script has no manifest entry"))
    for name in sorted(declared - actual):
        issues.append(api._issue("script_manifest_stale", f"scripts/{name}", "manifest entry has no top-level script"))

    for name in sorted(actual & declared):
        effective = api._effective_script_entry(manifest, name)
        rel = f"scripts/{name}"
        for field in required:
            if field not in effective:
                issues.append(api._issue("script_manifest_required_field_missing", rel, f"missing {field}"))
        issues.extend(api._check_script_lifecycle(manifest, name, effective))

        write_targets = set(effective.get("write_targets") or [])
        authority_scope = str(effective.get("authority_scope", ""))
        is_diagnostic_scope = authority_scope.startswith(
            "diagnostic_non_promotion"
        ) or authority_scope.startswith("report_artifact_non_promotion")
        if is_diagnostic_scope:
            forbidden_writes = sorted(
                target for target in write_targets if not write_target_allowed(target, diagnostic_allowed)
            )
            if forbidden_writes:
                issues.append(
                    api._issue_with_admission_severity(
                        "script_diagnostic_forbidden_write_target",
                        rel,
                        f"diagnostic writes forbidden targets {forbidden_writes}",
                    )
                )
            script_path = api.ROOT / rel
            if not script_path.exists():
                continue
            text = script_path.read_text(encoding="utf-8", errors="ignore")
            if any(helper in text for helper in CANONICAL_WRITE_HELPERS):
                issues.append(
                    api._issue(
                        "script_diagnostic_imports_canonical_write_helper",
                        rel,
                        "diagnostic script references canonical write helper",
                    )
                )
            if SQL_MUTATION_PATTERN.search(text) and write_targets - {"state/zeus_backtest.db", "stdout", "temp"}:
                issues.append(
                    api._issue(
                        "script_diagnostic_mutates_canonical_surface",
                        rel,
                        "diagnostic script contains SQL mutation outside diagnostic targets",
                    )
                )
            if FILE_WRITE_PATTERN.search(text) and write_targets <= {"stdout"}:
                issues.append(
                    api._issue(
                        "script_diagnostic_untracked_file_write",
                        rel,
                        "diagnostic script appears to write files but manifest declares stdout only",
                    )
                )

        if effective.get("dangerous_if_run"):
            fail_closed = effective.get("status") == "deprecated" and effective.get("fail_closed")
            if not fail_closed:
                apply_flag = effective.get("apply_flag")
                text = (api.ROOT / rel).read_text(encoding="utf-8", errors="ignore")
                if not apply_flag:
                    issues.append(api._issue("script_dangerous_missing_apply_flag", rel, "dangerous script needs explicit apply/no-dry-run flag"))
                elif apply_flag == "implicit":
                    if not effective.get("unguarded_write_rationale"):
                        issues.append(
                            api._issue(
                                "script_dangerous_implicit_apply_without_rationale",
                                rel,
                                "implicit write must carry unguarded_write_rationale",
                            )
                        )
                elif apply_flag != "explicit_import_only" and str(apply_flag) not in text:
                    issues.append(
                        api._issue(
                            "script_dangerous_apply_flag_not_in_source",
                            rel,
                            f"declared apply flag {apply_flag!r} not found in source",
                        )
                    )
                if not effective.get("target_db"):
                    issues.append(api._issue("script_dangerous_missing_target_db", rel, "dangerous script needs explicit target DB metadata"))

        if write_targets & canonical_targets and is_diagnostic_scope:
            issues.append(
                api._issue(
                    "script_diagnostic_writes_canonical_db",
                    rel,
                    f"diagnostic write targets canonical DB {sorted(write_targets & canonical_targets)}",
                )
            )

    return api.StrictResult(ok=not issues, issues=issues)
