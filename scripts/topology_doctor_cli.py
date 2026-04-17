"""CLI facade for scripts.topology_doctor.

Keep command parsing and rendering here; keep topology checks/builders in
topology_doctor.py until golden-output parity is strong enough for deeper
module extraction.
"""
# Lifecycle: created=2026-04-15; last_reviewed=2026-04-16; last_reused=2026-04-16
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
    parser.add_argument("--context-packs", action="store_true", help="Run context-pack profile checks")
    parser.add_argument("--agents-coherence", action="store_true", help="Check scoped AGENTS prose against machine maps")
    parser.add_argument("--idioms", action="store_true", help="Check intentional non-obvious code idiom registry")
    parser.add_argument("--self-check-coherence", action="store_true", help="Check zero-context self-check alignment with root navigation")
    parser.add_argument("--runtime-modes", action="store_true", help="Check discovery/runtime mode manifest and root visibility")
    parser.add_argument("--reference-replacement", action="store_true", help="Check reference replacement matrix")
    parser.add_argument("--core-claims", action="store_true", help="Check proof-backed core claim registry")
    parser.add_argument("--core-maps", action="store_true", help="Check core-map profile compilation")
    parser.add_argument("--naming-conventions", action="store_true", help="Check canonical file/function naming map")
    parser.add_argument("--freshness-metadata", action="store_true", help="Check changed scripts/tests for lifecycle freshness headers")
    parser.add_argument("--map-maintenance", action="store_true", help="Check companion registry updates for added/deleted files")
    parser.add_argument(
        "--map-maintenance-mode",
        choices=["advisory", "precommit", "closeout"],
        default="advisory",
        help="Map-maintenance severity mode",
    )
    parser.add_argument("--navigation", action="store_true", help="Run default navigation health and task digest")
    parser.add_argument("--planning-lock", action="store_true", help="Check whether changed files require planning evidence")
    parser.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        help="Files for --planning-lock; optional map-maintenance override (omitted there reads git status: staged, unstaged, untracked, deleted)",
    )
    parser.add_argument("--plan-evidence", default=None, help="Plan/current-state evidence path for --planning-lock")
    parser.add_argument("--work-record-path", default=None, help="Work record path for --work-record")
    parser.add_argument("--receipt-path", default=None, help="Receipt path for --change-receipts")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    parser.add_argument("--summary-only", action="store_true", help="Emit issue counts by code instead of full issue list")
    parser.add_argument("--task", default="", help="Task string for --navigation")
    parser.add_argument("--files", nargs="*", default=[], help="Files for --navigation")
    parser.add_argument("--zone", default=None, help="Zone selector for --invariants")

    sub = parser.add_subparsers(dest="command")
    digest = sub.add_parser("digest", help="Emit bounded task topology digest")
    digest.add_argument("--task", required=True)
    digest.add_argument("--files", nargs="*", default=[])
    digest.add_argument("--json", action="store_true", help="Emit JSON")

    packet = sub.add_parser("packet", help="Emit prefilled packet front matter from topology scope")
    packet.add_argument("--packet-type", default="refactor", choices=["refactor"], help="Packet template type")
    packet.add_argument("--scope", default="", help="Directory or file scope")
    packet.add_argument("--files", nargs="*", default=[], help="Concrete files in scope")
    packet.add_argument("--task", default="", help="Task statement")
    packet.add_argument("--json", action="store_true", help="Emit JSON")

    impact = sub.add_parser("impact", help="Emit provisional source impact summary")
    impact.add_argument("--files", nargs="+", required=True, help="Source files to inspect")
    impact.add_argument("--json", action="store_true", help="Emit JSON")

    core_map = sub.add_parser("core-map", help="Emit generated core working map")
    core_map.add_argument("--profile", required=True, help="Core-map profile id")
    core_map.add_argument("--json", action="store_true", help="Emit JSON")

    compiled_topology = sub.add_parser("compiled-topology", help="Emit derived compiled topology read model")
    compiled_topology.add_argument("--json", action="store_true", help="Emit JSON")

    closeout = sub.add_parser("closeout", help="Emit compiled closeout result for a scoped change set")
    closeout.add_argument("--changed-files", nargs="*", default=[], help="Files in the closeout scope; omitted prefers staged files, else uses git status")
    closeout.add_argument("--plan-evidence", default=None, help="Plan/current-state evidence path")
    closeout.add_argument("--work-record-path", default=None, help="Work record path")
    closeout.add_argument("--receipt-path", default=None, help="Receipt path")
    closeout.add_argument("--json", action="store_true", help="Emit JSON")
    closeout.add_argument("--summary-only", action="store_true", help="Emit compact lane summary")

    context_pack = sub.add_parser("context-pack", help="Emit task-shaped agent context packet")
    context_pack.add_argument("--pack-type", default="auto", choices=["auto", "package_review", "debug"], help="Context-pack profile")
    context_pack.add_argument("--task", required=True, help="Task statement")
    context_pack.add_argument("--files", nargs="+", required=True, help="Files in the reviewed package")
    context_pack.add_argument("--json", action="store_true", help="Emit JSON")
    return parser


