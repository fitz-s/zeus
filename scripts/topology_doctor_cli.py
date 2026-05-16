"""CLI facade for scripts.topology_doctor.

Keep command parsing and rendering here; keep topology checks/builders in
topology_doctor.py until golden-output parity is strong enough for deeper
module extraction.
"""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-16; last_reused=2026-05-09
# Purpose: Parse topology_doctor CLI flags and render checker payloads.
# Reuse: Inspect topology_doctor.py facade exports before adding new CLI lanes.

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def build_parser(description: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--strict", action="store_true", help="Run strict topology checks")
    parser.add_argument("--schema", action="store_true", help="Run topology schema checks only")
    parser.add_argument("--global-health", action="store_true", help="Alias for --strict full-repo health checks")
    parser.add_argument("--docs", action="store_true", help="Run Packet 3 docs-mesh checks")
    parser.add_argument("--source", action="store_true", help="Run Packet 4 source-rationale checks")
    parser.add_argument("--tests", action="store_true", help="Run Packet 5 test topology checks")
    parser.add_argument("--scripts", action="store_true", help="Run Packet 6 script manifest checks")
    parser.add_argument("--data-rebuild", action="store_true", help="Run Packet 8 data/rebuild topology checks")
    parser.add_argument("--invariants", action="store_true", help="Emit invariant slice, optionally by --zone")
    parser.add_argument("--history-lore", action="store_true", help="Run historical lore card checks")
    parser.add_argument("--context-budget", action="store_true", help="Run context budget checks")
    parser.add_argument("--artifact-lifecycle", action="store_true", help="Run artifact lifecycle/classification checks")
    parser.add_argument("--work-record", action="store_true", help="Check that repo-changing work has a short work record")
    parser.add_argument("--change-receipts", action="store_true", help="Check high-risk route/change receipts")
    parser.add_argument("--ownership", action="store_true", help="Check manifest fact ownership and issue owner metadata")
    parser.add_argument("--current-state-receipt-bound", action="store_true", help="Check current_state receipt-bound pointer integrity")
    parser.add_argument("--agents-coherence", action="store_true", help="Check scoped AGENTS prose against machine maps")
    parser.add_argument("--idioms", action="store_true", help="Check intentional non-obvious code idiom registry")
    parser.add_argument("--self-check-coherence", action="store_true", help="Check zero-context self-check alignment with root navigation")
    parser.add_argument("--runtime-modes", action="store_true", help="Check discovery/runtime mode manifest and root visibility")
    parser.add_argument("--task-boot-profiles", action="store_true", help="Check semantic task boot profile manifest")
    parser.add_argument("--fatal-misreads", action="store_true", help="Check fatal semantic misread manifest")
    parser.add_argument("--city-truth-contract", action="store_true", help="Check stable city truth contract schema")
    parser.add_argument("--code-review-graph-protocol", action="store_true", help="Check two-stage Code Review Graph protocol")
    parser.add_argument("--reference-replacement", action="store_true", help="Check reference replacement matrix")
    parser.add_argument("--core-claims", action="store_true", help="Check proof-backed core claim registry")
    parser.add_argument("--naming-conventions", action="store_true", help="Check canonical file/function naming map")
    parser.add_argument("--freshness-metadata", action="store_true", help="Check changed scripts/tests for lifecycle freshness headers")
    parser.add_argument("--code-review-graph-status", action="store_true", help="Check local Code Review Graph cache freshness")
    parser.add_argument("--map-maintenance", action="store_true", help="Check companion registry updates for added/deleted files")
    parser.add_argument(
        "--map-maintenance-mode",
        choices=["advisory", "precommit", "closeout"],
        default="advisory",
        help="Map-maintenance severity mode",
    )
    parser.add_argument("--navigation", action="store_true", help="Run default navigation health and task digest")
    parser.add_argument("--preflight", action="store_true", help="Alias for --navigation --route-card-only; emit compact agent pre-edit route card")
    parser.add_argument("--route-card-only", action="store_true", help="With --navigation, emit only the first-screen route card")
    parser.add_argument("--strict-health", action="store_true", help="Make --navigation fail on any repo-health error")
    parser.add_argument(
        "--issues-scope",
        choices=["task", "all"],
        default="task",
        help=(
            "Pretty-print scope for --navigation issues. 'task' (default) shows only "
            "direct_blockers + admission; cross-task repo-health drift is summarized as a count. "
            "'all' shows every issue (legacy behavior). JSON payload is unaffected."
        ),
    )
    parser.add_argument("--planning-lock", action="store_true", help="Check whether changed files require planning evidence")
    parser.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        help=(
            "Files for --planning-lock/closeout-style gates; with --navigation, "
            "acts as a --files alias when --files is omitted"
        ),
    )
    parser.add_argument("--plan-evidence", default=None, help="Plan/current-state evidence path for --planning-lock")
    parser.add_argument("--work-record-path", default=None, help="Work record path for --work-record")
    parser.add_argument("--receipt-path", default=None, help="Receipt path for --change-receipts")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--summary-only", action="store_true", help="Emit issue counts by code instead of full issue list")
    parser.add_argument(
        "--issue-schema-version",
        choices=["1", "2"],
        default="1",
        help="Issue JSON schema version; v1 preserves legacy issue keys, v2 includes typed metadata",
    )
    parser.add_argument("--task", default="", help="Task string for --navigation")
    parser.add_argument("--files", nargs="*", default=[], help="Files for --navigation")
    parser.add_argument("--intent", default=None, help="Typed digest profile id; overrides free-text profile scoring but not admission")
    parser.add_argument("--v-next-shadow", action="store_true", default=False, help="P3 shadow mode: run v_next.admit in parallel with current admission, log divergence to evidence/topology_v_next_shadow/; current admission remains authoritative")
    parser.add_argument("--task-class", default=None, help="Typed semantic boot task class")
    parser.add_argument("--write-intent", default=None, help="Runtime write intent: read_only, edit, apply, live, production")
    parser.add_argument("--operation-stage", default=None, help="Typed operation stage: explore, edit, merge, closeout, handoff")
    parser.add_argument("--mutation-surface", action="append", default=[], help="Typed mutation surface; repeat for multiple surfaces")
    parser.add_argument("--side-effect", default=None, help="Typed side effect: read_only, repo_edit, data_mutation, live_mutation")
    parser.add_argument("--artifact-target", default=None, help="Typed artifact target such as final_response, existing_work_log, receipt, runtime_scratch")
    parser.add_argument("--merge-state", default=None, help="Typed merge state: clean, narrow_conflict, broad_conflict, high_risk_conflict, unknown")
    parser.add_argument("--claim", action="append", default=[], help="Runtime completion claim to evaluate; repeat for multiple claims")
    parser.add_argument("--zone", default=None, help="Zone selector for --invariants")
    parser.add_argument(
        "--companion-loop-batch-cap",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Advisory threshold for companion-loop-break batch size (default 50). "
            "When --files contains more than N paths, a companion_loop_batch_advisory "
            "is emitted (non-blocking). Also settable via ZEUS_COMPANION_LOOP_BATCH_CAP env var."
        ),
    )

    sub = parser.add_subparsers(dest="command")
    digest = sub.add_parser("digest", help="Emit bounded task topology digest")
    digest.add_argument("--task", required=True)
    digest.add_argument("--files", nargs="*", default=[])
    digest.add_argument("--intent", default=None, help="Typed digest profile id; overrides free-text profile scoring but not admission")
    digest.add_argument("--task-class", default=None, help="Typed semantic boot task class")
    digest.add_argument("--write-intent", default=None, help="Runtime write intent: read_only, edit, apply, live, production")
    digest.add_argument("--operation-stage", default=None, help="Typed operation stage")
    digest.add_argument("--mutation-surface", action="append", default=[], help="Typed mutation surface; repeat for multiple surfaces")
    digest.add_argument("--side-effect", default=None, help="Typed side effect")
    digest.add_argument("--artifact-target", default=None, help="Typed artifact target")
    digest.add_argument("--merge-state", default=None, help="Typed merge state")
    digest.add_argument("--claim", action="append", default=[], help="Runtime completion claim to include in the route card")
    digest.add_argument("--json", action="store_true", help="Emit JSON")

    closeout = sub.add_parser("closeout", help="Emit compiled closeout result for a scoped change set")
    closeout.add_argument("--changed-files", nargs="*", default=[], help="Files in the closeout scope; omitted prefers staged files, else uses git status")
    closeout.add_argument("--plan-evidence", default=None, help="Plan/current-state evidence path")
    closeout.add_argument("--work-record-path", default=None, help="Work record path")
    closeout.add_argument("--receipt-path", default=None, help="Receipt path")
    closeout.add_argument("--claim", action="append", default=[], help="Runtime closeout claim to evaluate; repeat for multiple claims")
    closeout.add_argument("--json", action="store_true", help="Emit JSON")
    closeout.add_argument("--summary-only", action="store_true", help="Emit compact lane summary")
    closeout.add_argument(
        "--issue-schema-version",
        choices=["1", "2"],
        default=argparse.SUPPRESS,
        help="Issue JSON schema version for closeout issue payloads",
    )

    current_state = sub.add_parser("current-state", help="Emit generated current_state candidate from receipt")
    current_state.add_argument("--from-receipt", required=True, help="Receipt JSON path")
    current_state.add_argument("--json", action="store_true", help="Emit JSON")
    return parser


