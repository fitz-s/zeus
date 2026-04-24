"""Data-rebuild topology checker family for topology_doctor."""

from __future__ import annotations

from typing import Any


def protected_path_exists(api: Any, path: str) -> bool:
    if (api.ROOT / path).exists():
        return True
    if path.startswith("state/"):
        declared = api._declared_paths(api.load_topology().get("state_surfaces", []))
        return api._path_declared(path, declared)
    return False


def run_data_rebuild(api: Any) -> Any:
    topology = api.load_data_rebuild_topology()
    issues: list[Any] = []
    criteria = topology.get("criteria") or {}
    required = set((topology.get("live_math_certification") or {}).get("required_before_allowed") or [])
    actual = set(criteria)

    for criterion in sorted(required - actual):
        issues.append(api._issue("data_rebuild_criterion_missing", criterion, "required certification criterion missing"))
    for name, criterion in criteria.items():
        if not criterion.get("status"):
            issues.append(api._issue("data_rebuild_criterion_incomplete", name, "missing status"))
        if not criterion.get("source"):
            issues.append(api._issue("data_rebuild_criterion_incomplete", name, "missing source"))
        if not criterion.get("certification_gate"):
            issues.append(api._issue("data_rebuild_criterion_incomplete", name, "missing certification_gate"))
        if "blocks_live_math_certification" not in criterion:
            issues.append(api._issue("data_rebuild_criterion_incomplete", name, "missing blocks_live_math_certification"))
        for path in criterion.get("protects", []):
            if not protected_path_exists(api, path):
                issues.append(api._issue("data_rebuild_protects_missing", path, f"{name} protects missing path"))
        for path in criterion.get("required_tests", []):
            if not (api.ROOT / path).exists():
                issues.append(api._issue("data_rebuild_required_test_missing", path, f"{name} references missing test"))

    certification = topology.get("live_math_certification") or {}
    if "allowed" not in certification:
        issues.append(api._issue("data_rebuild_certification_allowed_missing", "live_math_certification", "allowed key is required"))
    elif not isinstance(certification.get("allowed"), bool):
        issues.append(api._issue("data_rebuild_certification_allowed_invalid", "live_math_certification", "allowed must be boolean"))
    elif certification.get("allowed") is not False:
        issues.append(api._issue("data_rebuild_certification_allowed_unsafe", "live_math_certification", "Packet 8 topology must not allow live math certification"))

    for criterion in sorted(required):
        if not (criteria.get(criterion) or {}).get("blocks_live_math_certification"):
            issues.append(
                api._issue(
                    "data_rebuild_required_criterion_not_blocking",
                    criterion,
                    "required_before_allowed criterion must block live math certification",
                )
            )

    if certification.get("allowed") is True:
        blockers = [
            name
            for name, criterion in criteria.items()
            if criterion.get("blocks_live_math_certification")
            and criterion.get("status") != "certified"
        ]
        if blockers:
            issues.append(
                api._issue(
                    "data_rebuild_live_math_certification_unsafe",
                    "live_math_certification",
                    f"cannot allow live math certification while blockers remain: {sorted(blockers)}",
                )
            )

    row_contract = topology.get("rebuilt_row_contract", {}).get("tables") or {}
    for table, spec in row_contract.items():
        if not spec.get("authority_required"):
            issues.append(api._issue("data_rebuild_row_contract_missing_authority", table, "authority label required"))
        if not spec.get("provenance_required"):
            issues.append(api._issue("data_rebuild_row_contract_missing_provenance", table, "provenance required"))
        if not spec.get("required_fields"):
            issues.append(api._issue("data_rebuild_row_contract_missing_fields", table, "required_fields must be non-empty"))
        producer = spec.get("producer_contract") or spec.get("producer_script")
        if not producer:
            issues.append(api._issue("data_rebuild_row_contract_missing_producer", table, "producer_contract or producer_script required"))
        elif not (api.ROOT / str(producer)).exists():
            issues.append(api._issue("data_rebuild_row_contract_missing_producer", str(producer), f"producer for {table} missing"))

    replay_rule = topology.get("replay_coverage_rule") or {}
    if replay_rule.get("wu_settlement_sample_is_strategy_coverage") is not False:
        issues.append(
            api._issue(
                "data_rebuild_replay_coverage_unsafe",
                "replay_coverage_rule",
                "WU settlement sample must not be treated as strategy replay coverage",
            )
        )
    for item in (
        "wu_settlement_value",
        "point_in_time_forecast_reference",
        "vector_compatible_p_raw_json",
        "parseable_typed_bin_labels",
        "market_price_linkage",
    ):
        if item not in (replay_rule.get("required_for_strategy_replay_coverage") or []):
            issues.append(api._issue("data_rebuild_replay_coverage_incomplete", item, "required replay coverage prerequisite missing"))
    if "N/A" not in str(replay_rule.get("p_and_l_without_market_price", "")):
        issues.append(api._issue("data_rebuild_replay_pnl_unsafe", "p_and_l_without_market_price", "missing N/A rule"))

    diagnostic = topology.get("diagnostic_non_promotion") or {}
    if diagnostic.get("authority_scope") != "diagnostic_non_promotion":
        issues.append(api._issue("data_rebuild_non_promotion_missing", "diagnostic_non_promotion", "authority_scope must be diagnostic_non_promotion"))
    for target in (
        "state/zeus_trades.db",
        "state/zeus-world.db",
        "live strategy thresholds",
        "calibration model activation",
    ):
        if target not in (diagnostic.get("forbidden_promotions") or []):
            issues.append(api._issue("data_rebuild_non_promotion_incomplete", target, "canonical target missing from forbidden promotions"))

    return api.StrictResult(ok=not issues, issues=issues)