def render_payload(api: Any, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(api.yaml.safe_dump(payload, sort_keys=False).strip())


def render_digest(api: Any, payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(f"Topology digest: {payload['profile']}")
    print(f"Task: {payload['task']}")
    for key in ("required_law", "allowed_files", "forbidden_files", "gates", "downstream", "stop_conditions"):
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


def run_flag_command(api: Any, args: argparse.Namespace) -> int | None:
    commands = [
        ("strict", api.run_strict),
        ("docs", api.run_docs),
        ("source", api.run_source),
        ("tests", api.run_tests),
        ("scripts", api.run_scripts),
        ("data_rebuild", api.run_data_rebuild),
        ("history_lore", api.run_history_lore),
        ("context_budget", api.run_context_budget),
        ("artifact_lifecycle", api.run_artifact_lifecycle),
        ("context_packs", api.run_context_packs),
        ("agents_coherence", api.run_agents_coherence),
        ("idioms", api.run_idioms),
        ("self_check_coherence", api.run_self_check_coherence),
        ("runtime_modes", api.run_runtime_modes),
        ("reference_replacement", api.run_reference_replacement),
        ("core_claims", api.run_core_claims),
        ("core_maps", api.run_core_maps),
        ("naming_conventions", api.run_naming_conventions),
    ]
    for attr, fn in commands:
        if getattr(args, attr):
            result = fn()
            api._print_strict(result, as_json=args.json, summary_only=args.summary_only)
            return 0 if result.ok else 1
    if args.invariants:
        render_payload(api, api.build_invariants_slice(args.zone), as_json=args.json)
        return 0
    if args.work_record:
        result = api.run_work_record(args.changed_files, args.work_record_path)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only)
        return 0 if result.ok else 1
    if args.change_receipts:
        result = api.run_change_receipts(args.changed_files, args.receipt_path)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only)
        return 0 if result.ok else 1
    if args.map_maintenance:
        result = api.run_map_maintenance(args.changed_files, mode=args.map_maintenance_mode)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only)
        return 0 if result.ok else 1
    if args.freshness_metadata:
        result = api.run_freshness_metadata(args.changed_files)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only)
        return 0 if result.ok else 1
    if args.navigation:
        payload = api.run_navigation(args.task or "general navigation", args.files)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"navigation ok: {payload['ok']}")
            print(f"profile: {payload['digest']['profile']}")
            if payload["issues"]:
                print("issues:")
                for issue in payload["issues"]:
                    print(f"- [{issue['severity']}:{issue['lane']}:{issue['code']}] {issue['path']}: {issue['message']}")
            print("excluded_lanes:")
            for lane, reason in payload["excluded_lanes"].items():
                print(f"- {lane}: {reason}")
        return 0 if payload["ok"] else 1
    if args.planning_lock:
        result = api.run_planning_lock(args.changed_files, args.plan_evidence)
        api._print_strict(result, as_json=args.json, summary_only=args.summary_only)
        return 0 if result.ok else 1
    return None


def run_subcommand(api: Any, args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.command == "digest":
        render_digest(api, api.build_digest(args.task, args.files), as_json=args.json)
        return 0
    if args.command == "packet":
        payload = api.build_packet_prefill(
            packet_type=args.packet_type,
            task=args.task,
            scope=args.scope,
            files=args.files,
        )
        render_payload(api, payload, as_json=args.json)
        return 0
    if args.command == "impact":
        render_payload(api, api.build_impact(args.files), as_json=args.json)
        return 0
    if args.command == "core-map":
        try:
            payload = api.build_core_map(args.profile)
        except ValueError as exc:
            print(f"core-map failed: {exc}", file=sys.stderr)
            return 1
        render_payload(api, payload, as_json=args.json)
        return 0 if not payload.get("invalid") else 1
    if args.command == "compiled-topology":
        render_payload(api, api.build_compiled_topology(), as_json=args.json)
        return 0
    if args.command == "closeout":
        payload = api.run_closeout(
            changed_files=args.changed_files,
            plan_evidence=args.plan_evidence,
            work_record_path=args.work_record_path,
            receipt_path=args.receipt_path,
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
        return 0 if payload["ok"] else 1
    if args.command == "context-pack":
        try:
            payload = api.build_context_pack(args.pack_type, task=args.task, files=args.files)
        except ValueError as exc:
            print(f"context-pack failed: {exc}", file=sys.stderr)
            return 1
        render_payload(api, payload, as_json=args.json)
        return 0
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