def render_payload(api: Any, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(api.yaml.safe_dump(payload, sort_keys=False).strip())


def _print_route_card(card: dict[str, Any]) -> None:
    if not card:
        return
    print("route_card:")
    for key in (
        "schema_version",
        "admission_status",
        "risk_tier",
        "dominant_driver",
        "persistence_target",
        "merge_conflict_scan",
        "next_action",
        "suggested_next_command",
    ):
        if card.get(key) is None:
            continue
        print(f"- {key}: {card.get(key)}")
    if card.get("merge_evidence_required"):
        evidence = card["merge_evidence_required"]
        print(f"- merge_evidence_required: {evidence.get('required')}")
        if evidence.get("reason"):
            print(f"  reason: {evidence.get('reason')}")
    if card.get("operation_vector"):
        vector = card["operation_vector"]
        surfaces = ", ".join(vector.get("mutation_surfaces") or [])
        print(
            "- operation_vector: "
            f"stage={vector.get('operation_stage')} "
            f"surface={surfaces or 'none'} "
            f"side_effect={vector.get('side_effect')} "
            f"artifact={vector.get('artifact_target')} "
            f"merge={vector.get('merge_state')}"
        )
    if card.get("admitted_files"):
        print("- admitted_files:")
        for path in card["admitted_files"]:
            print(f"  - {path}")
    if card.get("out_of_scope_files"):
        print("- out_of_scope_files:")
        for path in card["out_of_scope_files"]:
            print(f"  - {path}")
    if card.get("primary_blocker"):
        blocker = card["primary_blocker"]
        print("- primary_blocker:")
        print(f"  code: {blocker.get('code')}")
        print(f"  message: {blocker.get('message')}")
        if blocker.get("paths"):
            print("  paths:")
            for path in blocker["paths"]:
                print(f"    - {path}")
    if card.get("route_candidates"):
        print("- route_candidates:")
        for candidate in card["route_candidates"]:
            selected = " selected" if candidate.get("selected") else ""
            evidence = candidate.get("evidence_class") or "evidence"
            score = candidate.get("score")
            score_text = f" score={score:.2f}" if isinstance(score, (int, float)) else ""
            print(f"  - {candidate.get('rank')}: {candidate.get('profile')}{selected} [{evidence}{score_text}]")
    if card.get("mandatory_companion_files"):
        print("- mandatory_companion_files:")
        for companion in card["mandatory_companion_files"]:
            print(f"  - {companion.get('path')} -> {companion.get('companion')}")
    budget = card.get("gate_budget") or {}
    if budget:
        print(f"- gate_budget: {budget.get('label')}")
        for gate in budget.get("required") or []:
            print(f"  required: {gate}")
    if card.get("claims"):
        print("- claims:")
        for claim in card["claims"]:
            print(f"  - {claim}")
    if card.get("expansion_hints"):
        print("- expansion_hints:")
        for hint in card["expansion_hints"]:
            print(f"  - {hint}")
    if card.get("why_not_admitted") and not card.get("primary_blocker"):
        print("- why_not_admitted:")
        for reason in card["why_not_admitted"]:
            print(f"  - {reason}")
    if card.get("blocked_file_reasons"):
        print("- blocked_file_reasons:")
        for path, reasons in card["blocked_file_reasons"].items():
            print(f"  {path}:")
            for reason in reasons:
                print(f"    - {reason}")
    if card.get("provenance_notes"):
        print("- provenance_notes:")
        for note in card["provenance_notes"]:
            print(f"  - {note}")


def render_digest(api: Any, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(f"Topology digest: {payload['profile']}")
    print(f"Task: {payload['task']}")
    _print_route_card(payload.get("route_card") or {})
    admission = payload.get("admission") or {}
    print("\nadmission:")
    print(f"- status: {admission.get('status')}")
    print(f"- profile_id: {admission.get('profile_id')}")
    print("- admitted_files:")
    for item in admission.get("admitted_files") or []:
        print(f"  - {item}")
    print("- out_of_scope_files:")
    for item in admission.get("out_of_scope_files") or []:
        print(f"  - {item}")
    for key in ("required_law", "forbidden_files", "gates", "downstream", "stop_conditions"):
        print(f"\n{key}:")
        for item in payload[key]:
            print(f"- {item}")
    if payload.get("source_rationale"):
        print("\nsource_rationale:")
        for item in payload["source_rationale"]:
            print(f"- {item['path']}: {item.get('why', '')}")
            print(f"  zone: {item.get('zone', '')}")
            print(f"  authority_role: {item.get('authority_role', '')}")
            if item.get("hazards"):
                print(f"  hazards: {', '.join(item['hazards'])}")
            if item.get("write_routes"):
                print(f"  write_routes: {', '.join(item['write_routes'])}")
    if payload.get("data_rebuild_topology"):
        data_topology = payload["data_rebuild_topology"]
        print("\ndata_rebuild_topology:")
        certification = data_topology.get("live_math_certification", {})
        print(f"- live_math_certification.allowed: {certification.get('allowed')}")
        print("- row_contract_tables:")
        for name, spec in data_topology.get("row_contract_tables", {}).items():
            fields = ", ".join(spec.get("required_fields", []))
            print(f"  - {name}: fields=[{fields}] producer={spec.get('producer', '')}")
        required = ", ".join(data_topology.get("replay_coverage_rule", {}).get("required_for_strategy_replay_coverage", []))
        print(f"- replay_coverage_required: {required}")
    if payload.get("history_lore"):
        print("\nhistory_lore:")
        for card in payload["history_lore"]:
            print(f"- {card['id']} [{card['severity']}/{card['status']}]: {card['zero_context_digest']}")
    if payload.get("gate_trust"):
        print("\ngate_trust:")
        for entry in payload["gate_trust"]:
            status = entry["status"]
            print(f"- {entry['gate']}: {status}")
            if status == "audit_required":
                for untrusted in entry.get("untrusted_tests", []):
                    print(f"  ⚠ audit_required: {untrusted}")


def run_flag_command(api: Any, args: argparse.Namespace) -> int | None:
    commands = [
        ("strict", api.run_strict),
        ("schema", api.run_schema),
        ("global_health", api.run_strict),
        ("docs", api.run_docs),
        ("source", api.run_source),
        ("tests", api.run_tests),
        ("scripts", api.run_scripts),
        ("data_rebuild", api.run_data_rebuild),
        ("history_lore", api.run_history_lore),
        ("context_budget", api.run_context_budget),
        ("artifact_lifecycle", api.run_artifact_lifecycle),
        ("ownership", api.run_ownership),
        ("current_state_receipt_bound", api.run_current_state_receipt_bound),
        ("agents_coherence", api.run_agents_coherence),
        ("idioms", api.run_idioms),
        ("self_check_coherence", api.run_self_check_coherence),
        ("runtime_modes", api.run_runtime_modes),
        ("task_boot_profiles", api.run_task_boot_profiles),
        ("fatal_misreads", api.run_fatal_misreads),
        ("city_truth_contract", api.run_city_truth_contract),
        ("code_review_graph_protocol", api.run_code_review_graph_protocol),
        ("reference_replacement", api.run_reference_replacement),
        ("core_claims", api.run_core_claims),
        ("naming_conventions", api.run_naming_conventions),
    ]
    for attr, fn in commands:
        if getattr(args, attr):
            result = fn()
            api._print_strict(
                result,
                as_json=args.json,
                summary_only=args.summary_only,
                issue_schema_version=args.issue_schema_version,
            )
            return 0 if result.ok else 1
    if args.invariants:
        render_payload(api, api.build_invariants_slice(args.zone), as_json=args.json)
        return 0
    if args.work_record:
        result = api.run_work_record(args.changed_files, args.work_record_path)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.change_receipts:
        result = api.run_change_receipts(args.changed_files, args.receipt_path)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.map_maintenance:
        result = api.run_map_maintenance(args.changed_files, mode=args.map_maintenance_mode)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.freshness_metadata:
        result = api.run_freshness_metadata(args.changed_files)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.code_review_graph_status:
        result = api.run_code_review_graph_status(args.changed_files)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    if args.preflight:
        args.navigation = True
        args.route_card_only = True
    if args.navigation:
        navigation_kwargs = {"strict_health": args.strict_health}
        for field in (
            "intent",
            "task_class",
            "write_intent",
            "operation_stage",
            "mutation_surface",
            "side_effect",
            "artifact_target",
            "merge_state",
            "claim",
        ):
            value = getattr(args, field, None)
            if field in {"claim", "mutation_surface"} and not value:
                continue
            if value is not None:
                key = {"claim": "claims", "mutation_surface": "mutation_surfaces"}.get(field, field)
                navigation_kwargs[key] = value
        _batch_cap = getattr(args, "companion_loop_batch_cap", None)
        if _batch_cap is not None:
            navigation_kwargs["companion_loop_batch_cap"] = _batch_cap
        _v_next_shadow = getattr(args, "v_next_shadow", False)
        if _v_next_shadow:
            navigation_kwargs["v_next_shadow"] = True
        if args.issue_schema_version != "1":
            navigation_kwargs["issue_schema_version"] = args.issue_schema_version
        navigation_files = list(args.files or [])
        changed_files = list(args.changed_files or [])
        if navigation_files and changed_files and navigation_files != changed_files:
            print(
                "--navigation received both --files and --changed-files with different values; "
                "use one file list",
                file=sys.stderr,
            )
            return 2
        if not navigation_files and changed_files:
            navigation_files = changed_files
        payload = api.run_navigation(args.task or "general navigation", navigation_files, **navigation_kwargs)
        if args.route_card_only:
            route_card = dict(payload.get("route_card") or {})
            direct_blockers = list(payload.get("direct_blockers") or [])
            if args.preflight and direct_blockers and not route_card.get("primary_blocker"):
                blocker = direct_blockers[0]
                blocker_path = blocker.get("path")
                route_card["primary_blocker"] = {
                    "code": blocker.get("code", "direct_blocker"),
                    "message": blocker.get("message", "navigation direct blocker"),
                    "paths": [blocker_path] if blocker_path else [],
                }
            route_ok = bool(payload["ok"])
            if args.preflight:
                route_ok = (
                    bool(payload["ok"])
                    and route_card.get("admission_status") == "admitted"
                    and not route_card.get("primary_blocker")
                )
            route_payload = {"ok": route_ok, "route_card": route_card}
            if args.json:
                print(json.dumps(route_payload, indent=2))
            else:
                _print_route_card(route_payload["route_card"])
            return 0 if route_ok else 1
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"navigation ok: {payload['ok']}")
            print(f"profile: {payload['digest']['profile']}")
            _print_route_card(payload.get("route_card") or {})
            issues_scope = getattr(args, "issues_scope", "task")
            direct_blockers = payload.get("direct_blockers") or []
            repo_health_warnings = payload.get("repo_health_warnings") or []
            if direct_blockers:
                print("direct_blockers:")
                for issue in direct_blockers:
                    print(f"- [{issue['severity']}:{issue['lane']}:{issue['code']}] {issue['path']}: {issue['message']}")
            if issues_scope == "all":
                if repo_health_warnings:
                    print("repo_health_warnings:")
                    for issue in repo_health_warnings:
                        print(f"- [{issue['severity']}:{issue['lane']}:{issue['code']}] {issue['path']}: {issue['message']}")
                elif not direct_blockers and payload["issues"]:
                    print("issues:")
                    for issue in payload["issues"]:
                        print(f"- [{issue['severity']}:{issue['lane']}:{issue['code']}] {issue['path']}: {issue['message']}")
            else:
                # task scope: collapse repo-health drift into a single advisory count line
                if repo_health_warnings:
                    by_severity: dict[str, int] = {}
                    for issue in repo_health_warnings:
                        sev = issue.get("severity", "unknown")
                        by_severity[sev] = by_severity.get(sev, 0) + 1
                    severity_summary = ", ".join(
                        f"{count} {sev}" for sev, count in sorted(by_severity.items())
                    )
                    print(
                        f"repo_health_warnings: {len(repo_health_warnings)} "
                        f"({severity_summary}) [unrelated to this task; rerun with --issues-scope all to inspect]"
                    )
            print("excluded_lanes:")
            for lane, reason in payload["excluded_lanes"].items():
                print(f"- {lane}: {reason}")
        return 0 if payload["ok"] else 1
    if args.planning_lock:
        result = api.run_planning_lock(args.changed_files, args.plan_evidence)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only, issue_schema_version=args.issue_schema_version)
        return 0 if result.ok else 1
    return None


