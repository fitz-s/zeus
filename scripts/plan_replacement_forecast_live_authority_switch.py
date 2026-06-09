#!/usr/bin/env python3
"""Plan or apply the replacement forecast live-authority switch."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.replacement_forecast_config_switch import (  # noqa: E402
    apply_replacement_forecast_live_authority_config_switch,
    build_replacement_forecast_live_authority_config_switch_plan,
)
from src.data.replacement_forecast_go_live_report import (  # noqa: E402
    build_replacement_forecast_go_live_readiness_from_payload,
    replacement_forecast_capital_objective_evidence_from_payload,
    replacement_forecast_before_after_rows_from_csv,
    replacement_forecast_payload_with_current_live_switch_inventory,
    replacement_forecast_promotion_evidence_from_payload,
)
from src.data.replacement_forecast_runtime_policy import (  # noqa: E402
    DIRECTION_FLIP_FLAG,
    KELLY_INCREASE_FLAG,
    SHADOW_FLAG,
    TRADE_AUTHORITY_FLAG,
    VETO_FLAG,
)


TARGET_LIVE_FLAGS = {
    SHADOW_FLAG: True,
    VETO_FLAG: True,
    TRADE_AUTHORITY_FLAG: True,
    KELLY_INCREASE_FLAG: False,
    DIRECTION_FLIP_FLAG: False,
}


def _promotion_evidence_summary(evidence: object | None) -> dict[str, object] | None:
    if evidence is None:
        return None
    blockers = evidence.blocking_reason_codes()
    return {
        "official_days": evidence.official_days,
        "official_rows": evidence.official_rows,
        "after_cost_pnl": evidence.after_cost_pnl,
        "q_lcb_coverage": evidence.q_lcb_coverage,
        "anti_lookahead_violations": evidence.anti_lookahead_violations,
        "source_availability_violations": evidence.source_availability_violations,
        "unresolved_regression_clusters": evidence.unresolved_regression_clusters,
        "same_clob_replay_passed": evidence.same_clob_replay_passed,
        "same_clob_replay_scored_rows": evidence.same_clob_replay_scored_rows,
        "same_clob_replay_blocked_rows": evidence.same_clob_replay_blocked_rows,
        "fee_depth_fill_evidence_passed": evidence.fee_depth_fill_evidence_passed,
        "unit_pnl_only": evidence.unit_pnl_only,
        "nested_walk_forward_passed": evidence.nested_walk_forward_passed,
        "nested_holdout_brier": evidence.nested_holdout_brier,
        "nested_holdout_log_loss": evidence.nested_holdout_log_loss,
        "nested_selected_anchor_weight": evidence.nested_selected_anchor_weight,
        "nested_selected_anchor_sigma_c": evidence.nested_selected_anchor_sigma_c,
        "nested_guardrail_bucket_count": evidence.nested_guardrail_bucket_count,
        "nested_guardrail_bucket_min_rows": evidence.nested_guardrail_bucket_min_rows,
        "product_specific_refit_passed": evidence.product_specific_refit_passed,
        "promotion_allowed": not blockers,
        "blocking_reason_codes": list(blockers),
    }


def _load_payload(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("go-live payload must decode to an object")
    return payload


def _payload_declared_before_after_csv(payload: Mapping[str, object], input_path: Path) -> Path | None:
    capital_replay = payload.get("capital_replay")
    if not isinstance(capital_replay, Mapping):
        return None
    raw_path = capital_replay.get("before_after_rows_csv")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        return candidate
    for base in (input_path.parent, *input_path.parent.parents, Path.cwd(), ROOT):
        resolved = base / candidate
        if resolved.exists():
            return resolved
    return input_path.parent / candidate


def _inject_before_after_csv(payload: dict[str, object], input_path: Path, csv_path: Path | None) -> dict[str, object]:
    before_after_rows_csv = csv_path
    if before_after_rows_csv is None:
        before_after_rows_csv = _payload_declared_before_after_csv(payload, input_path)
    if before_after_rows_csv is None:
        return payload
    rows = replacement_forecast_before_after_rows_from_csv(before_after_rows_csv)
    payload["before_after_rows"] = [
        {
            "official_date": row.official_date,
            "city": row.city,
            "temperature_metric": row.temperature_metric,
            "guardrail_bucket": row.guardrail_bucket,
            "baseline_brier": row.baseline_brier,
            "replacement_brier": row.replacement_brier,
            "baseline_log_loss": row.baseline_log_loss,
            "replacement_log_loss": row.replacement_log_loss,
            "baseline_after_cost_pnl": row.baseline_after_cost_pnl,
            "replacement_after_cost_pnl": row.replacement_after_cost_pnl,
            "truth_authority": row.truth_authority,
            "replay_status": row.replay_status,
        }
        for row in rows
    ]
    return payload


def _payload_for_live_authority_gate(payload: Mapping[str, object], *, live_root: Path | None) -> dict[str, object]:
    candidate = dict(payload)
    if live_root is not None:
        candidate = replacement_forecast_payload_with_current_live_switch_inventory(candidate, live_root)
    runtime_flags = dict(candidate.get("runtime_flags") or {})
    runtime_flags.update(TARGET_LIVE_FLAGS)
    candidate["runtime_flags"] = runtime_flags
    return candidate


def _backup_settings(settings_path: Path, backup_dir: Path) -> Path | None:
    if not settings_path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / "config" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(settings_path, target)
    return target


def _rooted_config_path(root: Path, raw_path: object, fallback: str) -> Path:
    raw = str(raw_path or fallback)
    path = Path(raw)
    return path if path.is_absolute() else root / path


def _write_runtime_promotion_evidence_payload(
    *,
    root: Path,
    settings_payload: Mapping[str, object],
    payload: Mapping[str, object],
) -> Path:
    shadow_config = settings_payload.get("replacement_forecast_shadow")
    shadow_map = shadow_config if isinstance(shadow_config, Mapping) else {}
    path = _rooted_config_path(
        root,
        shadow_map.get("promotion_evidence_path"),
        "state/replacement_forecast_shadow/promotion_evidence.json",
    )
    evidence_payload = dict(payload)
    refit_handoff = evidence_payload.get("refit_handoff")
    promotion_evidence = evidence_payload.get("promotion_evidence")
    if isinstance(refit_handoff, Mapping) and isinstance(promotion_evidence, Mapping):
        refit_decision = refit_handoff.get("refit_decision")
        if isinstance(refit_decision, Mapping) and refit_decision.get("product_specific_training_allowed") is True:
            promotion_map = dict(promotion_evidence)
            promotion_map["product_specific_refit_passed"] = True
            evidence_payload["promotion_evidence"] = promotion_map
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _base_switch_blockers_after_planned_config_apply(readiness: object) -> list[str]:
    """Return base-switch blockers that would remain after applying config.

    The live-authority planner is itself responsible for applying the config
    patch. Treating REPLACEMENT_GO_LIVE_CONFIG_PATCH_NOT_APPLIED as a blocker
    before apply creates a circular gate: the switch is blocked because it has
    not already been switched.
    """

    blockers = getattr(readiness, "blockers", {})
    if not isinstance(blockers, Mapping):
        return ["REPLACEMENT_CAPITAL_OBJECTIVE_BASE_SWITCH_BLOCKERS_UNREADABLE"]
    remaining: list[str] = []
    base_categories = {
        "config_switch",
        "live_switch",
        "live_dry_run",
        "current_facts",
        "rollback",
        "switch_decision",
        "refit",
        "runtime_policy",
    }
    for category, raw_reasons in blockers.items():
        if str(category) not in base_categories:
            continue
        reasons = [str(item) for item in raw_reasons] if isinstance(raw_reasons, list) else [str(raw_reasons)]
        if category == "config_switch":
            reasons = [
                reason
                for reason in reasons
                if reason != "REPLACEMENT_GO_LIVE_CONFIG_PATCH_NOT_APPLIED"
            ]
        remaining.extend(reasons)
    return list(dict.fromkeys(remaining))


def plan_replacement_forecast_live_authority_switch(
    *,
    live_root: Path,
    go_live_payload_json: Path,
    before_after_rows_csv: Path | None = None,
    backup_dir: Path | None = None,
    allow_capital_objective_switch: bool = False,
    apply: bool = False,
) -> dict[str, object]:
    root = Path(live_root)
    settings_path = root / "config" / "settings.json"
    payload = _inject_before_after_csv(
        _load_payload(go_live_payload_json),
        go_live_payload_json,
        before_after_rows_csv,
    )
    payload = _payload_for_live_authority_gate(payload, live_root=root)
    capital_objective_evidence = replacement_forecast_capital_objective_evidence_from_payload(payload)
    readiness = build_replacement_forecast_go_live_readiness_from_payload(payload)
    promotion_evidence = replacement_forecast_promotion_evidence_from_payload(payload)
    reasons: list[str] = []
    if promotion_evidence is None:
        reasons.append("REPLACEMENT_LIVE_AUTHORITY_PROMOTION_EVIDENCE_MISSING")
    if not readiness.live_promotion_ready:
        reasons.append("REPLACEMENT_LIVE_AUTHORITY_GO_LIVE_GATE_BLOCKED")
    if readiness.switch_decision_status != "LIVE_AUTHORITY":
        reasons.append("REPLACEMENT_LIVE_AUTHORITY_SWITCH_DECISION_NOT_ADMITTED")
    if readiness.switch_can_increase_kelly or readiness.switch_can_flip_direction:
        reasons.append("REPLACEMENT_LIVE_AUTHORITY_UNEXPECTED_DANGEROUS_ESCALATION")

    strict_reasons = list(reasons)
    capital_objective_reasons: list[str] = []
    if allow_capital_objective_switch:
        capital_replay = payload.get("capital_replay")
        capital_replay_map = capital_replay if isinstance(capital_replay, Mapping) else {}
        coverage = capital_replay_map.get("coverage")
        coverage_map = coverage if isinstance(coverage, Mapping) else {}
        selected_label = str(capital_replay_map.get("selected_label") or "")
        if selected_label != "openmeteo_ecmwf_ifs9_aifs_sampled_2t_soft_anchor_w0.80_sigma3.00":
            capital_objective_reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_SELECTED_LABEL_MISMATCH")
        if str(capital_replay_map.get("status") or "") != "EMPIRICAL_WINNER":
            capital_objective_reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_REPLAY_NOT_EMPIRICAL_WINNER")
        if coverage_map.get("source_availability_observed") is not True:
            capital_objective_reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_SOURCE_AVAILABILITY_NOT_OBSERVED")
        try:
            if int(coverage_map.get("source_availability_violations") or 0) != 0:
                capital_objective_reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_SOURCE_AVAILABILITY_VIOLATIONS")
        except (TypeError, ValueError):
            capital_objective_reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_SOURCE_AVAILABILITY_INVALID")
        if capital_objective_evidence is None:
            capital_objective_reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_PROMOTION_EVIDENCE_MISSING")
        else:
            capital_objective_reasons.extend(capital_objective_evidence.blocking_reason_codes())
        if readiness.source_fact_status != "CURRENT_FOR_LIVE" or readiness.data_fact_status != "CURRENT_FOR_LIVE":
            capital_objective_reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_CURRENT_FACTS_NOT_LIVE")
        remaining_base_switch_blockers = _base_switch_blockers_after_planned_config_apply(readiness)
        if remaining_base_switch_blockers:
            capital_objective_reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_BASE_SWITCH_NOT_READY")
        if capital_objective_reasons:
            reasons.append("REPLACEMENT_CAPITAL_OBJECTIVE_SWITCH_BLOCKED")
        else:
            reasons = [
                reason
                for reason in reasons
                if reason
                not in {
                    "REPLACEMENT_LIVE_AUTHORITY_GO_LIVE_GATE_BLOCKED",
                    "REPLACEMENT_LIVE_AUTHORITY_SWITCH_DECISION_NOT_ADMITTED",
                }
            ]

    config_plan = None
    if promotion_evidence is not None:
        settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(settings_payload, Mapping):
            raise ValueError("settings JSON must decode to an object")
        config_plan = build_replacement_forecast_live_authority_config_switch_plan(
            settings_payload,
            promotion_evidence=promotion_evidence,
            capital_objective_evidence=capital_objective_evidence if allow_capital_objective_switch else None,
        )
        if not config_plan.ok and not allow_capital_objective_switch:
            reasons.append("REPLACEMENT_LIVE_AUTHORITY_CONFIG_PLAN_BLOCKED")

    generated_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    effective_backup_dir = (
        Path(backup_dir)
        if backup_dir is not None
        else root / "state" / "replacement_forecast_shadow" / "live_authority_switch_backups" / generated_at
    )
    backup_file = None
    promotion_evidence_file = None
    applied = False
    if apply and not reasons:
        if promotion_evidence is None:
            raise RuntimeError("promotion evidence unexpectedly missing")
        backup_file = _backup_settings(settings_path, effective_backup_dir)
        settings_payload_for_evidence = json.loads(settings_path.read_text(encoding="utf-8"))
        if not isinstance(settings_payload_for_evidence, dict):
            raise ValueError("settings JSON must decode to an object")
        if allow_capital_objective_switch:
            flags = settings_payload_for_evidence.setdefault("feature_flags", {})
            if not isinstance(flags, dict):
                raise ValueError("feature_flags must be an object")
            flags.update(TARGET_LIVE_FLAGS)
            shadow_config = settings_payload_for_evidence.setdefault("replacement_forecast_shadow", {})
            if not isinstance(shadow_config, dict):
                raise ValueError("replacement_forecast_shadow must be an object")
            shadow_config.setdefault(
                "promotion_evidence_path",
                "state/replacement_forecast_shadow/promotion_evidence.json",
            )
            promotion_evidence_file = _write_runtime_promotion_evidence_payload(
                root=root,
                settings_payload=settings_payload_for_evidence,
                payload=payload,
            )
            settings_path.write_text(json.dumps(settings_payload_for_evidence, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        else:
            config_plan = apply_replacement_forecast_live_authority_config_switch(
                settings_path,
                promotion_evidence=promotion_evidence,
            )
            settings_payload_after = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings_payload_after, dict):
                raise ValueError("settings JSON must decode to an object")
            promotion_evidence_file = _write_runtime_promotion_evidence_payload(
                root=root,
                settings_payload=settings_payload_after,
                payload=payload,
            )
        applied = True

    status = (
        "LIVE_AUTHORITY_SWITCH_APPLIED"
        if applied and not reasons
        else "LIVE_AUTHORITY_SWITCH_READY"
        if not apply and not reasons
        else "LIVE_AUTHORITY_SWITCH_BLOCKED"
    )
    admission_basis = "STRICT_PROMOTION_EVIDENCE"
    if allow_capital_objective_switch:
        admission_basis = "CAPITAL_OBJECTIVE_AFTER_COST_REPLAY"
    return {
        "schema_version": "replacement_forecast_live_authority_switch_receipt_v1",
        "status": status,
        "reason_codes": list(dict.fromkeys(reasons or ["REPLACEMENT_LIVE_AUTHORITY_SWITCH_READY"])),
        "admission_basis": admission_basis,
        "strict_reason_codes": list(dict.fromkeys(strict_reasons)),
        "capital_objective_reason_codes": list(dict.fromkeys(capital_objective_reasons)),
        "capital_objective_switch_allowed": bool(allow_capital_objective_switch and not capital_objective_reasons),
        "apply_requested": bool(apply),
        "applied": applied,
        "live_root": str(root),
        "settings_path": str(settings_path),
        "backup_dir": str(effective_backup_dir) if apply else None,
        "backup_settings": None if backup_file is None else str(backup_file),
        "promotion_evidence_path": None if promotion_evidence_file is None else str(promotion_evidence_file),
        "rollback_commands": [] if backup_file is None else [f"cp {backup_file} {settings_path}"],
        "go_live_status": readiness.status,
        "go_live_reason_codes": list(readiness.reason_codes),
        "promotion_evidence": _promotion_evidence_summary(promotion_evidence),
        "switch_decision_status": readiness.switch_decision_status,
        "switch_can_initiate_trade": readiness.switch_can_initiate_trade,
        "switch_can_increase_kelly": readiness.switch_can_increase_kelly,
        "switch_can_flip_direction": readiness.switch_can_flip_direction,
        "live_promotion_ready": readiness.live_promotion_ready,
        "config_switch": None if config_plan is None else config_plan.as_dict(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan/apply replacement forecast live-authority switch")
    parser.add_argument("--live-root", type=Path, default=ROOT)
    parser.add_argument("--go-live-payload-json", type=Path, required=True)
    parser.add_argument("--before-after-rows-csv", type=Path, default=None)
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--allow-capital-objective-switch", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receipt-json", type=Path, default=None)
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args(argv)
    try:
        receipt = plan_replacement_forecast_live_authority_switch(
            live_root=args.live_root,
            go_live_payload_json=args.go_live_payload_json,
            before_after_rows_csv=args.before_after_rows_csv,
            backup_dir=args.backup_dir,
            allow_capital_objective_switch=bool(args.allow_capital_objective_switch),
            apply=bool(args.apply),
        )
        if args.receipt_json is not None:
            args.receipt_json.parent.mkdir(parents=True, exist_ok=True)
            args.receipt_json.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "error_type": exc.__class__.__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    if args.stdout:
        print(json.dumps(receipt, sort_keys=True))
    else:
        print(f"{receipt['status']}: {','.join(receipt['reason_codes'])}")
    return 0 if receipt["status"] in {"LIVE_AUTHORITY_SWITCH_READY", "LIVE_AUTHORITY_SWITCH_APPLIED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