def run_subcommand(api: Any, args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command == "digest":
        render_digest(
            api,
            api.build_digest(
                args.task,
                args.files,
                intent=args.intent,
                task_class=args.task_class,
                write_intent=args.write_intent,
                operation_stage=args.operation_stage,
                mutation_surfaces=args.mutation_surface,
                side_effect=args.side_effect,
                artifact_target=args.artifact_target,
                merge_state=args.merge_state,
                claims=args.claim,
            ),
            as_json=args.json,
        )
        return 0
    if args.command == "closeout":
        payload = api.run_closeout(
            changed_files=args.changed_files,
            plan_evidence=args.plan_evidence,
            work_record_path=args.work_record_path,
            receipt_path=args.receipt_path,
            claims=args.claim,
            issue_schema_version=args.issue_schema_version,
        )
        if args.json:
            print(json.dumps(payload, indent=2))
        elif args.summary_only:
            status = "closeout ok" if payload["ok"] else "closeout failed"
            print(status)
            print(f"changed_files: {len(payload['changed_files'])}")
            for lane, summary in payload["lanes"].items():
                state = "ok" if summary["ok"] else "fail"
                print(
                    f"- {lane}: {state} "
                    f"(blocking={summary['blocking_count']}, warnings={summary['warning_count']})"
                )
            if payload.get("global_health"):
                print("global_health:")
                for lane, summary in payload["global_health"].items():
                    print(
                        f"- {lane}: "
                        f"(blocking={summary['blocking_count']}, warnings={summary['warning_count']})"
                    )
            telemetry = payload.get("telemetry") or {}
            print(
                f"telemetry: dark_write_targets={telemetry.get('dark_write_target_count', 0)}, "
                f"broken_visible_routes={telemetry.get('broken_visible_route_count', 0)}, "
                f"unclassified_docs_artifacts={telemetry.get('unclassified_docs_artifact_count', 0)}"
            )
        else:
            print("closeout ok" if payload["ok"] else "closeout failed")
            print("changed_files:")
            for path in payload["changed_files"]:
                print(f"- {path}")
            print("lanes:")
            for lane, summary in payload["lanes"].items():
                state = "ok" if summary["ok"] else "fail"
                print(
                    f"- {lane}: {state} "
                    f"(blocking={summary['blocking_count']}, warnings={summary['warning_count']})"
                )
                for issue in summary["issues"]:
                    print(
                        f"  - [{issue['severity']}:{issue['code']}] {issue['path']}: {issue['message']}"
                    )
            if payload.get("global_health"):
                print("global_health:")
                for lane, summary in payload["global_health"].items():
                    print(
                        f"- {lane}: "
                        f"(blocking={summary['blocking_count']}, warnings={summary['warning_count']})"
                    )
        return 0 if payload["ok"] else 1
    if args.command == "current-state":
        payload = api.build_current_state_candidate(args.from_receipt)
        render_payload(api, payload, as_json=args.json)
        return 0 if payload.get("ok") else 1
    parser.print_help()
    return 2


def main(argv: list[str] | None = None, api: Any | None = None) -> int:
    if api is None:
        try:
            from scripts import topology_doctor as api
        except ModuleNotFoundError:  # direct script execution from scripts/
            import topology_doctor as api

    parser = build_parser(getattr(api, "__doc__", None))
    args = parser.parse_args(argv)
    flag_result = run_flag_command(api, args)
    if flag_result is not None:
        return flag_result
    return run_subcommand(api, args, parser)
